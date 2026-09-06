"""Общие фикстуры bossman-core.

EH-01: ключ подписи улик — всегда во временном каталоге теста; боевой ключ
владельца (~/.bossman/keys) тесты не читают и не создают.

ISO-001: рабочий каталог ядра (`WORKSPACE_DIR`) на время прогона уводится во
временный каталог. Без этого `settings.workspace_dir` указывал на сам исходник
(`bossman-core/workspace`), а `cost_control.runtime` и `notifications.runtime`
СОЗДАЮТ свой SQLite прямо в момент импорта — то есть до того, как любой тест
успеет что-то подменить. Последствия ровно те, что и ожидаются от общего
изменяемого файла: строки копились из прогона в прогон (callback_tokens: 2 → 5
за три прогона), а два одновременных прогона писали в ОДИН файл. Переменные
ставятся здесь, на импорте conftest, потому что это единственный момент до
первого `import bossman.*`; уже заданное окружение (оператор/CI) не трогаем.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

_ISOLATED_WORKSPACE = Path(tempfile.mkdtemp(prefix="bossman-core-tests-"))
os.environ.setdefault("WORKSPACE_DIR", str(_ISOLATED_WORKSPACE))
os.environ.setdefault("BOSSMAN_NOTIFICATION_DB",
                      str(_ISOLATED_WORKSPACE / "_notifications" / "notifications.db"))
os.environ.setdefault("BOSSMAN_COST_DB",
                      str(_ISOLATED_WORKSPACE / "_cost_control" / "cost_control.db"))


def pytest_sessionfinish(session, exitstatus):
    """Свой временный каталог убираем за собой; чужой (заданный извне) — нет."""
    if os.environ.get("WORKSPACE_DIR") == str(_ISOLATED_WORKSPACE):
        shutil.rmtree(_ISOLATED_WORKSPACE, ignore_errors=True)


@pytest.fixture(autouse=True)
def _evidence_key_in_tmp(tmp_path, monkeypatch):
    import bossman._shared  # noqa: F401
    from bossman_shared import evidence
    monkeypatch.setenv(evidence.ENV_KEY_FILE, str(tmp_path / "keys" / "evidence.key"))
    evidence.reset_cache()
    yield
    evidence.reset_cache()
