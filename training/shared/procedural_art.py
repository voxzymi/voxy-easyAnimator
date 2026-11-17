"""Original, procedurally drawn anime-style characters (all output is CC0: made by this code).

The renderer draws every part as its own flat-shaded layer from the shared character template, so the
exact visible mask, the full (un-occluded) extent, the colours hidden under hair, the joints and the hair
strand flow are all known. That is what makes the ground truth exact.
"""
from __future__ import annotations

import colorsys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

import training  # noqa: F401  (puts service/ on sys.path)
from voxy_service import schema as S
from voxy_service.hair.subdivide import fallback_roots, split_hair, trace_streamlines
from voxy_service.template import Layout, render_template

RGB = Tuple[int, int, int]


def _hsv(h: float, s: float, v: float) -> RGB:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, min(max(s, 0), 1), min(max(v, 0), 1))
    return int(r * 255), int(g * 255), int(b * 255)


@dataclass
class Style:
    skin: RGB
    hair: RGB
    eye_white: RGB
    iris: RGB
    pupil: RGB
    shirt: RGB
    sleeves: bool
    legwear: RGB
    legs_skin: bool
    shoe: RGB
    lash: RGB
    brow: RGB
    mouth: RGB
    line: RGB
    outline_px: int
    open_mouth: bool


def random_style(rng: np.random.Generator) -> Style:
    skin = _hsv(rng.uniform(0.04, 0.10), rng.uniform(0.10, 0.38), rng.uniform(0.82, 0.99))
    natural = [(0.0, 0.0, 0.12), (0.07, 0.6, 0.25), (0.08, 0.55, 0.45), (0.12, 0.45, 0.85), (0.02, 0.7, 0.5)]
    if rng.random() < 0.45:
        h, s, v = natural[rng.integers(len(natural))]
        hair = _hsv(h + rng.normal(0, 0.01), s + rng.normal(0, 0.05), v + rng.normal(0, 0.05))
    else:
        hair = _hsv(rng.random(), rng.uniform(0.25, 0.9), rng.uniform(0.45, 0.98))
    iris = _hsv(rng.random(), rng.uniform(0.35, 0.9), rng.uniform(0.5, 0.95))
    hair_dark = tuple(int(c * 0.55) for c in hair)
    return Style(
        skin=skin, hair=hair, eye_white=(244, 244, 248), iris=iris, pupil=(24, 20, 30),
        shirt=_hsv(rng.random(), rng.uniform(0.1, 0.8), rng.uniform(0.4, 0.98)), sleeves=bool(rng.random() < 0.4),
        legwear=_hsv(rng.random(), rng.uniform(0.0, 0.7), rng.uniform(0.15, 0.95)), legs_skin=bool(rng.random() < 0.35),
        shoe=_hsv(rng.random(), rng.uniform(0.0, 0.5), rng.uniform(0.1, 0.8)),
        lash=(30, 24, 36), brow=hair_dark, mouth=_hsv(rng.uniform(0.95, 1.02), rng.uniform(0.3, 0.7), rng.uniform(0.7, 0.95)),
        line=(28, 22, 34), outline_px=int(rng.integers(0, 3)), open_mouth=bool(rng.random() < 0.25))


def random_base_layout(rng: np.random.Generator) -> Layout:
    """Per-source body proportions (the identity)."""
    return Layout(
        head_rx=float(rng.uniform(0.092, 0.12)), head_ry=float(rng.uniform(0.10, 0.125)),
        eye_dx=float(rng.uniform(0.038, 0.052)), eye_rx=float(rng.uniform(0.021, 0.03)), eye_ry=float(rng.uniform(0.02, 0.028)),
        hair_len=float(rng.uniform(0.29, 0.56)), bangs_drop=float(rng.uniform(0.126, 0.145)),
        shoulder_dx=float(rng.uniform(0.065, 0.085)), torso_w=float(rng.uniform(0.88, 1.15)),
        thigh=float(rng.uniform(0.18, 0.22)), shin=float(rng.uniform(0.17, 0.21)),
        upper_arm=float(rng.uniform(0.09, 0.11)), lower_arm=float(rng.uniform(0.09, 0.11)),
        arm_w=float(rng.uniform(0.026, 0.038)), leg_w=float(rng.uniform(0.038, 0.054)))


def pose_variant(rng: np.random.Generator, base: Layout) -> Layout:
    """A new pose of the same character."""
    return base.with_(
        head_tilt=float(rng.normal(0, 5.0)),
        arm_angle=(float(rng.uniform(2, 38)), float(rng.uniform(2, 38))),
        elbow_bend=(float(rng.uniform(-5, 38)), float(rng.uniform(-5, 38))),
        leg_angle=(float(rng.uniform(0, 9)), float(rng.uniform(0, 9))),
        knee_bend=(float(rng.uniform(-3, 8)), float(rng.uniform(-3, 8))))


# ------------------------------------------------------------------ flat shading helpers
def _bbox(m: np.ndarray):
    ys, xs = np.where(m)
    return xs.min(), xs.max() + 1, ys.min(), ys.max() + 1


def flat_layer(mask: np.ndarray, color: RGB, *, shade: float = 0.16, outline_px: int = 0, line: RGB = (28, 22, 34)) -> np.ndarray:
    """(H,W,3) uint8 picture of one part: flat colour, a cel-shade band, optional outline. Valid where mask."""
    h, w = mask.shape
    img = np.zeros((h, w, 3), np.uint8)
    if not mask.any():
        return img
    img[:] = color
    x0, x1, y0, y1 = _bbox(mask)
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    t = (0.35 * (xx - x0) / max(1, x1 - x0) + 0.65 * (yy - y0) / max(1, y1 - y0))
    band = np.zeros_like(mask)
    band[y0:y1, x0:x1] = t > 0.74
    img[band & mask] = (np.array(color, np.float32) * (1 - shade)).astype(np.uint8)
    if outline_px > 0:
        k = np.ones((2 * outline_px + 1, 2 * outline_px + 1), np.uint8)
        inner = cv2.erode(mask.astype(np.uint8), k) > 0
        edge = mask & ~inner
        img[edge] = (np.array(line, np.float32) * 0.85 + np.array(color, np.float32) * 0.15).astype(np.uint8)
    return img


def _ellipse_in(img: np.ndarray, mask: np.ndarray, cx: float, cy: float, rx: float, ry: float, color: RGB) -> None:
    layer = np.zeros(mask.shape, np.uint8)
    cv2.ellipse(layer, (int(round(cx)), int(round(cy))), (max(1, int(round(rx))), max(1, int(round(ry)))), 0, 0, 360, 255, -1)
    img[(layer > 0) & mask] = color


# ------------------------------------------------------------------ hair
@dataclass
class HairGT:
    mask: np.ndarray                # visible hair, bool
    flow: np.ndarray                # H,W,2 float32 unit vectors (valid inside the mask)
    roots: np.ndarray               # N,2 float32 root points (x, y)
    root_heat: np.ndarray           # H,W float32 gaussian heat-map
    subdivisions: np.ndarray        # H,W int32: 0 outside, 1..M clump ids (strand sections)
    clumps: int


def make_hair_gt(rng: np.random.Generator, hair: np.ndarray, clumps: Optional[int] = None) -> Optional[HairGT]:
    """Strands by tracing curves along a random flow field that grows away from the root points."""
    if hair.sum() < 50:
        return None
    h, w = hair.shape
    spacing = int(rng.integers(4, 8))
    roots = fallback_roots(hair, spacing)
    if len(roots) < 4:
        return None
    roots = roots + rng.normal(0, 0.4, roots.shape).astype(np.float32)
    rp = np.zeros((h, w), np.uint8)
    for x, y in roots:
        rp[int(np.clip(round(y), 0, h - 1)), int(np.clip(round(x), 0, w - 1))] = 1
    dist = cv2.distanceTransform((rp == 0).astype(np.uint8), cv2.DIST_L2, 5)
    dist = cv2.GaussianBlur(dist, (0, 0), 2.5)
    gx = cv2.Sobel(dist, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(dist, cv2.CV_32F, 0, 1, ksize=3)
    n = np.sqrt(gx * gx + gy * gy)
    gx = np.where(n > 1e-3, gx / np.maximum(n, 1e-6), 0.0)
    gy = np.where(n > 1e-3, gy / np.maximum(n, 1e-6), 1.0)
    # strands wave: rotate the flow by a smooth random angle field plus a constant sway
    noise = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), max(6.0, 0.04 * max(h, w)))
    noise = noise / max(float(np.abs(noise).max()), 1e-6)
    theta = (float(rng.uniform(0.15, 0.55)) * noise + float(rng.normal(0, 0.12))).astype(np.float32)
    c, s = np.cos(theta), np.sin(theta)
    fx, fy = gx * c - gy * s, gx * s + gy * c
    flow = np.stack([fx, fy], axis=2).astype(np.float32)
    flow /= np.maximum(np.sqrt((flow ** 2).sum(axis=2, keepdims=True)), 1e-6)
    m = int(clumps if clumps is not None else rng.integers(3, 10))
    sub, _ = split_hair(hair, flow, roots, m, 0.0)
    heat = np.zeros((h, w), np.float32)
    for x, y in roots:
        heat[int(np.clip(round(y), 0, h - 1)), int(np.clip(round(x), 0, w - 1))] = 1.0
    heat = cv2.GaussianBlur(heat, (0, 0), 2.0)
    heat /= max(float(heat.max()), 1e-6)
    return HairGT(mask=hair, flow=flow, roots=roots.astype(np.float32), root_heat=heat, subdivisions=sub.astype(np.int32),
                  clumps=int(sub.max()))


def hair_layer(hair: np.ndarray, gt: Optional[HairGT], color: RGB, rng: np.random.Generator, outline_px: int, line: RGB) -> np.ndarray:
    """Flat hair drawn as strand clumps: each section gets its own shade; strand lines follow the flow."""
    img = flat_layer(hair, color, shade=0.0)
    if gt is None:
        return img
    base = np.array(color, np.float32)
    for k in range(1, gt.clumps + 1):
        mk = gt.subdivisions == k
        shade = 0.82 + 0.30 * rng.random()
        img[mk] = np.clip(base * shade, 0, 255).astype(np.uint8)
    tracks = trace_streamlines(gt.flow, hair, gt.roots[:: max(1, len(gt.roots) // 24)])
    line_img = img.copy()
    for t in tracks:
        if len(t) < 6:
            continue
        light = rng.random() < 0.5
        col = np.clip(base * (1.18 if light else 0.72), 0, 255)
        cv2.polylines(line_img, [np.rint(t).astype(np.int32).reshape(-1, 1, 2)], False, tuple(int(v) for v in col),
                      int(rng.integers(1, 3)))
    img[hair] = line_img[hair]
    if outline_px > 0:
        k = np.ones((2 * outline_px + 1, 2 * outline_px + 1), np.uint8)
        edge = hair & ~(cv2.erode(hair.astype(np.uint8), k) > 0)
        img[edge] = (np.array(line, np.float32) * 0.8 + base * 0.2).astype(np.uint8)
    return img


# ------------------------------------------------------------------ the character
@dataclass
class Render:
    rgb: np.ndarray                     # H,W,3 character colours (background not drawn)
    alpha: np.ndarray                   # H,W float32 silhouette with soft edge
    labels: np.ndarray                  # H,W uint8 visible class index
    full: Dict[str, np.ndarray]         # class -> bool full extent
    amodal: Dict[str, np.ndarray]       # head/eye_l/eye_r/mouth -> bool full extent
    completion_rgb: np.ndarray          # H,W,3 uint8: the picture with hidden parts un-covered
    hidden: Dict[str, np.ndarray]       # base class -> bool hidden region (amodal & ~visible & covered)
    keypoints: Dict[str, Tuple[float, float, bool]]   # name -> (x, y, visible)
    z_order: Dict[str, int]
    hair: Optional[HairGT]
    layout: Layout
    style: Style
    outline_px: int = 0
    hair_sources: List[str] = field(default_factory=list)


def random_z(rng: np.random.Generator) -> Dict[str, int]:
    """Which parts are in front of which, varied per sample. The true order is saved with the sample."""
    z = dict(S.Z_ORDER)
    for side in ("l", "r"):
        if rng.random() < 0.25:                     # arm and hand behind the torso
            z[f"arm_{side}"], z[f"hand_{side}"] = 15, 17
    if rng.random() < 0.5:                          # which leg is in front when they overlap
        z["leg_l"], z["leg_r"], z["foot_l"], z["foot_r"] = 13, 12, 11, 10
    return z


def render_character(rng: np.random.Generator, style: Style, base: Layout, hw: Tuple[int, int],
                     *, height_frac: Tuple[float, float] = (0.72, 0.92), z: Optional[Dict[str, int]] = None,
                     sprites: Optional[list] = None) -> Render:
    h, w = hw
    layout = pose_variant(rng, base)
    z = z or random_z(rng)
    hp = float(rng.uniform(*height_frac)) * h / 0.96
    ox = w / 2 - 0.5 * hp + rng.uniform(-0.04, 0.04) * w
    oy = (h - 0.96 * hp) / 2 + rng.uniform(-0.03, 0.03) * h
    tr = render_template(layout, hw, hp, (ox, oy), z)
    sprite_rgb, hair_sources = None, []
    if sprites:                                     # hair assembled from reference-sheet pieces
        from .sheets import assemble_hair
        sprite_rgb, mask_h, hair_sources = assemble_hair(rng, sprites, tr.full["head"], int(rng.integers(2, 5)))
        if mask_h.any():
            tr = render_template(layout, hw, hp, (ox, oy), z, override_full={"hair": mask_h})
        else:
            sprite_rgb, hair_sources = None, []
    labels, full = tr.labels, tr.full
    hair_vis = labels == S.CLASS_INDEX["hair"]
    hair_gt = make_hair_gt(rng, hair_vis)

    st = style
    colors: Dict[str, RGB] = {
        "head": st.skin, "eye_l": st.eye_white, "eye_r": st.eye_white, "iris_l": st.iris, "iris_r": st.iris,
        "eyelash_l": st.lash, "eyelash_r": st.lash, "eyebrow_l": st.brow, "eyebrow_r": st.brow,
        "nose": tuple(int(c * 0.86) for c in st.skin), "mouth": st.mouth, "hair": st.hair, "torso": st.shirt,
        "arm_l": st.shirt if st.sleeves else st.skin, "arm_r": st.shirt if st.sleeves else st.skin,
        "hand_l": st.skin, "hand_r": st.skin,
        "leg_l": st.skin if st.legs_skin else st.legwear, "leg_r": st.skin if st.legs_skin else st.legwear,
        "foot_l": st.shoe, "foot_r": st.shoe}
    op = int(np.clip(st.outline_px + rng.integers(-1, 2), 0, 3))    # line-thickness jitter per sample
    layers: Dict[str, np.ndarray] = {}
    for cls, m in full.items():
        if cls == "hair":
            if sprite_rgb is not None:
                layers[cls] = np.where(m[..., None], sprite_rgb, 0).astype(np.uint8)
            else:
                layers[cls] = hair_layer(m, hair_gt_on_full(hair_gt, m), colors[cls], rng, op, st.line)
        else:
            layers[cls] = flat_layer(m, colors[cls], shade=0.14 if cls != "eye_l" and cls != "eye_r" else 0.0,
                                     outline_px=op if cls in ("head", "torso", "arm_l", "arm_r", "leg_l", "leg_r", "hand_l", "hand_r", "foot_l", "foot_r") else 0,
                                     line=st.line)
    for side in ("l", "r"):                                         # eye details inside the iris
        im, mk = layers[f"iris_{side}"], full[f"iris_{side}"]
        if mk.any():
            x0, x1, y0, y1 = _bbox(mk)
            cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
            _ellipse_in(im, mk, cx, cy + ry * 0.05, rx * 0.5, ry * 0.5, st.pupil)
            _ellipse_in(im, mk, cx - rx * 0.35, cy - ry * 0.35, max(1.0, rx * 0.22), max(1.0, ry * 0.2), (255, 255, 255))
    if st.open_mouth and full["mouth"].any():
        x0, x1, y0, y1 = _bbox(full["mouth"])
        _ellipse_in(layers["mouth"], full["mouth"], (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) * 0.3, max(1.0, (y1 - y0) * 0.3), (120, 30, 50))

    canvas = np.zeros((h, w, 3), np.uint8)
    for cls in sorted(full, key=lambda c: tr.z_order[c]):           # paint back to front = true stacking order
        canvas[full[cls]] = layers[cls][full[cls]]
    union = labels > 0
    alpha = cv2.GaussianBlur(union.astype(np.float32), (0, 0), 0.7)
    alpha = np.where(union, np.maximum(alpha, 0.85), alpha)

    amodal = {c: full[c] for c in S.AMODAL_CLASSES if c in full}
    completion = canvas.copy()
    hidden: Dict[str, np.ndarray] = {}
    for base in S.AMODAL_CLASSES:
        vis = labels == S.CLASS_INDEX[base]
        covered = (labels != 0) & ~vis
        hm = amodal[base] & ~vis & covered
        hidden[base] = hm
        completion[hm] = layers[base][hm]
    kps = {}
    for k, (x, y) in tr.keypoints.items():
        xi, yi = int(round(x)), int(round(y))
        inside = 0 <= xi < w and 0 <= yi < h
        kps[k] = (float(x), float(y), bool(inside))
    return Render(rgb=canvas, alpha=alpha.astype(np.float32), labels=labels, full=full, amodal=amodal,
                  completion_rgb=completion, hidden=hidden, keypoints=kps, z_order=tr.z_order, hair=hair_gt, layout=layout,
                  style=st, outline_px=op, hair_sources=hair_sources)


def hair_gt_on_full(gt: Optional[HairGT], full_mask: np.ndarray) -> Optional[HairGT]:
    """The flow/strand data are defined on the visible hair; use them for the full hair layer too
    (the full layer only differs where eyebrows are drawn over it)."""
    if gt is None:
        return None
    sub = gt.subdivisions
    if full_mask.sum() != gt.mask.sum():
        # fill the few pixels the eyebrows cover with the nearest clump id
        from scipy import ndimage
        _, idx = ndimage.distance_transform_edt(sub == 0, return_indices=True)
        sub = sub[idx[0], idx[1]] * full_mask
    return HairGT(mask=full_mask, flow=gt.flow, roots=gt.roots, root_heat=gt.root_heat, subdivisions=sub.astype(np.int32),
                  clumps=gt.clumps)
