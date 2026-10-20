"""Branch 8 (Memory): privacy and provenance of the note write path — behavioural.

Fake secrets are assembled at runtime so no literal token sits in the repo.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bcc.hybrid.context_filter import SecretMaterialRefused
from bcc.v2.memory.obsidian import ObsidianVault
from bcc.v2.memory.service import ObsidianMemoryService
from bcc.v2.memory.sqlite_index import SQLiteMemoryBackend

FAKE_KEY = "sk-" + "Zq7" * 10                                 # openai_key shape, not a real key
FAKE_GH = "ghp_" + "a1B2" * 10                               # github_token shape


def _vault(tmp_path: Path) -> ObsidianVault:
    root = tmp_path / "vault"
    root.mkdir()
    return ObsidianVault(root=root)


def _all_text(vault: ObsidianVault) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in vault.root.rglob("*.md"))


def test_secret_inside_note_is_redacted_and_recorded(tmp_path: Path):
    vault = _vault(tmp_path)
    path = vault.write_memory(title=f"Ключ {FAKE_GH}",
                              content=f"Решили ходить в API через прокси.\nOPENAI_API_KEY={FAKE_KEY}",
                              tags=["ops", FAKE_GH])
    text = path.read_text(encoding="utf-8")
    assert FAKE_KEY not in text and FAKE_GH not in text and FAKE_GH not in path.name
    assert "[REDACTED:" in text
    assert "Решили ходить в API через прокси." in text      # useful text survives
    assert "redactions: [" in text and "github_token" in text


def test_note_that_is_only_a_secret_is_refused_and_not_written(tmp_path: Path):
    vault = _vault(tmp_path)
    with pytest.raises(SecretMaterialRefused):
        vault.write_memory(title="token", content=FAKE_KEY)
    assert _all_text(vault) == ""


async def test_remembered_secret_is_not_retrievable(tmp_path: Path):
    vault = _vault(tmp_path)
    service = ObsidianMemoryService(
        vault=vault, backend=SQLiteMemoryBackend(index_path=tmp_path / "m.sqlite3", vault_root=vault.root))
    await service.remember(title="Деплой", content=f"Деплой через staging. password: {FAKE_KEY}")
    assert not (await service.search(FAKE_KEY)).items
    pack = await service.search("деплой staging")
    assert pack.items and all(FAKE_KEY not in i.content for i in pack.items)
