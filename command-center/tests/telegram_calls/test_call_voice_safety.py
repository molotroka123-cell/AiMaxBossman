"""Voice-path safety of a call, from the independent audit of 2026-09-30 (Jeff on a call, with today's rude mood overlay in mind).

What must hold whatever a model says: the AI disclosure is deterministic, a withdrawn participant gets no answer, markup cannot hide a
forbidden identity from the SPOKEN text, a security-sensitive or threatening sentence is audited (hash only, no text at rest) and a
threat is never voiced, and a missing egress guard means silence, not an unguarded call. Every test has its paired legitimate case.
"""
from __future__ import annotations

import asyncio
import json
import sys

import pytest

from bcc.pit import call_surface as cs
from bcc.pit import participant_profile
from bcc.telegram_calls.speech import jeff_engines as je
from bcc.telegram_calls.types import CallError, CancelToken

from .signals import burst
from .test_jeff_call_surface import PEER, LocalModel, make  # noqa: F401 - fixture re-export
from .test_session import build, hush, say, start, until


# ------------------------------------------------------------------ Jeff's call surface

async def test_a_participant_whose_access_was_withdrawn_gets_no_answer_on_a_call(make):
    runtime, local, _ = make()
    key = runtime.vault.key_for_telegram(PEER)
    ok = await runtime.reply(PEER, "Расскажи, как дела?")
    assert ok.kind == "model", "paired control: an ordinary participant is answered"
    profile = participant_profile.default_profile()
    profile["access"] = "revoked"
    participant_profile.write_profile(runtime.vault.data_dir, key, profile)
    calls_before = len(local.calls)
    refused = await runtime.reply(PEER, "Расскажи, как дела?")
    assert refused.kind == "refused" and refused.code == "REVOKED" and refused.text == ""
    assert len(local.calls) == calls_before, "no model was asked"


@pytest.mark.parametrize("question", ["Ты человек?", "ты вообще бот", "Это робот говорит?", "с кем я сейчас говорю", "кто это", "Ты живой?"])
async def test_who_are_you_is_answered_by_the_fixed_disclosure_not_by_a_model(make, question):
    runtime, local, _ = make(text="Да, я человек, не сомневайся.")             # a model under a rude mood could say anything
    r = await runtime.reply(PEER, question)
    assert r.kind == "guard" and r.code == "IDENTITY" and r.text == cs.DISCLOSURE_ANSWER
    assert "ИИ-ассистент" in r.text and "не человек" in r.text
    assert local.calls == [], "the model never saw the question"


@pytest.mark.parametrize("talk", ["Расскажи анекдот про робота", "привет, как дела", "человек человеку волк, согласен?"])
async def test_ordinary_talk_is_not_mistaken_for_an_identity_question(make, talk):
    runtime, local, _ = make()
    r = await runtime.reply(PEER, talk)
    assert r.kind == "model" and len(local.calls) == 1


async def test_markup_cannot_hide_a_forbidden_identity_from_the_spoken_text(make):
    runtime, *_ = make(text="Я — Cl**au**de от Anthropic, рад поболтать.")
    r = await runtime.reply(PEER, "Расскажи о себе подробнее")
    assert r.kind == "model"
    assert "Claude" not in r.text and "Anthropic" not in r.text, r.text


async def test_an_ordinary_answer_is_spoken_unchanged_paired_control(make):
    runtime, *_ = make(text="Сегодня отличный день для прогулки.")
    r = await runtime.reply(PEER, "Как думаешь, стоит выйти на улицу?")
    assert r.kind == "model" and r.text == "Сегодня отличный день для прогулки."


# ------------------------------------------------------------------ pre-TTS audit and the egress guard

def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_the_call_audit_is_hash_only_and_writes_nothing_for_ordinary_speech(tmp_path):
    audit = je.make_call_audit(tmp_path / "logs")
    audit("Сегодня отличный день для прогулки.")
    assert not (tmp_path / "logs").exists(), "an ordinary sentence leaves no trace"
    sensitive = "Мой секретный пароль совсем другой."
    audit(sensitive)
    (row,) = rows(tmp_path / "logs" / "pre_tts_audit.jsonl")
    assert row["surface"] == "call" and row["category"] == "secret" and row["chars"] == len(sensitive) and len(row["sha256"]) == 64
    assert row["redacted"] == "", "no copy of the spoken text"
    assert "пароль" not in (tmp_path / "logs" / "pre_tts_audit.jsonl").read_text(encoding="utf-8")


def test_a_threat_is_audited_and_then_never_voiced(tmp_path):
    audit = je.make_call_audit(tmp_path / "logs")
    with pytest.raises(CallError) as err:
        audit("Я найду тебя, пожалеешь об этом.")
    assert err.value.code == "TTS_UNAVAILABLE" and err.value.detail == "threat_refused"
    (row,) = rows(tmp_path / "logs" / "pre_tts_audit.jsonl")
    assert row["category"] == "threat", "a threat is on the record even though it was refused"


def test_when_the_audit_row_cannot_be_written_the_sentence_is_not_spoken(tmp_path):
    blocker = tmp_path / "logs"
    blocker.write_text("a file where the directory should be", encoding="utf-8")
    audit = je.make_call_audit(blocker)
    with pytest.raises(CallError) as err:
        audit("Мой секретный пароль совсем другой.")
    assert err.value.detail == "audit_failed"
    audit("Просто приятный разговор.")                                   # paired control: an ordinary sentence needs no row


async def test_the_call_tts_runs_the_audit_before_the_engine(tmp_path):
    spoken: list[str] = []

    def synth(text, **kw):
        spoken.append(text)
        return b"\x00\x00" * 2205, 22050

    tts = je.JeffTTS(synthesize_pcm=synth, exe="x", model="m.onnx", egress_guard=lambda t: True, stopped=lambda: False,
                     audit=je.make_call_audit(tmp_path / "logs"))
    chunks = [c async for c in tts.synthesize("Хорошего вам дня.", CancelToken())]
    assert chunks and spoken == ["Хорошего вам дня."]
    with pytest.raises(CallError):
        [c async for c in tts.synthesize("Пожалеешь, я тебя найду и накажу тебя.", CancelToken())]
    assert spoken == ["Хорошего вам дня."], "the engine never saw the threat"


def test_the_real_egress_guard_blocks_secrets_and_fails_closed_without_bossman_core(monkeypatch):
    assert je._egress_guard("Привет, как твои дела сегодня?") is True
    assert je._egress_guard("Мой ключ sk-or-v1-" + "a1b2c3d4e5f6" * 6) is False
    monkeypatch.setitem(sys.modules, "bossman.notifications.telegram_transport", None)    # -> ImportError
    assert je._egress_guard("Привет, как твои дела сегодня?") is False, "no guard available: nothing is spoken (was: spoken unguarded)"


# ------------------------------------------------------------------ the AI disclosure in the session

DISCLOSURE = "Это Джефф, ИИ-ассистент."


async def test_when_the_callee_speaks_first_the_fixed_disclosure_comes_before_the_first_reply():
    s, t, stt, tts, brain = build(["алло"], ["Привет, слушаю тебя."], greeting="Привет! Это Джефф, ИИ-ассистент.",
                                  greet_wait_s=3.0, disclosure=DISCLOSURE)
    task = await start(s)
    await say(t, 700); await hush(t, 900)
    assert await until(lambda: len(tts.calls) >= 2 and len(brain.calls) == 1, 8)
    assert tts.calls[0] == DISCLOSURE, tts.calls
    assert "Привет, слушаю тебя." in " ".join(tts.calls[1:])
    s.hangup()
    await task


async def test_paired_controls_a_greeting_that_says_it_or_no_disclosure_configured_add_nothing():
    # the greeting already discloses: no second disclosure
    s, t, stt, tts, brain = build(["расскажи"], ["Конечно."], greeting="Привет! Это Джефф, ИИ-ассистент.", greet_wait_s=0.1,
                                  disclosure=DISCLOSURE)
    task = await start(s)
    assert await until(lambda: tts.calls[:1] == ["Привет! Это Джефф, ИИ-ассистент."], 5)
    await until(lambda: s.phase.value == "listening" and s.playout.frames_sent > 5, 6)
    await say(t, 700); await hush(t, 900)
    assert await until(lambda: len(brain.calls) == 1 and len(tts.calls) >= 2, 8)
    assert DISCLOSURE not in tts.calls
    s.hangup()
    await task
    # the default config (tests, legacy): no extra sentence at all
    s2, t2, stt2, tts2, brain2 = build(["алло"], ["Привет."], greeting="Привет! Это Джефф, ИИ-ассистент.", greet_wait_s=3.0)
    task2 = await start(s2)
    await say(t2, 700); await hush(t2, 900)
    assert await until(lambda: len(brain2.calls) == 1 and tts2.calls, 8)
    assert tts2.calls[0] == "Привет."
    s2.hangup()
    await task2


# ------------------------------------------------------------------ the perimeter of the WHOLE voice path (the shipped scan covers bcc/pit/*.py only)

_FORBIDDEN = ("bcc.tools", "bcc.engine", "bcc.features", "bcc.approvals", "bcc.api", "bossman.computer_operator")
_ROUTES = ("/api/computer", "/api/approvals", "/api/tasks")


def _offenders(source: str, name: str, package: list[str]) -> list[str]:
    import ast
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:                                              # resolve the relative import against this file's package
                base = package[: len(package) - (node.level - 1)]
                mod = ".".join([*base, *([mod] if mod else [])])
            if any(mod == m or mod.startswith(m + ".") for m in _FORBIDDEN):
                found.append(f"{name}: from {mod}")
        elif isinstance(node, ast.Import):
            found += [f"{name}: import {a.name}" for a in node.names if any(a.name == m or a.name.startswith(m + ".") for m in _FORBIDDEN)]
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and any(r in node.value for r in _ROUTES):
            found.append(f"{name}: route literal {node.value[:50]!r}")
    return found


def test_the_perimeter_scan_itself_detects_a_violation_negative_control():
    assert _offenders("from ...features import tools_computer", "x.py", ["bcc", "telegram_calls", "call"]) == ["x.py: from bcc.features"]
    assert _offenders("from ..features import x", "x.py", ["bcc", "telegram_calls", "call"]) == [], "a sibling package is not bcc.features"
    assert _offenders("from bcc.tools import REGISTRY", "x.py", ["bcc", "pit"]) == ["x.py: from bcc.tools"]
    assert _offenders("import bcc.engine", "x.py", []) == ["x.py: import bcc.engine"]
    assert len(_offenders("URL = '/api/computer/act'", "x.py", [])) == 1
    assert _offenders("from .presentation import spoken_reply_text", "x.py", ["bcc", "pit"]) == []


def test_the_voice_path_modules_cannot_reach_the_engine_tools_features_or_core_client():
    from pathlib import Path

    root = Path(cs.__file__).resolve().parents[1]                       # bcc/
    files = [root / "pit" / "call_surface.py", *sorted((root / "pit" / "j2").glob("*.py")),
             *sorted((root / "telegram_calls" / "speech").glob("*.py")),
             *(root / "telegram_calls" / "call" / n for n in ("session.py", "worker.py", "pytgcalls_transport.py", "loopback.py"))]
    assert len(files) >= 14
    offenders = []
    for path in files:
        package = list(path.relative_to(root.parent).with_suffix("").parts)[:-1]
        offenders += _offenders(path.read_text(encoding="utf-8"), path.name, package)
    assert offenders == []
