"""Evaluation, reported separately for each model and for each kind of data.

    python -m training.shared.evaluate --general models/general/0.1.0 --hair models/hair/0.1.0 \\
        --synthetic data/synthetic/test --real data/labeled/general --hair-real data/labeled/hair \\
        --images data/raw --out out/eval

General model:  per-part IoU, Hair IoU (the single Hair class), group-assignment accuracy, key-point PCK@0.05 and
                PCK@0.10 (a joint is correct when it is within that fraction of the character's height), hidden-extent
                IoU and completion error (synthetic only, where the truth is known).
Hair model:     flow angular error, root-point F1 (4 px tolerance), subdivision quality (matched IoU against the true
                strand sections, with a trivial vertical-flow baseline for comparison).

"Held-out real art" means the folders passed with --real / --hair-real: corrected real images that were NOT added to the
training sets. Synthetic numbers show the pipeline works; only the real-art numbers say how good the models are.
Pass ``--general stub`` / ``--hair stub`` to evaluate the stub models (a pipeline check, not a quality number).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, Iterator, List, Optional, Tuple

import cv2
import numpy as np

import training  # noqa: F401
from training.shared.datasets import flow_from_roots, list_samples
from voxy_service import schema as S
from voxy_service.cancel import CancelToken
from voxy_service.hair.subdivide import extract_roots, split_hair
from voxy_service.models.stub import StubGeneralModel, StubHairModel

TOK = CancelToken()
GROUP_OF_INDEX = np.array(["bg"] + [S.GROUP_OF_CLASS[c] for c in S.CLASS_NAMES[1:]])


# ------------------------------------------------------------------ loading
def _rgb(p: str) -> np.ndarray:
    return cv2.cvtColor(cv2.imread(p, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)


def iter_synthetic(root: str) -> Iterator[Dict[str, Any]]:
    for d in list_samples(root):
        info = json.load(open(os.path.join(d, "sample.json")))
        z = np.load(os.path.join(d, "amodal.npz"))
        item = {"id": info["id"], "image": _rgb(os.path.join(d, "image.png")),
                "labels": cv2.imread(os.path.join(d, "labels.png"), cv2.IMREAD_UNCHANGED),
                "kps": {k["name"]: (k["x"], k["y"]) for k in info["keypoints"] if k["visible"]},
                "amodal": {c: z[f"amodal_{c}"] for c in S.AMODAL_CLASSES},
                "hidden": {c: z[f"hidden_{c}"] for c in S.AMODAL_CLASSES},
                "completion": _rgb(os.path.join(d, "completion.png")), "hair": None}
        if os.path.isfile(os.path.join(d, "hair.npz")):
            h = np.load(os.path.join(d, "hair.npz"))
            item["hair"] = {"mask": h["mask"], "flow": h["flow"].astype(np.float32), "roots": h["roots"], "subdivisions": h["subdivisions"]}
        yield item


def iter_real_general(labeled: str, images: str) -> Iterator[Dict[str, Any]]:
    for n in sorted(os.listdir(labeled)) if os.path.isdir(labeled) else []:
        d = os.path.join(labeled, n)
        if not os.path.isfile(os.path.join(d, "labels.png")):
            continue
        meta = json.load(open(os.path.join(d, "meta.json")))
        kps = {k["name"]: (k["x"], k["y"]) for k in json.load(open(os.path.join(d, "keypoints.json"))) if k.get("visible", True)}
        yield {"id": n, "image": _rgb(os.path.join(images, meta["image"])), "labels": cv2.imread(os.path.join(d, "labels.png"), cv2.IMREAD_UNCHANGED),
               "kps": kps, "amodal": None, "hidden": None, "completion": None, "hair": None}


def iter_real_hair(labeled: str, images: str) -> Iterator[Dict[str, Any]]:
    for n in sorted(os.listdir(labeled)) if os.path.isdir(labeled) else []:
        d = os.path.join(labeled, n)
        if not os.path.isfile(os.path.join(d, "subdivisions.png")):
            continue
        meta = json.load(open(os.path.join(d, "meta.json")))
        mask = cv2.imread(os.path.join(d, "hair_mask.png"), cv2.IMREAD_GRAYSCALE) > 0
        roots = np.array(json.load(open(os.path.join(d, "roots.json"))), np.float32).reshape(-1, 2)
        yield {"id": n, "image": _rgb(os.path.join(images, meta["image"])),
               "hair": {"mask": mask, "flow": flow_from_roots(mask, roots), "roots": roots,
                        "subdivisions": cv2.imread(os.path.join(d, "subdivisions.png"), cv2.IMREAD_GRAYSCALE)}}


# ------------------------------------------------------------------ metrics
def iou_table(pred_all: List[np.ndarray], gt_all: List[np.ndarray]) -> Dict[str, Any]:
    inter, union = np.zeros(S.NUM_CLASSES), np.zeros(S.NUM_CLASSES)
    present = np.zeros(S.NUM_CLASSES)
    for p, g in zip(pred_all, gt_all):
        for c in range(1, S.NUM_CLASSES):
            pc, gc = p == c, g == c
            u = (pc | gc).sum()
            if u:
                inter[c] += (pc & gc).sum()
                union[c] += u
            present[c] += bool(gc.any())
    per = {S.CLASS_NAMES[c]: (float(inter[c] / union[c]) if union[c] else None) for c in range(1, S.NUM_CLASSES)}
    vals = [v for v in per.values() if v is not None]
    return {"per_part_iou": per, "mean_iou": float(np.mean(vals)) if vals else None, "hair_iou": per["hair"],
            "images_with_part": {S.CLASS_NAMES[c]: int(present[c]) for c in range(1, S.NUM_CLASSES)}}


def group_accuracy(pred_all: List[np.ndarray], gt_all: List[np.ndarray]) -> Dict[str, float]:
    ok = tot = 0
    parts_ok = parts_tot = 0
    for p, g in zip(pred_all, gt_all):
        fg = g > 0
        ok += int((GROUP_OF_INDEX[p[fg]] == GROUP_OF_INDEX[g[fg]]).sum())
        tot += int(fg.sum())
        for c in range(1, S.NUM_CLASSES):
            m = g == c
            if m.sum() >= 8:
                vals, counts = np.unique(GROUP_OF_INDEX[p[m]], return_counts=True)
                parts_ok += int(vals[counts.argmax()] == GROUP_OF_INDEX[c])
                parts_tot += 1
    return {"pixel_group_accuracy": ok / max(1, tot), "part_group_accuracy": parts_ok / max(1, parts_tot)}


def pck(preds: List[Dict[str, Tuple[float, float, float]]], gts: List[Dict[str, Tuple[float, float]]],
        heights: List[float], thr: float) -> Dict[str, Any]:
    hit = tot = 0
    per: Dict[str, List[int]] = {k: [0, 0] for k in S.KEYPOINTS}
    for p, g, h in zip(preds, gts, heights):
        for k, (x, y) in g.items():
            if k not in p:
                continue
            d = float(np.hypot(p[k][0] - x, p[k][1] - y))
            ok = d <= thr * h
            hit += ok
            tot += 1
            per[k][0] += ok
            per[k][1] += 1
    return {"threshold": f"{thr:.2f} x character height", "pck": hit / max(1, tot), "joints": tot,
            "per_joint": {k: (v[0] / v[1] if v[1] else None) for k, v in per.items()}}


def matched_iou(pred: np.ndarray, gt: np.ndarray) -> float:
    """Mean IoU of the best one-to-one matching between predicted and true sections."""
    from scipy.optimize import linear_sum_assignment
    pl, gl = [int(v) for v in np.unique(pred) if v], [int(v) for v in np.unique(gt) if v]
    if not pl or not gl:
        return 0.0
    M = np.zeros((len(gl), len(pl)))
    for i, g in enumerate(gl):
        gm = gt == g
        for j, p in enumerate(pl):
            pm = pred == p
            u = (gm | pm).sum()
            M[i, j] = (gm & pm).sum() / u if u else 0
    r, c = linear_sum_assignment(-M)
    return float(M[r, c].sum() / len(gl))


def root_f1(pred: np.ndarray, gt: np.ndarray, tol: float = 4.0) -> Tuple[float, float, float]:
    if len(pred) == 0 or len(gt) == 0:
        return (0.0, 0.0, 0.0)
    d = np.linalg.norm(pred[:, None] - gt[None], axis=2)
    prec = float((d.min(axis=1) <= tol).mean())
    rec = float((d.min(axis=0) <= tol).mean())
    return prec, rec, (2 * prec * rec / (prec + rec) if prec + rec else 0.0)


# ------------------------------------------------------------------ evaluation
def _pal() -> np.ndarray:
    p = np.zeros((S.NUM_CLASSES, 3), np.uint8)
    p[0] = 255
    for i in range(1, S.NUM_CLASSES):
        p[i] = cv2.cvtColor(np.uint8([[[(i * 47) % 180, 170, 230]]]), cv2.COLOR_HSV2RGB)[0, 0]
    return p


def eval_general(gm, items: List[Dict[str, Any]], sheet: Optional[str] = None, sheet_n: int = 8) -> Dict[str, Any]:
    preds, gts, kp_p, kp_g, heights = [], [], [], [], []
    am_i = am_u = 0
    comp_err, comp_n = 0.0, 0
    tiles = []
    pal = _pal()
    for it in items:
        rgba = np.dstack([it["image"], np.full(it["image"].shape[:2], 255, np.uint8)])
        out = gm.predict(rgba, TOK)
        preds.append(out.labels)
        gts.append(it["labels"])
        kp_p.append(out.keypoints)
        kp_g.append(it["kps"])
        ys = np.where((it["labels"] > 0).any(axis=1))[0]
        heights.append(float(ys.max() - ys.min() + 1) if len(ys) else 1.0)
        if it["hidden"] is not None:
            for c in S.AMODAL_CLASSES:
                gh = it["hidden"][c]
                vis = it["labels"] == S.CLASS_INDEX[c]
                ph = out.amodal.get(c, np.zeros_like(gh)) & ~(out.labels == S.CLASS_INDEX[c]) & (out.labels != 0)
                am_i += int((gh & ph).sum()); am_u += int((gh | ph).sum())
            if out.completion_rgb is not None:
                hid = np.zeros(it["labels"].shape, bool)
                for c in S.AMODAL_CLASSES:
                    hid |= it["hidden"][c]
                if hid.any():
                    comp_err += float(np.abs(out.completion_rgb[hid].astype(float) - it["completion"][hid]).mean()) / 255.0
                    comp_n += 1
        if sheet and len(tiles) < sheet_n:
            tiles.append(np.concatenate([it["image"], pal[it["labels"]], pal[out.labels]], axis=1))
    rep: Dict[str, Any] = {"images": len(items), **iou_table(preds, gts), **group_accuracy(preds, gts),
                           "pck_0.05": pck(kp_p, kp_g, heights, 0.05), "pck_0.10": pck(kp_p, kp_g, heights, 0.10)}
    if am_u or comp_n:
        rep["hidden_extent_iou"] = am_i / am_u if am_u else None
        rep["completion_mean_abs_error"] = comp_err / comp_n if comp_n else None
    if sheet and tiles:
        _contact_sheet(sheet, tiles, ["input", "truth", "prediction"])
    return rep


def eval_hair(hm, items: List[Dict[str, Any]], sheet: Optional[str] = None, sheet_n: int = 8) -> Dict[str, Any]:
    ang, f1s, sub, base, n_ang = [], [], [], [], 0
    tiles = []
    for it in items:
        h = it["hair"]
        if h is None or h["mask"].sum() < 50:
            continue
        out = hm.predict(it["image"], h["mask"], TOK)
        m = h["mask"]
        dot = np.clip((out.flow[m] * h["flow"][m]).sum(axis=1), -1, 1)
        ang.append(float(np.degrees(np.arccos(dot)).mean()))
        roots = extract_roots(out.roots, m)
        f1s.append(root_f1(roots, h["roots"])[2])
        k = int(h["subdivisions"].max())
        if k >= 2:
            lab, _ = split_hair(m, out.flow, roots, k, 0.0)
            sub.append(matched_iou(lab, h["subdivisions"]))
            down = np.zeros_like(out.flow); down[..., 1] = 1
            blab, _ = split_hair(m, down, np.zeros((0, 2), np.float32), k, 0.0)
            base.append(matched_iou(blab, h["subdivisions"]))
            if sheet and len(tiles) < sheet_n:
                pal = _pal()
                ys, xs = np.where(m)
                sl = (slice(ys.min(), ys.max() + 1), slice(xs.min(), xs.max() + 1))
                tiles.append(np.concatenate([it["image"][sl], pal[h["subdivisions"] % S.NUM_CLASSES][sl], pal[lab % S.NUM_CLASSES][sl]], axis=1))
    rep = {"images": len(ang), "flow_angular_error_deg_mean": float(np.mean(ang)) if ang else None,
           "flow_angular_error_deg_median": float(np.median(ang)) if ang else None,
           "root_f1_4px": float(np.mean(f1s)) if f1s else None,
           "subdivision_matched_iou": float(np.mean(sub)) if sub else None,
           "subdivision_baseline_vertical_flow": float(np.mean(base)) if base else None}
    if sheet and tiles:
        w = max(t.shape[1] for t in tiles)
        _contact_sheet(sheet, [np.pad(t, ((0, 0), (0, w - t.shape[1]), (0, 0)), constant_values=255) for t in tiles],
                       ["input hair", "true sections", "predicted sections"])
    return rep


def _contact_sheet(path: str, tiles: List[np.ndarray], titles: List[str]) -> None:
    h = max(t.shape[0] for t in tiles)
    rows = [np.pad(t, ((0, h - t.shape[0]), (0, 0), (0, 0)), constant_values=255) for t in tiles]
    w = max(r.shape[1] for r in rows)
    rows = [np.pad(r, ((0, 0), (0, w - r.shape[1]), (0, 0)), constant_values=255) for r in rows]
    cols = 2 if len(rows) > 3 else 1
    cell_rows = [np.concatenate(rows[i:i + cols] + [np.full_like(rows[0], 255)] * (cols - len(rows[i:i + cols])), axis=1)
                 for i in range(0, len(rows), cols)]
    sheet = np.concatenate(cell_rows, axis=0)
    head = np.full((28, sheet.shape[1], 3), 255, np.uint8)
    cw = rows[0].shape[1] // len(titles)
    for c in range(cols):
        for i, t in enumerate(titles):
            cv2.putText(head, t, (c * rows[0].shape[1] + i * cw + 6, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (30, 30, 30), 1, cv2.LINE_AA)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    cv2.imwrite(path, cv2.cvtColor(np.concatenate([head, sheet], axis=0), cv2.COLOR_RGB2BGR))


def load_general(spec: str):
    if spec == "stub":
        return StubGeneralModel()
    from voxy_service.models.onnx_models import OnnxGeneralModel
    return OnnxGeneralModel(spec, prefer="cuda")


def load_hair(spec: str):
    if spec == "stub":
        return StubHairModel()
    from voxy_service.models.onnx_models import OnnxHairModel
    return OnnxHairModel(spec, prefer="cuda")


def run(args: argparse.Namespace) -> Dict[str, Any]:
    out = args.out
    os.makedirs(out, exist_ok=True)
    report: Dict[str, Any] = {"general_model": args.general, "hair_model": args.hair, "notes": []}
    if args.general:
        gm = load_general(args.general)
        report["general"] = {}
        if args.synthetic:
            report["general"]["synthetic_test"] = eval_general(gm, list(iter_synthetic(args.synthetic))[:args.max_images],
                                                               os.path.join(out, "contact_general_synthetic.png"))
        if args.real:
            items = list(iter_real_general(args.real, args.images))[:args.max_images]
            report["general"]["held_out_real"] = eval_general(gm, items, os.path.join(out, "contact_general_real.png")) if items else \
                {"images": 0, "note": "no held-out real images were found"}
    if args.hair:
        hm = load_hair(args.hair)
        report["hair"] = {}
        if args.synthetic:
            report["hair"]["synthetic_test"] = eval_hair(hm, list(iter_synthetic(args.synthetic))[:args.max_images],
                                                         os.path.join(out, "contact_hair_synthetic.png"))
        if args.hair_real:
            items = list(iter_real_hair(args.hair_real, args.images))[:args.max_images]
            report["hair"]["held_out_real"] = eval_hair(hm, items, os.path.join(out, "contact_hair_real.png")) if items else \
                {"images": 0, "note": "no held-out real hair images were found"}
    if "stub" in (args.general, args.hair):
        report["notes"].append("A stub model was evaluated: this checks the pipeline, it is not a quality number.")
    json.dump(report, open(os.path.join(out, "report.json"), "w"), indent=1)
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--general", help="models/general/<version> folder, or 'stub'")
    ap.add_argument("--hair", help="models/hair/<version> folder, or 'stub'")
    ap.add_argument("--synthetic", help="synthetic split folder, e.g. data/synthetic/test")
    ap.add_argument("--real", help="held-out corrected real images for the general model (data/labeled/general style)")
    ap.add_argument("--hair-real", help="held-out corrected real hair labels (data/labeled/hair style)")
    ap.add_argument("--images", default="data/raw")
    ap.add_argument("--out", default="out/eval")
    ap.add_argument("--max-images", type=int, default=10 ** 9)
    a = ap.parse_args(argv)
    if not (a.general or a.hair):
        ap.error("give --general and/or --hair")
    print(json.dumps(run(a), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
