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
