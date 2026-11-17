"""Hair pieces from reference sheets (data/raw), placed onto characters with random rotation,
scale, flips and colour shifts.

A sheet ``name.png`` is described by a sidecar ``name.parts.json``:

    {"pieces": [{"class": "hair", "polygon": [[x, y], ...], "anchor": [x, y]}, ...]}

``polygon`` outlines the piece on the sheet (or give ``"bbox": [x, y, w, h]`` and the piece is cut
out by keying the sheet's background colour). ``anchor`` is where the piece grows from (default: the middle
of its top edge). The labeling tool (training/shared/label_tool) writes these files.

A sheet is only loaded if provenance.status_of() says it is shareable, or ``allow_internal`` is set (the
dataset is then marked not shareable).
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np

from . import provenance


@dataclass
class Sprite:
    rgba: np.ndarray                 # h,w,4 uint8, alpha = the piece
    anchor: Tuple[float, float]      # x, y in sprite pixels
    source: str                      # sheet file, relative to data/raw
    shareable: bool


def _key_bg(img: np.ndarray, tol: int = 24) -> np.ndarray:
    border = np.concatenate([img[0], img[-1], img[:, 0], img[:, -1]]).astype(np.int16)
    bg = np.median(border, axis=0)
    return (np.abs(img.astype(np.int16) - bg).sum(axis=2) > tol)


def load_sheet(raw_dir: str, rel: str, allow_internal: bool = False) -> List[Sprite]:
    st = provenance.status_of(raw_dir, rel)
    if st.state != "shareable" and not (allow_internal and st.state == "internal_only"):
        raise PermissionError(f"{rel}: {st.state} ({st.reason}). Add a provenance row with a known license or owner "
                              f"permission, or pass --allow-internal (the dataset will be marked not shareable).")
    side = os.path.join(raw_dir, os.path.splitext(rel)[0] + ".parts.json")
    if not os.path.isfile(side):
        return []
    img = cv2.cvtColor(cv2.imread(os.path.join(raw_dir, rel), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
    meta = json.load(open(side, encoding="utf-8"))
    out: List[Sprite] = []
    for pc in meta.get("pieces", []):
        if pc.get("class") != "hair":
            continue
        if "polygon" in pc:
            poly = np.array(pc["polygon"], np.int32)
            x0, y0 = poly.min(axis=0)
            x1, y1 = poly.max(axis=0) + 1
            m = np.zeros(img.shape[:2], np.uint8)
            cv2.fillPoly(m, [poly], 255)
            m = m[y0:y1, x0:x1] > 0
        else:
            x0, y0, bw, bh = pc["bbox"]
            x1, y1 = x0 + bw, y0 + bh
            m = _key_bg(img[y0:y1, x0:x1])
        crop = img[y0:y1, x0:x1]
        rgba = np.dstack([crop, (m * 255).astype(np.uint8)])
        anchor = tuple(pc.get("anchor", [(x1 - x0) / 2, 0]))
        if m.sum() > 30:
            out.append(Sprite(rgba, (float(anchor[0]), float(anchor[1])), rel, st.state == "shareable"))
    return out


def load_all(raw_dir: str, allow_internal: bool = False) -> List[Sprite]:
    sprites: List[Sprite] = []
    if not os.path.isdir(raw_dir):
        return sprites
    for root, _, files in os.walk(raw_dir):
        for fn in sorted(files):
            if fn.lower().endswith(provenance.IMAGE_EXT):
                rel = os.path.relpath(os.path.join(root, fn), raw_dir).replace(os.sep, "/")
                if os.path.isfile(os.path.join(raw_dir, os.path.splitext(rel)[0] + ".parts.json")):
                    sprites += load_sheet(raw_dir, rel, allow_internal)
    return sprites


def _hue_shift(rgb: np.ndarray, dh: float, ds: float, dv: float) -> np.ndarray:
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV).astype(np.float32)
    hsv[..., 0] = (hsv[..., 0] + dh * 180.0) % 180.0
    hsv[..., 1] = np.clip(hsv[..., 1] * ds, 0, 255)
    hsv[..., 2] = np.clip(hsv[..., 2] * dv, 0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB)


def place_sprite(rng: np.random.Generator, sprite: Sprite, hw: Tuple[int, int], target: Tuple[float, float],
                 width_px: float) -> Tuple[np.ndarray, np.ndarray]:
    """Return (rgb, mask) of the sprite placed with its anchor at ``target``: random flip, rotation about
    the anchor (+-25 degrees), scale (so it is about ``width_px`` wide) and colour shift."""
    h, w = hw
    rgba = sprite.rgba
    ax, ay = sprite.anchor
    if rng.random() < 0.5:
        rgba = rgba[:, ::-1].copy()
        ax = rgba.shape[1] - 1 - ax
    rgb = _hue_shift(rgba[..., :3], float(rng.uniform(-0.05, 0.05)), float(rng.uniform(0.8, 1.2)), float(rng.uniform(0.85, 1.15)))
    s = width_px / max(1, rgba.shape[1]) * float(rng.uniform(0.8, 1.25))
    ang = float(rng.uniform(-25, 25))
    M = cv2.getRotationMatrix2D((ax, ay), ang, s)
    M[0, 2] += target[0] - ax
    M[1, 2] += target[1] - ay
    warp_rgb = cv2.warpAffine(rgb, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=0)
    warp_a = cv2.warpAffine(rgba[..., 3], M, (w, h), flags=cv2.INTER_NEAREST, borderValue=0)
    return warp_rgb, warp_a > 127


def assemble_hair(rng: np.random.Generator, sprites: List[Sprite], head: np.ndarray, n_pieces: int
                  ) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    """Hair from n pieces placed along the top of the head in random stacking order.
    Returns (rgb, full hair mask, piece source files in stacking order)."""
    h, w = head.shape
    ys, xs = np.where(head)
    if len(xs) == 0:
        return np.zeros((h, w, 3), np.uint8), np.zeros((h, w), bool), []
    x0, x1, y_top = xs.min(), xs.max(), ys.min()
    head_w = float(x1 - x0)
    rgb = np.zeros((h, w, 3), np.uint8)
    mask = np.zeros((h, w), bool)
    order = rng.permutation(n_pieces)                   # the random stacking order is the record
    used: List[str] = []
    for i in order:
        sp = sprites[int(rng.integers(len(sprites)))]
        t = (i + 0.5) / n_pieces
        col = int(round(x0 + t * head_w))
        col_top = np.where(head[:, min(max(col, 0), w - 1)])[0]
        top = float(col_top[0]) if len(col_top) else float(y_top)
        pr, pm = place_sprite(rng, sp, (h, w), (col, top + 0.02 * head_w), head_w * float(rng.uniform(0.45, 0.8)))
        rgb[pm] = pr[pm]
        mask |= pm
        used.append(sp.source)
    return rgb, mask, used
