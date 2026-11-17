"""Service configuration (YAML) and per-OS data folders."""
from __future__ import annotations

import copy
import os
import sys
from typing import Any, Dict, Optional

import yaml

DEFAULTS: Dict[str, Any] = {
    "service": {
        "host": "127.0.0.1",
        "port": 0,                         # 0 = choose a free port
        "allow_stub_models": True,         # release builds set this to false
        "max_job_history": 25,
        "max_request_mb": 256,
    },
    "timeouts_s": {"load": 30, "matting": 90, "general": 180, "hair": 180, "inpaint": 240,
                   "postprocess": 60, "upscale": 900},
    "device": {"prefer": "cuda", "cpu_fallback": True},
    "models": {
        "root": "models",
        "general": {"backend": "stub", "version": "0.1.0"},
        "hair": {"backend": "stub", "version": "0.1.0"},
        "matting": {"backend": "algorithm", "version": "0.1.0"},
        "inpaint": {"backend": "patch", "version": "0.1.0"},
    },
    "make_layers": {"min_part_pixels": 12},
    "hair": {"default_count": 6, "default_smoothing": 0.3, "min_count": 2, "max_count": 24},
    "background": {"fill": "auto", "feather_px": 3, "hole_grow_px": 3, "patch_size": 9, "patch_work_max": 256},
    "upscale": {
        "executable": None,
        "models_dir": None,
        "model": "realesr-animevideov3-x2",
        "model_scale": 2,                  # -z: the scale the model was trained for
        "scale": 2,                        # -s: the output scale we ask for
        "gpu_id": None,
        "tile_sequence": [0, 256, 128, 64, 32],   # 0 = let the tool choose; the rest are OOM retries
        "max_single_pass_px": 9_000_000,   # above this we split the picture ourselves
        "outer_tile": 1536,
        "outer_overlap": 32,
    },
    "disk": {"required_free_mb": 3000},
}


def _merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: Optional[str] = None, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = copy.deepcopy(DEFAULTS)
    if path:
        with open(path, "r", encoding="utf-8") as f:
            cfg = _merge(cfg, yaml.safe_load(f) or {})
    if overrides:
        cfg = _merge(cfg, overrides)
    return cfg


def default_data_dir() -> str:
    env = os.environ.get("VOXY_DATA_DIR")
    if env:
        return env
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        return os.path.join(base, "VoxyEasyAnimator")
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "VoxyEasyAnimator")


def app_dir() -> str:
    """Folder that holds the installed app (models, tools). Frozen builds: next to the executable."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
