"""Start the local service:  python -m voxy_service [--config configs/service.yaml] [--port 0]."""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time

from .config import app_dir, default_data_dir, load_config
from .server import Service, make_server, write_info_file


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return True
    if sys.platform.startswith("win"):
        import ctypes
        SYNCHRONIZE = 0x00100000
        h = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not h:
            return False
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(h, 0) == 0x102     # WAIT_TIMEOUT: still running
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _check_mode(args) -> int:
    """Used by the installer: no window, no network, nothing left behind except the checks' output."""
    import json
    import tempfile
    import urllib.request

    root = app_dir()
    cfg_path = args.config or next((p for p in (os.path.join(root, "configs", "service.yaml"),) if os.path.isfile(p)), None)
    cfg = load_config(cfg_path)
    data_dir = args.data_dir or tempfile.mkdtemp(prefix="voxy-check-")
    svc = Service(cfg, data_dir, root)
    if args.preflight:
        res = svc.preflight()
        if args.plain:
            mark = {"ok": "OK   ", "warn": "NOTE ", "fail": "FIX  "}
            for c in res["checks"]:
                print(f"{mark[c['status']]}{c['message']}" + (f" {c['what_to_do']}" if c["status"] != "ok" and c["what_to_do"] else ""))
        else:
            print(json.dumps(res))
        return 1 if res["overall"] == "fail" else 0
    steps = []
    httpd = make_server(svc, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{svc.port}"
    ok = True
    try:
        h = json.load(urllib.request.urlopen(base + "/v1/health", timeout=10))
        steps.append({"step": "health", "ok": h.get("status") == "ok"})
        req = urllib.request.Request(base + "/v1/jobs?wait=1", data=json.dumps({"type": "make_layers", "sample": True}).encode(),
                                     headers={"Content-Type": "application/json", "X-Voxy-Token": svc.token})
        r = json.load(urllib.request.urlopen(req, timeout=300))
        m = r["manifest"]
        good = m["job"]["status"] in ("ok", "partial") and len(m["parts"]) > 0
        steps.append({"step": "sample", "ok": good, "parts": len(m["parts"]), "status": m["job"]["status"],
                      "error": (m["errors"][0]["user_message"] if m["errors"] else None)})
        ok = all(s["ok"] for s in steps)
    except Exception as e:  # noqa: BLE001 - the installer only needs ok / not ok and a short reason
        steps.append({"step": "error", "ok": False, "message": f"{type(e).__name__}: {e}"[:200]})
        ok = False
    httpd.shutdown()
    print(json.dumps({"ok": ok, "steps": steps}))
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="voxy-service", description="Voxy EasyAnimator local service (localhost only)")
    ap.add_argument("--config", help="YAML config file (default: configs/service.yaml next to the app, if present)")
    ap.add_argument("--port", type=int, default=None, help="port to listen on (0 = pick a free one)")
    ap.add_argument("--data-dir", default=None, help="where jobs, logs and settings are kept")
    ap.add_argument("--info-file", default=None, help="write {port, token, pid} here when ready")
    ap.add_argument("--parent-pid", type=int, default=0, help="exit when this process (After Effects) exits")
    ap.add_argument("--version", action="store_true")
    ap.add_argument("--preflight", action="store_true", help="print the setup checks (plain words) as JSON and exit")
    ap.add_argument("--plain", action="store_true", help="with --preflight: print one plain sentence per line instead of JSON")
    ap.add_argument("--selftest", action="store_true", help="start, answer a health check, cut the sample character, then exit 0/1")
    args = ap.parse_args(argv)
    if args.version:
        from . import __version__
        print(__version__)
        return 0

    if args.preflight or args.selftest:
        return _check_mode(args)
    root = app_dir()
    cfg_path = args.config or next((p for p in (os.path.join(root, "configs", "service.yaml"),) if os.path.isfile(p)), None)
    cfg = load_config(cfg_path)
    data_dir = args.data_dir or default_data_dir()
    svc = Service(cfg, data_dir, root)
    port = cfg["service"]["port"] if args.port is None else args.port
    httpd = make_server(svc, cfg["service"]["host"], port)
    info = args.info_file or os.path.join(data_dir, "service.json")
    write_info_file(info, svc)
    print(f"Voxy service ready on 127.0.0.1:{svc.port}", flush=True)

    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        while not svc.shutdown_event.is_set():
            time.sleep(0.5)
            if args.parent_pid and not pid_alive(args.parent_pid):
                break
    except KeyboardInterrupt:
        pass
    httpd.shutdown()
    svc.jobs.cleanup_tmp()
    try:
        os.remove(info)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
