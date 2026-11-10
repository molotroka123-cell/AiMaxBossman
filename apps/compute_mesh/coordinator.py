"""Compute mesh coordinator (runs on the main Bossman PC). Other PCs of the owner join as workers and lend compute.

Design choices that are also the safety rules:
  - workers connect OUT to the coordinator; a worker opens no listening port on the second PC;
  - every request carries the pairing token made by make_drive (constant-time compare); unknown token = 401;
  - the job vocabulary is a fixed whitelist (ping, ollama_generate). There is no "run command" job and none may be added
    without a code change on both sides: the worker rejects anything it does not know;
  - plain HTTP, so this is for the home LAN of the owner only. Do not expose the port to the internet.

    python -m apps.compute_mesh.coordinator --host 0.0.0.0 --port 8850 --token-file mesh.token
"""
from __future__ import annotations

import argparse
import hmac
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

JOB_TYPES = ("ping", "ollama_generate")


class Mesh:
    def __init__(self, token: str):
        if len(token) < 32:
            raise ValueError("pairing token must be at least 32 characters")
        self.token = token
        self.lock = threading.Lock()
        self.workers: dict[str, dict] = {}
        self.jobs: dict[str, dict] = {}

    def authorised(self, header: str | None) -> bool:
        return bool(header) and hmac.compare_digest(str(header), "Bearer " + self.token)

    def register(self, info: dict) -> str:
        wid = str(info.get("worker_id") or uuid.uuid4().hex)
        keep = {k: info.get(k) for k in ("hostname", "cpu_count", "ram_gb", "models")}
        with self.lock:
            self.workers[wid] = {"info": keep, "seen": time.time()}
        return wid

    def submit(self, typ: str, payload: dict) -> str:
        if typ not in JOB_TYPES:
            raise ValueError(f"job type must be one of {JOB_TYPES}")
        jid = uuid.uuid4().hex
        with self.lock:
            self.jobs[jid] = {"id": jid, "type": typ, "payload": payload, "state": "queued", "worker": None, "result": None}
        return jid

    def take(self, wid: str) -> dict | None:
        with self.lock:
            if wid not in self.workers:
                return None
            self.workers[wid]["seen"] = time.time()
            for job in self.jobs.values():
                if job["state"] == "queued":
                    job["state"], job["worker"] = "running", wid
                    return {"id": job["id"], "type": job["type"], "payload": job["payload"]}
        return None

    def finish(self, wid: str, jid: str, ok: bool, result) -> bool:
        with self.lock:
            job = self.jobs.get(jid)
            if not job or job["worker"] != wid or job["state"] != "running":
                return False
            job["state"], job["result"] = ("done" if ok else "failed"), result
            return True

    def status(self) -> dict:
        with self.lock:
            now = time.time()
            workers = {w: dict(v["info"], online=now - v["seen"] < 30) for w, v in self.workers.items()}
            jobs = {j["id"]: {"type": j["type"], "state": j["state"], "worker": j["worker"]} for j in self.jobs.values()}
            return {"workers": workers, "jobs": jobs}


def make_handler(mesh: Mesh):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code: int, body: dict):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 8_000_000:
                raise ValueError("body too large")
            return json.loads(self.rfile.read(n) or b"{}")

        def do_GET(self):
            if not mesh.authorised(self.headers.get("Authorization")):
                return self._send(401, {"error": "unauthorised"})
            if self.path == "/status":
                return self._send(200, mesh.status())
            if self.path.startswith("/jobs/"):
                job = mesh.jobs.get(self.path.split("/")[2])
                return self._send(200, job) if job else self._send(404, {"error": "no such job"})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not mesh.authorised(self.headers.get("Authorization")):
                return self._send(401, {"error": "unauthorised"})
            try:
                body = self._body()
                if self.path == "/register":
                    return self._send(200, {"worker_id": mesh.register(body)})
                if self.path == "/take":
                    return self._send(200, {"job": mesh.take(str(body.get("worker_id")))})
                if self.path == "/finish":
                    ok = mesh.finish(str(body.get("worker_id")), str(body.get("job_id")), bool(body.get("ok")), body.get("result"))
                    return self._send(200 if ok else 409, {"accepted": ok})
                if self.path == "/submit":
                    return self._send(200, {"job_id": mesh.submit(str(body.get("type")), body.get("payload") or {})})
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            self._send(404, {"error": "not found"})

    return Handler


def serve(mesh: Mesh, host: str, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(mesh))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8850)
    ap.add_argument("--token-file", required=True)
    a = ap.parse_args()
    srv = serve(Mesh(open(a.token_file, encoding="utf-8").read().strip()), a.host, a.port)
    print(f"mesh coordinator on {a.host}:{a.port}; workers join with the drive made by make_drive")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.shutdown()
