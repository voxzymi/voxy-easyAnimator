"""Job pipelines. Each returns a validated manifest dict and never touches the AE project.

Failure rules (see docs/architecture.md):
* general model fails        -> make_layers is ``failed`` and lists no parts.
* hair subdivision fails     -> the single Hair part is still returned, status ``partial``,
                                and the manifest records the failure.
* background / matting fail  -> parts are still returned, status ``partial``.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from . import schema as S
from .background import choose_engine, make_background_plate
from .cancel import CancelToken, run_step
from .errors import Cancelled, VoxyError, error_dict, warning_dict
from .hair.subdivide import extract_roots, split_hair
from .imaging import b64_to_bytes, decode_image, load_image, save_png, sha256_of
from .manifest import finalize, new_manifest, validate_manifest
from .models.base import ModelInfo
from .models.registry import ModelRegistry
from .postprocess import build_parts, make_part
from .upscale import Upscaler

log = logging.getLogger("voxy.pipeline")

WARNING_TEXT = {
    "GPU_NOT_AVAILABLE": ("Voxy is using the slow mode because it could not use your graphics card.",
                          "Update your NVIDIA driver to use the fast mode. The slow mode still works.", "gpu-not-detected"),
    "GPU_NOT_USED": ("Voxy is using the slow mode because the graphics card could not be started.",
                     "Update your NVIDIA driver to use the fast mode. The slow mode still works.", "gpu-not-detected"),
    "BACKGROUND_FILL_FALLBACK": ("The AI background fill is not installed, so Voxy used the flat-color fill.",
                                 "Run the installer again if you want the AI fill.", "missing-files"),
}


@dataclass
class Ctx:
    cfg: Dict[str, Any]
    registry: ModelRegistry
    app_dir: str
    data_dir: str
    job_id: str
    job_dir: str
    cancel: CancelToken
    resolve_job_dir: Callable[[str], Optional[str]]
    upscaler: Optional[Upscaler] = None
    on_step: Callable[[str], None] = lambda s: None
    timings: Dict[str, float] = field(default_factory=dict)

    def step(self, name: str, fn: Callable[[], Any], timeout_key: Optional[str] = None) -> Any:
        self.cancel.check(name)
        self.on_step(name)
        t0 = time.monotonic()
        try:
            return run_step(fn, stage=name, timeout_s=self.cfg["timeouts_s"].get(timeout_key or name), token=self.cancel)
        finally:
            self.timings[name] = round(self.timings.get(name, 0.0) + (time.monotonic() - t0) * 1000, 1)


# ------------------------------------------------------------------ helpers
def _request_image(req: Dict[str, Any], key: str = "image") -> np.ndarray:
    if req.get(f"{key}_path"):
        return load_image(req[f"{key}_path"])
    if req.get(f"{key}_b64"):
        return decode_image(b64_to_bytes(req[f"{key}_b64"]))
    if req.get(f"{key}_bytes"):
        return decode_image(req[f"{key}_bytes"])
    raise VoxyError("BAD_REQUEST", f"request has no {key}", stage="load")


def _fail(m: Dict[str, Any], err: VoxyError, ctx: Ctx) -> Dict[str, Any]:
    m["errors"].append(err.to_dict())
    return m


def _warn(m: Dict[str, Any], code: str) -> None:
    u, w, h = WARNING_TEXT[code]
    m["warnings"].append(warning_dict(code, u, w, h))


def _write_manifest(ctx: Ctx, m: Dict[str, Any]) -> Dict[str, Any]:
    m["job"]["output_dir"] = ctx.job_dir
    m["timings_ms"] = dict(ctx.timings)
    validate_manifest(m, base_dir=ctx.job_dir)
    with open(os.path.join(ctx.job_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(m, f, indent=1)
    return m


def _clear_parts(ctx: Ctx) -> None:
    shutil.rmtree(os.path.join(ctx.job_dir, "parts"), ignore_errors=True)


def _classify(e: BaseException, stage: str, default_code: str) -> VoxyError:
    if isinstance(e, VoxyError):
        return e
    log.error("step %s failed:\n%s", stage, "".join(traceback.format_exception(type(e), e, e.__traceback__)))
    msg = str(e)
    if "out of memory" in msg.lower():
        return VoxyError("OUT_OF_MEMORY", msg, stage=stage)
    return VoxyError(default_code, f"{type(e).__name__}: {msg}", stage=stage)


def _device_warnings(ctx: Ctx, m: Dict[str, Any]) -> None:
    for w in set(ctx.registry.warnings.values()):
        if w in WARNING_TEXT:
            _warn(m, w)


# ------------------------------------------------------------------ make layers
def make_layers(ctx: Ctx, req: Dict[str, Any]) -> Dict[str, Any]:
    m = new_manifest(ctx.job_id, "make_layers", output_dir=ctx.job_dir)
    opts = req.get("options") or {}
    status = "ok"
    try:
        rgba = ctx.step("load", lambda: _request_image(req))
        h, w = rgba.shape[:2]
        m["source"] = {"width": w, "height": h, "sha256": sha256_of(rgba)}
        save_png(os.path.join(ctx.job_dir, "source.png"), rgba)
        work = rgba

        if opts.get("background_removal"):
            try:
                matter = _matter(ctx)
                alpha = ctx.step("matting", lambda: matter.alpha(rgba, ctx.cancel))
                work = rgba.copy()
                work[:, :, 3] = np.clip(alpha * 255.0, 0, 255).astype(np.uint8)
                m["models"]["matting"] = {"name": "matting", "version": getattr(matter, "version", "0"),
                                          "backend": matter.backend, "device": matter.device}
            except Cancelled:
                raise
            except Exception as e:  # noqa: BLE001
                m["errors"].append(_classify(e, "matting", "MATTING_FAILED").to_dict())
                status = "partial"
                work = rgba

        try:
            general = ctx.registry.general()
            m["models"]["general"] = general.info.to_dict()
            out = ctx.step("general", lambda: general.predict(work, ctx.cancel))
        except Cancelled:
            raise
        except Exception as e:  # noqa: BLE001
            ve = _classify(e, "general", "GENERAL_MODEL_FAILED")
            if ve.code not in ("GENERAL_MODEL_MISSING", "OUT_OF_MEMORY", "STEP_TIMEOUT", "IMAGE_UNREADABLE"):
                ve = VoxyError("GENERAL_MODEL_FAILED", ve.message, stage="general")
            raise ve
        if not out.labels.any():
            raise VoxyError("NO_CHARACTER_FOUND", "the model found no character pixels", stage="general")

        parts, skipped, masks = ctx.step(
            "postprocess", lambda: build_parts(work, out, general.info, ctx.job_dir,
                                               min_pixels=int(ctx.cfg["make_layers"]["min_part_pixels"])))
        if not parts:
            raise VoxyError("NO_CHARACTER_FOUND", "every part was empty", stage="postprocess")
        m["parts"] = parts
        m["skipped"] = skipped
        m["keypoints"] = [{"name": k, "x": round(v[0], 2), "y": round(v[1], 2), "score": round(max(0.0, min(1.0, v[2])), 3)}
                          for k, v in out.keypoints.items()]
        m["models"]["hair"] = None
        _device_warnings(ctx, m)

        if opts.get("background_removal"):
            try:
                char = np.zeros((h, w), bool)
                for cls, mk in masks.items():
                    if cls in S.CLASS_INDEX:
                        char |= mk
                _background(ctx, m, rgba, char, opts.get("background_fill", ctx.cfg["background"]["fill"]))
            except Cancelled:
                raise
            except Exception as e:  # noqa: BLE001
                m["errors"].append(_classify(e, "inpaint", "INPAINT_FAILED").to_dict())
                status = "partial"
        finalize(m, status)
    except Cancelled as e:
        _clear_parts(ctx)
        m["parts"], m["skipped"], m["keypoints"] = [], [], []
        m["errors"].append(e.to_dict())
        finalize(m, "cancelled")
    except BaseException as e:  # noqa: BLE001
        ve = _classify(e, "make_layers", "INTERNAL_ERROR")
        _clear_parts(ctx)
        m["parts"], m["skipped"], m["keypoints"], m["background"] = [], [], [], None
        m["errors"].append(ve.to_dict())
        finalize(m, "failed")
    return _write_manifest(ctx, m)


def _matter(ctx: Ctx):
    from .matting import FlatMatter, OnnxMatter
    spec = ctx.cfg["models"]["matting"]
    if spec["backend"] == "onnx":
        return OnnxMatter(ctx.registry.model_dir("matting"), ctx.registry.prefer, ctx.registry.cpu_fallback)
    return FlatMatter()


def _background(ctx: Ctx, m: Dict[str, Any], rgba: np.ndarray, character: np.ndarray, fill: str) -> None:
    spec = ctx.cfg["models"]["inpaint"]
    engine, warn = choose_engine(fill, ctx.registry.model_dir("inpaint"), spec["backend"])
    if warn:
        _warn(m, warn)
    inp = None
    version, backend, device = "0.1.0", "algorithm", "cpu"
    if engine == "model":
        from .inpaint import OnnxInpainter
        inp = OnnxInpainter(ctx.registry.model_dir("inpaint"), ctx.registry.prefer, ctx.registry.cpu_fallback)
        version, backend, device = inp.version, "onnx", inp.device
    plate = ctx.step("inpaint", lambda: make_background_plate(rgba, character, engine=engine,
                                                              bg_cfg=ctx.cfg["background"], inpainter=inp, cancel=ctx.cancel))
    rel = "parts/background.png"
    save_png(os.path.join(ctx.job_dir, rel), plate)
    h, w = plate.shape[:2]
    m["models"]["inpaint"] = {"name": "inpaint", "version": version, "backend": backend, "device": device}
    m["background"] = {"name": "Background", "image": rel, "method": engine, "bbox": [0, 0, w, h], "z_order": -10,
                       "produced_by": {"model": "inpaint", "version": version, "backend": backend}}


def redraw_background(ctx: Ctx, req: Dict[str, Any]) -> Dict[str, Any]:
    m = new_manifest(ctx.job_id, "redraw_background", parent_job_id=req.get("parent_job_id"), output_dir=ctx.job_dir)
    try:
        rgba = ctx.step("load", lambda: _request_image(req))
        h, w = rgba.shape[:2]
        m["source"] = {"width": w, "height": h, "sha256": sha256_of(rgba)}
        if req.get("mask_path") or req.get("mask_b64"):
            mk = _request_image(req, "mask")
            character = mk[:, :, 3] > 127 if (mk[:, :, 3] < 250).any() else mk[:, :, 0] > 127
        else:
            raise VoxyError("BAD_REQUEST", "redraw_background needs a character mask", stage="load")
        _background(ctx, m, rgba, character, (req.get("options") or {}).get("fill", ctx.cfg["background"]["fill"]))
        finalize(m, "ok")
    except Cancelled as e:
        m["errors"].append(e.to_dict())
        finalize(m, "cancelled")
    except BaseException as e:  # noqa: BLE001
        m["errors"].append(_classify(e, "inpaint", "INPAINT_FAILED").to_dict())
        finalize(m, "failed")
    return _write_manifest(ctx, m)


# ------------------------------------------------------------------ hair
def _load_parent(ctx: Ctx, parent_id: str):
    pdir = ctx.resolve_job_dir(parent_id) if parent_id else None
    if not pdir or not os.path.isfile(os.path.join(pdir, "manifest.json")):
        raise VoxyError("PARENT_JOB_MISSING", f"no job {parent_id}", stage="load")
    with open(os.path.join(pdir, "manifest.json"), "r", encoding="utf-8") as f:
        return pdir, json.load(f)


def _part_mask(pdir: str, part: Dict[str, Any], hw) -> np.ndarray:
    mk = load_image(os.path.join(pdir, part["mask"]))[:, :, 0] > 127
    full = np.zeros(hw, bool)
    x, y, w, h = part["bbox"]
    full[y:y + h, x:x + w] = mk[:h, :w]
    return full


def _copy_part_files(src_dir: str, dst_dir: str, part: Dict[str, Any]) -> None:
    for key in ("image", "mask"):
        dst = os.path.join(dst_dir, part[key])
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(os.path.join(src_dir, part[key]), dst)


def subdivide_hair(ctx: Ctx, req: Dict[str, Any]) -> Dict[str, Any]:
    hc = ctx.cfg["hair"]
    opts = req.get("options") or {}
    count = int(min(max(int(opts.get("count", hc["default_count"])), hc["min_count"]), hc["max_count"]))
    smoothing = float(min(max(float(opts.get("smoothing", hc["default_smoothing"])), 0.0), 1.0))
    m = new_manifest(ctx.job_id, "subdivide_hair", parent_job_id=req.get("parent_job_id"), output_dir=ctx.job_dir)
    m["hair"] = {"subdivision": {"status": "not_run", "count": 0, "smoothing": smoothing, "pivot": None, "error": None}}
    original: Optional[Dict[str, Any]] = None
    pdir = ""
    try:
        pdir, pm = _load_parent(ctx, req.get("parent_job_id", ""))
        m["source"] = pm["source"]
        hair_parts = [p for p in pm["parts"] if p.get("class") == "hair"]
        if not hair_parts:
            raise VoxyError("NO_HAIR", "parent job has no Hair part", stage="load")
        original = hair_parts[0]
        src = load_image(os.path.join(pdir, "source.png"))
        hh, ww = src.shape[:2]
        hair_mask = _part_mask(pdir, original, (hh, ww))
        try:
            model = ctx.registry.hair()
            m["models"]["hair"] = model.info.to_dict()
            out = ctx.step("hair", lambda: model.predict(src[:, :, :3], hair_mask, ctx.cancel))
            roots = extract_roots(out.roots, hair_mask)
            labels, pivot = ctx.step("hair", lambda: split_hair(hair_mask, out.flow, roots, count, smoothing))
        except Cancelled:
            raise
        except Exception as e:  # noqa: BLE001
            ve = _classify(e, "hair", "HAIR_MODEL_FAILED")
            if ve.code not in ("HAIR_MODEL_MISSING", "OUT_OF_MEMORY", "STEP_TIMEOUT"):
                ve = VoxyError("HAIR_MODEL_FAILED", ve.message, stage="hair")
            raise ve
        pb = model.info.produced_by("hair")
        parts = []
        n = int(labels.max())
        for k in range(1, n + 1):
            mk = labels == k
            if not mk.any():
                continue
            parts.append(make_part(f"hair_{k:02d}", f"Hair {k:02d}", "Hair", "hair", "subdivision", S.Z_ORDER["hair"],
                                   src, mk, (round(float(pivot[0]), 2), round(float(pivot[1]), 2)), ctx.job_dir, pb,
                                   parent="hair"))
        if len(parts) < 1:
            raise VoxyError("HAIR_MODEL_FAILED", "no subdivisions produced", stage="hair")
        m["parts"] = parts
        m["hair"]["subdivision"].update({"status": "ok", "count": len(parts),
                                         "pivot": [round(float(pivot[0]), 2), round(float(pivot[1]), 2)]})
        _device_warnings(ctx, m)
        finalize(m, "ok")
    except BaseException as e:  # noqa: BLE001
        cancelled = isinstance(e, Cancelled)
        ve = e if isinstance(e, VoxyError) else _classify(e, "hair", "HAIR_MODEL_FAILED")
        shutil.rmtree(os.path.join(ctx.job_dir, "parts"), ignore_errors=True)
        m["parts"] = []
        if original is not None:       # the single Hair part from Make Layers is still there
            _copy_part_files(pdir, ctx.job_dir, original)
            m["parts"] = [original]
        m["errors"].append(ve.to_dict())
        if cancelled:
            finalize(m, "cancelled")
        else:
            m["hair"]["subdivision"].update({"status": "failed", "error": ve.to_dict()})
            finalize(m, "partial" if original is not None else "failed")
    return _write_manifest(ctx, m)


def combine_hair(ctx: Ctx, req: Dict[str, Any]) -> Dict[str, Any]:
    """Merge the subdivisions of an earlier subdivide_hair job back into one Hair part."""
    m = new_manifest(ctx.job_id, "combine_hair", parent_job_id=req.get("subdivision_job_id"), output_dir=ctx.job_dir)
    try:
        sdir, sm = _load_parent(ctx, req.get("subdivision_job_id", ""))
        pdir, pm = _load_parent(ctx, sm["job"].get("parent_job_id") or "")
        m["source"] = pm["source"]
        subs = [p for p in sm["parts"] if p["kind"] == "subdivision"]
        if not subs:
            raise VoxyError("NO_HAIR", "no subdivisions to combine", stage="load")
        original = [p for p in pm["parts"] if p.get("class") == "hair"][0]
        src = load_image(os.path.join(pdir, "source.png"))
        hw = src.shape[:2]
        union = np.zeros(hw, bool)
        for p in subs:
            union |= _part_mask(sdir, p, hw)
        part = make_part("hair", "Hair", "Hair", "hair", "visible", S.Z_ORDER["hair"], src, union,
                         (original["pivot"][0], original["pivot"][1]), ctx.job_dir, subs[0]["produced_by"],
                         confidence=original.get("confidence", 1.0))
        part["produced_by"] = original["produced_by"]     # the single Hair part comes from the general model
        m["parts"] = [part]
        m["models"]["general"] = pm["models"].get("general")
        finalize(m, "ok")
    except Cancelled as e:
        m["errors"].append(e.to_dict())
        finalize(m, "cancelled")
    except BaseException as e:  # noqa: BLE001
        m["errors"].append(_classify(e, "combine", "INTERNAL_ERROR").to_dict())
        finalize(m, "failed")
    return _write_manifest(ctx, m)


# ------------------------------------------------------------------ upscale
def upscale(ctx: Ctx, req: Dict[str, Any]) -> Dict[str, Any]:
    """Upscale one exported frame (the selected layer's). The result stays in a temporary file."""
    m = new_manifest(ctx.job_id, "upscale", output_dir=ctx.job_dir)
    up = ctx.upscaler or Upscaler(ctx.cfg["upscale"], ctx.app_dir)
    try:
        inp = req.get("image_path")
        if not inp:
            raise VoxyError("BAD_REQUEST", "upscale needs the exported frame (image_path)", stage="load")
        res = ctx.step("upscale", lambda: up.upscale(inp, os.path.join(ctx.job_dir, "upscale"), ctx.cancel,
                                                     float(ctx.cfg["timeouts_s"]["upscale"])))
        rel = os.path.relpath(res.output_path, ctx.job_dir).replace(os.sep, "/")
        m["source"] = {"width": res.input_size[0], "height": res.input_size[1]}
        m["models"]["upscaler"] = {"name": res.model, "version": "ncnn", "backend": "ncnn", "device": "vulkan"}
        m["upscale"] = {"status": "ok", "image": rel, "scale": int(ctx.cfg["upscale"]["scale"]), "model": res.model,
                        "tile": res.tile, "input_size": list(res.input_size), "output_size": list(res.output_size),
                        "attempts": res.attempts, "error": None}
        finalize(m, "ok")
    except Cancelled as e:
        m["errors"].append(e.to_dict())
        m["upscale"] = {"status": "failed", "image": None, "error": e.to_dict()}
        finalize(m, "cancelled")
    except BaseException as e:  # noqa: BLE001
        ve = _classify(e, "upscale", "UPSCALER_FAILED")
        if ve.code not in ("UPSCALER_MISSING", "UPSCALER_TIMEOUT", "UPSCALER_OOM", "IMAGE_UNREADABLE", "BAD_REQUEST",
                           "OUT_OF_MEMORY", "STEP_TIMEOUT"):
            ve = VoxyError("UPSCALER_FAILED", ve.message, stage="upscale")
        if ve.code == "STEP_TIMEOUT":
            ve = VoxyError("UPSCALER_TIMEOUT", ve.message, stage="upscale")
        m["errors"].append(ve.to_dict())
        m["upscale"] = {"status": "failed", "image": None, "error": ve.to_dict()}
        finalize(m, "failed")
    return _write_manifest(ctx, m)


HANDLERS = {
    "make_layers": make_layers,
    "subdivide_hair": subdivide_hair,
    "combine_hair": combine_hair,
    "upscale": upscale,
    "redraw_background": redraw_background,
}
