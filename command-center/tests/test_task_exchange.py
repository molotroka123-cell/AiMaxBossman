"""Local Task Exchange V1 + discovery — интеграционные тесты (изолированный tmp APPS_DIR)."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from bcc.features import apps as apps_mod
from bcc.features import task_exchange as tx


def _app_dir(tmp_path: Path, app_id: str, permissions: dict | None = None) -> Path:
    d = tmp_path / "apps" / app_id
    d.mkdir(parents=True, exist_ok=True)
    perms = permissions if permissions is not None else {"local.compute": "auto"}
    perms_yaml = "".join(f"  {k}: {v}\n" for k, v in perms.items())
    (d / "app.manifest.yaml").write_text(
        f"id: {app_id}\nname: {app_id}\nversion: '1.0'\ndefault_port: 8931\n"
        "permissions:\n" + perms_yaml,
        encoding="utf-8")
    return d


@pytest.fixture
def xenv(tmp_path, monkeypatch):
    monkeypatch.setattr(apps_mod, "APPS_DIR", tmp_path / "apps")
    monkeypatch.setattr(tx, "APPS_DIR", tmp_path / "apps")
    # сброс кэша discovery
    monkeypatch.setattr(apps_mod, "_cache", {"at": 0.0, "apps": []})
    return tmp_path


def _task(app_id: str, *, ttype="local_calc", caps=(), task_id=None, idem=None,
          reply_to=None):
    task_id = task_id or str(uuid.uuid4())
    return {
        "task_id": task_id, "app_id": app_id, "type": ttype, "priority": "normal",
        "input": {"q": 1}, "requested_capabilities": list(caps),
        "idempotency_key": idem or task_id,
        "reply_to": reply_to or f"bossman/completed/{task_id}.json",
    }


def _write(app: str, task: dict, bucket="inbox", name=None) -> Path:
    d = tx.exchange_root(app) / bucket
    d.mkdir(parents=True, exist_ok=True)
    p = d / (name or f"{task['task_id']}.json")
    p.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
    return p


async def test_fake_manifest_appears_and_disappears(tmp_path, monkeypatch):
    _app_dir(tmp_path, "fake-app")
    monkeypatch.setattr(apps_mod, "APPS_DIR", tmp_path / "apps")
    monkeypatch.setattr(tx, "APPS_DIR", tmp_path / "apps")
    ids = set(tx.known_apps())
    assert "fake-app" in ids
    import shutil
    shutil.rmtree(tmp_path / "apps" / "fake-app")
    assert "fake-app" not in set(tx.known_apps())


async def test_malformed_manifest_is_ignored_not_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(apps_mod, "APPS_DIR", tmp_path / "apps")
    monkeypatch.setattr(tx, "APPS_DIR", tmp_path / "apps")
    d = tmp_path / "apps" / "broken-app"
    d.mkdir(parents=True)
    (d / "app.manifest.yaml").write_text("id: [unclosed\n  bad: yaml", encoding="utf-8")
    assert "broken-app" not in set(tx.known_apps())


async def test_roundtrip_deterministic(xenv):
    _app_dir(xenv, "fake-app")
    task = _task("fake-app")
    _write("fake-app", task)
    counts = await tx.exchange.process(None)
    assert counts["processed"] == 1
    res = json.loads((tx.exchange_root("fake-app") / "completed" /
                      f"{task['task_id']}.json").read_text(encoding="utf-8"))
    assert res["status"] == "COMPLETED" and res["task_id"] == task["task_id"]


async def test_external_capability_fails_honestly(xenv):
    _app_dir(xenv, "fake-app", {"browser.read": "auto"})
    task = _task("fake-app", ttype="browser_research", caps=["browser.read"])
    p = _write("fake-app", task)
    # bounded retries: задача честно ретраится, затем FAILED
    for _ in range(tx.MAX_ATTEMPTS + 1):
        await tx.exchange.process(None)
    body = json.loads((tx.exchange_root("fake-app") / "failed" / p.name).read_text(encoding="utf-8"))
    assert "live Bossman tool execution" in body["error"]


async def test_oversized_rejected(xenv):
    _app_dir(xenv, "fake-app")
    big = _task("fake-app")
    big["input"] = {"blob": "x" * (tx.MAX_TASK_BYTES + 10)}
    p = tx._buckets("fake-app")["inbox"] / f"{big['task_id']}.json"
    p.write_text(json.dumps(big), encoding="utf-8")
    await tx.exchange.process(None)
    body = json.loads((tx._buckets("fake-app")["failed"] / p.name).read_text(encoding="utf-8"))
    assert "too large" in body["error"]


async def test_malformed_json_rejected(xenv):
    _app_dir(xenv, "fake-app")
    dirs = tx._buckets("fake-app")
    (dirs["inbox"] / "malformed.json").write_text("{not json", encoding="utf-8")
    await tx.exchange.process(None)
    body = json.loads((dirs["failed"] / "malformed.json").read_text(encoding="utf-8"))
    assert "malformed JSON" in body["error"]


async def test_reply_to_traversal_denied(xenv):
    _app_dir(xenv, "fake-app")
    dirs = tx._buckets("fake-app")
    task = _task("fake-app", reply_to="../../outside.json")
    p = dirs["inbox"] / f"{task['task_id']}.json"
    p.write_text(json.dumps(task), encoding="utf-8")
    await tx.exchange.process(None)
    body = json.loads((dirs["failed"] / p.name).read_text(encoding="utf-8"))
    assert "reply_to" in body["error"]


async def test_app_mismatch_rejected(xenv):
    _app_dir(xenv, "fake-app")
    dirs = tx._buckets("fake-app")
    task = _task("other-app")
    p = dirs["inbox"] / f"{task['task_id']}.json"
    p.write_text(json.dumps(task), encoding="utf-8")
    await tx.exchange.process(None)
    body = json.loads((dirs["failed"] / p.name).read_text(encoding="utf-8"))
    assert "app_id" in body["error"]


async def test_unknown_and_denied_capabilities(xenv):
    _app_dir(xenv, "fake-app", {"local.compute": "auto", "banking.transfer": "deny"})
    dirs = tx._buckets("fake-app")
    t1 = _task("fake-app", caps=["nuclear.launch"])
    t2 = _task("fake-app", caps=["banking.transfer"])
    for t in (t1, t2):
        p = dirs["inbox"] / f"{t['task_id']}.json"
        p.write_text(json.dumps(t), encoding="utf-8")
    await tx.exchange.process(None)
    e1 = json.loads((dirs["failed"] / f"{t1['task_id']}.json").read_text(encoding="utf-8"))
    e2 = json.loads((dirs["failed"] / f"{t2['task_id']}.json").read_text(encoding="utf-8"))
    assert "unsupported capability" in e1["error"]
    assert "denied by policy" in e2["error"]


async def test_ask_creates_approval(env, tmp_path, monkeypatch):
    _app_dir(tmp_path, "fake-app", {"external.send": "ask"})
    monkeypatch.setattr(apps_mod, "APPS_DIR", tmp_path / "apps")
    monkeypatch.setattr(tx, "APPS_DIR", tmp_path / "apps")
    dirs = tx._buckets("fake-app")
    task = _task("fake-app", caps=["external.send"])
    p = dirs["inbox"] / f"{task['task_id']}.json"
    p.write_text(json.dumps(task), encoding="utf-8")
    counts = await tx.exchange.process(env.svc)
    assert counts["needs_approval"] == 1
    pending = await env.svc.approvals.list(status="pending")
    assert any(a["kind"] == "task_exchange" and task["task_id"] in a["preview"]
               for a in pending)


async def test_replay_rejected_and_mutation_not_repeated(xenv):
    _app_dir(xenv, "fake-app")
    dirs = tx._buckets("fake-app")
    task = _task("fake-app")
    p = dirs["inbox"] / f"{task['task_id']}.json"
    p.write_text(json.dumps(task), encoding="utf-8")
    first = await tx.exchange.process(None)
    assert first["processed"] == 1
    p.write_text(json.dumps(task), encoding="utf-8")
    second = await tx.exchange.process(None)
    assert second["replayed"] == 1 and second["processed"] == 0


async def test_crash_recovery_no_loss_no_duplicate(xenv):
    _app_dir(xenv, "fake-app")
    dirs = tx._buckets("fake-app")
    task = _task("fake-app")
    (dirs["claimed"] / f"{task['task_id']}.json").write_text(
        json.dumps(task), encoding="utf-8")
    counts = await tx.exchange.process(None)
    assert counts["processed"] == 1
    assert (tx.exchange_root("fake-app") / "completed" /
            f"{task['task_id']}.json").exists()


async def test_bounded_retries_then_failed(xenv):
    _app_dir(xenv, "fake-app")
    calls = {"n": 0}

    async def boom(task):
        calls["n"] += 1
        raise RuntimeError("worker crash")

    ex = tx.LocalTaskExchange()
    ex.executors["always_fails"] = boom
    dirs = tx._buckets("fake-app")
    task = _task("fake-app", ttype="always_fails")
    p = dirs["inbox"] / f"{task['task_id']}.json"
    p.write_text(json.dumps(task), encoding="utf-8")
    for _ in range(tx.MAX_ATTEMPTS + 1):
        await ex.process(None)
    assert calls["n"] == tx.MAX_ATTEMPTS
    finals = [json.loads(x.read_text(encoding="utf-8"))
              for x in dirs["failed"].glob("*.json")]
    assert any(b.get("status") == "FAILED" and "requeued" not in b.get("error", "")
               and b.get("task_id") == task["task_id"] for b in finals)


async def test_boundary_normalization_row_dict():
    from bcc.db import _jsonable
    from datetime import datetime
    from decimal import Decimal
    import uuid
    assert _jsonable(datetime(2026, 1, 1, 12, 0)) == "2026-01-01T12:00:00"
    assert _jsonable(Decimal("10.50")) == "10.50"
    u = uuid.uuid4()
    assert _jsonable(u) == str(u)


async def test_migrate_only_swallows_duplicate_column(xenv):
    from bcc.db import _is_duplicate_column_error
    assert _is_duplicate_column_error(Exception("duplicate column name: foo"))
    assert _is_duplicate_column_error(Exception('column "x" of relation "t" already exists'))
    assert not _is_duplicate_column_error(Exception("syntax error at or near SELEC"))
