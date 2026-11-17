"""Memory estimates for the 12 GB reference card.

    python -m training.shared.memory_estimate            # CPU-side estimate from measured activation sizes
    python -m training.shared.measure_memory             # real peak VRAM, run this on the RTX 3060

The estimate counts the bytes PyTorch actually saves for the backward pass (measured with saved-tensor hooks at the
real input size), plus weights, gradients and Adam state. It is an estimate: CUDA context, cuDNN workspaces and
allocator fragmentation add a few hundred MB, so always confirm with measure_memory.py.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict

import torch

import training  # noqa: F401
from training.general.model import GeneralNet
from training.hair.model import HairNet
from training.inpaint.model import InpaintNet

MB = 2 ** 20


def _saved_bytes(fn) -> int:
    total = [0]
    seen = set()

    def pack(t: torch.Tensor):
        key = (t.data_ptr(), t.numel(), t.dtype)
        if key not in seen:
            seen.add(key)
            total[0] += t.numel() * t.element_size()
        return t

    with torch.autograd.graph.saved_tensors_hooks(pack, lambda t: t):
        out = fn()
        (out[0] if isinstance(out, (tuple, list)) else out).float().sum().backward()
    return total[0]


def _largest_activations(model: torch.nn.Module, args) -> int:
    sizes = []
    hooks = [m.register_forward_hook(lambda m, i, o: sizes.append(o.numel() * 4 if isinstance(o, torch.Tensor) else 0))
             for m in model.modules() if not list(m.children())]
    with torch.no_grad():
        model(*args)
    for h in hooks:
        h.remove()
    sizes.sort(reverse=True)
    return sum(sizes[:3])               # the few biggest tensors alive together (skip connections keep some longer)


def estimate(name: str, model: torch.nn.Module, args, batch: int, amp: bool = True) -> Dict[str, Any]:
    params = sum(p.numel() for p in model.parameters())
    act32 = _saved_bytes(lambda: model(*args))                      # batch 1, fp32
    act = act32 * batch * (0.55 if amp else 1.0)                    # autocast keeps norm/softmax in fp32: ~0.55x
    train = params * 16 + act                                       # fp32 weights + grads + Adam m,v (4+4+4+4 bytes)
    infer_w = params * 4
    infer_act = _largest_activations(model, args) * 0.5             # FP16 not required: ORT runs fp32; keep 0.5 margin for fusion
    return {"model": name, "params_m": round(params / 1e6, 2), "train_batch": batch, "amp": amp,
            "train_gb_estimate": round(train / 2 ** 30 + 0.6, 2),   # +0.6 GB CUDA context and workspaces
            "activation_mb_per_sample_fp32": round(act32 / MB, 1),
            "inference_mb_estimate": round((infer_w + infer_act) / MB + 300, 0)}   # +300 MB runtime/context


def main() -> int:
    res = []
    g = GeneralNet(32, 5); h = HairNet(24, 4); i = InpaintNet(48)
    res.append(estimate("general (512x512, batch 4, FP16)", g, (torch.zeros(1, 4, 512, 512),), 4))
    res.append(estimate("hair (256x256, batch 8, FP16)", h, (torch.zeros(1, 4, 256, 256),), 8))
    res.append(estimate("inpaint (256x256, batch 8, FP16)", i, (torch.zeros(1, 3, 256, 256), torch.zeros(1, 1, 256, 256)), 8))
    both = res[0]["inference_mb_estimate"] + res[1]["inference_mb_estimate"]
    out = {"models": res, "general_plus_hair_resident_inference_mb": both,
           "note": "estimates, not measurements: confirm on the target GPU with training.shared.measure_memory"}
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
