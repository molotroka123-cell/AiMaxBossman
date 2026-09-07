"""Общие фикстуры bossman-core. Ключ подписи улик (EH-01) — всегда во временном
каталоге теста: боевой ключ владельца (~/.bossman/keys) тесты не читают и не создают."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _evidence_key_in_tmp(tmp_path, monkeypatch):
    import bossman._shared  # noqa: F401
    from bossman_shared import evidence
    monkeypatch.setenv(evidence.ENV_KEY_FILE, str(tmp_path / "keys" / "evidence.key"))
    evidence.reset_cache()
    yield
    evidence.reset_cache()


@pytest.fixture(autouse=True)
def _fresh_evidence_ledger():
    """AUDIT001-F5-REPLAY: promotion evidence is single-use *per process*.

    The ledger is deliberately process-wide, so without this every test after
    the first would inherit spent measurements from unrelated tests. Production
    keeps the accumulated ledger; tests each start from an empty one.
    """
    from bossman.learning_guard.evidence_ledger import reset_default_ledger
    reset_default_ledger()
    yield
    reset_default_ledger()


@pytest.fixture(autouse=True)
def _real_workload_corpus_in_tmp(tmp_path, monkeypatch):
    """Телеметрия реальных нагрузок пишется автоматически на терминальной границе
    исполнения. Корпус владельца тесты не трогают: иначе прогон suite превратился
    бы в «реальные нагрузки» и отравил бы аудит железа выдуманными выборками."""
    from bossman_v3.execution import telemetry
    monkeypatch.setenv(telemetry.ENV_ROOT, str(tmp_path / "benchmarks"))
    yield
