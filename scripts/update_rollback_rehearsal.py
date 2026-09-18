"""Репетиция обновления и отката на настоящих данных владельца.

Зачем. `KNOWN_GOOD_SHAS.json` до сих пор называет обновление и откат
НЕПРОВЕРЕННЫМИ, а раздел 6 задания требует не открывать старым runtime уже
необратимо изменённую базу без проверки совместимости и не удалять файлы
владельца ради «чистой установки». Документ этого не доказывает: доказывает
только прогон, который на самом деле пишет данные, на самом деле их портит и
на самом деле возвращает.

Что здесь измеряется по-настоящему (сторона ДАННЫХ):

1. Прежняя сборка создаёт каталог данных владельца и пишет в него записи
   через настоящий `bcc.db.Database` и настоящую схему, плюс файл владельца
   на диске.
2. Снимается резерв каталога — шаг 1 из `docs/final/ROLLBACK.md`.
3. «Новая сборка» меняет базу НЕОБРАТИМО для прежней: ставит поколение схемы
   на единицу выше и добавляет таблицу, которой прежняя сборка не знает.
4. Прежний runtime обязан ОТКАЗАТЬ по имени. Проверяется не только отказ:
   файл владельца обязан остаться байт в байт, база — не получить ни одной
   записи от прежней сборки.
5. Парный негативный контроль: та же база, тот же прежний код, но без
   защиты — открывается молча и принимает запись. Без этой пары шаг 4 не
   отличить от совпадения.
6. Резерв возвращается на место, прежний runtime открывает каталог, записи
   владельца и файл совпадают с исходными.

Чего здесь НЕТ, и это не смягчается формулировкой:

* подмена папки приложения Windows (распакованный ZIP на целевой машине) —
  `OWNER_REQUIRED`: нужен сам компьютер владельца;
* сборки, выпущенные ДО отметки поколения, — проверять нечем, их защищает
  только резервная копия.

Запуск: `python scripts/update_rollback_rehearsal.py [--json <файл>]`.
Коды выхода: 0 — PASS, 1 — FAIL.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "command-center"))

import sqlalchemy as sa  # noqa: E402

from bcc.db import (  # noqa: E402
    SCHEMA_GENERATION,
    Database,
    DatabaseFromNewerBuild,
    decisions,
    metadata,
    settings_kv,
)

OWNER_KEY = "owner-rehearsal/update-rollback@v1"
OWNER_DECISION = "Каталог данных владельца переживает обновление и откат."
OWNER_NOTE = "щит поколения схемы"
OWNER_FILE_BYTES = b"\x89PNG\r\n\x1a\n" + b"owner asset that must survive both directions" * 64


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(256 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _user_version(db_file: Path) -> int:
    engine = sa.create_engine(f"sqlite:///{db_file}")
    try:
        with engine.connect() as conn:
            return int(conn.execute(sa.text("PRAGMA user_version")).scalar() or 0)
    finally:
        engine.dispose()


class Step:
    """Один шаг репетиции: имя, вердикт, чем он подтверждён."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.verdict = "NOT_RUN"
        self.detail = ""

    def record(self, ok: bool, detail: str) -> bool:
        self.verdict = "PASS" if ok else "FAIL"
        self.detail = detail
        return ok

    def as_dict(self) -> dict:
        return {"step": self.name, "verdict": self.verdict, "detail": self.detail}


async def _old_build_writes_owner_data(data_dir: Path) -> dict:
    """Прежняя сборка: настоящая схема, настоящие записи, настоящий файл."""
    db_file = data_dir / "bossman.sqlite3"
    db = Database(f"sqlite+aiosqlite:///{db_file}")
    try:
        await db.create_all()
        async with db.engine.begin() as conn:
            await conn.execute(decisions.insert().values(
                key=OWNER_KEY, decision=OWNER_DECISION,
                reason="репетиция §6", scope="Bossman V8", created_by="owner"))
            await conn.execute(settings_kv.insert().values(
                key="owner.rehearsal.note", value_enc=OWNER_NOTE))
    finally:
        await db.engine.dispose()

    asset = data_dir / "media" / "owner-asset.png"
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_bytes(OWNER_FILE_BYTES)
    return {"db_file": db_file, "asset": asset, "asset_sha256": _sha256(asset)}


async def _read_owner_data(db_file: Path) -> dict:
    """Читает то же самое прежним runtime — без него «данные на месте» слово."""
    db = Database(f"sqlite+aiosqlite:///{db_file}")
    try:
        await db.create_all()
        async with db.engine.connect() as conn:
            decision = (await conn.execute(
                sa.select(decisions.c.decision).where(decisions.c.key == OWNER_KEY)
            )).scalar()
            note = (await conn.execute(
                sa.select(settings_kv.c.value_enc).where(
                    settings_kv.c.key == "owner.rehearsal.note")
            )).scalar()
            rows = (await conn.execute(
                sa.select(sa.func.count()).select_from(decisions))).scalar()
    finally:
        await db.engine.dispose()
    return {"decision": decision, "note": note, "decisions_rows": rows}


def _newer_build_changes_the_database(db_file: Path) -> None:
    """«Новая сборка»: меняет форму базы и поднимает поколение.

    Таблица `studio_collections_v9` — ровно тот случай, ради которого
    поколение и растят: прежняя сборка о ней не знает, а данные владельца уже
    в неё уехали.
    """
    engine = sa.create_engine(f"sqlite:///{db_file}")
    try:
        with engine.begin() as conn:
            conn.execute(sa.text(
                "CREATE TABLE studio_collections_v9 ("
                " id INTEGER PRIMARY KEY, title TEXT NOT NULL)"))
            conn.execute(sa.text(
                "INSERT INTO studio_collections_v9 (title) VALUES ('альбом владельца')"))
            conn.execute(sa.text(f"PRAGMA user_version = {SCHEMA_GENERATION + 1}"))
    finally:
        engine.dispose()


def _unguarded_open(db_file: Path) -> str:
    """Негативный контроль: тот же прежний код БЕЗ защиты.

    Повторяет `create_all` в обход `_refuse_a_newer_database`. Если это
    проходит молча и принимает запись — вред настоящий, а шаг 4 не совпадение.
    """
    engine = sa.create_engine(f"sqlite:///{db_file}")
    try:
        metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(decisions.insert().values(
                key="unguarded/old-build-wrote-here@v1",
                decision="прежняя сборка записала в базу новой",
                reason="негативный контроль", scope="Bossman V8", created_by="old-build"))
        with engine.connect() as conn:
            wrote = int(conn.execute(sa.text(
                "SELECT count(*) FROM decisions "
                "WHERE key='unguarded/old-build-wrote-here@v1'")).scalar())
        return f"без защиты открылась молча и приняла запись (строк {wrote})"
    finally:
        engine.dispose()


async def rehearse(root: Path) -> dict:
    steps: list[Step] = []
    ok = True

    def step(name: str) -> Step:
        made = Step(name)
        steps.append(made)
        return made

    data_dir = root / "CommandCenter"
    data_dir.mkdir(parents=True)

    # 1. Прежняя сборка пишет данные владельца.
    s = step("прежняя сборка создала каталог данных и записала данные владельца")
    written = await _old_build_writes_owner_data(data_dir)
    before = await _read_owner_data(written["db_file"])
    stamp = _user_version(written["db_file"])
    ok &= s.record(
        before["decision"] == OWNER_DECISION and before["note"] == OWNER_NOTE
        and stamp == SCHEMA_GENERATION,
        f"решение и заметка на месте, поколение схемы {stamp}, "
        f"файл владельца SHA-256 {written['asset_sha256'][:16]}…")

    # 2. Резерв — шаг 1 из ROLLBACK.md.
    s = step("снят резерв каталога данных перед обновлением")
    backup = root / "CommandCenter.backup"
    shutil.copytree(data_dir, backup)
    backup_sha = _sha256(backup / "media" / "owner-asset.png")
    ok &= s.record(backup_sha == written["asset_sha256"],
                   f"резерв {backup.name}, файл владельца совпадает побайтно")

    # 3. «Новая сборка» меняет базу необратимо.
    s = step("новая сборка изменила базу необратимо для прежней")
    _newer_build_changes_the_database(written["db_file"])
    raised = _user_version(written["db_file"])
    ok &= s.record(raised == SCHEMA_GENERATION + 1,
                   f"добавлена таблица studio_collections_v9, поколение {raised}")

    # 4. Прежний runtime обязан отказать по имени и ничего не тронуть.
    s = step("прежний runtime ОТКАЗАЛ открыть базу новой сборки")
    asset_before = _sha256(written["asset"])
    refusal = ""
    db = Database(f"sqlite+aiosqlite:///{written['db_file']}")
    try:
        try:
            await db.create_all()
            refused = False
        except DatabaseFromNewerBuild as exc:
            refused, refusal = True, str(exc)
    finally:
        await db.engine.dispose()
    still = _user_version(written["db_file"])
    asset_after = _sha256(written["asset"])
    engine = sa.create_engine(f"sqlite:///{written['db_file']}")
    try:
        with engine.connect() as conn:
            rows_now = int(conn.execute(
                sa.text("SELECT count(*) FROM decisions")).scalar())
    finally:
        engine.dispose()
    named = (str(SCHEMA_GENERATION + 1) in refusal and str(SCHEMA_GENERATION) in refusal
             and "резерв" in refusal.lower())
    intact = asset_after == asset_before and rows_now == before["decisions_rows"]
    if not refused:
        detail = ("прежний runtime ОТКРЫЛ базу новой сборки молча — "
                  "это и есть та потеря данных, ради которой шаг существует")
    elif not named:
        detail = ("отказ был, но не назвал оба поколения и резерв: "
                  f"{refusal[:200]}")
    elif not intact:
        detail = (f"отказ был, но прежняя сборка успела тронуть данные: "
                  f"записей {rows_now} вместо {before['decisions_rows']}, "
                  f"файл владельца {'цел' if asset_after == asset_before else 'изменён'}")
    else:
        detail = ("отказ назвал оба поколения и путь восстановления; файл "
                  f"владельца не тронут; записей в decisions по-прежнему {rows_now}")
    ok &= s.record(refused and named and still == SCHEMA_GENERATION + 1 and intact, detail)

    # 5. Негативный контроль на копии: без защиты вред настоящий.
    s = step("негативный контроль: без защиты прежняя сборка пишет в базу новой")
    twin = root / "twin.sqlite3"
    shutil.copy2(written["db_file"], twin)
    harm = _unguarded_open(twin)
    ok &= s.record(bool(harm), harm)

    # 6. Возврат резерва — и данные владельца на месте.
    s = step("резерв возвращён, прежний runtime открыл каталог, данные владельца целы")
    shutil.rmtree(data_dir)
    shutil.copytree(backup, data_dir)
    after = await _read_owner_data(data_dir / "bossman.sqlite3")
    restored_sha = _sha256(data_dir / "media" / "owner-asset.png")
    ok &= s.record(
        after == before and restored_sha == written["asset_sha256"],
        f"решение, заметка и {after['decisions_rows']} записей совпали; "
        f"файл владельца SHA-256 совпал побайтно")

    return {
        "verdict": "PASS" if ok else "FAIL",
        "schema_generation": SCHEMA_GENERATION,
        "steps": [s.as_dict() for s in steps],
        "scope": {
            "UPDATE_ROLLBACK_DATA": "PASS" if ok else "FAIL",
            "UPDATE_ROLLBACK_ARCHIVE": "OWNER_REQUIRED",
            "UPDATE_ROLLBACK_PRE_STAMP": "NOT_COVERED",
        },
        "not_covered": [
            "подмена распакованной папки приложения Windows на машине владельца",
            "сборки, выпущенные ДО отметки поколения: их защищает только резерв",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, help="куда записать улику")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="update-rollback-") as tmp:
        report = asyncio.run(rehearse(Path(tmp)))

    for entry in report["steps"]:
        print(f"{entry['verdict']:<4} {entry['step']}\n     {entry['detail']}")
    print()
    for key, value in report["scope"].items():
        print(f"{key}={value}")
    print(f"UPDATE_ROLLBACK={report['verdict']}")
    for line in report["not_covered"]:
        print(f"NOT_COVERED: {line}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
