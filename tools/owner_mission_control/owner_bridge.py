#!/usr/bin/env python3
"""Owner Mission Control read-only local telemetry server. Never launches agents."""
import argparse
import json
import pathlib
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = pathlib.Path(__file__).resolve().parent

def read_status():
    data = {"source": "local_psutil", "timestamp": datetime.now(timezone.utc).isoformat(),
            "cpu_percent": None, "ram_used_gb": None, "ram_total_gb": None,
            "gpu_percent": None, "tokens": None, "usd_cost": None,
            "agents": None, "tasks": None, "autonomy": None}
    try:
        import psutil
        data["cpu_percent"] = psutil.cpu_percent(interval=0.1)
        vm = psutil.virtual_memory()
        data["ram_used_gb"] = round(vm.used / 2**30, 2)
        data["ram_total_gb"] = round(vm.total / 2**30, 2)
    except ImportError:
        data["error"] = "Install psutil to enable CPU/RAM telemetry"
    return data

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            file = HERE / "index.html"
            if not file.is_file():
                self.send_error(404, "index.html missing")
                return
            body = file.read_bytes()
            mime = "text/html; charset=utf-8"
        elif path == "/api/owner/status":
            body = json.dumps(read_status()).encode()
            mime = "application/json; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, fmt, *args):
        pass

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args()
    # Loopback ONLY: no remote control, no LAN bind, no secrets.
    print(f"Bossman Owner Dashboard: http://127.0.0.1:{args.port}")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
