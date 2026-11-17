"""A small U-Net backbone shared by the general and hair models (GroupNorm, so small batches and gradient
accumulation behave; every op exports to ONNX)."""
from __future__ import annotations

from typing import List

import torch
import torch.nn as nn
import torch.nn.functional as F


def _gn(c: int) -> nn.GroupNorm:
    g = 8 if c % 8 == 0 else (4 if c % 4 == 0 else 1)
    return nn.GroupNorm(g, c)


def conv_block(cin: int, cout: int) -> nn.Sequential:
    return nn.Sequential(nn.Conv2d(cin, cout, 3, padding=1, bias=False), _gn(cout), nn.SiLU(inplace=True),
                         nn.Conv2d(cout, cout, 3, padding=1, bias=False), _gn(cout), nn.SiLU(inplace=True))


class UNet(nn.Module):
    """Encoder-decoder with skip connections. ``forward`` returns decoder features at every stride:
    index i has stride 2**i (index 0 = full resolution)."""

    def __init__(self, in_ch: int, base: int = 32, depth: int = 5, cap: int = 8):
        super().__init__()
        self.chs = [base * min(2 ** i, cap) for i in range(depth)]
        self.enc = nn.ModuleList([conv_block(in_ch if i == 0 else self.chs[i - 1], self.chs[i]) for i in range(depth)])
        self.dec = nn.ModuleList([conv_block(self.chs[i + 1] + self.chs[i], self.chs[i]) for i in range(depth - 1)])
        self.depth = depth

    def forward(self, x: torch.Tensor) -> List[torch.Tensor]:
        skips = []
        for i, e in enumerate(self.enc):
            x = e(x if i == 0 else F.max_pool2d(x, 2))
            skips.append(x)
        feats = [None] * self.depth
        feats[-1] = x
        for i in range(self.depth - 2, -1, -1):
            up = F.interpolate(feats[i + 1], size=skips[i].shape[2:], mode="bilinear", align_corners=False)
            feats[i] = self.dec[i](torch.cat([up, skips[i]], dim=1))
        return feats
