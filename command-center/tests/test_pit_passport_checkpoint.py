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


def test_taskstore_checkpoint_snapshot_and_update_input_on_change(tmp_path: Path):
    """Tier 5 LangGraph extension: durable checkpoint snapshot and input update on change
    without repeating completed external actions."""
    from bcc.pit.tasks import TaskStore

    store = TaskStore(tmp_path)
    executed_calls = []

    def exec_quote(key, params):
        executed_calls.append(("quote", key))
        return "Q-12345678"

    def exec_settle(key, params):
        executed_calls.append(("settle", key))
        return "D-1"

    task = store.create(
        "owner", "Оформить обмен 1000 USDT",
        steps=[
            {"id": "quote", "title": "Котировка", "kind": "issue_quote"},
            {"id": "settle", "title": "Расчёт", "kind": "settle_deal", "needs_input": True},
        ],
    )
    tid = task["id"]
    t1 = asyncio.run(store.run("owner", tid, {"issue_quote": exec_quote, "settle_deal": exec_settle}))
    assert t1["state"] == "WAITING_INPUT"
    assert len(executed_calls) == 1

    snap1 = store.checkpoint_snapshot("owner", tid)
    assert snap1["done_steps"] == ["quote"]
    assert snap1["pending_steps"] == ["settle"]
    assert snap1["awaited_input"]["step"] == "settle"
    assert len(snap1["completed_action_keys"]) == 1
    assert len(snap1["checkpoint_sha256"]) == 64

    # Supply initial input and then update it on change before running step 2
    store.update_input_on_change("owner", tid, "settle", "3%", reason="initial")
    t_same = store.update_input_on_change("owner", tid, "settle", "3%", reason="same")
    assert t_same["input_revisions"][-1]["changed"] is False

    t_changed = store.update_input_on_change(
        "owner", tid, "settle", "2.5%", reason="Менеджер согласовал скидку"
    )
    assert t_changed["input_revisions"][-1]["changed"] is True
    snap2 = store.checkpoint_snapshot("owner", tid)
    assert snap2["done_steps"] == ["quote"]
    assert snap2["inputs"]["settle"] == "2.5%"
    assert snap2["checkpoint_sha256"] != snap1["checkpoint_sha256"]

    # Resume execution: step 1 ('quote') is NOT repeated; only step 2 ('settle') runs
    t_done = asyncio.run(store.run("owner", tid, {"issue_quote": exec_quote, "settle_deal": exec_settle}))
    assert t_done["state"] == "DONE"
    assert [c[0] for c in executed_calls] == ["quote", "settle"]
