#!/usr/bin/env python3
"""A stand-in for the ML service used by the supervisor test: writes the info file and answers /v1/health."""
import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

ap = argparse.ArgumentParser()
ap.add_argument("--parent-pid", type=int, default=0)
ap.add_argument("--data-dir")
ap.add_argument("--info-file")
ap.add_argument("--die", action="store_true")
ap.add_argument("--slow", type=float, default=0)
a = ap.parse_args()
if a.die:
    sys.exit(3)
time.sleep(a.slow)


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def log_message(self, *x):
        pass


srv = HTTPServer(("127.0.0.1", 0), H)
json.dump({"port": srv.server_address[1], "token": "tok", "pid": os.getpid()}, open(a.info_file, "w"))
threading.Thread(target=srv.serve_forever, daemon=True).start()
while True:
    time.sleep(0.2)
    if a.parent_pid:
        try:
            os.kill(a.parent_pid, 0)
        except OSError:
            break
