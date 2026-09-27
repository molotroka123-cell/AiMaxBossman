import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from bcc.pit.models import ConsentState
from bcc.pit.passport_checkpoint import build_checkpoint, save_checkpoint
from bcc.pit.vault import PersonaVault


def test_checkpoint_reads_only_consented_facts_and_marks_unknown(tmp_path: Path):
    home = tmp_path / "pit-v1.7"
    home.mkdir()
    with sqlite3.connect(home / "companion.sqlite3") as db:
        db.execute("CREATE TABLE inbox (who TEXT)")
        db.executemany("INSERT INTO inbox VALUES (?)", [("111:111",), ("222:222",)])
    settings = SimpleNamespace(data_dir=tmp_path, identity_salt="01" * 32, people=())
    vault = PersonaVault(tmp_path, bytes.fromhex(settings.identity_salt))
    key = vault.key_for_telegram(111)
    vault.set_consent(key, ConsentState(memory_enabled=True))
    person = vault.ensure(key)
    (person / "facts.jsonl").write_text(
        json.dumps({"category": "visual_context", "value": "Проверяет 3D-модель",
                    "confidence": 0.8}, ensure_ascii=False) + "\n", encoding="utf-8")
    (person / "raw" / "events.jsonl").write_text("PRIVATE_CHAT_TEXT", encoding="utf-8")

    class LocalModel:
        calls = []

        async def chat(self, model, messages, **kwargs):
            self.calls.append((model, messages, kwargs))
            return SimpleNamespace(text='{"context":"Проверяет 3D-модель","topic_tag":"3D"}',
                                   finish="stop")

    model = LocalModel()
    report = asyncio.run(build_checkpoint(settings, adapter=model))
    assert [row["telegram_id"] for row in report["participants"]] == [111, 222]
    assert report["participants"][0]["status"] == "DRAFT_REVIEW"
    assert report["participants"][1]["status"] == "INSUFFICIENT_DATA"
    assert len(model.calls) == 1
    assert "PRIVATE_CHAT_TEXT" not in str(model.calls)
    assert (save_checkpoint(settings, report)).is_file()
