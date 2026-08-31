"""V2.6 модуль M — Artifact Engine: реестр артефактов + сборка контента.

Реестровая часть — PG-гейт (как test_v26_flight_recorder): без
`BOSSMAN_TEST_PG_DSN` — честный SKIP_HOST. build_content — чистый stdlib,
эти тесты бегут всегда.
"""
from __future__ import annotations

import csv
import io
import json
import os
import uuid
import zipfile

import pytest

from bossman.artifacts_engine import SUPPORTED_CREATE_FORMATS, build_content

DSN = os.getenv("BOSSMAN_TEST_PG_DSN")
pg_gate = pytest.mark.skipif(
    not DSN, reason="SKIP_HOST: no BOSSMAN_TEST_PG_DSN (real PostgreSQL) available")


@pytest.fixture()
async def pg(monkeypatch):
    monkeypatch.setenv("BOSSMAN_DATABASE_URL", DSN)
    from bossman import db
    from bossman.config import settings
    monkeypatch.setattr(settings, "database_url", DSN, raising=False)
    await db.close()
    yield db
    await db.close()


# ---------- реестр (PG) ----------

@pg_gate
async def test_register_and_versioning(pg):
    from bossman import artifacts_engine
    path = f"/tmp/report-{uuid.uuid4().hex}.md"

    rec1 = await artifacts_engine.register_artifact(
        type="md", path=path, content="# Отчёт v1", creator_task="42",
        source_evidence=[{"file": "data.csv", "ref": "rows=1:3"}])
    assert rec1.version == 1
    assert rec1.artifact_id.startswith(f"{rec1.content_hash[:12]}-") and rec1.artifact_id.endswith("-v1")
    assert rec1.verification == "unverified"
    assert rec1.source_evidence == [{"file": "data.csv", "ref": "rows=1:3"}]

    # тот же контент + тот же path → всё равно НОВАЯ версия (явная история)
    rec2 = await artifacts_engine.register_artifact(
        type="md", path=path, content="# Отчёт v1")
    assert rec2.version == 2
    assert rec2.content_hash == rec1.content_hash
    assert rec2.artifact_id.endswith("-v2") and rec2.artifact_id != rec1.artifact_id

    versions = await artifacts_engine.list_versions(path)
    assert [r.version for r in versions] == [1, 2]

    got = await artifacts_engine.get_artifact(rec1.artifact_id)
    assert got is not None and got.creator_task == "42"


@pg_gate
async def test_mark_verified(pg):
    from bossman import artifacts_engine
    path = f"/tmp/verify-{uuid.uuid4().hex}.txt"
    rec = await artifacts_engine.register_artifact(
        type="txt", path=path, content=b"payload")
    assert await artifacts_engine.mark_verified(rec.artifact_id, "verified") is True
    got = await artifacts_engine.get_artifact(rec.artifact_id)
    assert got.verification == "verified"
    # несуществующий id — честный False, не исключение
    assert await artifacts_engine.mark_verified("нет-такого-v9", "verified") is False


# ---------- build_content (без PG) ----------

def test_build_content_csv_roundtrip():
    rows = [["месяц", "выручка"], ["январь", "1200"]]
    data = build_content("csv", rows)
    back = list(csv.reader(io.StringIO(data.decode("utf-8"))))
    assert back == rows


def test_build_content_zip_roundtrip():
    data = build_content("zip", {"readme.md": "# привет", "a/b.txt": "внутри"})
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert set(zf.namelist()) == {"readme.md", "a/b.txt"}
        assert zf.read("a/b.txt").decode("utf-8") == "внутри"


def test_build_content_json_and_text():
    data = build_content("json", {"город": "Москва", "n": 1})
    assert json.loads(data.decode("utf-8")) == {"город": "Москва", "n": 1}
    assert "Москва" in data.decode("utf-8")        # ensure_ascii=False
    assert build_content("md", "# Заголовок") == "# Заголовок".encode("utf-8")
    assert build_content("txt", "просто текст") == "просто текст".encode("utf-8")


def test_build_content_zip_rejects_traversal_and_absolute():
    with pytest.raises(ValueError, match="небезопасное имя"):
        build_content("zip", {"../evil.txt": "нет"})
    with pytest.raises(ValueError, match="небезопасное имя"):
        build_content("zip", {"/abs.txt": "нет"})
    with pytest.raises(ValueError, match="небезопасное имя"):
        build_content("zip", {"a\\..\\evil": "нет"})


def test_build_content_unsupported_is_honest():
    for fmt in ("xlsx", "docx", "pdf", "pptx"):
        with pytest.raises(ValueError) as exc:
            build_content(fmt, "что угодно")
        msg = str(exc.value)
        assert "внешней библиотеки" in msg and "не реализовано" in msg
        assert fmt in msg
    assert not SUPPORTED_CREATE_FORMATS & {"xlsx", "docx", "pdf", "pptx"}
    with pytest.raises(ValueError, match="неизвестный формат"):
        build_content("exe", "нет")


@pg_gate
async def test_same_content_different_paths_get_distinct_ids(pg):
    """Регресс: одинаковый контент по РАЗНЫМ путям — разные артефакты, оба v1.
    До фикса их artifact_id совпадали и вторая регистрация падала на UNIQUE."""
    from bossman import artifacts_engine
    content = "# одинаковый отчёт"
    a = await artifacts_engine.register_artifact(
        type="md", path=f"/tmp/a-{uuid.uuid4().hex}.md", content=content)
    b = await artifacts_engine.register_artifact(
        type="md", path=f"/tmp/b-{uuid.uuid4().hex}.md", content=content)
    assert a.version == b.version == 1
    assert a.content_hash == b.content_hash
    assert a.artifact_id != b.artifact_id
