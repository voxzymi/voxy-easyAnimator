"""Wrapper around upscayl-ncnn (https://github.com/upscayl/upscayl-ncnn), run as a subprocess.

Flags used (read from the tool's own source and re-checked at run time with ``-h``; never assumed):
  -i input  -o output  -z model-scale  -s output-scale  -n model-name  -m models-folder
  -t tile-size (0 = auto)  -g gpu-id  -f format

The wrapper only ever touches the one picture it is given. Large pictures are split into
overlapping tiles by us (so host memory stays bounded); each tile is run with a tile size
chosen for the GPU, and an out-of-memory failure is retried with smaller tiles.
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from .cancel import CancelToken
from .errors import VoxyError

REQUIRED_FLAGS = {"i", "o", "s", "n", "m", "t", "f"}
OOM_PATTERNS = re.compile(r"out of memory|vkallocatememory failed|vkqueuesubmit failed|vk_error_out_of|"
                          r"vkcreate\w+ failed|bad_alloc|cannot allocate", re.I)
EXE_NAMES = ["upscayl-bin", "upscayl-ncnn", "upscayl-bin.exe", "upscayl-ncnn.exe"]


@dataclass
class UpscaleResult:
    output_path: str
    input_size: Tuple[int, int]
    output_size: Tuple[int, int]
    tile: int
    attempts: int
    model: str


def find_executable(cfg: Dict[str, Any], app_dir: str) -> Optional[str]:
    cands: List[str] = []
    if cfg.get("executable"):
        cands.append(cfg["executable"])
    for n in EXE_NAMES:
        cands.append(os.path.join(app_dir, "tools", "upscayl", n))
    for c in cands:
        if os.path.isfile(c):
            return c
    for n in EXE_NAMES:
        w = shutil.which(n)
        if w:
            return w
    return None


def probe_flags(exe: str, timeout: float = 15.0) -> Tuple[set, str]:
    """Run ``exe -h`` and return the single-letter flags it lists, plus the raw help text."""
    try:
        p = subprocess.run(_cmd(exe, ["-h"]), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise VoxyError("UPSCALER_FAILED", f"cannot run {exe} -h: {e}", stage="upscale") from e
    text = (p.stdout or "") + "\n" + (p.stderr or "")
    flags = set(re.findall(r"^\s*-([A-Za-z])\b", text, re.M))
    return flags, text


def _cmd(exe: str, args: List[str]) -> List[str]:
    if exe.lower().endswith(".py"):
        return [sys.executable, exe] + args      # lets tests use a Python stand-in
    return [exe] + args


def build_command(exe: str, inp: str, out: str, *, model: str, model_scale: int, scale: int, tile: int,
                  models_dir: Optional[str], gpu_id: Optional[int]) -> List[str]:
    args = ["-i", inp, "-o", out, "-z", str(model_scale), "-s", str(scale), "-n", model, "-t", str(tile), "-f", "png"]
    if models_dir:
        args += ["-m", models_dir]
    if gpu_id is not None:
        args += ["-g", str(gpu_id)]
    return _cmd(exe, args)


def plan_tiles(w: int, h: int, tile: int, overlap: int) -> List[Tuple[int, int, int, int]]:
    """Cover a w x h picture with tiles of at most ``tile`` px. Returns (x0, y0, x1, y1) boxes
    that overlap by ``overlap`` px on interior edges."""
    def spans(n: int) -> List[Tuple[int, int]]:
        if n <= tile:
            return [(0, n)]
        step = tile - 2 * overlap
        out, x = [], 0
        while True:
            x1 = min(n, x + tile)
            out.append((x, x1))
            if x1 >= n:
                break
            x += step
        return out
    return [(xa, ya, xb, yb) for (ya, yb) in spans(h) for (xa, xb) in spans(w)]


def stitch(tiles: List[Tuple[Tuple[int, int, int, int], np.ndarray]], w: int, h: int, scale: int, overlap: int) -> np.ndarray:
    """Paste upscaled tiles, keeping each tile's centre and dropping the overlap margins."""
    out = np.zeros((h * scale, w * scale, 4), np.uint8)
    for (x0, y0, x1, y1), arr in tiles:
        cx0 = 0 if x0 == 0 else overlap
        cy0 = 0 if y0 == 0 else overlap
        cx1 = (x1 - x0) if x1 == w else (x1 - x0) - overlap
        cy1 = (y1 - y0) if y1 == h else (y1 - y0) - overlap
        out[(y0 + cy0) * scale:(y0 + cy1) * scale, (x0 + cx0) * scale:(x0 + cx1) * scale] = \
            arr[cy0 * scale:cy1 * scale, cx0 * scale:cx1 * scale]
    return out


def _run(cmd: List[str], timeout_s: float, cancel: CancelToken) -> Tuple[int, str]:
    popen_kw: Dict[str, Any] = {}
    if sys.platform.startswith("win"):
        popen_kw["creationflags"] = 0x00000200 | 0x08000000      # new process group, no console window
    else:
        popen_kw["start_new_session"] = True
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, **popen_kw)

    def kill() -> None:
        try:
            if sys.platform.startswith("win"):
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
            else:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            try:
                p.kill()
            except Exception:  # noqa: BLE001
                pass

    cancel.on_cancel(kill)
    deadline = time.monotonic() + timeout_s
    out_chunks: List[str] = []
    import threading

    def reader() -> None:
        assert p.stdout is not None
        for line in p.stdout:
            out_chunks.append(line)
    t = threading.Thread(target=reader, daemon=True)
    t.start()
    while p.poll() is None:
        if cancel.cancelled:
            kill()
            p.wait()
            cancel.check("upscale")
        if time.monotonic() > deadline:
            kill()
            p.wait()
            raise VoxyError("UPSCALER_TIMEOUT", f"upscale exceeded {timeout_s}s", stage="upscale")
        time.sleep(0.05)
    t.join(2)
    cancel.check("upscale")        # the cancel hook may have killed the tool: that is a cancel, not a failure
    return p.returncode, "".join(out_chunks)[-4000:]


class Upscaler:
    def __init__(self, cfg: Dict[str, Any], app_dir: str):
        self.cfg = cfg
        self.app_dir = app_dir

    def _models_dir(self) -> Optional[str]:
        d = self.cfg.get("models_dir")
        if d:
            return d
        cand = os.path.join(self.app_dir, "tools", "upscayl", "models")
        return cand if os.path.isdir(cand) else None

    def check(self) -> Dict[str, Any]:
        """For preflight: is the tool there, does it accept the flags we use, is the model there."""
        exe = find_executable(self.cfg, self.app_dir)
        if not exe:
            return {"ok": False, "reason": "missing"}
        flags, text = probe_flags(exe)
        missing = sorted(REQUIRED_FLAGS - flags)
        md = self._models_dir()
        model_ok = bool(md) and all(os.path.isfile(os.path.join(md, self.cfg["model"] + ext)) for ext in (".param", ".bin"))
        return {"ok": not missing and model_ok, "exe": exe, "missing_flags": missing, "model_found": model_ok,
                "reason": "ok" if not missing and model_ok else ("flags" if missing else "model")}

    def upscale(self, input_png: str, out_dir: str, cancel: CancelToken, timeout_s: float) -> UpscaleResult:
        exe = find_executable(self.cfg, self.app_dir)
        if not exe:
            raise VoxyError("UPSCALER_MISSING", "upscayl executable not found", stage="upscale")
        flags, _ = probe_flags(exe)
        missing = REQUIRED_FLAGS - flags
        if missing:
            raise VoxyError("UPSCALER_FAILED", f"this upscayl build lacks flags: {sorted(missing)}", stage="upscale")
        if not os.path.isfile(input_png):
            raise VoxyError("IMAGE_UNREADABLE", f"missing {input_png}", stage="upscale")
        os.makedirs(out_dir, exist_ok=True)
        with Image.open(input_png) as im:
            im.load()
            rgba = np.array(im.convert("RGBA"))
        h, w = rgba.shape[:2]
        scale = int(self.cfg["scale"])
        stem = os.path.splitext(os.path.basename(input_png))[0]
        final = os.path.join(out_dir, f"{stem}_x{scale}.png")
        t0 = time.monotonic()
        attempts_total = 0
        used_tile = 0

        def left() -> float:
            return max(1.0, timeout_s - (time.monotonic() - t0))

        if w * h <= int(self.cfg["max_single_pass_px"]):
            used_tile, attempts = self._one(exe, input_png, final, cancel, left)
            attempts_total += attempts
        else:
            tile, ov = int(self.cfg["outer_tile"]), int(self.cfg["outer_overlap"])
            boxes = plan_tiles(w, h, tile, ov)
            done = []
            tmp = os.path.join(out_dir, "_tiles")
            os.makedirs(tmp, exist_ok=True)
            try:
                for i, (x0, y0, x1, y1) in enumerate(boxes):
                    cancel.check("upscale")
                    tin, tout = os.path.join(tmp, f"t{i}.png"), os.path.join(tmp, f"t{i}_out.png")
                    Image.fromarray(rgba[y0:y1, x0:x1], "RGBA").save(tin)
                    used_tile, attempts = self._one(exe, tin, tout, cancel, left)
                    attempts_total += attempts
                    with Image.open(tout) as o:
                        done.append(((x0, y0, x1, y1), np.array(o.convert("RGBA"))))
                Image.fromarray(stitch(done, w, h, scale, ov), "RGBA").save(final)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        with Image.open(final) as o:
            ow, oh = o.size
        if (ow, oh) != (w * scale, h * scale):
            raise VoxyError("UPSCALER_FAILED", f"unexpected output size {ow}x{oh} for {w}x{h} x{scale}", stage="upscale")
        return UpscaleResult(final, (w, h), (ow, oh), used_tile, attempts_total, self.cfg["model"])

    def _one(self, exe: str, inp: str, out: str, cancel: CancelToken, left) -> Tuple[int, int]:
        """Run one picture, retrying with smaller tiles after an out-of-memory failure."""
        seq = [int(t) for t in self.cfg["tile_sequence"]]
        last = ""
        for n, tile in enumerate(seq, start=1):
            cancel.check("upscale")
            cmd = build_command(exe, inp, out, model=self.cfg["model"], model_scale=int(self.cfg["model_scale"]),
                                scale=int(self.cfg["scale"]), tile=tile, models_dir=self._models_dir(),
                                gpu_id=self.cfg.get("gpu_id"))
            if os.path.exists(out):
                os.remove(out)
            code, text = _run(cmd, left(), cancel)
            last = text
            if code == 0 and os.path.isfile(out) and os.path.getsize(out) > 0:
                return tile, n
            if not OOM_PATTERNS.search(text):
                raise VoxyError("UPSCALER_FAILED", f"exit {code}: {text[-300:]}", stage="upscale")
        raise VoxyError("UPSCALER_OOM", f"out of memory at every tile size: {last[-200:]}", stage="upscale")
