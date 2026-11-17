"""Interfaces for the two dissection models and the helper models.

The general model and the hair model are independent: neither imports or calls
the other. The pipeline wires them together.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Protocol, Tuple

import numpy as np

from ..cancel import CancelToken


@dataclass
class ModelInfo:
    name: str
    version: str
    backend: str          # stub | onnx | ncnn | algorithm
    device: str = "cpu"   # cuda | cpu | vulkan | n/a

    def to_dict(self) -> Dict[str, str]:
        return {"name": self.name, "version": self.version, "backend": self.backend, "device": self.device}

    def produced_by(self, model: str) -> Dict[str, str]:
        return {"model": model, "version": self.version, "backend": self.backend}


@dataclass
class GeneralOutput:
    labels: np.ndarray                                   # HxW uint8, visible class index, 0 = background
    keypoints: Dict[str, Tuple[float, float, float]] = field(default_factory=dict)  # name -> (x, y, score)
    amodal: Dict[str, np.ndarray] = field(default_factory=dict)     # base class -> bool HxW full extent
    completion_rgb: Optional[np.ndarray] = None          # HxWx3 uint8, completed pixels for hidden regions
    class_confidence: Dict[str, float] = field(default_factory=dict)


@dataclass
class HairOutput:
    flow: np.ndarray        # HxWx2 float32, unit strand direction (dx, dy) per pixel
    roots: np.ndarray       # HxW float32 in [0, 1], where strands start


class GeneralModel(Protocol):
    info: ModelInfo

    def predict(self, rgba: np.ndarray, cancel: CancelToken) -> GeneralOutput: ...


class HairModel(Protocol):
    info: ModelInfo

    def predict(self, rgb: np.ndarray, hair_mask: np.ndarray, cancel: CancelToken) -> HairOutput: ...
