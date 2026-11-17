"""Labeling tool: correct predicted masks, key points and groups on real reference images.

    python -m training.shared.label_tool.server --images data/raw --out data/labeled --port 8765

Two label sets, kept in separate folders and never mixed:

  <out>/general/<image>/   labels.png (one class per pixel; hair is ONE class), keypoints.json, meta.json
  <out>/hair/<image>/      subdivisions.png (strand sections inside the hair mask), roots.json, hair_mask.png, meta.json

The group of a part is derived from its class (docs/manifest.md), so correcting a class corrects its group.
Pre-labels written by ``bootstrap predict`` are loaded from <out>/prelabels/{general,hair}/<image>/ when no saved
labels exist yet. The server listens on 127.0.0.1 only and sends nothing anywhere.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, unquote, urlparse

import numpy as np
from PIL import Image

import training  # noqa: F401
from voxy_service import schema as S

STATIC = os.path.join(os.path.dirname(__file__), "static")
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")
MAX_SUBDIVISIONS = 24
NAME_RE = re.compile(r"^[A-Za-z0-9._\- ]+$")


def stem_of(name: str) -> str:
    return os.path.splitext(name)[0]


class LabelStore:
    """All the file handling, separate from HTTP so it can be tested directly."""

    def __init__(self, images_dir: str, out_dir: str):
        self.images = os.path.abspath(images_dir)
        self.out = os.path.abspath(out_dir)

    # ---- images
    def list_images(self) -> list:
        res = []
        for root, _, files in os.walk(self.images):
            for fn in sorted(files):
                if fn.lower().endswith(IMG_EXT):
                    rel = os.path.relpath(os.path.join(root, fn), self.images).replace(os.sep, "/")
                    res.append({"name": rel, "general": os.path.isfile(self.path("general", rel, "labels.png")),
                                "hair": os.path.isfile(self.path("hair", rel, "subdivisions.png"))})
        return res

    def image_path(self, name: str) -> str:
        full = os.path.realpath(os.path.join(self.images, name))
        if not full.startswith(self.images + os.sep) or not os.path.isfile(full):
            raise FileNotFoundError(name)
        return full

    def size_of(self, name: str):
        with Image.open(self.image_path(name)) as im:
            return im.size

    def path(self, kind: str, name: str, fn: str, pre: bool = False) -> str:
        base = os.path.join(self.out, "prelabels" if pre else "", kind, stem_of(name).replace("/", "__"))
        return os.path.join(base, fn)

    # ---- state
    def load(self, kind: str, name: str) -> Dict[str, Any]:
        w, h = self.size_of(name)
        files = {"general": ("labels.png", "keypoints.json"), "hair": ("subdivisions.png", "roots.json")}[kind]
        for pre in (False, True):
            lp = self.path(kind, name, files[0], pre)
            if os.path.isfile(lp):
                arr = np.array(Image.open(lp).convert("L"))
                kp = []
                kpath = self.path(kind, name, files[1], pre)
                if os.path.isfile(kpath):
                    kp = json.load(open(kpath))
                out = {"width": w, "height": h, "saved": not pre, "prelabel": pre, "labels": base64.b64encode(arr.tobytes()).decode(),
                       "points": kp}
                if kind == "hair":
                    hm = self.path("hair", name, "hair_mask.png", pre)
                    if os.path.isfile(hm):
                        out["hair_mask"] = base64.b64encode((np.array(Image.open(hm).convert("L")) > 0).astype(np.uint8).tobytes()).decode()
                return out
        return {"width": w, "height": h, "saved": False, "prelabel": False, "labels": None, "points": []}

    def save(self, kind: str, name: str, data: Dict[str, Any]) -> str:
        w, h = self.size_of(name)
        if int(data["width"]) != w or int(data["height"]) != h:
            raise ValueError("label size does not match the image")
        arr = np.frombuffer(base64.b64decode(data["labels"]), np.uint8)
        if arr.size != w * h:
            raise ValueError("label data has the wrong length")
        arr = arr.reshape(h, w)
        d = os.path.dirname(self.path(kind, name, "x"))
        meta = {"image": name, "tool": "voxy-label-tool-1", "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "width": w, "height": h}
        # validate everything first so a rejected save leaves nothing behind
        if kind == "general":
            if int(arr.max()) >= S.NUM_CLASSES:
                raise ValueError("class id out of range")
            pts = []
            for p in data.get("points", []):
                if p["name"] not in S.KEYPOINT_INDEX:
                    raise ValueError(f"unknown key point {p['name']}")
                pts.append({"name": p["name"], "x": float(p["x"]), "y": float(p["y"]), "visible": bool(p.get("visible", True))})
        elif kind == "hair":
            if int(arr.max()) > MAX_SUBDIVISIONS:
                raise ValueError("too many subdivisions")
            hm = np.frombuffer(base64.b64decode(data["hair_mask"]), np.uint8).reshape(h, w) > 0
            if (arr[~hm] != 0).any():
                raise ValueError("subdivisions must lie inside the hair mask")
        else:
            raise ValueError("kind must be general or hair")
        os.makedirs(d, exist_ok=True)
        if kind == "general":
            Image.fromarray(arr, "L").save(os.path.join(d, "labels.png"))
            json.dump(pts, open(os.path.join(d, "keypoints.json"), "w"), indent=1)
            meta["classes"] = S.CLASS_NAMES
            meta["groups"] = {c: S.GROUP_OF_CLASS[c] for c in S.CLASS_NAMES[1:]}
        else:
            Image.fromarray(arr, "L").save(os.path.join(d, "subdivisions.png"))
            Image.fromarray((hm * 255).astype(np.uint8), "L").save(os.path.join(d, "hair_mask.png"))
            roots = [[float(p["x"]), float(p["y"])] for p in data.get("points", [])]
            json.dump(roots, open(os.path.join(d, "roots.json"), "w"))
            meta["sections"] = int(arr.max())
        json.dump(meta, open(os.path.join(d, "meta.json"), "w"), indent=1)
        return d


def make_handler(store: LabelStore):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _send(self, code: int, body: Any, ctype: str = "application/json") -> None:
            data = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            u = urlparse(self.path)
            try:
                if u.path in ("/", "/index.html"):
                    return self._send(200, open(os.path.join(STATIC, "index.html"), "rb").read(), "text/html; charset=utf-8")
                if u.path == "/api/schema":
                    return self._send(200, {"classes": S.CLASS_NAMES, "groups": {c: S.GROUP_OF_CLASS[c] for c in S.CLASS_NAMES[1:]},
                                            "group_order": S.GROUP_ORDER, "keypoints": S.KEYPOINTS, "max_subdivisions": MAX_SUBDIVISIONS})
                if u.path == "/api/images":
                    return self._send(200, store.list_images())
                if u.path.startswith("/api/image/"):
                    return self._send(200, open(store.image_path(unquote(u.path[11:])), "rb").read(), "image/png")
                if u.path.startswith("/api/state/"):
                    kind = parse_qs(u.query).get("kind", ["general"])[0]
                    return self._send(200, store.load(kind, unquote(u.path[11:])))
                self._send(404, {"error": "not found"})
            except FileNotFoundError:
                self._send(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            u = urlparse(self.path)
            try:
                if u.path.startswith("/api/save/"):
                    kind = parse_qs(u.query).get("kind", ["general"])[0]
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                    d = store.save(kind, unquote(u.path[10:]), body)
                    return self._send(200, {"ok": True, "dir": d})
                self._send(404, {"error": "not found"})
            except (ValueError, KeyError) as e:
                self._send(400, {"error": str(e)})
            except FileNotFoundError:
                self._send(404, {"error": "not found"})
    return H


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--images", default="data/raw")
    ap.add_argument("--out", default="data/labeled")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args(argv)
    httpd = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(LabelStore(a.images, a.out)))
    print(f"Labeling tool on http://127.0.0.1:{httpd.server_address[1]}  (images: {a.images}, labels: {a.out})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
