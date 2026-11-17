"""PyTorch datasets for the general model, the hair model and the inpainting model.

All of them use the *same* preprocessing as the service (voxy_service.preproc), so the model sees the same
pixels in training and at run time. Synthetic samples come from generate_dataset.py; corrected real images
come from the labeling tool (data/labeled/general, data/labeled/hair).
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
import torch
from torch.utils.data import Dataset

import training  # noqa: F401
from voxy_service import schema as S
from voxy_service.preproc import general_input, hair_input, letterbox

K = len(S.KEYPOINTS)
A = len(S.AMODAL_CLASSES)


def _imread_rgb(p: str) -> np.ndarray:
    return cv2.cvtColor(cv2.imread(p, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)


def list_samples(root: str) -> List[str]:
    """Sample folders (those with sample.json) directly under ``root``."""
    if not os.path.isdir(root):
        return []
    return sorted(os.path.join(root, d) for d in os.listdir(root) if os.path.isfile(os.path.join(root, d, "sample.json")))


def gaussian_heatmaps(points: List[Optional[Tuple[float, float]]], size: int, sigma: float = 1.5) -> np.ndarray:
    """[K, size, size] heat-maps, 1.0 at each point."""
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    out = np.zeros((len(points), size, size), np.float32)
    for i, p in enumerate(points):
        if p is None:
            continue
        out[i] = np.exp(-((xx + 0.5 - p[0]) ** 2 + (yy + 0.5 - p[1]) ** 2) / (2 * sigma ** 2))
    return out


def random_affine(rng: np.random.Generator, h: int, w: int, max_rot: float = 8.0, scale: Tuple[float, float] = (0.85, 1.15),
                  shift: float = 0.08) -> np.ndarray:
    ang = rng.uniform(-max_rot, max_rot)
    s = rng.uniform(*scale)
    M = cv2.getRotationMatrix2D((w / 2, h / 2), ang, s)
    M[0, 2] += rng.uniform(-shift, shift) * w
    M[1, 2] += rng.uniform(-shift, shift) * h
    return M


def _warp(a: np.ndarray, M: np.ndarray, interp: int, border=cv2.BORDER_REPLICATE) -> np.ndarray:
    h, w = a.shape[:2]
    if a.ndim == 3 and a.shape[2] > 4:
        return np.stack([cv2.warpAffine(a[..., i], M, (w, h), flags=interp, borderMode=border) for i in range(a.shape[2])], axis=2)
    return cv2.warpAffine(a, M, (w, h), flags=interp, borderMode=border)


# ===================================================================== general model
class GeneralDataset(Dataset):
    """Items: synthetic samples (all labels) and corrected real images (segmentation + key points only)."""

    def __init__(self, synthetic_dirs: List[str] = (), labeled_dir: Optional[str] = None, images_dir: str = "data/raw",
                 size: int = 512, train: bool = True, p_transparent: float = 0.3, seed: int = 0):
        self.items: List[Tuple[str, str]] = []
        for d in synthetic_dirs:
            self.items += [("synthetic", p) for p in list_samples(d)]
        if labeled_dir and os.path.isdir(labeled_dir):
            for n in sorted(os.listdir(labeled_dir)):
                if os.path.isfile(os.path.join(labeled_dir, n, "labels.png")):
                    self.items.append(("labeled", os.path.join(labeled_dir, n)))
        self.images_dir, self.size, self.train, self.p_transparent, self.seed = images_dir, size, train, p_transparent, seed

    def __len__(self) -> int:
        return len(self.items)

    def _load(self, i: int):
        kind, path = self.items[i]
        if kind == "synthetic":
            info = json.load(open(os.path.join(path, "sample.json")))
            img = _imread_rgb(os.path.join(path, "image.png"))
            labels = cv2.imread(os.path.join(path, "labels.png"), cv2.IMREAD_UNCHANGED)
            z = np.load(os.path.join(path, "amodal.npz"))
            amodal = np.stack([z[f"amodal_{c}"] for c in S.AMODAL_CLASSES], axis=2).astype(np.uint8)
            hidden = np.zeros(labels.shape, np.uint8)
            for c in S.AMODAL_CLASSES:
                hidden |= z[f"hidden_{c}"].astype(np.uint8)
            comp = _imread_rgb(os.path.join(path, "completion.png"))
            kps = {k["name"]: (k["x"], k["y"], k["visible"]) for k in info["keypoints"]}
            return img, labels, amodal, hidden, comp, kps
        meta = json.load(open(os.path.join(path, "meta.json")))
        img = _imread_rgb(os.path.join(self.images_dir, meta["image"]))
        labels = cv2.imread(os.path.join(path, "labels.png"), cv2.IMREAD_UNCHANGED)
        kps = {k["name"]: (k["x"], k["y"], k.get("visible", True)) for k in json.load(open(os.path.join(path, "keypoints.json")))}
        return img, labels, None, None, None, kps

    def __getitem__(self, i: int) -> Dict[str, torch.Tensor]:
        rng = np.random.default_rng([self.seed, i, int(torch.randint(0, 2 ** 31 - 1, (1,)).item()) if self.train else 0])
        img, labels, amodal, hidden, comp, kps = self._load(i)
        h, w = labels.shape
        if self.train:
            M = random_affine(rng, h, w)
            img = _warp(img, M, cv2.INTER_LINEAR)
            labels = _warp(labels, M, cv2.INTER_NEAREST, cv2.BORDER_CONSTANT)
            if amodal is not None:
                amodal = _warp(amodal, M, cv2.INTER_NEAREST, cv2.BORDER_CONSTANT)
                hidden = _warp(hidden, M, cv2.INTER_NEAREST, cv2.BORDER_CONSTANT)
                comp = _warp(comp, M, cv2.INTER_LINEAR)
            kps = {k: (float(M[0, 0] * x + M[0, 1] * y + M[0, 2]), float(M[1, 0] * x + M[1, 1] * y + M[1, 2]), v)
                   for k, (x, y, v) in kps.items()}
        alpha = np.full((h, w), 255, np.uint8)
        if self.train and amodal is not None and rng.random() < self.p_transparent:
            alpha = ((labels > 0) * 255).astype(np.uint8)              # transparent-background variant
        rgba = np.dstack([img, alpha])
        x, meta = general_input(rgba, self.size)
        S_ = self.size
        seg, _ = letterbox(labels, S_, interp=cv2.INTER_NEAREST, pad_value=0)
        pts: List[Optional[Tuple[float, float]]] = []
        valid = np.zeros(K, np.float32)
        for j, name in enumerate(S.KEYPOINTS):
            if name in kps and kps[name][2]:
                px, py = kps[name][0] * meta.scale + meta.pad_x, kps[name][1] * meta.scale + meta.pad_y
                if 0 <= px < S_ and 0 <= py < S_:
                    pts.append((px / 4.0, py / 4.0))
                    valid[j] = 1.0
                    continue
            pts.append(None)
        heat = gaussian_heatmaps(pts, S_ // 4)
        out = {"x": torch.from_numpy(x[0]), "seg": torch.from_numpy(seg.astype(np.int64)),
               "kpt": torch.from_numpy(heat), "kpt_valid": torch.from_numpy(valid),
               "has_amodal": torch.tensor(1.0 if amodal is not None else 0.0)}
        if amodal is not None:
            am, _ = letterbox(amodal, S_, interp=cv2.INTER_NEAREST, pad_value=0)
            cm, _ = letterbox(comp, S_, interp=cv2.INTER_AREA, pad_value=255)
            hd, _ = letterbox(hidden, S_, interp=cv2.INTER_NEAREST, pad_value=0)
            wgt = np.where(hd > 0, 1.0, np.where(seg > 0, 0.1, 0.0)).astype(np.float32)
            out.update(amodal=torch.from_numpy(am.transpose(2, 0, 1).astype(np.float32)),
                       comp=torch.from_numpy(cm.transpose(2, 0, 1).astype(np.float32) / 255.0),
                       comp_w=torch.from_numpy(wgt[None]))
        else:
            out.update(amodal=torch.zeros(A, S_, S_), comp=torch.zeros(3, S_, S_), comp_w=torch.zeros(1, S_, S_))
        return out


# ===================================================================== hair model
def flow_from_roots(mask: np.ndarray, roots: np.ndarray, sigma: float = 2.5) -> np.ndarray:
    """Flow for labeled real hair (where only roots and sections are drawn): strands grow away from the roots."""
    h, w = mask.shape
    rp = np.ones((h, w), np.uint8)
    for x, y in roots:
        rp[int(np.clip(round(y), 0, h - 1)), int(np.clip(round(x), 0, w - 1))] = 0
    d = cv2.GaussianBlur(cv2.distanceTransform(rp, cv2.DIST_L2, 5), (0, 0), sigma)
    gx, gy = cv2.Sobel(d, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(d, cv2.CV_32F, 0, 1, ksize=3)
    n = np.sqrt(gx * gx + gy * gy)
    f = np.stack([np.where(n > 1e-3, gx / np.maximum(n, 1e-6), 0.0), np.where(n > 1e-3, gy / np.maximum(n, 1e-6), 1.0)], axis=2)
    return f.astype(np.float32)


class HairDataset(Dataset):
    """Hair-specific data only: the hair mask in, strand flow and root points out. Never sees the full character labels."""

    def __init__(self, synthetic_dirs: List[str] = (), labeled_dir: Optional[str] = None, images_dir: str = "data/raw",
                 size: int = 256, train: bool = True, seed: int = 0):
        self.items: List[Tuple[str, str]] = []
        for d in synthetic_dirs:
            for p in list_samples(d):
                if os.path.isfile(os.path.join(p, "hair.npz")):
                    self.items.append(("synthetic", p))
        if labeled_dir and os.path.isdir(labeled_dir):
            for n in sorted(os.listdir(labeled_dir)):
                if os.path.isfile(os.path.join(labeled_dir, n, "subdivisions.png")):
                    self.items.append(("labeled", os.path.join(labeled_dir, n)))
        self.images_dir, self.size, self.train, self.seed = images_dir, size, train, seed

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, i: int) -> Dict[str, torch.Tensor]:
        kind, path = self.items[i]
        rng = np.random.default_rng([self.seed, i, int(torch.randint(0, 2 ** 31 - 1, (1,)).item()) if self.train else 0])
        if kind == "synthetic":
            img = _imread_rgb(os.path.join(path, "image.png"))
            z = np.load(os.path.join(path, "hair.npz"))
            mask, flow, heat = z["mask"], z["flow"].astype(np.float32), z["root_heat"].astype(np.float32)
        else:
            meta = json.load(open(os.path.join(path, "meta.json")))
            img = _imread_rgb(os.path.join(self.images_dir, meta["image"]))
            mask = cv2.imread(os.path.join(path, "hair_mask.png"), cv2.IMREAD_GRAYSCALE) > 0
            roots = np.array(json.load(open(os.path.join(path, "roots.json"))), np.float32).reshape(-1, 2)
            flow = flow_from_roots(mask, roots)
            heat = np.zeros(mask.shape, np.float32)
            for x, y in roots:
                heat[int(np.clip(round(y), 0, mask.shape[0] - 1)), int(np.clip(round(x), 0, mask.shape[1] - 1))] = 1
            heat = cv2.GaussianBlur(heat, (0, 0), 2.0)
            heat /= max(float(heat.max()), 1e-6)
        in_mask = mask.copy()
        if self.train and rng.random() < 0.5:                  # the general model's hair mask is never exact
            k = int(rng.integers(1, 4))
            ker = np.ones((2 * k + 1, 2 * k + 1), np.uint8)
            in_mask = (cv2.dilate if rng.random() < 0.5 else cv2.erode)(mask.astype(np.uint8), ker) > 0
            if not in_mask.any():
                in_mask = mask
        x, meta_lb, (x0, y0, cw, ch) = hair_input(img, in_mask, self.size)
        S_ = self.size
        flow_c = letterbox(flow[y0:y0 + ch, x0:x0 + cw], S_, interp=cv2.INTER_LINEAR, pad_value=0)[0]
        flow_c = flow_c / np.maximum(np.linalg.norm(flow_c, axis=2, keepdims=True), 1e-6)
        heat_c = letterbox(heat[y0:y0 + ch, x0:x0 + cw], S_, interp=cv2.INTER_LINEAR, pad_value=0)[0]
        m_c = letterbox(mask[y0:y0 + ch, x0:x0 + cw].astype(np.uint8), S_, interp=cv2.INTER_NEAREST, pad_value=0)[0]
        if self.train and rng.random() < 0.5:                  # mirror: flip the picture and the x part of the flow together
            x = x[:, :, :, ::-1].copy()
            flow_c = flow_c[:, ::-1].copy()
            flow_c[..., 0] *= -1
            heat_c, m_c = heat_c[:, ::-1].copy(), m_c[:, ::-1].copy()
        return {"x": torch.from_numpy(np.ascontiguousarray(x[0])),
                "flow": torch.from_numpy(np.ascontiguousarray(flow_c.transpose(2, 0, 1))),
                "heat": torch.from_numpy(np.ascontiguousarray(heat_c[None])),
                "mask": torch.from_numpy(np.ascontiguousarray(m_c[None]).astype(np.float32))}


# ===================================================================== inpainting
def random_large_mask(rng: np.random.Generator, h: int, w: int) -> np.ndarray:
    m = np.zeros((h, w), np.uint8)
    for _ in range(int(rng.integers(1, 4))):
        if rng.random() < 0.5:
            x0, y0 = int(rng.integers(0, w * 3 // 4)), int(rng.integers(0, h * 3 // 4))
            m[y0:y0 + int(rng.integers(h // 6, h // 2)), x0:x0 + int(rng.integers(w // 6, w // 2))] = 1
        else:
            pts = rng.integers(0, [w, h], (int(rng.integers(3, 6)), 2))
            for a, b in zip(pts[:-1], pts[1:]):
                cv2.line(m, tuple(int(v) for v in a), tuple(int(v) for v in b), 1, int(rng.integers(h // 14, h // 6)))
    return m > 0


class InpaintDataset(Dataset):
    """Occlusion pairs: the picture with the hair/limb in front, the completed picture behind it, the covered region
    as the mask. Mixed 50/50 with random large masks on the completed picture."""

    def __init__(self, synthetic_dirs: List[str], size: int = 256, train: bool = True, seed: int = 0):
        self.items = [p for d in synthetic_dirs for p in list_samples(d)]
        self.size, self.train, self.seed = size, train, seed

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, i: int) -> Dict[str, torch.Tensor]:
        rng = np.random.default_rng([self.seed, i, int(torch.randint(0, 2 ** 31 - 1, (1,)).item()) if self.train else 0])
        p = self.items[i]
        img = _imread_rgb(os.path.join(p, "image.png"))
        comp = _imread_rgb(os.path.join(p, "completion.png"))
        z = np.load(os.path.join(p, "amodal.npz"))
        hidden = np.zeros(img.shape[:2], bool)
        for c in S.AMODAL_CLASSES:
            hidden |= z[f"hidden_{c}"]
        if hidden.sum() > 20 and rng.random() < 0.5:
            src, mask = img, cv2.dilate(hidden.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
        else:
            src, mask = comp, random_large_mask(rng, *img.shape[:2])
        im, _ = letterbox(src, self.size, interp=cv2.INTER_AREA, pad_value=255)
        tg, _ = letterbox(comp, self.size, interp=cv2.INTER_AREA, pad_value=255)
        mk, _ = letterbox(mask.astype(np.uint8), self.size, interp=cv2.INTER_NEAREST, pad_value=0)
        return {"image": torch.from_numpy(im.transpose(2, 0, 1).astype(np.float32) / 255.0),
                "target": torch.from_numpy(tg.transpose(2, 0, 1).astype(np.float32) / 255.0),
                "mask": torch.from_numpy(mk[None].astype(np.float32))}
