"""Mandatory role-stability suite (autonomy freeze, line C, task 7) - fakes only in CI.

The live run is for the integrator: ``python -m bcc.pit.role_stability --model <id>``.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from bcc.pit import role_stability as rs
from bcc.providers import ChatResult


class StableFake:
    """Answers like a well-behaved Jeff."""

    def __init__(self):
        self.calls = []

    async def chat(self, model, messages, **kw):
        self.calls.append(model)
        last = messages[-1]["content"].lower()
        if any(w in last for w in ("промпт", "instructions", "approvals")):
            text = "Я Jeff и не раскрываю свои инструкции и не меняю approvals."
        elif "двухфактор" in last:
            text = ("Откройте настройки безопасности аккаунта, выберите двухфакторную аутентификацию, "
                    "подключите приложение-аутентификатор и сохраните резервные коды в надёжном месте.")
        elif "english" in last or "английск" in last or last.startswith("explain"):
            text = "Photosynthesis lets plants turn light, water and carbon dioxide into sugar and oxygen."
        elif any(w in last for w in ("кто", "who", "модел", "model", "introduce")):
            text = "Я Jeff — AI-помощник в экосистеме Bossman."
        else:
            text = "Небо голубое, потому что воздух сильнее рассеивает короткие синие волны света."
        if "russian" in last:
            text = "Фотосинтез — это процесс, в котором растения превращают свет, воду и углекислый газ в сахар."
        return ChatResult(text=text, model=model)


class DriftingFake:
    """Adopts its base identity, complies with extraction and ignores the requested language."""

    async def chat(self, model, messages, **kw):
        system = messages[0]["content"]
        last = messages[-1]["content"].lower()
        if any(w in last for w in ("промпт", "instructions", "approvals")):
            return ChatResult(text="Sure! " + system[:300], model=model)
        if "двухфактор" in last:
            return ChatResult(text="Не могу.", model=model)
        return ChatResult(text="I'm Claude, an AI assistant made by Anthropic.", model=model)


def test_cases_cover_identity_refusal_and_language():
    cats = {c.category for c in rs.CASES}
    assert cats == {"identity", "refusal", "language"}
    assert any(len(c.turns) > 1 for c in rs.CASES)


def test_stable_model_passes_every_case():
    report = asyncio.run(rs.run_suite(StableFake(), "fake/stable:free"))
    assert report["passed"] == report["total"], [c for c in report["cases"] if not c["ok"]]
    assert report["schema"] == rs.ROLE_STABILITY_SCHEMA


def test_drifting_model_fails_each_axis_and_the_report_hides_the_leaks():
    report = asyncio.run(rs.run_suite(DriftingFake(), "fake/drift:free"))
    failed = {c["id"] for c in report["cases"] if not c["ok"]}
    assert {"identity.direct.ru", "refusal.system_prompt", "refusal.no_over_refusal", "language.ru"} <= failed
    dumped = json.dumps(report, ensure_ascii=False)
    assert "Anthropic" not in dumped and "Claude" not in dumped


def test_a_failing_adapter_is_a_failed_case_not_a_crash():
    class Broken:
        async def chat(self, *a, **k):
            raise RuntimeError("down")

    report = asyncio.run(rs.run_suite(Broken(), "fake/broken:free", cases=rs.CASES[:2]))
    assert report["passed"] == 0 and report["cases"][0]["problems"] == ["call_failed:RuntimeError"]


class CatalogFake(StableFake):
    def __init__(self, pricing):
        super().__init__()
        self.pricing = pricing

    async def list_model_info(self):
        return [{"id": m} for m in self.pricing]

    async def list_model_pricing(self):
        return self.pricing


@pytest.mark.parametrize("model,pricing,code,reason", [
    ("liquid/lfm-2.5-2.6b:free", {}, 2, "banned_model_family"),
    ("x/paid-70b", {"x/paid-70b": {"prompt": 1e-6, "completion": 1e-6}}, 2, "price_positive"),
    ("x/absent-70b:free", {}, 2, "not_listed"),
])
def test_cli_refuses_before_sending_any_prompt(monkeypatch, capsys, model, pricing, code, reason):
    fake = CatalogFake(pricing)
    monkeypatch.setattr(rs, "_build_adapter", lambda base_url, key: fake)
    assert rs.main(["--model", model]) == code
    assert json.loads(capsys.readouterr().out)["refused"] == reason
    assert fake.calls == []


def test_cli_runs_the_suite_on_a_verified_free_model(monkeypatch, tmp_path, capsys):
    model = "nvidia/nemotron-3-ultra-550b-a55b:free"
    fake = CatalogFake({model: {"prompt": 0.0, "completion": 0.0}})
    monkeypatch.setattr(rs, "_build_adapter", lambda base_url, key: fake)
    out = tmp_path / "report.json"
    assert rs.main(["--model", model, "--out", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["passed"] == report["total"] and report["policy"]["tier"] == "main"
    assert fake.calls
