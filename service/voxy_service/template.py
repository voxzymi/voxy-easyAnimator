"""Canonical character layout.

A small parametric description of a front-facing anime-proportioned character,
rasterised into part masks with an exact visible/hidden split. It is used in
two places:

* the stub models (so the service pipeline can be tested before real models
  exist), and
* the synthetic-art generator in ``training/shared`` (so the generator knows the
  true mask of every part).

Coordinates are in "character-height units": the character is 1.0 tall, and
``cx`` is the horizontal centre. ``_l`` is the character's left, which is on the
+x side of the image.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from . import schema as S

Point = Tuple[float, float]


@dataclass
class Layout:
    cx: float = 0.5
    head_cy: float = 0.135
    head_rx: float = 0.105
    head_ry: float = 0.115
    head_tilt: float = 0.0            # degrees, about the neck
    neck_y: float = 0.235
    hair_len: float = 0.37            # y where the side locks end
    eye_dx: float = 0.045
    eye_rx: float = 0.026
    eye_ry: float = 0.024
    bangs_drop: float = 0.135         # y of the bang corners (larger = more eye covered)
    shoulder_y: float = 0.31
    shoulder_dx: float = 0.075
    arm_angle: Tuple[float, float] = (8.0, 8.0)   # (l, r) degrees away from the body
    elbow_bend: Tuple[float, float] = (6.0, 6.0)
    upper_arm: float = 0.10
    lower_arm: float = 0.10
    arm_w: float = 0.032
    hand_r: float = 0.022
    hip_y: float = 0.54
    hip_dx: float = 0.032
    leg_angle: Tuple[float, float] = (2.0, 2.0)
    knee_bend: Tuple[float, float] = (0.0, 0.0)
    thigh: float = 0.20
    shin: float = 0.19
    leg_w: float = 0.045
    foot_rx: float = 0.036
    foot_ry: float = 0.017
    torso_w: float = 1.0              # width multiplier

    def with_(self, **kw) -> "Layout":
        return replace(self, **kw)


def _rot(p: Point, c: Point, deg: float) -> Point:
    a = math.radians(deg)
    dx, dy = p[0] - c[0], p[1] - c[1]
    return (c[0] + dx * math.cos(a) - dy * math.sin(a), c[1] + dx * math.sin(a) + dy * math.cos(a))


def _dir(angle_deg: float, side: str) -> Point:
    """Unit vector pointing down and outward. side 'l' is +x."""
    a = math.radians(angle_deg)
    sx = 1.0 if side == "l" else -1.0
    return (sx * math.sin(a), math.cos(a))


@dataclass
class Shape:
    cls: str
    kind: str                       # ellipse | poly | polyline
    pts: List[Point] = field(default_factory=list)
    center: Point = (0.0, 0.0)
    axes: Tuple[float, float] = (0.0, 0.0)
    angle: float = 0.0
    width: float = 0.0              # polyline width
    subtract: Optional[str] = None  # another class whose full extent is removed first


def build_shapes(L: Layout) -> Tuple[List[Shape], Dict[str, Point]]:
    """Return the shapes (full extent, before occlusion) and the keypoints."""
    cx = L.cx
    neck = (cx, L.neck_y)
    tilt = L.head_tilt

    def H(p: Point) -> Point:           # head-group transform
        return _rot(p, neck, tilt)

    shapes: List[Shape] = []
    kp: Dict[str, Point] = {}
    hy = L.head_cy

    # ---- head, neck
    shapes.append(Shape("head", "ellipse", center=H((cx, hy)), axes=(L.head_rx, L.head_ry), angle=tilt))
    shapes.append(Shape("head", "poly", pts=[H((cx - 0.022, L.neck_y - 0.03)), H((cx + 0.022, L.neck_y - 0.03)),
                                             (cx + 0.024, L.shoulder_y + 0.01), (cx - 0.024, L.shoulder_y + 0.01)]))
    # ---- face parts (left = +x)
    ey = hy + 0.013
    for side, sx in (("l", 1.0), ("r", -1.0)):
        ec = H((cx + sx * L.eye_dx, ey))
        shapes.append(Shape(f"eye_{side}", "ellipse", center=ec, axes=(L.eye_rx, L.eye_ry), angle=tilt))
        shapes.append(Shape(f"iris_{side}", "ellipse", center=H((cx + sx * L.eye_dx, ey + 0.002)),
                            axes=(L.eye_rx * 0.55, L.eye_ry * 0.85), angle=tilt))
        # eyelash: a thick arc along the top edge of the eye
        arc = []
        for t in np.linspace(math.pi * 1.05, math.pi * 1.95, 9):
            arc.append(H((cx + sx * L.eye_dx + math.cos(t) * (L.eye_rx + 0.003),
                          ey + math.sin(t) * (L.eye_ry + 0.003))))
        shapes.append(Shape(f"eyelash_{side}", "polyline", pts=arc, width=0.006))
        shapes.append(Shape(f"eyebrow_{side}", "polyline",
                            pts=[H((cx + sx * (L.eye_dx - 0.026), ey - 0.044)),
                                 H((cx + sx * (L.eye_dx + 0.028), ey - 0.050))], width=0.008))
        kp[f"eye_{side}"] = H((cx + sx * L.eye_dx, ey))
    shapes.append(Shape("nose", "ellipse", center=H((cx, ey + 0.032)), axes=(0.009, 0.007), angle=tilt))
    shapes.append(Shape("mouth", "ellipse", center=H((cx, ey + 0.057)), axes=(0.022, 0.008), angle=tilt))
    kp["mouth"] = H((cx, ey + 0.057))

    # ---- hair: back hair (minus head) + bangs + side locks, a single class
    back = Shape("hair", "ellipse", center=H((cx, hy - 0.012)), axes=(L.head_rx + 0.04, L.head_ry + 0.02),
                 angle=tilt, subtract="head")
    shapes.append(back)
    bd = L.bangs_drop
    shapes.append(Shape("hair", "poly", pts=[H(p) for p in [
        (cx - 0.112, hy - 0.135), (cx + 0.112, hy - 0.135), (cx + 0.112, bd), (cx + 0.06, bd),
        (cx, hy - 0.03), (cx - 0.06, bd), (cx - 0.112, bd)]]))
    for sx in (1.0, -1.0):
        shapes.append(Shape("hair", "poly", pts=[
            H((cx + sx * 0.100, hy - 0.02)), H((cx + sx * 0.150, hy)),
            (cx + sx * 0.158, L.hair_len), (cx + sx * 0.108, L.hair_len - 0.02),
            H((cx + sx * 0.094, hy + 0.06))]))

    # ---- torso
    tw = L.torso_w
    sy, sd = L.shoulder_y, L.shoulder_dx
    shapes.append(Shape("torso", "poly", pts=[
        (cx - sd * tw, sy - 0.015), (cx + sd * tw, sy - 0.015), (cx + 0.072 * tw, 0.38),
        (cx + 0.056 * tw, 0.47), (cx + 0.068 * tw, L.hip_y + 0.02), (cx - 0.068 * tw, L.hip_y + 0.02),
        (cx - 0.056 * tw, 0.47), (cx - 0.072 * tw, 0.38)]))

    # ---- arms and hands
    for i, (side, sx) in enumerate((("l", 1.0), ("r", -1.0))):
        sh = (cx + sx * sd * tw, sy)
        a = L.arm_angle[i]
        el = (sh[0] + L.upper_arm * _dir(a, side)[0], sh[1] + L.upper_arm * _dir(a, side)[1])
        a2 = a + L.elbow_bend[i]
        wr = (el[0] + L.lower_arm * _dir(a2, side)[0], el[1] + L.lower_arm * _dir(a2, side)[1])
        hc = (wr[0] + 0.022 * _dir(a2, side)[0], wr[1] + 0.022 * _dir(a2, side)[1])
        shapes.append(Shape(f"arm_{side}", "polyline", pts=[sh, el, wr], width=L.arm_w))
        shapes.append(Shape(f"hand_{side}", "ellipse", center=hc, axes=(L.hand_r, L.hand_r * 1.15), angle=0))
        kp[f"shoulder_{side}"], kp[f"elbow_{side}"], kp[f"wrist_{side}"] = sh, el, wr

    # ---- legs and feet
    for i, (side, sx) in enumerate((("l", 1.0), ("r", -1.0))):
        hp = (cx + sx * L.hip_dx, L.hip_y)
        a = L.leg_angle[i]
        kn = (hp[0] + L.thigh * _dir(a, side)[0], hp[1] + L.thigh * _dir(a, side)[1])
        a2 = a + L.knee_bend[i]
        an = (kn[0] + L.shin * _dir(a2, side)[0], kn[1] + L.shin * _dir(a2, side)[1])
        shapes.append(Shape(f"leg_{side}", "polyline", pts=[hp, kn, an], width=L.leg_w))
        shapes.append(Shape(f"foot_{side}", "ellipse", center=(an[0] + sx * 0.012, an[1] + 0.016),
                            axes=(L.foot_rx, L.foot_ry), angle=0))
        kp[f"hip_{side}"], kp[f"knee_{side}"], kp[f"ankle_{side}"] = hp, kn, an
    return shapes, kp


def _raster(shape: Shape, hw: Tuple[int, int], scale: float, ox: float, oy: float) -> np.ndarray:
    h, w = hw
    m = np.zeros((h, w), np.uint8)

    def P(p: Point) -> Tuple[int, int]:
        return int(round(ox + p[0] * scale)), int(round(oy + p[1] * scale))

    if shape.kind == "ellipse":
        ax = (max(1, int(round(shape.axes[0] * scale))), max(1, int(round(shape.axes[1] * scale))))
        cv2.ellipse(m, P(shape.center), ax, shape.angle, 0, 360, 255, -1)
    elif shape.kind == "poly":
        cv2.fillPoly(m, [np.array([P(p) for p in shape.pts], np.int32)], 255)
    elif shape.kind == "polyline":
        th = max(1, int(round(shape.width * scale)))
        pts = [P(p) for p in shape.pts]
        for a, b in zip(pts[:-1], pts[1:]):
            cv2.line(m, a, b, 255, th)
        if th > 2:
            for p in pts:
                cv2.circle(m, p, th // 2, 255, -1)
    return m > 0


@dataclass
class TemplateRender:
    labels: np.ndarray                      # HxW uint8, visible class index (0 = background)
    full: Dict[str, np.ndarray]             # class -> full-extent bool mask (before occlusion)
    keypoints: Dict[str, Point]             # pixel coordinates
    z_order: Dict[str, int]


def render_template(L: Layout, hw: Tuple[int, int], height_px: float, origin: Point,
                    z_order: Optional[Dict[str, int]] = None,
                    override_full: Optional[Dict[str, np.ndarray]] = None) -> TemplateRender:
    """Rasterise the layout. ``origin`` is the pixel position of unit (0, 0). ``z_order`` overrides
    the default stacking (the dataset generator uses this to vary which parts are in front)."""
    shapes, kp = build_shapes(L)
    ox, oy = origin
    full: Dict[str, np.ndarray] = {}
    for sh in shapes:
        m = _raster(sh, hw, height_px, ox, oy)
        full[sh.cls] = (full[sh.cls] | m) if sh.cls in full else m
    # hair: the back hair is only what is not head (the bangs and locks are in front of the head)
    back_hair_masks = [(_raster(s, hw, height_px, ox, oy)) for s in shapes if s.cls == "hair" and s.subtract]
    if back_hair_masks:
        front = np.zeros(hw, bool)
        for s in shapes:
            if s.cls == "hair" and not s.subtract:
                front |= _raster(s, hw, height_px, ox, oy)
        full["hair"] = (back_hair_masks[0] & ~full["head"]) | front
    if override_full:          # e.g. hair assembled from reference pieces instead of the template hair
        full.update(override_full)
    labels = np.zeros(hw, np.uint8)
    z = dict(S.Z_ORDER)
    if z_order:
        z.update(z_order)
    for cls in sorted(full, key=lambda c: z[c]):
        labels[full[cls]] = S.CLASS_INDEX[cls]
    kp_px = {k: (ox + v[0] * height_px, oy + v[1] * height_px) for k, v in kp.items()}
    return TemplateRender(labels=labels, full=full, keypoints=kp_px, z_order=z)


def amodal_masks(tr: TemplateRender) -> Dict[str, np.ndarray]:
    """Full extent of the occludable parts: head, eyes, mouth (channel order = S.AMODAL_CLASSES)."""
    return {c: tr.full[c] for c in S.AMODAL_CLASSES if c in tr.full}
