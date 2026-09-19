"""Владельческие сценарии 76–80: восстановление после срыва, откат и выпуск.

Пять вопросов, на которые владельцу нельзя отвечать «посмотри в логи»:

* 76 — необратимый эффект после срыва ПАРКУЕТСЯ до владельца, а объявленный
  идемпотентный отпускается на новый допуск (BL-092/BL-097);
* 77 — схема БД несёт отметку поколения: база, записанная более новой сборкой,
  НАЗЫВАЕТСЯ и не открывается молча, и отказ наступает ДО единой записи;
* 78 — репетиция отката читает НАСТОЯЩЕЕ состояние, ничего не исполняет, и это
  проверяется сверкой снимка с чтением хранилища, а не словами плана;
* 79 — сертификация выпуска не выдаётся SHA, у которого обязательное задание
  ОТСУТСТВУЕТ: отсутствующий прогон не окрашен никак и читался бы как
  «замечаний нет» — ловушка закрыта и проверена отдельно от «красных» исходов;
* 80 — настоящий откат данных владельца молча не происходит, а произойдя,
  возвращает продукт в рабочее состояние, и это проверяется ЧТЕНИЕМ файла базы
  мимо продукта.

ПОРЯДОК ПОЛОВИН. В 80 отрицательные контроли стоят ПЕРВЫМИ: успешный откат
потребляет одноразовое подтверждение владельца, и после него «без подтверждения
нельзя» доказывалось бы уже потреблённым подтверждением, а не проверкой.

ЖИВОГО ШАГА МОДЕЛИ НЕТ: ключа ИИ в прогоне нет, все пять объявлены
`model_step: "none"`, и ни один шаг здесь модели не требует.

Зависимости модуля — стандартная библиотека, `bossman_shared` (через
`spine_fixtures`) и `tools/exact_sha_certify.py`. Всё, что тянет sqlalchemy и
fastapi (`bcc.*`), импортируется ВНУТРИ функций и объявлено в реестре как
`command_center`: BL-085 — корневой CI ставит только
pytest/pytest-timeout/psutil/httpx/pyyaml.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "command-center"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bossman-core"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import spine_fixtures as fx  # noqa: E402
from bossman_shared.objective_recovery import (APPLIED, COMMITTED, IDEMPOTENT,  # noqa: E402
                                               IRREVERSIBLE, NOT_APPLIED, PARKED,
                                               REASON_AMBIGUOUS, REASON_BAD_ANSWER,
                                               REASON_IRREVERSIBLE, REASON_RETRYABLE,
                                               RELEASED, ROLLBACK_ORDER, UNKNOWN,
                                               bounded_retry, prepare_rollback, recover,
                                               resume_point)
from bossman_shared.objective_store import ObjectiveStoreError  # noqa: E402
from scenario_runner import INSTALLED_PRODUCT, PRODUCT_CONTRACTS, scenario  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]


def _crashed_objective(ctx, tag: str, kind: str):
    """Цель с ОДНОЙ бронью в полёте: ровно то, что остаётся после смерти процесса."""
    now = fx.real_now()
    store, spec, _ = fx.ready_store(ctx.path("состояние", f"{tag}.sqlite3"),
                                    spec=fx.make_spec(expires_at=now + 100_000.0))
    target = ctx.path("работа", f"{tag}.txt")
    proposal = fx.propose(store, spec, target=target, now=now,
                          observation_id=f"obs-{tag}", observed_at=now - 10.0,
                          effects=(fx.effect(target, kind=kind),))
    if proposal is None:
        raise AssertionError("наблюдение не отклонилось: предложения нет")
    decision = fx.kernel().admit(store, proposal, now=now)
    if not decision.admitted:
        raise AssertionError(f"допуск отказал: {decision.reason}")
    return store, spec, target, now


# ------------------------------------------------------------------ 76
@scenario(id="OS-76", depth=PRODUCT_CONTRACTS)
def os76_irreversible_parks_idempotent_releases(ctx) -> None:
    """Необратимое — владельцу; объявленно идемпотентное — на новый допуск.

    BL-097: объявленный класс эффекта раньше до восстановления НЕ ДОХОДИЛ, и
    парковалась ЛЮБАЯ бронь, включая идемпотентную запись файла. Здесь
    проверяется обе половины сразу, потому что по отдельности каждая
    удовлетворяется вырожденно: «всё паркуется» проходит первую, «всё
    отпускается» — вторую, и только пара отличает работу от вырождения.
    """
    store, spec, target, now = _crashed_objective(ctx, "идемпотентная", "IDEMPOTENT_WRITE")
    released = recover(store, fx.OBJECTIVE_A, now=now + 100,
                       is_effect_applied=lambda reservation: NOT_APPLIED)
    outcome = released.outcomes[0]
    ctx.positive("объявленная идемпотентная запись доходит до восстановления как IDEMPOTENT",
                 outcome.effect_class == IDEMPOTENT, f"класс={outcome.effect_class}")
    ctx.positive("не приземлившийся идемпотентный эффект ОТПУСКАЕТСЯ, а не паркуется",
                 outcome.disposition == RELEASED and outcome.reason == REASON_RETRYABLE
                 and outcome.requires_owner is False and released.blocked is False,
                 f"{outcome.disposition}/{outcome.reason}")
    again = fx.propose(store, spec, target=target, now=now + 200,
                       observation_id="obs-после-срыва", observed_at=now + 190,
                       effects=(fx.effect(target, kind="IDEMPOTENT_WRITE"),))
    fresh = fx.kernel().admit(store, again, now=now + 200)
    ctx.positive("после отпускания НОВЫЙ допуск проходит — задача продолжается",
                 fresh.admitted, fresh.reason)

    irreversible_store, _spec2, _t2, now2 = _crashed_objective(ctx, "необратимая", "IRREVERSIBLE")
    parked = recover(irreversible_store, fx.OBJECTIVE_A, now=now2 + 100,
                     is_effect_applied=lambda reservation: NOT_APPLIED)
    parked_outcome = parked.outcomes[0]
    ctx.positive("необратимый эффект после срыва ПАРКУЕТСЯ и требует владельца",
                 parked_outcome.disposition == PARKED
                 and parked_outcome.effect_class == IRREVERSIBLE
                 and parked_outcome.reason == REASON_IRREVERSIBLE
                 and parked_outcome.requires_owner and parked.blocked,
                 f"{parked_outcome.disposition}/{parked_outcome.reason}")
    ctx.positive("восстановление НЕ объявляет состояние проверенным",
                 resume_point(irreversible_store, fx.OBJECTIVE_A).has_verified_state is False
                 and parked.condition == "UNKNOWN", parked.condition)

    # --- отрицательные контроли
    unknown_store, _s3, _t3, now3 = _crashed_objective(ctx, "неизвестная", "IDEMPOTENT_WRITE")
    ambiguous = recover(unknown_store, fx.OBJECTIVE_A, now=now3 + 100,
                        is_effect_applied=lambda reservation: UNKNOWN)
    ctx.negative("даже идемпотентный эффект при НЕИЗВЕСТНОМ исходе паркуется",
                 ambiguous.outcomes[0].disposition == PARKED
                 and ambiguous.outcomes[0].reason == REASON_AMBIGUOUS,
                 ambiguous.outcomes[0].reason)

    broken_store, _s4, _t4, now4 = _crashed_objective(ctx, "упавшая", "IDEMPOTENT_WRITE")

    def probe_fails(_reservation):
        raise RuntimeError("наблюдатель недоступен")

    crashed_probe = recover(broken_store, fx.OBJECTIVE_A, now=now4 + 100,
                            is_effect_applied=probe_fails)
    ctx.negative("упавшая проба отвечает «не знаю», а не «не применилось»",
                 crashed_probe.outcomes[0].answer == UNKNOWN
                 and crashed_probe.outcomes[0].disposition == PARKED,
                 crashed_probe.outcomes[0].reason)

    junk_store, _s5, _t5, now5 = _crashed_objective(ctx, "мусорная", "IDEMPOTENT_WRITE")
    junk = recover(junk_store, fx.OBJECTIVE_A, now=now5 + 100,
                   is_effect_applied=lambda reservation: "ВОЗМОЖНО_ПРИМЕНИЛОСЬ")
    ctx.negative("невнятный ответ пробы не считается ответом",
                 junk.outcomes[0].disposition == PARKED
                 and junk.outcomes[0].reason == REASON_BAD_ANSWER, junk.outcomes[0].reason)

    undeclared_store, _s6, _t6, now6 = _crashed_objective(ctx, "необъявленная", "НЕИЗВЕСТНЫЙ_ВИД")
    undeclared = recover(undeclared_store, fx.OBJECTIVE_A, now=now6 + 100,
                         is_effect_applied=lambda reservation: NOT_APPLIED)
    ctx.negative("НЕобъявленный класс эффекта считается необратимым, а не удобным",
                 undeclared.outcomes[0].effect_class == IRREVERSIBLE
                 and undeclared.outcomes[0].disposition == PARKED,
                 undeclared.outcomes[0].effect_class)

    applied_store, _s7, _t7, now7 = _crashed_objective(ctx, "приземлившаяся", "IRREVERSIBLE")
    landed = recover(applied_store, fx.OBJECTIVE_A, now=now7 + 100,
                     is_effect_applied=lambda reservation: APPLIED)
    ctx.negative("наблюдённый как ПРИМЕНЁННЫЙ эффект не отпускается на повтор",
                 landed.outcomes[0].disposition == COMMITTED
                 and landed.outcomes[0].requires_owner is False,
                 landed.outcomes[0].reason)
    ctx.negative("восстановление не крутится вечно: бюджет попыток терминален",
                 bounded_retry(3, budget=3).blocked is True
                 and bounded_retry(0, budget=3).allowed is True)
    ctx.refused("необсуждаемая проба обязательна: без неё восстановления нет",
                lambda: recover(applied_store, fx.OBJECTIVE_A, now=now7 + 200,
                                is_effect_applied="не функция"),
                ValueError)


# ------------------------------------------------------------------ 77
@scenario(id="OS-77", depth=PRODUCT_CONTRACTS)
def os77_schema_generation_is_named_not_guessed(ctx) -> None:
    """База новее сборки НАЗЫВАЕТСЯ отказом до первой записи, а не открывается.

    Откат сборки выглядит безопасным ровно до первой неаддитивной миграции:
    прежняя сборка открыла бы базу новой молча и работала бы с таблицами,
    которых не понимает. Узнал бы об этом владелец по своим данным.

    Измеряется `bcc/db.py:637` `Database.create_all` — единственный вход в базу
    продукта; отказ проверяется не только исключением, но и тем, что в файле
    после него НЕ СОЗДАНО НИ ОДНОЙ таблицы.
    """
    import sqlite3  # noqa: PLC0415

    import sqlalchemy as sa  # noqa: PLC0415

    from bcc.db import SCHEMA_GENERATION, Database, DatabaseFromNewerBuild  # noqa: PLC0415

    own = ctx.path("база", "своя.db")
    newer = ctx.path("база", "новее.db")
    legacy = ctx.path("база", "без-отметки.db")

    async def run() -> dict:
        out: dict = {}
        db = Database(f"sqlite+aiosqlite:///{own}")
        await db.create_all()
        out["stamp"] = await db._schema_generation()
        # Повторное открытие своей же базы должно проходить без возражений.
        await db.create_all()
        out["reopen_ok"] = True
        out["tables_own"] = await _table_count(db)
        await db.close()

        # База, записанная БОЛЕЕ НОВОЙ сборкой: только отметка, ни одной таблицы.
        ahead = Database(f"sqlite+aiosqlite:///{newer}")
        async with ahead.engine.begin() as conn:
            await conn.execute(sa.text(f"PRAGMA user_version = {SCHEMA_GENERATION + 5}"))
        try:
            await ahead.create_all()
            out["refused"] = None
        except DatabaseFromNewerBuild as exc:
            out["refused"] = str(exc)
        out["tables_after_refusal"] = await _table_count(ahead)
        out["stamp_after_refusal"] = await ahead._schema_generation()
        await ahead.close()

        # Старая база БЕЗ отметки не ломается: отметка лечит будущее, не прошлое.
        old = Database(f"sqlite+aiosqlite:///{legacy}")
        async with old.engine.begin() as conn:
            await conn.execute(sa.text("CREATE TABLE IF NOT EXISTS наследие (id INTEGER)"))
            await conn.execute(sa.text("PRAGMA user_version = 0"))
        await old.create_all()
        out["legacy_stamp"] = await old._schema_generation()
        out["legacy_kept"] = await _table_exists(old, "наследие")
        await old.close()
        return out

    async def _table_count(db) -> int:
        async with db.engine.begin() as conn:
            row = await conn.execute(sa.text(
                "SELECT count(*) FROM sqlite_master WHERE type='table'"))
            return int(row.scalar() or 0)

    async def _table_exists(db, name: str) -> bool:
        async with db.engine.begin() as conn:
            row = await conn.execute(sa.text(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name=:n"),
                {"n": name})
            return bool(row.scalar())

    got = asyncio.run(run())
    ctx.positive("после успешной миграции база несёт отметку поколения",
                 got["stamp"] == SCHEMA_GENERATION and SCHEMA_GENERATION >= 1,
                 f"отметка={got['stamp']}, сборка знает {SCHEMA_GENERATION}")
    ctx.positive("своя база открывается повторно без возражений",
                 got["reopen_ok"] and got["tables_own"] > 0,
                 f"таблиц={got['tables_own']}")
    ctx.positive("старая база БЕЗ отметки не ломается и получает отметку",
                 got["legacy_stamp"] == SCHEMA_GENERATION and got["legacy_kept"],
                 f"отметка={got['legacy_stamp']}, таблица владельца на месте={got['legacy_kept']}")
    ctx.positive("отказ НАЗЫВАЕТ оба числа и говорит владельцу, что делать",
                 got["refused"] is not None
                 and str(SCHEMA_GENERATION + 5) in got["refused"]
                 and str(SCHEMA_GENERATION) in got["refused"]
                 and "ROLLBACK.md" in got["refused"], (got["refused"] or "")[:160])

    # --- отрицательные контроли
    ctx.negative("база более новой сборки НЕ открывается молча",
                 got["refused"] is not None, "отказ не наступил" if not got["refused"] else "отказ")
    ctx.negative("отказ наступил ДО единой записи: ни одной таблицы не создано",
                 got["tables_after_refusal"] == 0,
                 f"таблиц после отказа={got['tables_after_refusal']}")
    ctx.negative("чужая, более новая отметка не перетёрта своей",
                 got["stamp_after_refusal"] == SCHEMA_GENERATION + 5,
                 f"отметка={got['stamp_after_refusal']}")
    raw = sqlite3.connect(newer)
    try:
        version = raw.execute("PRAGMA user_version").fetchone()[0]
    finally:
        raw.close()
    ctx.negative("чтение файла МИМО продукта подтверждает ту же отметку",
                 int(version) == SCHEMA_GENERATION + 5, f"user_version={version}")
    ctx.negative("отметка поколения объявлена продуктом, а не выдумана сценарием",
                 isinstance(SCHEMA_GENERATION, int) and not isinstance(SCHEMA_GENERATION, bool))


# ------------------------------------------------------------------ 78
@scenario(id="OS-78", depth=PRODUCT_CONTRACTS)
def os78_rollback_rehearsal_reads_and_changes_nothing(ctx) -> None:
    """Репетиция отката на НАСТОЯЩИХ данных: план как данные, мир не тронут.

    Репетиция и есть смысл: порядок отката обязан быть осмотрен и сверен ДО
    того, как кому-то разрешат его выполнить. Доказательство «ничего не
    исполнено» здесь не в словах плана, а в том, что снимок совпал с ЧТЕНИЕМ
    хранилища, версия цели не сдвинулась, бронь осталась открытой, и после
    репетиции продукт продолжает работать.
    """
    store, spec, target, now = _crashed_objective(ctx, "репетиция", "IDEMPOTENT_WRITE")
    before = resume_point(store, fx.OBJECTIVE_A)
    plan = prepare_rollback(store, fx.OBJECTIVE_A, now=now + 5)
    after = resume_point(store, fx.OBJECTIVE_A)
    snapshot = plan.snapshot()

    ctx.positive("порядок отката выдан ДАННЫМИ, которые можно осмотреть",
                 [step.action for step in plan.steps] == [a for a, _why in ROLLBACK_ORDER]
                 and all(step.why for step in plan.steps)
                 and [step.order for step in plan.steps] == list(range(1, len(ROLLBACK_ORDER) + 1)),
                 str([step.action for step in plan.steps]))
    ctx.positive("наблюдатели останавливаются ПЕРВЫМИ, а снимок состояния — последним",
                 plan.steps[0].action == "pause_observers"
                 and plan.steps[-1].action == "snapshot_objective_state")
    ctx.positive("снимок сверен ЧТЕНИЕМ хранилища, а не словами плана",
                 snapshot["version"] == after.version
                 and snapshot["open_reservations"] == list(after.open_reservations)
                 and snapshot["condition"] == after.condition
                 and snapshot["lifecycle"] == after.lifecycle
                 and snapshot["observations_used"] == after.observations_used,
                 f"версия={snapshot['version']} броней={len(after.open_reservations)}")
    ctx.positive("репетиция идёт по НАСТОЯЩЕЙ работе в полёте, а не по пустому месту",
                 len(after.open_reservations) == 1 and bool(plan.open_reservations),
                 f"броней в полёте={len(after.open_reservations)}")
    ctx.positive("после репетиции продукт продолжает работать",
                 fx.propose(store, spec, target=target, now=now + 300,
                            observation_id="obs-после-репетиции", observed_at=now + 290,
                            effects=(fx.effect(target, kind="IDEMPOTENT_WRITE"),)) is not None)

    # --- отрицательные контроли
    ctx.negative("репетиция НЕ объявляет себя исполненной",
                 plan.executed is False)
    ctx.negative("репетиция ничего не изменила: состояние до и после совпало",
                 before == after,
                 f"версия до={before.version}, после={after.version}")
    ctx.negative("репетиция не закрыла бронь в полёте",
                 after.open_reservations == before.open_reservations
                 and len(after.open_reservations) == 1)
    ctx.negative("репетиция не изготовила проверенное состояние",
                 after.has_verified_state is False and after.condition == "UNKNOWN",
                 after.condition)
    ctx.refused("плана для несуществующей цели не бывает",
                lambda: prepare_rollback(store, "objective:такой-нет", now=now + 5),
                ObjectiveStoreError)
    second = prepare_rollback(store, fx.OBJECTIVE_A, now=now + 6)
    ctx.negative("повторная репетиция тоже ничего не двигает",
                 second.snapshot()["version"] == snapshot["version"]
                 and resume_point(store, fx.OBJECTIVE_A).version == after.version)


# ------------------------------------------------------------------ 79
@scenario(id="OS-79", depth=PRODUCT_CONTRACTS)
def os79_certification_refuses_a_missing_required_run(ctx) -> None:
    """Отсутствующий обязательный прогон — ЛОВУШКА: он не окрашен никак.

    Красный прогон виден. Отсутствующий не виден ничем: в ответе GitHub его
    просто нет, и наивная сводка читает это как «замечаний нет». Поэтому здесь
    отсутствие проверяется ОТДЕЛЬНО от «красных» исходов, и отдельно
    проверяется, что имена обязательных заданий совпадают с файлами заданий
    ЭТОЙ ветки: переименование задания иначе тихо выключило бы требование.
    """
    from exact_sha_certify import (CERTIFIED, DEFAULT_REQUIRED,  # noqa: PLC0415
                                   INSUFFICIENT, NOT_CERTIFIED, CertificationError,
                                   certify, check_scorecard)

    sha = "a" * 40
    other = "b" * 40

    def run(name: str, *, head=sha, status="completed", conclusion="success",
            number=1, attempt=1, ident=1) -> dict:
        return {"name": name, "head_sha": head, "status": status, "conclusion": conclusion,
                "run_number": number, "run_attempt": attempt, "id": ident}

    green = [run(name, ident=i) for i, name in enumerate(DEFAULT_REQUIRED, 1)]
    certified = certify(sha, green)
    ctx.positive("все обязательные задания зелены на ЭТОМ коммите — сертификат выдан",
                 certified["verdict"] == CERTIFIED and certified["final"] is True,
                 certified["reason"])

    absent_name = "One-download Windows application"
    without = [row for row in green if row["name"] != absent_name]
    missing = certify(sha, without)
    ctx.positive("ОТСУТСТВУЮЩИЙ обязательный прогон назван MISSING, а не пропущен",
                 missing["workflows"][absent_name]["result"] == "MISSING"
                 and absent_name in missing["reason"],
                 missing["workflows"][absent_name]["reason"])
    ctx.positive("имена обязательных заданий совпадают с файлами заданий этой ветки",
                 not _workflows_absent(DEFAULT_REQUIRED),
                 f"обязательных={len(DEFAULT_REQUIRED)}, без файла={_workflows_absent(DEFAULT_REQUIRED)}")
    ctx.positive("повторный прогон вытесняет прежнюю попытку ТОГО ЖЕ коммита",
                 certify(sha, [row for row in green if row["name"] != "Command Center CI"] + [
                     run("Command Center CI", conclusion="failure", attempt=1, ident=90),
                     run("Command Center CI", conclusion="success", attempt=2, ident=91),
                 ])["verdict"] == CERTIFIED)

    # --- отрицательные контроли
    ctx.negative("отсутствие обязательного прогона НЕ читается как «замечаний нет»",
                 missing["verdict"] == NOT_CERTIFIED, missing["verdict"])
    not_pass = {}
    for conclusion in ("skipped", "cancelled", "neutral", "timed_out", "stale",
                       "failure", "action_required", "startup_failure", ""):
        rows = [row for row in green if row["name"] != "Bossman Core CI"]
        rows.append(run("Bossman Core CI", conclusion=conclusion, ident=99))
        report = certify(sha, rows)
        not_pass[conclusion or "пусто"] = (report["verdict"],
                                           report["workflows"]["Bossman Core CI"]["result"])
    ctx.negative("skipped, cancelled и прочие незелёные исходы не равны PASS",
                 all(verdict == NOT_CERTIFIED and result == "NOT_PASS"
                     for verdict, result in not_pass.values()), str(sorted(not_pass)))
    ctx.negative("зелёное на ДРУГОМ коммите не переносится на этот",
                 certify(sha, [run(name, head=other, ident=i)
                               for i, name in enumerate(DEFAULT_REQUIRED, 1)]
                         )["verdict"] == NOT_CERTIFIED)
    unfinished = [row for row in green if row["name"] != "ASTRA acceptance"]
    unfinished.append(run("ASTRA acceptance", status="in_progress", conclusion="", ident=77))
    ctx.negative("незавершённый прогон не сертифицирует и помечает SHA как нефинальный",
                 certify(sha, unfinished)["verdict"] == NOT_CERTIFIED
                 and certify(sha, unfinished)["final"] is False)
    ctx.negative("пустая страница прогонов — недостаток улик, а не сертификат",
                 certify(sha, [])["verdict"] == INSUFFICIENT)
    ctx.refused("пустой набор требований не сертифицирует ничего",
                lambda: certify(sha, green, ()), CertificationError)
    ctx.refused("сокращённый SHA сертифицировать нельзя",
                lambda: certify("a" * 7, green), CertificationError)
    ctx.negative("заявка табло PASS для НЕсертифицированного SHA опровергается",
                 bool(check_scorecard(missing, {"exact_sha_ci": "PASS",
                                                "last_evidence_sha": sha}))
                 and bool(check_scorecard(certified, {"exact_sha_ci": "PASS",
                                                      "last_evidence_sha": other}))
                 and check_scorecard(certified, {"exact_sha_ci": "PASS",
                                                 "last_evidence_sha": sha}) == "")


def _workflows_absent(required) -> list[str]:
    """Обязательные задания, у которых в этой ветке НЕТ файла с таким `name:`."""
    names: set[str] = set()
    folder = REPO_ROOT / ".github" / "workflows"
    for path in sorted(folder.glob("*.yml")):
        found = re.search(r"^name:\s*(.+?)\s*$", path.read_text(encoding="utf-8"), re.M)
        if found:
            names.add(found.group(1))
    return [name for name in required if name not in names]


# ------------------------------------------------------------------ 80
@scenario(id="OS-80", depth=INSTALLED_PRODUCT)
def os80_real_rollback_restores_a_working_product(ctx) -> None:
    """Откат данных владельца: молча — никогда; выполнен — проверен чтением.

    Цепочка идёт через НАСТОЯЩИЙ контейнер служб Command Center (`bcc.api.Services`
    — тот самый объект, который `bcc/api.py:452` кладёт в `app.state.svc`) и через
    собственные обработчики продукта `bcc/features/snapshot.py`. Итог проверяется
    дважды: продуктом (`Database.ping`, чтение строк) и НЕЗАВИСИМО — чтением
    файла базы стандартным `sqlite3`, мимо продукта.

    Отрицательные контроли стоят ПЕРВЫМИ: успешный откат потребляет одноразовое
    подтверждение, и после него «без подтверждения нельзя» доказывалось бы
    потреблённым подтверждением, а не проверкой.
    """
    import shutil  # noqa: PLC0415
    import sqlite3  # noqa: PLC0415

    import sqlalchemy as sa  # noqa: PLC0415
    from fastapi import HTTPException  # noqa: PLC0415

    from bcc.api import Services  # noqa: PLC0415
    from bcc.config import Settings  # noqa: PLC0415
    from bcc.db import fetch_one, models as models_t, providers as providers_t  # noqa: PLC0415
    from bcc.db import snapshots as snapshots_t, utcnow  # noqa: PLC0415
    from bcc.features import snapshot as snap  # noqa: PLC0415

    ctx.reached_installed_product(
        "bcc.api.Services и bcc.features.snapshot установленного Command Center этой ветки")
    data_dir = ctx.path("данные", "держатель").parent
    data_dir.mkdir(parents=True, exist_ok=True)
    db_file = data_dir / "bcc.db"

    class Call:
        """Запрос продукта: ровно то, что читают его обработчики."""

        def __init__(self, svc, body: dict | None = None) -> None:
            state = type("Состояние", (), {"svc": svc})()
            self.app = type("Приложение", (), {"state": state})()
            self._body = body

        async def json(self) -> dict:
            if self._body is None:
                raise ValueError("тела нет")
            return self._body

    async def run() -> dict:
        settings = Settings(data_dir=data_dir,
                            database_url=f"sqlite+aiosqlite:///{db_file}")
        svc = Services(settings, start_workers=False, announce_token=False)
        await svc.db.create_all()
        async with svc.db.session() as session:
            pid = int((await session.execute(sa.insert(providers_t).values(
                name="местный", kind="openai_compat",
                base_url="http://127.0.0.1:11434/v1",
                created_at=utcnow()))).inserted_primary_key[0])
            await session.execute(sa.insert(models_t).values(
                provider_id=pid, name="qwen", alias="состояние-владельца", kind="local"))
            await session.commit()

        made = await snap.create_snapshot(Call(svc, {"name": "перед обновлением"}))
        snapshot_id = int(made["snapshot"]["id"])
        out: dict = {"digest": ((made["snapshot"].get("manifest") or {})
                                .get("database") or {}).get("sha256", "")}

        # Обновление испортило состояние владельца.
        async with svc.db.session() as session:
            await session.execute(sa.update(models_t).values(alias="испорчено-обновлением"))
            await session.commit()
        out["broken"] = await _aliases(svc)

        # 1. ОТРИЦАТЕЛЬНЫЙ: без подтверждения откат не выполняется.
        try:
            await snap.restore(snapshot_id, Call(svc, {}))
            out["no_approval"] = None
        except HTTPException as exc:
            out["no_approval"] = (exc.status_code, dict(exc.detail))
        approval_id = int((out["no_approval"] or (0, {}))[1].get("approval_id") or 0)
        out["after_no_approval"] = await _aliases(svc)

        # 2. ОТРИЦАТЕЛЬНЫЙ: неодобренное подтверждение откат не открывает.
        try:
            await snap.restore(snapshot_id, Call(svc, {"approval_id": approval_id}))
            out["pending"] = None
        except HTTPException as exc:
            out["pending"] = (exc.status_code, str(exc.detail.get("message")))
        out["after_pending"] = await _aliases(svc)
        if out["pending"] is None:
            # Откат по НЕодобренному подтверждению прошёл. Дальше идти некуда:
            # база уже подменена, подтверждение владельца исчезло вместе с ней, и
            # любой следующий шаг упал бы вместо того, чтобы НАЗВАТЬ этот дефект.
            await svc.db.close()
            return out

        await svc.approvals.decide(approval_id, "approved")

        # 3. ОТРИЦАТЕЛЬНЫЙ: повреждённый артефакт отвергается даже с одобрением.
        async with svc.db.session() as session:
            row = await fetch_one(session, snapshots_t, snapshot_id)
        artifact = Path(row["path"]) / snap.DB_FILE
        keep = artifact.with_suffix(".целый")
        shutil.copy2(artifact, keep)
        artifact.write_bytes(b"not a database")
        try:
            await snap.restore(snapshot_id, Call(svc, {"approval_id": approval_id}))
            out["corrupt"] = None
        except HTTPException as exc:
            out["corrupt"] = (exc.status_code, str(exc.detail.get("message")))
        out["after_corrupt"] = await _aliases(svc)
        shutil.move(str(keep), str(artifact))

        # 4. ПОЛОЖИТЕЛЬНЫЙ: одобренный откат выполняется.
        result = await snap.restore(snapshot_id, Call(svc, {"approval_id": approval_id,
                                                            "by": "владелец"}))
        out["restored"] = bool(result.get("ok"))
        out["safety_copy_exists"] = Path(result["safety_copy"]).is_file()
        out["after_restore"] = await _aliases(svc)
        out["ping"] = await svc.db.ping()
        out["registry_kept"] = len((await snap.list_snapshots(Call(svc)))["snapshots"])

        # 5. ОТРИЦАТЕЛЬНЫЙ: то же подтверждение второй раз не работает.
        try:
            await snap.restore(snapshot_id, Call(svc, {"approval_id": approval_id}))
            out["replay"] = None
        except HTTPException as exc:
            out["replay"] = (exc.status_code, str(exc.detail.get("message")))
        out["after_replay"] = await _aliases(svc)
        await svc.db.close()
        return out

    async def _aliases(svc) -> list[str]:
        import sqlalchemy as _sa  # noqa: PLC0415

        from bcc.db import models as _models  # noqa: PLC0415
        async with svc.db.session() as session:
            return [row[0] for row in
                    (await session.execute(_sa.select(_models.c.alias))).fetchall()]

    got = asyncio.run(run())

    # --- отрицательные контроли ПЕРВЫМИ: откат потребляет подтверждение
    ctx.negative("откат без подтверждения НЕ выполняется, а заводит подтверждение",
                 got["no_approval"] is not None and got["no_approval"][0] == 202
                 and int(got["no_approval"][1].get("approval_id") or 0) > 0,
                 str(got["no_approval"][0] if got["no_approval"] else "выполнился"))
    ctx.negative("состояние владельца после отказа не тронуто",
                 got["after_no_approval"] == got["broken"] == ["испорчено-обновлением"],
                 str(got["after_no_approval"]))
    ctx.negative("неодобренное подтверждение откат не открывает",
                 got["pending"] is not None and got["pending"][0] == 403
                 and got["after_pending"] == ["испорчено-обновлением"],
                 str(got["pending"]))
    ctx.negative("повреждённый артефакт отвергается ДО подмены базы",
                 got.get("corrupt") is not None and got["corrupt"][0] == 409
                 and got.get("after_corrupt") == ["испорчено-обновлением"],
                 str(got.get("corrupt")))
    ctx.negative("одно подтверждение — один откат: переигрывание отвергнуто",
                 got.get("replay") is not None and got["replay"][0] == 403
                 and got.get("after_replay") == ["состояние-владельца"],
                 str(got.get("replay")))

    # --- положительная половина
    ctx.positive("одобренный откат выполнен и оставил страховочную копию",
                 bool(got.get("restored")) and bool(got.get("safety_copy_exists")))
    ctx.positive("продукт вернулся в рабочее состояние — проверено ЧТЕНИЕМ его же базы",
                 got.get("after_restore") == ["состояние-владельца"] and got.get("ping") is True,
                 str(got.get("after_restore")))
    ctx.positive("реестр точек отката пережил откат — возвращаться есть куда",
                 int(got.get("registry_kept") or 0) >= 1, f"точек={got.get('registry_kept')}")
    ctx.positive("снимок нёс контрольную сумму базы, а не обещание",
                 len(got["digest"]) == 64, got["digest"][:16])

    # Независимое наблюдение: читаем файл базы МИМО продукта.
    raw = sqlite3.connect(db_file)
    try:
        rows = [row[0] for row in raw.execute("SELECT alias FROM models").fetchall()]
    finally:
        raw.close()
    ctx.positive("чтение файла базы мимо продукта видит откаченное состояние",
                 rows == ["состояние-владельца"], str(rows))
