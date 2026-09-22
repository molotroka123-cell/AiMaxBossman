"""JevDecisionProvider + client: offline, against a mocked HTTP server.

Every strictness rule has a pair: the legitimate case passes AND the bad case is
rejected (negative control). No paid calls: the server is 127.0.0.1.
"""
from __future__ import annotations

import json
import logging

import pytest

from bcc.jev import config as jev_config
from bcc.jev.client import (CircuitBreaker, JevClient, JevCircuitOpen, JevDisabled, JevInvalidResponse,
                            JevNoKey, JevUnavailable, validate_choice)
from bcc.jev.decision import (Baseline, HEADS, JevDecisionProvider, ShadowRecorder, TaskContext,
                              build_questions, effective_needs_approval, parse_decision)

from .jev_mock import FAKE_KEY, MockJev, answer_all, choice


@pytest.fixture
def mock():
    server = MockJev()
    yield server
    server.close()


@pytest.fixture
def jev_env(monkeypatch, mock, tmp_path):
    for name in ("TYPESAFE_API_KEY", "BOSSMAN_JEV_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BOSSMAN_JEV_ENABLED", "1")
    monkeypatch.setenv("BOSSMAN_JEV_API_KEY", FAKE_KEY)
    monkeypatch.setenv("BOSSMAN_JEV_ENDPOINT", mock.url)
    monkeypatch.setenv("BOSSMAN_JEV_TIMEOUT_MS", "300")
    monkeypatch.setenv("BOSSMAN_JEV_MAX_RETRIES", "1")
    monkeypatch.setenv("BOSSMAN_JEV_KILL_FILE", str(tmp_path / "jev.disabled"))
    return mock


def _client(**kw):
    return JevClient(jev_config.load(), sleep=lambda _s: None, **kw)


CTX = TaskContext(task_id=7, kind="coding", prompt="Refactor module X and run tests", tools=["terminal.run"])


# ------------------------------------------------------------------ success

def test_success_one_request_all_heads(jev_env):
    jev_env.script = [("json", lambda req: answer_all(req["questions"], {
        "model_route": "local_reasoner", "tool_route": "terminal", "complexity": "medium", "risk": "low",
        "needs_owner_approval": "no", "needs_strong_verifier": "no", "retry_or_escalate": "proceed"}))]
    provider = JevDecisionProvider(_client())
    d = provider.decide(CTX)
    assert (d.model_route, d.tool_route, d.complexity_score, d.risk_score) == ("local_reasoner", "terminal", 0.5, 0.2)
    assert d.needs_owner_approval is False and d.retry_or_escalate == "proceed"
    assert len(jev_env.requests) == 1
    req = jev_env.requests[0]
    assert req["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
    assert set(req["body"]["questions"]) == set(HEADS)
    assert all(q["type"] == "choice" for q in req["body"]["questions"].values())
    assert req["body"]["model"] == jev_config.DEFAULT_MODEL


def test_shadow_records_agreement_and_never_is_authoritative(jev_env, tmp_path):
    jev_env.script = [("json", lambda req: answer_all(req["questions"], {"model_route": "cloud_reasoner"}))]
    rec = ShadowRecorder(tmp_path / "shadow.jsonl")
    provider = JevDecisionProvider(_client(), rec)
    row = provider.shadow(CTX, Baseline(model_route="cloud_reasoner", locality="cloud", tool_route="none"))
    assert row["authoritative"] is False and row["fallback_reason"] is None
    assert row["agreement"]["model_route"] is True and row["agreement"]["locality"] is True
    assert row["agreement"]["tool_route"] is True          # first option "none" picked by mock
    row2 = provider.shadow(CTX, Baseline(locality="local"))
    assert row2["agreement"]["locality"] is False and row2["agreement"]["model_route"] is None
    assert rec.summary()["agreement"]["locality"] == {"agree": 1, "n": 2, "rate": 0.5}
    assert len((tmp_path / "shadow.jsonl").read_text().splitlines()) == 2


# ------------------------------------------------------------------ failures → fallback

def test_timeout_is_bounded_and_falls_back(jev_env):
    jev_env.script = [("sleep", 1.0, ("json", lambda req: answer_all(req["questions"])))]
    provider = JevDecisionProvider(_client())
    row = provider.shadow(CTX, Baseline())
    assert row["fallback_reason"] == "timeout" and row["jev"] is None
    assert len(jev_env.requests) == 2                       # 1 try + max_retries=1, never more


def test_4xx_is_not_retried(jev_env):
    jev_env.script = [("status", 400)]
    with pytest.raises(JevUnavailable) as exc:
        _client().ask({}, build_questions())
    assert exc.value.reason == "http_4xx" and len(jev_env.requests) == 1


def test_500_is_not_retried_but_503_is(jev_env):
    jev_env.script = [("status", 500)]
    with pytest.raises(JevUnavailable) as exc:
        _client().ask({}, build_questions())
    assert exc.value.reason == "http_5xx" and len(jev_env.requests) == 1
    jev_env.requests.clear()
    jev_env.script = [("status", 503)]
    with pytest.raises(JevUnavailable):
        _client().ask({}, build_questions())
    assert len(jev_env.requests) == 2


def test_rate_limit_retried_then_success(jev_env):
    jev_env.script = [("status", 429), ("json", lambda req: answer_all(req["questions"]))]
    d = JevDecisionProvider(_client()).decide(CTX)
    assert d.model_route == "local_fast" and len(jev_env.requests) == 2


def test_rate_limit_persistent_falls_back(jev_env):
    jev_env.script = [("status", 429)]
    row = JevDecisionProvider(_client()).shadow(CTX, Baseline())
    assert row["fallback_reason"] == "rate_limited"


def test_non_json_response_fails_closed(jev_env):
    jev_env.script = [("raw", b"<html>not json</html>")]
    row = JevDecisionProvider(_client()).shadow(CTX, Baseline())
    assert row["fallback_reason"] == "invalid_schema" and row["jev"] is None


@pytest.mark.parametrize("mutate", [
    lambda a: a["model_route"].update(choice="gpt-5-turbo"),                    # not an offered option
    lambda a: a["risk"]["probabilities"].update(low=0.7),                       # sum != 1
    lambda a: a["risk"]["probabilities"].pop("high"),                           # missing id
    lambda a: a["risk"]["probabilities"].update(extra=0.0),                     # extra id
    lambda a: a["complexity"].update(confidence=float("nan")),                  # NaN
    lambda a: a["complexity"].update(confidence=True),                          # bool is not a number
    lambda a: a["tool_route"].update(choice="rm -rf /"),                        # free text
    lambda a: a.pop("needs_owner_approval"),                                    # missing head
    lambda a: a["retry_or_escalate"].update(probabilities={"proceed": 0.1, "retry": 0.1, "escalate": 0.8}),
])
def test_malformed_answers_rejected(jev_env, mutate):
    def respond(req):
        payload = answer_all(req["questions"])
        mutate(payload["answers"])
        return payload
    jev_env.script = [("json", respond)]
    with pytest.raises(JevInvalidResponse):
        JevDecisionProvider(_client()).decide(CTX)


def test_malformed_negative_control_valid_passes(jev_env):
    """Control for the parametrized rejects: the untouched envelope is accepted."""
    jev_env.script = [("json", lambda req: answer_all(req["questions"]))]
    assert JevDecisionProvider(_client()).decide(CTX).model_route == "local_fast"


def test_validate_choice_accepts_tie_and_rejects_non_max():
    assert validate_choice(choice(["a", "b"], "a") | {"probabilities": {"a": 0.5, "b": 0.5}}, ["a", "b"])
    with pytest.raises(JevInvalidResponse):
        validate_choice({"choice": "a", "confidence": 0.9, "probabilities": {"a": 0.4, "b": 0.6}}, ["a", "b"])


def test_envelope_without_model_rejected(jev_env):
    jev_env.script = [("json", lambda req: {"answers": answer_all(req["questions"])["answers"]})]
    with pytest.raises(JevInvalidResponse):
        _client().ask({}, build_questions())


# ------------------------------------------------------------------ circuit breaker

def test_circuit_breaker_opens_then_half_opens_and_closes(jev_env, monkeypatch):
    monkeypatch.setenv("BOSSMAN_JEV_MAX_RETRIES", "0")
    monkeypatch.setenv("BOSSMAN_JEV_BREAKER_FAILURES", "2")
    now = [1000.0]
    clock = lambda: now[0]  # noqa: E731
    cfg = jev_config.load()
    client = JevClient(cfg, sleep=lambda _s: None, clock=clock,
                       breaker=CircuitBreaker(2, 30.0, clock=clock))
    jev_env.script = [("status", 500)]
    for _ in range(2):
        with pytest.raises(JevUnavailable):
            client.ask({}, build_questions())
    assert client.breaker.state == "open"
    sent = len(jev_env.requests)
    with pytest.raises(JevCircuitOpen):
        client.ask({}, build_questions())
    assert len(jev_env.requests) == sent                     # open: nothing sent
    now[0] += 31
    assert client.breaker.state == "half_open"
    jev_env.script = [("json", lambda req: answer_all(req["questions"]))]
    jev_env.requests.clear()
    client.ask({}, build_questions())                        # the single trial succeeds
    assert client.breaker.state == "closed" and len(jev_env.requests) == 1


def test_circuit_half_open_failure_reopens(jev_env, monkeypatch):
    monkeypatch.setenv("BOSSMAN_JEV_MAX_RETRIES", "0")
    now = [0.0]
    clock = lambda: now[0]  # noqa: E731
    client = JevClient(jev_config.load(), sleep=lambda _s: None, clock=clock,
                       breaker=CircuitBreaker(1, 10.0, clock=clock))
    jev_env.script = [("status", 500)]
    with pytest.raises(JevUnavailable):
        client.ask({}, build_questions())
    now[0] += 11
    with pytest.raises(JevUnavailable):
        client.ask({}, build_questions())                    # half-open trial fails
    assert client.breaker.state == "open"
    with pytest.raises(JevCircuitOpen):
        client.ask({}, build_questions())


def test_invalid_schema_counts_toward_breaker(jev_env):
    jev_env.script = [("raw", b"{}")]
    client = JevClient(jev_config.load(), sleep=lambda _s: None, breaker=CircuitBreaker(2, 60.0))
    provider = JevDecisionProvider(client)
    provider.shadow(CTX, Baseline())
    provider.shadow(CTX, Baseline())
    assert client.breaker.state == "open"
    assert provider.shadow(CTX, Baseline())["fallback_reason"] == "circuit_open"


# ------------------------------------------------------------------ disabled / no key / kill switch

def test_disabled_by_default_sends_nothing(jev_env, monkeypatch):
    monkeypatch.delenv("BOSSMAN_JEV_ENABLED")
    assert jev_config.load().enabled is False
    with pytest.raises(JevDisabled):
        _client().ask({}, build_questions())
    assert jev_env.requests == []


def test_enabled_control_sends_request(jev_env):
    """Control for the disabled test: the same call with the flag on does reach the server."""
    _client().ask({}, build_questions())
    assert len(jev_env.requests) == 1


def test_no_key_sends_nothing(jev_env, monkeypatch):
    monkeypatch.delenv("BOSSMAN_JEV_API_KEY")
    with pytest.raises(JevNoKey):
        _client().ask({}, build_questions())
    assert jev_env.requests == []


def test_kill_file_stops_immediately(jev_env, tmp_path):
    (tmp_path / "jev.disabled").write_text("off")
    row = JevDecisionProvider(_client()).shadow(CTX, Baseline())
    assert row["fallback_reason"] == "disabled" and jev_env.requests == []
    (tmp_path / "jev.disabled").unlink()
    assert JevDecisionProvider(_client()).shadow(CTX, Baseline())["fallback_reason"] is None


# ------------------------------------------------------------------ secrets

def test_secrets_never_in_logs_records_or_errors(jev_env, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    prompt_secret = "sk-live-ABCDEFGHIJKLMNOP1234"                 # ci-secret-scan: allow (canary)
    ctx = TaskContext(task_id=1, kind="generic", prompt=f"use key {prompt_secret} to deploy")
    rec = ShadowRecorder(tmp_path / "s.jsonl")
    for script in ([("status", 401, json.dumps({"echo": FAKE_KEY}).encode())],
                   [("raw", f"garbage {FAKE_KEY}".encode())],
                   [("json", lambda req: answer_all(req["questions"]))]):
        jev_env.script = script
        client = _client()
        row = JevDecisionProvider(client, rec).shadow(ctx, Baseline())
        blob = json.dumps(row) + json.dumps(client.status()) + json.dumps(jev_config.load().public())
        assert FAKE_KEY not in blob and prompt_secret not in blob
    disk = (tmp_path / "s.jsonl").read_text()
    assert FAKE_KEY not in disk and prompt_secret not in disk
    assert FAKE_KEY not in caplog.text
    # The prompt secret never left the process either.
    assert all(prompt_secret not in json.dumps(r["body"]) for r in jev_env.requests)
    assert jev_config.load().public()["key_present"] is True


# ------------------------------------------------------------------ approval / escalation semantics

def _decision(**picks):
    questions = build_questions()
    return parse_decision(answer_all(questions, picks), min_confidence=0.6, latency_ms=1)


def test_jev_cannot_clear_an_existing_approval():
    no = _decision(needs_owner_approval="no")
    assert effective_needs_approval(True, no) is True          # existing "ask" stays
    assert effective_needs_approval(False, None) is False      # no Jev → existing policy only


def test_jev_can_only_add_approval_control():
    yes = _decision(needs_owner_approval="yes")
    assert effective_needs_approval(False, yes) is True


def test_low_confidence_or_high_risk_escalates():
    low = parse_decision(answer_all(build_questions(), confidence=0.3), min_confidence=0.6, latency_ms=1)
    assert low.low_confidence and low.needs_strong_verifier and low.retry_or_escalate == "escalate"
    risky = _decision(risk="high")
    assert risky.needs_strong_verifier and risky.retry_or_escalate == "escalate"
    calm = _decision(risk="low", needs_strong_verifier="no")                                # control: no escalation invented
    assert not calm.needs_strong_verifier and calm.retry_or_escalate == "proceed"
