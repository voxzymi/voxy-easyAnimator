"""Losses (PyTorch)."""
from __future__ import annotations

import torch
import torch.nn.functional as F


def dice_loss(logits: torch.Tensor, target: torch.Tensor, eps: float = 1.0) -> torch.Tensor:
    """Soft Dice over classes. logits [B,C,H,W], target [B,H,W] int. Averaged over the classes present in the
    batch (a class absent from both target and prediction should not pull the loss down)."""
    c = logits.shape[1]
    p = F.softmax(logits.float(), dim=1)
    t = F.one_hot(target, c).permute(0, 3, 1, 2).float()
    inter = (p * t).sum(dim=(0, 2, 3))
    denom = p.sum(dim=(0, 2, 3)) + t.sum(dim=(0, 2, 3))
    dice = (2 * inter + eps) / (denom + eps)
    present = (t.sum(dim=(0, 2, 3)) > 0)
    return 1 - dice[present].mean() if present.any() else logits.sum() * 0


def seg_loss(logits: torch.Tensor, target: torch.Tensor, ce_weight: float = 1.0, dice_weight: float = 1.0) -> torch.Tensor:
    """Dice plus cross-entropy (the general model's segmentation loss)."""
    ce = F.cross_entropy(logits.float(), target)
    return ce_weight * ce + dice_weight * dice_loss(logits, target)


def heatmap_mse(pred: torch.Tensor, target: torch.Tensor, valid: torch.Tensor) -> torch.Tensor:
    """MSE on key-point heat-maps. valid [B,K] (0 where the joint is not labelled / outside the picture)."""
    se = (pred.float() - target) ** 2
    w = valid[:, :, None, None].float()
    return (se * w).sum() / (w.sum() * pred.shape[2] * pred.shape[3] + 1e-6)


def amodal_loss(logits: torch.Tensor, target: torch.Tensor, valid: torch.Tensor) -> torch.Tensor:
    """BCE + Dice on full-extent masks; ``valid`` [B] is 0 for samples without amodal labels (real images)."""
    if valid.sum() == 0:
        return logits.sum() * 0
    v = valid.bool()
    lg, tg = logits[v].float(), target[v]
    bce = F.binary_cross_entropy_with_logits(lg, tg)
    p = torch.sigmoid(lg)
    inter = (p * tg).sum(dim=(0, 2, 3))
    dice = 1 - (2 * inter + 1) / (p.sum(dim=(0, 2, 3)) + tg.sum(dim=(0, 2, 3)) + 1)
    return bce + dice.mean()


def completion_l1(pred: torch.Tensor, target: torch.Tensor, weight: torch.Tensor, valid: torch.Tensor) -> torch.Tensor:
    if valid.sum() == 0:
        return pred.sum() * 0
    v = valid.bool()
    w = weight[v]
    return ((pred[v].float() - target[v]).abs() * w).sum() / (w.sum() * 3 + 1e-6)


def angular_loss(flow: torch.Tensor, target: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    """Angular error on the unit direction: 1 - cos(angle), inside the hair mask. flow/target [B,2,H,W]."""
    f = F.normalize(flow.float(), dim=1, eps=1e-6)
    cos = (f * target).sum(dim=1, keepdim=True)
    m = mask.float()
    return ((1 - cos) * m).sum() / (m.sum() + 1e-6)


def root_loss(logits: torch.Tensor, heat: torch.Tensor, pos_weight: float = 20.0) -> torch.Tensor:
    """Root-point heat-map: BCE with a positive weight (roots are a few pixels in a big picture)."""
    return F.binary_cross_entropy_with_logits(logits.float(), heat, pos_weight=torch.tensor(pos_weight, device=logits.device))
