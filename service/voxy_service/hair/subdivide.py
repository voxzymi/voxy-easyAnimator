"""Hair subdivision geometry.

Inputs: the Hair mask from the general model, and the hair model's flow field and
root heat-map. Output: an integer label map splitting the Hair mask into N
subdivisions along traced flow lines. Every Hair pixel gets exactly one label, so the
union of the subdivisions is the original Hair mask (this is what Combine relies on).

No ML in this file: it is plain geometry and is unit tested on its own.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import cv2
import numpy as np


def extract_roots(heat: np.ndarray, hair_mask: np.ndarray, *, threshold: float = 0.35, min_dist: int = 4,
                  max_roots: int = 400) -> np.ndarray:
    """Peaks of the root heat-map near the hair, as (N, 2) float32 (x, y)."""
    h, w = heat.shape
    near = cv2.dilate(hair_mask.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    k = np.ones((2 * min_dist + 1, 2 * min_dist + 1), np.uint8)
    local_max = (heat >= cv2.dilate(heat, k)) & (heat >= threshold) & near
    ys, xs = np.where(local_max)
    if len(xs) == 0:
        return np.zeros((0, 2), np.float32)
    order = np.argsort(-heat[ys, xs])
    taken: List[Tuple[int, int]] = []
    for i in order:
        x, y = int(xs[i]), int(ys[i])
        if all((x - tx) ** 2 + (y - ty) ** 2 >= min_dist ** 2 for tx, ty in taken):
            taken.append((x, y))
            if len(taken) >= max_roots:
                break
    return np.array(taken, np.float32)


def fallback_roots(hair_mask: np.ndarray, spacing: int = 4) -> np.ndarray:
    """If the model finds fewer than two roots, use the top edge of every few columns."""
    pts = []
    ys, xs = np.where(hair_mask)
    if len(xs) == 0:
        return np.zeros((0, 2), np.float32)
    for x in range(int(xs.min()), int(xs.max()) + 1, spacing):
        col = np.where(hair_mask[:, x])[0]
        if len(col):
            pts.append((x, int(col[0])))
    return np.array(pts, np.float32)


def _smooth_flow(flow: np.ndarray, sigma: float) -> np.ndarray:
    f = flow.astype(np.float32).copy()
    if sigma > 0:
        f[..., 0] = cv2.GaussianBlur(f[..., 0], (0, 0), sigma)
        f[..., 1] = cv2.GaussianBlur(f[..., 1], (0, 0), sigma)
    n = np.sqrt((f ** 2).sum(axis=2, keepdims=True))
    return f / np.maximum(n, 1e-6)


def trace_streamlines(flow: np.ndarray, hair_mask: np.ndarray, roots: np.ndarray, *, step: float = 1.0,
                      max_steps: int = 3000, gap_tolerance: int = 6, flow_sigma: float = 1.5) -> List[np.ndarray]:
    """Follow the flow field from every root until it leaves the hair. Midpoint (RK2) steps."""
    h, w = hair_mask.shape
    f = _smooth_flow(flow, flow_sigma)
    n = len(roots)
    if n == 0:
        return []
    p = roots.astype(np.float32).copy()
    alive = np.ones(n, bool)
    gap = np.zeros(n, np.int32)
    tracks: List[List[Tuple[float, float]]] = [[(float(x), float(y))] for x, y in p]

    def sample(q: np.ndarray) -> np.ndarray:
        xi = np.clip(np.rint(q[:, 0]).astype(int), 0, w - 1)
        yi = np.clip(np.rint(q[:, 1]).astype(int), 0, h - 1)
        return f[yi, xi]

    def inside(q: np.ndarray) -> np.ndarray:
        xi = np.rint(q[:, 0]).astype(int)
        yi = np.rint(q[:, 1]).astype(int)
        ok = (xi >= 0) & (xi < w) & (yi >= 0) & (yi < h)
        out = np.zeros(len(q), bool)
        out[ok] = hair_mask[yi[ok], xi[ok]]
        return out

    for _ in range(max_steps):
        if not alive.any():
            break
        idx = np.where(alive)[0]
        q = p[idx]
        d1 = sample(q)
        mid = q + 0.5 * step * d1
        d2 = sample(mid)
        nq = q + step * d2
        ins = inside(nq)
        gap[idx] = np.where(ins, 0, gap[idx] + 1)
        stop = (gap[idx] > gap_tolerance) | (nq[:, 0] < 0) | (nq[:, 0] >= w) | (nq[:, 1] < 0) | (nq[:, 1] >= h)
        for j, i in enumerate(idx):
            if not stop[j]:
                p[i] = nq[j]
                tracks[i].append((float(nq[j, 0]), float(nq[j, 1])))
        alive[idx[stop]] = False
    return [np.array(t, np.float32) for t in tracks]


def _order_roots(roots: np.ndarray) -> np.ndarray:
    """Order along the line the roots lie on (their principal axis), e.g. left to right along a hairline."""
    c = roots.mean(axis=0)
    u, s, vt = np.linalg.svd(roots - c, full_matrices=False)
    axis = vt[0]
    if abs(axis[0]) >= abs(axis[1]) and axis[0] < 0:
        axis = -axis
    elif abs(axis[1]) > abs(axis[0]) and axis[1] < 0:
        axis = -axis
    return (roots - c) @ axis


def split_hair(hair_mask: np.ndarray, flow: np.ndarray, roots: np.ndarray, count: int, smoothing: float
               ) -> Tuple[np.ndarray, np.ndarray]:
    """Split the Hair mask into ``count`` subdivisions.

    Returns (labels, pivot). ``labels`` is HxW int32: 0 outside the hair, 1..N inside,
    numbered along the root line. ``pivot`` is the shared root pivot (x, y).
    """
    count = int(max(1, count))
    smoothing = float(min(max(smoothing, 0.0), 1.0))
    h, w = hair_mask.shape
    ys, xs = np.where(hair_mask)
    if len(xs) == 0:
        return np.zeros((h, w), np.int32), np.zeros(2, np.float32)
    if len(roots) < 2:
        roots = fallback_roots(hair_mask)
    if len(roots) < 2:
        roots = np.array([[xs.mean(), ys.min()], [xs.mean() + 1, ys.min()]], np.float32)
    pivot = roots.mean(axis=0)

    count = min(count, len(roots))
    proj = _order_roots(roots)
    order = np.argsort(proj)
    groups = np.array_split(order, count)

    # crop to the hair's bounding box (plus margin) for speed
    m = 8
    x0, x1 = max(0, int(xs.min()) - m), min(w, int(xs.max()) + 1 + m)
    y0, y1 = max(0, int(ys.min()) - m), min(h, int(ys.max()) + 1 + m)
    cm = hair_mask[y0:y1, x0:x1]
    cf = flow[y0:y1, x0:x1]
    local_roots = roots - np.array([x0, y0], np.float32)

    tracks = trace_streamlines(cf, cm, local_roots)
    seeds = np.zeros(cm.shape, np.int32)
    for k, g in enumerate(groups, start=1):
        for i in g:
            t = tracks[i]
            if len(t) >= 2:
                cv2.polylines(seeds, [np.rint(t).astype(np.int32).reshape(-1, 1, 2)], False, k, 1)
            else:
                xi, yi = np.rint(t[0]).astype(int)
                if 0 <= yi < seeds.shape[0] and 0 <= xi < seeds.shape[1]:
                    seeds[yi, xi] = k
    if not (seeds > 0).any():   # no track survived: seed the first root so every pixel has a label
        rx = int(np.clip(np.rint(local_roots[0][0]), 0, cm.shape[1] - 1))
        ry = int(np.clip(np.rint(local_roots[0][1]), 0, cm.shape[0] - 1))
        seeds[ry, rx] = 1

    # every hair pixel takes the label of the nearest seed (flow-line) pixel
    seed_px = seeds > 0
    _, lab = cv2.distanceTransformWithLabels((~seed_px).astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE,
                                             labelType=cv2.DIST_LABEL_PIXEL)
    lut = np.zeros(int(lab.max()) + 1, np.int32)
    lut[lab[seed_px]] = seeds[seed_px]        # a seed pixel's own label identifies its group
    out = lut[lab]
    out[~cm] = 0

    # smoothing: blur the one-hot labels and take the arg-max, then clear speckle
    sigma = smoothing * 0.03 * max(cm.shape)
    if sigma >= 0.5:
        ks = sorted(int(v) for v in np.unique(out) if v > 0)
        stack = np.stack([cv2.GaussianBlur((out == k).astype(np.float32), (0, 0), sigma) for k in ks], axis=0)
        arg = np.array(ks, np.int32)[stack.argmax(axis=0)]
        out = np.where(cm, arg, 0).astype(np.int32)
    out = _remove_speckle(out, cm)

    # renumber 1..N in root order, dropping any label that ended up empty
    present = [int(v) for v in np.unique(out) if v > 0]
    remap = np.zeros(max(present) + 1 if present else 1, np.int32)
    for new, old in enumerate(present, start=1):
        remap[old] = new
    out = np.where(out > 0, remap[np.clip(out, 0, len(remap) - 1)], 0).astype(np.int32)

    full = np.zeros((h, w), np.int32)
    full[y0:y1, x0:x1] = out
    full[~hair_mask] = 0
    return full, pivot.astype(np.float32)


def _remove_speckle(lab: np.ndarray, mask: np.ndarray, min_frac: float = 0.02) -> np.ndarray:
    """Small disconnected fragments of a label are given to the neighbouring label that surrounds them."""
    out = lab.copy()
    total = max(1, int(mask.sum()))
    for k in [int(v) for v in np.unique(out) if v > 0]:
        mk = (out == k).astype(np.uint8)
        n, cc, stats, _ = cv2.connectedComponentsWithStats(mk, connectivity=4)
        if n <= 2:
            continue
        biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        for i in range(1, n):
            if i == biggest or stats[i, cv2.CC_STAT_AREA] >= min_frac * total:
                continue
            comp = cc == i
            ring = (cv2.dilate(comp.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0) & ~comp & mask & (out != k)
            vals = out[ring]
            vals = vals[vals > 0]
            if len(vals):
                out[comp] = np.bincount(vals).argmax()
    return out


def combine_labels(labels: np.ndarray) -> np.ndarray:
    """Merge subdivisions back into one Hair mask (the union)."""
    return labels > 0
