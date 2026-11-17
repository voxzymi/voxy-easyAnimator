"""YAML configs with ``--set a.b=value`` overrides."""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List

import yaml

_FLOAT = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)[eE][+-]?\d+$")


def _coerce(v: Any) -> Any:
    """PyYAML reads ``2e-3`` (no decimal point) as a string; read it as the float it obviously is."""
    if isinstance(v, str) and _FLOAT.match(v):
        return float(v)
    if isinstance(v, list):
        return [_coerce(x) for x in v]
    if isinstance(v, dict):
        return {k: _coerce(x) for k, x in v.items()}
    return v


def load(path: str, overrides: List[str] = ()) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        cfg = _coerce(yaml.safe_load(f) or {})
    cfg = copy.deepcopy(cfg)
    for o in overrides:
        key, _, val = o.partition("=")
        node = cfg
        parts = key.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = _coerce(yaml.safe_load(val))
    return cfg
