"""ONNX Runtime wrappers: CUDA when available, CPU otherwise.

Each model folder ``models/<name>/<version>/`` holds ``model.onnx`` and ``model.json``
(input size, output names, license, training notes).
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np

from .. import schema as S
from ..cancel import CancelToken
from ..errors import VoxyError
from ..imaging import foreground_mask
from ..preproc import general_input, hair_input, unletterbox
from .base import GeneralOutput, HairOutput, ModelInfo


def _ort():
    import onnxruntime as ort
    return ort


def available_providers() -> list:
    try:
        return list(_ort().get_available_providers())
    except Exception:  # noqa: BLE001
        return []


def make_session(path: str, *, prefer: str = "cuda", cpu_fallback: bool = True,
                 gpu_mem_limit_mb: int = 0) -> Tuple[Any, str, Optional[str]]:
    """Open an ONNX session. Returns (session, device, warning_code_or_None).
    ``gpu_mem_limit_mb`` caps this session's CUDA memory arena so two models in one job stay inside 12 GB."""
    ort = _ort()
    so = ort.SessionOptions()
    so.log_severity_level = 3
    avail = available_providers()
    warning = None
    if prefer == "cuda" and "CUDAExecutionProvider" in avail:
        try:
            cuda_opts: Dict[str, Any] = {"arena_extend_strategy": "kSameAsRequested"}
            if gpu_mem_limit_mb > 0:
                cuda_opts["gpu_mem_limit"] = int(gpu_mem_limit_mb) * 1024 * 1024
            sess = ort.InferenceSession(path, so, providers=[("CUDAExecutionProvider", cuda_opts), "CPUExecutionProvider"])
            if sess.get_providers()[0] == "CUDAExecutionProvider":
                return sess, "cuda", None
            warning = "GPU_NOT_USED"
        except Exception:  # noqa: BLE001
            warning = "GPU_NOT_USED"
            if not cpu_fallback:
                raise
    elif prefer == "cuda":
        warning = "GPU_NOT_AVAILABLE"
    sess = ort.InferenceSession(path, so, providers=["CPUExecutionProvider"])
    return sess, "cpu", warning


def read_meta(model_dir: str) -> Dict[str, Any]:
    p = os.path.join(model_dir, "model.json")
    if not os.path.isfile(p):
        raise VoxyError("GENERAL_MODEL_MISSING", f"missing {p}")
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


class _OnnxBase:
    def __init__(self, model_dir: str, name: str, code_missing: str, prefer: str, cpu_fallback: bool, gpu_mem_limit_mb: int = 0):
        self.dir = model_dir
        self.meta = read_meta(model_dir) if os.path.isdir(model_dir) else {}
        path = os.path.join(model_dir, "model.onnx")
        if not os.path.isfile(path):
            raise VoxyError(code_missing, f"missing {path}")
        self.sess, device, self.warning = make_session(path, prefer=prefer, cpu_fallback=cpu_fallback,
                                                       gpu_mem_limit_mb=gpu_mem_limit_mb)
        self.info = ModelInfo(name, str(self.meta.get("version", os.path.basename(model_dir))), "onnx", device)
        self.size = int(self.meta.get("input_size", 512))
        self._input = self.sess.get_inputs()[0].name
        self._outs = [o.name for o in self.sess.get_outputs()]

    def _run(self, x: np.ndarray, cancel: CancelToken) -> Dict[str, np.ndarray]:
        cancel.check(self.info.name)
        ro = _ort().RunOptions()
        cancel.on_cancel(lambda: setattr(ro, "terminate", True))
        try:
            res = self.sess.run(self._outs, {self._input: x}, ro)
        except Exception as e:  # noqa: BLE001
            if "out of memory" in str(e).lower() or "bfc_arena" in str(e).lower():
                raise VoxyError("OUT_OF_MEMORY", str(e), stage=self.info.name) from e
            raise
        cancel.check(self.info.name)
        return dict(zip(self._outs, res))


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


class OnnxGeneralModel(_OnnxBase):
    """Outputs: seg [1,C,S,S] logits, kpt [1,K,S/4,S/4] heat-maps, amodal [1,A,S,S] logits,
    completion [1,3,S,S] in [0,1]."""

    def __init__(self, model_dir: str, prefer: str = "cuda", cpu_fallback: bool = True, gpu_mem_limit_mb: int = 0):
        super().__init__(model_dir, "general", "GENERAL_MODEL_MISSING", prefer, cpu_fallback, gpu_mem_limit_mb)
        if list(self.meta.get("classes", S.CLASS_NAMES)) != S.CLASS_NAMES:
            raise VoxyError("GENERAL_MODEL_FAILED", "model class list does not match this app version")

    def predict(self, rgba: np.ndarray, cancel: CancelToken) -> GeneralOutput:
        h, w = rgba.shape[:2]
        x, meta = general_input(rgba, self.size)
        out = self._run(x, cancel)
        seg = out["seg"][0].transpose(1, 2, 0)                            # S,S,C
        seg_full = unletterbox(seg, meta)                                  # h,w,C
        labels = seg_full.argmax(axis=2).astype(np.uint8)
        e = np.exp(seg_full - seg_full.max(axis=2, keepdims=True))
        prob = e / e.sum(axis=2, keepdims=True)
        has_alpha = (rgba[:, :, 3] < 250).any()
        if has_alpha:
            labels[~foreground_mask(rgba)] = 0
        conf = {}
        for i, c in enumerate(S.CLASS_NAMES[1:], start=1):
            sel = labels == i
            conf[c] = float(prob[:, :, i][sel].mean()) if sel.any() else 0.0

        kp = out["kpt"][0]                                                 # K,S/4,S/4
        f = self.size / kp.shape[1]
        kps = {}
        for i, name in enumerate(S.KEYPOINTS):
            idx = int(kp[i].argmax())
            ky, kx = divmod(idx, kp.shape[2])
            # canvas pixel -> original pixel
            cx, cy = (kx + 0.5) * f, (ky + 0.5) * f
            ox = (cx - meta.pad_x) / meta.scale
            oy = (cy - meta.pad_y) / meta.scale
            kps[name] = (float(np.clip(ox, 0, w - 1)), float(np.clip(oy, 0, h - 1)), float(np.clip(kp[i].max(), 0, 1)))

        am = out["amodal"][0].transpose(1, 2, 0)
        am_full = unletterbox(am, meta)
        amodal = {base: (am_full[:, :, i] > 0.0) for i, base in enumerate(S.AMODAL_CLASSES)}
        comp = None
        if "completion" in out:
            c = unletterbox(out["completion"][0].transpose(1, 2, 0), meta)
            comp = np.clip(c * 255.0, 0, 255).astype(np.uint8)
        return GeneralOutput(labels=labels, keypoints=kps, amodal=amodal, completion_rgb=comp, class_confidence=conf)


class OnnxHairModel(_OnnxBase):
    """Outputs: flow [1,2,S,S] (strand direction, normalised here) and roots [1,1,S,S] logits."""

    def __init__(self, model_dir: str, prefer: str = "cuda", cpu_fallback: bool = True, gpu_mem_limit_mb: int = 0):
        super().__init__(model_dir, "hair", "HAIR_MODEL_MISSING", prefer, cpu_fallback, gpu_mem_limit_mb)

    def predict(self, rgb: np.ndarray, hair_mask: np.ndarray, cancel: CancelToken) -> HairOutput:
        h, w = hair_mask.shape
        x, meta, (x0, y0, cw, ch) = hair_input(rgb, hair_mask, self.size)
        out = self._run(x, cancel)
        flow_c = unletterbox(out["flow"][0].transpose(1, 2, 0), meta)
        roots_c = unletterbox(_sigmoid(out["roots"][0, 0])[:, :, None], meta)
        if roots_c.ndim == 3:
            roots_c = roots_c[:, :, 0]
        n = np.sqrt((flow_c ** 2).sum(axis=2, keepdims=True))
        flow_c = flow_c / np.maximum(n, 1e-6)
        flow = np.zeros((h, w, 2), np.float32)
        flow[..., 1] = 1.0
        roots = np.zeros((h, w), np.float32)
        flow[y0:y0 + ch, x0:x0 + cw] = flow_c
        roots[y0:y0 + ch, x0:x0 + cw] = roots_c
        return HairOutput(flow=flow, roots=roots)
