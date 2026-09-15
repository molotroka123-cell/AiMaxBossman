"""Брейкер обязан держать хостинговую и владельческую улику РАЗДЕЛЬНО.

Ценность этого харнесса ровно одна: он не даёт зелёному раннеру закрыть пункт,
который раннер структурно доказать не может. Если бы HOSTED_PASS попадал в ту же
колонку, что OWNER_LIVE_PASS, список невыполненного схлопнулся бы в ноль, ничего
на машине владельца при этом не проверив. Поэтому тесты ниже бьют не по формату
отчёта, а по этой границе.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load(name: str):
    """Регистрация в sys.modules обязательна: без неё @dataclass внутри скрипта
    не находит свой модуль и падает на ровном месте."""
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def breaker(tmp_path, monkeypatch):
    monkeypatch.setenv("BOSSMAN_BREAKER_ROOT", str(tmp_path / "breaker"))
    return _load("owner_breaker")


def test_self_check_passes_on_the_shipped_corpus(breaker, capsys):
    assert breaker.main(["verify"]) == 0
    assert "исправен" in capsys.readouterr().out


def test_the_breaker_sequence_is_b1_through_b12(breaker):
    assert [c.ident for c in breaker.BREAKER] == [f"B{n}" for n in range(1, 13)]
    titles = " ".join(c.title.lower() for c in breaker.BREAKER)
    for required in ("json", "браузер", "файл", "openhands", "video", "fallback",
                     "подтверждение", "перезапуск"):
        assert required in titles, required


def test_every_step_records_the_evidence_fields(breaker):
    """§5: шаг без пост-состояния — это отчёт о клике, а не о результате."""
    fields = dict(breaker.FIELDS)
    for required in ("task_id", "run_id", "model", "status", "effect_evidence",
                     "approvals", "retries", "http_errors", "dead_clicks",
                     "console_errors", "tokens_cost", "resource_telemetry"):
        assert required in fields, required


def test_the_run_is_bound_to_a_build_sha(breaker):
    """Улика без SHA не привязывается к коммиту, а значит не улика."""
    assert breaker.BUILD_SHA
    assert re.fullmatch(r"[0-9a-f]{40}(-DIRTY)?|SOURCE_IDENTITY_UNKNOWN", breaker.BUILD_SHA)


def test_every_item_says_who_can_prove_it(breaker):
    """Пункт, про который не сказано ни «закрыто раннером», ни «нужна машина
    владельца», — это пункт, который тихо считается закрытым."""
    for check in breaker.ALL:
        assert check.hosted or check.owner_live, check.ident
    for check in breaker.LIVE:
        assert check.owner_live, f"{check.ident}: живой пункт без владельческой части"


def test_hosted_pass_does_not_close_an_owner_live_item(breaker, capsys):
    """Главная граница. B3 доказан на раннере политикой браузера, но живой сайт
    и живую проверку «вы человек» раннер не проходит; HOSTED_PASS обязан
    оставить пункт в списке невыполненного, а не убрать оттуда."""
    state = breaker.load_state()
    state["results"]["B3"] = {"ident": "B3", "title": "t", "verdict": breaker.HOSTED_PASS,
                             "blocks_release": True}
    breaker.save_state(state)
    gates = breaker._gates(breaker.load_state())
    assert "B3" in gates["owner_live_outstanding"]
    assert gates["breaker_verdict"] != "OWNER_LIVE_COMPLETE"

    # Негативный контроль: тот же пункт с настоящей владельческой уликой уходит.
    state = breaker.load_state()
    state["results"]["B3"]["verdict"] = breaker.OWNER_LIVE_PASS
    breaker.save_state(state)
    assert "B3" not in breaker._gates(breaker.load_state())["owner_live_outstanding"]


def test_an_item_hosting_fully_covers_needs_no_owner_verdict(breaker):
    """Обратная сторона той же границы: B4 (создание файла) раннер доказывает
    целиком, и он не обязан ждать вечера владельца."""
    b4 = next(c for c in breaker.BREAKER if c.ident == "B4")
    assert b4.hosted and not b4.owner_live
    state = breaker.load_state()
    state["results"]["B4"] = {"ident": "B4", "title": "t", "verdict": breaker.HOSTED_PASS,
                              "blocks_release": True}
    breaker.save_state(state)
    assert "B4" not in breaker._gates(breaker.load_state())["owner_live_outstanding"]


def test_a_blocking_failure_is_never_reported_as_incomplete(breaker):
    state = breaker.load_state()
    state["results"]["B2"] = {"ident": "B2", "title": "t", "verdict": breaker.FAIL,
                              "blocks_release": True}
    breaker.save_state(state)
    gates = breaker._gates(breaker.load_state())
    assert gates["breaker_verdict"] == "FAIL" and gates["blocking_failures"] == ["B2"]


def test_report_survives_an_empty_state_and_names_every_item(breaker, tmp_path, capsys):
    out = tmp_path / "report.md"
    assert breaker.main(["report", "--md-out", str(out)]) == 1
    body = out.read_text(encoding="utf-8")
    for check in breaker.ALL:
        assert check.ident in body, check.ident
    assert "NOT_RUN" in body


def test_corrupt_state_does_not_crash_the_harness(breaker):
    path = breaker.state_path()
    path.write_text("{not json", encoding="utf-8")
    state = breaker.load_state()
    assert state["results"] == {} and state.get("corrupt_previous_state")


def test_recorded_state_is_valid_json_on_disk(breaker):
    state = breaker.load_state()
    state["results"]["B1"] = {"ident": "B1", "title": "t",
                              "verdict": breaker.HOSTED_PASS, "blocks_release": True}
    path = breaker.save_state(state)
    assert json.loads(path.read_text(encoding="utf-8"))["results"]["B1"]["ident"] == "B1"
