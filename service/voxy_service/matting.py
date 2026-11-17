"""Background removal.

Default engine (``algorithm``): a flood-fill key from the picture border. It is exact for the
flat or gradient backgrounds the install guide recommends, and it keeps bg-coloured areas
inside the character (white eyes on a white background) because only background connected
to the border is removed.

Optional engine (``onnx``): an anime matting network in ``models/matting/<version>/``.
No network ships in this repository; see docs/models.md for candidates and their licenses.
"""
from __future__ import annotations

import os
from typing import Any, Dict

import cv2
import numpy as np

from .cancel import CancelToken
from .errors import VoxyError
from .preproc import letterbox, unletterbox


def key_background(rgba: np.ndarray, tolerance: int = 14, feather: float = 0.8) -> np.ndarray:
    """Return float alpha [0,1]: 0 for background connected to the border."""
    h, w = rgba.shape[:2]
    rgb = np.ascontiguousarray(rgba[:, :, :3])
    # pad by 1 so the flood fill can walk around the whole border
    padded = cv2.copyMakeBorder(rgb, 1, 1, 1, 1, cv2.BORDER_REPLICATE)
    mask = np.zeros((h + 4, w + 4), np.uint8)
    flags = 4 | cv2.FLOODFILL_MASK_ONLY | (255 << 8)
    tol = (tolerance,) * 3
    seeds = [(0, 0), (w + 1, 0), (0, h + 1), (w + 1, h + 1)]
    seeds += [(x, 0) for x in range(0, w + 2, max(1, w // 16))] + [(x, h + 1) for x in range(0, w + 2, max(1, w // 16))]
    seeds += [(0, y) for y in range(0, h + 2, max(1, h // 16))] + [(w + 1, y) for y in range(0, h + 2, max(1, h // 16))]
    for sx, sy in seeds:
        if mask[sy + 1, sx + 1]:
            continue
        cv2.floodFill(padded.copy(), mask, (sx, sy), (0, 0, 0), tol, tol, flags)
    bg = mask[2:h + 2, 2:w + 2] > 0
    alpha = (~bg).astype(np.float32)
    if feather > 0:
        alpha = cv2.GaussianBlur(alpha, (0, 0), feather)
        alpha[bg & (alpha < 0.5)] = np.minimum(alpha[bg & (alpha < 0.5)], 0.5)
    return np.clip(alpha, 0, 1)


class FlatMatter:
    backend = "algorithm"
    version = "0.1.0"
    device = "n/a"

    def __init__(self, tolerance: int = 14):
        self.tolerance = tolerance

    def alpha(self, rgba: np.ndarray, cancel: CancelToken) -> np.ndarray:
        if (rgba[:, :, 3] < 250).any():           # already transparent: keep the given alpha
            return rgba[:, :, 3].astype(np.float32) / 255.0
        a = key_background(rgba, self.tolerance)
        border = np.concatenate([a[0, :], a[-1, :], a[:, 0], a[:, -1]])
        if float((border < 0.5).mean()) < 0.5 or float(a.mean()) < 0.01:
            # a plain background touches most of the picture's edge; if it does not, there is nothing to key
            raise VoxyError("MATTING_FAILED", "no plain background found around the picture", stage="matting")
        return a


class OnnxMatter:
    backend = "onnx"
    device = "cpu"

    def __init__(self, model_dir: str, prefer: str = "cuda", cpu_fallback: bool = True):
        from .models.onnx_models import make_session, read_meta
        path = os.path.join(model_dir, "model.onnx")
        if not os.path.isfile(path):
            raise VoxyError("MATTING_FAILED", f"missing {path}", stage="matting")
        meta = read_meta(model_dir)
        self.version = str(meta.get("version", "0"))
        self.size = int(meta.get("input_size", 1024))
        self.sess, self.device, _ = make_session(path, prefer=prefer, cpu_fallback=cpu_fallback)
        self._in = self.sess.get_inputs()[0].name

    def alpha(self, rgba: np.ndarray, cancel: CancelToken) -> np.ndarray:
        rgb = rgba[:, :, :3].astype(np.float32) / 255.0
        lb, meta = letterbox(rgb, self.size, interp=cv2.INTER_AREA, pad_value=1.0)
        mean, std = np.array([0.485, 0.456, 0.406], np.float32), np.array([0.229, 0.224, 0.225], np.float32)
        x = ((lb - mean) / std).transpose(2, 0, 1)[None].astype(np.float32)
        cancel.check("matting")
        y = self.sess.run(None, {self._in: x})[0][0, 0]
        y = 1 / (1 + np.exp(-y)) if (y.min() < 0 or y.max() > 1) else y
        return np.clip(unletterbox(y.astype(np.float32), meta), 0, 1)
