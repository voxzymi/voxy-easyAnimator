"""Image helpers shared by the pipeline stages."""
from __future__ import annotations

import base64
import hashlib
import io
import os
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

from .errors import VoxyError

MAX_PIXELS = 64_000_000


def decode_image(data: bytes) -> np.ndarray:
    """Decode PNG/JPEG bytes to an RGBA uint8 array."""
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception as e:  # noqa: BLE001
        raise VoxyError("IMAGE_UNREADABLE", f"cannot decode image: {e}", stage="load") from e
    if im.width * im.height > MAX_PIXELS:
        raise VoxyError("IMAGE_TOO_LARGE", f"{im.width}x{im.height}", stage="load")
    return np.array(im.convert("RGBA"))


def load_image(path: str) -> np.ndarray:
    try:
        with open(path, "rb") as f:
            return decode_image(f.read())
    except OSError as e:
        raise VoxyError("IMAGE_UNREADABLE", f"cannot open {path}: {e}", stage="load") from e


def save_png(path: str, arr: np.ndarray) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    mode = {2: "L", 3: "RGB", 4: "RGBA"}[arr.ndim if arr.ndim == 2 else arr.shape[2] if arr.shape[2] > 1 else 2]
    if arr.ndim == 3 and arr.shape[2] == 1:
        arr = arr[:, :, 0]
        mode = "L"
    tmp = path + ".tmp"
    Image.fromarray(arr, mode).save(tmp, format="PNG")
    os.replace(tmp, path)


def sha256_of(arr: np.ndarray) -> str:
    return hashlib.sha256(arr.tobytes()).hexdigest()


def b64_to_bytes(s: str) -> bytes:
    if "," in s[:64]:
        s = s.split(",", 1)[1]
    return base64.b64decode(s)


def foreground_mask(rgba: np.ndarray, tol: int = 28) -> np.ndarray:
    """True where the character is: alpha if the image has transparency, otherwise
    pixels that differ from the border colour."""
    a = rgba[:, :, 3]
    if (a < 250).any():
        return a > 127
    rgb = rgba[:, :, :3].astype(np.int16)
    border = np.concatenate([rgb[0, :], rgb[-1, :], rgb[:, 0], rgb[:, -1]])
    bg = np.median(border, axis=0)
    return np.abs(rgb - bg).sum(axis=2) > tol


def bbox_of(mask: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
    return x0, y0, x1 - x0, y1 - y0


def clean_mask(mask: np.ndarray, *, min_pixels: int, keep_frac: float = 0.2, keep_all: bool = False) -> np.ndarray:
    """Drop speckle. Keep every component >= min_pixels when ``keep_all`` (hair is often
    split in two behind the head), otherwise only components >= keep_frac of the largest."""
    m8 = mask.astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m8, connectivity=8)
    if n <= 1:
        return mask
    areas = stats[1:, cv2.CC_STAT_AREA]
    big = areas.max()
    keep = np.zeros(n, bool)
    for i in range(1, n):
        a = stats[i, cv2.CC_STAT_AREA]
        if a >= min_pixels and (keep_all or a >= keep_frac * big):
            keep[i] = True
    return keep[lab]


def outline_of(mask_crop: np.ndarray, origin: Tuple[int, int], epsilon: float = 0.8) -> List[Dict]:
    """Polygons (outer contours and holes) of a binary mask, in source-image coordinates."""
    m8 = mask_crop.astype(np.uint8) * 255
    cnts, hier = cv2.findContours(m8, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    out: List[Dict] = []
    if hier is None:
        return out
    for i, c in enumerate(cnts):
        if cv2.contourArea(c) < 2.0 and len(c) < 4:
            continue
        ap = cv2.approxPolyDP(c, epsilon, True).reshape(-1, 2)
        if len(ap) < 3:
            ap = c.reshape(-1, 2)
            if len(ap) < 3:
                continue
        pts = [[float(x + origin[0]), float(y + origin[1])] for x, y in ap]
        out.append({"points": pts, "hole": bool(hier[0][i][3] != -1)})
    return out


def centroid(mask: np.ndarray) -> Tuple[float, float]:
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return (0.0, 0.0)
    return (float(xs.mean()), float(ys.mean()))


def crop_rgba(rgba: np.ndarray, mask: np.ndarray, bbox: Tuple[int, int, int, int]) -> Tuple[np.ndarray, np.ndarray]:
    x, y, w, h = bbox
    m = mask[y:y + h, x:x + w]
    out = rgba[y:y + h, x:x + w].copy()
    out[:, :, 3] = np.where(m, rgba[y:y + h, x:x + w, 3], 0).astype(np.uint8)
    return out, (m.astype(np.uint8) * 255)
