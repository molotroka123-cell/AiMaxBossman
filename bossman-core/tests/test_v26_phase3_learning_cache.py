"""V2.6 Phase 3 — Failure Pattern Learner, Counterfactual Verifier,
Verified Execution Cache, Task Compiler (core-часть).

Матрица раздела 31: no one-shot promotion / correct recovery / wrong-context
rejection; valid hit / stale invalidation / security-sensitive no-cache;
simple bypass / complex DAG.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import pytest

from bossman import counterfactual as cf
from bossman import exec_cache as ec
from bossman import failure_patterns as fp
from bossman import task_compiler as tc
from bossman.signals import DecisionSignals, derive_signals


# ---------------- Module C: failure patterns ----------------

@dataclass
class _Rec:
    task_id: str = "t1"
    failure_id: str = "f1"
    error_class: str = "network"
    environment: dict = field(default_factory=lambda: {"agent": "coder"})
    created_at: float = field(default_factory=time.time)
    resolved: bool = False
    attempted_fix: str = ""
    result: str = ""


def test_classify_error_maps_runner_symptoms():
    assert fp.classify_error("остановлено: превышен timeout_min агента") == "timeout"
    assert fp.classify_error("остановлено: превышен max_steps агента") == "budget_steps"
    assert fp.classify_error("остановлено: превышен max_tokens агента") == "budget_tokens"
    assert fp.classify_error("отправка в облако отклонена") == "cloud_denied"
    assert fp.classify_error("connection refused to host") == "network"
    assert fp.classify_error("ошибка петли:\nTraceback") == "loop_error"
    assert fp.classify_error("что-то невиданное") == "task_failed"


def test_no_pattern_from_single_episode():
    """Один эпизод НИКОГДА не рождает паттерн."""
    assert fp.extract_patterns([_Rec()]) == []
    assert fp.extract_patterns([_Rec(), _Rec(failure_id="f2")]) == []


def test_pattern_needs_min_episodes_and_recovery_needs_min_successes():
    recs = [_Rec(failure_id=f"f{i}") for i in range(fp.MIN_PATTERN_EPISODES)]
    pats = fp.extract_patterns(recs)
    assert len(pats) == 1 and pats[0].episodes == fp.MIN_PATTERN_EPISODES
    assert pats[0].successful_recovery is None, \
        "нет разрешённых эпизодов -> стратегии нет"

    # одна удачная починка не делает стратегию
    recs[0].resolved, recs[0].attempted_fix = True, "re-observe DOM before retry"
    assert fp.extract_patterns(recs)[0].successful_recovery is None

    # MIN_PATTERN_EPISODES одинаковых доказанных починок -> стратегия есть
    proven = [_Rec(failure_id=f"p{i}", resolved=True,
                   attempted_fix="re-observe DOM before retry")
              for i in range(fp.MIN_PATTERN_EPISODES)]
    got = fp.extract_patterns(recs + proven)
    assert got[0].successful_recovery == "re-observe DOM before retry"


def test_wrong_environment_not_transferred():
    """Стратегия agent=coder не рекомендуется для agent=analyst."""
    pats = fp.extract_patterns([
        _Rec(failure_id=f"f{i}", resolved=True, attempted_fix="retry with backoff")
        for i in range(fp.MIN_PATTERN_EPISODES)])
    assert fp.recommended_recovery("network", {"agent": "coder"}, pats) is not None
    assert fp.recommended_recovery("network", {"agent": "analyst"}, pats) is None
    assert fp.recommended_recovery("timeout", {"agent": "coder"}, pats) is None


def test_stale_episodes_decay_out():
    old = time.time() - (fp.PATTERN_MAX_AGE_DAYS + 5) * 86400
    recs = [_Rec(failure_id=f"f{i}", created_at=old) for i in range(5)]
    assert fp.extract_patterns(recs) == []


def test_holdout_episodes_excluded_from_learning():
    from bossman import learning_guard as lg
    try:
        lg.set_holdout(lg.SecretHoldout.seal(["holdout-task"]))
        recs = [_Rec(task_id="holdout-task", failure_id=f"f{i}") for i in range(5)]
        assert fp.extract_patterns(recs) == [], \
            "эпизоды holdout-задач не участвуют в обучении (защита в глубину)"
    finally:
        lg.set_holdout(None)


# ---------------- Module D: counterfactual ----------------

def test_assumptions_bounded_and_deterministic():
    a = cf.critical_assumptions("browser.confirmed_click", {})
    assert 1 <= len(a) <= cf.MAX_ASSUMPTIONS
    assert a == cf.critical_assumptions("browser.confirmed_click", {})
    assert any("наблюдением" in x.text for x in a)


def test_assumptions_for_host_shell_and_unknown_tool():
    assert cf.critical_assumptions("run", {})            # есть допущения
    assert cf.critical_assumptions("неведомый.tool", {}) == ()


def test_should_verify_only_for_risk_or_uncertainty():
    assert cf.should_verify(DecisionSignals(risk=0.8))
    assert cf.should_verify(DecisionSignals(uncertainty=0.8))
    assert cf.should_verify(DecisionSignals(uncertainty=0.55, evidence_confidence=0.3))
    assert not cf.should_verify(derive_signals("посчитай 2+2"))


def test_preview_render_lists_assumptions():
    text = cf.render_for_preview(cf.critical_assumptions("http", {}))
    assert "counterfactual" in text and "endpoint" in text
    assert cf.render_for_preview(()) == ""


# ---------------- Module E: execution cache ----------------

def test_cache_valid_hit_and_provenance():
    c = ec.ExecutionCache()
    key = c.key("parsed_file", "doc.pdf", "sha256abc")
    assert c.put(key, {"pages": 3}, verified=True, evidence="parsed doc.pdf")
    rec = c.get(key)
    assert rec is not None and rec.result == {"pages": 3} and rec.verified
    assert c.stats()["hits"] == 1


def test_cache_ttl_expiry_and_env_invalidation():
    c = ec.ExecutionCache()
    key = c.key("pkg_meta", "requests", "2.31")
    c.put(key, "meta", verified=True, ttl_s=0.01, env_fingerprint="env1")
    time.sleep(0.02)
    assert c.get(key, env_fingerprint="env1") is None, "TTL истёк"
    key2 = c.key("pkg_meta", "httpx", "0.27")
    c.put(key2, "meta", verified=True, env_fingerprint="env1")
    assert c.get(key2, env_fingerprint="env2") is None, "окружение изменилось"


def test_cache_refuses_security_sensitive_kinds():
    c = ec.ExecutionCache()
    for kind in ("live_balance", "browser_state", "email_search",
                 "security_state", "credentials"):
        assert not c.put(c.key(kind, "x"), "data", verified=True), kind
        assert c.get(c.key(kind, "x")) is None
    assert c.stats()["rejected_kinds"] == 5


def test_cache_lru_bound_and_prefix_invalidation():
    c = ec.ExecutionCache(max_entries=3)
    for i in range(5):
        c.put(c.key("parsed_file", i), i, verified=True)
    assert c.stats()["entries"] == 3
    assert c.invalidate("parsed_file:") == 3
    assert c.stats()["entries"] == 0


def test_real_window_uses_cache_but_stays_correct(tmp_path, monkeypatch):
    """Правка registry.yaml (mtime) инвалидирует запись сама."""
    from bossman.config import settings
    from bossman.llm import real_window
    reg = tmp_path / "registry.yaml"
    reg.write_text("model_windows:\n  test-alias: 12345\n", encoding="utf-8")
    monkeypatch.setattr(settings, "tools_registry", reg, raising=False)
    assert real_window("test-alias") == 12345
    assert real_window("test-alias") == 12345          # из кэша
    import os
    reg.write_text("model_windows:\n  test-alias: 54321\n", encoding="utf-8")
    os.utime(reg, (time.time() + 5, time.time() + 5))  # гарантированно новый mtime
    assert real_window("test-alias") == 54321, "новый mtime -> новый разбор"


# ---------------- Module F: task compiler ----------------

def test_simple_task_bypasses_decomposition():
    ok, ev = tc.should_decompose(derive_signals("посчитай 2+2"))
    assert not ok and ev <= 0.0


def test_complex_task_decomposes_with_positive_ev():
    sig = derive_signals(
        "исследуй конкурентов, затем создай таблицу-отчёт, после этого отправь сводку")
    ok, ev = tc.should_decompose(sig)
    assert ok and ev > 0.0


def test_compiled_task_topological_order_and_cycle_detection():
    steps = (
        tc.CompiledStep("verify", "verify", depends_on=("artifact",)),
        tc.CompiledStep("research", "web_search"),
        tc.CompiledStep("artifact", "file_create", depends_on=("research",)),
    )
    task = tc.CompiledTask(goal="отчёт", steps=steps)
    assert [s.step_id for s in task.ordered()] == ["research", "artifact", "verify"]

    bad = tc.CompiledTask(goal="цикл", steps=(
        tc.CompiledStep("a", "x", depends_on=("b",)),
        tc.CompiledStep("b", "x", depends_on=("a",))))
    with pytest.raises(ValueError):
        bad.ordered()
