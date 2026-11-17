"""Compact large-mask inpainting network: a dilated-convolution encoder/decoder (large receptive field, every op
exports to ONNX). Contract used by the service's OnnxInpainter:
    inputs  image [B,3,S,S] in [0,1], mask [B,1,S,S] (1 = fill)      output [B,3,S,S] in [0,1]
For a stronger base, the service can also load an ONNX export of a LaMa-style model (see docs/models.md).
"""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

import training  # noqa: F401
from training.shared.unet import _gn


class DilatedRes(nn.Module):
    def __init__(self, c: int, d: int):
        super().__init__()
        self.body = nn.Sequential(nn.Conv2d(c, c, 3, padding=d, dilation=d, bias=False), _gn(c), nn.SiLU(inplace=True),
                                  nn.Conv2d(c, c, 3, padding=1, bias=False), _gn(c))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return F.silu(x + self.body(x))


class InpaintNet(nn.Module):
    def __init__(self, base: int = 48, dilations=(1, 2, 4, 8, 16, 1, 2, 4, 8, 16)):
        super().__init__()
        self.e1 = nn.Sequential(nn.Conv2d(4, base, 5, padding=2), _gn(base), nn.SiLU(inplace=True))
        self.e2 = nn.Sequential(nn.Conv2d(base, base * 2, 3, stride=2, padding=1), _gn(base * 2), nn.SiLU(inplace=True))
        self.e3 = nn.Sequential(nn.Conv2d(base * 2, base * 4, 3, stride=2, padding=1), _gn(base * 4), nn.SiLU(inplace=True))
        self.mid = nn.Sequential(*[DilatedRes(base * 4, d) for d in dilations])
        self.d2 = nn.Sequential(nn.Conv2d(base * 4 + base * 2, base * 2, 3, padding=1), _gn(base * 2), nn.SiLU(inplace=True))
        self.d1 = nn.Sequential(nn.Conv2d(base * 2 + base, base, 3, padding=1), _gn(base), nn.SiLU(inplace=True))
        self.out = nn.Conv2d(base, 3, 1)
        self.encoder_modules = [self.e1, self.e2, self.e3]

    def forward(self, image: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        x = torch.cat([image * (1 - mask), mask], dim=1)
        a = self.e1(x)
        b = self.e2(a)
        c = self.mid(self.e3(b))
        d = self.d2(torch.cat([F.interpolate(c, size=b.shape[2:], mode="bilinear", align_corners=False), b], 1))
        d = self.d1(torch.cat([F.interpolate(d, size=a.shape[2:], mode="bilinear", align_corners=False), a], 1))
        return torch.sigmoid(self.out(d))

    def freeze_encoder(self) -> None:
        """Save memory when fine-tuning: only the middle blocks and decoder learn."""
        for m in self.encoder_modules:
            for p in m.parameters():
                p.requires_grad = False
