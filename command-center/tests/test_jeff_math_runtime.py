"""Jeff mathematics, wiring part: the owner switch ``math_assist`` and the runtime hook (fake adapter, no network).

The pure module is covered by test_jeff_math_assist.py. The questions of the negative control are the fixed corpus
of the before/after measurement (tests/data/jeff_math_corpus.json).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from bcc.pit import jeff_settings as js

from .test_jeff_settings_overlay import pit_setup  # noqa: F401  (fixture)
from .test_pit_runtime import FakeAdapter, make_runtime, message, warm

ITEMS = json.loads((Path(__file__).parent / "data" / "jeff_math_corpus.json").read_text(encoding="utf-8"))["items"]


@pytest.fixture(autouse=True)
def _fresh_overlay_cache(monkeypatch):
    monkeypatch.delenv(js.ENV_PATH, raising=False)
    js._cache.clear()
    js._warned.clear()
    yield
    js._cache.clear()


# ---------------------------------------------------------------------------------------- owner switch
def test_overlay_flag_roundtrip_and_default(tmp_path):
    assert js.math_assist_enabled(tmp_path) is True                         # no file = on
    path = js.settings_path(tmp_path)
    js.write_overlay(path, {"version": 1, "math_assist": False})
    assert js.math_assist_enabled(tmp_path) is False
    assert json.loads(path.read_text(encoding="utf-8"))["math_assist"] is False
    js.write_overlay(path, {"version": 1, "math_assist": True})
    assert js.math_assist_enabled(tmp_path) is True
    assert "math_assist" not in json.loads(path.read_text(encoding="utf-8"))   # default on = not written
    with pytest.raises(js.OverlayError):
        js.normalize({"version": 1, "math_assist": "no"})
    path.write_text("{not json", encoding="utf-8")
    js._cache.clear()
    assert js.math_assist_enabled(tmp_path) is True                         # an unreadable file never switches it off


# ---------------------------------------------------------------------------------------- runtime hook
def _messages_for(tmp_path, text, *, off=False):
    runtime = make_runtime(tmp_path, adapter=FakeAdapter("Готово."))
    person = runtime.settings.people[0]
    warm(runtime, runtime.vault.key_for_telegram(person.user_id))
    if off:
        js.write_overlay(js.settings_path(runtime.vault.data_dir), {"version": 1, "math_assist": False})
    answer = asyncio.run(runtime.handle(person, message(text)))
    assert answer
    assert len(runtime.adapter.calls) == 1
    return runtime.adapter.calls[0][1]


def test_runtime_puts_the_hint_right_before_the_user_message(tmp_path):
    messages = _messages_for(tmp_path, "Сколько будет 48 271 × 9 356?")
    assert messages[-1] == {"role": "user", "content": "Сколько будет 48 271 × 9 356?"}
    where = [i for i, m in enumerate(messages) if m["content"].startswith("Точный расчёт")]
    assert len(where) == 1
    # only system notes (the Jeff 2.0 director may add one) stand between the hint and the user message
    assert all(m["role"] == "system" for m in messages[where[0]:-1])
    assert "48271 * 9356 = 451623476" in messages[where[0]]["content"]
    assert sum(m["content"].startswith("Точный расчёт") for m in messages if m["role"] == "system") == 1


def test_runtime_leaves_the_persona_and_privacy_rules_byte_identical(tmp_path):
    with_hint = _messages_for(tmp_path / "on", "Сколько будет 48 271 × 9 356?")
    without = _messages_for(tmp_path / "off", "Сколько будет 48 271 × 9 356?", off=True)
    assert with_hint[0] == without[0]                                  # the system prompt
    assert [m for m in with_hint if not m["content"].startswith("Точный расчёт")] == without
    assert not any(m["content"].startswith("Точный расчёт") for m in without)


def test_runtime_switch_off_means_no_hint(tmp_path):
    messages = _messages_for(tmp_path, "Посчитай 123456789 * 987654321", off=True)
    assert not any("Точный расчёт" in m["content"] for m in messages)


def test_runtime_negative_control_prompt_is_identical_with_the_patch_on_and_off(tmp_path):
    for index, item in enumerate(i for i in ITEMS if i["control"]):
        on = _messages_for(tmp_path / f"on{index}", item["question"])
        off = _messages_for(tmp_path / f"off{index}", item["question"], off=True)
        assert on == off, item["question"]
        assert not any("Точный расчёт" in m["content"] for m in on)


def test_runtime_survives_a_failing_hint_builder(tmp_path, monkeypatch):
    def boom(_text):
        raise RuntimeError("bug in the helper")
    monkeypatch.setattr("bcc.pit.math_assist.hint_text", boom)
    messages = _messages_for(tmp_path, "Сколько будет 2+2?")
    assert messages[-1]["role"] == "user" and not any("Точный расчёт" in m["content"] for m in messages)


def test_runtime_hint_only_for_the_single_unambiguous_calculation(tmp_path):
    for index, text in enumerate(["Расскажи про инфляцию", "Мне 30 лет, что почитать?", "Привет, 2-3 дня буду занят",
                                  "Сколько будет 2+2 и сколько будет 3*3?"]):
        messages = _messages_for(tmp_path / f"c{index}", text)
        assert not any("Точный расчёт" in m["content"] for m in messages), text


# ---------------------------------------------------------------------------------------- owner API
async def test_api_switch_roundtrip_default_on_and_reset_keeps_an_explicit_off(env, pit_setup):
    c = env.client
    body = {"defaults": {"behavior_scales": {}, "system_extra": ""}}
    assert (await c.get("/api/jeff-settings")).json()["math_assist"] is True
    r = await c.put("/api/jeff-settings", json={**body, "math_assist": False})
    assert r.status_code == 200 and r.json()["settings"]["math_assist"] is False
    assert js.math_assist_enabled(pit_setup) is False
    assert (await c.get("/api/jeff-settings")).json()["math_assist"] is False
    await c.put("/api/jeff-settings", json={"defaults": {"behavior_scales": {"humor": 9}, "system_extra": ""}})
    assert js.math_assist_enabled(pit_setup) is False                  # a save that does not mention it keeps it
    await c.post("/api/jeff-settings/reset", json={})
    assert js.math_assist_enabled(pit_setup) is False                  # a style reset is not a feature switch
    r = await c.put("/api/jeff-settings", json={**body, "math_assist": True})
    assert r.status_code == 200 and js.math_assist_enabled(pit_setup) is True
    saved = json.loads(js.settings_path(pit_setup).read_text(encoding="utf-8"))
    assert "math_assist" not in saved                                   # default on = not written (old builds read the file)
