"""Pivot (anchor point) rules: rotations should turn at the joint, not the middle."""
from __future__ import annotations

from typing import Dict, Optional, Tuple

import cv2
import numpy as np

from .imaging import centroid

KP = Dict[str, Tuple[float, float, float]]   # name -> (x, y, score)
MIN_KP_SCORE = 0.2


def _kp(kps: KP, name: str) -> Optional[Tuple[float, float]]:
    v = kps.get(name)
    if v is None or v[2] < MIN_KP_SCORE:
        return None
    return (v[0], v[1])


def _mid(a: Optional[Tuple[float, float]], b: Optional[Tuple[float, float]]) -> Optional[Tuple[float, float]]:
    if a and b:
        return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    return a or b


def hair_root_pivot(hair_mask: np.ndarray, head_mask: Optional[np.ndarray]) -> Tuple[float, float]:
    """Where the hair grows from: the hair pixels next to the head (the hairline)."""
    if head_mask is not None and head_mask.any():
        r = max(3, int(0.02 * max(hair_mask.shape)))
        near = cv2.dilate(head_mask.astype(np.uint8), np.ones((2 * r + 1, 2 * r + 1), np.uint8)) > 0
        roots = hair_mask & near
        if roots.any():
            # the upper half of the hairline, where the strands start
            ys, xs = np.where(roots)
            cut = np.percentile(ys, 50)
            sel = ys <= cut
            return (float(xs[sel].mean()), float(ys[sel].mean()))
    ys, xs = np.where(hair_mask)
    if len(xs) == 0:
        return (0.0, 0.0)
    top = ys <= np.percentile(ys, 10)
    return (float(xs[top].mean()), float(ys[top].mean()))


def compute_pivot(cls: str, mask: np.ndarray, kps: KP, *, head_mask: Optional[np.ndarray] = None) -> Tuple[float, float]:
    side = cls[-2:] if cls[-2:] in ("_l", "_r") else ""
    base = cls[:-2] if side else cls
    c = None
    if base in ("arm",):
        c = _kp(kps, f"shoulder{side}")
    elif base == "hand":
        c = _kp(kps, f"wrist{side}")
    elif base == "leg":
        c = _kp(kps, f"hip{side}")
    elif base == "foot":
        c = _kp(kps, f"ankle{side}")
    elif base in ("eye", "iris", "eyelash"):
        c = _kp(kps, f"eye{side}")
    elif base == "mouth":
        c = _kp(kps, "mouth")
    elif base == "torso":
        c = _mid(_kp(kps, "hip_l"), _kp(kps, "hip_r"))
    elif base == "head":
        sh = _mid(_kp(kps, "shoulder_l"), _kp(kps, "shoulder_r"))
        ys, xs = np.where(mask)
        if sh is not None and len(ys):
            top = float(ys.min())
            c = (sh[0], sh[1] - 0.2 * (sh[1] - top))      # the middle of the neck
    elif base == "hair":
        c = hair_root_pivot(mask, head_mask)
    if c is None:
        c = centroid(mask)
    return (round(float(c[0]), 2), round(float(c[1]), 2))
