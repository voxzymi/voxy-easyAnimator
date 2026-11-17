"""Turn the general model's output into manifest parts (and write their PNG files)."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from . import schema as S
from .imaging import bbox_of, clean_mask, crop_rgba, outline_of, save_png
from .models.base import GeneralOutput, ModelInfo
from .pivots import compute_pivot


def _write_part(out_dir: str, pid: str, rgba: np.ndarray, mask: np.ndarray, bbox) -> Tuple[str, str]:
    img, m = crop_rgba(rgba, mask, bbox)
    rel_img, rel_mask = f"parts/{pid}.png", f"parts/{pid}_mask.png"
    save_png(os.path.join(out_dir, rel_img), img)
    save_png(os.path.join(out_dir, rel_mask), m)
    return rel_img, rel_mask


def make_part(pid: str, name: str, group: str, cls: Optional[str], kind: str, z: int, rgba: np.ndarray,
              mask: np.ndarray, pivot: Tuple[float, float], out_dir: str, produced_by: Dict[str, str],
              *, parent: Optional[str] = None, confidence: float = 1.0) -> Dict[str, Any]:
    bb = bbox_of(mask)
    assert bb is not None
    rel_img, rel_mask = _write_part(out_dir, pid, rgba, mask, bb)
    x, y, w, h = bb
    part = {
        "id": pid, "name": name, "group": group, "class": cls, "kind": kind, "z_order": int(z),
        "bbox": [x, y, w, h], "image": rel_img, "mask": rel_mask,
        "outline": outline_of(mask[y:y + h, x:x + w], (x, y)),
        "pivot": [pivot[0], pivot[1]], "pixel_count": int(mask.sum()),
        "parent": parent, "confidence": round(float(confidence), 4), "produced_by": produced_by,
    }
    return part


def build_parts(rgba: np.ndarray, out: GeneralOutput, info: ModelInfo, out_dir: str, *,
                min_pixels: int = 12) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, np.ndarray]]:
    """Return (parts, skipped, masks-by-class). Parts with no detected pixels are skipped."""
    parts: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    masks: Dict[str, np.ndarray] = {}
    pb = info.produced_by("general")
    labels = out.labels

    # 1) cleaned masks for every class
    for cls, disp, grp in S.PART_CLASSES:
        m = labels == S.CLASS_INDEX[cls]
        if m.any():
            m = clean_mask(m, min_pixels=min_pixels, keep_all=(cls == "hair"))
        masks[cls] = m

    head_mask = masks.get("head")
    # 2) parts, in manifest order (group order, then class order)
    for cls, disp, grp in S.PART_CLASSES:
        m = masks[cls]
        if int(m.sum()) < min_pixels:
            skipped.append({"id": cls, "name": disp, "group": grp, "reason": "no_pixels"})
            continue
        pivot = compute_pivot(cls, m, out.keypoints, head_mask=head_mask)
        parts.append(make_part(cls, disp, grp, cls, "visible", S.Z_ORDER[cls], rgba, m, pivot, out_dir, pb,
                               confidence=out.class_confidence.get(cls, 1.0)))

    # 3) hidden parts: where an occluder covers the part, completed by the model
    for hid, disp, base in S.HIDDEN_PARTS:
        am = out.amodal.get(base)
        if am is None:
            skipped.append({"id": hid, "name": disp, "group": "Hidden", "reason": "no_pixels"})
            continue
        vis = labels == S.CLASS_INDEX[base]
        occluder = (labels != 0) & ~vis
        hm = am & ~vis & occluder
        if hm.any():
            hm = clean_mask(hm, min_pixels=min_pixels, keep_all=True)
        if int(hm.sum()) < min_pixels:
            skipped.append({"id": hid, "name": disp, "group": "Hidden", "reason": "no_pixels"})
            continue
        comp = rgba.copy()
        if out.completion_rgb is not None:
            comp[:, :, :3] = np.where(hm[:, :, None], out.completion_rgb, rgba[:, :, :3])
        comp[:, :, 3] = np.where(hm, 255, 0).astype(np.uint8)
        base_mask = masks.get(base)
        pivot = compute_pivot(base, base_mask if base_mask is not None and base_mask.any() else hm, out.keypoints,
                              head_mask=head_mask)
        parts.append(make_part(hid, disp, "Hidden", hid, "hidden", S.Z_ORDER[hid], comp, hm, pivot, out_dir, pb,
                               confidence=out.class_confidence.get(base, 1.0)))
        masks[hid] = hm
    return parts, skipped, masks
