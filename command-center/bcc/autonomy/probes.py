"""Real staging probes for the autonomy cycle (registered into ``staging.build_default_runner``).

Every probe runs INSIDE the staged candidate checkout (``python -m bcc.autonomy.probes
<check> --data-dir <staging temp dir>``), so it exercises the candidate's own code
with the staging temp data dir - never the owner data and never a network:

* ``telegram_fake``  - the Jeff participant runtime behind an in-process fake Telegram
  Bot API and a fake model adapter answers ``/start`` (zero-start intro) and a text;
* ``memory``         - vault write -> read -> forget (delete) in the temp data dir;
* ``jeff_identity``  - the JEFF-0042 red-team suite through the runtime's own reply path
  (``ParticipantRuntime.handle``) with a clean fake model: 0 leaks required;
* ``voice_status``   - ASR/TTS status is REPORTED (installed or not is fine, a crash is not);
* ``model_routing``  - free-only route verdicts on a fixture catalog: paid and unknown
  prices refused, a ``:free`` id at 0/0 accepted;
* ``acceptance``     - the goal's ``pytest:`` acceptance tests in the candidate checkout.

A probe that cannot run, raises or returns malformed output fails closed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

STAGING_PROBES = ("telegram_fake", "memory", "jeff_identity", "voice_status", "model_routing", "acceptance")
PROBE_TIMEOUT_S = 600
_FAKE_BOT = "0:staging-fake"
_SALT = "ab" * 32


# ------------------------------------------------------------------ in-process checks


def _settings(data_dir: Path):
    from ..pit.config import PITSettings
    from ..telegram_companion.config import Person
    return PITSettings(data_dir=data_dir, people=(Person(user_id=101, chat_id=101, role="owner"),),
                       chat_models=("free/model:free",), provider_base_url="http://127.0.0.1:9/v1",
                       provider_key="staging-fake", bot_token=_FAKE_BOT, identity_salt=_SALT)


class _FakeModel:
    def __init__(self, text: str):
        self.text, self.calls = text, 0

    async def chat(self, model, messages, **kw):
        from ..providers import ChatResult
        self.calls += 1
        return ChatResult(text=self.text, tokens_in=10, tokens_out=5, model=model)

    async def list_model_info(self):
        return [{"id": "free/model:free"}]

    async def list_model_pricing(self):
        return {"free/model:free": {"prompt": 0.0, "completion": 0.0}}

    async def close(self):
        return None


def _fake_bot_api(updates: list[dict]):
    import httpx

    class FakeBotAPI(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.pending, self.sent, self.on_send = list(updates), [], None

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/getUpdates") and not self.pending:
                await asyncio.sleep(0.05)
            await request.aread()
            method = request.url.path.rsplit("/", 1)[-1]
            body = json.loads(request.content or b"{}")
            if method == "getMe":
                return httpx.Response(200, json={"ok": True, "result": {"is_bot": True, "username": "jeff_staging"}})
            if method == "getWebhookInfo":
                return httpx.Response(200, json={"ok": True, "result": {"url": ""}})
            if method == "getUpdates":
                batch, self.pending = self.pending[:1], self.pending[1:]
                return httpx.Response(200, json={"ok": True, "result": batch})
            if method == "sendMessage":
                self.sent.append(body)
                if self.on_send:
                    self.on_send()
                return httpx.Response(200, json={"ok": True, "result": {"message_id": 1000 + len(self.sent)}})
            if method == "sendChatAction":
                return httpx.Response(200, json={"ok": True, "result": True})
            return httpx.Response(200, json={"ok": False, "description": "unsupported in staging fake"})

    return FakeBotAPI()


def _update(n: int, text: str) -> dict:
    return {"update_id": n, "message": {"message_id": n, "from": {"id": 101, "is_bot": False},
                                        "chat": {"id": 101, "type": "private"}, "text": text}}


def _runtime(data_dir: Path, model_text: str, transport: Any = None):
    from ..pit import runtime as rt
    from ..telegram_companion.adapters import Telegram
    settings = _settings(data_dir)
    runtime = rt.ParticipantRuntime(settings)
    if transport is not None:
        runtime.telegram = Telegram(rt._transport_settings(settings), transport=transport)
    runtime.adapter = _FakeModel(model_text)
    return runtime


def _one_session(data_dir: Path, text: str, n: int, model_text: str, timeout: float) -> list[str]:
    from ..pit import runtime as rt
    api = _fake_bot_api([_update(n, text)])
    runtime = _runtime(data_dir, model_text, api)

    def stop() -> None:
        if api.sent:
            (runtime.home / rt.STOP_FLAG).write_text("staging probe", encoding="utf-8")

    api.on_send = stop
    asyncio.run(asyncio.wait_for(runtime.run(), timeout=timeout))
    return [str(row.get("text", "")) for row in api.sent]


def check_telegram_fake(data_dir: Path, opts: Mapping[str, Any]) -> tuple[bool, str]:
    from ..pit import runtime as rt
    marker = "Staging reply OK"
    first = _one_session(data_dir, "/start", 1, marker, float(opts.get("timeout", 30)))
    second = _one_session(data_dir, "Привет, как дела?", 2, marker, float(opts.get("timeout", 30)))
    ok = first == [rt.INTRO_RU] and len(second) == 1 and marker in second[0]
    return ok, f"/start -> {'intro' if first == [rt.INTRO_RU] else first!r}; text -> {second!r}"[:500]


def check_memory(data_dir: Path, opts: Mapping[str, Any]) -> tuple[bool, str]:
    from ..pit.models import ConsentState, EvidenceKind, MemoryCandidate, Sensitivity
    from ..pit.vault import PersonaVault
    vault = PersonaVault(data_dir, bytes.fromhex(_SALT))
    key = vault.key_for_telegram(101)
    vault.set_consent(key, ConsentState(memory_enabled=True, raw_history_enabled=True))
    fact = MemoryCandidate(id="staging-fact-1", category="preference", key="drink", value="tea", confidence=0.9,
                           evidence_kind=EvidenceKind.EXPLICIT, sensitivity=Sensitivity.NORMAL,
                           source_message_id="staging-1")
    wrote = vault.append_candidate(key, fact)
    read = [f for f in vault.list_facts(key) if f["id"] == "staging-fact-1" and f["value"] == "tea"]
    forgot = vault.delete_fact(key, "staging-fact-1", actor="participant")
    after = [f for f in vault.list_facts(key) if f["id"] == "staging-fact-1"]
    ok = bool(wrote and read and forgot and not after)
    return ok, f"write={wrote} read={len(read)} forget={forgot} left={len(after)}"


def check_jeff_identity(data_dir: Path, opts: Mapping[str, Any]) -> tuple[bool, str]:
    from ..telegram_companion.config import Person
    from .identity_task import IDENTITY_PROMPTS, run_redteam
    runtime = _runtime(data_dir, "Я Jeff, AI-помощник Bossman.")
    person = Person(user_id=101, chat_id=101, role="owner")
    counter = iter(range(10, 10_000))

    async def go():
        await runtime.handle(person, {"text": "/start", "_message_id": 1})

        async def respond(prompt: str) -> str:
            return str(await runtime.handle(person, {"text": prompt, "_message_id": next(counter)}) or "")
        return await run_redteam(respond)

    rep = asyncio.run(go())
    return rep.leaks == 0, f"identity_redteam.leaks={rep.leaks}/{rep.total} leaked={rep.leaked}"[:500]


def check_voice_status(data_dir: Path, opts: Mapping[str, Any]) -> tuple[bool, str]:
    from ..pit import speech
    asr, tts = speech.asr_status(), speech.tts_status()
    ok = isinstance(asr.get("available"), bool) and isinstance(tts.get("available"), bool)
    return ok, (f"asr={'available' if asr.get('available') else asr.get('reason_code')} "
                f"tts={'available' if tts.get('available') else tts.get('reason_code')}")


def check_model_routing(data_dir: Path, opts: Mapping[str, Any]) -> tuple[bool, str]:
    from ..pit.model_route import NOT_FREE_ID, PRICE_POSITIVE, PRICE_UNKNOWN, route_verdict
    from .planner import NEMOTRON, ModelFacts, planner_model_allowed
    cases = {
        "paid/model": (True, {"prompt": 0.000002, "completion": 0.000004}, PRICE_POSITIVE),
        "vendor/mystery:free": (True, {}, PRICE_UNKNOWN),
        "vendor/zero-not-free": (True, {"prompt": 0.0, "completion": 0.0}, NOT_FREE_ID),
        NEMOTRON: (True, {"prompt": 0.0, "completion": 0.0}, ""),
    }
    bad = [m for m, (listed, prices, want) in cases.items() if route_verdict(m, listed, prices) != want]
    free_ok = planner_model_allowed(ModelFacts("openrouter", NEMOTRON, 550.0, 55.0, 0.0, 0.0, "fixture", "t"))[0]
    paid_ok = planner_model_allowed(ModelFacts("openrouter", "paid/model", 70.0, None, 0.5, 1.0, "fixture", "t"))[0]
    ok = not bad and free_ok and not paid_ok
    return ok, f"mismatched={bad} free_accepted={free_ok} paid_refused={not paid_ok}"


def check_acceptance(data_dir: Path, opts: Mapping[str, Any]) -> tuple[bool, str]:
    tests = [str(t) for t in (opts.get("tests") or []) if str(t).strip()]
    if not tests:
        return False, "no pytest: acceptance tests for this candidate (fail closed)"
    cwd = Path(opts.get("cwd") or Path.cwd())
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests], cwd=str(cwd),
                          capture_output=True, timeout=int(opts.get("timeout", PROBE_TIMEOUT_S)),
                          env={**os.environ, "BCC_DATA_DIR": str(data_dir), "PYTHONDONTWRITEBYTECODE": "1"})
    tail = (proc.stdout or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
    return proc.returncode == 0, f"pytest exit {proc.returncode}: {tail[0][:300]}"


CHECKS: dict[str, Callable[[Path, Mapping[str, Any]], tuple[bool, str]]] = {
    "telegram_fake": check_telegram_fake, "memory": check_memory, "jeff_identity": check_jeff_identity,
    "voice_status": check_voice_status, "model_routing": check_model_routing, "acceptance": check_acceptance,
}


def run_check(name: str, data_dir: Path, opts: Mapping[str, Any] | None = None) -> dict:
    fn = CHECKS.get(name)
    if fn is None:
        return {"check": name, "ok": False, "detail": "unknown check"}
    try:
        ok, detail = fn(Path(data_dir), dict(opts or {}))
    except Exception as exc:  # noqa: BLE001 - a crashing probe is a failed check
        ok, detail = False, f"probe raised {type(exc).__name__}: {str(exc)[:300]}"
    return {"check": name, "ok": bool(ok), "detail": str(detail)[:1000]}


# ------------------------------------------------------------------ staging wiring (candidate subprocess)


Runner = Callable[..., Any]


def _pytest_ids(tests: Any) -> list[str]:
    from .hands import _pytest_ids as ids
    return ids(tests)


def candidate_probe(name: str, *, tests_for: Callable[[str], list[str]] | None = None,
                    runner: Runner = subprocess.run, timeout_s: int = PROBE_TIMEOUT_S):
    """A staging Probe that runs `name` inside the staged candidate checkout."""
    def probe(handle: Any, ctx: Mapping[str, Any]) -> tuple[bool, str]:
        checkout = Path(handle["checkout"]) if isinstance(handle, Mapping) else None
        if checkout is None or not checkout.is_dir():
            return False, "no staged checkout"
        cc = checkout / "command-center"
        base = cc if cc.is_dir() else checkout
        argv = [sys.executable, "-m", "bcc.autonomy.probes", name, "--data-dir", str(ctx["data_dir"])]
        if name == "acceptance":
            tests = tests_for(str(ctx.get("sha", ""))) if tests_for else []
            if not tests:
                return False, "no pytest: acceptance tests for this candidate (fail closed)"
            argv += ["--cwd", str(base), *[f"--test={t}" for t in tests]]
        from ..rave.connectors import child_env
        paths = [str(base)] + ([str(checkout / "bossman-core")] if (checkout / "bossman-core").is_dir() else [])
        env = {**child_env(), "BCC_DATA_DIR": str(ctx["data_dir"]), "BOSSMAN_STAGING": "1",
               "PYTHONPATH": os.pathsep.join(paths), "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            proc = runner(argv, cwd=str(base), env=env, capture_output=True, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return False, f"probe timed out after {timeout_s}s"
        lines = (proc.stdout or b"").decode("utf-8", "replace").strip().splitlines()
        try:
            out = json.loads(lines[-1])
        except (IndexError, ValueError):
            return False, f"probe returned no JSON (exit {proc.returncode})"
        if not isinstance(out, dict) or out.get("check") != name or not isinstance(out.get("ok"), bool):
            return False, "malformed probe output"
        return bool(out["ok"] and proc.returncode == 0), str(out.get("detail", ""))
    return probe


def provider(root: Path, repo: Path, *, runner: Runner = subprocess.run) -> dict:
    """Probe provider for ``staging.register_probe_provider``: all STAGING_PROBES as
    candidate subprocess probes; acceptance tests come from the goal whose candidate
    SHA is being staged (GoalStore under ``root``)."""
    def tests_for(sha: str) -> list[str]:
        from .goals import GoalStore
        try:
            for rec in GoalStore(root).list():
                if (rec.get("candidate") or {}).get("sha") == sha:
                    return _pytest_ids(rec["goal"].get("acceptance_tests"))
        except Exception:  # noqa: BLE001 - unknown -> no tests -> fail closed
            return []
        return []
    return {name: candidate_probe(name, tests_for=tests_for, runner=runner) for name in STAGING_PROBES}


def _register() -> None:
    from .staging import register_probe_provider
    register_probe_provider(provider)


_register()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m bcc.autonomy.probes")
    p.add_argument("check", choices=sorted(CHECKS))
    p.add_argument("--data-dir", required=True)
    p.add_argument("--cwd", default="")
    p.add_argument("--test", action="append", default=[])
    args = p.parse_args(argv)
    data = Path(args.data_dir)
    data.mkdir(parents=True, exist_ok=True)
    res = run_check(args.check, data, {"cwd": args.cwd or None, "tests": args.test})
    sys.stdout.write(json.dumps(res, ensure_ascii=False) + "\n")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
