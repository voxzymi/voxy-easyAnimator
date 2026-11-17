"""Model input/output preparation shared by the service and the training code.

Keeping this in one place means the model sees exactly the same pixels at training
time and at run time.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

import cv2
import numpy as np

from .imaging import foreground_mask


@dataclass
class LetterboxMeta:
    scale: float
    pad_x: int
    pad_y: int
    new_w: int
    new_h: int
    orig_w: int
    orig_h: int
    size: int


def letterbox(arr: np.ndarray, size: int, *, interp: int = cv2.INTER_AREA, pad_value=0) -> Tuple[np.ndarray, LetterboxMeta]:
    """Resize so the longer side is ``size`` and centre on a size x size canvas."""
    h, w = arr.shape[:2]
    s = size / max(h, w)
    nw, nh = max(1, int(round(w * s))), max(1, int(round(h * s)))
    if (nw, nh) != (w, h):
        i = interp if s < 1 else (cv2.INTER_LINEAR if interp == cv2.INTER_AREA else interp)
        r = cv2.resize(arr, (nw, nh), interpolation=i)
    else:
        r = arr
    if r.ndim == 2:
        r = r[:, :, None]
        squeeze = True
    else:
        squeeze = False
    canvas = np.empty((size, size, r.shape[2]), arr.dtype)
    canvas[...] = pad_value
    px, py = (size - nw) // 2, (size - nh) // 2
    canvas[py:py + nh, px:px + nw] = r
    if squeeze:
        canvas = canvas[:, :, 0]
    return canvas, LetterboxMeta(s, px, py, nw, nh, w, h, size)


def unletterbox(arr: np.ndarray, meta: LetterboxMeta, *, interp: int = cv2.INTER_LINEAR) -> np.ndarray:
    """Inverse of ``letterbox`` for an array defined on the size x size canvas (any channel count)."""
    f = arr.shape[0] / meta.size        # the array may be at a lower resolution (e.g. heat-maps)
    x0, y0 = int(round(meta.pad_x * f)), int(round(meta.pad_y * f))
    x1, y1 = int(round((meta.pad_x + meta.new_w) * f)), int(round((meta.pad_y + meta.new_h) * f))
    crop = arr[y0:y1, x0:x1]
    return cv2.resize(crop, (meta.orig_w, meta.orig_h), interpolation=interp)


def general_input(rgba: np.ndarray, size: int) -> Tuple[np.ndarray, LetterboxMeta]:
    """RGBA picture -> float32 [1, 4, size, size]: RGB in [-1, 1] and the foreground mask."""
    rgb = rgba[:, :, :3].astype(np.float32)
    a = rgba[:, :, 3:4].astype(np.float32) / 255.0
    rgb = rgb * a + 255.0 * (1 - a)                       # transparent areas become white
    fg = foreground_mask(rgba).astype(np.float32)[:, :, None]
    x = np.concatenate([(rgb / 255.0 - 0.5) / 0.5, fg], axis=2)
    pad = np.array([1.0, 1.0, 1.0, 0.0], np.float32)       # white, not foreground
    lb, meta = letterbox(x, size, interp=cv2.INTER_AREA, pad_value=0)
    # fix the padding colour: letterbox filled zeros; set pad area to white/not-fg
    mask = np.ones((size, size), bool)
    mask[meta.pad_y:meta.pad_y + meta.new_h, meta.pad_x:meta.pad_x + meta.new_w] = False
    lb[mask] = pad
    return np.ascontiguousarray(lb.transpose(2, 0, 1)[None]), meta


def hair_input(rgb: np.ndarray, hair_mask: np.ndarray, size: int, margin_frac: float = 0.08
               ) -> Tuple[np.ndarray, LetterboxMeta, Tuple[int, int, int, int]]:
    """Crop to the hair (plus a margin) and prepare [1, 4, size, size]: RGB in [-1, 1] and the hair mask."""
    ys, xs = np.where(hair_mask)
    h, w = hair_mask.shape
    if len(xs) == 0:
        raise ValueError("empty hair mask")
    mx = int((xs.max() - xs.min() + 1) * margin_frac) + 2
    my = int((ys.max() - ys.min() + 1) * margin_frac) + 2
    x0, x1 = max(0, xs.min() - mx), min(w, xs.max() + 1 + mx)
    y0, y1 = max(0, ys.min() - my), min(h, ys.max() + 1 + my)
    crop = rgb[y0:y1, x0:x1].astype(np.float32)
    m = hair_mask[y0:y1, x0:x1].astype(np.float32)[:, :, None]
    x = np.concatenate([(crop / 255.0 - 0.5) / 0.5, m], axis=2)
    lb, meta = letterbox(x, size, interp=cv2.INTER_AREA, pad_value=0)
    return np.ascontiguousarray(lb.transpose(2, 0, 1)[None]), meta, (int(x0), int(y0), int(x1 - x0), int(y1 - y0))
