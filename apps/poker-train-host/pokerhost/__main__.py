"""python -m pokerhost run|stop|status — host Poker Train as a Telegram Mini App from the owner's PC.

run: game server on 127.0.0.1:PORT + Cloudflare quick tunnel (free, no account, HTTPS URL changes per start) +
menu button «🃏 Покер» on the Jeff bot for everyone and in the owner's pult chat. stop: buttons back to default,
tunnel and server down. Bot tokens are read from the existing local stores and never printed or written.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

DATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Bossman" / "poker-host"
STATE = DATA / "state.json"
CLOUDFLARED = Path(os.environ.get("BOSSMAN_CLOUDFLARED",
                                  r"C:\Users\asd\Bossman\apps-local\cloudflared\cloudflared.exe"))
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
BUTTON_TEXT = "🃏 Покер"


def tokens() -> dict:
    """{'jeff': token, 'pult': token, 'owner_chat': id} from the local Bossman stores (never logged)."""
    out: dict = {}
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "command-center"))
    with contextlib.suppress(Exception):
        from bcc.pit import config as pit
        out["jeff"] = pit.load(pit.config_path(pit.default_data_dir())).bot_token
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "telegram-companion"
    with contextlib.suppress(Exception):
        for line in (base / "companion.env").read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r"\s*(?:export\s+)?(\w*BOT_TOKEN\w*)\s*=\s*(.+)", line)
            if m:
                out["pult"] = m.group(2).strip().strip("\"'")
                break
        cfg = json.loads((base / "config.json").read_text(encoding="utf-8"))
        out["owner_chat"] = [p for p in cfg["people"] if p.get("role") == "owner"][0]["chat_id"]
    return out


def tg(token: str, method: str, payload: dict) -> bool:
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return bool(json.loads(r.read()).get("ok"))
    except Exception:            # noqa: BLE001 — a failed button must not take the host down; status shows it
        return False


def set_buttons(tok: dict, url: str | None) -> dict:
    button = {"type": "web_app", "text": BUTTON_TEXT, "web_app": {"url": url}} if url else {"type": "default"}
    done = {}
    if tok.get("jeff"):
        done["jeff_all_users"] = tg(tok["jeff"], "setChatMenuButton", {"menu_button": button})
    if tok.get("pult") and tok.get("owner_chat"):
        done["pult_owner_only"] = tg(tok["pult"], "setChatMenuButton",
                                     {"chat_id": tok["owner_chat"], "menu_button": button})
    return done


def write_state(**kw) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps({**kw, "updated": time.time()}, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE)


def read_state() -> dict:
    with contextlib.suppress(Exception):
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {}


async def run(dist: Path, port: int) -> None:
    import uvicorn
    from .server import create_app
    tok = tokens()
    app = create_app(dist, DATA, [t for t in (tok.get("jeff"), tok.get("pult")) if t])
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                           proxy_headers=False))
    serve = asyncio.create_task(server.serve())
    tunnel = subprocess.Popen([str(CLOUDFLARED), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                              errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    url = None
    deadline = time.monotonic() + 60
    while url is None and time.monotonic() < deadline and tunnel.poll() is None:
        line = await asyncio.to_thread(tunnel.stdout.readline)
        m = URL_RE.search(line or "")
        url = m.group(0) if m else None
    if url is None:
        tunnel.terminate()
        server.should_exit = True
        write_state(status="failed", reason="tunnel_url_not_received", pid=os.getpid())
        raise SystemExit("tunnel did not report a URL")
    buttons = set_buttons(tok, url)
    write_state(status="running", url=url, port=port, pid=os.getpid(), tunnel_pid=tunnel.pid, buttons=buttons,
                started=time.time(), dist=str(dist))
    print(f"POKER_HOST running {url} buttons={buttons}", flush=True)
    asyncio.create_task(asyncio.to_thread(lambda: [None for _ in tunnel.stdout]))   # keep draining tunnel logs
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(Exception):
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    try:
        while not stop.is_set() and not serve.done() and tunnel.poll() is None and not (DATA / "STOP").exists():
            await asyncio.sleep(1)
    finally:
        set_buttons(tok, None)
        tunnel.terminate()
        server.should_exit = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(serve, 10)
        with contextlib.suppress(FileNotFoundError):
            (DATA / "STOP").unlink()
        write_state(status="stopped", stopped=time.time())


def main() -> None:
    ap = argparse.ArgumentParser(prog="pokerhost")
    ap.add_argument("cmd", choices=["run", "stop", "status"])
    ap.add_argument("--dist", type=Path, default=DATA / "dist")
    ap.add_argument("--port", type=int, default=8810)
    a = ap.parse_args()
    if a.cmd == "run":
        if not (a.dist / "index.html").is_file():
            raise SystemExit(f"no game build at {a.dist}")
        asyncio.run(run(a.dist, a.port))
    elif a.cmd == "stop":
        DATA.mkdir(parents=True, exist_ok=True)
        (DATA / "STOP").write_text("stop", encoding="utf-8")      # the running host sees it within a second
        print("stop requested")
    else:
        print(json.dumps(read_state(), ensure_ascii=False))


if __name__ == "__main__":
    main()
