"""Does a local ollama model really hold N tokens of context? Probe before raising the chat window.

    python tools/context_probe.py --model gemma4-31b-uncensored:latest --target 126976 [--apply --model-id 98] [--wait]

Builds a prompt of ~target tokens: a canary code at the very start, neutral filler, and a question at the
end asking for the code. Sends it with num_ctx=target to ollama (127.0.0.1:11434). PASS only when the
model echoes the canary AND ollama reports prompt_eval_count >= 85 % of the target (no truncation).
With --apply (and the Bossman model id) the registry row's context_window is set through
PATCH /api/models/<id> — only on PASS, never otherwise. Receipt: <data>/context-probes/.
Loads the model: run it when the generation queue is empty (owner rule: never slow a running render).

Owner rule 23.10 (incident: this probe held ollama ~10 min while the owner's pult chat waited and the phone showed
«нет связи»): the owner's live requests always beat this background probe. While <data>/runtime/owner_active.json
is live (bcc/owner_activity.py, written by every owner chat turn) the probe
  * refuses to start (exit 3) - or, with --wait, waits until the owner is idle;
  * checks again right before sending;
  * while its request is in flight, polls the marker and drops the connection when the owner becomes active
    (ollama cancels a request whose client disconnected), exit 3 - with --wait it waits and tries again.
"""
from __future__ import annotations

import argparse
import http.client
import importlib.util
import json
import os
import random
import socket
import string
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]


def _load_owner_activity():
    """bcc/owner_activity.py of THIS checkout, loaded by path: on the owner PC ``import bcc`` resolves to the installed
    live checkout (editable install), which may predate the module. It is stdlib-only, so loading it alone is safe."""
    name = "bossman_owner_activity"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "command-center" / "bcc" / "owner_activity.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


owner_activity = _load_owner_activity()

OLLAMA = "http://127.0.0.1:11434"
# Measured 23.10 on gemma4-31b: 355 304 chars of this filler = 63 491 prompt tokens (the old «~3 chars/token» guess built
# a prompt of HALF the target, so a 124k probe silently tested 63k).
CHARS_PER_TOKEN = 5.6
FILLER = ("Запись {i}: температура {t} градусов, влажность {h} процентов, давление {p} мм, ветер {w} м/с. "
          "Record {i}: temperature {t}, humidity {h}, pressure {p}, wind {w}.")
EXIT_OWNER_ACTIVE = owner_activity.EXIT_OWNER_ACTIVE
OWNER_POLL_S = 5.0
MAX_PREEMPTIONS = 3


class OwnerPreempted(RuntimeError):
    """The owner became active while the probe's request was in flight; the request was dropped."""


def build_prompt(target_tokens: int, canary: str) -> str:
    rnd = random.Random(7)
    head = f"СЕКРЕТНЫЙ КОД ДЛЯ ПРОВЕРКИ ПАМЯТИ: {canary}. Запомни его; в самом конце я спрошу этот код.\n\n"
    parts, approx = [head], len(head) / CHARS_PER_TOKEN
    i = 0
    while approx < target_tokens * 0.92:
        line = FILLER.format(i=i, t=rnd.randint(-30, 40), h=rnd.randint(10, 99), p=rnd.randint(700, 790), w=rnd.randint(0, 25))
        parts.append(line + "\n")
        approx += len(line) / CHARS_PER_TOKEN
        i += 1
    parts.append("\n\nВОПРОС: какой СЕКРЕТНЫЙ КОД был в самом начале? Ответь только кодом.")
    return "".join(parts)


def post_json(url: str, body: bytes, timeout: float, should_abort: Callable[[], bool] | None = None,
              poll_s: float = OWNER_POLL_S) -> dict:
    """POST and read a JSON answer; the call runs in a thread so ``should_abort`` can be polled meanwhile. When it
    turns true the socket is shut down (ollama drops a request whose client went away) -> :class:`OwnerPreempted`."""
    parts = urllib.parse.urlsplit(url)
    conn = http.client.HTTPConnection(parts.hostname or "127.0.0.1", parts.port or 80, timeout=timeout)
    box: dict[str, Any] = {}

    def work() -> None:
        try:
            conn.request("POST", parts.path or "/", body=body, headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            raw = resp.read()
            if resp.status >= 400:
                raise RuntimeError(f"HTTP {resp.status}: {raw[:300]!r}")
            box["doc"] = json.loads(raw)
        except BaseException as exc:  # noqa: BLE001 — re-raised in the caller's thread
            box["error"] = exc

    worker = threading.Thread(target=work, name="context-probe-post", daemon=True)
    worker.start()
    while worker.is_alive():
        worker.join(poll_s)
        if worker.is_alive() and should_abort is not None and should_abort():
            sock = getattr(conn, "sock", None)
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            conn.close()
            worker.join(10)
            raise OwnerPreempted("owner became active: probe request dropped")
    if "error" in box:
        raise box["error"]
    return box["doc"]


def probe(model: str, target: int, timeout: int = 1800, *, send: Callable[..., dict] | None = None,
          should_abort: Callable[[], bool] | None = None, before_send: Callable[[], None] | None = None) -> dict:
    send = send or post_json
    canary = "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(10))
    prompt = build_prompt(target, canary)
    body = json.dumps({"model": model, "prompt": prompt, "stream": False,
                       "options": {"num_ctx": target, "temperature": 0, "num_predict": 32}}).encode()
    if before_send is not None:
        before_send()          # building a 127k prompt takes a while: the owner may have arrived meanwhile
    t0 = time.time()
    doc = send(OLLAMA + "/api/generate", body, timeout, should_abort)
    answer = str(doc.get("response") or "")
    evaluated = int(doc.get("prompt_eval_count") or 0)
    ok = canary in answer and evaluated >= target * 0.85
    return {"model": model, "target_num_ctx": target, "prompt_chars": len(prompt), "prompt_eval_count": evaluated,
            "canary_recalled": canary in answer, "verdict": "PASS" if ok else "FAIL", "seconds": round(time.time() - t0, 1),
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def _data_dir() -> Path:
    return Path(os.environ.get("BCC_DATA_DIR") or Path(os.environ.get("LOCALAPPDATA", ".")) / "Bossman" / "CommandCenter")


def apply(model_id: int, context_window: int) -> dict:
    token = (_data_dir() / "token").read_text(encoding="utf-8").strip()
    req = urllib.request.Request(f"http://127.0.0.1:8801/api/models/{model_id}",
                                 data=json.dumps({"context_window": context_window}).encode(),
                                 headers={"X-BCC-Token": token, "Content-Type": "application/json"}, method="PATCH")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


class OwnerActive(RuntimeError):
    """Refused: the owner is active and --wait was not given (or the wait ran out)."""


def _say(msg: str) -> None:
    print(f"context_probe: {msg}", file=sys.stderr, flush=True)


GATE_POLL_S = 15.0


def owner_gate(data_dir: Path, wait: bool, max_wait_s: float) -> None:
    """Return when the local model may be used; raise :class:`OwnerActive` otherwise."""
    doc = owner_activity.active(data_dir=data_dir)
    if doc is None:
        return
    if not wait:
        raise OwnerActive(owner_activity.describe(doc) + "; refusing to start (rerun with --wait to queue behind "
                          "the owner)")
    if not owner_activity.wait_until_idle(data_dir=data_dir, poll_s=GATE_POLL_S, max_wait_s=max_wait_s,
                                          log=lambda m: _say(m + "; waiting (--wait)")):
        raise OwnerActive(f"owner still active after {max_wait_s:g} s of waiting; giving up")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", required=True)
    ap.add_argument("--target", type=int, default=126976, help="num_ctx to prove (124k = 126976)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--model-id", type=int, default=None, help="Bossman registry id for --apply")
    ap.add_argument("--wait", action="store_true", help="wait while the owner is active instead of refusing (exit 3)")
    ap.add_argument("--max-wait-s", type=float, default=0.0, help="with --wait: give up after this long (0 = no limit)")
    a = ap.parse_args(argv)
    data_dir = _data_dir()
    should_abort = lambda: owner_activity.active(data_dir=data_dir) is not None  # noqa: E731
    gate = lambda: owner_gate(data_dir, a.wait, a.max_wait_s)  # noqa: E731
    res = None
    try:
        gate()
        for attempt in range(1, MAX_PREEMPTIONS + 1):
            try:
                res = probe(a.model, a.target, should_abort=should_abort, before_send=gate)
                break
            except OwnerPreempted as exc:
                _say(f"{exc} (attempt {attempt})")
                if not a.wait or attempt == MAX_PREEMPTIONS:
                    return EXIT_OWNER_ACTIVE
                gate()
    except OwnerActive as exc:
        _say(str(exc))
        return EXIT_OWNER_ACTIVE
    out = data_dir / "context-probes"
    out.mkdir(parents=True, exist_ok=True)
    safe = a.model.replace("/", "_").replace(":", "_")
    (out / f"{safe}-{time.strftime('%Y%m%d-%H%M')}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                                                      encoding="utf-8", newline="\n")
    print(json.dumps(res, ensure_ascii=False))
    if a.apply and res["verdict"] == "PASS" and a.model_id:
        print("applied:", apply(a.model_id, a.target).get("context_window"))
    return 0 if res["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
