"""Measure real peak VRAM on a CUDA card (run on the RTX 3060): training steps for each model and inference for the
general model, the hair model, and both in one job. Writes docs/memory_measured.json.

    python -m training.shared.measure_memory [--out docs/memory_measured.json]
"""
from __future__ import annotations

import argparse
import json
import sys

import torch

import training  # noqa: F401
from training.general.model import GeneralNet
from training.hair.model import HairNet
from training.inpaint.model import InpaintNet

MB = 2 ** 20


def _train_peak(model, make_args, loss, batch, steps=3) -> float:
    dev = torch.device("cuda")
    model.to(dev).train()
    opt = torch.optim.AdamW(model.parameters(), 1e-4)
    scaler = torch.amp.GradScaler("cuda")
    torch.cuda.empty_cache(); torch.cuda.reset_peak_memory_stats()
    for _ in range(steps):
        with torch.autocast("cuda", dtype=torch.float16):
            l = loss(model(*[a.to(dev) for a in make_args(batch)]))
        scaler.scale(l).backward(); scaler.step(opt); scaler.update(); opt.zero_grad(set_to_none=True)
    peak = torch.cuda.max_memory_allocated() / MB
    model.cpu(); del opt; torch.cuda.empty_cache()
    return peak


def _infer_peak(models_args) -> float:
    dev = torch.device("cuda")
    torch.cuda.empty_cache(); torch.cuda.reset_peak_memory_stats()
    held = []
    with torch.no_grad():
        for m, args in models_args:
            m.to(dev).eval(); held.append(m)
            m(*[a.to(dev) for a in args])
    return torch.cuda.max_memory_allocated() / MB


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/memory_measured.json")
    a = ap.parse_args(argv)
    if not torch.cuda.is_available():
        print("No CUDA device found: run this on the machine with the NVIDIA card.", file=sys.stderr)
        return 2
    gpu = torch.cuda.get_device_name(0)
    g, h, i = GeneralNet(32, 5), HairNet(24, 4), InpaintNet(48)
    res = {"gpu": gpu, "vram_total_mb": torch.cuda.get_device_properties(0).total_memory / MB, "torch": torch.__version__,
           "train_peak_mb": {
               "general_512_b4_fp16": _train_peak(g, lambda b: (torch.zeros(b, 4, 512, 512),), lambda o: o[0].float().mean() + o[2].float().mean(), 4),
               "hair_256_b8_fp16": _train_peak(h, lambda b: (torch.zeros(b, 4, 256, 256),), lambda o: o[0].float().mean(), 8),
               "inpaint_256_b8_fp16": _train_peak(i, lambda b: (torch.zeros(b, 3, 256, 256), torch.zeros(b, 1, 256, 256)), lambda o: o.float().mean(), 8)}}
    gi, hi = (g, (torch.zeros(1, 4, 512, 512),)), (h, (torch.zeros(1, 4, 256, 256),))
    res["inference_peak_mb"] = {"general": _infer_peak([gi]), "hair": _infer_peak([hi]), "general_and_hair_in_one_job": _infer_peak([gi, hi])}
    json.dump(res, open(a.out, "w"), indent=1)
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
