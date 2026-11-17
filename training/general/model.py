"""General dissection model: part segmentation (hair is ONE class), key points, hidden-part extents and completion.

Outputs (the contract the service's OnnxGeneralModel relies on, see docs/models.md):
    seg         [B, 22, S, S]   class logits (channel 0 = background)
    kpt         [B, 15, S/4, S/4] key-point heat-maps
    amodal      [B, 4, S, S]    logits of the full extent of head / eye_l / eye_r / mouth
    completion  [B, 3, S, S]    RGB in [0, 1] for the covered parts
The group of a part is derived from its class (fixed mapping), so there is no group head.
"""
from __future__ import annotations

import torch
import torch.nn as nn

import training  # noqa: F401
from training.shared.unet import UNet
from voxy_service import schema as S


class GeneralNet(nn.Module):
    def __init__(self, base: int = 32, depth: int = 5, in_ch: int = 4):
        super().__init__()
        self.backbone = UNet(in_ch, base, depth)
        c0 = self.backbone.chs[0]
        self.seg = nn.Conv2d(c0, S.NUM_CLASSES, 1)
        self.amodal = nn.Conv2d(c0, len(S.AMODAL_CLASSES), 1)
        self.completion = nn.Sequential(nn.Conv2d(c0, c0, 3, padding=1), nn.SiLU(), nn.Conv2d(c0, 3, 1), nn.Sigmoid())
        self.kpt = nn.Conv2d(self.backbone.chs[2], len(S.KEYPOINTS), 1)       # from the stride-4 decoder features

    def forward(self, x: torch.Tensor):
        f = self.backbone(x)
        return self.seg(f[0]), self.kpt(f[2]), self.amodal(f[0]), self.completion(f[0])


OUTPUT_NAMES = ["seg", "kpt", "amodal", "completion"]
