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
