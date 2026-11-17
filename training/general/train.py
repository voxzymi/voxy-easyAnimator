"""Train the general dissection model (part segmentation + key points + hidden-part extents + completion).

    python -m training.general.train --config configs/general.yaml [--set train.epochs=5 ...]

Mixed precision (FP16) and gradient accumulation keep it inside 12 GB. Only one training job runs at a time.
The hair subdivision model is trained separately (training/hair/train.py), never in parallel.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict

import numpy as np
import torch

import training  # noqa: F401
from training.general.model import GeneralNet
from training.shared import cfg as cfgmod
from training.shared.datasets import GeneralDataset
from training.shared.joblock import JobLock
from training.shared.losses import amodal_loss, completion_l1, heatmap_mse, seg_loss
from training.shared.trainer import fit
from voxy_service import schema as S


def make_loss(w: Dict[str, float]):
    def loss_fn(model, b):
        seg, kpt, am, comp = model(b["x"])
        l_seg = seg_loss(seg, b["seg"], w["seg_ce"], w["seg_dice"])
        l_k = heatmap_mse(kpt, b["kpt"], b["kpt_valid"])
        l_a = amodal_loss(am, b["amodal"], b["has_amodal"])
        l_c = completion_l1(comp, b["comp"], b["comp_w"], b["has_amodal"])
        total = l_seg + w["kpt"] * l_k + w["amodal"] * l_a + w["completion"] * l_c
        return total, {"seg": float(l_seg), "kpt": float(l_k), "amodal": float(l_a), "completion": float(l_c)}
    return loss_fn


def val_fn(model, loader, dev) -> Dict[str, float]:
    inter = torch.zeros(S.NUM_CLASSES)
    union = torch.zeros(S.NUM_CLASSES)
    kerr, kn = 0.0, 0
    for b in loader:
        x = b["x"].to(dev)
        seg, kpt, _, _ = model(x)
        pred = seg.argmax(1).cpu()
        gt = b["seg"]
        for c in range(1, S.NUM_CLASSES):
            p, g = pred == c, gt == c
            inter[c] += (p & g).sum()
            union[c] += (p | g).sum()
        # key-point error in heat-map cells, for joints that are labelled
        hp = kpt.cpu().flatten(2).argmax(2)
        hg = b["kpt"].flatten(2).argmax(2)
        n = b["kpt"].shape[-1]
        d = torch.sqrt(((hp // n - hg // n).float() ** 2) + ((hp % n - hg % n).float() ** 2))
        v = b["kpt_valid"] > 0
        kerr += float(d[v].sum()); kn += int(v.sum())
    present = union[1:] > 0
    miou = float((inter[1:][present] / union[1:][present]).mean()) if present.any() else 0.0
    return {"miou": miou, "kpt_err_cells": kerr / max(1, kn), "score": miou}


def run(cfg: Dict[str, Any], device: str = None, lock: str = "models/.training.lock") -> Dict[str, Any]:
    m, t, d = cfg["model"], cfg["train"], cfg["data"]
    size = int(m["input_size"])
    train_ds = GeneralDataset(d["synthetic"], d.get("labeled"), d.get("images", "data/raw"), size, True, seed=t.get("seed", 0))
    val_ds = GeneralDataset(d["val"], None, size=size, train=False)
    if len(train_ds) == 0:
        raise SystemExit("no training samples found; run training/shared/generate_dataset.py first")
    model = GeneralNet(base=m["base"], depth=m["depth"])
    out = os.path.join(cfg["output"]["checkpoints"], "general", cfg["export"]["version"])
    with JobLock(lock, f"general {cfg['export']['version']}"):
        res = fit(model, make_loss(cfg["loss"]), train_ds, val_ds, val_fn, out, epochs=t["epochs"], batch_size=t["batch_size"],
                  accum_steps=t["accum_steps"], lr=t["lr"], weight_decay=t["weight_decay"], amp=t["amp"],
                  workers=t["workers"], grad_clip=t.get("grad_clip", 1.0), max_steps=t.get("max_steps"), device=device,
                  seed=t.get("seed", 0), meta={"config": cfg, "kind": "general"})
    summary = {"checkpoint": os.path.join(out, "best.pt"), **{k: getattr(res, k) for k in
               ("steps", "epochs", "first_loss", "last_loss", "best_score", "peak_cuda_mb", "seconds", "device", "amp")},
               "history": res.history}
    json.dump(summary, open(os.path.join(out, "summary.json"), "w"), indent=1)
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="configs/general.yaml")
    ap.add_argument("--set", action="append", default=[], help="override, e.g. train.epochs=3")
    ap.add_argument("--device", default=None)
    a = ap.parse_args(argv)
    print(json.dumps(run(cfgmod.load(a.config, a.set), a.device), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
