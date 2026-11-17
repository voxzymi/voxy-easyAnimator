"""Train the hair subdivision model: strand flow field (angular loss) and root points (heat-map).

    python -m training.hair.train --config configs/hair.yaml [--set train.epochs=5 ...]

Trained on hair-specific data only (synthetic strands + labeled hair sections); it never sees the full-character
labels, and it is trained at a different time from the general model (one job at a time).
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
from training.hair.model import HairNet
from training.shared import cfg as cfgmod
from training.shared.datasets import HairDataset
from training.shared.joblock import JobLock
from training.shared.losses import angular_loss, root_loss
from training.shared.trainer import fit


def make_loss(w: Dict[str, float]):
    def loss_fn(model, b):
        flow, roots = model(b["x"])
        l_f = angular_loss(flow, b["flow"], b["mask"])
        l_r = root_loss(roots, b["heat"], w.get("root_pos_weight", 20.0))
        return w["flow"] * l_f + w["roots"] * l_r, {"flow": float(l_f), "roots": float(l_r)}
    return loss_fn


def val_fn(model, loader, dev) -> Dict[str, float]:
    ang, n, hit, tot = 0.0, 0.0, 0.0, 0.0
    for b in loader:
        flow, roots = model(b["x"].to(dev))
        f = torch.nn.functional.normalize(flow.cpu(), dim=1)
        cos = (f * b["flow"]).sum(1, keepdim=True).clamp(-1, 1)
        m = b["mask"]
        ang += float((torch.acos(cos) * m).sum()); n += float(m.sum())
        # roots: is the peak of the predicted heat-map near a true root?
        pr = torch.sigmoid(roots.cpu())
        for i in range(pr.shape[0]):
            ph = pr[i, 0]
            idx = int(ph.argmax()); y, x = divmod(idx, ph.shape[1])
            gh = b["heat"][i, 0]
            hit += float(gh[max(0, y - 3):y + 4, max(0, x - 3):x + 4].max() > 0.5); tot += 1
    deg = 180.0 / np.pi * ang / max(1.0, n)
    return {"flow_err_deg": deg, "root_peak_hit": hit / max(1.0, tot), "score": -deg}


def run(cfg: Dict[str, Any], device: str = None, lock: str = "models/.training.lock") -> Dict[str, Any]:
    m, t, d = cfg["model"], cfg["train"], cfg["data"]
    size = int(m["input_size"])
    train_ds = HairDataset(d["synthetic"], d.get("labeled"), d.get("images", "data/raw"), size, True, seed=t.get("seed", 0))
    val_ds = HairDataset(d["val"], None, size=size, train=False)
    if len(train_ds) == 0:
        raise SystemExit("no hair samples found; run training/shared/generate_dataset.py first")
    model = HairNet(base=m["base"], depth=m["depth"])
    out = os.path.join(cfg["output"]["checkpoints"], "hair", cfg["export"]["version"])
    with JobLock(lock, f"hair {cfg['export']['version']}"):
        res = fit(model, make_loss(cfg["loss"]), train_ds, val_ds, val_fn, out, epochs=t["epochs"], batch_size=t["batch_size"],
                  accum_steps=t["accum_steps"], lr=t["lr"], weight_decay=t["weight_decay"], amp=t["amp"],
                  workers=t["workers"], grad_clip=t.get("grad_clip", 1.0), max_steps=t.get("max_steps"), device=device,
                  seed=t.get("seed", 0), meta={"config": cfg, "kind": "hair"})
    summary = {"checkpoint": os.path.join(out, "best.pt"), **{k: getattr(res, k) for k in
               ("steps", "epochs", "first_loss", "last_loss", "best_score", "peak_cuda_mb", "seconds", "device", "amp")},
               "history": res.history}
    json.dump(summary, open(os.path.join(out, "summary.json"), "w"), indent=1)
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="configs/hair.yaml")
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--device", default=None)
    a = ap.parse_args(argv)
    print(json.dumps(run(cfgmod.load(a.config, a.set), a.device), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
