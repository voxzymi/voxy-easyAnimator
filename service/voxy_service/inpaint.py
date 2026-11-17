"""Filling the area the character covered.

Two engines, chosen by the user:

* ``model``: a large-mask inpainting network (LaMa-style) loaded from ``models/inpaint/<version>/``.
* ``patch``: a colour-aware patch-based fill for flat or gently shaded backgrounds. It works on the
  hole's neighbourhood at reduced resolution (Lab colour, onion-peel order, best masked-SSD patch).

Both fill only the hole; ``blend_fill`` then feathers the seam so the edge does not show.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np

from .cancel import CancelToken
from .errors import VoxyError
from .preproc import letterbox, unletterbox


def _roi(hole: np.ndarray, margin_frac: float = 0.6, min_margin: int = 24) -> Tuple[int, int, int, int]:
    ys, xs = np.where(hole)
    h, w = hole.shape
    bw, bh = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
    m = int(max(min_margin, margin_frac * max(bw, bh)))
    return (max(0, xs.min() - m), max(0, ys.min() - m), min(w, xs.max() + 1 + m), min(h, ys.max() + 1 + m))


def patch_fill(rgb: np.ndarray, hole: np.ndarray, *, patch: int = 9, work_max: int = 256,
               cancel: Optional[CancelToken] = None, max_candidates: int = 5000, seed: int = 0) -> np.ndarray:
    """Return an RGB image where ``hole`` pixels are filled from the rest of the picture."""
    if not hole.any():
        return rgb.copy()
    x0, y0, x1, y1 = _roi(hole)
    roi = rgb[y0:y1, x0:x1]
    hm = hole[y0:y1, x0:x1]
    rh, rw = hm.shape
    s = min(1.0, work_max / max(rh, rw))
    if s < 1.0:
        wr = cv2.resize(roi, (max(8, int(rw * s)), max(8, int(rh * s))), interpolation=cv2.INTER_AREA)
        wm = cv2.resize(hm.astype(np.uint8), (wr.shape[1], wr.shape[0]), interpolation=cv2.INTER_NEAREST) > 0
    else:
        wr, wm = roi.copy(), hm.copy()
    H, W = wm.shape
    p = patch | 1
    r = p // 2
    lab = cv2.cvtColor(wr, cv2.COLOR_RGB2LAB).astype(np.float32)
    known = ~wm
    if known.sum() < p * p:
        raise VoxyError("INPAINT_FAILED", "not enough background around the character to copy from", stage="inpaint")
    conf = known.astype(np.float32)
    padL = np.pad(lab, ((r, r), (r, r), (0, 0)), mode="edge")

    # candidate source patches: fully known windows
    kn_i = cv2.erode(known.astype(np.uint8), np.ones((p, p), np.uint8), borderType=cv2.BORDER_CONSTANT, borderValue=0) > 0
    cy, cx = np.where(kn_i)
    rng = np.random.default_rng(seed)
    if len(cy) > max_candidates:
        sel = rng.choice(len(cy), max_candidates, replace=False)
        cy, cx = cy[sel], cx[sel]
    if len(cy) == 0:
        raise VoxyError("INPAINT_FAILED", "no clean patches to copy from", stage="inpaint")
    offs = np.arange(-r, r + 1)
    iy = (cy[:, None, None] + offs[None, :, None] + r)
    ix = (cx[:, None, None] + offs[None, None, :] + r)
    cand = padL[iy, ix].reshape(len(cy), -1)           # N, p*p*3

    work = lab.copy()
    wm_cur = wm.copy()
    guard = 0
    while wm_cur.any():
        guard += 1
        if cancel:
            cancel.check("inpaint")
        if guard > 20000:
            raise VoxyError("INPAINT_FAILED", "patch fill did not converge", stage="inpaint")
        front = wm_cur & (cv2.dilate((~wm_cur).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0)
        fy, fx = np.where(front)
        if len(fy) == 0:
            break
        # confidence of the patch around each front pixel
        cpad = np.pad(conf, r)
        csum = cv2.boxFilter(cpad, -1, (p, p), normalize=True, borderType=cv2.BORDER_CONSTANT)[r:-r, r:-r]
        k = int(np.argmax(csum[fy, fx]))
        py, px = int(fy[k]), int(fx[k])
        wpad = np.pad(work, ((r, r), (r, r), (0, 0)), mode="edge")
        tgt = wpad[py:py + p, px:px + p]
        mk = np.pad((~wm_cur).astype(np.float32), r)[py:py + p, px:px + p]
        diff = cand - tgt.reshape(1, -1)
        w3 = np.repeat(mk.reshape(-1), 3)[None, :]
        err = ((diff * diff) * w3).sum(axis=1)
        best = int(np.argmin(err))
        src = cand[best].reshape(p, p, 3)
        y_lo, y_hi = max(0, py - r), min(H, py + r + 1)
        x_lo, x_hi = max(0, px - r), min(W, px + r + 1)
        sy0, sx0 = y_lo - (py - r), x_lo - (px - r)
        sub_src = src[sy0:sy0 + (y_hi - y_lo), sx0:sx0 + (x_hi - x_lo)]
        sub_unknown = wm_cur[y_lo:y_hi, x_lo:x_hi]
        work[y_lo:y_hi, x_lo:x_hi][sub_unknown] = sub_src[sub_unknown]
        conf[y_lo:y_hi, x_lo:x_hi][sub_unknown] = float(csum[py, px])
        wm_cur[y_lo:y_hi, x_lo:x_hi] = False

    filled_w = cv2.cvtColor(np.clip(work, 0, 255).astype(np.uint8), cv2.COLOR_LAB2RGB)
    filled = cv2.resize(filled_w, (rw, rh), interpolation=cv2.INTER_CUBIC) if s < 1.0 else filled_w
    out = rgb.copy()
    region = out[y0:y1, x0:x1]
    region[hm] = filled[hm]
    return out


class OnnxInpainter:
    """Large-mask inpainting network. Inputs: image [1,3,S,S] in [0,1], mask [1,1,S,S] (1 = fill).
    Output: [1,3,S,S] in [0,1] (or [0,255]; model.json says which)."""

    backend = "onnx"
    device = "cpu"

    def __init__(self, model_dir: str, prefer: str = "cuda", cpu_fallback: bool = True):
        from .models.onnx_models import make_session, read_meta
        path = os.path.join(model_dir, "model.onnx")
        if not os.path.isfile(path):
            raise VoxyError("INPAINT_MODEL_MISSING", f"missing {path}", stage="inpaint")
        meta = read_meta(model_dir)
        self.version = str(meta.get("version", "0"))
        self.size = int(meta.get("input_size", 512))
        self.out_range = float(meta.get("output_range", 1.0))
        self.sess, self.device, _ = make_session(path, prefer=prefer, cpu_fallback=cpu_fallback)
        names = [i.name for i in self.sess.get_inputs()]
        self._img_name, self._mask_name = names[0], names[1]

    def fill(self, rgb: np.ndarray, hole: np.ndarray, cancel: CancelToken) -> np.ndarray:
        x0, y0, x1, y1 = _roi(hole)
        roi = rgb[y0:y1, x0:x1]
        hm = hole[y0:y1, x0:x1].astype(np.uint8) * 255
        lb_img, meta = letterbox(roi, self.size, interp=cv2.INTER_AREA, pad_value=255)
        lb_msk, _ = letterbox(hm, self.size, interp=cv2.INTER_NEAREST, pad_value=0)
        img = (lb_img.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
        msk = (lb_msk > 127).astype(np.float32)[None, None]
        cancel.check("inpaint")
        out = self.sess.run(None, {self._img_name: img, self._mask_name: msk})[0][0].transpose(1, 2, 0)
        out = np.clip(out / self.out_range, 0, 1)
        back = unletterbox((out * 255).astype(np.uint8), meta, interp=cv2.INTER_CUBIC)
        res = rgb.copy()
        region = res[y0:y1, x0:x1]
        sel = hole[y0:y1, x0:x1]
        region[sel] = back[sel]
        return res


def fill_region_for(hole: np.ndarray, feather_px: float) -> np.ndarray:
    """The area to inpaint: the hole plus a ring of ``feather_px`` (the ring was background already)."""
    r = int(np.ceil(max(0.0, feather_px))) + 1
    return cv2.dilate(hole.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))) > 0


def blend_fill(original: np.ndarray, filled: np.ndarray, hole: np.ndarray, feather_px: float) -> np.ndarray:
    """Cross-fade the fill into the original across a ring just outside the hole.

    Weight is 1 on the hole (the character's old position, where only the fill is correct) and
    falls to 0 over ``feather_px`` pixels outside it, so the fill's edge cannot show as a seam.
    ``filled`` must have been produced for ``fill_region_for(hole, feather_px)``.
    """
    if feather_px <= 0:
        a = hole.astype(np.float32)
    else:
        dist = cv2.distanceTransform((~hole).astype(np.uint8), cv2.DIST_L2, 3)
        a = np.clip(1.0 - dist / float(feather_px), 0.0, 1.0)
    a3 = a[:, :, None]
    return np.clip(filled.astype(np.float32) * a3 + original.astype(np.float32) * (1 - a3), 0, 255).astype(np.uint8)
