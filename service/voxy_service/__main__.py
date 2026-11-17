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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="voxy-service", description="Voxy EasyAnimator local service (localhost only)")
    ap.add_argument("--config", help="YAML config file (default: configs/service.yaml next to the app, if present)")
    ap.add_argument("--port", type=int, default=None, help="port to listen on (0 = pick a free one)")
    ap.add_argument("--data-dir", default=None, help="where jobs, logs and settings are kept")
    ap.add_argument("--info-file", default=None, help="write {port, token, pid} here when ready")
    ap.add_argument("--parent-pid", type=int, default=0, help="exit when this process (After Effects) exits")
    ap.add_argument("--version", action="store_true")
    args = ap.parse_args(argv)
    if args.version:
        from . import __version__
        print(__version__)
        return 0

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
