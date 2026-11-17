"""Preflight checks, in plain words. Run by the installer and again by the panel's "Test setup".

Every result is ``{id, status: ok|warn|fail, title, message, what_to_do}``. ``warn`` means
Voxy still works (for example in the slower mode); ``fail`` means it cannot run.
"""
from __future__ import annotations

import ctypes.util
import os
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional

from .models.onnx_models import available_providers

MIN_VRAM_MB_FAST = 6000          # the 12 GB reference card has plenty of headroom; below this we warn


def _check(id_: str, status: str, title: str, message: str, what_to_do: str = "") -> Dict[str, str]:
    return {"id": id_, "status": status, "title": title, "message": message, "what_to_do": what_to_do}


def query_nvidia() -> Optional[Dict[str, Any]]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        p = subprocess.run([exe, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0 or not p.stdout.strip():
        return None
    name, mem, drv = [x.strip() for x in p.stdout.strip().splitlines()[0].split(",")]
    try:
        return {"name": name, "vram_mb": int(float(mem)), "driver": drv}
    except ValueError:
        return {"name": name, "vram_mb": 0, "driver": drv}


def vulkan_present() -> bool:
    if sys.platform.startswith("win"):
        sysroot = os.environ.get("SystemRoot", r"C:\Windows")
        return os.path.isfile(os.path.join(sysroot, "System32", "vulkan-1.dll"))
    return bool(ctypes.util.find_library("vulkan"))


def check_gpu() -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    gpu = query_nvidia()
    cuda_ep = "CUDAExecutionProvider" in available_providers()
    if gpu is None:
        out.append(_check("gpu", "warn", "Graphics card",
                          "Voxy did not find a supported NVIDIA graphics card. It will use the slower mode.",
                          "Update your NVIDIA driver to use the fast mode. The slow mode will still work."))
    elif gpu["vram_mb"] and gpu["vram_mb"] < MIN_VRAM_MB_FAST:
        out.append(_check("gpu", "warn", "Graphics card",
                          f"Your graphics card ({gpu['name']}) has {gpu['vram_mb'] // 1024} GB of memory. Big pictures may be slow or fail.",
                          "Use smaller pictures, or close other programs that use the graphics card."))
    elif not cuda_ep:
        out.append(_check("gpu", "warn", "Graphics card",
                          f"Your graphics card ({gpu['name']}) was found, but Voxy cannot use it for the fast mode yet.",
                          "Update your NVIDIA driver to use the fast mode. The slow mode will still work."))
    else:
        out.append(_check("gpu", "ok", "Graphics card", "Your graphics card is supported."))
    if vulkan_present():
        out.append(_check("vulkan", "ok", "Upscaling support", "Your computer can run the upscaler."))
    else:
        out.append(_check("vulkan", "warn", "Upscaling support",
                          "Your computer does not seem to have the graphics support the upscaler needs.",
                          "Update your NVIDIA driver, then run Test setup again. Everything except Upscale 2x will still work."))
    return out


def check_disk(data_dir: str, required_mb: int) -> Dict[str, str]:
    probe = data_dir
    while probe and not os.path.exists(probe):
        probe = os.path.dirname(probe)
    free_mb = shutil.disk_usage(probe or ".").free // (1024 * 1024)
    if free_mb < required_mb:
        return _check("disk", "fail", "Free disk space",
                      f"Only {free_mb // 1024} GB is free. Voxy needs about {required_mb // 1024} GB for its files and backups.",
                      "Free up some disk space, then try again.")
    return _check("disk", "ok", "Free disk space", f"{free_mb // 1024} GB is free. That is enough.")


def check_models(describe: Dict[str, Any]) -> Dict[str, str]:
    missing = [n for n in ("general", "hair") if not describe[n]["available"]]
    if missing:
        return _check("models", "fail", "Voxy files",
                      "Some of Voxy's files are missing, so it cannot cut your character yet.",
                      "Run the installer again to repair Voxy EasyAnimator.")
    stub = [n for n in ("general", "hair") if describe[n]["backend"] == "stub"]
    if stub:
        return _check("models", "warn", "Voxy files",
                      "This is a test build: it uses placeholder results instead of the real cutting files.",
                      "Install the full version from the official download page.")
    return _check("models", "ok", "Voxy files", "All of Voxy's files are in place.")


def check_upscaler(info: Dict[str, Any]) -> Dict[str, str]:
    if info.get("ok"):
        return _check("upscaler", "ok", "Upscaler", "The upscaler is installed and ready.")
    reason = info.get("reason")
    if reason == "missing":
        return _check("upscaler", "warn", "Upscaler", "The upscaler is not installed. Upscale 2x will not work.",
                      "Run the installer again to repair Voxy EasyAnimator.")
    return _check("upscaler", "warn", "Upscaler", "The upscaler is installed but not ready. Upscale 2x may not work.",
                  "Run the installer again to repair Voxy EasyAnimator.")


def summarize(checks: List[Dict[str, str]]) -> Dict[str, Any]:
    order = {"ok": 0, "warn": 1, "fail": 2}
    overall = max((c["status"] for c in checks), key=lambda s: order[s], default="ok")
    return {"overall": overall, "checks": checks}
