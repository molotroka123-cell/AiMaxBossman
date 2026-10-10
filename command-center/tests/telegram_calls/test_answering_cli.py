"""`bossman call answer …` and `bossman call selftest answering-machine`: thin client of the same API, no secrets, honest wording."""
from __future__ import annotations

import time

import pytest

import bcc.terminal_cli.cli as cli
from bcc.terminal_cli import calls
from bcc.terminal_cli.api_client import BossmanError
from bcc.terminal_cli.cli import main
from bcc.terminal_cli.records import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE

from .test_cli_call import BASE, FakeClient, fake, last_json, run, status   # noqa: F401  (``fake`` is the fixture)


def answering(**over) -> dict:
    base = {"enabled": True, "armed": True, "listening": True, "ready_state": "ready", "ringing": False, "in_call": False, "stopped": False,
            "transport": "telegram", "live_tested": False, "pending_reports": 0, "ring_delay_s": 12, "max_call_s": 180,
            "allow_count": 0, "deny_count": 0, "allow_unknown": True, "mode": "telegram", "stop": {"active": False}}
    base.update(over)
    return base


def settings(**over) -> dict:
    base = {"answering_machine": True, "answer_ring_delay_s": 12, "answer_max_call_s": 180, "answer_allow_ids": [], "answer_deny_ids": [],
            "answer_allow_unknown": True}
    base.update(over)
    return base


def test_the_answering_machine_is_off_by_default_in_the_status_line_and_says_what_is_missing():
    assert "выключен" in calls.answer_line({"enabled": False})
    line = calls.answer_line({"enabled": True, "armed": False})
    assert "не запущен" in line and "не принимаются" in line
    live = calls.answer_line(answering())
    assert "слушает" in live and "не проверен в живую" in live and "пауза до ответа 12" in live
    assert "модели загружаются" in calls.answer_line(answering(ready_state="loading"))
    assert "ОСТАНОВЛЕН" in calls.answer_line(answering(stopped=True)) and "ждут отправки: 2" in calls.answer_line(answering(pending_reports=2))
    assert "не проверен" not in calls.answer_line(answering(live_tested=True))


def test_call_status_shows_the_answering_line(fake, capsys):
    fake({("GET", f"{BASE}/status"): status(answering=answering())})
    code, out, _ = run(capsys, "call", "status")
    assert code == EXIT_OK and "Автоответчик: слушает" in out


def test_answer_on_enables_through_the_settings_route_and_never_claims_a_live_test(fake, capsys):
    c = fake({("PUT", f"{BASE}/settings"): lambda body: {**settings(), "answering": {"armed": True}}})
    code, out, _ = run(capsys, "call", "answer", "on")
    assert code == EXIT_OK and c.bodies("PUT", f"{BASE}/settings") == [{"answering_machine": True}]
    assert "слушает" in out and "ещё НЕ проверен в живую" in out and "представится ассистентом" in out


def test_answer_on_that_the_backend_cannot_arm_is_blocked_and_says_how_to_fix_it(fake, capsys):
    err = {"code": "NOT_LOGGED_IN", "message": "Аккаунт не подключён.", "hint": "Пройдите вход."}
    fake({("PUT", f"{BASE}/settings"): {**settings(), "answering": {"armed": False, "error": err}}})
    code, out, _ = run(capsys, "call", "answer", "on", "--json")
    rec = last_json(out)
    assert code == EXIT_BLOCKED and rec["type"] == "call_answer" and rec["error"] == "NOT_LOGGED_IN" and rec["ok"] is False


def test_answer_off_says_the_phone_rings_as_usual(fake, capsys):
    c = fake({("PUT", f"{BASE}/settings"): {**settings(answering_machine=False), "answering": {"armed": False}}})
    code, out, _ = run(capsys, "call", "answer", "off")
    assert code == EXIT_OK and c.bodies("PUT", f"{BASE}/settings") == [{"answering_machine": False}] and "выключен" in out


def test_answer_status_reads_the_answering_route(fake, capsys):
    fake({("GET", f"{BASE}/answering"): answering(pending_reports=3)})
    code, out, _ = run(capsys, "call", "answer", "status")
    assert code == EXIT_OK and "ждут отправки: 3" in out


def test_answer_config_merges_the_lists_instead_of_replacing_them(fake, capsys):
    c = fake({("GET", f"{BASE}/settings"): settings(answer_allow_ids=[5], answer_deny_ids=[9]),
              ("PUT", f"{BASE}/settings"): lambda body: {**settings(), **body}})
    code, out, _ = run(capsys, "call", "answer", "config", "--ring-delay", "5", "--allow", "7", "--deny", "9", "--deny", "10",
                       "--allow-unknown", "no", "--greeting", "Это ИИ-ассистент владельца.")
    assert code == EXIT_OK
    [body] = c.bodies("PUT", f"{BASE}/settings")
    assert body == {"answer_ring_delay_s": 5, "answer_allow_ids": [5, 7], "answer_deny_ids": [9, 10], "answer_allow_unknown": False,
                    "answer_greeting": "Это ИИ-ассистент владельца."}
    assert "не отвечаем" in out


def test_answer_config_clears_a_list_and_refuses_contradictions_and_empty_requests(fake, capsys):
    c = fake({("PUT", f"{BASE}/settings"): lambda body: {**settings(), **body}, ("GET", f"{BASE}/settings"): settings()})
    assert run(capsys, "call", "answer", "config", "--clear-deny")[0] == EXIT_OK
    assert c.bodies("PUT", f"{BASE}/settings") == [{"answer_deny_ids": []}]
    code, _, err = run(capsys, "call", "answer", "config", "--clear-allow", "--allow", "5")
    assert code == EXIT_USAGE and len(c.bodies("PUT", f"{BASE}/settings")) == 1
    code, _, err = run(capsys, "call", "answer", "config")
    assert code == EXIT_USAGE and "нечего менять" in err


def test_a_refused_setting_is_reported_with_the_backends_words(fake, capsys):
    err = BossmanError("Настройки не приняты: greeting must say that an assistant answers", kind="validation", code="SETTINGS_INVALID")
    fake({("PUT", f"{BASE}/settings"): err})
    code, _, stderr = run(capsys, "call", "answer", "config", "--greeting", "Слушаю вас")
    assert code != EXIT_OK and "must say that an assistant answers" in stderr


def test_answer_reports_lists_and_shows_one_with_the_owner_notice_text(fake, capsys):
    report = {"id": "ar-0123456789ab", "caller": {"id": 5, "label": "Иван", "known": True}, "received_at": time.time() - 90, "duration_s": 40,
              "answered": True, "outcome": "message_taken", "outcome_label": "принято сообщение", "test": False, "notify": True, "delivered": False,
              "summary": ["Суть: просит перенести встречу", "Просьба перезвонить: нет"], "flags": [], "callback": {"requested": False},
              "transcript": [{"role": "caller", "text": "Перенесите встречу"}]}
    fake({("GET", f"{BASE}/answering/reports"): {"items": [report]}, ("GET", f"{BASE}/answering/reports/ar-0123456789ab"): report})
    code, out, _ = run(capsys, "call", "answer", "reports")
    assert code == EXIT_OK and "ar-0123456789ab" in out and "Иван" in out and "не отправлен владельцу" in out
    code, out, _ = run(capsys, "call", "answer", "reports", "ar-0123456789ab")
    assert code == EXIT_OK and "Суть: просит перенести встречу" in out and "Звонящий: Перенесите встречу" in out


def test_answer_without_an_action_is_a_usage_error(fake, capsys):
    fake({})
    assert run(capsys, "call", "answer")[0] == EXIT_USAGE


def test_selftest_accepts_the_hyphenated_scenario_and_labels_it_as_a_test(fake, capsys):
    results = [{"scenario": "answering_machine", "verdict": "PASS", "checks": {"answered_after_ring_delay": True}}]
    c = fake({("POST", f"{BASE}/selftest"): {"verdict": "PASS", "results": results, "evidence_level": "loopback", "label": "ТЕСТ БЕЗ TELEGRAM"}})
    code, out, _ = run(capsys, "call", "selftest", "answering-machine")
    assert code == EXIT_OK and c.bodies("POST", f"{BASE}/selftest") == [{"scenario": "answering_machine"}]
    assert "ТЕСТ БЕЗ TELEGRAM" in out and "answered_after_ring_delay" in out


def test_selftest_still_refuses_an_unknown_scenario(fake, capsys):
    fake({})
    assert run(capsys, "call", "selftest", "nonsense")[0] == EXIT_USAGE


def test_the_help_lists_the_answering_machine_and_the_selftest_scenario(capsys):
    assert main(["call", "--help"]) == 0
    out = capsys.readouterr().out
    assert "bossman call answer on | off | status" in out and "answering-machine" in out
