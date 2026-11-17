"""Export a trained general model to models/general/<version>/model.onnx (+ model.json)."""
from __future__ import annotations

import argparse
import json
import sys

import torch

import training  # noqa: F401
from training.general.model import OUTPUT_NAMES, GeneralNet
from training.shared.onnx_export import export_model
from voxy_service import schema as S


def export(ckpt: str, out_dir: str, version: str, trained_on: str = "") -> dict:
    s = torch.load(ckpt, map_location="cpu", weights_only=False)
    cfg = s["meta"]["config"]
    m = GeneralNet(base=cfg["model"]["base"], depth=cfg["model"]["depth"])
    m.load_state_dict(s["model"])
    size = int(cfg["model"]["input_size"])
    meta = {"name": "general", "version": version, "input_size": size, "classes": S.CLASS_NAMES, "keypoints": S.KEYPOINTS,
            "amodal_classes": S.AMODAL_CLASSES, "outputs": OUTPUT_NAMES, "validation": s.get("metrics", {}),
            "input": "[1,4,S,S]: RGB in [-1,1] (transparent areas white) + foreground mask; letterboxed (voxy_service.preproc)",
            "license": "Voxy project weights", "trained_on": trained_on or "synthetic CC0 data (see dataset_info.json)"}
    return export_model(m, (torch.zeros(1, 4, size, size),), ["input"], OUTPUT_NAMES, out_dir, meta)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", default=None, help="default: models/general/<version>")
    ap.add_argument("--trained-on", default="")
    a = ap.parse_args(argv)
    print(json.dumps(export(a.checkpoint, a.out or f"models/general/{a.version}", a.version, a.trained_on), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
