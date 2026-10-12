#!/usr/bin/env python
"""JEFF-BUNDLE-PROOF: prove Jeff 2.0 criteria on a BUILT Windows bundle (its own runtime\\python.exe), not on the repo.

What it does
------------
* runs the product exactly like the owner launcher does:
      <bundle>\\runtime\\python.exe -I -X utf8 -m bcc.pit.cli <cmd> --data-dir D
      <bundle>\\runtime\\python.exe -I -X utf8 -m bcc --host 127.0.0.1 --port P          (BCC_DATA_DIR=D)
  with a WHITELISTED environment (no inherited BOSSMAN_*/BCC_*/tokens), LOCALAPPDATA/APPDATA/USERPROFILE/TEMP
  redirected into a throwaway root, and the repo NOT on sys.path (the interpreter is isolated, -I);
* talks only to loopback FAKES: a Telegram Bot API (SIMULATED), an OpenAI-compatible provider and a
  DuckDuckGo-like page. The only way the fakes are reached is the test seam below;
* the seam is a ``sitecustomize.py`` placed in <bundle>\\runtime\\Lib\\site-packages for the duration of the run (its
  SHA256 goes into the report and it is deleted at the end). It does nothing unless JEFF_PROOF_FAKE_TG_URL is set,
  refuses a non-loopback URL, and then only repoints two module constants of
  ``bcc.telegram_companion.adapters`` (TELEGRAM_API and the DuckDuckGo doctor/search URL) to the loopback fake.
  Everything on the start path (``pit start`` -> ParticipantRuntime -> Telegram/provider adapters) is product code;
* scenarios S1..S7 each yield PASS / FAIL / NOT_RUN with evidence; the process exits 0 only when every executed
  scenario passes. Known gaps that the stated criteria do not cover are recorded as ``gaps`` and are never hidden.

Nothing here edits a repo file, starts a scheduled task or leaves a process behind: every child is stopped at the end
and a leftover scan (by command line / port) is part of the report.
"""
from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT_BUNDLE = r"C:\Users\asd\Bossman\nb\3d2ef931eecd\BOSSMAN-Windows-x64-3d2ef931eecd"
DEFAULT_CONTROL = r"C:\Users\asd\Bossman\nb\f34f7e56d43c\BOSSMAN-Windows-x64-f34f7e56d43c"
DEFAULT_OUT = r"C:\Users\asd\Bossman\creative-runs\JEFF-BUNDLE-PROOF-20261012"


def _bootstrap_psutil():
    try:
        import psutil as module
        return module
    except ImportError:
        pass
    # not the bundle interpreter: run again with the bundle's own python (it has psutil/httpx)
    bundle = DEFAULT_BUNDLE
    argv = sys.argv[1:]
    for index, item in enumerate(argv):
        if item == "--bundle" and index + 1 < len(argv):
            bundle = argv[index + 1]
        elif item.startswith("--bundle="):
            bundle = item.split("=", 1)[1]
    py = Path(bundle) / "runtime" / "python.exe"
    if py.is_file() and Path(sys.executable).resolve() != py.resolve():
        raise SystemExit(subprocess.call([str(py), "-I", "-X", "utf8", str(Path(__file__).resolve()), *argv]))
    raise SystemExit("psutil is required (run this script with <bundle>\\runtime\\python.exe)")


psutil = _bootstrap_psutil()

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
FAKE_TOKEN = "700120001:JEFFPROOF" + "Q" * 24          # FAKE: only our own loopback Bot API accepts it
PROVIDER_KEY = "fake-provider-key-jeffproof"
FREE_MODEL = "fake/free-chat:free"
PAID_MODEL = "fake/paid-chat"
SNEAKY_MODEL = "fake/sneaky-paid:free"                  # ':free' id but a live price > 0
CURSOR = " \u258d"                                       # the draft cursor the product appends to a live preview
NONCE_RE = re.compile(r"JP-[A-Z]{2,8}-[A-Z]{6}")
SEAM_NAME = "sitecustomize.py"

# product texts (prefixes) copied from bcc/pit/runtime.py of the bundle: an HONEST refusal is one of these
REFUSAL_PREFIX = {
    "NO_MODEL_RU": "Сейчас у меня нет доступной бесплатной модели для ответа.",
    "PROVIDER_DOWN_RU": "Модель-провайдер недоступен.",
    "CLOUD_PAUSED_RU": "Бесплатный облачный лимит на сейчас исчерпан",
    "INCOMPLETE_REPLY_RU": "Ответ модели оборвался.",
}
INTRO_PREFIX = "Привет, я Джефф"
TASK_DONE_PREFIX = "Задача готова"

WHITELIST = ["SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "PATH", "PATHEXT", "COMSPEC", "PROCESSOR_ARCHITECTURE",
             "PROCESSOR_IDENTIFIER", "PROCESSOR_LEVEL", "PROCESSOR_REVISION", "NUMBER_OF_PROCESSORS", "OS",
             "COMPUTERNAME", "USERNAME", "USERDOMAIN", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
             "COMMONPROGRAMFILES", "ALLUSERSPROFILE", "PUBLIC", "DRIVERDATA"]


# ---------------------------------------------------------------------------------------------- small helpers
def now() -> float:
    return time.time()


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def read_json(path: Path):
    for _ in range(4):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            time.sleep(0.05)
    return None


def wait_until(pred, timeout: float, step: float = 0.25):
    end = time.time() + timeout
    while time.time() < end:
        value = pred()
        if value:
            return value
        time.sleep(step)
    return pred() or None


def http_json(url: str, timeout: float = 5.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read(200000))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def free_ports(count: int, start: int = 28850) -> list[int]:
    found: list[int] = []
    port = start
    while len(found) < count and port < start + 400:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                found.append(port)
            except OSError:
                pass
        port += 1
    if len(found) < count:
        raise SystemExit("no free loopback ports")
    return found


def kill_tree(pid: int) -> None:
    try:
        root = psutil.Process(pid)
        victims = [*root.children(recursive=True), root]
    except psutil.Error:
        return
    for proc in victims:
        try:
            proc.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(victims, timeout=10)


def descendants(pid: int) -> list[int]:
    try:
        return [c.pid for c in psutil.Process(pid).children(recursive=True)]
    except psutil.Error:
        return []


def listening_pids(port: int) -> list[int]:
    try:
        return sorted({c.pid for c in psutil.net_connections(kind="tcp")
                       if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port and c.pid})
    except Exception:       # noqa: BLE001
        return []


def jeff_pollers_like_product() -> list[dict]:
    """The same logical count ``pit doctor`` uses (every `... pit|bcc.pit|bcc.pit.cli start`), host-wide."""
    rows = []
    me = os.getpid()
    for proc in psutil.process_iter(["pid", "ppid", "cmdline", "exe"]):
        try:
            tokens = [str(t).lower() for t in (proc.info.get("cmdline") or [])]
            if proc.info["pid"] == me or "start" not in tokens:
                continue
            head = tokens[: tokens.index("start")]
            if head and head[-1] in {"pit", "bcc.pit", "bcc.pit.cli"}:
                rows.append({"pid": proc.info["pid"], "ppid": proc.info["ppid"],
                             "data_dir": next((tokens[i + 1] for i, t in enumerate(tokens)
                                               if t == "--data-dir" and i + 1 < len(tokens)), "")})
        except (psutil.Error, TypeError, ValueError):
            continue
    return rows


def pollers_for(data_dir: Path) -> list[int]:
    want = os.path.normcase(os.path.normpath(str(data_dir)))
    return sorted(r["pid"] for r in jeff_pollers_like_product()
                  if r["data_dir"] and os.path.normcase(os.path.normpath(r["data_dir"])) == want)


def classify_refusal(text: str) -> str | None:
    for name, prefix in REFUSAL_PREFIX.items():
        if str(text).strip().startswith(prefix):
            return name
    return None


def clip(text: str, n: int = 240) -> str:
    text = str(text)
    return text if len(text) <= n else text[: n - 1] + "…"


# ------------------------------------------------------------------------------------------- test seam
SEAM_SOURCE = '''"""JEFF-PROOF test seam (NOT product code). Placed by tools/jeff_bundle_proof.py and removed at the end of its run.

Only when JEFF_PROOF_FAKE_TG_URL points at a LOOPBACK fake, the module constants
``bcc.telegram_companion.adapters.TELEGRAM_API`` and ``DDG_HTML`` are repointed to it right after that module is
imported. Without the env var this file does nothing; a non-loopback URL is refused (the process exits).
"""
import os
import sys

_url = os.environ.get("JEFF_PROOF_FAKE_TG_URL", "").strip()
if _url:
    if not _url.startswith("http://127.0.0.1:"):
        raise SystemExit("JEFF-PROOF seam: refusing a non-loopback fake Telegram URL")
    import importlib.abc
    import importlib.util

    _TARGET = "bcc.telegram_companion.adapters"

    class _PatchTelegramApi(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path, target=None):
            if name != _TARGET:
                return None
            try:
                sys.meta_path.remove(self)
            except ValueError:
                pass
            spec = importlib.util.find_spec(name)
            if spec is None or spec.loader is None:
                return spec
            original = spec.loader.exec_module

            def exec_module(module, _original=original):
                _original(module)
                module.TELEGRAM_API = _url
                module.DDG_HTML = _url + "/ddg/html/"

            spec.loader.exec_module = exec_module
            return spec

    sys.meta_path.insert(0, _PatchTelegramApi())
'''


# ------------------------------------------------------------------------------------ helper run INSIDE the bundle
HELPER = r'''
import json, os, sys
from pathlib import Path
req = json.loads(sys.argv[1])
op = req["op"]
out = {}
if op == "env_check":
    import bcc, bossman_shared
    import bcc.pit.cli as cli
    out = {"bcc": bcc.__file__, "bossman_shared": bossman_shared.__file__, "cli": cli.__file__,
           "sys_path": list(sys.path), "executable": sys.executable, "isolated": int(sys.flags.isolated)}
else:
    from bcc.pit.config import config_path, save_setup, load
    from bcc.pit.models import ConsentState
    from bcc.pit.vault import PersonaVault
    from bcc.telegram_companion.config import Person
    data_dir = Path(req["data_dir"])
    path = config_path(data_dir)
    if op == "setup":
        if not path.exists():
            save_setup(path,
                       people=[Person(user_id=u["user_id"], chat_id=u["user_id"], role=u.get("role", "guest"))
                               for u in req["people"]],
                       chat_models=req["chat_models"], provider_base_url=req["provider_base_url"],
                       core_url=req["core_url"], allowlist_open=bool(req.get("allowlist_open", True)),
                       bot_token=req["bot_token"], provider_key=req["provider_key"])
            out["created"] = True
    s = load(path)
    vault = PersonaVault(Path(s.data_dir), bytes.fromhex(s.identity_salt))
    users = req.get("users") or [u["user_id"] for u in req.get("people", [])]
    out["config"] = str(path)
    out["keys"] = {str(u): vault.key_for_telegram(u) for u in users}
    out["person_dirs"] = {str(u): str(vault.person_dir(vault.key_for_telegram(u))) for u in users}
    if op == "consent":
        for item in req["consents"]:
            key = vault.key_for_telegram(item["user_id"])
            vault.set_consent(key, ConsentState(
                memory_enabled=True, remote_processing_enabled=True,
                remote_personalization_enabled=bool(item.get("personalization", False)),
                training_use_enabled=False, accepted_at="2026-10-12T00:00:00Z"))
    elif op == "facts":
        out["facts"] = {str(u): [r.get("value") for r in vault.iter_candidate_records(vault.key_for_telegram(u))]
                        for u in users}
        out["consent"] = {str(u): vault.consent(vault.key_for_telegram(u)).to_dict() for u in users}
    elif op == "sender_diag":
        import asyncio
        from bcc.pit.runtime import ParticipantRuntime
        from bcc.pit.j2.proactive import TelegramSender
        rt = ParticipantRuntime(s)
        key = vault.key_for_telegram(users[0])
        sender = TelegramSender(rt)
        person = sender._resolve(key)
        out["configured_roles"] = {str(pp.user_id): pp.role for pp in s.people}
        out["resolved_role"] = person.role if person else None
        out["allowlist_open"] = s.allowlist_open
        async def _go():
            try:
                out["sender_result"] = repr(await sender(key, "diag: delivery probe", "diag-key"))
            except Exception as exc:
                out["sender_exception"] = type(exc).__name__ + ": " + str(exc)
            await rt.close()
        asyncio.run(_go())
    elif op == "grep":
        needle = req["needle"].lower()
        hits = {}
        for u in users:
            base = vault.person_dir(vault.key_for_telegram(u))
            found = []
            if base.is_dir():
                for p in base.rglob("*"):
                    if p.is_file():
                        try:
                            if needle in p.read_text(encoding="utf-8", errors="ignore").lower():
                                found.append(str(p.relative_to(base)))
                        except OSError:
                            pass
            hits[str(u)] = found
        out["hits"] = hits
print(json.dumps(out, ensure_ascii=False))
'''


# ------------------------------------------------------------------------------------------ fake Telegram Bot API
class QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):      # a killed Jeff resets its sockets: expected, not noise
        pass


class FakeTelegram:
    """SIMULATED Bot API on loopback. Records every call; supports getFile + file download and Stars invoices."""

    def __init__(self, port: int, token: str):
        self.port, self.token = port, token
        self.cv = threading.Condition()
        self.next_update = 7000
        self.mid = 40000
        self.reset()
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, code, obj=None, raw=None, ctype="application/json"):
                data = raw if raw is not None else json.dumps(obj, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(n) if n else b""
                    try:
                        body = json.loads(raw or b"{}")
                    except ValueError:
                        body = {}
                    m = re.match(r"^/bot([^/]+)/(?:test/)?(\w+)$", self.path)
                    if not m or m.group(1) != outer.token:
                        outer.bad_token += 1
                        return self._send(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})
                    self._send(200, outer.handle(m.group(2), body))
                except (BrokenPipeError, ConnectionError, OSError):
                    pass

            def do_GET(self):
                try:
                    m = re.match(r"^/file/bot([^/]+)/(?:test/)?(.+)$", self.path)
                    if m and m.group(1) == outer.token:
                        data = outer.files.get(m.group(2))
                        if data is None:
                            return self._send(404, {"ok": False})
                        outer.file_downloads.append({"t": now(), "path": m.group(2)})
                        return self._send(200, raw=data, ctype="application/octet-stream")
                    if self.path.startswith("/ddg/html"):
                        html = ('<html><body><div class="result"><a class="result__a" '
                                'href="https://example.org/ping">Ping page</a>'
                                '<a class="result__snippet">Loopback fake search result.</a></div></body></html>')
                        outer.ddg_hits += 1
                        return self._send(200, raw=html.encode(), ctype="text/html; charset=utf-8")
                    self._send(404, {"ok": False})
                except (BrokenPipeError, ConnectionError, OSError):
                    pass

        self.httpd = QuietServer(("127.0.0.1", port), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    # -- state ---------------------------------------------------------------------------------------------
    def reset(self) -> None:
        with self.cv:
            self.updates: list[dict] = []
            self.sent: list[dict] = []
            self.calls: Counter = Counter()
            self.bad_token = 0
            self.holds: list[dict] = []
            self.files: dict[str, bytes] = {}
            self.file_ids: dict[str, dict] = {}
            self.file_downloads: list[dict] = []
            self.invoices: list[dict] = []
            self.pre_checkout_answers: list[dict] = []
            self.ddg_hits = 0
            self.cv.notify_all()

    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # -- injection --------------------------------------------------------------------------------------------
    def _push(self, update: dict) -> int:
        with self.cv:
            self.next_update += 1
            update["update_id"] = self.next_update
            self.updates.append(update)
            self.cv.notify_all()
            return self.next_update

    @staticmethod
    def _chat_from(user_id: int) -> tuple[dict, dict]:
        return ({"id": user_id, "is_bot": False, "first_name": "Tester", "language_code": "ru"},
                {"id": user_id, "type": "private", "first_name": "Tester"})

    def inject_text(self, user_id: int, text: str) -> int:
        sender, chat = self._chat_from(user_id)
        with self.cv:
            msg_id = self.next_update + 1
        return self._push({"message": {"message_id": msg_id, "date": int(now()), "text": text,
                                       "from": sender, "chat": chat}})

    def inject_document(self, user_id: int, file_id: str, file_name: str, caption: str = "") -> int:
        sender, chat = self._chat_from(user_id)
        with self.cv:
            msg_id = self.next_update + 1
        message = {"message_id": msg_id, "date": int(now()), "from": sender, "chat": chat,
                   "document": {"file_id": file_id, "file_name": file_name, "mime_type": "text/plain",
                                "file_size": len(self.files.get(self.file_ids.get(file_id, {}).get("path", ""), b""))}}
        if caption:
            message["caption"] = caption
        return self._push({"message": message})

    def inject_pre_checkout(self, user_id: int, payload: str, stars: int, query_id: str) -> int:
        sender, _ = self._chat_from(user_id)
        return self._push({"pre_checkout_query": {"id": query_id, "from": sender, "currency": "XTR",
                                                  "total_amount": stars, "invoice_payload": payload}})

    def inject_successful_payment(self, user_id: int, payload: str, stars: int, charge_id: str) -> int:
        sender, chat = self._chat_from(user_id)
        with self.cv:
            msg_id = self.next_update + 1
        return self._push({"message": {"message_id": msg_id, "date": int(now()), "from": sender, "chat": chat,
                                       "successful_payment": {"currency": "XTR", "total_amount": stars,
                                                              "invoice_payload": payload,
                                                              "telegram_payment_charge_id": charge_id,
                                                              "provider_payment_charge_id": charge_id}}})

    def register_file(self, file_id: str, path: str, data: bytes) -> None:
        with self.cv:
            self.files[path] = data
            self.file_ids[file_id] = {"path": path, "size": len(data)}

    def hold(self, method: str, text_prefix: str, seconds: float) -> dict:
        rule = {"method": method, "prefix": text_prefix, "seconds": seconds, "hits": 0}
        with self.cv:
            self.holds.append(rule)
        return rule

    # -- Bot API --------------------------------------------------------------------------------------------------
    def handle(self, method: str, body: dict):
        with self.cv:
            self.calls[method] += 1
        if method == "getMe":
            return {"ok": True, "result": {"id": 700120001, "is_bot": True, "first_name": "JeffProofFake",
                                           "username": "jeff_proof_fake_bot"}}
        if method == "getWebhookInfo":
            return {"ok": True, "result": {"url": "", "pending_update_count": 0}}
        if method == "getUpdates":
            offset = int(body.get("offset") or 0)
            deadline = now() + min(float(body.get("timeout") or 0), 3.0)
            limit = int(body.get("limit") or 20)
            with self.cv:
                self.updates = [u for u in self.updates if u["update_id"] >= offset]
                while True:
                    batch = [u for u in self.updates if u["update_id"] >= offset][:limit]
                    left = deadline - now()
                    if batch or left <= 0:
                        return {"ok": True, "result": batch}
                    self.cv.wait(timeout=min(0.5, left))
        if method == "sendMessage":
            text = str(body.get("text", ""))
            with self.cv:
                self.mid += 1
                mid = self.mid
                row = {"mid": mid, "chat_id": body.get("chat_id"), "text": text, "t": now(), "edits": [],
                       "deleted": False, "acked": False, "method": method, "parse_mode": body.get("parse_mode")}
                self.sent.append(row)
                self.cv.notify_all()
            self._maybe_hold(method, text)
            row["acked"] = True
            return {"ok": True, "result": {"message_id": mid, "date": int(now()), "text": text,
                                           "chat": {"id": body.get("chat_id"), "type": "private"}}}
        if method == "editMessageText":
            mid = body.get("message_id")
            with self.cv:
                for row in self.sent:
                    if row["mid"] == mid:
                        row["edits"].append({"t": now(), "text": str(body.get("text", ""))})
                self.cv.notify_all()
            return {"ok": True, "result": {"message_id": mid, "date": int(now()),
                                           "chat": {"id": body.get("chat_id"), "type": "private"},
                                           "text": str(body.get("text", ""))}}
        if method == "deleteMessage":
            mid = body.get("message_id")
            with self.cv:
                for row in self.sent:
                    if row["mid"] == mid:
                        row["deleted"] = True
                self.cv.notify_all()
            return {"ok": True, "result": True}
        if method == "getFile":
            info = self.file_ids.get(str(body.get("file_id", "")))
            if not info:
                return {"ok": False, "error_code": 400, "description": "Bad Request: file not found"}
            return {"ok": True, "result": {"file_id": body.get("file_id"), "file_path": info["path"],
                                           "file_size": info["size"]}}
        if method == "sendInvoice":
            with self.cv:
                self.mid += 1
                mid = self.mid
                self.invoices.append({"t": now(), "mid": mid, "payload": dict(body)})
                self.cv.notify_all()
            return {"ok": True, "result": {"message_id": mid, "date": int(now()),
                                           "chat": {"id": body.get("chat_id"), "type": "private"},
                                           "invoice": {"title": body.get("title"), "currency": body.get("currency"),
                                                       "total_amount": sum(int(p.get("amount", 0))
                                                                           for p in body.get("prices", []))}}}
        if method == "answerPreCheckoutQuery":
            with self.cv:
                self.pre_checkout_answers.append({"t": now(), "payload": dict(body)})
                self.cv.notify_all()
            return {"ok": True, "result": True}
        if method in ("sendChatAction", "setMyCommands", "answerCallbackQuery"):
            return {"ok": True, "result": True}
        return {"ok": False, "error_code": 400, "description": "unsupported in jeff-proof fake: " + method}

    def _maybe_hold(self, method: str, text: str) -> None:
        with self.cv:
            rules = [r for r in self.holds if r["method"] == method and text.startswith(r["prefix"])]
            for r in rules:
                r["hits"] += 1
        if rules:
            time.sleep(max(r["seconds"] for r in rules))

    # -- queries --------------------------------------------------------------------------------------------------
    @staticmethod
    def visible(row: dict) -> str:
        return row["edits"][-1]["text"] if row["edits"] else row["text"]

    def mark(self) -> int:
        with self.cv:
            return len(self.sent)

    def snapshot(self) -> list[dict]:
        with self.cv:
            return [{"mid": r["mid"], "chat_id": r["chat_id"], "t": r["t"], "text": r["text"], "visible": self.visible(r),
                     "edits": len(r["edits"]), "deleted": r["deleted"], "acked": r["acked"]} for r in self.sent]

    def rows_after(self, mark: int, chat_id: int | None = None) -> list[dict]:
        return [dict(r, index=i) for i, r in enumerate(self.snapshot())
                if i >= mark and (chat_id is None or r["chat_id"] == chat_id)]

    def wait_reply(self, chat_id: int, mark: int, *, contains: str | None = None, timeout: float = 75.0,
                   starts: str | None = None) -> dict | None:
        """First FINAL (non-draft, non-deleted) bot message to `chat_id` after `mark` meeting the predicate."""
        def find():
            for row in self.rows_after(mark, chat_id):
                text = row["visible"]
                if row["deleted"] or not text.strip() or text.rstrip().endswith(CURSOR.strip()):
                    continue
                if contains is not None and contains not in text:
                    continue
                if starts is not None and not text.startswith(starts):
                    continue
                return row
            return None
        return wait_until(find, timeout, 0.2)

    def answers(self, needle: str, chat_id: int | None = None, *, prefix: str | None = None) -> list[dict]:
        """Final (non-draft, non-deleted) messages showing `needle` (optionally starting with `prefix`)."""
        out = []
        for row in self.snapshot():
            text = row["visible"]
            if row["deleted"] or needle not in text or text.rstrip().endswith(CURSOR.strip()):
                continue
            if chat_id is not None and row["chat_id"] != chat_id:
                continue
            if prefix is not None and not text.startswith(prefix):
                continue
            out.append(row)
        return out

    def drafts(self, needle: str, chat_id: int | None = None) -> list[dict]:
        out = []
        for row in self.snapshot():
            text = row["visible"]
            if row["deleted"] or needle not in text or not text.rstrip().endswith(CURSOR.strip()):
                continue
            if chat_id is not None and row["chat_id"] != chat_id:
                continue
            out.append(row)
        return out

    def stop(self) -> None:
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:       # noqa: BLE001
            pass


# --------------------------------------------------------------------------------------------- fake provider
class FakeModel:
    """OpenAI-compatible loopback provider: /v1/models (with live pricing) and /v1/chat/completions (JSON + SSE).

    Every chat request is logged IN FULL (all messages) so the scenarios can assert what a model was ever shown."""

    def __init__(self, port: int, api_key: str):
        self.port, self.api_key = port, api_key
        self.lock = threading.Lock()
        self.reset()
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _json(self, code, obj):
                data = json.dumps(obj, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                try:
                    if self.path.rstrip("/").endswith("/models"):
                        with outer.lock:
                            outer.models_gets += 1
                            rows = [{"id": m["id"], "object": "model", "owned_by": "jeff-proof-fake",
                                     "context_length": 32768,
                                     "pricing": {"prompt": str(m["prompt"]), "completion": str(m["completion"])}}
                                    for m in outer.models]
                        return self._json(200, {"object": "list", "data": rows})
                    self._json(404, {"error": {"message": "not found"}})
                except (BrokenPipeError, ConnectionError, OSError):
                    pass

            def do_POST(self):
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(n) or b"{}")
                    if not self.path.rstrip("/").endswith("/chat/completions"):
                        return self._json(404, {"error": {"message": "not found"}})
                    outer.chat(self, body)
                except (BrokenPipeError, ConnectionError, OSError):
                    pass

        self.httpd = QuietServer(("127.0.0.1", port), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def reset(self) -> None:
        with self.lock:
            self.models: list[dict] = [{"id": FREE_MODEL, "prompt": 0, "completion": 0}]
            self.requests: list[dict] = []
            self.models_gets = 0
            self.fail: dict | None = None
            self.slow_tags: dict[str, float] = {}
            self.auth_seen: set[str] = set()
            self.nonce_owner: dict[str, str] = {}

    # -- behaviour ---------------------------------------------------------------------------------------------------
    @staticmethod
    def _last_user(messages) -> str:
        last = ""
        for msg in messages or []:
            if isinstance(msg, dict) and msg.get("role") == "user":
                content = msg.get("content")
                last = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        return last

    @staticmethod
    def compose(last_user: str) -> str:
        found = NONCE_RE.findall(last_user)
        tag = found[-1] if found else ""
        if "Фрагменты:" in last_user and "[1]" in last_user:
            return ("Срок оплаты по договору — 14 календарных дней [1]. Это ответ для проверки связи"
                    + (f" {tag}." if tag else "."))
        if tag:
            return (f"Принято: {tag}. Это ответ для проверки связи, он намеренно чуть длиннее сорока "
                    "символов, чтобы поток шёл по частям и черновик был виден участнику.")
        return "Ок, принял. Это ответ для проверки связи без метки, он тоже длиннее сорока символов."

    def chat(self, handler: BaseHTTPRequestHandler, body: dict) -> None:
        messages = body.get("messages") or []
        model = str(body.get("model", ""))
        last_user = self._last_user(messages)
        stream = bool(body.get("stream"))
        auth = handler.headers.get("Authorization", "")
        rec = {"t": now(), "model": model, "stream": stream, "messages": messages, "last_user": last_user,
               "nonces": NONCE_RE.findall(last_user), "status": 200, "auth_ok": auth == f"Bearer {self.api_key}"}
        with self.lock:
            rec["i"] = len(self.requests)
            self.requests.append(rec)
            fail = self.fail
            applies = bool(fail and (fail.get("models") is None or model in fail["models"])
                           and (fail.get("times") is None or fail.get("used", 0) < fail["times"]))
            if applies:
                fail["used"] = fail.get("used", 0) + 1
        if applies:
            rec["status"] = fail["status"] if fail["mode"] == "http" else 200
            rec["failed_with"] = f'{fail["mode"]}:{fail["status"]}'
            err = {"error": {"message": fail.get("message", "provider failure"), "code": fail["status"]}}
            data = json.dumps(err, ensure_ascii=False).encode()
            handler.send_response(fail["status"] if fail["mode"] == "http" else 200)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(data)))
            handler.end_headers()
            handler.wfile.write(data)
            return
        delay = 0.0
        for tag in rec["nonces"]:
            parts = tag.split("-")
            delay = max(delay, float(self.slow_tags.get(parts[1], 0.0)) if len(parts) > 1 else 0.0)
        rec["delay"] = delay
        text = self.compose(last_user)
        if not stream:
            if delay:
                time.sleep(delay)
            payload = {"id": "jeffproof", "object": "chat.completion", "created": int(now()), "model": model,
                       "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                                    "finish_reason": "stop"}],
                       "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}}
            data = json.dumps(payload, ensure_ascii=False).encode()
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(data)))
            handler.end_headers()
            handler.wfile.write(data)
            return
        # SSE: a first piece of >=48 chars at once (so the product's draft preview appears), the rest paced over `delay`
        pieces = [text[:48]]
        rest = text[48:]
        step = max(1, (len(rest) + 4) // 5)
        pieces += [rest[i:i + step] for i in range(0, len(rest), step)] if rest else []
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.close_connection = True

        def frame(delta, finish=None, usage=None):
            chunk = {"id": "jeffproof", "object": "chat.completion.chunk", "created": int(now()), "model": model,
                     "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            if usage:
                chunk["usage"] = usage
            handler.wfile.write(b"data: " + json.dumps(chunk, ensure_ascii=False).encode() + b"\n\n")
            handler.wfile.flush()

        for index, piece in enumerate(pieces):
            if index and delay:
                time.sleep(delay / max(1, len(pieces) - 1))
            frame({"content": piece})
        frame({}, finish="stop")
        frame({}, usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30})
        handler.wfile.write(b"data: [DONE]\n\n")
        handler.wfile.flush()

    # -- queries --------------------------------------------------------------------------------------------------------
    def snapshot(self) -> list[dict]:
        with self.lock:
            return list(self.requests)

    @staticmethod
    def blob(rec: dict) -> str:
        return json.dumps(rec["messages"], ensure_ascii=False)

    def requests_with(self, needle: str) -> list[dict]:
        return [r for r in self.snapshot() if needle in r["last_user"]]

    def stop(self) -> None:
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:       # noqa: BLE001
            pass


# ------------------------------------------------------------------------------------------------ bundle handle
class Bundle:
    def __init__(self, path: str):
        self.dir = Path(path).resolve()
        self.py = self.dir / "runtime" / "python.exe"
        self.site = self.dir / "runtime" / "Lib" / "site-packages"
        manifest_path = self.dir / "MANIFEST.json"
        self.manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
        self.sha = str(self.manifest.get("source_sha", ""))
        build = read_json(self.site / "bcc" / "_build.json") or {}
        self.build_json_sha = str(build.get("source_sha", ""))
        self.build_dirty = build.get("source_dirty")

    def ok(self) -> bool:
        return self.py.is_file() and len(self.sha) == 40


class Scn:
    def __init__(self, name: str, title: str):
        self.name, self.title = name, title
        self.checks: list[dict] = []
        self.evidence: dict = {}
        self.gaps: list[str] = []
        self.defects: list[dict] = []
        self.notes: list[str] = []
        self.not_run: str = ""
        self.error: str = ""
        self.secs = 0.0

    def check(self, name: str, ok: bool, detail: str = "", *, required: bool = True) -> bool:
        self.checks.append({"name": name, "ok": bool(ok), "required": required, "detail": clip(detail, 600)})
        log(f"    [{'ok' if ok else 'FAIL'}] {self.name}.{name}" + (f" - {clip(detail, 160)}" if detail else ""))
        return bool(ok)

    def defect(self, title: str, severity: str, repro: str, fix: str) -> None:
        self.defects.append({"title": title, "severity": severity, "repro": repro, "smallest_fix": fix})

    @property
    def verdict(self) -> str:
        if self.not_run:
            return "NOT_RUN"
        if self.error:
            return "FAIL"
        return "PASS" if all(c["ok"] for c in self.checks if c["required"]) and self.checks else "FAIL"

    def as_dict(self) -> dict:
        return {"name": self.name, "title": self.title, "verdict": self.verdict, "secs": round(self.secs, 1),
                "checks": self.checks, "gaps": self.gaps, "defects": self.defects, "notes": self.notes,
                "evidence": self.evidence, "not_run_reason": self.not_run, "harness_error": self.error}


# ------------------------------------------------------------------------------------------------------- poller
class Poller:
    """One `pit start` process of the bundle (hard-killable, restartable)."""

    def __init__(self, proof: "Proof", data_dir: Path, label: str, extra_env: dict | None = None):
        self.proof, self.data_dir, self.label = proof, Path(data_dir), label
        self.extra_env = extra_env or {}
        self.popen: subprocess.Popen | None = None
        self.starts = 0
        self.lock_refusals = 0
        self.ready_s: float | None = None
        self.exit_codes: list[int] = []

    @property
    def pid(self) -> int | None:
        return self.popen.pid if self.popen else None

    @property
    def heartbeat_path(self) -> Path:
        return self.data_dir / "pit-v1.7" / "heartbeat.json"

    def heartbeat(self) -> dict | None:
        return read_json(self.heartbeat_path)

    def start(self, ready_timeout: float = 120.0, retries: int = 6) -> bool:
        argv = [str(self.proof.bundle.py), "-I", "-X", "utf8", "-m", "bcc.pit.cli", "start",
                "--data-dir", str(self.data_dir)]
        env = self.proof.env(self.data_dir, seam=True, extra=self.extra_env)
        for attempt in range(retries):
            logf = (self.data_dir.parent / f"poller-{self.label}.log").open("ab")
            self.starts += 1
            t0 = time.time()
            self.popen = subprocess.Popen(argv, env=env, cwd=str(self.proof.root), stdin=subprocess.DEVNULL,
                                          stdout=logf, stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW)
            logf.close()
            self.proof.track(self.popen)
            while time.time() - t0 < ready_timeout:
                code = self.popen.poll()
                if code is not None:
                    break
                hb = self.heartbeat()
                if (hb and hb.get("pid") == self.popen.pid and (hb.get("telegram") or {}).get("last_poll_ok_at")):
                    self.ready_s = round(time.time() - t0, 1)
                    return True
                time.sleep(0.3)
            code = self.popen.poll()
            if code is None:
                kill_tree(self.popen.pid)
                return False
            self.exit_codes.append(code)
            if code == 3 and attempt < retries - 1:       # the OS frees a killed holder's lock a moment later
                self.lock_refusals += 1
                time.sleep(1.5)
                continue
            return False
        return False

    def alive(self) -> bool:
        return self.popen is not None and self.popen.poll() is None

    def kill(self) -> None:
        if self.popen is not None:
            kill_tree(self.popen.pid)
            try:
                self.popen.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass

    def stop_clean(self, timeout: float = 40.0) -> str:
        """Product STOP (stop.flag via `pit stop`), then escalate; returns how it ended."""
        if not self.alive():
            return "already_exited"
        self.proof.cli(self.data_dir, "stop", timeout=60)
        try:
            self.popen.wait(timeout=timeout)
            return f"clean_exit_{self.popen.returncode}"
        except subprocess.TimeoutExpired:
            self.kill()
            return "killed_after_stop_timeout"


# --------------------------------------------------------------------------------------------------------- proof
class Proof:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.bundle = Bundle(args.bundle)
        self.control = Bundle(args.control_bundle) if args.control_bundle else None
        self.out = Path(args.out).resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        self.root = Path(args.root).resolve() if args.root else (self.out / "work")
        # a throwaway root must be FRESH: persisted offsets/configs of an earlier run would silently change results
        marker = self.root / ".jeff-proof-root"
        if self.root.exists():
            if marker.is_file():
                def _force(func, path, exc_info):          # owner-only ACLs / read-only files on Windows
                    try:
                        os.chmod(path, 0o700)
                        func(path)
                    except OSError:
                        pass
                shutil.rmtree(self.root, onerror=_force)
            elif any(self.root.iterdir()):
                raise SystemExit(f"refusing to reuse a non-empty root that this script did not create: {self.root}")
        self.root.mkdir(parents=True, exist_ok=True)
        marker.write_text("created by tools/jeff_bundle_proof.py; safe to delete\n", encoding="utf-8")
        for sub in ("home", "locks", "no-companion"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        self.t_start = time.time()
        self.procs: list[subprocess.Popen] = []
        self.pollers: list[Poller] = []
        self.seam_path = self.bundle.site / SEAM_NAME
        self.seam_sha = ""
        self.seam_installed = False
        self.seam_honoured = None
        self.scenarios: list[Scn] = []
        ports = free_ports(5)
        self.p_backend, self.p_backend_ctl, self.p_tg, self.p_model, _ = ports
        self.tg = FakeTelegram(self.p_tg, FAKE_TOKEN)
        self.model = FakeModel(self.p_model, PROVIDER_KEY)
        self.rng = random.Random(int(time.time()))
        self._nonce_used: set[str] = set()
        self.baseline_pollers = jeff_pollers_like_product()
        atexit.register(self.cleanup)

    # -- environment ---------------------------------------------------------------------------------------------------
    def env(self, data_dir: Path | None, *, seam: bool = False, extra: dict | None = None,
            bundle: Bundle | None = None) -> dict[str, str]:
        bundle = bundle or self.bundle
        env = {k: os.environ[k] for k in WHITELIST if k in os.environ}
        home = self.root / "home"
        env.update({
            "LOCALAPPDATA": str(home / "localappdata"), "APPDATA": str(home / "appdata"),
            "USERPROFILE": str(home), "TEMP": str(home / "temp"), "TMP": str(home / "temp"),
            "BOSSMAN_TELEGRAM_POLLER_LOCK_DIR": str(self.root / "locks"),
            "BOSSMAN_COMPANION_CONFIG": str(self.root / "no-companion" / "config.json"),
            "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "NoDefaultCurrentDirectoryInExePath": "1",
        })
        for name in ("localappdata", "appdata", "temp"):
            (home / name).mkdir(parents=True, exist_ok=True)
        if data_dir is not None:
            env["BCC_DATA_DIR"] = str(data_dir)
            env["BOSSMAN_DATA_DIR"] = str(data_dir)
        env["PATH"] = f"{bundle.dir / 'runtime' / 'Scripts'};{bundle.dir / 'media'};{env.get('PATH', '')}"
        if seam:
            env["JEFF_PROOF_FAKE_TG_URL"] = self.tg.url()
        env.update(extra or {})
        return env

    def track(self, popen: subprocess.Popen) -> None:
        self.procs.append(popen)

    def run_capture(self, argv: list[str], env: dict, timeout: float) -> tuple[int, str, str]:
        try:
            cp = subprocess.run(argv, env=env, cwd=str(self.root), capture_output=True, timeout=timeout,
                                stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired as exc:
            return 124, (exc.stdout or b"").decode("utf-8", "replace"), "TIMEOUT"
        return cp.returncode, cp.stdout.decode("utf-8", "replace"), cp.stderr.decode("utf-8", "replace")

    def cli(self, data_dir: Path, command: str, *extra: str, timeout: float = 180.0, bundle: Bundle | None = None,
            seam: bool = True, env_extra: dict | None = None) -> tuple[int, str, str]:
        bundle = bundle or self.bundle
        argv = [str(bundle.py), "-I", "-X", "utf8", "-m", "bcc.pit.cli", command, *extra, "--data-dir", str(data_dir)]
        return self.run_capture(argv, self.env(data_dir, seam=seam, extra=env_extra, bundle=bundle), timeout)

    def helper(self, op: dict, data_dir: Path | None = None, bundle: Bundle | None = None) -> dict:
        bundle = bundle or self.bundle
        argv = [str(bundle.py), "-I", "-X", "utf8", "-c", HELPER, json.dumps(op)]
        rc, out, err = self.run_capture(argv, self.env(data_dir, bundle=bundle), 180)
        lines = [ln for ln in out.splitlines() if ln.strip().startswith("{")]
        if rc != 0 or not lines:
            raise RuntimeError(f"helper {op.get('op')} failed rc={rc}: {clip(err or out, 400)}")
        return json.loads(lines[-1])

    def start_backend(self, bundle: Bundle, data_dir: Path, port: int, timeout: float = 120.0):
        data_dir.mkdir(parents=True, exist_ok=True)
        argv = [str(bundle.py), "-I", "-X", "utf8", "-m", "bcc", "--host", "127.0.0.1", "--port", str(port)]
        logf = (data_dir.parent / f"backend-{port}.log").open("ab")
        popen = subprocess.Popen(argv, env=self.env(data_dir, bundle=bundle), cwd=str(self.root),
                                 stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT,
                                 creationflags=CREATE_NO_WINDOW)
        logf.close()
        self.track(popen)
        t0 = time.time()
        ident = wait_until(lambda: http_json(f"http://127.0.0.1:{port}/api/identity", 2) if popen.poll() is None
                           else {"_exited": popen.returncode}, timeout, 0.5)
        return popen, ident, round(time.time() - t0, 1)

    # -- seam -------------------------------------------------------------------------------------------------------------
    def install_seam(self) -> None:
        if self.seam_path.exists():
            raise SystemExit(f"refusing to overwrite an existing {self.seam_path}")
        self.seam_path.write_text(SEAM_SOURCE, encoding="utf-8", newline="\n")
        self.seam_installed = True
        self.seam_sha = sha256_file(self.seam_path)
        # honoured under -I ? (and inert without the env var?)
        probe = "import bcc.telegram_companion.adapters as a, json; print(json.dumps({'api': a.TELEGRAM_API, 'ddg': a.DDG_HTML}))"
        argv = [str(self.bundle.py), "-I", "-X", "utf8", "-c", probe]
        rc1, out1, err1 = self.run_capture(argv, self.env(None, seam=True), 90)
        rc2, out2, err2 = self.run_capture(argv, self.env(None, seam=False), 90)
        try:
            with_env = json.loads(out1.strip().splitlines()[-1])
            without = json.loads(out2.strip().splitlines()[-1])
        except (ValueError, IndexError):
            with_env, without = {"error": clip(err1 or out1)}, {"error": clip(err2 or out2)}
        self.seam_honoured = (with_env.get("api") == self.tg.url() and without.get("api") == "https://api.telegram.org")
        self.seam_probe = {"with_env": with_env, "without_env": without}

    def remove_seam(self) -> None:
        if not self.seam_installed:
            return
        for path in (self.seam_path, self.bundle.site / "__pycache__"):
            try:
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    for pyc in path.glob("sitecustomize*.pyc"):
                        pyc.unlink()
            except OSError:
                pass
        self.seam_installed = False

    # -- cleanup --------------------------------------------------------------------------------------------------------------
    def cleanup(self) -> None:
        for poller in self.pollers:
            try:
                if poller.alive():
                    poller.stop_clean(timeout=25)
            except Exception:       # noqa: BLE001
                pass
        for popen in self.procs:
            try:
                if popen.poll() is None:
                    kill_tree(popen.pid)
            except Exception:       # noqa: BLE001
                pass
        marker = str(self.root).lower()
        mine = {self.p_backend, self.p_backend_ctl}
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmd = " ".join(proc.info["cmdline"] or []).lower()
            except psutil.Error:
                continue
            if marker in cmd and proc.info["pid"] != os.getpid():
                kill_tree(proc.info["pid"])
        for port in mine:
            for pid in listening_pids(port):
                kill_tree(pid)
        self.remove_seam()
        for srv in (getattr(self, "tg", None), getattr(self, "model", None)):
            if srv is not None:
                srv.stop()

    def leftovers(self) -> dict:
        marker = str(self.root).lower()
        rows = []
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmd = " ".join(proc.info["cmdline"] or [])
            except psutil.Error:
                continue
            if marker in cmd.lower() and proc.info["pid"] != os.getpid():
                rows.append({"pid": proc.info["pid"], "cmd": clip(cmd, 160)})
        return {"processes_with_root_in_cmdline": rows,
                "listening": {str(p): listening_pids(p) for p in (self.p_backend, self.p_backend_ctl)},
                "seam_file_present": self.seam_path.exists(),
                "seam_pyc_present": any((self.bundle.site / "__pycache__").glob("sitecustomize*.pyc"))
                if (self.bundle.site / "__pycache__").is_dir() else False}

    # -- generic scenario helpers -----------------------------------------------------------------------------------------------
    def nonce(self, tag: str) -> str:
        while True:
            value = f"JP-{tag}-" + "".join(self.rng.choice("BCDFGHJKLMNPQRSTVWXZ") for _ in range(6))
            if value not in self._nonce_used:
                self._nonce_used.add(value)
                return value

    def scn_dir(self, name: str) -> Path:
        path = self.root / name / "data"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def setup_config(self, data_dir: Path, people: list[dict], *, chat_models: list[str] | None = None,
                     core_url: str | None = None, consents: list[dict] | None = None,
                     allowlist_open: bool = True) -> dict:
        # allowlist_open=True is what the interactive `pit setup` writes (the owner's real shape);
        # False is the library default / strict allowlist and is exercised explicitly where it matters.
        op = {"op": "setup", "data_dir": str(data_dir), "people": people, "allowlist_open": allowlist_open,
              "chat_models": chat_models or [FREE_MODEL],
              "provider_base_url": f"http://127.0.0.1:{self.p_model}/v1",
              "core_url": core_url or f"http://127.0.0.1:{self.p_backend}",
              "bot_token": FAKE_TOKEN, "provider_key": PROVIDER_KEY}
        info = self.helper(op, data_dir)
        if consents:
            info = self.helper({"op": "consent", "data_dir": str(data_dir), "consents": consents,
                                "users": [u["user_id"] for u in people]}, data_dir)
        return info

    def new_poller(self, data_dir: Path, label: str, extra_env: dict | None = None) -> Poller:
        poller = Poller(self, data_dir, label, extra_env)
        self.pollers.append(poller)
        return poller

    def reset_fakes(self) -> None:
        self.tg.reset()
        self.model.reset()

    @staticmethod
    def inbox(data_dir: Path) -> list[dict]:
        import sqlite3
        db_path = data_dir / "pit-v1.7" / "companion.sqlite3"
        if not db_path.is_file():
            return []
        for _ in range(5):
            try:
                db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
                rows = db.execute("SELECT id, who, phase FROM inbox ORDER BY id").fetchall()
                db.close()
                return [{"id": r[0], "who": r[1], "phase": r[2]} for r in rows]
            except sqlite3.Error:
                time.sleep(0.2)
        return []

    @staticmethod
    def product_logs(data_dir: Path) -> dict:
        logs = data_dir / "pit-v1.7" / "logs"
        out: dict = {}
        if not logs.is_dir():
            return out
        for path in sorted(logs.glob("*.jsonl")):
            try:
                lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            except OSError:
                continue
            entry: dict = {"lines": len(lines)}
            if path.name == "runtime_error.jsonl":
                entry["rows"] = [json.loads(ln) for ln in lines[-5:] if ln.startswith("{")]
            out[path.name] = entry
        return out

    def say(self, user_id: int, text: str, *, expect: str | None = None, timeout: float = 75.0,
            starts: str | None = None) -> tuple[int, dict | None, float]:
        """Inject one message, wait for the product's final reply; returns (update_id, row, seconds)."""
        mark = self.tg.mark()
        t0 = time.time()
        uid = self.tg.inject_text(user_id, text)
        row = self.tg.wait_reply(user_id, mark, contains=expect, timeout=timeout, starts=starts)
        return uid, row, round(time.time() - t0, 2)

    # =============================================================================================================
    # S1 build identity
    # =============================================================================================================
    def s1(self, s: Scn) -> None:
        bundle = self.bundle
        s.check("manifest_sha_equals_installed_build_json", bundle.sha == bundle.build_json_sha and len(bundle.sha) == 40,
                f"MANIFEST {bundle.sha[:12]} / bcc/_build.json {bundle.build_json_sha[:12]} dirty={bundle.build_dirty}")
        env_chk = self.helper({"op": "env_check"}, None)
        inside = str(env_chk.get("bcc", "")).lower().startswith(str(bundle.dir).lower())
        repo_on_path = [p for p in env_chk.get("sys_path", []) if "wt-unified" in p.lower() or "command-center" in p.lower()]
        s.check("code_resolves_inside_bundle_repo_not_on_path", inside and not repo_on_path and env_chk.get("isolated") == 1,
                f"bcc={env_chk.get('bcc')} isolated={env_chk.get('isolated')} repo_on_path={repo_on_path}")
        s.evidence["env_check"] = {k: env_chk.get(k) for k in ("bcc", "bossman_shared", "cli", "isolated")}
        s.evidence["sys_path"] = env_chk.get("sys_path")
        base = [r for r in self.baseline_pollers]
        s.evidence["host_pollers_before_run"] = base

        d = self.scn_dir("s1")
        popen, ident, secs = self.start_backend(bundle, d, self.p_backend)
        s.evidence["backend_start_secs"] = secs
        s.evidence["backend_identity"] = ident
        if not ident or ident.get("_exited") is not None or "build_sha" not in ident:
            s.check("backend_started", False, f"identity={ident}")
            return
        s.check("backend_identity_build_sha_equals_manifest", ident.get("build_sha") == bundle.sha
                and ident.get("source_identity") == "PASS" and ident.get("source") == "installed_build",
                f"build_sha={str(ident.get('build_sha'))[:12]} source={ident.get('source')} identity={ident.get('source_identity')}")
        self.setup_config(d, [{"user_id": 910001, "role": "owner"}], core_url=f"http://127.0.0.1:{self.p_backend}")
        t0 = time.time()
        rc, out, err = self.cli(d, "doctor")
        s.evidence["doctor_secs"] = round(time.time() - t0, 1)
        try:
            doc = json.loads(out)
        except ValueError:
            s.check("doctor_json_parsed", False, f"rc={rc} out={clip(out)} err={clip(err)}")
            return
        rows = {r["check"]: r for r in doc.get("checks", [])}
        s.evidence["doctor_rc"] = rc
        s.evidence["doctor_ok"] = doc.get("ok")
        s.evidence["doctor_rows"] = {k: {"status": v.get("status"), "detail": v.get("detail")} for k, v in rows.items()}
        own, bi, pl = rows.get("own_build", {}), rows.get("backend_identity", {}), rows.get("pollers", {})
        s.check("doctor_own_build_equals_manifest", own.get("status") == "PASS" and bundle.sha[:12] in own.get("detail", ""),
                f"{own.get('status')}: {own.get('detail')}")
        s.check("doctor_backend_identity_ok", bi.get("status") == "PASS" and bundle.sha[:12] in bi.get("detail", ""),
                f"{bi.get('status')}: {bi.get('detail')}")
        if "Jeff 1.1" in own.get("detail", ""):
            s.notes.append("the product's own version string (bcc/pit/version.py JEFF_VERSION, shown by doctor and the "
                           "heartbeat) is '1.1'; the Jeff 2.0 module layer is bcc.pit.j2 (schema jeff.j2/1) - nothing in "
                           "doctor says '2.0'.")
        count = len(base)
        s.check("doctor_pollers_at_most_one", pl.get("status") == "PASS" and count <= 1,
                f"{pl.get('status')}: {pl.get('detail')} (host pollers at start: {count})")
        for name in ("scheduled_tasks", "scheduler_build"):
            if name in rows:
                s.notes.append(f"doctor row {name} = {rows[name].get('status')} ({clip(rows[name].get('detail',''), 160)}): "
                               "read-only `schtasks /Query` of the host's REAL scheduler, not part of the build-identity criteria")
        rc2, out2, _ = self.cli(d, "status")
        try:
            st = json.loads(out2)
        except ValueError:
            st = {}
        s.check("pit_status_build_sha_equals_manifest", st.get("build_sha") == bundle.sha,
                f"status.build_sha={str(st.get('build_sha'))[:12]} rc={rc2}")

        # ---- negative control: Jeff (this bundle) against a backend of the OTHER build, same data dir fingerprint
        if self.control is None or not self.control.ok():
            s.notes.append("negative control skipped: control bundle missing")
            s.check("negative_control_ran", False, "no control bundle")
            return
        if self.control.sha == bundle.sha:
            s.check("negative_control_is_a_different_build", False, "control build equals the bundle build")
            return
        dc = self.scn_dir("s1c")
        cpop, cident, csecs = self.start_backend(self.control, dc, self.p_backend_ctl)
        s.evidence["control_backend_identity"] = cident
        if not cident or "build_sha" not in cident:
            s.check("control_backend_started", False, f"identity={cident}")
            return
        s.check("control_backend_is_other_build", cident.get("build_sha") == self.control.sha != bundle.sha,
                f"control backend {str(cident.get('build_sha'))[:12]} vs bundle {bundle.sha[:12]}")
        self.setup_config(dc, [{"user_id": 910001, "role": "owner"}], core_url=f"http://127.0.0.1:{self.p_backend_ctl}")
        rcn, outn, errn = self.cli(dc, "doctor")
        try:
            docn = json.loads(outn)
        except ValueError:
            s.check("control_doctor_json_parsed", False, f"rc={rcn} out={clip(outn)} err={clip(errn)}")
            return
        rown = {r["check"]: r for r in docn.get("checks", [])}
        s.evidence["control_doctor_rows"] = {k: {"status": v.get("status"), "detail": v.get("detail"),
                                                 "remedy": v.get("remedy")} for k, v in rown.items()}
        bin_ = rown.get("backend_identity", {})
        s.check("control_backend_identity_blocked_on_build_mismatch",
                bin_.get("status") in {"BLOCKED", "FAIL"} and "mismatch" in bin_.get("detail", "")
                and bundle.sha[:12] in bin_.get("detail", "") and self.control.sha[:12] in bin_.get("detail", "")
                and docn.get("ok") is False and rcn == 2,
                f"{bin_.get('status')}: {bin_.get('detail')} (doctor ok={docn.get('ok')} rc={rcn})")
        s.check("control_own_build_still_pass", rown.get("own_build", {}).get("status") == "PASS",
                str(rown.get("own_build")))
        # ---- negative control 2: nothing listens on core_url
        kill_tree(cpop.pid)
        wait_until(lambda: not listening_pids(self.p_backend_ctl), 20)
        rcd, outd, _ = self.cli(dc, "doctor")
        try:
            rowd = {r["check"]: r for r in json.loads(outd).get("checks", [])}
        except ValueError:
            rowd = {}
        s.check("dead_backend_is_fail_not_pass", rowd.get("backend_identity", {}).get("status") == "FAIL",
                str(rowd.get("backend_identity")))
        kill_tree(popen.pid)

    # =============================================================================================================
    # S2 one poller + durable queue
    # =============================================================================================================
    def s2(self, s: Scn) -> None:
        self.reset_fakes()
        self.model.slow_tags = {"SLOW": 6.0}
        d = self.scn_dir("s2")
        U = 920001
        self.setup_config(d, [{"user_id": U, "role": "owner"}])
        p = self.new_poller(d, "a")
        t0 = time.time()
        ok = p.start()
        s.check("first_heartbeat", ok, f"ready in {p.ready_s}s exit_codes={p.exit_codes} lock_refusals={p.lock_refusals}")
        if not ok:
            return
        hb = p.heartbeat() or {}
        s.evidence["first_heartbeat"] = {k: hb.get(k) for k in ("state", "pid", "build_sha", "jeff_version", "queue",
                                                              "uptime_s", "telegram")}
        s.check("heartbeat_pid_and_build", hb.get("pid") == p.pid and hb.get("build_sha") == self.bundle.sha,
                f"pid {hb.get('pid')}=={p.pid} build {str(hb.get('build_sha'))[:12]}")
        j2 = wait_until(lambda: read_json(d / "pit-v1.7" / "j2-status.json"), 40, 1.0) or {}
        modules = {m.get("name"): bool(m.get("switched_on")) for m in j2.get("modules", []) if isinstance(m, dict)}
        wanted = {"safety", "model_guard", "director", "memory_palace", "persona", "research", "media", "proactive",
                  "quality_lab", "insights", "longtask"}
        s.evidence["jeff2_modules_loaded_in_bundle"] = modules
        s.check("jeff2_module_layer_loaded_in_built_bundle", wanted <= set(modules) and all(modules[m] for m in wanted),
                f"modules={sorted(modules)} missing={sorted(wanted - set(modules))}")
        # /start and an echoed nonce
        _, row, secs = self.say(U, "/start", starts=INTRO_PREFIX, timeout=60)
        s.check("start_gets_intro", row is not None, clip(row["visible"]) if row else "no reply")
        n_ping = self.nonce("PING")
        _, row, secs = self.say(U, f"Привет, это проверка связи {n_ping}. Ответь, пожалуйста.", expect=n_ping)
        s.check("ping_gets_echoed_nonce", row is not None, f"{secs}s " + (clip(row["visible"]) if row else "no reply"))
        s.evidence["ping_latency_s"] = secs
        # second start: refused (exit 3), exactly one poller of this data dir
        rc, out, err = self.cli(d, "start", timeout=90)
        s.check("second_start_refused_exit_3", rc == 3, f"rc={rc} stderr={clip(err or out)}")
        alive = pollers_for(d)
        s.check("only_one_poller_process", alive == [p.pid], f"pollers of this data dir: {alive} (first={p.pid})")
        # same token, different data dir: refused by the per-token lock
        d2 = self.scn_dir("s2b")
        self.setup_config(d2, [{"user_id": U, "role": "owner"}])
        rc3, out3, err3 = self.cli(d2, "start", timeout=90)
        s.check("same_token_other_data_dir_refused_exit_3", rc3 == 3, f"rc={rc3} stderr={clip(err3 or out3)}")
        s.evidence["refusal_texts"] = {"same_data_dir": clip(err or out, 160), "same_token": clip(err3 or out3, 160)}

        # ---- in-flight kill with a slow (6 s, streamed) model -------------------------------------------------------
        nonces = [self.nonce("SLOW") for _ in range(3)]
        mark = self.tg.mark()
        uids = [self.tg.inject_text(U, f"Это сообщение номер {i + 1} для проверки очереди {n}. Ответь, пожалуйста.")
                for i, n in enumerate(nonces)]
        first_in_flight = wait_until(lambda: self.model.requests_with(nonces[0]), 45)
        three_ingested = wait_until(lambda: len([r for r in self.inbox(d) if r["id"] in uids]) == 3, 30)
        time.sleep(1.5)                                     # the first one is now in flight and streaming
        t_kill = time.time()
        kill_inbox = {r["id"]: r["phase"] for r in self.inbox(d) if r["id"] in uids}
        p.kill()
        s.evidence["at_kill"] = {"first_request_seen": bool(first_in_flight), "all_three_ingested": bool(three_ingested),
                                 "inbox_phases": {nonces[uids.index(k)]: v for k, v in kill_inbox.items()},
                                 "bot_messages_since": [clip(r["visible"], 120) for r in self.tg.rows_after(mark, U)]}
        s.check("inflight_state_as_designed_at_kill",
                kill_inbox.get(uids[0]) == "processing" and all(kill_inbox.get(u) == "pending" for u in uids[1:]),
                f"phases at kill: {kill_inbox}")
        post_kill_mark = self.tg.mark()
        time.sleep(1.0)
        p2 = self.new_poller(d, "b")
        ok2 = p2.start()
        s.check("restart_ready", ok2, f"ready in {p2.ready_s}s exit_codes={p2.exit_codes} lock_refusals={p2.lock_refusals}")
        s.evidence["restart"] = {"ready_s": p2.ready_s, "lock_refusals_after_kill": p2.lock_refusals + p.lock_refusals}
        if not ok2:
            return
        got2 = wait_until(lambda: self.tg.answers(nonces[1], U) and self.tg.answers(nonces[2], U), 90)
        time.sleep(8)                                       # let any duplicate show up
        final_inbox = {r["id"]: r["phase"] for r in self.inbox(d) if r["id"] in uids}
        per = {}
        for i, n in enumerate(nonces):
            per[n] = {"model_requests": len(self.model.requests_with(n)),
                      "complete_answers": len(self.tg.answers(n, U)),
                      "stuck_drafts": [clip(r["visible"], 100) for r in self.tg.drafts(n, U)],
                      "inbox_phase_final": final_inbox.get(uids[i])}
        s.evidence["per_message"] = per
        s.check("no_message_answered_twice", all(v["complete_answers"] <= 1 for v in per.values()),
                json.dumps({k: v["complete_answers"] for k, v in per.items()}))
        s.check("pending_messages_answered_exactly_once",
                bool(got2) and per[nonces[1]]["complete_answers"] == 1 and per[nonces[2]]["complete_answers"] == 1,
                f"m2={per[nonces[1]]['complete_answers']} m3={per[nonces[2]]['complete_answers']}")
        s.check("pending_messages_not_sent_to_model_twice",
                per[nonces[1]]["model_requests"] == 1 and per[nonces[2]]["model_requests"] == 1,
                f"model requests m2={per[nonces[1]]['model_requests']} m3={per[nonces[2]]['model_requests']}")
        m1 = per[nonces[0]]
        after = self.tg.rows_after(post_kill_mark, U)
        extra = [r for r in after if not any(n in r["visible"] for n in nonces[1:])]
        told = [clip(r["visible"], 160) for r in extra if not r["deleted"]]
        s.evidence["in_flight_message"] = {
            "nonce": nonces[0], "inbox_phase_after_restart": m1["inbox_phase_final"],
            "model_requests": m1["model_requests"], "complete_answers": m1["complete_answers"],
            "stuck_drafts_visible_to_participant": m1["stuck_drafts"],
            "messages_sent_after_restart_that_are_not_answers_to_m2_m3": told}
        s.check("inflight_marked_interrupted_unknown_and_not_replayed",
                m1["inbox_phase_final"] == "interrupted_unknown" and m1["model_requests"] == 1
                and m1["complete_answers"] == 0,
                f"phase={m1['inbox_phase_final']} model_requests={m1['model_requests']} answers={m1['complete_answers']}")
        if not told:
            s.gaps.append(
                "IN-FLIGHT MESSAGE IS DROPPED SILENTLY: after a hard kill the message that was being answered is marked "
                "interrupted_unknown, never replayed (by design) and the participant is told NOTHING - no apology, no "
                "'please resend'. " + (f"A half-streamed preview {m1['stuck_drafts']} stays in the chat with the cursor mark "
                                       "and is never completed or deleted." if m1["stuck_drafts"] else
                                       "(No preview was visible in this run.)"))
        else:
            s.notes.append("participant was told something after restart: " + json.dumps(told, ensure_ascii=False))
        s.evidence["product_logs"] = self.product_logs(d)
        s.evidence["model_requests_total"] = len(self.model.requests)
        p2.stop_clean()

    # =============================================================================================================
    # S3 memory + isolation
    # =============================================================================================================
    def s3(self, s: Scn) -> None:
        self.reset_fakes()
        d = self.scn_dir("s3")
        A, B = 930001, 930002
        info = self.setup_config(d, [{"user_id": A, "role": "owner"}, {"user_id": B, "role": "guest"}],
                                 consents=[{"user_id": A, "personalization": False}, {"user_id": B, "personalization": False}])
        MARK_A = "Qvortelium" + "".join(self.rng.choice("bcdfghjklmnpqrstvwxz") for _ in range(4))
        MARK_B = "Zyphrenia" + "".join(self.rng.choice("bcdfghjklmnpqrstvwxz") for _ in range(4))
        who_of: dict[str, str] = {}

        def n(tag: str, who: str) -> str:
            v = self.nonce(tag)
            who_of[v] = who
            return v

        def attribute(rec: dict) -> str:
            for v in rec["nonces"]:
                if v in who_of:
                    return who_of[v]
            return "?"

        p = self.new_poller(d, "a")
        if not p.start():
            s.check("poller_ready", False, f"exit_codes={p.exit_codes}")
            return
        steps: dict = {}

        def req_for(nonce: str) -> dict | None:
            found = self.model.requests_with(nonce)
            return found[-1] if found else None

        # 1. A states a unique fact (default consent: memory on, cloud personalization OFF)
        na1 = n("AMEM", "A")
        _, row, _ = self.say(A, f"Я люблю разводить кактусы сорта {MARK_A}. Просто к сведению: {na1}.", expect=na1)
        s.check("A_statement_answered", row is not None, clip(row["visible"]) if row else "no reply")
        stored = wait_until(lambda: self.helper({"op": "facts", "data_dir": str(d), "users": [A, B]}, d)
                            ["facts"][str(A)], 20, 1.0)
        s.check("A_fact_stored_in_A_vault", bool(stored) and any(MARK_A in str(v) for v in stored),
                f"facts of A: {stored}")
        # 2. default privacy: the cloud model must not receive A's memory yet
        na2 = n("AASK", "A")
        _, row, _ = self.say(A, f"Какие кактусы я люблю разводить? Проверка {na2}.", expect=na2)
        r = req_for(na2)
        s.check("default_privacy_cloud_gets_no_memory", r is not None and MARK_A not in self.model.blob(r),
                "request of A's question carries the marker" if r and MARK_A in self.model.blob(r)
                else "request had no marker (remote personalization off by default)")
        # 3. both participants opt in to cloud personalization through the product command
        for who, uid in (("A", A), ("B", B)):
            _, row, _ = self.say(uid, "/privacy personalization on", timeout=40)
            s.check(f"{who}_personalization_on", row is not None and "включ" in row["visible"].lower() or
                    (row is not None and "Разрешено" in row["visible"]), clip(row["visible"]) if row else "no reply")
        # 4. B has its own fact and asks for it
        nb1 = n("BMEM", "B")
        _, row, _ = self.say(B, f"Я люблю собирать марки сорта {MARK_B}. Просто к сведению: {nb1}.", expect=nb1)
        nb2 = n("BASK", "B")
        _, row, _ = self.say(B, f"Что я люблю собирать? Проверка {nb2}.", expect=nb2)
        rb2 = req_for(nb2)
        s.check("B_memory_used_for_B", rb2 is not None and MARK_B in self.model.blob(rb2),
                "B's request carries B's marker" if rb2 and MARK_B in self.model.blob(rb2) else "B's marker missing")
        # 5. A asks: memory must be used (cloud personalization is ON now)
        na3 = n("AASK", "A")
        _, row, _ = self.say(A, f"Какие кактусы я люблю разводить? Проверка {na3}.", expect=na3)
        ra3 = req_for(na3)
        in_persona = False
        if ra3:
            for m in ra3["messages"]:
                if m.get("role") == "system" and "Память ЭТОГО собеседника" in str(m.get("content", "")) \
                        and MARK_A in str(m.get("content", "")):
                    in_persona = True
        s.check("A_memory_used_after_opt_in", ra3 is not None and MARK_A in self.model.blob(ra3),
                f"marker in A's request; persona-memory system message holds it: {in_persona}")
        # 6. B asks something that would hit A's fact if it leaked
        nb3 = n("BASK", "B")
        _, row, _ = self.say(B, f"Какие кактусы я люблю разводить? Проверка {nb3}.", expect=nb3)
        # 7. A forgets the marker
        _, row, _ = self.say(A, f"/forget {MARK_A}", timeout=40)
        s.check("forget_command_acknowledged", row is not None and "Забыл" in row["visible"],
                clip(row["visible"]) if row else "no reply")
        facts_after = self.helper({"op": "facts", "data_dir": str(d), "users": [A]}, d)["facts"][str(A)]
        s.check("forget_removed_fact_from_vault", not any(MARK_A in str(v) for v in facts_after), f"facts of A: {facts_after}")
        na4 = n("AASK", "A")
        _, row, _ = self.say(A, f"Какие кактусы я люблю разводить? Проверка {na4}.", expect=na4)
        ra4 = req_for(na4)
        where: list[dict] = []
        if ra4:
            for idx, m in enumerate(ra4["messages"]):
                if MARK_A in str(m.get("content", "")):
                    where.append({"index": idx, "role": m.get("role"), "snippet": clip(str(m.get("content")), 150)})
        s.evidence["after_forget_marker_locations"] = where
        s.check("after_forget_next_request_of_A_has_no_marker", ra4 is not None and not where,
                "marker gone from the next request" if not where else
                f"marker STILL in the next request of A: {[(w['index'], w['role']) for w in where]}")
        if where:
            s.defect(
                "/forget removes the stored fact but the same text still goes to the model from the conversation history",
                "medium",
                "1) participant says 'Я люблю разводить кактусы сорта <X>'; 2) /privacy personalization on; "
                "3) /forget <X> -> product answers 'Забыл: удалено 1 факт(ов)'; 4) ask any question: the next model "
                f"request still contains <X> in {[(w['role'], w['index']) for w in where]} (PITStore.history()/learning_log "
                "are not touched by ParticipantRuntime._forget, which only rewrites facts.jsonl).",
                "In ParticipantRuntime._forget (bcc/pit/runtime.py) also purge the needle from the participant's own "
                "PITStore rows: DELETE/rewrite matching `history` and `learning_log` rows (+ `transcript`, + "
                "<person>/training.jsonl) after decrypting them with store.open(); e.g. add PITStore.scrub_text(who, needle).")
        # 8. restart Jeff and re-check isolation + forget persistence
        p.kill()
        time.sleep(1.0)
        p2 = self.new_poller(d, "b")
        if not p2.start():
            s.check("restart_ready", False, f"exit_codes={p2.exit_codes}")
            return
        nb4 = n("BASK", "B")
        _, row, _ = self.say(B, f"Какие кактусы я люблю разводить? Проверка {nb4}.", expect=nb4)
        nb5 = n("BASK", "B")
        _, row, _ = self.say(B, f"Что я люблю собирать? Проверка {nb5}.", expect=nb5)
        rb5 = req_for(nb5)
        s.check("B_memory_survives_restart", rb5 is not None and MARK_B in self.model.blob(rb5),
                "B's own marker is in B's post-restart request" if rb5 and MARK_B in self.model.blob(rb5)
                else "B's marker missing after restart")
        na5 = n("AASK", "A")
        _, row, _ = self.say(A, f"Какие кактусы я люблю разводить? Проверка {na5}.", expect=na5)
        ra5 = req_for(na5)
        s.evidence["after_restart_A_marker_present"] = bool(ra5 and MARK_A in self.model.blob(ra5))
        s.check("after_restart_forgotten_marker_stays_out_of_A_request", ra5 is not None and MARK_A not in self.model.blob(ra5),
                "clean" if ra5 and MARK_A not in self.model.blob(ra5) else "marker back in A's request (conversation history persists)",
                required=False)
        # ---- global assertions over EVERY model request ------------------------------------------------------------------
        reqs = self.model.snapshot()
        by_who = Counter(attribute(r) for r in reqs)
        s.evidence["model_requests_by_participant"] = dict(by_who)
        leak_to_b = [r["i"] for r in reqs if attribute(r) == "B" and (MARK_A in self.model.blob(r))]
        leak_nonce_to_b = [r["i"] for r in reqs if attribute(r) == "B"
                           and any(v in self.model.blob(r) for v, w in who_of.items() if w == "A")]
        leak_to_a = [r["i"] for r in reqs if attribute(r) == "A" and MARK_B in self.model.blob(r)]
        unattributed = [r["i"] for r in reqs if attribute(r) == "?"]
        s.check("B_never_gets_A_marker_in_any_request", not leak_to_b, f"requests of B carrying A's marker: {leak_to_b}")
        s.check("B_never_gets_A_history_in_any_request", not leak_nonce_to_b,
                f"requests of B carrying any A nonce: {leak_nonce_to_b}")
        s.check("A_never_gets_B_marker_in_any_request", not leak_to_a, f"requests of A carrying B's marker: {leak_to_a}")
        s.check("every_model_request_attributable_to_one_participant", not unattributed,
                f"unattributed request indexes: {unattributed}", required=False)
        s.check("isolation_after_restart_B_has_no_A_marker",
                (lambda r: r is not None and MARK_A not in self.model.blob(r))(req_for(nb4)), "B's post-restart request clean")
        grep = self.helper({"op": "grep", "data_dir": str(d), "needle": MARK_A, "users": [A, B]}, d)["hits"]
        s.evidence["vault_files_containing_A_marker"] = grep
        s.check("vault_isolation_on_disk_B_dir_has_no_A_marker", not grep.get(str(B)), f"B dir hits: {grep.get(str(B))}")
        s.check("A_vault_facts_file_holds_no_marker_after_forget", not [f for f in grep.get(str(A), []) if f.startswith("facts")],
                f"A dir hits: {grep.get(str(A))}")
        residue = [f for f in grep.get(str(A), []) if not f.startswith("facts")]
        s.check("A_dir_has_no_other_residue_of_the_forgotten_text", not residue,
                f"files of A that still hold the forgotten text: {residue}", required=False)
        if residue:
            s.notes.append("residue after /forget in A's own vault dir (not another participant's): "
                           + ", ".join(residue) + " - corrections.jsonl keeps the forget QUERY verbatim, "
                           "quality/scores.jsonl keeps a sample of the user message (j2 quality_lab).")
        s.evidence["markers"] = {"A": MARK_A, "B": MARK_B}
        s.evidence["product_logs"] = self.product_logs(d)
        p2.stop_clean()

    # =============================================================================================================
    # S4 free route + honest refusal
    # =============================================================================================================
    def _s4_run(self, s: Scn, label: str, d: Path, U: int, fail: dict | None, expect_names: set[str]) -> tuple[dict, Poller]:
        """Fresh poller (fresh in-process route state), one failure mode; returns evidence."""
        self.model.fail = dict(fail) if fail else None
        if self.model.fail:
            self.model.fail["used"] = 0
        p = self.new_poller(d, label)
        ok = p.start()
        ev: dict = {"started": ok}
        if not ok:
            ev["exit_codes"] = p.exit_codes
            return ev, p
        n1 = self.nonce("FAIL")
        before = len(self.model.snapshot())
        _, row, secs = self.say(U, f"Расскажи что-нибудь короткое. Проверка {n1}.", timeout=100,
                                expect=None)
        text = row["visible"] if row else ""
        ev["first"] = {"reply": clip(text, 200), "classified": classify_refusal(text), "secs": secs,
                       "model_requests": [r["model"] for r in self.model.snapshot()[before:]]}
        n2 = self.nonce("FAIL")
        before2 = len(self.model.snapshot())
        _, row2, secs2 = self.say(U, f"И ещё один короткий вопрос. Проверка {n2}.", timeout=100)
        text2 = row2["visible"] if row2 else ""
        ev["second"] = {"reply": clip(text2, 200), "classified": classify_refusal(text2), "secs": secs2,
                        "model_requests": [r["model"] for r in self.model.snapshot()[before2:]]}
        hb = p.heartbeat() or {}
        ev["route"] = (hb.get("route") or {}) if isinstance(hb.get("route"), dict) else {}
        return ev, p

    def s4(self, s: Scn) -> None:
        self.reset_fakes()
        self.model.models = [{"id": FREE_MODEL, "prompt": 0, "completion": 0},
                             {"id": PAID_MODEL, "prompt": 0.000001, "completion": 0.000002},
                             {"id": SNEAKY_MODEL, "prompt": 0.000003, "completion": 0.000004}]
        U = 940001
        all_models = [FREE_MODEL, PAID_MODEL, SNEAKY_MODEL]

        def fresh(label: str, models: list[str]) -> Path:
            # one data dir per mode: the cloud budget (model cooldowns) is persisted in the data dir on purpose
            dd = self.scn_dir(f"s4-{label}")
            self.setup_config(dd, [{"user_id": U, "role": "owner"}], chat_models=models,
                              consents=[{"user_id": U, "personalization": False}])
            return dd

        # -- healthy: only the free model is ever named
        d = fresh("ok", all_models)
        ev, p = self._s4_run(s, "ok", d, U, None, set())
        s.evidence["healthy"] = ev
        if not ev.get("started"):
            s.check("poller_ready", False, json.dumps(ev))
            return
        route = ev.get("route", {})
        s.check("catalog_verdicts_published", route.get("free_remote") == [FREE_MODEL]
                and route.get("rejected", {}).get(PAID_MODEL) == "price_positive"
                and route.get("rejected", {}).get(SNEAKY_MODEL) == "price_positive"
                and route.get("paid_routes_allowed") is False,
                json.dumps({k: route.get(k) for k in ("free_remote", "rejected", "paid_routes_allowed", "refusal")},
                           ensure_ascii=False))
        s.check("healthy_turn_answered_by_free_model", bool(ev["first"]["model_requests"]) and
                set(ev["first"]["model_requests"]) == {FREE_MODEL} and ev["first"]["classified"] is None
                and "Принято" in ev["first"]["reply"],
                json.dumps(ev["first"], ensure_ascii=False))
        p.stop_clean()
        # -- failure modes, each with a fresh process AND a fresh data dir (route blocks / cooldowns persist)
        modes = [
            ("http402", {"status": 402, "mode": "http", "message": "Insufficient credits", "models": None}),
            ("inbody402", {"status": 402, "mode": "in_body", "message": "Insufficient credits", "models": None}),
            ("http429", {"status": 429, "mode": "http", "message": "Rate limit exceeded: free-models-per-min",
                         "models": None}),
            ("http500", {"status": 500, "mode": "http", "message": "Internal error", "models": None}),
        ]
        expected_first = {"http402": {"PROVIDER_DOWN_RU"}, "inbody402": {"PROVIDER_DOWN_RU"},
                          "http429": {"CLOUD_PAUSED_RU"}, "http500": {"PROVIDER_DOWN_RU"}}
        results = {}
        for label, fail in modes:
            ev, p = self._s4_run(s, label, fresh(label, all_models), U, fail, set())
            results[label] = ev
            if not ev.get("started"):
                s.check(f"{label}_poller_ready", False, json.dumps(ev))
                continue
            first, second = ev["first"], ev["second"]
            honest = {"PROVIDER_DOWN_RU", "CLOUD_PAUSED_RU", "NO_MODEL_RU"}
            s.check(f"{label}_participant_gets_honest_refusal", first["classified"] in expected_first[label],
                    f"{first['classified']}: {first['reply']}")
            s.check(f"{label}_second_turn_also_honest", second["classified"] in honest,
                    f"{second['classified']}: {second['reply']}")
            s.check(f"{label}_no_fake_answer_leaked", "Принято" not in first["reply"] and "Принято" not in second["reply"],
                    "refusals carry no model text")
            p.stop_clean()
        s.evidence["failure_modes"] = results
        # -- only paid models configured: honest NO_MODEL, zero chat requests
        self.model.fail = None
        ev, p = self._s4_run(s, "paidonly", fresh("paidonly", [PAID_MODEL, SNEAKY_MODEL]), U, None, set())
        s.evidence["paid_only_config"] = ev
        if ev.get("started"):
            s.check("paid_only_config_refuses_without_any_request",
                    ev["first"]["classified"] == "NO_MODEL_RU" and not ev["first"]["model_requests"],
                    f"{ev['first']['classified']} requests={ev['first']['model_requests']}")
            p.stop_clean()
        else:
            s.check("paid_only_poller_ready", False, json.dumps(ev))
        # -- global: no request ever named a paid model
        named = Counter(r["model"] for r in self.model.snapshot())
        s.evidence["chat_requests_by_model"] = dict(named)
        s.evidence["provider_auth_header_ok"] = all(r["auth_ok"] for r in self.model.snapshot())
        s.check("no_request_ever_named_a_paid_model", named.get(PAID_MODEL, 0) == 0 and named.get(SNEAKY_MODEL, 0) == 0
                and set(named) <= {FREE_MODEL}, json.dumps(dict(named)))
        s.check("provider_requests_carry_the_configured_key", s.evidence["provider_auth_header_ok"], "", required=False)
        # -- observation: real HTTP 402 vs in-body 402 (is the route blocked for the process lifetime?)
        h402, b402 = results.get("http402", {}), results.get("inbody402", {})
        if h402.get("second") and b402.get("second"):
            h_blocked = not h402["second"]["model_requests"]
            b_blocked = not b402["second"]["model_requests"]
            s.evidence["payment_required_handling"] = {
                "http_402_second_turn_hit_provider_again": not h_blocked,
                "in_body_402_second_turn_hit_provider_again": not b_blocked,
                "http_402_second_reply": h402["second"]["classified"], "in_body_402_second_reply": b402["second"]["classified"]}
            s.check("http402_blocks_the_route_like_in_body_402", h_blocked == b_blocked and b_blocked,
                    f"HTTP 402: second turn hit the provider again = {not h_blocked}; "
                    f"in-body 402: second turn hit the provider again = {not b_blocked}")
            if b_blocked and not h_blocked:
                s.defect(
                    "a real HTTP 402 from the provider does not block the route; only an in-body 402 does",
                    "low",
                    "fake provider answers POST /chat/completions with HTTP 402 (as OpenRouter does for exhausted "
                    "credits): the second turn calls the provider again instead of marking the route payment-blocked, "
                    "while an HTTP-200 body {error:{code:402}} blocks it for PAYMENT_BLOCK_SECONDS. "
                    "bcc/providers.py::_explain() formats 402 as 'провайдер ответил 402' (no '(402)') while "
                    "bcc/pit/model_route.py::is_payment_required() looks for the literal '(402)'.",
                    "Add 402 to the `known` map of providers._explain(): 402: 'требуется оплата (402)' - or carry the "
                    "status code on ProviderError and test `exc.status_code == 402` in is_payment_required(). "
                    "No paid model is ever selected either way (the live 0/0 catalog gate still holds).")
        s.evidence["product_logs"] = self.product_logs(self.scn_dir("s4-ok"))

    # =============================================================================================================
    # S5 long task across restart
    # =============================================================================================================
    @staticmethod
    def task_files(d: Path) -> list[dict]:
        out = []
        root = d / "pit-v1.7" / "tasks"
        if not root.is_dir():
            return out
        for path in sorted(root.glob("*/*.json")):
            data = read_json(path)
            if data:
                out.append({"id": data.get("id"), "state": data.get("state"), "goal": clip(data.get("goal", ""), 100),
                            "actions": {k[:8]: {"kind": v.get("kind"), "status": v.get("status"),
                                                "attempts": v.get("attempts")} for k, v in (data.get("actions") or {}).items()},
                            "result_file": (path.parent / f"{data.get('id')}.result").is_file()})
        return out

    def s5(self, s: Scn) -> None:
        self.reset_fakes()
        self.model.slow_tags = {"LONGA": 8.0, "LONGB": 2.0}
        U = 950001
        d = self.scn_dir("s5")
        self.setup_config(d, [{"user_id": U, "role": "owner"}], consents=[{"user_id": U, "personalization": False}])
        p = self.new_poller(d, "a")
        if not p.start():
            s.check("poller_ready", False, f"exit_codes={p.exit_codes}")
            return
        # ---- 5a: killed while the model is working --------------------------------------------------------------------
        na = self.nonce("LONGA")
        _, row, _ = self.say(U, f"поставь задачу: составь краткий план из трёх шагов для проверки связи {na}",
                             starts="Поставил задачу", timeout=60)
        s.check("task_created_ack", row is not None, clip(row["visible"]) if row else "no reply")
        in_flight = wait_until(lambda: self.model.requests_with(na), 45)
        time.sleep(1.5)
        before_kill = self.task_files(d)
        p.kill()
        s.evidence["5a_at_kill"] = {"model_request_seen": bool(in_flight), "tasks": before_kill}
        s.check("5a_work_in_flight_at_kill", bool(in_flight) and before_kill and before_kill[0]["state"] == "RUNNING"
                and not self.tg.answers(na, U, prefix=TASK_DONE_PREFIX), json.dumps(before_kill))
        time.sleep(1.0)
        p2 = self.new_poller(d, "b")
        if not p2.start():
            s.check("5a_restart_ready", False, f"exit_codes={p2.exit_codes}")
            return
        delivered = wait_until(lambda: self.tg.answers(na, U, prefix=TASK_DONE_PREFIX), 120)
        time.sleep(6)
        count = len(self.tg.answers(na, U, prefix=TASK_DONE_PREFIX))
        tasks = self.task_files(d)
        s.evidence["5a_after"] = {"delivered_messages": count, "tasks": tasks,
                                  "model_requests_for_goal": len(self.model.requests_with(na))}
        s.check("5a_result_delivered_exactly_once_after_restart", bool(delivered) and count == 1, f"delivered={count}")
        s.check("5a_task_done", bool(tasks) and tasks[0]["state"] == "DONE", json.dumps(tasks[:1]))
        # ---- 5b: killed between 'delivery started' and 'delivery completed' ---------------------------------------------
        hold = self.tg.hold("sendMessage", TASK_DONE_PREFIX, 60.0)
        nb = self.nonce("LONGB")
        _, row, _ = self.say(U, f"поставь задачу: напиши два предложения о проверке доставки {nb}",
                             starts="Поставил задачу", timeout=60)
        s.check("5b_task_created_ack", row is not None, clip(row["visible"]) if row else "no reply")
        held = wait_until(lambda: hold["hits"] >= 1 and self.tg.answers(nb, U, prefix=TASK_DONE_PREFIX), 90)
        s.check("5b_delivery_started_and_held", bool(held), f"hold hits={hold['hits']}")
        time.sleep(1.0)
        mid = self.task_files(d)
        p2.kill()
        s.evidence["5b_at_kill"] = {"hold_hits": hold["hits"], "tasks": mid}
        received_before = len(self.tg.answers(nb, U, prefix=TASK_DONE_PREFIX))
        hold["seconds"] = 0.0                                # nothing else is ever held again
        time.sleep(1.0)
        p3 = self.new_poller(d, "c")
        if not p3.start():
            s.check("5b_restart_ready", False, f"exit_codes={p3.exit_codes}")
            return
        mark = self.tg.mark()
        time.sleep(15)                                       # resume loop runs at once; give it ample time
        tasks_b = self.task_files(d)
        unknown = [t for t in tasks_b if t["state"] == "UNKNOWN_OUTCOME"]
        total = len(self.tg.answers(nb, U, prefix=TASK_DONE_PREFIX))
        unsolicited = [clip(r["visible"], 120) for r in self.tg.rows_after(mark, U)]   # before the participant asks anything
        s.evidence["5b_after"] = {"tasks": tasks_b, "task_done_messages_total": total,
                                  "received_before_kill": received_before,
                                  "bot_messages_after_restart_before_any_question": unsolicited}
        s.check("5b_no_blind_resend", total == received_before == 1, f"'Задача готова' messages for the task: {total}")
        s.check("5b_task_is_unknown_outcome", len(unknown) == 1 and unknown[0]["result_file"], json.dumps(unknown))
        _, row, _ = self.say(U, "мои задачи", timeout=40)
        listing = row["visible"] if row else ""
        s.check("5b_listing_states_delivery_unconfirmed", "доставка не подтверждена" in listing, clip(listing, 300))
        _, row, _ = self.say(U, "результат задачи 2", timeout=40)
        s.check("5b_result_still_retrievable", row is not None and nb in row["visible"],
                clip(row["visible"]) if row else "no reply")
        if not unsolicited:
            s.gaps.append("UNKNOWN_OUTCOME IS NOT ANNOUNCED: after the restart the participant gets no message that the "
                          "delivery of a finished task was not confirmed; they learn it only by asking «мои задачи» "
                          "(by design: nothing is resent blindly).")
        s.evidence["product_logs"] = self.product_logs(d)
        p3.stop_clean()

        # ---- 5c: STRICT allowlist (allowlist_open=false, the library default): the allowlisted owner's task result -----
        self.model.slow_tags = {}
        ds = self.scn_dir("s5-strict")
        self.setup_config(ds, [{"user_id": U, "role": "owner"}], consents=[{"user_id": U, "personalization": False}],
                          allowlist_open=False)
        self.tg.reset()
        ps = self.new_poller(ds, "strict")
        if not ps.start():
            s.check("5c_strict_poller_ready", False, f"exit_codes={ps.exit_codes}")
            return
        nc = self.nonce("LONGC")
        _, row, _ = self.say(U, f"поставь задачу: напиши одно предложение о проверке связи {nc}",
                             starts="Поставил задачу", timeout=60)
        s.check("5c_strict_task_created_ack", row is not None, clip(row["visible"]) if row else "no reply")
        got = wait_until(lambda: self.tg.answers(nc, U, prefix=TASK_DONE_PREFIX), 45)
        time.sleep(3)
        strict_tasks = self.task_files(ds)
        ps.stop_clean()
        diag = {}
        try:
            diag = self.helper({"op": "sender_diag", "data_dir": str(ds), "users": [U]}, ds)
        except Exception as exc:      # noqa: BLE001
            diag = {"error": str(exc)[:300]}
        s.evidence["5c_strict_allowlist"] = {"delivered": len(self.tg.answers(nc, U, prefix=TASK_DONE_PREFIX)),
                                             "tasks": strict_tasks, "sender_diag": diag}
        s.check("5c_strict_allowlist_owner_receives_task_result", bool(got) and bool(strict_tasks)
                and strict_tasks[0]["state"] == "DONE",
                f"delivered={len(self.tg.answers(nc, U, prefix=TASK_DONE_PREFIX))} state="
                f"{strict_tasks[0]['state'] if strict_tasks else None} sender_exception={diag.get('sender_exception')}")
        if not got:
            s.defect(
                "closed allowlist: a finished long task is never delivered to the allowlisted owner "
                "(CompanionError IDENTITY_REVOKED -> task UNKNOWN_OUTCOME); proactive reminders share the sender",
                "medium",
                "config with allowlist_open=false (PITSettings default / save_setup default) and the owner as the FIRST "
                "person with role 'owner': send 'поставь задачу: ...'; the model works, then the deliver step raises "
                f"({diag.get('sender_exception')}) and the task ends UNKNOWN_OUTCOME with nothing sent. Cause: "
                "bcc/pit/j2/proactive.py::TelegramSender._resolve() appends Person(role='guest') for every key in "
                "runtime._spawned_workers AFTER the configured people (which include the owner), so the guest object "
                "overwrites the owner's entry; Telegram.send() then fails `person in settings.people` because the "
                f"transport list holds the first person with role 'owner' (resolved role={diag.get('resolved_role')}, "
                f"configured={diag.get('configured_roles')}). With allowlist_open=true (what `pit setup` writes) "
                "authorize_delivery short-circuits and delivery works.",
                "In TelegramSender._resolve keep the configured person: iterate spawned workers first and the configured "
                "people last, or use `people.setdefault(derive_person_key(p.user_id, salt), p)` with configured people "
                "first.")

    # =============================================================================================================
    # S6 crisis phrase
    # =============================================================================================================
    def s6(self, s: Scn) -> None:
        self.reset_fakes()
        U = 960001
        d = self.scn_dir("s6")
        self.setup_config(d, [{"user_id": U, "role": "owner"}], consents=[{"user_id": U, "personalization": False}])
        p = self.new_poller(d, "a")
        if not p.start():
            s.check("poller_ready", False, f"exit_codes={p.exit_codes}")
            return
        before = len(self.model.snapshot())
        marker = self.nonce("CRIS")
        _, row, _ = self.say(U, f"Я хочу покончить с собой. {marker}", timeout=40)
        reply = row["visible"] if row else ""
        s.evidence["crisis_reply"] = clip(reply, 400)
        s.check("crisis_gets_fixed_reply_with_resources", row is not None and "112" in reply and "8-800-2000-122" in reply
                and marker not in reply, clip(reply, 200))
        s.check("crisis_makes_no_model_call", len(self.model.snapshot()) == before,
                f"model requests during the crisis turn: {len(self.model.snapshot()) - before}")
        facts = self.helper({"op": "facts", "data_dir": str(d), "users": [U]}, d)["facts"][str(U)]
        s.check("crisis_text_not_stored_as_fact", not facts, f"facts: {facts}")
        crisis_log = d / "pit-v1.7" / "logs" / "crisis_audit.jsonl"
        audit_text = crisis_log.read_text(encoding="utf-8") if crisis_log.is_file() else ""
        s.check("crisis_audit_has_no_message_text", bool(audit_text) and marker not in audit_text and "покончить" not in audit_text,
                clip(audit_text, 200), required=False)
        # negative control: not a crisis -> a model answers
        n2 = self.nonce("CTRL")
        before2 = len(self.model.snapshot())
        _, row2, _ = self.say(U, f"Я не хочу жить в этом городе, хочу переехать. {n2}", expect=n2, timeout=60)
        s.check("non_crisis_sentence_goes_to_model", row2 is not None and len(self.model.snapshot()) > before2,
                clip(row2["visible"]) if row2 else "no reply")
        p.stop_clean()

    # =============================================================================================================
    # S7 paid documents (SIMULATED Bot API / Stars)
    # =============================================================================================================
    def s7(self, s: Scn) -> None:
        self.reset_fakes()
        s.notes.append("SIMULATED Bot API: invoices, pre-checkout queries and successful_payment updates come from a "
                       "loopback fake; no real Telegram Stars were involved.")
        OWNER, BUYER = 970000, 970001
        d = self.scn_dir("s7")
        # first person is the transport owner by construction; the buyer is an ordinary guest participant
        self.setup_config(d, [{"user_id": OWNER, "role": "owner"}, {"user_id": BUYER, "role": "guest"}],
                          consents=[{"user_id": OWNER}, {"user_id": BUYER}])
        p = self.new_poller(d, "a", extra_env={"BOSSMAN_JEFF_PAID_DOCS": "1"})
        if not p.start():
            s.check("poller_ready_with_paid_docs_flag", False, f"exit_codes={p.exit_codes}")
            return
        _, row, _ = self.say(BUYER, "/plan", timeout=40)
        plan = row["visible"] if row else ""
        s.check("plan_command_works_with_flag", "Помощник по документам" in plan and "⭐" in plan, clip(plan, 300))
        s.evidence["plan_text"] = clip(plan, 500)
        doc = ("Договор поставки. Срок оплаты по договору — 14 календарных дней с даты выставления счёта. "
               "Код проекта ZXQ-77. Штраф за просрочку — 0,1 процента в день.\n").encode("utf-8")
        self.tg.register_file("FILE-1", "documents/contract.txt", doc)
        mark = self.tg.mark()
        self.tg.inject_document(BUYER, "FILE-1", "contract.txt")
        row = self.tg.wait_reply(BUYER, mark, timeout=60)
        s.check("document_uploaded_through_getFile", row is not None and "сохранён" in row["visible"]
                and bool(self.tg.file_downloads), clip(row["visible"]) if row else "no reply")
        nq = self.nonce("ASK")
        _, row, _ = self.say(BUYER, f"/ask какой срок оплаты по договору? {nq}", timeout=90)
        answer = row["visible"] if row else ""
        s.evidence["ask_reply"] = clip(answer, 700)
        s.check("ask_returns_answer_with_sources", row is not None and "Источники:" in answer and "[1]" in answer
                and "14" in answer, clip(answer, 300))
        s.check("ask_request_was_grounded_in_the_document", any("Фрагменты:" in r["last_user"] and "14 календарных" in r["last_user"]
                                                              for r in self.model.snapshot()),
                "model request carried the retrieved fragment")
        mark = self.tg.mark()
        self.tg.inject_text(BUYER, "/buy")
        inv = wait_until(lambda: self.tg.invoices[-1] if self.tg.invoices else None, 40)
        payload = (inv or {}).get("payload", {})
        prices = payload.get("prices") or []
        s.evidence["invoice"] = {k: payload.get(k) for k in ("title", "currency", "provider_token", "prices", "chat_id")}
        s.check("buy_emits_sendInvoice_in_XTR", bool(inv) and payload.get("currency") == "XTR"
                and payload.get("provider_token") == "" and prices and int(prices[0].get("amount", 0)) > 0,
                json.dumps(s.evidence["invoice"], ensure_ascii=False))
        if inv:
            stars = int(prices[0]["amount"])
            self.tg.inject_pre_checkout(BUYER, payload["payload"], stars, "pcq-proof-1")
            answered = wait_until(lambda: self.tg.pre_checkout_answers and self.tg.pre_checkout_answers[-1], 30)
            s.check("pre_checkout_answered_ok", bool(answered) and answered["payload"].get("ok") is True,
                    json.dumps(answered["payload"] if answered else {}))
            mark = self.tg.mark()
            self.tg.inject_successful_payment(BUYER, payload["payload"], stars, "SIMCHARGE-PROOF-1")
            row = self.tg.wait_reply(BUYER, mark, timeout=60)
            s.check("successful_payment_grants_access", row is not None and "Оплата получена" in row["visible"],
                    clip(row["visible"]) if row else "no reply")
            mark = self.tg.mark()
            self.tg.inject_successful_payment(BUYER, payload["payload"], stars, "SIMCHARGE-PROOF-1")
            row = self.tg.wait_reply(BUYER, mark, timeout=60)
            s.check("redelivered_payment_is_not_double_booked", row is not None and "один раз" in row["visible"],
                    clip(row["visible"]) if row else "no reply")
        s.evidence["product_logs"] = self.product_logs(d)
        p.stop_clean()


# --------------------------------------------------------------------------------------------------------- reporting
SCENARIOS = [
    ("S1", "s1", "Build identity: own_build == MANIFEST, backend identity, one poller; negative control (other build)"),
    ("S2", "s2", "One poller + durable queue: refusal of a 2nd poller, kill mid-turn, no double answers"),
    ("S3", "s3", "Memory + isolation: two participants, forget, restart"),
    ("S4", "s4", "Free route only + honest refusal when the free route fails"),
    ("S5", "s5", "Long task across restart: exactly once; UNKNOWN_OUTCOME is never blindly resent"),
    ("S6", "s6", "Crisis phrase: fixed reply, no model call"),
    ("S7", "s7", "Paid documents (SIMULATED Bot API / Stars)"),
]


def write_report(proof: Proof, out_dir: Path, scenarios: list[Scn], leftovers: dict, args) -> int:
    verdicts = {s.name: s.verdict for s in scenarios}
    executed = [s for s in scenarios if s.verdict != "NOT_RUN"]
    ok = bool(executed) and all(s.verdict == "PASS" for s in executed)
    bundle = proof.bundle
    zip_info = None
    if args.zip:
        zp = Path(args.zip)
        zip_info = {"path": str(zp), "sha256": sha256_file(zp) if zp.is_file() else None,
                    "bytes": zp.stat().st_size if zp.is_file() else None}
        sums = zp.parent / "SHA256.txt"
        if zp.is_file() and sums.is_file():
            for line in sums.read_text(encoding="utf-8", errors="ignore").splitlines():
                parts = line.replace("*", " ").split()
                if len(parts) >= 2 and parts[1] == zp.name:
                    zip_info["published_sha256"] = parts[0].lower()
                    zip_info["matches_published_sha256"] = parts[0].lower() == zip_info["sha256"]
        zip_info["extracted_equals_zip_members"] = zip_vs_extracted(zp, bundle.dir)
    report = {
        "schema": "jeff-bundle-proof/1", "script": str(Path(__file__).resolve()),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(proof.t_start)),
        "total_secs": round(time.time() - proof.t_start, 1),
        "overall": "PASS" if ok else "FAIL", "verdicts": verdicts,
        "bundle": {"dir": str(bundle.dir), "artifact": bundle.manifest.get("artifact"),
                   "manifest_source_sha": bundle.sha, "build_json_source_sha": bundle.build_json_sha,
                   "source_dirty": bundle.manifest.get("source_dirty"),
                   "built_at": bundle.manifest.get("built_at"),
                   "python": (bundle.manifest.get("contents", {}).get("runtime", {}) or {}).get("python_version"),
                   "zip": zip_info},
        "control_bundle": ({"dir": str(proof.control.dir), "manifest_source_sha": proof.control.sha}
                           if proof.control else None),
        "seam": {"file": str(proof.seam_path), "sha256": proof.seam_sha, "honoured_under_I": proof.seam_honoured,
                 "probe": getattr(proof, "seam_probe", None), "deleted_at_end": not leftovers.get("seam_file_present"),
                 "env_var": "JEFF_PROOF_FAKE_TG_URL (loopback only)"},
        "environment": {"whitelist": WHITELIST + ["+ redirected LOCALAPPDATA/APPDATA/USERPROFILE/TEMP/TMP",
                                                  "+ BCC_DATA_DIR, BOSSMAN_DATA_DIR, BOSSMAN_TELEGRAM_POLLER_LOCK_DIR, "
                                                  "BOSSMAN_COMPANION_CONFIG, PYTHONUTF8, NoDefaultCurrentDirectoryInExePath",
                                                  "+ JEFF_PROOF_FAKE_TG_URL (seam), BOSSMAN_JEFF_PAID_DOCS=1 (S7 only)"],
                        "interpreter_flags": "-I -X utf8", "throwaway_root": str(proof.root),
                        "ports": {"backend": proof.p_backend, "backend_control": proof.p_backend_ctl,
                                  "fake_telegram": proof.p_tg, "fake_provider": proof.p_model},
                        "host_pollers_before_run": proof.baseline_pollers},
        "findings": {
            "defects": [dict(d, scenario=s.name) for s in scenarios for d in s.defects],
            "gaps": [{"scenario": s.name, "gap": g} for s in scenarios for g in s.gaps],
        },
        "scenarios": {s.name: s.as_dict() for s in scenarios},
        "leftovers": leftovers,
    }
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(render_md(report, scenarios), encoding="utf-8")
    return 0 if ok else 1


def zip_vs_extracted(zip_path: Path, bundle_dir: Path) -> dict:
    """Is the extracted bundle really the zip's content? SHA256 of a few load-bearing members, both sides."""
    import zipfile
    wanted = ["MANIFEST.json", "runtime/python.exe", "runtime/Lib/site-packages/bcc/_build.json",
              "runtime/Lib/site-packages/bcc/pit/runtime.py", "runtime/Lib/site-packages/bcc/pit/cli.py",
              "runtime/Lib/site-packages/bcc/pit/j2/longtask.py", "runtime/Lib/site-packages/bcc/pit/heartbeat.py"]
    out: dict = {}
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = {n.replace("\\", "/"): n for n in zf.namelist()}
            top = next((n.split("/")[0] for n in names if n.endswith("/MANIFEST.json") and n.count("/") == 1), "")
            for rel in wanted:
                member = names.get(f"{top}/{rel}" if top else rel)
                local = bundle_dir / rel
                if member is None or not local.is_file():
                    out[rel] = "missing_on_one_side"
                    continue
                h = hashlib.sha256()
                with zf.open(member) as fh:
                    for block in iter(lambda: fh.read(1 << 20), b""):
                        h.update(block)
                out[rel] = "equal" if h.hexdigest() == sha256_file(local) else "DIFFERENT"
    except (OSError, zipfile.BadZipFile) as exc:
        out["error"] = str(exc)[:200]
    return out


def render_md(report: dict, scenarios: list[Scn]) -> str:
    b = report["bundle"]
    lines = [
        "# JEFF-BUNDLE-PROOF 2026-10-12",
        "",
        f"Overall: **{report['overall']}**  |  run {report['started_at']}, {report['total_secs']} s",
        "",
        "## Subject",
        f"- bundle: `{b['dir']}`",
        f"- MANIFEST source_sha: `{b['manifest_source_sha']}` (bcc/_build.json: `{b['build_json_source_sha']}`, dirty={b['source_dirty']})",
        f"- built: {b.get('built_at')}, python {b.get('python')}",
        f"- zip: {json.dumps(b['zip'], ensure_ascii=False)}",
        f"- negative-control build: `{(report['control_bundle'] or {}).get('manifest_source_sha')}`",
        f"- test seam sitecustomize.py SHA256: `{report['seam']['sha256']}` (honoured under -I: {report['seam']['honoured_under_I']}, "
        f"deleted at end: {report['seam']['deleted_at_end']})",
        "- Telegram is a **SIMULATED** loopback Bot API; the model provider is a loopback fake; no real network service is used by the scenarios.",
        "- Run like the owner launcher: `<bundle>\\runtime\\python.exe -I -X utf8 -m bcc.pit.cli <cmd> --data-dir D`, "
        "whitelisted environment, throwaway LOCALAPPDATA/USERPROFILE, repo not on sys.path.",
        "",
        "## Verdicts",
        "",
        "| scenario | verdict | secs | what |",
        "|---|---|---|---|",
    ]
    for s in scenarios:
        lines.append(f"| {s.name} | **{s.verdict}** | {round(s.secs)} | {s.title} |")
    findings = report.get("findings", {})
    lines += ["", "## Findings (defects and gaps, not hidden)", ""]
    if not findings.get("defects") and not findings.get("gaps"):
        lines.append("None.")
    for d in findings.get("defects", []):
        lines += [f"- DEFECT [{d['scenario']}, {d['severity']}] **{d['title']}**",
                  f"  - repro: {d['repro']}", f"  - smallest fix: {d['smallest_fix']}"]
    for g in findings.get("gaps", []):
        lines.append(f"- GAP [{g['scenario']}] {g['gap']}")
    failed = [(s.name, c["name"], c["detail"]) for s in scenarios for c in s.checks if c["required"] and not c["ok"]]
    if failed:
        lines += ["", "Failed required checks:"] + [f"- {n}.{c}: {d}" for n, c, d in failed]
    for s in scenarios:
        lines += ["", f"## {s.name} - {s.verdict}", f"{s.title}", ""]
        if s.not_run:
            lines.append(f"NOT_RUN: {s.not_run}")
        if s.error:
            lines += ["Harness error:", "```", s.error[-1500:], "```"]
        for c in s.checks:
            mark = "ok" if c["ok"] else "FAIL"
            req = "" if c["required"] else " (advisory)"
            lines.append(f"- [{mark}] `{c['name']}`{req}" + (f" - {c['detail']}" if c["detail"] else ""))
        if s.gaps:
            lines += ["", "Known gaps (recorded, not hidden):"]
            lines += [f"- {g}" for g in s.gaps]
        if s.defects:
            lines += ["", "Defects:"]
            for dft in s.defects:
                lines += [f"- **{dft['title']}** (severity {dft['severity']})",
                          f"  - repro: {dft['repro']}", f"  - smallest fix: {dft['smallest_fix']}"]
        if s.notes:
            lines += ["", "Notes:"] + [f"- {n}" for n in s.notes]
    lines += ["", "## Leftovers after cleanup", "```", json.dumps(report["leftovers"], ensure_ascii=False, indent=2), "```", ""]
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------------------ main
def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bundle", default=DEFAULT_BUNDLE)
    ap.add_argument("--control-bundle", default=DEFAULT_CONTROL, help="a DIFFERENT build, negative control for S1")
    ap.add_argument("--zip", default="", help="bundle zip: its SHA256 goes into the report")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--root", default="", help="throwaway root (default <out>/work)")
    ap.add_argument("--only", default="", help="comma list, e.g. S1,S3")
    args = ap.parse_args()

    proof = Proof(args)
    if not proof.bundle.ok():
        print(f"bundle not usable: {proof.bundle.dir}")
        return 2
    selected = [x.strip().upper() for x in args.only.split(",") if x.strip()] or [n for n, _, _ in SCENARIOS]
    log(f"bundle {proof.bundle.dir.name} source_sha={proof.bundle.sha[:12]}; ports "
        f"backend={proof.p_backend} ctl={proof.p_backend_ctl} tg={proof.p_tg} model={proof.p_model}")
    log(f"host pollers before the run: {[r['pid'] for r in proof.baseline_pollers]}")
    scenarios: list[Scn] = []
    try:
        proof.install_seam()
        log(f"seam installed sha256={proof.seam_sha[:16]} honoured_under_-I={proof.seam_honoured}")
        for name, method, title in SCENARIOS:
            if name not in selected:
                continue
            scn = Scn(name, title)
            scenarios.append(scn)
            if proof.seam_honoured is not True:
                scn.not_run = f"test seam not honoured by the embedded python: {getattr(proof, 'seam_probe', None)}"
                continue
            log(f"=== {name}: {title}")
            t0 = time.time()
            try:
                getattr(proof, method)(scn)
            except Exception:       # noqa: BLE001 - a harness fault is reported, never swallowed
                scn.error = traceback.format_exc()
                log(f"    HARNESS ERROR in {name}:\n{scn.error}")
            finally:
                scn.secs = time.time() - t0
                for poller in proof.pollers:
                    try:
                        if poller.alive():
                            poller.stop_clean(timeout=20)
                    except Exception:       # noqa: BLE001
                        pass
                proof.pollers.clear()
            log(f"=== {name}: {scn.verdict} ({round(scn.secs)}s)")
    finally:
        proof.cleanup()
        time.sleep(2)
    leftovers = proof.leftovers()
    rc = write_report(proof, Path(args.out), scenarios, leftovers, args)
    log(f"report: {Path(args.out) / 'report.json'}  overall={'PASS' if rc == 0 else 'FAIL'}")
    log(f"leftovers: {json.dumps(leftovers, ensure_ascii=False)}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
