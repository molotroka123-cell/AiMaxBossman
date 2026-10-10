"""One Bossman on the owner's machine: backup, launch, repoint, agent fixes, status.

Stdlib only. Runs under the bundled runtime (``runtime\\python.exe -I``) or any
Python 3.11+. The Windows orchestration (install, scheduled tasks, shortcuts,
rollback) lives in ``tools/owner_one_bossman.ps1``; this file holds the parts
that must be exact and testable:

  backup   --src DATA --dest DIR      consistent copy of a data root: SQLite files through
                                      the online backup API, everything else byte-copied,
                                      SHA-256 manifest (MANIFEST.sha256) + backup.json
  verify   --dir DIR                  recompute every hash of a backup/restore against its manifest
  restore  --backup DIR --dest DIR    copy a backup into an EMPTY folder, prove every hash and
                                      run PRAGMA integrity_check on every SQLite file
  launch   backend|jeff|companion|supervisor ...
                                      start ONE process hidden (own console, so Ctrl+C can stop it
                                      cleanly) unless the kernel lock says it already runs
  repoint  --data-dir D --port P      core_url of the Jeff (PIT) and «Пульт» configs -> the backend
  agents   --port P --data-dir D --plan FILE --evidence DIR
                                      PATCH agents through the backend API, before/after JSON
  status   --data-dir D               one backend / one poller per bot token, as JSON

Secrets (bot tokens, the access token) are read only to hash them or to send
them in a request header; they are never printed or written anywhere.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable

SQLITE_MAGIC = b"SQLite format 3\x00"
MANIFEST = "MANIFEST.sha256"
META = "backup.json"
# Kernel byte-range lock files: their content is meaningless and reading a
# locked byte fails on Windows. SQLite side files are folded into the backup.
SKIP_SUFFIXES = (".lock", "-wal", "-shm", "-journal")
STATE_DIR = "one-bossman"          # <data>/one-bossman: pid files and launcher logs
PIT_HOME = "pit-v1.7"
TTS_KEYS = ("BOSSMAN_PIT_TTS_EXECUTABLE", "BOSSMAN_PIT_TTS_MODEL_PATH")


# --------------------------------------------------------------------------- hashing

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_sqlite(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(16) == SQLITE_MAGIC
    except OSError:
        return False


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _walk(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            yield Path(dirpath) / name


def read_manifest(folder: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (folder / MANIFEST).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, rel = line.split("  ", 1)
        out[rel] = digest
    return out


def write_manifest(folder: Path, entries: dict[str, str]) -> str:
    text = "".join(f"{entries[rel]}  {rel}\n" for rel in sorted(entries))
    (folder / MANIFEST).write_text(text, encoding="utf-8", newline="\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- backup

def _sqlite_backup(src: Path, dst: Path) -> None:
    """Consistent snapshot of a live SQLite file (WAL included) through the backup API."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        source = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True, timeout=30)
        source.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlite3.Error:
        source = sqlite3.connect(str(src), timeout=30)
    try:
        target = sqlite3.connect(str(dst))
        try:
            source.backup(target)
            # A standalone file: no -wal beside it, readable as one file.
            target.execute("PRAGMA journal_mode=DELETE")
        finally:
            target.close()
    finally:
        source.close()


def backup(src: Path, dest: Path) -> dict[str, Any]:
    src, dest = Path(src).resolve(), Path(dest).resolve()
    if not src.is_dir():
        raise SystemExit(f"source {src} is not a folder")
    if dest.exists() and any(dest.iterdir()):
        raise SystemExit(f"destination {dest} is not empty")
    if dest == src or src in dest.parents:
        raise SystemExit("destination must be outside the source")
    dest.mkdir(parents=True, exist_ok=True)
    entries: dict[str, str] = {}
    skipped: list[dict[str, str]] = []
    sqlite_files: list[str] = []
    total = 0
    started = time.time()
    for path in _walk(src):
        rel = _rel(path, src)
        if path.name.endswith(SKIP_SUFFIXES):
            skipped.append({"path": rel, "why": "lock/sqlite side file"})
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if is_sqlite(path):
                _sqlite_backup(path, target)
                sqlite_files.append(rel)
            else:
                shutil.copy2(path, target)
        except (OSError, sqlite3.Error) as exc:
            skipped.append({"path": rel, "why": f"{type(exc).__name__}: {str(exc)[:120]}"})
            with contextlib.suppress(OSError):
                target.unlink()
            continue
        entries[rel] = sha256_file(target)
        total += target.stat().st_size
    manifest_sha = write_manifest(dest, entries)
    meta = {
        "source": str(src), "destination": str(dest),
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "seconds": round(time.time() - started, 1), "files": len(entries), "bytes": total,
        "sqlite_via_backup_api": sqlite_files, "skipped": skipped,
        "manifest": MANIFEST, "manifest_sha256": manifest_sha,
    }
    (dest / META).write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def verify(folder: Path) -> dict[str, Any]:
    folder = Path(folder)
    manifest = read_manifest(folder)
    missing, differ = [], []
    for rel, digest in manifest.items():
        path = folder / rel
        if not path.is_file():
            missing.append(rel)
        elif sha256_file(path) != digest:
            differ.append(rel)
    text = (folder / MANIFEST).read_text(encoding="utf-8")
    return {"folder": str(folder), "files": len(manifest), "missing": missing, "differ": differ,
            "manifest_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "ok": not missing and not differ}


def restore(backup_dir: Path, dest: Path) -> dict[str, Any]:
    backup_dir, dest = Path(backup_dir), Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise SystemExit(f"restore destination {dest} is not empty")
    manifest = read_manifest(backup_dir)
    for rel in manifest:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup_dir / rel, target)
    shutil.copy2(backup_dir / MANIFEST, dest / MANIFEST)
    result = verify(dest)            # hashes BEFORE anything opens the databases
    integrity = {}
    for rel in manifest:
        if is_sqlite(dest / rel):
            con = sqlite3.connect(str(dest / rel))
            try:
                integrity[rel] = con.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                con.close()
    result["integrity"] = integrity
    result["ok"] = result["ok"] and all(v == "ok" for v in integrity.values())
    (dest / MANIFEST).unlink()
    return result


# --------------------------------------------------------------------------- locks

def lock_held(path: Path) -> bool:
    """True when another process holds the kernel byte lock at offset 0 of ``path``."""
    path = Path(path)
    if not path.exists():
        return False
    try:
        f = open(path, "a+b")
    except OSError:
        return True
    try:
        f.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return True
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            return False
        import fcntl
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(f, fcntl.LOCK_UN)
        return False
    finally:
        f.close()


def token_fingerprint(token: str) -> str:
    """Same fingerprint as bcc.pit.bot_guard.token_fingerprint."""
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()[:24]


def poller_lock_dir(environ=os.environ) -> Path:
    configured = environ.get("BOSSMAN_TELEGRAM_POLLER_LOCK_DIR", "").strip()
    if configured:
        return Path(configured)
    base = environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share"))
    return Path(base) / "Bossman" / "telegram-pollers"


def read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not Path(path).is_file():
        return out
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        name, sep, value = line.partition("=")
        if sep and name.strip() and not name.lstrip().startswith("#"):
            out[name.strip()] = value.strip().strip('"').strip("'")
    return out


COMPANION_ENV_KEYS = ("TG_COMPANION_BOT_TOKEN", "TG_COMPANION_CORE_TOKEN", "TG_COMPANION_LOCAL_TOKEN",
                      "TG_COMPANION_CLOUD_TOKEN", "TG_COMPANION_PROXY")


def companion_env(config: Path) -> dict[str, str]:
    """The «Пульт» reads its secrets from TG_COMPANION_* (scripts/Start-TelegramCompanion.ps1
    imports them from companion.env beside the config); only those names pass."""
    values = read_env_file(Path(config).parent / "companion.env")
    return {k: v for k, v in values.items() if k in COMPANION_ENV_KEYS and v}


def companion_fingerprint(config: Path) -> str | None:
    token = read_env_file(Path(config).parent / "companion.env").get("TG_COMPANION_BOT_TOKEN", "")
    return token_fingerprint(token) if token else None


def backend_holder(data_dir: Path) -> dict[str, Any] | None:
    data_dir = Path(data_dir)
    if not lock_held(data_dir / "backend.lock"):
        return None
    try:
        return json.loads((data_dir / "backend.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"pid": None, "port": None}


# --------------------------------------------------------------------------- launch

def tts_env_from_script(script: Path) -> dict[str, str]:
    """The owner's chosen Jeff voice lives in <data>/voice/Start-Jeff-Voice.ps1
    (``$env:BOSSMAN_PIT_TTS_* = '...'``). Keep using it, whichever build runs Jeff."""
    out: dict[str, str] = {}
    if not Path(script).is_file():
        return out
    pattern = re.compile(r"^\s*\$env:(BOSSMAN_PIT_TTS_[A-Z_]+)\s*=\s*'([^']*)'\s*$", re.M)
    for key, value in pattern.findall(Path(script).read_text(encoding="utf-8-sig")):
        if key in TTS_KEYS and value:
            out[key] = value
    return out


def jeff_voice_env(data_dir: Path) -> dict[str, str]:
    voice = Path(data_dir) / "voice"
    env = tts_env_from_script(voice / "Start-Jeff-Voice.ps1")
    if all(Path(env.get(k, "")).is_file() for k in TTS_KEYS):
        return env
    exe, model = voice / "piper" / "piper.exe", voice / "ru_RU-denis-medium.onnx"
    if exe.is_file() and model.is_file():
        return {"BOSSMAN_PIT_TTS_EXECUTABLE": str(exe), "BOSSMAN_PIT_TTS_MODEL_PATH": str(model)}
    return {}


def child_env(home: Path, data_dir: Path, base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    for k in ("BCC_PORT", "BCC_HOST", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(k, None)
    home = Path(home)
    env["BCC_DATA_DIR"] = str(data_dir)
    env["BOSSMAN_DATA_DIR"] = str(data_dir)
    env["BOSSMAN_HOME"] = str(home) + os.sep
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(home / "browser")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PATH"] = os.pathsep.join([str(home / "runtime" / "Scripts"), str(home / "media"),
                                   env.get("PATH", "")])
    return env


def command_for(kind: str, home: Path, data_dir: Path, port: int, companion_config: Path | None) -> list[str]:
    py = str(Path(home) / "runtime" / "python.exe")
    if kind == "backend":
        return [py, "-I", "-X", "utf8", "-m", "bcc", "--host", "127.0.0.1", "--port", str(port)]
    if kind == "jeff":
        # under Jeff's own watchdog: restarted after a crash or a stale heartbeat (owner 07.10: always on)
        return [py, "-I", "-X", "utf8", "-m", "bcc.pit.cli", "watch", "--data-dir", str(data_dir)]
    if kind == "companion":
        return [py, "-I", "-X", "utf8", "-m", "bcc.telegram_companion", "--config", str(companion_config)]
    if kind == "jeff-window":
        pyw = str(Path(home) / "runtime" / "pythonw.exe")
        return [pyw, "-I", "-X", "utf8", "-m", "bcc.jeff_desktop", "--data-dir", str(data_dir),
                "--port", str(port)]
    raise ValueError(kind)


def _spawn_hidden(argv: list[str], cwd: Path, env: dict[str, str], log: Path) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        import ctypes
        # Children inherit "ignore Ctrl+C" from whoever started us (git-bash does that);
        # the one backend must stop cleanly on a Ctrl+C sent to its console.
        ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        # Own hidden console: a real Ctrl+C can reach it (graceful stop), and
        # closing whoever launched it does not kill it.
        kwargs = {"creationflags": subprocess.CREATE_NEW_CONSOLE, "startupinfo": si}
    with open(log, "ab") as out:
        p = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                             stdout=out, stderr=subprocess.STDOUT, **kwargs)
    return p.pid


def _health(port: int, timeout: float = 3.0) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health/live", timeout=timeout) as r:
            return json.loads(r.read())
    except (OSError, ValueError, urllib.error.URLError):
        return None


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return code.value == 259                      # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def keys_ensure(say) -> None:
    """Owner order 07.10: provider keys must not be lost. Before the backend starts, bring any
    missing key back from the DPAPI copy and refresh the copy (tools/keys_guard.py). Names-only
    status line; a failure here never blocks the launch. Windows only (DPAPI)."""
    if os.name != "nt":
        return
    path = Path(__file__).with_name("keys_guard.py")
    if not path.is_file():
        say("keys: keys_guard.py not found next to this tool - keys not checked")
        return
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("keys_guard", path)
        kg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(kg)
        out = kg.ensure(kg.default_keys_dir(), kg.default_backup_dir())
        restored = (out.get("restore") or {}).get("restored") or []
        v = out.get("verify") or {}
        say(f"keys: {v.get('verdict', '?')} ({len(v.get('env_names') or [])} names)"
            + (f"; restored: {', '.join(restored)}" if restored else ""))
    except Exception as exc:  # noqa: BLE001 — never block the launch on the guard
        say(f"keys: not checked ({type(exc).__name__})")


def launch(kind: str, home: Path, data_dir: Path, port: int, companion_config: Path | None,
           wait: float = 120.0, out=sys.stdout, ensure: bool = False) -> int:
    home, data_dir = Path(home), Path(data_dir)
    state = data_dir / STATE_DIR
    stamp = time.strftime("%Y%m%d-%H%M%S")
    say = lambda m: print(f"[one-bossman] {kind}: {m}", file=out, flush=True)  # noqa: E731
    hold = state / f"hold-{kind}"
    if ensure and hold.exists():
        say(f"HELD: the owner stopped {kind} on purpose ({hold}); --ensure does not start it")
        return 0
    if not ensure:
        hold.unlink(missing_ok=True)       # an explicit launch is the owner taking it back
    if kind == "supervisor":
        spec = state / "supervisor.json"
        if not spec.is_file():
            say(f"no {spec} yet (learning supervisor not delivered): nothing to start")
            return 0
        argv = json.loads(spec.read_text(encoding="utf-8"))["argv"]
        pid = _spawn_hidden(argv, home, child_env(home, data_dir), state / f"supervisor-{stamp}.log")
        (state / "supervisor.pid").write_text(str(pid), encoding="ascii")
        say(f"started pid {pid}")
        return 0
    if kind == "backend":
        holder = backend_holder(data_dir)
        if holder:
            say(f"ALREADY RUNNING: pid {holder.get('pid')} port {holder.get('port')} "
                f"build {str(holder.get('build_sha') or '?')[:12]} - not starting a second one")
            return 0
        if _health(port):
            say(f"REFUSED: port {port} already answers /health/live but does not hold {data_dir}")
            return 3
        keys_ensure(say)
    elif kind == "jeff":
        if lock_held(data_dir / PIT_HOME / "poller.lock"):
            say("ALREADY RUNNING: pit-v1.7/poller.lock is held - one Jeff poller per data root")
            return 0
    elif kind == "companion":
        cfg = Path(companion_config)
        fp = companion_fingerprint(cfg)
        if lock_held(cfg.parent / "poller.lock") or (
                fp and lock_held(poller_lock_dir() / f"{fp}.lock")):
            say("ALREADY POLLED: the «Пульт» bot token lock is held by another process - not starting")
            return 0
    env = child_env(home, data_dir)
    if kind in ("jeff", "jeff-window"):
        env.update(jeff_voice_env(data_dir))
    if kind == "companion":
        env.update(companion_env(Path(companion_config)))
    argv = command_for(kind, home, data_dir, port, companion_config)
    if kind == "jeff-window":
        # The participant window: its own small server + browser window (bcc.jeff_desktop
        # never starts or stops the Command Center backend). No console, nothing to wait for.
        state.mkdir(parents=True, exist_ok=True)
        with open(state / f"jeff-window-{stamp}.log", "ab") as log:
            p = subprocess.Popen(argv, cwd=str(home), env=env, stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=subprocess.STDOUT)
        say(f"started pid {p.pid}")
        return 0
    state.mkdir(parents=True, exist_ok=True)
    pid = _spawn_hidden(argv, home, env, state / f"{kind}-{stamp}.log")
    (state / f"{kind}.pid").write_text(str(pid), encoding="ascii")
    say(f"started pid {pid}: {' '.join(argv[1:])}")
    deadline = time.monotonic() + (wait if kind == "backend" else min(wait, 15.0))
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            tail = (state / f"{kind}-{stamp}.log").read_text(encoding="utf-8", errors="replace")[-800:]
            say(f"EXITED early; log tail:\n{tail}")
            return 4
        if kind == "backend" and _health(port):
            say(f"healthy on 127.0.0.1:{port}")
            return 0
        time.sleep(0.5)
    if kind == "backend":
        say(f"FAIL: no /health/live on {port} within {wait:.0f} s")
        return 5
    say("still running after the start window")
    return 0


# --------------------------------------------------------------------------- repoint

def repoint(config: Path, port: int, stamp: str) -> dict[str, Any]:
    """Set core_url in one JSON config; the previous file is kept beside it."""
    config = Path(config)
    raw = config.read_text(encoding="utf-8")
    data = json.loads(raw)
    new_url = f"http://127.0.0.1:{port}"
    before = data.get("core_url")
    if before == new_url:
        return {"config": str(config), "core_url": before, "changed": False}
    backup = config.with_name(f"{config.name}.before-one-bossman-{stamp}")
    backup.write_text(raw, encoding="utf-8")
    data["core_url"] = new_url
    # In place, never replace the file: its owner and ACL stay as they were. A file
    # re-created by an elevated shell is owned by Administrators, and under the
    # owner-only ACL of pit-v1.7 the non-elevated logon task could no longer read it.
    with open(config, "r+", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2))
        f.truncate()
    return {"config": str(config), "before": before, "after": new_url, "backup": str(backup),
            "changed": True}


# --------------------------------------------------------------------------- agents

def _api(port: int, token: str, method: str, path: str, body: Any = None) -> Any:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                 headers={"X-BCC-Token": token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"null")


def apply_agents(port: int, data_dir: Path, plan: list[dict[str, Any]], evidence: Path,
                 api=_api) -> dict[str, Any]:
    """plan: [{"id": 1, "model_id": 9, "fallback_model_id": 6}, ...]. Before/after kept."""
    token = (Path(data_dir) / "token").read_text(encoding="utf-8").strip()
    evidence = Path(evidence)
    evidence.mkdir(parents=True, exist_ok=True)
    before = api(port, token, "GET", "/api/agents", None)
    models = {m["id"] for m in api(port, token, "GET", "/api/models", None)}
    by_id = {a["id"]: a for a in before}
    changes = []
    for step in plan:
        agent = by_id.get(step["id"])
        if agent is None:
            raise SystemExit(f"agent {step['id']} does not exist")
        patch = {k: v for k, v in step.items() if k != "id" and agent.get(k) != v}
        for k, v in patch.items():
            if k.endswith("model_id") and v is not None and v not in models:
                raise SystemExit(f"agent {step['id']}: model {v} does not exist")
        if patch:
            api(port, token, "PATCH", f"/api/agents/{step['id']}", patch)
        changes.append({"id": step["id"], "name": agent.get("name"),
                        "before": {k: agent.get(k) for k in patch}, "patch": patch})
    after = api(port, token, "GET", "/api/agents", None)
    after_by_id = {a["id"]: a for a in after}
    ok = all(after_by_id[s["id"]].get(k) == v for s in plan for k, v in s.items() if k != "id")
    (evidence / "agents-before.json").write_text(json.dumps(before, ensure_ascii=False, indent=1), encoding="utf-8")
    (evidence / "agents-after.json").write_text(json.dumps(after, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"changes": changes, "ok": ok}


# --------------------------------------------------------------------------- status

def status(data_dir: Path, companion_config: Path | None) -> dict[str, Any]:
    data_dir = Path(data_dir)
    holder = backend_holder(data_dir)
    out: dict[str, Any] = {"data_dir": str(data_dir), "backend": holder,
                           "backend_pid_alive": bool(holder and holder.get("pid") and _pid_alive(int(holder["pid"])))}
    if holder and holder.get("port"):
        out["health"] = _health(int(holder["port"]))
    out["jeff_poller_lock_held"] = lock_held(data_dir / PIT_HOME / "poller.lock")
    if companion_config:
        fp = companion_fingerprint(Path(companion_config))
        out["companion_poller_lock_held"] = lock_held(Path(companion_config).parent / "poller.lock")
        out["companion_token_lock_held"] = bool(fp and lock_held(poller_lock_dir() / f"{fp}.lock"))
    return out


# --------------------------------------------------------------------------- find

def _same_path(a: str, b: Path) -> bool:
    try:
        return os.path.normcase(os.path.abspath(a.strip().strip('"'))) == os.path.normcase(os.path.abspath(str(b)))
    except (TypeError, ValueError):
        return False


def process_kind(line: str) -> str | None:
    """Which Bossman process a command line is (None: not one of ours)."""
    if re.search(r"-m\s+bcc(\.app)?(\s|$)", line):
        return "backend"
    if "bcc.pit.cli" in line and re.search(r"\s(start|watch)(\s|$)", line):
        return "jeff"
    if "bcc.telegram_companion" in line:
        return "companion"
    if "bcc.desktop" in line or "bcc.jeff_desktop" in line:
        return "window"
    return None


def data_dir_arg(cmd: list[str]) -> str | None:
    """``--data-dir D`` and ``--data-dir=D`` (a launcher script may use either form)."""
    for i, part in enumerate(cmd):
        if part == "--data-dir" and i + 1 < len(cmd):
            return cmd[i + 1]
        if part.startswith("--data-dir="):
            return part.split("=", 1)[1]
    return None


def find_processes(data_dir: Path) -> list[dict[str, Any]]:
    """Every Bossman process that serves or polls ``data_dir`` (needs psutil: system Python).

    A backend is matched by its BCC_DATA_DIR (or the default root when unset and it is an
    installed build), a Jeff/companion by ``--data-dir``/``--config`` on its command line."""
    import psutil  # noqa: PLC0415 — optional, only the system Python has it
    found = []
    for p in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        cmd = p.info.get("cmdline") or []
        if not cmd or not str(p.info.get("name") or "").lower().startswith("python"):
            continue
        line = " ".join(cmd)
        kind = process_kind(line)
        if not kind:
            continue
        try:
            env = p.environ()
        except (psutil.Error, OSError):
            env = {}
        mine = _same_path(env.get("BCC_DATA_DIR", ""), data_dir)
        if not env.get("BCC_DATA_DIR") and "\\runtime\\python" in cmd[0].lower() and env.get("LOCALAPPDATA"):
            # an installed build without BCC_DATA_DIR serves %LOCALAPPDATA%\Bossman\CommandCenter
            mine = mine or _same_path(str(Path(env["LOCALAPPDATA"]) / "Bossman" / "CommandCenter"), data_dir)
        arg = data_dir_arg(cmd)
        if arg:
            mine = mine or _same_path(arg, data_dir)
        if not mine:
            continue
        ports = []
        with contextlib.suppress(psutil.Error, OSError):
            ports = sorted({c.laddr.port for c in p.net_connections(kind="tcp") if c.status == "LISTEN"})
        found.append({"pid": p.info["pid"], "kind": kind, "cmdline": line, "ports": ports,
                      "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.info["create_time"]))})
    return found


# --------------------------------------------------------------------------- stop

_CTRL_C = """
import ctypes, sys
k = ctypes.WinDLL("kernel32", use_last_error=True)
k.FreeConsole()
if not k.AttachConsole(int(sys.argv[1])):
    sys.exit(3)
keep = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)(lambda event: 1)   # survive our own event
k.SetConsoleCtrlHandler(keep, True)
sys.exit(0 if k.GenerateConsoleCtrlEvent(int(sys.argv[2]), 0) else 4)
"""
CTRL_C_EVENT, CTRL_BREAK_EVENT = 0, 1


def ctrl_c(pid: int, python: str = sys.executable, event: int = CTRL_C_EVENT) -> int:
    """Deliver a real Ctrl+C (or Ctrl+Break) to the console of ``pid`` from a helper
    process, so this process keeps its own console. uvicorn shuts down gracefully on
    both (it handles SIGINT and SIGBREAK); Ctrl+Break also reaches a process that was
    started with Ctrl+C ignored (an attribute children inherit, e.g. from git-bash)."""
    exe = python
    if exe.lower().endswith("pythonw.exe"):
        exe = exe[: -len("pythonw.exe")] + "python.exe"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run([exe, "-I", "-c", _CTRL_C, str(pid), str(event)], creationflags=flags).returncode


def _wait_exit(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.5)
    return not _pid_alive(pid)


def stop_process(proc: dict[str, Any], data_dir: Path, force: bool = False,
                 timeout: float = 60.0) -> dict[str, Any]:
    """Graceful first: Jeff through its own stop.flag protocol, everything else by Ctrl+C."""
    pid = int(proc["pid"])
    how = []
    if proc["kind"] == "jeff":
        flag = Path(data_dir) / PIT_HOME / "stop.flag"
        flag.write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), encoding="utf-8")
        how.append("stop.flag")
        if _wait_exit(pid, timeout):
            return {"pid": pid, "kind": proc["kind"], "stopped": "graceful", "how": how}
    rc = ctrl_c(pid)
    how.append(f"ctrl-c(rc={rc})")
    if _wait_exit(pid, 20.0):
        return {"pid": pid, "kind": proc["kind"], "stopped": "graceful", "how": how}
    rc = ctrl_c(pid, event=CTRL_BREAK_EVENT)
    how.append(f"ctrl-break(rc={rc})")
    if _wait_exit(pid, 30.0):
        # uvicorn: graceful shutdown on SIGBREAK; a poller without a SIGBREAK handler exits at once
        return {"pid": pid, "kind": proc["kind"],
                "stopped": "graceful" if proc["kind"] == "backend" else "ctrl-break", "how": how}
    if force:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        how.append("taskkill /F")
        return {"pid": pid, "kind": proc["kind"], "stopped": "FORCED", "how": how}
    return {"pid": pid, "kind": proc["kind"], "stopped": "NO", "how": how}


# --------------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    if sys.stdout is None:          # pythonw (scheduled task): keep a log instead of nothing
        log_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Bossman" / "one-bossman-logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(log_dir / "launcher.log", "a", encoding="utf-8")
        print(time.strftime("--- %Y-%m-%d %H:%M:%S"), " ".join(sys.argv[1:]), flush=True)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup"); b.add_argument("--src", required=True); b.add_argument("--dest", required=True)
    v = sub.add_parser("verify"); v.add_argument("--dir", required=True)
    r = sub.add_parser("restore"); r.add_argument("--backup", required=True); r.add_argument("--dest", required=True)
    la = sub.add_parser("launch")
    la.add_argument("kind", choices=("backend", "jeff", "companion", "supervisor", "jeff-window"))
    la.add_argument("--home", required=True); la.add_argument("--data-dir", required=True)
    la.add_argument("--port", type=int, default=8801); la.add_argument("--companion-config", default=None)
    la.add_argument("--wait", type=float, default=120.0)
    la.add_argument("--ensure", action="store_true",
                    help="periodic self-heal: start only if not running and not held by `stop --hold`")
    rp = sub.add_parser("repoint"); rp.add_argument("--config", action="append", required=True)
    rp.add_argument("--port", type=int, required=True)
    ag = sub.add_parser("agents"); ag.add_argument("--port", type=int, required=True)
    ag.add_argument("--data-dir", required=True); ag.add_argument("--plan", required=True)
    ag.add_argument("--evidence", required=True)
    fd = sub.add_parser("find"); fd.add_argument("--data-dir", required=True)
    so = sub.add_parser("stop"); so.add_argument("--data-dir", required=True)
    so.add_argument("--kinds", default="jeff,companion,backend")
    so.add_argument("--except-home", default="", help="leave processes whose executable is under this install")
    so.add_argument("--only-home", default="", help="stop only processes whose executable is under this install")
    so.add_argument("--force", action="store_true")
    so.add_argument("--hold", action="store_true",
                    help="keep these kinds down: the periodic --ensure launch will not restart them")
    st = sub.add_parser("status"); st.add_argument("--data-dir", required=True)
    st.add_argument("--companion-config", default=None)
    a = p.parse_args(argv)
    if a.cmd == "backup":
        res = backup(Path(a.src), Path(a.dest)); print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    if a.cmd == "verify":
        res = verify(Path(a.dir)); print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0 if res["ok"] else 1
    if a.cmd == "restore":
        res = restore(Path(a.backup), Path(a.dest)); print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0 if res["ok"] else 1
    if a.cmd == "launch":
        return launch(a.kind, Path(a.home), Path(a.data_dir), a.port,
                      Path(a.companion_config) if a.companion_config else None, a.wait, ensure=a.ensure)
    if a.cmd == "repoint":
        stamp = time.strftime("%Y%m%d-%H%M%S")
        res = [repoint(Path(c), a.port, stamp) for c in a.config]
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    if a.cmd == "agents":
        plan = json.loads(Path(a.plan).read_text(encoding="utf-8"))
        res = apply_agents(a.port, Path(a.data_dir), plan, Path(a.evidence))
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0 if res["ok"] else 1
    if a.cmd == "find":
        print(json.dumps(find_processes(Path(a.data_dir)), ensure_ascii=False, indent=1))
        return 0
    if a.cmd == "stop":
        procs = find_processes(Path(a.data_dir))
        kinds = [k for k in a.kinds.split(",") if k]
        res = []
        for kind in kinds:                      # pollers first, the backend last
            for proc in procs:
                exe = proc["cmdline"].lower()
                if proc["kind"] != kind:
                    continue
                if a.except_home and exe.startswith(os.path.normcase(a.except_home).lower()):
                    continue
                if a.only_home and not exe.startswith(os.path.normcase(a.only_home).lower()):
                    continue
                res.append({**stop_process(proc, Path(a.data_dir), force=a.force), "cmdline": proc["cmdline"]})
        if a.hold:
            state = Path(a.data_dir) / STATE_DIR
            state.mkdir(parents=True, exist_ok=True)
            for kind in kinds:
                (state / f"hold-{kind}").write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), encoding="utf-8")
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0 if all(r["stopped"] != "NO" for r in res) else 1
    if a.cmd == "status":
        res = status(Path(a.data_dir), Path(a.companion_config) if a.companion_config else None)
        print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
