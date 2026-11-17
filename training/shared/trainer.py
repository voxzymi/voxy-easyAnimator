"""Generic training loop: mixed precision (FP16 autocast + GradScaler on CUDA), gradient accumulation,
warm-up + cosine learning rate, checkpoint/resume, JSON-lines log. One job at a time is enforced by the
callers with ``joblock.JobLock``."""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import torch
from torch.utils.data import DataLoader, Dataset


@dataclass
class TrainResult:
    steps: int = 0
    epochs: int = 0
    last_loss: float = float("nan")
    first_loss: float = float("nan")
    best_score: float = float("-inf")
    peak_cuda_mb: Optional[float] = None
    seconds: float = 0.0
    device: str = "cpu"
    amp: bool = False
    history: List[Dict[str, Any]] = field(default_factory=list)


def _to(batch: Dict[str, torch.Tensor], dev: torch.device) -> Dict[str, torch.Tensor]:
    return {k: v.to(dev, non_blocking=True) for k, v in batch.items()}


def fit(model: torch.nn.Module,
        loss_fn: Callable[[torch.nn.Module, Dict[str, torch.Tensor]], Tuple[torch.Tensor, Dict[str, float]]],
        train_ds: Dataset, val_ds: Optional[Dataset],
        val_fn: Optional[Callable[[torch.nn.Module, DataLoader, torch.device], Dict[str, float]]],
        out_dir: str, *, epochs: int = 1, batch_size: int = 4, accum_steps: int = 1, lr: float = 2e-4,
        weight_decay: float = 1e-4, amp: bool = True, workers: int = 0, grad_clip: float = 1.0,
        max_steps: Optional[int] = None, device: Optional[str] = None, resume: bool = True, log_every: int = 10,
        seed: int = 0, warmup: int = 20, meta: Optional[Dict[str, Any]] = None) -> TrainResult:
    torch.manual_seed(seed)
    dev = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
    use_amp = bool(amp and dev.type == "cuda")
    os.makedirs(out_dir, exist_ok=True)
    model.to(dev)
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=lr, weight_decay=weight_decay)
    scaler = torch.amp.GradScaler("cuda", enabled=use_amp)
    loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True, num_workers=workers, drop_last=len(train_ds) > batch_size,
                        pin_memory=dev.type == "cuda", persistent_workers=workers > 0)
    vloader = DataLoader(val_ds, batch_size=batch_size, shuffle=False, num_workers=workers) if val_ds is not None and len(val_ds) else None
    steps_per_epoch = max(1, math.ceil(len(loader) / accum_steps))
    total = max_steps or epochs * steps_per_epoch

    def lr_at(step: int) -> float:
        if step < warmup:
            return lr * (step + 1) / warmup
        t = (step - warmup) / max(1, total - warmup)
        return lr * (0.01 + 0.99 * 0.5 * (1 + math.cos(math.pi * min(1.0, t))))

    res = TrainResult(device=dev.type, amp=use_amp)
    step, epoch0 = 0, 0
    ck = os.path.join(out_dir, "last.pt")
    if resume and os.path.isfile(ck):
        s = torch.load(ck, map_location=dev, weights_only=False)
        want, have = (meta or {}).get("kind"), (s.get("meta") or {}).get("kind")
        if want and have and want != have:
            raise RuntimeError(f"{ck} is a '{have}' checkpoint but this is a '{want}' run: use a different output folder")
        model.load_state_dict(s["model"])
        opt.load_state_dict(s["opt"])
        step, epoch0, res.best_score = s["step"], s["epoch"], s.get("best", res.best_score)
    logf = open(os.path.join(out_dir, "train_log.jsonl"), "a", encoding="utf-8")
    t0 = time.time()
    if dev.type == "cuda":
        torch.cuda.reset_peak_memory_stats()
    done = False
    for epoch in range(epoch0, epochs if not max_steps else 10 ** 9):
        model.train()
        opt.zero_grad(set_to_none=True)
        micro = 0
        for batch in loader:
            b = _to(batch, dev)
            with torch.autocast(device_type=dev.type, dtype=torch.float16, enabled=use_amp):
                loss, parts = loss_fn(model, b)
            scaler.scale(loss / accum_steps).backward()
            micro += 1
            if micro % accum_steps == 0:
                for g in opt.param_groups:
                    g["lr"] = lr_at(step)
                scaler.unscale_(opt)
                torch.nn.utils.clip_grad_norm_(params, grad_clip)
                scaler.step(opt)
                scaler.update()
                opt.zero_grad(set_to_none=True)
                step += 1
                lv = float(loss.detach())
                if math.isnan(res.first_loss):
                    res.first_loss = lv
                res.last_loss = lv
                if step % log_every == 0 or step == 1:
                    rec = {"step": step, "epoch": epoch, "loss": round(lv, 5), "lr": round(lr_at(step), 8),
                           **{k: round(v, 5) for k, v in parts.items()}}
                    logf.write(json.dumps(rec) + "\n")
                    logf.flush()
                if max_steps and step >= max_steps:
                    done = True
                    break
        res.epochs = epoch + 1
        metrics: Dict[str, float] = {}
        if vloader is not None and val_fn is not None:
            model.eval()
            with torch.no_grad():
                metrics = val_fn(model, vloader, dev)
            logf.write(json.dumps({"epoch": epoch, "val": {k: round(v, 5) for k, v in metrics.items()}}) + "\n")
            logf.flush()
            res.history.append({"epoch": epoch, **metrics})
            score = metrics.get("score", float("-inf"))
            if score > res.best_score:
                res.best_score = score
                torch.save({"model": model.state_dict(), "meta": meta or {}, "metrics": metrics}, os.path.join(out_dir, "best.pt"))
        torch.save({"model": model.state_dict(), "opt": opt.state_dict(), "step": step, "epoch": epoch + 1,
                    "best": res.best_score, "meta": meta or {}}, ck)
        if done or epoch + 1 >= epochs and not max_steps:
            break
    if not os.path.isfile(os.path.join(out_dir, "best.pt")):
        torch.save({"model": model.state_dict(), "meta": meta or {}, "metrics": {}}, os.path.join(out_dir, "best.pt"))
    res.steps, res.seconds = step, time.time() - t0
    if dev.type == "cuda":
        res.peak_cuda_mb = torch.cuda.max_memory_allocated() / 2 ** 20
    logf.close()
    return res
