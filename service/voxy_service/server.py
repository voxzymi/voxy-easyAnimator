"""Local HTTP interface (documented in docs/interface.md).

Bound to 127.0.0.1 only. Every call except /v1/health needs the session token
(``X-Voxy-Token``), which is written to ``service.json`` in the data folder. Nothing here
talks to the internet.
"""
from __future__ import annotations

import email
import email.policy
import json
import logging
import logging.handlers
import mimetypes
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from . import schema as S
from .config import app_dir as default_app_dir
from .errors import VoxyError, error_dict
from .jobs import JobManager
from .models.registry import ModelRegistry
from .preflight import check_disk, check_gpu, check_models, check_upscaler, summarize
from .upscale import Upscaler

log = logging.getLogger("voxy.server")

STATUS_FOR = {"BAD_REQUEST": 400, "UNAUTHORIZED": 401, "UNKNOWN_JOB": 404, "PARENT_JOB_MISSING": 404, "IMAGE_TOO_LARGE": 413}


def setup_logging(data_dir: str) -> str:
    os.makedirs(os.path.join(data_dir, "logs"), exist_ok=True)
    path = os.path.join(data_dir, "logs", "voxy-service.log")
    root = logging.getLogger("voxy")
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        h = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(h)
        root.setLevel(logging.INFO)
    return path


def parse_multipart(content_type: str, body: bytes) -> Dict[str, bytes]:
    msg = email.message_from_bytes(b"Content-Type: " + content_type.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + body,
                                   policy=email.policy.HTTP)
    out: Dict[str, bytes] = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if name:
            out[name] = part.get_payload(decode=True) or b""
    return out


class Service:
    """Everything the HTTP handler needs, so tests can build one without sockets."""

    def __init__(self, cfg: Dict[str, Any], data_dir: str, app_dir: Optional[str] = None, token: Optional[str] = None,
                 registry: Optional[ModelRegistry] = None, upscaler: Optional[Upscaler] = None):
        self.cfg = cfg
        self.data_dir = data_dir
        self.app_dir = app_dir or default_app_dir()
        os.makedirs(data_dir, exist_ok=True)
        self.log_path = setup_logging(data_dir)
        self.token = token or secrets.token_urlsafe(24)
        self.registry = registry or ModelRegistry(cfg, self.app_dir)
        self.upscaler = upscaler or Upscaler(cfg["upscale"], self.app_dir)
        self.jobs = JobManager(cfg, self.registry, self.app_dir, data_dir, self.upscaler)
        self.started = time.time()
        self.settings_path = os.path.join(data_dir, "settings.json")
        self.port = 0
        self.shutdown_event = threading.Event()

    # ---- payloads
    def health(self) -> Dict[str, Any]:
        return {"status": "ok", "service_version": __version__, "api_version": S.API_VERSION,
                "manifest_schema": S.MANIFEST_SCHEMA_VERSION, "models": self.registry.describe(),
                "device": self.registry.device(), "busy": self.jobs.busy(),
                "stub_models": any(v["backend"] == "stub" for k, v in self.registry.describe().items() if k in ("general", "hair")),
                "uptime_s": round(time.time() - self.started, 1), "log_path": self.log_path}

    def preflight(self) -> Dict[str, Any]:
        checks = check_gpu()
        checks.append(check_disk(self.data_dir, int(self.cfg["disk"]["required_free_mb"])))
        checks.append(check_models(self.registry.describe()))
        try:
            checks.append(check_upscaler(self.upscaler.check()))
        except VoxyError:
            checks.append(check_upscaler({"ok": False, "reason": "flags"}))
        return summarize(checks)

    def get_settings(self) -> Dict[str, Any]:
        try:
            with open(self.settings_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def put_settings(self, data: Dict[str, Any]) -> None:
        tmp = self.settings_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, self.settings_path)


def make_handler(svc: Service):
    class Handler(BaseHTTPRequestHandler):
        server_version = "VoxyService/" + __version__
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:     # keep request lines out of the console
            log.debug("%s " + fmt, self.address_string(), *args)

        # ---- plumbing
        def _send(self, status: int, payload: Any, ctype: str = "application/json") -> None:
            body = payload if isinstance(payload, (bytes, bytearray)) else json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Voxy-Token")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _error(self, code: str, message: str = "") -> None:
            self._send(STATUS_FOR.get(code, 500 if code == "INTERNAL_ERROR" else 400), {"error": error_dict(code, message)})

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0].lower()
            return host in ("127.0.0.1", "localhost", "[::1]", "")

        def _authed(self, query: Optional[Dict[str, Any]] = None, allow_query: bool = False) -> bool:
            tok = self.headers.get("X-Voxy-Token") or ""
            if not tok and allow_query and query:                     # <img> tags cannot send headers: picture URLs carry the token
                tok = (query.get("token") or [""])[0]
            if not tok and (self.headers.get("Authorization") or "").startswith("Bearer "):
                tok = self.headers["Authorization"][7:]
            return secrets.compare_digest(tok, svc.token)

        def _body(self) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            if n > int(svc.cfg["service"]["max_request_mb"]) * 1024 * 1024:
                raise VoxyError("IMAGE_TOO_LARGE", "request too large")
            return self.rfile.read(n) if n else b""

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._send(204, b"")

        def _route(self, method: str) -> None:
            if not self._host_ok():
                return self._error("UNAUTHORIZED", "bad host header")
            u = urlparse(self.path)
            path, q = u.path.rstrip("/"), parse_qs(u.query)
            try:
                if method == "GET" and path == "/v1/health":
                    return self._send(200, svc.health())
                is_file = method == "GET" and "/files/" in path
                if not self._authed(q, allow_query=is_file):
                    return self._error("UNAUTHORIZED", "missing or wrong token")
                if method == "GET" and path == "/v1/preflight":
                    return self._send(200, svc.preflight())
                if path == "/v1/settings":
                    if method == "GET":
                        return self._send(200, svc.get_settings())
                    data = json.loads(self._body() or b"{}")
                    svc.put_settings(data)
                    return self._send(200, {"ok": True})
                if method == "POST" and path == "/v1/tmp/cleanup":
                    return self._send(200, {"removed": svc.jobs.cleanup_tmp()})
                if method == "POST" and path == "/v1/shutdown":
                    self._send(200, {"ok": True})
                    svc.shutdown_event.set()
                    return
                if method == "POST" and path == "/v1/jobs":
                    return self._submit(q)
                m = re.match(r"^/v1/jobs/([A-Za-z0-9]+)(?:/(cancel|discard|files/.+))?$", path)
                if m:
                    return self._job(method, m.group(1), m.group(2), q)
                return self._send(404, {"error": error_dict("BAD_REQUEST", f"no route {method} {path}")})
            except VoxyError as e:
                self._send(STATUS_FOR.get(e.code, 400), {"error": e.to_dict()})
            except json.JSONDecodeError:
                self._error("BAD_REQUEST", "invalid JSON")
            except BaseException as e:  # noqa: BLE001
                log.exception("unhandled error in %s %s", method, path)
                self._error("INTERNAL_ERROR", f"{type(e).__name__}: {e}")

        def do_GET(self) -> None:  # noqa: N802
            self._route("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._route("POST")

        def do_PUT(self) -> None:  # noqa: N802
            self._route("PUT")

        # ---- jobs
        def _submit(self, q: Dict[str, Any]) -> None:
            ctype = self.headers.get("Content-Type", "application/json")
            body = self._body()
            if ctype.startswith("multipart/form-data"):
                parts = parse_multipart(ctype, body)
                req = json.loads(parts.get("request", b"{}") or b"{}")
                if "image" in parts:
                    req["image_bytes"] = parts["image"]
            else:
                req = json.loads(body or b"{}")
            kind = req.get("type")
            if not isinstance(kind, str):
                raise VoxyError("BAD_REQUEST", "request needs a 'type'")
            job = svc.jobs.submit(kind, req)
            if q.get("wait", ["0"])[0] in ("1", "true"):
                svc.jobs.wait(job.id, float(q.get("timeout", ["600"])[0]))
                return self._send(200, job.public())
            self._send(202, job.public(with_manifest=False))

        def _job(self, method: str, job_id: str, action: Optional[str], q: Dict[str, Any]) -> None:
            if action and action.startswith("files/"):
                return self._file(job_id, unquote(action[6:]))
            if method == "POST" and action == "cancel":
                if not svc.jobs.cancel(job_id):
                    return self._error("UNKNOWN_JOB")
                return self._send(200, {"ok": True})
            if method == "POST" and action == "discard":
                return self._send(200, {"ok": svc.jobs.discard(job_id)})
            job = svc.jobs.get(job_id)
            if job is None:
                return self._error("UNKNOWN_JOB")
            if q.get("wait", ["0"])[0] in ("1", "true"):
                svc.jobs.wait(job_id, float(q.get("timeout", ["60"])[0]))
            self._send(200, job.public())

        def _file(self, job_id: str, rel: str) -> None:
            base = svc.jobs.job_dir(job_id)
            if not base:
                return self._error("UNKNOWN_JOB")
            full = os.path.realpath(os.path.join(base, rel))
            if not full.startswith(os.path.realpath(base) + os.sep) or not os.path.isfile(full):
                return self._error("BAD_REQUEST", "no such file")
            with open(full, "rb") as f:
                data = f.read()
            self._send(200, data, mimetypes.guess_type(full)[0] or "application/octet-stream")

    return Handler


def make_server(svc: Service, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), make_handler(svc))
    httpd.daemon_threads = True
    svc.port = httpd.server_address[1]
    return httpd


def write_info_file(path: str, svc: Service) -> None:
    """Tell the host where the service is (port, token). Readable only by the current user where the OS allows."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"port": svc.port, "token": svc.token, "pid": os.getpid(), "version": __version__,
                   "log_path": svc.log_path, "data_dir": svc.data_dir}, f)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)
