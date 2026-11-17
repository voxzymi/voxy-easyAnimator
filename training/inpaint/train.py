"""Fine-tune the large-mask inpainting model on the generated occlusion pairs. Loss: L1 + perceptual.

    python -m training.inpaint.train --config configs/inpaint.yaml [--init models/inpaint/<v>/last.pt] [--freeze-encoder]

The perceptual loss needs VGG16 features. Put torchvision's ``vgg16-397923af.pth`` at the path in the config
(see docs/models.md for its license note), or pass ``--no-perceptual`` to train with L1 only.
Freeze the encoder (``--freeze-encoder``) to save memory when fine-tuning on a 12 GB card.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, Optional

import torch
import torch.nn as nn
import torch.nn.functional as F

import training  # noqa: F401
from training.inpaint.model import InpaintNet
from training.shared import cfg as cfgmod
from training.shared.datasets import InpaintDataset
from training.shared.joblock import JobLock
from training.shared.trainer import fit


class VGGPerceptual(nn.Module):
    """L1 distance between VGG16 features (relu1_2, relu2_2, relu3_3)."""
    TAPS = (3, 8, 15)

    def __init__(self, weights: Optional[str], allow_random: bool = False):
        super().__init__()
        import torchvision
        vgg = torchvision.models.vgg16(weights=None).features[:16]
        if weights and os.path.isfile(weights):
            sd = torch.load(weights, map_location="cpu")
            vgg.load_state_dict({k[len("features."):]: v for k, v in sd.items() if k.startswith("features.") and int(k.split(".")[1]) < 16})
        elif not allow_random:
            raise FileNotFoundError(f"VGG16 weights not found at {weights!r}. See docs/models.md, or use --no-perceptual.")
        self.vgg = vgg.eval()
        for p in self.vgg.parameters():
            p.requires_grad = False
        self.register_buffer("mean", torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1))
        self.register_buffer("std", torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1))

    def forward(self, a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
        x, y = (a - self.mean) / self.std, (b - self.mean) / self.std
        loss = a.new_zeros(())
        for i, layer in enumerate(self.vgg):
            x, y = layer(x), layer(y)
            if i in self.TAPS:
                loss = loss + F.l1_loss(x, y)
        return loss


def make_loss(w: Dict[str, float], perceptual: Optional[VGGPerceptual]):
    def loss_fn(model, b):
        out = model(b["image"], b["mask"])
        comp = out * b["mask"] + b["target"] * (1 - b["mask"])           # only the hole is the model's work
        hole = (out - b["target"]).abs() * b["mask"]
        l1 = hole.sum() / (b["mask"].sum() * 3 + 1e-6) * w["l1_hole"] + (out - b["target"]).abs().mean() * w["l1_all"]
        perc = perceptual(comp, b["target"]) if perceptual is not None else out.new_zeros(())
        return l1 + w["perceptual"] * perc, {"l1": float(l1), "perceptual": float(perc)}
    return loss_fn


def val_fn(model, loader, dev) -> Dict[str, float]:
    tot, n = 0.0, 0.0
    for b in loader:
        out = model(b["image"].to(dev), b["mask"].to(dev)).cpu()
        m = b["mask"]
        tot += float(((out - b["target"]).abs() * m).sum()); n += float(m.sum() * 3)
    l1 = tot / max(1.0, n)
    return {"hole_l1": l1, "score": -l1}


def run(cfg: Dict[str, Any], device: str = None, init: Optional[str] = None, freeze_encoder: bool = False,
        perceptual: bool = True, allow_random_vgg: bool = False, lock: str = "models/.training.lock") -> Dict[str, Any]:
    m, t, d = cfg["model"], cfg["train"], cfg["data"]
    size = int(m["input_size"])
    train_ds, val_ds = InpaintDataset(d["synthetic"], size, True), InpaintDataset(d["val"], size, False)
    if len(train_ds) == 0:
        raise SystemExit("no samples found; run training/shared/generate_dataset.py first")
    model = InpaintNet(base=m["base"])
    if init:
        model.load_state_dict(torch.load(init, map_location="cpu", weights_only=False)["model"])
    if freeze_encoder or cfg["train"].get("freeze_encoder"):
        model.freeze_encoder()
    perc = VGGPerceptual(cfg["loss"].get("vgg_weights"), allow_random_vgg) if perceptual else None
    out = os.path.join(cfg["output"]["checkpoints"], "inpaint", cfg["export"]["version"])
    with JobLock(lock, f"inpaint {cfg['export']['version']}"):
        res = fit(model, make_loss(cfg["loss"], perc.to(device or "cpu") if perc else None), train_ds, val_ds, val_fn, out,
                  epochs=t["epochs"], batch_size=t["batch_size"], accum_steps=t["accum_steps"], lr=t["lr"],
                  weight_decay=t["weight_decay"], amp=t["amp"], workers=t["workers"], max_steps=t.get("max_steps"),
                  device=device, meta={"config": cfg, "kind": "inpaint"})
    summary = {"checkpoint": os.path.join(out, "best.pt"), "perceptual_loss": perc is not None,
               **{k: getattr(res, k) for k in ("steps", "epochs", "first_loss", "last_loss", "best_score", "peak_cuda_mb", "seconds", "device", "amp")}}
    json.dump(summary, open(os.path.join(out, "summary.json"), "w"), indent=1)
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="configs/inpaint.yaml")
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--device", default=None)
    ap.add_argument("--init", default=None, help="checkpoint to fine-tune from")
    ap.add_argument("--freeze-encoder", action="store_true")
    ap.add_argument("--no-perceptual", action="store_true")
    a = ap.parse_args(argv)
    print(json.dumps(run(cfgmod.load(a.config, a.set), a.device, a.init, a.freeze_encoder, not a.no_perceptual), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
