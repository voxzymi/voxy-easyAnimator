"""Synthetic dataset generator with exact ground truth for both models.

    python -m training.shared.generate_dataset --out data/synthetic --sources 400 --variants 4 --seed 1

What it writes per sample (under <out>/<split>/<sample id>/):

    image.png        the picture (RGB)
    labels.png       visible part class per pixel (hair is ONE class, front and back together)   [general model]
    masks/<id>.png   one mask per part, written because the script placed each part               [general model]
    amodal.npz       full extent of head/eyes/mouth and the hidden regions                         [general model]
    completion.png   the picture with covered parts un-covered (target for completion/inpainting) [general, inpaint]
    hair.npz         hair mask, strand flow field, root points, strand sections                    [hair model only]
    sample.json      parts, masks, key points, z-order, groups, and which model each label is for

Splits are made by *source* (one source = one character identity or reference sheet), so variants of the
same source never land in two splits. Reference sheets from data/raw are used only when provenance.csv shows a
known license or the owner's permission (or --allow-internal, which marks the dataset not shareable).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

import training  # noqa: F401
from voxy_service import schema as S

from . import augment, provenance, sheets
from .procedural_art import Render, random_base_layout, random_style, render_character

GENERATOR_VERSION = "1.0"
SPLITS = ("train", "val", "test")


def assign_splits(source_ids: List[str], fractions: Tuple[float, float, float], seed: int) -> Dict[str, str]:
    """Deterministic split by source id: every sample of a source goes to the same split."""
    keyed = sorted(source_ids, key=lambda s: hashlib.sha1(f"{seed}:{s}".encode()).hexdigest())
    n = len(keyed)
    n_val = int(round(n * fractions[1]))
    n_test = int(round(n * fractions[2]))
    if n >= 3:
        n_val, n_test = max(1, n_val), max(1, n_test)
    n_train = n - n_val - n_test
    out: Dict[str, str] = {}
    for i, sid in enumerate(keyed):
        out[sid] = "train" if i < n_train else ("val" if i < n_train + n_val else "test")
    return out


def _write_png(path: str, arr: np.ndarray) -> None:
    if arr.ndim == 3:
        arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cv2.imwrite(path, arr)


def _bbox(m: np.ndarray) -> List[int]:
    ys, xs = np.where(m)
    return [int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)]


def build_sample(source_id: str, variant: int, seed: int, hw: Tuple[int, int], style, base_layout,
                 sprite_pool: Optional[list], shareable: bool, source_note: Dict[str, Any]) -> Dict[str, Any]:
    """Render one sample and return everything as arrays plus the JSON description (nothing is written here)."""
    rng = np.random.default_rng([seed, int(hashlib.sha1(source_id.encode()).hexdigest()[:8], 16), variant])
    h, w = hw
    r: Render = render_character(rng, style, base_layout, hw, sprites=sprite_pool)
    flipped = bool(rng.random() < 0.5)
    labels, amodal, hidden = r.labels, dict(r.amodal), dict(r.hidden)
    rgb, alpha, completion = r.rgb, r.alpha, r.completion_rgb
    kps = r.keypoints
    hair = r.hair
    hair_mask = (labels == S.CLASS_INDEX["hair"])
    flow = hair.flow if hair else None
    heat = hair.root_heat if hair else None
    subs = hair.subdivisions if hair else None
    roots = hair.roots if hair else None
    z_order = dict(r.z_order)
    if flipped:
        labels = augment.flip_labels(labels)
        rgb, alpha, completion = rgb[:, ::-1], alpha[:, ::-1], completion[:, ::-1]
        amodal = {S.FLIP_CLASS[k]: v[:, ::-1] for k, v in amodal.items()}
        hidden = {S.FLIP_CLASS[k]: v[:, ::-1] for k, v in hidden.items()}
        kps = augment.flip_keypoints(kps, w)
        hair_mask = hair_mask[:, ::-1]
        if hair:
            flow, heat, subs = augment.flip_flow(flow), heat[:, ::-1], subs[:, ::-1]
            roots = np.stack([w - 1 - roots[:, 0], roots[:, 1]], axis=1)
        z_order = {S.FLIP_CLASS[k]: v for k, v in z_order.items()}
    rgb = augment.color_shift(rng, np.ascontiguousarray(rgb))
    bg, bg_kind = augment.make_background(rng, hw)
    a3 = np.ascontiguousarray(alpha)[..., None]
    img = (bg.astype(np.float32) * (1 - a3) + rgb.astype(np.float32) * a3).astype(np.uint8)
    completion_img = (bg.astype(np.float32) * (1 - a3) + augment.color_shift(rng, np.ascontiguousarray(completion)).astype(np.float32) * a3).astype(np.uint8)
    img, aug_params = augment.scan_artifacts(rng, img)

    parts: List[Dict[str, Any]] = []
    masks: Dict[str, np.ndarray] = {}
    for cls, disp, grp in S.PART_CLASSES:
        m = labels == S.CLASS_INDEX[cls]
        if not m.any():
            continue
        masks[cls] = m
        parts.append({"id": cls, "class": cls, "group": grp, "kind": "visible", "label_value": S.CLASS_INDEX[cls],
                      "mask": f"masks/{cls}.png", "bbox": _bbox(m), "pixel_count": int(m.sum()),
                      "z_order": int(z_order[cls]), "for_models": ["general"]})
    for hid, disp, base in S.HIDDEN_PARTS:
        m = hidden[base]
        if int(m.sum()) < 12:
            continue
        masks[hid] = m
        parts.append({"id": hid, "class": hid, "group": "Hidden", "kind": "hidden", "label_value": None,
                      "mask": f"masks/{hid}.png", "bbox": _bbox(m), "pixel_count": int(m.sum()),
                      "z_order": int(S.Z_ORDER[hid]), "for_models": ["general", "inpaint"]})
    info: Dict[str, Any] = {
        "format": "voxy-sample-1", "generator_version": GENERATOR_VERSION, "id": f"{source_id}-v{variant}",
        "source_id": source_id, "size": {"width": w, "height": h}, "seed": seed, "flipped": flipped,
        "background": bg_kind, "augmentations": {**aug_params, "outline_px": r.outline_px},
        "license": "CC0-1.0" if source_note["kind"] == "procedural" else "see sources",
        "shareable": shareable, "sources": [source_note] + [{"kind": "sheet", "id": s} for s in sorted(set(r.hair_sources))],
        "parts": parts,
        "keypoints": [{"name": k, "x": round(float(v[0]), 2), "y": round(float(v[1]), 2), "visible": bool(v[2])}
                      for k, v in kps.items()],
        "files": {"image": "image.png", "labels": "labels.png", "completion": "completion.png",
                  "amodal": "amodal.npz", "hair": "hair.npz" if hair is not None else None},
        "labels_for": {
            "general": ["labels (hair is one class)", "parts/masks", "keypoints", "amodal + hidden", "completion"],
            "hair": ["hair.npz: mask, flow, roots, subdivisions"] if hair is not None else [],
            "inpaint": ["completion"],
        },
        "hair": None if hair is None else {
            "pixels": int(hair_mask.sum()), "roots": int(len(roots)), "sections": int(hair.clumps),
            "pieces_from_sheets": r.hair_sources},
    }
    arrays = {"image": img, "labels": labels.astype(np.uint8), "completion": completion_img, "masks": masks,
              "amodal": {k: np.ascontiguousarray(v) for k, v in amodal.items()},
              "hidden": {k: np.ascontiguousarray(v) for k, v in hidden.items()},
              "hair": None if hair is None else {"mask": np.ascontiguousarray(hair_mask), "flow": np.ascontiguousarray(flow).astype(np.float16),
                                                 "root_heat": np.ascontiguousarray(heat).astype(np.float16),
                                                 "subdivisions": np.ascontiguousarray(subs).astype(np.uint8),
                                                 "roots": roots.astype(np.float32)}}
    return {"info": info, "arrays": arrays}


def write_sample(root: str, split: str, built: Dict[str, Any], part_masks: bool = True) -> str:
    info, a = built["info"], built["arrays"]
    d = os.path.join(root, split, info["id"])
    os.makedirs(d, exist_ok=True)
    info = dict(info, split=split)
    _write_png(os.path.join(d, "image.png"), a["image"])
    _write_png(os.path.join(d, "labels.png"), a["labels"])
    _write_png(os.path.join(d, "completion.png"), a["completion"])
    np.savez_compressed(os.path.join(d, "amodal.npz"), **{f"amodal_{k}": v for k, v in a["amodal"].items()},
                        **{f"hidden_{k}": v for k, v in a["hidden"].items()})
    if a["hair"] is not None:
        np.savez_compressed(os.path.join(d, "hair.npz"), **a["hair"])
    if part_masks:
        for pid, m in a["masks"].items():
            _write_png(os.path.join(d, "masks", f"{pid}.png"), (m * 255).astype(np.uint8))
    else:
        for p in info["parts"]:
            p["mask"] = None
    with open(os.path.join(d, "sample.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, indent=1)
    return d


def _job(args) -> Tuple[str, str, str]:
    (out, split, source_id, variants, seed, hw, style, base, sprite_pool, shareable, note, part_masks) = args
    for v in range(variants):
        built = build_sample(source_id, v, seed, hw, style, base, sprite_pool, shareable, note)
        write_sample(out, split, built, part_masks)
    return source_id, split, "ok"


def generate(out: str, *, sources: int, variants: int, seed: int, size: Tuple[int, int] = (512, 384),
             fractions: Tuple[float, float, float] = (0.8, 0.1, 0.1), raw_dir: Optional[str] = None,
             allow_internal: bool = False, part_masks: bool = True, workers: int = 1, use_sheets: bool = True) -> Dict[str, Any]:
    t0 = time.time()
    pool: List[sheets.Sprite] = []
    sheet_files: List[str] = []
    if use_sheets and raw_dir and os.path.isdir(raw_dir):
        pool = sheets.load_all(raw_dir, allow_internal)         # raises PermissionError for unlicensed sheets
        sheet_files = sorted({s.source for s in pool})
    shareable = all(s.shareable for s in pool)
    ids = [f"proc{seed:04d}-{i:06d}" for i in range(sources)]
    split_of = assign_splits(ids, fractions, seed)
    jobs = []
    for i, sid in enumerate(ids):
        rng = np.random.default_rng([seed, i, 7919])
        style, base = random_style(rng), random_base_layout(rng)
        # every 3rd source builds its hair from reference pieces when there are any
        use_pool = pool if (pool and i % 3 == 0) else None
        jobs.append((out, split_of[sid], sid, variants, seed, size, style, base, use_pool, shareable,
                     {"kind": "procedural", "id": sid}, part_masks))
    if workers > 1:
        with mp.get_context("spawn").Pool(workers) as p:
            list(p.imap_unordered(_job, jobs))
    else:
        for j in jobs:
            _job(j)
    info = {"format": "voxy-dataset-1", "generator_version": GENERATOR_VERSION, "seed": seed, "size": list(size),
            "sources": {sp: sorted(s for s, v in split_of.items() if v == sp) for sp in SPLITS},
            "samples": {sp: sum(1 for s, v in split_of.items() if v == sp) * variants for sp in SPLITS},
            "variants_per_source": variants, "reference_sheets": sheet_files, "shareable": shareable,
            "license": "CC0-1.0 (procedural)" if not pool else "mixed, see per-sample sources",
            "seconds": round(time.time() - t0, 1)}
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "dataset_info.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, indent=1)
    return info


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data/synthetic")
    ap.add_argument("--sources", type=int, default=100, help="number of character identities / sheets")
    ap.add_argument("--variants", type=int, default=4, help="samples per source (pose, flip, background, scan artifacts)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--height", type=int, default=512)
    ap.add_argument("--width", type=int, default=384)
    ap.add_argument("--split", default="0.8,0.1,0.1", help="train,val,test fractions (by source)")
    ap.add_argument("--raw", default="data/raw", help="reference sheets (need provenance rows)")
    ap.add_argument("--no-sheets", action="store_true", help="procedural characters only")
    ap.add_argument("--allow-internal", action="store_true",
                    help="also use sheets that are internal-only; the dataset is then marked NOT shareable")
    ap.add_argument("--no-part-masks", action="store_true", help="skip the per-part mask PNGs (labels.png has them all)")
    ap.add_argument("--workers", type=int, default=1)
    a = ap.parse_args(argv)
    fr = tuple(float(x) for x in a.split.split(","))
    try:
        info = generate(a.out, sources=a.sources, variants=a.variants, seed=a.seed, size=(a.height, a.width), fractions=fr,
                        raw_dir=a.raw, allow_internal=a.allow_internal, part_masks=not a.no_part_masks, workers=a.workers,
                        use_sheets=not a.no_sheets)
    except PermissionError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print(json.dumps({k: info[k] for k in ("samples", "shareable", "reference_sheets", "seconds")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
