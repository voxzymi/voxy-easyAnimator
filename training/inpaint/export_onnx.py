"""Export the inpainting model to models/inpaint/<version>/model.onnx (+ model.json)."""
from __future__ import annotations

import argparse
import json
import sys

import torch

import training  # noqa: F401
from training.inpaint.model import InpaintNet
from training.shared.onnx_export import export_model


def export(ckpt: str, out_dir: str, version: str) -> dict:
    s = torch.load(ckpt, map_location="cpu", weights_only=False)
    cfg = s["meta"]["config"]
    m = InpaintNet(base=cfg["model"]["base"])
    m.load_state_dict(s["model"])
    size = int(cfg["model"]["input_size"])
    meta = {"name": "inpaint", "version": version, "input_size": size, "output_range": 1.0, "outputs": ["output"],
            "license": "Voxy project weights", "trained_on": "synthetic CC0 occlusion pairs",
            "input": "image [1,3,S,S] in [0,1]; mask [1,1,S,S] (1 = fill)"}
    return export_model(m, (torch.zeros(1, 3, size, size), torch.zeros(1, 1, size, size)), ["image", "mask"], ["output"], out_dir, meta)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    print(json.dumps(export(a.checkpoint, a.out or f"models/inpaint/{a.version}", a.version), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
