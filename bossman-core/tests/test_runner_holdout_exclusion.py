"""Learning Quality Guard wiring: secret holdout исключается из durable
learning-корпуса (decision/failure). No-op по умолчанию (fast path не тронут).
"""
import bossman.learning_guard as lg
from bossman.runner import _learning_excluded


def test_excluded_false_by_default(monkeypatch):
    lg.set_holdout(None)
    assert _learning_excluded("any-task") is False


def test_holdout_task_is_excluded(monkeypatch):
    lg.set_holdout(lg.SecretHoldout.seal(["hold-42"]))
    try:
        assert _learning_excluded("hold-42") is True
        assert _learning_excluded("open-task") is False
    finally:
        lg.set_holdout(None)


def test_guard_failure_never_breaks_task(monkeypatch):
    # если get_holdout бросит — не роняем задачу, трактуем как «не исключено»
    monkeypatch.setattr("bossman.learning_guard.get_holdout",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert _learning_excluded("x") is False
