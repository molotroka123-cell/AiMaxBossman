"""Владельческие сценарии 97–100: ДАННЫЕ ВЛАДЕЛЬЦА — выгрузка и удаление.

Четыре вопроса, на которые нельзя отвечать «ну, оно где-то удалилось»:

* 97 — выгрузка владельца читается НЕЗАВИСИМО от продукта (стандартный
  `zipfile` + `json`) и содержит ровно то, что владелец видел: состав событий
  сверяется и с лентой продукта, и с ЧТЕНИЕМ таблицы мимо продукта;
* 98 — в выгрузку не попадает ни один ключ и ни одно приватное поле: точное
  значение ключа, ЛЕЖАЩЕЕ в логе на диске, не встречается ни в одном байте ни
  одного члена архива, и его нет в самой таблице событий;
* 99 — удаление по требованию владельца удаляет ВЕЗДЕ, а не из витрины:
  после переиндексации от источника не остаётся ни строки ни в одной из
  четырёх таблиц индекса — проверено чтением файла индекса мимо продукта;
* 100 — ограниченное хранение убирает старое ИЗ ХРАНИЛИЩА, а не с экрана, и
  не трогает свежее.

ПОЧЕМУ ИМЕННО ЧТЕНИЕ МИМО ПРОДУКТА. Витрина и хранилище расходятся молча:
продукт перестаёт ПОКАЗЫВАТЬ запись задолго до того, как перестаёт её ХРАНИТЬ.
Поэтому каждое утверждение «удалено» здесь подтверждается отдельным
соединением стандартного `sqlite3` прямо по файлу, а каждое утверждение
«выгружено» — чтением архива стандартным `zipfile`.

КЛЮЧИ. Настоящих ключей в прогоне нет и не требуется: фиктивные значения
собираются из кусков, чтобы целиком в исходнике не лежать, и живут только в
окружении и во временном каталоге сценария.

ЖИВОГО ШАГА МОДЕЛИ НЕТ: все четыре объявлены `model_step: "none"`.

Зависимости модуля — стандартная библиотека. Всё, что тянет sqlalchemy и
fastapi (`bcc.*`), импортируется ВНУТРИ функций и объявлено в реестре как
`command_center`: BL-085 — корневой CI ставит только
pytest/pytest-timeout/psutil/httpx/pyyaml.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "command-center"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scenario_runner import INSTALLED_PRODUCT, PRODUCT_CONTRACTS, scenario  # noqa: E402


class _Call:
    """Запрос продукта: ровно то, что читают его обработчики."""

    def __init__(self, svc, body: dict | None = None) -> None:
        state = type("Состояние", (), {"svc": svc})()
        self.app = type("Приложение", (), {"state": state})()
        self._body = body

    async def json(self) -> dict:
        if self._body is None:
            raise ValueError("тела нет")
        return self._body


class _EnvPatch:
    """Переменные окружения на время сценария и обязательный возврат назад."""

    def __init__(self, **values: str) -> None:
        self._values = values
        self._before: dict[str, str | None] = {}

    def __enter__(self) -> "_EnvPatch":
        for name, value in self._values.items():
            self._before[name] = os.environ.get(name)
            os.environ[name] = value
        return self

    def __exit__(self, *_exc) -> None:
        for name, previous in self._before.items():
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous


def _services(ctx, tag: str):
    from bcc.api import Services  # noqa: PLC0415
    from bcc.config import Settings  # noqa: PLC0415

    data_dir = ctx.path(tag, "держатель").parent
    data_dir.mkdir(parents=True, exist_ok=True)
    db_file = data_dir / "bcc.db"
    settings = Settings(data_dir=data_dir, database_url=f"sqlite+aiosqlite:///{db_file}")
    return Services(settings, start_workers=False, announce_token=False), data_dir, db_file


def _raw(db_file: Path, sql: str, params: tuple = ()) -> list[tuple]:
    """Чтение хранилища МИМО продукта: отдельное соединение стандартного sqlite3."""
    con = sqlite3.connect(str(db_file))
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def _fake_key(*parts: str) -> str:
    """Фиктивный ключ, собранный из кусков: целиком в исходнике он не лежит."""
    return "".join(parts)


def _owner_data(ctx, tag: str, key: str):
    """Продукт со следами владельца: лог с ключом, .env с ключом, два события."""
    from bcc import desktop  # noqa: PLC0415

    svc, data_dir, db_file = _services(ctx, tag)
    (data_dir / ".env").write_text(f"OWNER_PROVIDER_KEY={key}\n", encoding="utf-8")
    log = desktop._run_log_path(data_dir)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("окно поднялось\n"
                   f"провайдер отверг ключ {key}\n"
                   "видимая владельцу строка\n", encoding="utf-8")
    return svc, data_dir, db_file, log


# ------------------------------------------------------------------ 97
@scenario(id="OS-97", depth=INSTALLED_PRODUCT)
def os97_export_reads_without_the_product(ctx) -> None:
    """Выгрузка открывается без продукта и несёт ровно то, что видел владелец.

    Цепочка — собственная ручка продукта `POST /api/diag/bundle`
    (`bcc/features/diag_bundle.py::build`) на настоящем `bcc.api.Services`.
    Дальше продукт в цепочке НЕ участвует: архив открывается стандартным
    `zipfile`, части разбираются стандартным `json`, а состав событий
    сверяется с ДВУМЯ независимыми источниками — лентой продукта
    (`svc.bus.recent`) и чтением таблицы `events` стандартным `sqlite3`.

    ПОРЯДОК. Отказ при выключенном флаге проверяется ПЕРВЫМ: он обязан не
    создать ни одного файла, и доказать это можно только пока каталога сборки
    ещё нет.
    """
    from fastapi import HTTPException  # noqa: PLC0415

    from bcc.features import diag_bundle as diag  # noqa: PLC0415

    ctx.reached_installed_product(
        "bcc.api.Services и bcc.features.diag_bundle установленного Command Center")
    key = _fake_key("sk-", "Hq4", "Tz8", "Rm2", "Vd6", "Nb1", "Ws9")

    async def run() -> dict:
        out: dict = {}
        svc, data_dir, db_file, _log = _owner_data(ctx, "служба-97", key)
        await svc.db.create_all()
        await svc.bus.emit("owner.заметка", message="первое событие владельца")
        await svc.bus.emit("owner.вторая", message="второе событие владельца")

        with _EnvPatch(**{diag.FLAG: "0"}):
            try:
                await diag.build(_Call(svc))
                out["off"] = ("СОБРАЛ", "")
            except HTTPException as exc:
                out["off"] = (exc.status_code, str(exc.detail)[:160])
            bundle_dir = data_dir / diag.BUNDLE_DIRNAME
            out["off_files"] = sorted(p.name for p in bundle_dir.glob("*")) \
                if bundle_dir.is_dir() else []

        with _EnvPatch(**{diag.FLAG: "1"}):
            result = await diag.build(_Call(svc))
        out["result"] = {k: result[k] for k in ("name", "size_bytes", "redactions", "included")}

        # Дальше продукт не участвует.
        with zipfile.ZipFile(result["path"]) as archive:
            out["members"] = archive.namelist()
            blobs = {name: archive.read(name) for name in out["members"]}
        out["events"] = json.loads(blobs[diag.EVENTS_NAME].decode("utf-8"))
        out["report"] = json.loads(blobs[diag.REPORT_NAME].decode("utf-8"))
        out["files_part"] = json.loads(blobs[diag.FILES_NAME].decode("utf-8"))
        out["denied_members"] = [n for n in out["members"] if diag._denied(n)]
        # Проверка «запрещённого члена нет» бессмысленна, если запрещённых
        # файлов нет и на диске: тогда она проходит вырожденно. Поэтому сначала
        # перечисляем то, что на диске ЕСТЬ и что класть нельзя.
        out["denied_on_disk"] = sorted(
            {p.name for p in data_dir.rglob("*") if p.is_file() and diag._denied(p.name)})
        out["denied_leaked"] = sorted(set(out["denied_on_disk"]) & set(out["members"]))
        out["private_in_listing"] = key.encode() in blobs[diag.FILES_NAME]

        out["shown"] = [row["kind"] for row in
                        reversed(await svc.bus.recent(diag.EVENTS_LIMIT))]
        await svc.db.close()
        out["stored"] = [row[0] for row in
                         _raw(db_file, "select kind from events order by id")]
        return out

    out = asyncio.run(run())
    exported = [row.get("kind") for row in out["events"]]
    ctx.positive("архив открывается стандартным zipfile, без продукта",
                 set(out["members"]) >= {"events.json", "report.json", "files.json"},
                 str(out["members"]))
    ctx.positive("выгруженные события — ровно те, что владелец видит в ленте",
                 exported == out["shown"][:len(exported)] and len(exported) >= 2,
                 f"архив {exported} / лента {out['shown']}")
    ctx.positive("выгруженные события совпадают с чтением таблицы МИМО продукта",
                 exported == out["stored"][:len(exported)],
                 f"архив {exported} / хранилище {out['stored']}")
    ctx.positive("отчёт называет платформу, пределы и перечень исключённого",
                 bool(out["report"]["environment"].get("platform"))
                 and out["report"]["limits"]["events"] == 200
                 and len(out["report"]["excluded"]) >= 5,
                 str(out["report"]["limits"]))

    # --- отрицательные контроли
    ctx.negative("при выключенном флаге сбор отказан с названной причиной",
                 out["off"][0] == 409 and "выключен" in out["off"][1], str(out["off"]))
    ctx.negative("при выключенном флаге не создано НИ ОДНОГО файла",
                 out["off_files"] == [], str(out["off_files"]))
    ctx.negative("запрещённые файлы на диске ЕСТЬ, но ни один не стал членом архива",
                 len(out["denied_on_disk"]) >= 2 and out["denied_leaked"] == []
                 and out["denied_members"] == [],
                 f"на диске {out['denied_on_disk']}, в архиве {out['members']}")
    ctx.negative("перечень файлов несёт имена и размеры, но не содержимое",
                 out["private_in_listing"] is False
                 and all(set(e) == {"path", "bytes"} for e in out["files_part"]["files"]),
                 f"файлов в перечне {out['files_part']['count']}")


# ------------------------------------------------------------------ 98
@scenario(id="OS-98", depth=INSTALLED_PRODUCT)
def os98_export_carries_no_key_and_no_private_field(ctx) -> None:
    """Ни один ключ и ни одно приватное поле не доезжают до выгрузки.

    Ключ владельца кладётся ровно туда, куда он попадает в жизни: в `.env`, в
    лог окна на диске и внутрь события (и в имя поля, и в свободный текст).
    После сборки архив проверяется ПОБАЙТОВО по каждому члену, а таблица
    событий — отдельным соединением `sqlite3` мимо продукта.

    Две половины обязательны вместе: «ключа нет» удовлетворяется вырожденно
    архивом, из которого вычищено всё, поэтому рядом проверяется, что
    безобидные строки владельца на месте, а счётчик вычищенных мест не нулевой
    — молчаливая чистка неотличима от неработающей.
    """
    from bcc.features import diag_bundle as diag  # noqa: PLC0415

    ctx.reached_installed_product(
        "bcc.api.Services, шина событий и bcc.features.diag_bundle этой ветки")
    key = _fake_key("sk-", "Gp5", "Lx3", "Qa7", "Zr2", "Mn8", "Bd4")

    async def run() -> dict:
        out: dict = {}
        svc, data_dir, db_file, log = _owner_data(ctx, "служба-98", key)
        await svc.db.create_all()
        await svc.bus.emit("owner.ключ",
                           message=f"провайдер отверг ключ {key} владельца",
                           api_key=key, harmless="безобидная строка владельца")
        out["log_has_key"] = key in log.read_text(encoding="utf-8")
        out["env_has_key"] = key in (data_dir / ".env").read_text(encoding="utf-8")

        with _EnvPatch(**{diag.FLAG: "1"}):
            result = await diag.build(_Call(svc))
        out["redactions"] = int(result["redactions"])
        with zipfile.ZipFile(result["path"]) as archive:
            out["members"] = archive.namelist()
            blobs = {name: archive.read(name) for name in out["members"]}
        out["leaked_members"] = [name for name, blob in blobs.items() if key.encode() in blob]
        harmless = "безобидная строка владельца".encode("utf-8")
        out["harmless_kept"] = any(harmless in blob for blob in blobs.values())
        out["log_member"] = blobs.get(log.name, b"").decode("utf-8", "replace")
        events = json.loads(blobs[diag.EVENTS_NAME].decode("utf-8"))
        out["event"] = next((e for e in events if e.get("kind") == "owner.ключ"), {})
        out["excluded_names"] = [e["path"] for e in result["excluded"]]

        await svc.db.close()
        stored = _raw(db_file, "select data from events")
        out["key_in_events_table"] = any(key in str(row[0]) for row in stored)
        out["stored_sample"] = str(stored[0][0])[:200] if stored else ""
        return out

    out = asyncio.run(run())
    if not (out["log_has_key"] and out["env_has_key"]):
        ctx.not_proven("ключ не лёг ни в лог, ни в .env: чистку проверять не на чем")
    ctx.positive("ключ действительно лежал на диске до сборки — мерить есть что",
                 out["log_has_key"] and out["env_has_key"],
                 f"лог={out['log_has_key']} .env={out['env_has_key']}")
    ctx.positive("чистка видима: счётчик вычищенных мест не нулевой",
                 out["redactions"] > 0, f"вычищено мест: {out['redactions']}")
    ctx.positive("безобидная строка владельца в выгрузке ОСТАЛАСЬ",
                 out["harmless_kept"] is True, str(out["harmless_kept"]))

    # --- отрицательные контроли
    ctx.negative("значение ключа не встречается НИ В ОДНОМ члене архива",
                 out["leaked_members"] == [],
                 f"членов {len(out['members'])}, утечки в {out['leaked_members']}")
    ctx.negative("в выгруженном логе на месте ключа стоит отметка чистки",
                 key not in out["log_member"] and "REDACTED" in out["log_member"],
                 out["log_member"][:160])
    ctx.negative("приватное поле события отдано отметкой, а не значением",
                 out["event"].get("data", {}).get("api_key") == "***REDACTED***"
                 and key not in json.dumps(out["event"], ensure_ascii=False),
                 str(out["event"].get("data", {}))[:200])
    ctx.negative("ключ не попал и в саму таблицу событий (чтение мимо продукта)",
                 out["key_in_events_table"] is False, out["stored_sample"])
    ctx.negative(".env, secret.key, токен и база объявлены исключёнными поимённо",
                 any(".env" in n for n in out["excluded_names"])
                 and any("secret.key" in n for n in out["excluded_names"])
                 and any(".db" in n for n in out["excluded_names"]),
                 str(out["excluded_names"]))


# ------------------------------------------------------------------ 99
@scenario(id="OS-99", depth=PRODUCT_CONTRACTS)
def os99_owner_deletion_removes_everything_everywhere(ctx) -> None:
    """Удалённая владельцем заметка исчезает из ВСЕХ таблиц индекса, не из витрины.

    Настоящий путь владельца: заметка лежит в хранилище, продукт её
    проиндексировал (`SQLiteMemoryBackend.index_sync` — тот же код, что стоит
    за `POST /api/memory/index`), владелец удаляет файл и просит переиндексацию.
    Поиск после этого её не находит — но поиск это ВИТРИНА. Поэтому индекс
    читается отдельным соединением стандартного `sqlite3`, и проверяются все
    четыре таблицы, где содержимое заметки хранится: `files`, `sections`,
    `chunks` и `postings`.

    Отрицательные контроли ловят два разных вырождения: «удалили всё» (сосед
    обязан выжить) и «удалили лишнее» (переиндексация ЧАСТИ хранилища не имеет
    права стирать то, во что не заглядывала).
    """
    from bcc.v2.memory.sqlite_index import SQLiteMemoryBackend  # noqa: PLC0415

    vault = ctx.path("хранилище", "личное", "держатель").parent.parent
    (vault / "личное").mkdir(parents=True, exist_ok=True)
    (vault / "рабочее").mkdir(parents=True, exist_ok=True)
    private = vault / "личное" / "сейф.md"
    private.write_text("# Личное\n\nкод от сейфа и номер квартиры сорок два\n",
                       encoding="utf-8")
    neighbour = vault / "рабочее" / "проект.md"
    neighbour.write_text("# Рабочее\n\nдедлайн в пятницу по проекту\n", encoding="utf-8")

    index = ctx.path("индекс", "память.sqlite")
    backend = SQLiteMemoryBackend(index_path=index, vault_root=vault)
    roots = [vault / "личное", vault / "рабочее"]

    out: dict = {}
    out["indexed"] = backend.index_sync(roots)
    out["found_before"] = [(h.source, h.content[:40]) for h in backend.search_sync("сейфа")]
    out["rows_before"] = {
        table: _raw(index, f"select count(*) from {table} where source = ?",
                    ("личное/сейф.md",))[0][0]
        for table in ("files", "sections", "chunks")}
    out["rows_before"]["postings"] = _raw(
        index, "select count(*) from postings where chunk_hash in "
               "(select chunk_hash from chunks where source = ?)", ("личное/сейф.md",))[0][0]

    # Переиндексация ЧАСТИ хранилища не имеет права трогать остальное.
    out["partial"] = backend.index_sync([vault / "личное"])
    out["sources_after_partial"] = sorted(r[0] for r in
                                          _raw(index, "select source from files"))

    private.unlink()
    out["reindexed"] = backend.index_sync(roots)
    out["found_after"] = [(h.source, h.content[:40]) for h in backend.search_sync("сейфа")]
    out["neighbour_found"] = [h.source for h in backend.search_sync("дедлайн")]
    out["rows_after"] = {
        table: _raw(index, f"select count(*) from {table} where source = ?",
                    ("личное/сейф.md",))[0][0]
        for table in ("files", "sections", "chunks")}
    out["rows_after"]["postings"] = _raw(
        index, "select count(*) from postings where chunk_hash in "
               "(select chunk_hash from chunks where source = ?)", ("личное/сейф.md",))[0][0]
    out["orphan_postings"] = _raw(
        index, "select count(*) from postings where chunk_hash not in "
               "(select chunk_hash from chunks)")[0][0]
    out["sources_after"] = sorted(r[0] for r in _raw(index, "select source from files"))

    ctx.positive("до удаления заметка владельца действительно ищется и находится",
                 len(out["found_before"]) == 1
                 and out["found_before"][0][0] == "личное/сейф.md",
                 str(out["found_before"]))
    ctx.positive("до удаления содержимое лежит во ВСЕХ четырёх таблицах индекса",
                 all(v > 0 for v in out["rows_before"].values()), str(out["rows_before"]))
    ctx.positive("после удаления файла переиндексация называет число убранного",
                 out["reindexed"]["removed"] == 1, str(out["reindexed"]))
    ctx.positive("соседняя заметка владельца продолжает находиться",
                 out["neighbour_found"] == ["рабочее/проект.md"],
                 str(out["neighbour_found"]))

    # --- отрицательные контроли
    ctx.negative("витрина поиска больше не отдаёт удалённую заметку",
                 out["found_after"] == [], str(out["found_after"]))
    ctx.negative("ни одной строки удалённого источника не осталось в хранилище",
                 all(v == 0 for v in out["rows_after"].values()), str(out["rows_after"]))
    ctx.negative("осиротевших постингов после удаления не осталось",
                 out["orphan_postings"] == 0, str(out["orphan_postings"]))
    ctx.negative("переиндексация ЧАСТИ хранилища не стёрла остальное",
                 out["sources_after_partial"] == ["личное/сейф.md", "рабочее/проект.md"]
                 and out["partial"]["removed"] == 0,
                 f"{out['sources_after_partial']} / {out['partial']}")
    ctx.negative("удалено ровно одно — сосед остался в хранилище",
                 out["sources_after"] == ["рабочее/проект.md"], str(out["sources_after"]))


# ------------------------------------------------------------------ 100
@scenario(id="OS-100", depth=INSTALLED_PRODUCT)
def os100_retention_removes_from_storage_not_from_the_screen(ctx) -> None:
    """Ограниченное хранение убирает старое из ФАЙЛА базы, а не с экрана.

    Меряется настоящая шина установленного продукта (`bcc.events.EventBus.prune`
    на `bcc.api.Services`) — та самая, которую дёргает `features/control_plane.py`
    по объявленному сроку. Хранилище читается стандартным `sqlite3` прямо по
    файлу базы: «исчезло из ленты» и «исчезло из хранилища» — разные
    утверждения, и владельцу обещано второе.

    Отрицательные контроли закрывают три вырождения: старое обязано исчезнуть
    ИЗ ФАЙЛА, свежее обязано выжить, а предел по числу строк обязан резать
    самое старое, а не самое новое.
    """
    from datetime import timedelta  # noqa: PLC0415

    import sqlalchemy as sa  # noqa: PLC0415

    from bcc.db import events as events_t, utcnow  # noqa: PLC0415
    from bcc.events import EventBus  # noqa: PLC0415

    ctx.reached_installed_product(
        "шина событий bcc.api.Services и её ограниченное хранение на этой ветке")

    async def run() -> dict:
        out: dict = {}
        svc, _data_dir, db_file = _services(ctx, "служба-100")
        await svc.db.create_all()
        await svc.bus.emit("owner.свежее", message="свежее событие владельца")
        async with svc.db.session() as session:
            await session.execute(sa.insert(events_t).values(
                kind="owner.старое", data={"message": "старое событие владельца"},
                ts=utcnow() - timedelta(days=40)))
            await session.commit()

        out["before"] = [row[0] for row in
                         _raw(db_file, "select kind from events order by id")]
        out["removed"] = await svc.bus.prune(max_age_days=14, max_rows=200_000)
        out["after"] = [row[0] for row in
                        _raw(db_file, "select kind from events order by id")]
        out["shown"] = [row["kind"] for row in await svc.bus.recent(50)]

        # Предел по числу строк: режется самое старое, новейшее остаётся.
        async with svc.db.session() as session:
            for i in range(6):
                await session.execute(sa.insert(events_t).values(
                    kind=f"owner.{i}", data={"n": i},
                    ts=utcnow() - timedelta(minutes=6 - i)))
            await session.commit()
        out["rows_removed"] = await svc.bus.prune(max_age_days=14, max_rows=2)
        out["tail"] = [row[0] for row in
                       _raw(db_file, "select kind from events order by id")]

        out["no_db"] = await EventBus(None).prune()
        await svc.db.close()
        return out

    out = asyncio.run(run())
    ctx.positive("до уборки старое и свежее лежат в хранилище рядом",
                 out["before"] == ["owner.свежее", "owner.старое"], str(out["before"]))
    ctx.positive("уборка называет ЧИСЛО убранных строк, а не «готово»",
                 out["removed"]["events"] == 1
                 and (len(out["before"]) - len(out["after"])) == out["removed"]["events"],
                 f"{out['removed']}: {out['before']} → {out['after']}")
    ctx.positive("свежее событие владельца осталось и видно в ленте",
                 out["after"] == ["owner.свежее"] and "owner.свежее" in out["shown"],
                 f"{out['after']} / {out['shown']}")

    # --- отрицательные контроли
    ctx.negative("старое событие исчезло ИЗ ФАЙЛА базы, а не только из ленты",
                 "owner.старое" not in out["after"]
                 and "owner.старое" not in out["shown"],
                 f"{out['after']} / {out['shown']}")
    ctx.negative("предел по числу строк режет самое старое, а не самое новое",
                 out["tail"] == ["owner.4", "owner.5"]
                 and out["rows_removed"]["events"] == 5,
                 f"{out['rows_removed']}: {out['tail']}")
    ctx.negative("уборка без базы ничего не удаляет и не падает",
                 out["no_db"] == {"events": 0, "run_events": 0}, str(out["no_db"]))
