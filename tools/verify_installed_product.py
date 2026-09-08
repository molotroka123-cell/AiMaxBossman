#!/usr/bin/env python3
"""Exercise an installed Bossman process over HTTP, from outside its source tree.

No model is mocked or invoked. This proves install/boot/auth/assets/background
loops/persistence; it does not claim live provider or agent-execution acceptance.
Use the clean installation's Python to execute this script.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import importlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


def verify(work: Path) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    prefix = Path(sys.prefix).resolve()
    imports = {}
    for name in ("bossman_shared", "bossman", "bcc"):
        module = importlib.import_module(name)
        path = Path(module.__file__).resolve()
        if not path.is_relative_to(prefix):
            raise AssertionError(f"{name} was imported outside clean environment: {path}")
        imports[name] = str(path)
    from bcc.config import Settings
    defaults = Settings()
    assert defaults.ui_dir.is_relative_to(prefix), defaults.ui_dir
    assert (defaults.ui_dir / "index.html").is_file(), defaults.ui_dir
    assert not defaults.data_dir.is_relative_to(prefix), defaults.data_dir
    bindir = prefix / ("Scripts" if os.name == "nt" else "bin")
    for entry in ("bossman", "bossman-gateway", "bcc", "bcc-desktop", "bcc-open"):
        path = bindir / (entry + (".exe" if os.name == "nt" else ""))
        assert path.is_file(), path
        subprocess.run([str(path), "--help"], cwd=work, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ)
    for key in ("PYTHONPATH", "BCC_UI_DIR", "DATABASE_URL"):
        env.pop(key, None)
    env.update(BCC_DATA_DIR=str(work / "data"), BCC_TOKEN_STDOUT="0", PYTHONUNBUFFERED="1")
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    csrf = None

    def request(path, method="GET", payload=None, expected=200, token=None):
        headers = {}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["X-BCC-Token"] = token
        if csrf:
            headers["X-BCC-CSRF"] = csrf
        req = urllib.request.Request(base + path, data=(json.dumps(payload).encode()
            if payload is not None else None), method=method, headers=headers)
        try:
            response = client.open(req, timeout=15)
        except urllib.error.HTTPError as exc:
            response = exc
        body = response.read()
        assert response.status == expected, (path, response.status, body[:500])
        return json.loads(body) if "application/json" in response.headers.get("Content-Type", "") else body

    def start():
        log = (work / "server.log").open("ab")
        process = subprocess.Popen([sys.executable, "-m", "bcc", "--host", "127.0.0.1",
            "--port", str(port)], cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if process.poll() is not None:
                log.close()
                raise AssertionError(f"installed server exited: {(work / 'server.log').read_text()[-5000:]}")
            try:
                identity = request("/api/identity")
                assert identity["app"] == "bossman-command-center", identity
                return process, log
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.1)
        process.terminate()
        process.wait(timeout=20)
        log.close()
        raise AssertionError("installed BCC did not start within 60s")

    def stop(process, log):
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
            raise AssertionError("installed BCC did not stop within 20s")
        finally:
            log.close()

    process, log = start()
    try:
        assert b"<html" in request("/").lower()
        request("/api/system", expected=401)
        token = (work / "data" / "token").read_text().strip()
        request("/api/login", method="POST", payload={"token": "invalid-install-probe"}, expected=401)
        login = request("/api/login", method="POST", payload={"token": token})
        csrf = login["csrf"]
        # Honor the actual server's contract rather than a guessed header.
        assert login["csrf_header"] == "X-BCC-CSRF", login["csrf_header"]
        deadline = time.monotonic() + 20
        while True:
            system = request("/api/system")
            health = system["health"]
            required = ("db", "queue_worker", "scheduler", "metrics")
            if all(health[name]["status"] == "ok" for name in required):
                break
            assert time.monotonic() < deadline, health
            time.sleep(0.1)
        assets = [p for p in defaults.ui_dir.rglob("*") if p.is_file()]
        for asset in assets:
            served = request("/" + asset.relative_to(defaults.ui_dir).as_posix())
            expected_bytes = asset.read_bytes()
            if isinstance(served, bytes):
                assert served == expected_bytes, asset
            else:
                assert served == json.loads(expected_bytes), asset
        agent = request("/api/agents", method="POST", payload={
            "name": "installed-persistence-probe", "enabled": False})
        agent_id = agent["id"]
        assert (work / "data" / "bcc.db").is_file()
    finally:
        stop(process, log)
    process, log = start()
    try:
        # Cookie session, persistent token and the actual DB row survive restart.
        assert (work / "data" / "token").read_text().strip() == token
        agents = request("/api/agents")
        assert any(a["id"] == agent_id and a["name"] == "installed-persistence-probe"
                   and not a["enabled"] for a in agents), agents
        assert b"<html" in request("/").lower()
        request(f"/api/agents/{agent_id}", method="DELETE")
    finally:
        stop(process, log)
    return {"status": "PASS", "python": sys.version.split()[0], "platform": sys.platform,
        "imports": imports, "ui_dir": str(defaults.ui_dir), "assets_served": len(assets),
        "health": health, "restart_persistence": "PASS", "auth": "PASS",
        "live_model_execution": "OWNER_LIVE_REQUIRED"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.workdir:
        result = verify(args.workdir.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix="bossman-installed-") as work:
            result = verify(Path(work))
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
