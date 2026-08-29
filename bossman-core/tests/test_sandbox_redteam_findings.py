"""Регрессы на находки red-team Stage 8 (29.08). Каждый тест — воспроизведение
конкретной дыры, которая РАБОТАЛА до фикса.
"""
from __future__ import annotations

import json
import os

import pytest

from bossman import errors, obs
from bossman.sandbox import DatasetGate
from bossman.sandbox.artifacts import ArtifactGate
from bossman.sandbox.models import SandboxSession, SandboxSpec
from bossman.sandbox.trajectory import TrajectoryRecorder


# ---------- RT-01: хардлинк наружу проходил ArtifactGate ----------

def test_hardlink_out_of_root_rejected(tmp_path):
    """У жёсткой ссылки нет пути-цели, поэтому resolve() её не ловил: файл
    выглядел обычным и лежал внутри root, а содержимое принадлежало чужому файлу
    хоста. Так можно было вынести любой читаемый файл."""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "host_secret.txt"
    outside.write_text("TOP-SECRET-HOST-FILE")
    link = root / "looks_normal.txt"
    try:
        os.link(outside, link)
    except (OSError, AttributeError):
        pytest.skip("хардлинки недоступны в этой ФС")
    with pytest.raises(errors.ArtifactRejected) as ei:
        ArtifactGate(root).inspect("looks_normal.txt")
    assert "hard link" in str(ei.value)


def test_regular_file_still_accepted(tmp_path):
    """Фикс не должен ломать нормальный артефакт (nlink == 1)."""
    (tmp_path / "ok.txt").write_text("normal output")
    art = ArtifactGate(tmp_path).inspect("ok.txt")
    assert art.sha256 and not art.quarantined


# ---------- RT-02: песочница исполнялась под uid ядра (root) ----------

def test_privileges_dropped_regardless_of_network_mode():
    """Сброс прав был привязан к наличию egress-прокси, поэтому OFFLINE-песочница
    (режим ПО УМОЛЧАНИЮ) шла под uid ядра. Из-под root не действует
    protected_hardlinks — и хардлинк на любой файл хоста удавался."""
    from bossman.sandbox.runtimes.safe import SafeRuntime
    from bossman.sandbox.netguard import sandbox_uid

    offline = SandboxSession(id="sbx_offline", spec=SandboxSpec(task="t"))   # без прокси
    online = SandboxSession(id="sbx_online",
                            spec=SandboxSpec(task="t", labels={"egress_proxy": "127.0.0.1:1"}))
    if os.name != "posix" or os.geteuid() != 0:
        pytest.skip("нужен root, чтобы вообще было что сбрасывать")
    # ОБА режима сбрасывают привилегии, а не только сетевой
    assert SafeRuntime._drop_uid_for(offline) == sandbox_uid("sbx_offline")
    assert SafeRuntime._drop_uid_for(online) == sandbox_uid("sbx_online")


# ---------- RT-03: секрет протекал в траекторию и датасет ----------

LEAKY = [
    "sk-LEAK-abcdef0123456789",          # дефис ВНУТРИ токена — паттерн не ловил  # ci-secret-scan: allow
    "sk-or-v1-0123456789abcdef0123",     # реальный вид ключа OpenRouter  # ci-secret-scan: allow
    "ghp_0123456789abcdefABCD",  # ci-secret-scan: allow
    "AKIAIOSFODNN7EXAMPLE",  # ci-secret-scan: allow
]


@pytest.mark.parametrize("secret", LEAKY)
def test_token_with_hyphens_is_redacted(secret):
    assert secret not in obs.redact(secret)
    assert secret not in obs.redact(f"значение: {secret} конец")


def test_key_named_field_is_redacted():
    """Слово `key` не значилось секретным ключом — `key=<токен>` и {'key': …}
    уходили в лог как есть."""
    assert "sk-LEAK-abcdef0123456789" not in obs.redact("key=sk-LEAK-abcdef0123456789")  # ci-secret-scan: allow
    red = obs.redact_obj({"key": "sk-LEAK-abcdef0123456789"})  # ci-secret-scan: allow
    assert red["key"] == obs.REDACTED


def test_secret_does_not_reach_trajectory_or_dataset():
    """Сквозная проверка: вложенная структура → траектория → датасет-кандидат."""
    rec = TrajectoryRecorder("sbx1")
    rec.record("tool_call",
               nested={"deep": [{"authorization": "Bearer sk-LEAK-abcdef0123456789"}]},  # ci-secret-scan: allow
               text="key=sk-LEAK-abcdef0123456789")  # ci-secret-scan: allow
    blob = json.dumps(rec.events, ensure_ascii=False)
    assert "sk-LEAK-abcdef0123456789" not in blob  # ci-secret-scan: allow

    candidate = DatasetGate().build_candidate("sbx1", rec.events)
    assert "sk-LEAK-abcdef0123456789" not in json.dumps(candidate.samples, ensure_ascii=False)  # ci-secret-scan: allow


def test_redaction_stays_idempotent_and_keeps_non_secrets():
    once = obs.redact("sk-LEAK-abcdef0123456789")  # ci-secret-scan: allow
    assert obs.redact(once) == once
    # не-секреты не должны затираться, иначе логи станут бесполезными
    assert obs.redact("lease_id=abc123") == "lease_id=abc123"
    assert obs.redact("run_id=42 alias=bossman-coder") == "run_id=42 alias=bossman-coder"
