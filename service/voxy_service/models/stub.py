"""Stub models: fixed, deterministic outputs for testing the rest of the pipeline.

They are not machine learning. The general stub lays the canonical template over
the character's silhouette; the hair stub returns a fan-shaped flow field and roots
along the top edge of the hair. Release builds refuse to load them (see registry.py).
"""
from __future__ import annotations

import cv2
import numpy as np

from .. import schema as S
from ..cancel import CancelToken
from ..imaging import bbox_of, foreground_mask
from ..template import Layout, amodal_masks, render_template
from .base import GeneralOutput, HairOutput, ModelInfo


class StubGeneralModel:
    def __init__(self, version: str = "0.0.0-stub", fail: bool = False, delay_s: float = 0.0):
        self.info = ModelInfo("general-stub", version, "stub", "cpu")
        self.fail = fail
        self.delay_s = delay_s

    def predict(self, rgba: np.ndarray, cancel: CancelToken) -> GeneralOutput:
        import time
        t_end = time.monotonic() + self.delay_s
        while time.monotonic() < t_end:
            cancel.check("general")
            time.sleep(0.01)
        if self.fail:
            raise RuntimeError("stub general model: forced failure")
        h, w = rgba.shape[:2]
        fg = foreground_mask(rgba)
        bb = bbox_of(fg)
        if bb is None:
            return GeneralOutput(labels=np.zeros((h, w), np.uint8))
        x, y, bw, bh = bb
        # The template spans roughly y = 0.005 .. 0.963 of its height unit.
        s = bh / 0.958
        origin = (x + bw / 2.0 - 0.5 * s, y - 0.005 * s)
        tr = render_template(Layout(), (h, w), s, origin)
        labels = np.where(fg, tr.labels, 0).astype(np.uint8)
        kps = {k: (float(px), float(py), 0.9) for k, (px, py) in tr.keypoints.items()}
        amodal = {c: (m & fg) if c != "head" else m for c, m in amodal_masks(tr).items()}
        # completion: flat colour of the visible part, a stand-in for a learned completion
        comp = rgba[:, :, :3].copy()
        for c, m in amodal.items():
            vis = labels == S.CLASS_INDEX[c]
            if vis.any():
                comp[m & ~vis] = np.median(rgba[vis][:, :3], axis=0).astype(np.uint8)
        conf = {c: 1.0 for c in S.CLASS_NAMES[1:]}
        return GeneralOutput(labels=labels, keypoints=kps, amodal=amodal, completion_rgb=comp, class_confidence=conf)


class StubHairModel:
    def __init__(self, version: str = "0.0.0-stub", fail: bool = False):
        self.info = ModelInfo("hair-stub", version, "stub", "cpu")
        self.fail = fail

    def predict(self, rgb: np.ndarray, hair_mask: np.ndarray, cancel: CancelToken) -> HairOutput:
        if self.fail:
            raise RuntimeError("stub hair model: forced failure")
        h, w = hair_mask.shape
        ys, xs = np.where(hair_mask)
        flow = np.zeros((h, w, 2), np.float32)
        roots = np.zeros((h, w), np.float32)
        if len(xs) == 0:
            flow[..., 1] = 1.0
            return HairOutput(flow=flow, roots=roots)
        cx = float(xs.mean())
        top = float(ys.min())
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        dx = (xx - cx) * 0.05
        dy = np.maximum(yy - top, 1.0)
        n = np.sqrt(dx * dx + dy * dy)
        flow[..., 0] = dx / n
        flow[..., 1] = dy / n
        # roots: the top-most hair pixel of every 3rd column
        for x in range(int(xs.min()), int(xs.max()) + 1, 3):
            col = np.where(hair_mask[:, x])[0]
            if len(col):
                roots[col[0], x] = 1.0
        roots = cv2.GaussianBlur(roots, (0, 0), 1.5)
        roots /= max(float(roots.max()), 1e-6)
        return HairOutput(flow=flow, roots=roots)
