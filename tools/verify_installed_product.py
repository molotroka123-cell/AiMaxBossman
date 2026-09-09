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
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


def verified_source(manifest: Path, expected_sha: str | None = None) -> str:
    source = json.loads(manifest.read_text(encoding="utf-8"))
    assert isinstance(source, dict), "Installed source attestation must be an object"
    source_sha = source.get("source_sha")
    assert isinstance(source_sha, str) and re.fullmatch(r"[0-9a-f]{40}", source_sha), source
    assert source.get("source_dirty") is False, "Installed source is dirty or unmeasured"
    if expected_sha:
        assert source_sha == expected_sha, (source_sha, expected_sha)
    return source_sha


def verify(work: Path, expected_sha: str | None = None) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    prefix = Path(sys.prefix).resolve()
    imports = {}
    for name in ("bossman_shared", "bossman", "bcc", "ai_3d_maker", "ai_webcam_vision",
                 "bossman_accountant", "exam_trainer_ai", "file_commander_mini",
                 "pc_autopilot_mini", "social_farm", "travel_architect"):
        module = importlib.import_module(name)
        path = Path(module.__file__).resolve()
        if not path.is_relative_to(prefix):
            raise AssertionError(f"{name} was imported outside clean environment: {path}")
        imports[name] = str(path)
    from ai_3d_maker.config import DEFAULT_PROFILE, DEFAULT_MATERIALS
    from ai_3d_maker.profile import PrinterProfile, load_material_defaults
    assert DEFAULT_PROFILE.is_relative_to(prefix) and DEFAULT_MATERIALS.is_relative_to(prefix)
    printer = PrinterProfile.load(DEFAULT_PROFILE)
    assert printer.model and load_material_defaults(DEFAULT_MATERIALS)
    from social_farm.media.profiles import load_bundle
    media_profile = Path(imports["social_farm"]).parent / "media" / "profiles" / "instagram.v1.json"
    assert load_bundle(media_profile).provider == "instagram"
    source_sha = verified_source(Path(imports["bcc"]).parent / "_build.json", expected_sha)
    from bcc.config import Settings
    defaults = Settings()
    assert defaults.ui_dir.is_relative_to(prefix), defaults.ui_dir
    assert (defaults.ui_dir / "index.html").is_file(), defaults.ui_dir
    assert not defaults.data_dir.is_relative_to(prefix), defaults.data_dir
    bindir = prefix / ("Scripts" if os.name == "nt" else "bin")
    for entry in ("bossman", "bossman-gateway", "bcc", "bcc-desktop", "bcc-open"):
        # A venv installation writes .exe wrappers; the downloadable archive
        # installs into an embeddable runtime and writes .cmd shims that resolve
        # the interpreter beside them. Either way the command must EXIST and RUN
        # here — only the wrapper format differs, never the requirement.
        suffixes = (".exe", ".cmd") if os.name == "nt" else ("",)
        candidates = [bindir / (entry + suffix) for suffix in suffixes]
        path = next((p for p in candidates if p.is_file()), None)
        assert path is not None, candidates
        argv = [str(path), "--help"]
        if path.suffix == ".cmd":
            argv = [os.environ.get("COMSPEC", "cmd.exe"), "/c", *argv]
        result = subprocess.run(argv, cwd=work,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        if result.returncode:
            # Keep the actual installed CLI failure. CalledProcessError alone
            # hid a Windows console encoding crash behind a bare exit code.
            detail = (result.stderr or result.stdout).decode("utf-8", "replace")
            raise AssertionError(f"Installed {entry} --help exited {result.returncode}:\n{detail[-6000:]}")
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
            required = ("db", "queue_worker", "scheduler", "metrics",
                        *(name for name in health if name.startswith("tick:")))
            if all(health[name]["status"] == "ok" for name in required):
                break
            assert time.monotonic() < deadline, health
            time.sleep(0.1)
        # File Intelligence is off by default and must stay off in a clean
        # install. Its doctor answers anyway, and it must name the pinned
        # integration: the manifest travels in the wheel, not in a checkout the
        # installed product does not have.
        file_intelligence = request("/api/file-intelligence/status")
        assert file_intelligence["enabled"] is False, file_intelligence
        assert re.fullmatch(r"[0-9a-f]{40}", file_intelligence["pinned_upstream_sha"] or ""), \
            f"installed build reports no pinned integration: {file_intelligence}"
        assert file_intelligence["binary"]["status"] == "NOT_INSTALLED", file_intelligence
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
    return {"status": "PASS", "source_sha": source_sha, "python": sys.version.split()[0], "platform": sys.platform,
        "imports": imports, "ui_dir": str(defaults.ui_dir), "assets_served": len(assets),
        "health": health, "restart_persistence": "PASS", "auth": "PASS",
        "app_package_resources": "PASS",
        "file_intelligence": {"enabled": file_intelligence["enabled"],
                              "pinned_upstream_sha": file_intelligence["pinned_upstream_sha"],
                              "binary": file_intelligence["binary"]["status"],
                              "real_binary": "OWNER_LIVE_REQUIRED"},
        "live_model_execution": "OWNER_LIVE_REQUIRED"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--expected-sha")
    args = parser.parse_args()
    if args.workdir:
        result = verify(args.workdir.resolve(), args.expected_sha)
    else:
        with tempfile.TemporaryDirectory(prefix="bossman-installed-") as work:
            result = verify(Path(work), args.expected_sha)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    # Windows redirected stdout commonly uses cp1252. Keep the persisted
    # evidence UTF-8 while making the same JSON safe for any ASCII console.
    print(json.dumps(result, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
