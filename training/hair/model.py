"""Hair subdivision model: works ONLY on the hair mask produced by the general model.

Input  [B, 4, S, S]: RGB of the hair crop in [-1, 1] and the hair mask.
Output flow  [B, 2, S, S]: strand direction at each pixel (normalised outside the network)
       roots [B, 1, S, S]: logits of the points where strands start
It never segments the character. The subdivision itself is geometry (voxy_service.hair.subdivide).
"""
from __future__ import annotations

import torch
import torch.nn as nn

import training  # noqa: F401
from training.shared.unet import UNet


class HairNet(nn.Module):
    def __init__(self, base: int = 24, depth: int = 4, in_ch: int = 4):
        super().__init__()
        self.backbone = UNet(in_ch, base, depth)
        c0 = self.backbone.chs[0]
        self.flow = nn.Conv2d(c0, 2, 1)
        self.roots = nn.Conv2d(c0, 1, 1)

    def forward(self, x: torch.Tensor):
        f = self.backbone(x)[0]
        return self.flow(f), self.roots(f)


OUTPUT_NAMES = ["flow", "roots"]
