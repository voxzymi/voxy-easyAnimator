"""ONNX export with a parity check against PyTorch and the model.json the service reads."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from typing import Any, Dict, List, Sequence

import numpy as np
import torch


def export_model(model: torch.nn.Module, example: Sequence[torch.Tensor], input_names: List[str], output_names: List[str],
                 out_dir: str, meta: Dict[str, Any], *, opset: int = 17, atol: float = 2e-3) -> Dict[str, Any]:
    """Write ``out_dir/model.onnx`` and ``out_dir/model.json``; raise if onnxruntime disagrees with PyTorch."""
    import onnxruntime as ort
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "model.onnx")
    model = model.cpu().eval()
    example = tuple(e.cpu() for e in example)
    torch.onnx.export(model, example, path, input_names=input_names, output_names=output_names, opset_version=opset,
                      dynamo=False, do_constant_folding=True)
    sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    got = sess.run(None, {n: e.numpy() for n, e in zip(input_names, example)})
    with torch.no_grad():
        want = model(*example)
    want = want if isinstance(want, (tuple, list)) else (want,)
    worst = 0.0
    for name, g, w in zip(output_names, got, want):
        diff = float(np.abs(g - w.numpy()).max())
        worst = max(worst, diff)
        if diff > atol:
            raise RuntimeError(f"ONNX output '{name}' differs from PyTorch by {diff:.5f} (> {atol})")
    sha = hashlib.sha256(open(path, "rb").read()).hexdigest()
    info = {"export_date": dt.date.today().isoformat(), "opset": opset, "max_abs_diff_vs_torch": worst,
            "sha256": sha, "size_mb": round(os.path.getsize(path) / 2 ** 20, 2), "torch": torch.__version__,
            "params_m": round(sum(p.numel() for p in model.parameters()) / 1e6, 3), **meta}
    with open(os.path.join(out_dir, "model.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, indent=1)
    return info
