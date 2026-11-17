"""Background redraw: fill the area the character covered, then blend the edge."""
from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np

from .cancel import CancelToken
from .errors import VoxyError
from .inpaint import OnnxInpainter, blend_fill, fill_region_for, patch_fill


def choose_engine(requested: str, model_dir: str, inpaint_backend: str) -> Tuple[str, Optional[str]]:
    """Return (engine, warning_code). ``auto`` uses the model when installed, else the patch fill."""
    have_model = inpaint_backend == "onnx" and os.path.isfile(os.path.join(model_dir, "model.onnx"))
    if requested == "patch":
        return "patch", None
    if requested == "model":
        if not have_model:
            raise VoxyError("INPAINT_MODEL_MISSING", f"no inpainting model in {model_dir}", stage="inpaint")
        return "model", None
    return ("model", None) if have_model else ("patch", "BACKGROUND_FILL_FALLBACK")


def make_background_plate(rgba: np.ndarray, character: np.ndarray, *, engine: str, bg_cfg: Dict[str, Any],
                          inpainter: Optional[OnnxInpainter], cancel: CancelToken) -> np.ndarray:
    """Return an opaque RGBA picture of the scene with the character removed."""
    if (rgba[:, :, 3] < 250).any():
        raise VoxyError("INPAINT_FAILED", "the picture is transparent: there is no background to redraw", stage="inpaint")
    grow = int(bg_cfg["hole_grow_px"])
    feather = float(bg_cfg["feather_px"])
    hole = character.copy()
    if grow > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow + 1, 2 * grow + 1))
        hole = cv2.dilate(hole.astype(np.uint8), k) > 0
    if not hole.any():
        raise VoxyError("INPAINT_FAILED", "nothing to fill", stage="inpaint")
    region = fill_region_for(hole, feather)
    rgb = np.ascontiguousarray(rgba[:, :, :3])
    if engine == "model":
        if inpainter is None:
            raise VoxyError("INPAINT_MODEL_MISSING", "model engine without a model", stage="inpaint")
        filled = inpainter.fill(rgb, region, cancel)
    else:
        filled = patch_fill(rgb, region, patch=int(bg_cfg["patch_size"]), work_max=int(bg_cfg["patch_work_max"]),
                            cancel=cancel)
    out = blend_fill(rgb, filled, hole, feather)
    plate = np.dstack([out, np.full(out.shape[:2], 255, np.uint8)])
    return plate
