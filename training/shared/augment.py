"""Photometric and scan augmentations, and mirroring. None of these move pixels except the mirror,
which is applied to the image, every mask, the key points and the hair flow together."""
from __future__ import annotations

from typing import Dict, Tuple

import cv2
import numpy as np

import training  # noqa: F401
from voxy_service import schema as S

RGB = Tuple[int, int, int]


def make_background(rng: np.random.Generator, hw: Tuple[int, int]) -> Tuple[np.ndarray, str]:
    h, w = hw
    kind = str(rng.choice(["flat", "gradient", "noise"], p=[0.5, 0.3, 0.2]))
    base = np.array([rng.uniform(150, 255), rng.uniform(150, 255), rng.uniform(150, 255)], np.float32)
    if kind == "flat":
        bg = np.broadcast_to(base, (h, w, 3)).copy()
    elif kind == "gradient":
        other = np.clip(base + rng.uniform(-60, 60, 3), 0, 255)
        t = (np.linspace(0, 1, h)[:, None] * np.cos(rng.uniform(0, 3)) + np.linspace(0, 1, w)[None, :] * np.sin(rng.uniform(0, 3)))
        t = (t - t.min()) / max(1e-6, t.max() - t.min())
        bg = base[None, None] * (1 - t[..., None]) + other[None, None] * t[..., None]
    else:
        n = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 6)
        n = n / max(1e-6, float(np.abs(n).max()))
        bg = base[None, None] + 25 * n[..., None]
    return np.clip(bg, 0, 255).astype(np.uint8), kind


def color_shift(rng: np.random.Generator, rgb: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV).astype(np.float32)
    hsv[..., 0] = (hsv[..., 0] + rng.uniform(-6, 6)) % 180
    hsv[..., 1] = np.clip(hsv[..., 1] * rng.uniform(0.85, 1.15), 0, 255)
    hsv[..., 2] = np.clip(hsv[..., 2] * rng.uniform(0.9, 1.1), 0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB)


def scan_artifacts(rng: np.random.Generator, img: np.ndarray) -> Tuple[np.ndarray, Dict]:
    """JPEG compression, paper tint, uneven lighting, blur, grain and dust, each with a random strength."""
    p: Dict = {}
    out = img.astype(np.float32)
    if rng.random() < 0.6:
        p["blur_sigma"] = float(rng.uniform(0.3, 1.1))
        out = cv2.GaussianBlur(out, (0, 0), p["blur_sigma"])
    if rng.random() < 0.5:
        tint = np.array([rng.uniform(0.94, 1.0), rng.uniform(0.92, 1.0), rng.uniform(0.86, 1.0)], np.float32)
        p["paper_tint"] = [float(t) for t in tint]
        out *= tint
    if rng.random() < 0.5:
        h, w = img.shape[:2]
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        a = float(rng.uniform(0.03, 0.12))
        light = 1 - a * ((xx / w - rng.uniform(0.2, 0.8)) ** 2 + (yy / h - rng.uniform(0.2, 0.8)) ** 2)
        p["vignette"] = a
        out *= light[..., None]
    if rng.random() < 0.7:
        p["grain_sigma"] = float(rng.uniform(1.0, 6.0))
        out += rng.normal(0, p["grain_sigma"], out.shape)
    if rng.random() < 0.3:
        n = int(rng.integers(5, 40))
        p["dust_specks"] = n
        h, w = img.shape[:2]
        for _ in range(n):
            cv2.circle(out, (int(rng.integers(w)), int(rng.integers(h))), int(rng.integers(1, 3)),
                       tuple(float(v) for v in rng.uniform(60, 140, 3)), -1)
    out = np.clip(out, 0, 255).astype(np.uint8)
    if rng.random() < 0.7:
        p["jpeg_quality"] = int(rng.integers(30, 96))
        ok, buf = cv2.imencode(".jpg", cv2.cvtColor(out, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, p["jpeg_quality"]])
        out = cv2.cvtColor(cv2.imdecode(buf, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
    return out, p


def flip_labels(labels: np.ndarray) -> np.ndarray:
    """Mirror left-right and swap l/r classes."""
    lut = np.array(S.FLIP_CLASS_INDEX, np.uint8)
    return lut[labels[:, ::-1]]


def flip_keypoints(kps: Dict[str, Tuple[float, float, bool]], width: int) -> Dict[str, Tuple[float, float, bool]]:
    return {S.FLIP_KEYPOINT[k]: (float(width - 1 - x), y, v) for k, (x, y, v) in kps.items()}


def flip_flow(flow: np.ndarray) -> np.ndarray:
    f = flow[:, ::-1].copy()
    f[..., 0] *= -1
    return f
