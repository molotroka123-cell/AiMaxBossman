"""V2.1 фаза O — снапшот/откат (пробел G13).

Проверяем ровно то, что обещано: снапшот содержит ссылки и контрольные суммы,
НЕ содержит расшифрованных секретов и тяжёлых файлов, а откат невозможен без
одобренного approval. Всё — на одноразовой БД фикстуры `env` (tmp_path).
"""
from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa

from bcc.db import approvals as approvals_t, providers as providers_t, settings_kv, tasks as tasks_t
from bcc.features.snapshot import MAX_ARTIFACT_BYTES, RESTORE_KIND

# Заведомо уникальное «секретное» значение: ищем его во всех байтах артефакта.
SECRET = "sk-live-BOSSMAN-PLAINTEXT-CANARY-0a1b2c3d4e5f"  # ci-secret-scan: allow
WALLET = "wallet-seed-canary-correct-horse-battery-staple"


async def _make_secrets(env):
    """Провайдер с ключом + зашифрованная настройка «кошелька»."""
    resp = await env.client.post("/api/providers", json={
        "name": "canary", "kind": "openai_compat",
        "base_url": "http://127.0.0.1:9/v1", "api_key": SECRET})
    assert resp.status_code == 200, resp.text
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(settings_kv).values(
            key="wallet.seed", value_enc=env.svc.vault.encrypt(WALLET)))
        await s.commit()
    return resp.json()


async def _create(env, **body):
    resp = await env.client.post("/api/snapshots", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _artifact_files(path: str) -> list[Path]:
    return [p for p in Path(path).rglob("*") if p.is_file()]


# ---------------------------------------------------------------- создание

async def test_create_writes_artifact_and_manifest_with_checksums(env):
    await _make_secrets(env)
    made = await _create(env, name="перед миссией", kind="pre_mission")
    snap = made["snapshot"]
    assert snap["id"] and snap["name"] == "перед миссией" and snap["kind"] == "pre_mission"

    base = Path(made["path"])
    assert (base / "db.sqlite").is_file()
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))

    # контрольные суммы — не украшение: sha256 совпадает с реальным файлом
    import hashlib
    digest = hashlib.sha256((base / "db.sqlite").read_bytes()).hexdigest()
    assert manifest["database"]["sha256"] == digest
    assert manifest["files"] and all(f["sha256"] for f in manifest["files"])

    # ссылки на всё требуемое
    assert manifest["database"]["table_counts"]["providers"] >= 1
    assert "git" in manifest and "state" in manifest
    assert manifest["config"]["data_dir"] == str(env.settings.data_dir)
    assert isinstance(manifest["state"]["skills"], list)
    assert isinstance(manifest["state"]["settings_keys"], list)
    assert "wallet.seed" in manifest["state"]["settings_keys"]   # ключ — да, значение — нет


async def test_snapshot_contains_no_plaintext_secret(env):
    """Главная гарантия: в артефакте нет ни ключа провайдера, ни секрета настройки,
    ни самого Fernet-ключа, которым их можно расшифровать."""
    await _make_secrets(env)
    made = await _create(env)
    files = _artifact_files(made["path"])
    assert files, "снапшот пуст"

    blob = b"".join(p.read_bytes() for p in files)
    assert SECRET.encode() not in blob
    assert WALLET.encode() not in blob

    # ключ шифрования не копируется — иначе снапшот стал бы связкой секретов
    key_bytes = (env.settings.data_dir / "secret.key").read_bytes().strip()
    assert key_bytes not in blob
    assert not any(p.name in ("secret.key", "token") for p in files)

    # провайдер описан отпечатком, а не ключом
    manifest = json.loads((Path(made["path"]) / "manifest.json").read_text(encoding="utf-8"))
    provider = manifest["state"]["providers"][0]
    assert provider["has_key"] is True
    assert provider["key_fingerprint"].startswith("sha256:")
    assert SECRET[-4:] not in json.dumps(manifest, ensure_ascii=False)
    assert manifest["secrets"]["plaintext_included"] is False
    assert manifest["secrets"]["encryption_key_included"] is False


async def test_snapshot_copies_nothing_huge(env):
    """Снапшот — состояние, а не архив: только db.sqlite + manifest.json."""
    made = await _create(env)
    files = _artifact_files(made["path"])
    assert sorted(p.name for p in files) == ["db.sqlite", "manifest.json"]
    for p in files:
        assert p.stat().st_size <= MAX_ARTIFACT_BYTES
    assert made["size_bytes"] <= MAX_ARTIFACT_BYTES
    # никаких весов моделей и профилей браузера рядом
    assert not any(p.suffix in (".gguf", ".safetensors", ".bin") for p in files)


async def test_list_returns_created_snapshots(env):
    a = await _create(env, name="первый")
    b = await _create(env, name="второй")
    resp = await env.client.get("/api/snapshots")
    assert resp.status_code == 200
    items = resp.json()["snapshots"]
    ids = [i["id"] for i in items]
    assert ids == sorted(ids, reverse=True)
    assert a["snapshot"]["id"] in ids and b["snapshot"]["id"] in ids
    first = next(i for i in items if i["id"] == b["snapshot"]["id"])
    assert first["exists"] is True and first["size_bytes"] > 0


# ---------------------------------------------------------------- preview

async def test_restore_preview_reports_what_would_change(env):
    made = await _create(env)
    sid = made["snapshot"]["id"]
    # после снапшота появляется новая задача — её и должен показать diff
    await env.client.post("/api/agents", json={"name": "агент отката"})
    resp = await env.client.get(f"/api/snapshots/{sid}/restore-preview")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["artifact_present"] is True and data["checksum_ok"] is True
    assert data["restorable"] is True
    assert data["requires_approval"] is True and data["approval_kind"] == RESTORE_KIND
    changed = {c["table"]: c for c in data["database"]["changes"]}
    assert "agents" in changed and changed["agents"]["delta"] == -1
    assert data["will_replace"] and data["will_not_touch"]
    assert "git" in data and "note" in data["git"]


async def test_restore_preview_detects_corrupted_artifact(env):
    made = await _create(env)
    sid = made["snapshot"]["id"]
    (Path(made["path"]) / "db.sqlite").write_bytes(b"not a database")
    data = (await env.client.get(f"/api/snapshots/{sid}/restore-preview")).json()
    assert data["checksum_ok"] is False and data["restorable"] is False
    assert any("контрольная сумма" in w for w in data["warnings"])


async def test_preview_and_restore_404_for_unknown_snapshot(env):
    assert (await env.client.get("/api/snapshots/999/restore-preview")).status_code == 404
    assert (await env.client.post("/api/snapshots/999/restore", json={})).status_code == 404


# ---------------------------------------------------------------- откат

async def test_restore_without_approval_is_refused_and_creates_one(env):
    made = await _create(env)
    sid = made["snapshot"]["id"]
    resp = await env.client.post(f"/api/snapshots/{sid}/restore", json={})
    assert resp.status_code == 202
    err = resp.json()["error"]
    approval_id = err["approval_id"]
    assert approval_id

    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(approvals_t).where(
            approvals_t.c.id == approval_id))).first()
    assert row._mapping["kind"] == RESTORE_KIND
    assert row._mapping["status"] == "pending"

    # неодобренный approval откат не пропускает
    pending = await env.client.post(f"/api/snapshots/{sid}/restore",
                                    json={"approval_id": approval_id})
    assert pending.status_code == 403
    assert "approved" in json.dumps(pending.json(), ensure_ascii=False)


async def test_rejected_approval_does_not_restore(env):
    made = await _create(env)
    sid = made["snapshot"]["id"]
    approval_id = (await env.client.post(f"/api/snapshots/{sid}/restore",
                                         json={})).json()["error"]["approval_id"]
    await env.svc.approvals.decide(approval_id, False, "тест")
    denied = await env.client.post(f"/api/snapshots/{sid}/restore",
                                   json={"approval_id": approval_id})
    assert denied.status_code == 403


async def test_foreign_approval_cannot_authorise_restore(env):
    """Approval другого рода (например tool) откат не разрешает."""
    made = await _create(env)
    sid = made["snapshot"]["id"]
    other = await env.svc.approvals.create(kind="tool", preview="rm -rf /")
    await env.svc.approvals.decide(other["id"], True, "тест")
    resp = await env.client.post(f"/api/snapshots/{sid}/restore",
                                 json={"approval_id": other["id"]})
    assert resp.status_code == 403


async def test_approved_restore_rolls_state_back(env):
    """Одобренный откат действительно возвращает состояние БД."""
    task = (await env.client.post("/api/tasks", json={
        "title": "до снапшота", "prompt": "раз"})).json()
    before_id = task.get("id") or task.get("task", {}).get("id")
    made = await _create(env, name="точка отката")
    sid = made["snapshot"]["id"]

    after = (await env.client.post("/api/tasks", json={
        "title": "после снапшота", "prompt": "два"})).json()
    after_id = after.get("id") or after.get("task", {}).get("id")
    assert after_id and after_id != before_id

    approval_id = (await env.client.post(f"/api/snapshots/{sid}/restore",
                                         json={})).json()["error"]["approval_id"]
    await env.svc.approvals.decide(approval_id, True, "тест")
    done = await env.client.post(f"/api/snapshots/{sid}/restore",
                                 json={"approval_id": approval_id, "by": "тест"})
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["ok"] is True and Path(body["safety_copy"]).is_file()

    async with env.svc.db.session() as s:
        ids = [r[0] for r in (await s.execute(sa.select(tasks_t.c.id))).fetchall()]
    assert before_id in ids
    assert after_id not in ids          # задача, созданная после снапшота, откатилась

    # приложение продолжает работать на восстановленной базе
    assert (await env.client.get("/api/tasks")).status_code == 200


async def test_approval_cannot_be_replayed(env):
    """Одно подтверждение — один откат (approval после отката недоступен)."""
    made = await _create(env)
    sid = made["snapshot"]["id"]
    approval_id = (await env.client.post(f"/api/snapshots/{sid}/restore",
                                         json={})).json()["error"]["approval_id"]
    await env.svc.approvals.decide(approval_id, True, "тест")
    assert (await env.client.post(f"/api/snapshots/{sid}/restore",
                                  json={"approval_id": approval_id})).status_code == 200
    replay = await env.client.post(f"/api/snapshots/{sid}/restore",
                                   json={"approval_id": approval_id})
    assert replay.status_code == 403


async def test_snapshot_registry_survives_restore(env):
    """После отката точки отката остаются: иначе вернуться было бы некуда."""
    first = await _create(env, name="точка 1")
    second = await _create(env, name="точка 2")
    sid = first["snapshot"]["id"]
    approval_id = (await env.client.post(f"/api/snapshots/{sid}/restore",
                                         json={})).json()["error"]["approval_id"]
    await env.svc.approvals.decide(approval_id, True, "тест")
    assert (await env.client.post(f"/api/snapshots/{sid}/restore",
                                  json={"approval_id": approval_id})).status_code == 200
    ids = [i["id"] for i in (await env.client.get("/api/snapshots")).json()["snapshots"]]
    assert sid in ids and second["snapshot"]["id"] in ids


async def test_restore_refuses_corrupted_artifact_even_when_approved(env):
    made = await _create(env)
    sid = made["snapshot"]["id"]
    approval_id = (await env.client.post(f"/api/snapshots/{sid}/restore",
                                         json={})).json()["error"]["approval_id"]
    await env.svc.approvals.decide(approval_id, True, "тест")
    (Path(made["path"]) / "db.sqlite").write_bytes(b"corrupted")
    resp = await env.client.post(f"/api/snapshots/{sid}/restore",
                                 json={"approval_id": approval_id})
    assert resp.status_code == 409


async def test_snapshot_endpoints_require_auth(env):
    import httpx
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app),
                                 base_url="http://test") as anon:
        assert (await anon.get("/api/snapshots")).status_code == 401
        assert (await anon.post("/api/snapshots", json={})).status_code == 401


async def test_provider_key_survives_restore(env):
    """Откат не ломает шифрование: ключ Fernet остаётся на месте, ключ провайдера
    после отката снова читается (значит, в снапшоте лежал именно шифротекст)."""
    await _make_secrets(env)
    made = await _create(env)
    sid = made["snapshot"]["id"]
    approval_id = (await env.client.post(f"/api/snapshots/{sid}/restore",
                                         json={})).json()["error"]["approval_id"]
    await env.svc.approvals.decide(approval_id, True, "тест")
    assert (await env.client.post(f"/api/snapshots/{sid}/restore",
                                  json={"approval_id": approval_id})).status_code == 200
    async with env.svc.db.session() as s:
        enc = (await s.execute(sa.select(providers_t.c.api_key_enc))).scalar()
    assert env.svc.vault.decrypt(enc) == SECRET
