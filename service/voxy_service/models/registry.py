"""Choose and (lazily) load the models named in the config."""
from __future__ import annotations

import os
import threading
from typing import Any, Dict, Optional

from ..errors import VoxyError
from .base import GeneralModel, HairModel
from .stub import StubGeneralModel, StubHairModel


class ModelRegistry:
    """Models load on first use, so the service starts fast and a missing file is reported
    as a friendly error when the job needs it, not at launch."""

    def __init__(self, cfg: Dict[str, Any], app_dir: str):
        self.cfg = cfg
        self.root = cfg["models"]["root"]
        if not os.path.isabs(self.root):
            self.root = os.path.join(app_dir, self.root)
        self._lock = threading.Lock()
        self._cache: Dict[str, Any] = {}
        self.allow_stub = bool(cfg["service"]["allow_stub_models"])
        self.prefer = cfg["device"]["prefer"]
        self.cpu_fallback = bool(cfg["device"]["cpu_fallback"])
        self.mem_mb = int(cfg["device"].get("gpu_mem_limit_mb", 0))
        self.warnings: Dict[str, str] = {}

    def model_dir(self, name: str) -> str:
        spec = self.cfg["models"][name]
        return os.path.join(self.root, name, str(spec["version"]))

    def _backend(self, name: str) -> str:
        b = self.cfg["models"][name]["backend"]
        if b == "stub" and not self.allow_stub:
            code = {"general": "GENERAL_MODEL_MISSING", "hair": "HAIR_MODEL_MISSING"}.get(name, "INTERNAL_ERROR")
            raise VoxyError(code, f"stub {name} model is disabled in this build")
        return b

    def general(self) -> GeneralModel:
        with self._lock:
            if "general" not in self._cache:
                if self._backend("general") == "stub":
                    self._cache["general"] = StubGeneralModel(self.cfg["models"]["general"]["version"])
                else:
                    from .onnx_models import OnnxGeneralModel
                    m = OnnxGeneralModel(self.model_dir("general"), self.prefer, self.cpu_fallback, self.mem_mb)
                    if m.warning:
                        self.warnings["general"] = m.warning
                    self._cache["general"] = m
            return self._cache["general"]

    def hair(self) -> HairModel:
        with self._lock:
            if "hair" not in self._cache:
                if self._backend("hair") == "stub":
                    self._cache["hair"] = StubHairModel(self.cfg["models"]["hair"]["version"])
                else:
                    from .onnx_models import OnnxHairModel
                    m = OnnxHairModel(self.model_dir("hair"), self.prefer, self.cpu_fallback, self.mem_mb)
                    if m.warning:
                        self.warnings["hair"] = m.warning
                    self._cache["hair"] = m
            return self._cache["hair"]

    def override(self, name: str, model: Any) -> None:
        """Tests (and the training evaluation) inject a model object directly."""
        with self._lock:
            self._cache[name] = model

    def describe(self) -> Dict[str, Any]:
        """For /v1/health: what is configured and whether the files are there. Loads nothing."""
        out: Dict[str, Any] = {}
        for name in ("general", "hair", "matting", "inpaint"):
            spec = self.cfg["models"][name]
            backend = spec["backend"]
            ok = True
            if backend == "onnx":
                ok = os.path.isfile(os.path.join(self.model_dir(name), "model.onnx"))
            elif backend == "stub":
                ok = self.allow_stub
            out[name] = {"backend": backend, "version": str(spec["version"]), "available": ok,
                         "loaded": name in self._cache}
        return out

    def device(self) -> Dict[str, Any]:
        from .onnx_models import available_providers
        cuda = "CUDAExecutionProvider" in available_providers()
        used = "cpu"
        for m in self._cache.values():
            d = getattr(getattr(m, "info", None), "device", None)
            if d == "cuda":
                used = "cuda"
        return {"cuda_available": cuda, "in_use": used if self._cache else ("cuda" if cuda else "cpu"),
                "warnings": dict(self.warnings)}
