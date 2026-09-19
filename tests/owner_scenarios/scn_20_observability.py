"""Владельческие сценарии 86–90: наблюдаемость — журнал, метрики, здоровье.

Пять вопросов, на которые нельзя отвечать «посмотри в логи»:

* 86 — журнал владельца отвечает на «что вообще произошло»: сводка за период
  читается, и ни одна её строка не несёт ключа;
* 87 — ошибка, которую владелец увидел НА ЭКРАНЕ, находится в журнале по её же
  признаку, и находится она в ВЫРОСШЕМ журнале тоже (BL-108);
* 88 — метрики и здоровье показывают ОТКАЗ отказом: неизмеренное отвечает
  «не измерено», а не нулём и не «ok»;
* 89 — отключённая подсистема НАЗВАНА отключённой и говорит, чем её включить, а
  не молча отсутствует в ответе;
* 90 — журнал ограничен по возрасту и по числу строк, и обрезка уносит СТАРОЕ,
  оставляя последнюю улику и след самой обрезки.

ПОРЯДОК ПОЛОВИН. В 89 положительная половина идёт ПОСЛЕ отрицательной: флаг
подсистемы включается последним действием сценария, и «выключенная отказывает»
пришлось бы доказывать уже включённой подсистемой.

ЧТО ЗДЕСЬ НЕ ЗАПУСКАЕТСЯ. `POST /api/testing/publish` коммитит журнал в git
рабочего дерева. Сценарий не имеет права трогать чужую ветку, поэтому
публикация не вызывается; её чистка (`testing_period.scrub`) меряется прямо на
тексте журнала — это та же функция, через которую проходит каждая строка перед
уходом наружу.

ЖИВОГО ШАГА МОДЕЛИ НЕТ: все пять объявлены `model_step: "none"`.

Зависимости модуля — стандартная библиотека и каркас. Всё, что тянет
sqlalchemy/fastapi (`bcc.*`), импортируется ВНУТРИ функций и объявлено в реестре
как `command_center`: BL-085 — корневой CI ставит только
pytest/pytest-timeout/psutil/httpx/pyyaml.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "command-center"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scenario_runner import INSTALLED_PRODUCT, scenario  # noqa: E402

TESTING_FLAG = "BOSSMAN_TESTING_PERIOD"
DIAG_FLAG = "BOSSMAN_DIAG_BUNDLE_ENABLED"


# ------------------------------------------------------------------ оснастка
@contextlib.contextmanager
def _env(**pairs: str | None):
    """Временное окружение: раннер один на все сценарии, следов не оставляем."""
    before = {name: os.environ.get(name) for name in pairs}
    try:
        for name, value in pairs.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        yield
    finally:
        for name, value in before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@contextlib.asynccontextmanager
async def _product(ctx, *, workers: bool = False):
    """Тот же Bossman, что открывает владелец: та же фабрика, тот же токен."""
    import httpx  # noqa: PLC0415

    from bcc.api import create_app  # noqa: PLC0415
    from bcc.auth import HEADER  # noqa: PLC0415
    from bcc.config import Settings  # noqa: PLC0415

    data = ctx.path("установка", "data")
    settings = Settings(data_dir=data, database_url=f"sqlite+aiosqlite:///{data / 'bcc.db'}",
                        ui_dir=ctx.path("установка", "нет-ui"))
    app = create_app(settings, announce_token=False, start_workers=workers)
    svc = app.state.svc
    await svc.start()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://bossman.local",
                                     headers={HEADER: svc.auth.token}) as client:
            yield svc, client
    finally:
        await svc.stop()


# ------------------------------------------------------------------ 86
@scenario(id="OS-86", depth=INSTALLED_PRODUCT)
def os86_owner_journal_answers_what_happened(ctx) -> None:
    """Сводка за период читается и не несёт ключей.

    Журнал, который нельзя прочитать целиком, отвечает на вопрос «что вообще
    произошло» не лучше, чем его отсутствие. Поэтому меряется не факт записи, а
    ТРИ свойства сразу: период назван (сессия, начало, размер), содержимое
    разложено по видам и источникам, и ни в одной строке нет ключа.

    Чистка считает вычищенные места вслух: молчаливая чистка неотличима от
    неработающей, и владельцу пришлось бы верить на слово.
    """
    async def run() -> None:
        from bcc.features import testing_period as tp  # noqa: PLC0415

        with _env(**{TESTING_FLAG: "1"}):
            async with _product(ctx) as (svc, client):
                await client.get("/api/activity")
                await client.get("/api/tasks/999999")          # ошибка владельца — тоже событие
                await svc.bus.emit("owner.step", note="владелец нажал кнопку")

                status = (await client.get("/api/testing/status")).json()
                ctx.positive("журнал назван: сессия, начало, размер и разбивка по видам",
                             status["enabled"] is True and bool(status["session"])
                             and bool(status["started_at"]) and status["bytes"] > 0
                             and len(status["by_kind"]) >= 2,
                             f"сессия {status['session']}, {status['bytes']} байт, "
                             f"виды: {sorted(status['by_kind'])}")

                events = (await client.get("/api/testing/events?limit=500")).json()["events"]
                summary = tp._summary(events)
                ctx.positive("сводка за период отвечает «что произошло», а не «сколько строк»",
                             summary["total"] == len(events) and summary["total"] > 0
                             and summary["by_source"].get("server", 0) > 0
                             and "session.start" in summary["by_kind"],
                             f"всего {summary['total']}, источники {dict(summary['by_source'])}")
                ctx.positive("ошибки выделены отдельно, а не растворены в общем потоке",
                             summary["error_count"] >= 1
                             and any("error" in str(e.get("kind")) for e in summary["errors"]),
                             f"ошибок {summary['error_count']}")

                report = tp._render_report(events, summary, status["session"])
                ctx.positive("отчёт за период читается человеком, а не разбирается по json",
                             "## Что происходило" in report and "## Источники" in report
                             and "## Ошибки" in report and f"записей {summary['total']}" in report,
                             f"{len(report.splitlines())} строк отчёта")
                ctx.reached_installed_product("bcc.api.create_app + /api/testing/* этой ветки")

                # --- отрицательные контроли
                token = svc.auth.token
                await svc.testing_log.write("ui", "ui.click", {"token": token, "где": "кнопка"})
                raw = svc.testing_log.path.read_text(encoding="utf-8")
                ctx.negative("поле с именем ключа в журнал не пишется вовсе",
                             '"token": "[не записывается]"' in raw
                             or '"token":"[не записывается]"' in raw,
                             "значение поля token заменено пометкой")

                dirty = raw + f"\nобращение с ключом {token} в тексте\n"
                clean, hits = tp.scrub(dirty, {token})
                ctx.negative("ключ не уходит наружу и внутри ЗНАЧЕНИЯ, а не только в имени поля",
                             token not in clean and hits >= 1,
                             f"вычищено мест: {hits}")
                ctx.negative("отчёт за период ключа не несёт",
                             token not in report and token not in json.dumps(summary,
                                                                             ensure_ascii=False),
                             "значение токена в отчёте и сводке не найдено")
                ctx.negative("сводка пустого периода не выдумывает работу",
                             tp._summary([]) == {"total": 0, "by_kind": {}, "by_source": {},
                                                 "errors": [], "error_count": 0},
                             "пустой период = нули, а не «всё хорошо»")

    asyncio.run(run())


# ------------------------------------------------------------------ 87
@scenario(id="OS-87", depth=INSTALLED_PRODUCT)
def os87_screen_error_is_findable_in_the_journal(ctx) -> None:
    """Ошибка с экрана находится в журнале по её же признаку (BL-108).

    «Посмотри в логи» — пустой совет, если из увиденного на экране нет пути к
    строке журнала. Признаков здесь два, и оба меряются: путь+код отказавшего
    запроса и `trace_id` действия, по которому продукт отдаёт всю цепочку
    ручкой `/api/observability/trace/{trace_id}`.

    BL-108 измерен здесь же: цепочка ищется в журнале, ПЕРЕРОСШЕМ прежнее окно
    поиска в 5000 строк. До правки отбирались 5000 самых СТАРЫХ строк с
    фильтрацией в памяти, и свежая цепочка отвечала пустым списком — тем же,
    что и несуществующее действие.
    """
    async def run() -> None:
        import sqlalchemy as sa  # noqa: PLC0415

        from bcc.db import events as events_t, utcnow  # noqa: PLC0415
        from bcc.trace import run_trace_id, trace  # noqa: PLC0415

        with _env(**{TESTING_FLAG: "1"}):
            async with _product(ctx) as (svc, client):
                seen = await client.get("/api/tasks/424242")
                shown = seen.json()["error"]["message"]
                ctx.positive("на экране владельца — сообщение и код, а не трасса",
                             seen.status_code == 404 and bool(shown)
                             and "Traceback" not in seen.text, f"HTTP 404: {shown}")

                events = (await client.get("/api/testing/events?limit=500")).json()["events"]
                hit = [e for e in events if e["kind"] == "http.error"
                       and e["data"].get("path") == "/api/tasks/424242"
                       and e["data"].get("status") == 404]
                ctx.positive("та же ошибка лежит в журнале с тем же путём и тем же кодом",
                             len(hit) == 1 and hit[0]["source"] == "server",
                             f"найдено записей: {len(hit)}")

                tid = run_trace_id(4242)
                with trace(tid):
                    await svc.bus.emit("action.started", step="взять файл")
                    await svc.bus.emit("action.result", ok=False, reason="цель пропала")
                chain = (await client.get(f"/api/observability/trace/{tid}")).json()
                ctx.positive("по признаку действия продукт отдаёт ЦЕПОЧКУ, а не одну строку",
                             [e["kind"] for e in chain["events"]] == ["action.started",
                                                                      "action.result"],
                             f"{len(chain['events'])} событий по {tid}")

                # Журнал перерастает прежнее окно поиска (BL-108).
                async with svc.db.session() as session:
                    await session.execute(sa.insert(events_t),
                                          [{"kind": "noise", "ts": utcnow(), "data": {"i": i}}
                                           for i in range(5200)])
                    await session.commit()
                grown = run_trace_id(4243)
                with trace(grown):
                    await svc.bus.emit("action.started", step="после роста журнала")
                    await svc.bus.emit("action.result", ok=True)
                found = (await client.get(f"/api/observability/trace/{grown}")).json()
                async with svc.db.session() as session:
                    rows = int((await session.execute(
                        sa.select(sa.func.count()).select_from(events_t))).scalar())
                ctx.positive("в ВЫРОСШЕМ журнале свежая цепочка по-прежнему находится (BL-108)",
                             [e["kind"] for e in found["events"]] == ["action.started",
                                                                      "action.result"],
                             f"строк в журнале {rows}, найдено {len(found['events'])}")
                ctx.reached_installed_product("bcc.api.create_app + /api/observability/trace")

                # --- отрицательные контроли
                missing = (await client.get("/api/observability/trace/run-999999")).json()
                ctx.negative("несуществующее действие даёт пусто — и пусто теперь значит «нет»",
                             missing["events"] == [], "цепочки нет")
                mixed = [e for e in found["events"] if e["data"].get("trace_id") != grown]
                ctx.negative("в цепочку не попадают чужие события",
                             not mixed and all(e["data"].get("trace_id") == grown
                                               for e in found["events"]),
                             f"чужих событий: {len(mixed)}")
                ctx.negative("событие без признака действия не приписывается чужой цепочке",
                             all(e["kind"] != "noise" for e in found["events"]),
                             "шум в цепочку не попал")
                ctx.negative("запись в журнале не несёт ни тела запроса, ни токена",
                             svc.auth.token not in json.dumps(hit, ensure_ascii=False)
                             and "authorization" not in json.dumps(hit, ensure_ascii=False).lower(),
                             "ни токена, ни заголовков авторизации")

    asyncio.run(run())


# ------------------------------------------------------------------ 88
@scenario(id="OS-88", depth=INSTALLED_PRODUCT)
def os88_failure_reads_as_failure_not_zero(ctx) -> None:
    """Метрики и здоровье показывают отказ отказом, а не нулём.

    Ноль — это измерение. «Не измерено» — не ноль, и подменять одно другим
    опаснее, чем не показывать ничего: владелец принимает решение по цифре,
    которой не было. Здесь меряются оба конца: сырой разбор показаний железа и
    сводный ответ `/health`, который обязан называть состояние словом.
    """
    async def run() -> None:
        from bcc import metrics as m  # noqa: PLC0415
        from bcc.health import snapshot  # noqa: PLC0415
        from bcc.lifecycle import StartupTrace  # noqa: PLC0415

        async with _product(ctx) as (svc, client):
            system = (await client.get("/api/system")).json()
            health = system["health"]
            ctx.positive("здоровье компонента — НАЗВАННОЕ состояние с деталью, а не число",
                         all(isinstance(entry.get("status"), str) and entry["status"]
                             for entry in health.values())
                         and health["db"]["status"] == "ok",
                         f"{len(health)} компонентов, db={health['db']['status']}")
            ctx.positive("непроверенная подсистема отвечает «не знаю», а не «ok»",
                         health["browser"]["status"] in {"unknown", "offline"}
                         and bool(health["browser"]["detail"]),
                         f"browser={health['browser']['status']}: {health['browser']['detail'][:60]}")
            ctx.positive("ненастроенное отвечает «не настроено», а не нулём доступных моделей",
                         health["models"]["status"] == "empty"
                         and health["models"]["available"] == 0,
                         f"models={health['models']['status']}, available="
                         f"{health['models']['available']}")

            public = (await client.get("/health")).json()
            ctx.positive("сводный ответ не зеленеет, пока хоть одна подсистема не доказана",
                         public["ready"] is False and public["status"] in {"DEGRADED", "UNHEALTHY"}
                         and public["alive"] is True,
                         f"{public['status']}, ready={public['ready']}")

            reading = svc.metrics.read()
            ctx.positive("показания железа приходят измеренными числами",
                         isinstance(reading["cpu_pct"], (int, float))
                         and reading["ram_total_mb"] > 0,
                         f"cpu={reading['cpu_pct']}, ram={reading['ram_total_mb']} МБ")
            ctx.reached_installed_product("bcc.api.create_app + /api/system и /health этой ветки")

            # --- отрицательные контроли
            ctx.negative("нечисло от железа не превращается в ноль",
                         m._num("нечисло") is None and m._num("") is None
                         and m._num(None) is None, "мусор → None")
            ctx.negative("бесконечность, NaN и «поле недоступно» не становятся измерением",
                         m._num("nan") is None and m._num("inf") is None
                         and m._num("-5") is None and m._num("[N/A]") is None,
                         "nan/inf/отрицательное/[N/A] → None")
            blind = m.MetricsSampler(svc.db, svc.bus, disk_path="/нет-такого-каталога")
            no_disk = blind.read()
            ctx.negative("недоступный диск отвечает «не измерено», а не нулём занятого места",
                         no_disk["disk_used_gb"] is None and no_disk["disk_total_gb"] is None,
                         "disk_used_gb=None, disk_total_gb=None")
            gpu = m.gpu_info()
            ctx.negative("отсутствие карты — None, а не пустой список и не нулевая загрузка",
                         gpu is None or (isinstance(gpu, list) and len(gpu) > 0 and all(
                             card.get("vram_procs_mb") is None
                             or isinstance(card.get("vram_procs_mb"), (int, float))
                             for card in gpu)),
                         "нет карты (None)" if gpu is None else f"{len(gpu)} карт с измеренными полями")
            ctx.negative("незавершённый старт не отчитывается нулём миллисекунд",
                         StartupTrace().to_dict()["total_ms"] is None
                         and StartupTrace().to_dict()["ready"] is False,
                         "total_ms=None, ready=False")
            broken = snapshot(svc, {**{k: dict(v) for k, v in health.items()},
                                    "db": {"status": "error", "detail": "база недоступна"}},
                              public=True)
            ctx.negative("сломанная база делает сводный ответ UNHEALTHY, а не DEGRADED",
                         broken["status"] == "UNHEALTHY" and broken["ready"] is False
                         and broken["components"]["db"]["status"] == "UNHEALTHY",
                         f"{broken['status']}, db={broken['components']['db']['status']}")

    asyncio.run(run())


# ------------------------------------------------------------------ 89
@scenario(id="OS-89", depth=INSTALLED_PRODUCT)
def os89_disabled_subsystem_says_it_is_disabled(ctx) -> None:
    """Отключённая подсистема названа отключённой, а не тихо отсутствует.

    Разница видна владельцу: «этого у меня нет» и «это выключено вот этим
    флагом» ведут к разным действиям, а молчание — ни к какому. Поэтому
    проверяется не только отказ, но и то, что выключенная подсистема ОТВЕЧАЕТ:
    называет флаг, показывает, что будет собрано, и не исчезает из ответа
    здоровья.

    ОТРИЦАТЕЛЬНАЯ ПОЛОВИНА ИДЁТ ПЕРВОЙ. Включение флага — последнее действие
    сценария: после него «выключенная отказывает» доказывалось бы уже
    включённой подсистемой.
    """
    async def run() -> None:
        with _env(**{DIAG_FLAG: None, TESTING_FLAG: "1"}):
            async with _product(ctx) as (svc, client):
                data = Path(svc.settings.data_dir)

                # --- отрицательные контроли: подсистема выключена
                refused = await client.post("/api/diag/bundle")
                body = refused.json()["error"]
                ctx.negative("выключенная подсистема отказывает и НАЗЫВАЕТ свой флаг",
                             refused.status_code == 409 and DIAG_FLAG in body["message"]
                             and DIAG_FLAG in body.get("hint", ""),
                             f"HTTP {refused.status_code}: {body['message']}")
                ctx.negative("отказ не оставляет следов: ни одного файла не создано",
                             not (data / "diag").exists(),
                             "каталога сбора нет")

                health = (await client.get("/api/system")).json()["health"]
                stopped = {name: entry for name, entry in health.items()
                           if entry["status"] == "stopped"}
                ctx.negative("отключённые фоновые циклы не исчезают из ответа здоровья",
                             len(stopped) >= 3
                             and all(entry["detail"] for entry in stopped.values()),
                             f"названо отключёнными: {len(stopped)}, например "
                             f"{sorted(stopped)[:3]}")
                ctx.negative("отключённый цикл не выдаётся за работающий",
                             all(entry["status"] != "ok" for entry in stopped.values()),
                             "ни один из них не «ok»")

                # --- положительные: выключенная подсистема всё равно отвечает
                preview = (await client.get("/api/diag/bundle")).json()
                ctx.positive("выключенная подсистема отвечает и называет себя выключенной",
                             preview["enabled"] is False and preview["flag"] == DIAG_FLAG,
                             f"enabled={preview['enabled']}, флаг={preview['flag']}")
                ctx.positive("владелец видит заранее, ЧТО попадёт в сбор и чего в нём не будет",
                             len(preview["will_include"]) >= 4 and len(preview["will_exclude"]) >= 4
                             and all(item.get("reason") for item in preview["will_exclude"]),
                             f"{len(preview['will_include'])} частей, "
                             f"{len(preview['will_exclude'])} исключений с причинами")

                fi = (await client.get("/api/file-intelligence/status")).json()
                ctx.positive("вторая выключенная подсистема тоже названа, а не отсутствует",
                             fi["enabled"] is False and bool(fi["flag"])
                             and fi["binary"]["status"] == "NOT_INSTALLED",
                             f"{fi['flag']}: enabled={fi['enabled']}, "
                             f"бинарь={fi['binary']['status']}")

                caps = (await client.get("/api/capabilities")).json()
                ctx.positive("манифест способностей называет ИЗМЕРЕННЫЕ предпосылки рантайма",
                             isinstance(caps.get("probes"), dict) and bool(caps["probes"])
                             and all(isinstance(v, bool) for v in caps["probes"].values())
                             and all(set(("capability_ok", "policy_ok", "runtime_ok"))
                                     <= set(c["grant"]) for c in caps["capabilities"]),
                             f"{len(caps['capabilities'])} способностей, "
                             f"предпосылки: {caps['probes']}")

                from bcc import capability as cap_mod  # noqa: PLC0415
                from bcc.tools import REGISTRY as TOOLS  # noqa: PLC0415

                browser_cap = next((c for c in cap_mod.manifest(TOOLS)
                                    if c.capability_id.startswith("browser.")), None)
                if browser_cap is None:
                    ctx.not_proven("в этой сборке нет ни одной браузерной способности: "
                                   "правило «предпосылки нет — способности нет» мерить не на чем")
                blind = cap_mod.grant(browser_cap, spec=TOOLS.get(browser_cap.tool), args={},
                                      agent={"permissions": []}, probes={})
                ctx.positive("способность с НЕизмеренной предпосылкой не выдаётся и называет нехватку",
                             blind.granted is False and blind.runtime_ok is False
                             and "chromium" in blind.missing and "chromium" in blind.reason,
                             f"{browser_cap.capability_id}: {blind.reason}")

            # Включение флага — последним: отказ выше доказан ВЫКЛЮЧЕННОЙ подсистемой.
            with _env(**{DIAG_FLAG: "1"}):
                async with _product(ctx, workers=False) as (svc, client):
                    (Path(svc.settings.data_dir) / "desktop-run.log").write_text(
                        f"start pid=1\nвыдан токен {svc.auth.token}\n", encoding="utf-8")
                    built = await client.post("/api/diag/bundle")
                    result = built.json()
                    ctx.positive("включённая подсистема действительно работает: отказ был про флаг",
                                 built.status_code == 200 and result["enabled"] is True
                                 and Path(result["path"]).is_file()
                                 and result["size_bytes"] > 0,
                                 f"{result['name']}, {result['size_bytes']} байт")
                    ctx.positive("чистка сбора считает вычищенное вслух",
                                 result["redactions"] >= 1,
                                 f"вычищено мест: {result['redactions']}")

    asyncio.run(run())


# ------------------------------------------------------------------ 90
@scenario(id="OS-90", depth=INSTALLED_PRODUCT)
def os90_journal_is_bounded_and_keeps_the_last_evidence(ctx) -> None:
    """Журнал не растёт бесконечно, и обрезка не уносит последнюю улику.

    Два предела и один запрет. Пределы — возраст и число строк, их держит
    фоновый тик продукта (`features/control_plane._tick`), а не ручной вызов.
    Запрет — обрезка обязана уносить СТАРОЕ: журнал, из которого при
    переполнении пропадает свежее, хуже отсутствующего, потому что выглядит
    полным.

    Отдельно меряется след самой обрезки: без него «пусто» после долгого
    простоя не отличить от «ничего не происходило».
    """
    async def run() -> None:
        import sqlalchemy as sa  # noqa: PLC0415

        from bcc.db import events as events_t, utcnow  # noqa: PLC0415
        from bcc.features import control_plane as cp  # noqa: PLC0415

        from datetime import timedelta  # noqa: PLC0415

        with _env(**{TESTING_FLAG: "0",
                     "BOSSMAN_EVENTS_RETENTION_DAYS": "14",
                     "BOSSMAN_EVENTS_MAX_ROWS": "40"}):
            async with _product(ctx) as (svc, client):
                # Реестр читает пределы при импорте модуля — правим на время прогона.
                keep_days, keep_rows = cp.RETENTION_DAYS, cp.RETENTION_MAX_ROWS
                cp.RETENTION_DAYS, cp.RETENTION_MAX_ROWS = 14, 40
                try:
                    async with svc.db.session() as session:
                        await session.execute(sa.insert(events_t), [
                            {"kind": "owner.old", "ts": utcnow() - timedelta(days=40),
                             "data": {"i": i}} for i in range(25)])
                        await session.commit()
                    for i in range(60):
                        await svc.bus.emit("owner.step", n=i, note=f"шаг {i}")
                    last = (await client.get("/api/activity?limit=1")).json()[0]

                    async with svc.db.session() as session:
                        before = int((await session.execute(
                            sa.select(sa.func.count()).select_from(events_t))).scalar())

                    await cp._tick(svc)                     # тот самый фоновый тик продукта

                    async with svc.db.session() as session:
                        after = int((await session.execute(
                            sa.select(sa.func.count()).select_from(events_t))).scalar())
                        kinds = {r[0] for r in (await session.execute(
                            sa.select(events_t.c.kind))).fetchall()}
                    feed = (await client.get("/api/activity?limit=200")).json()
                finally:
                    cp.RETENTION_DAYS, cp.RETENTION_MAX_ROWS = keep_days, keep_rows

                ctx.positive("журнал ограничен: после тика строк не больше объявленного предела",
                             before > 40 and after <= 41,
                             f"{before} → {after} строк при пределе 40")
                ctx.positive("устаревшее унесено по возрасту",
                             "owner.old" not in kinds, f"виды после обрезки: {sorted(kinds)[:5]}")
                ctx.positive("последняя улика владельца пережила обрезку",
                             any(row["kind"] == last["kind"]
                                 and row["data"].get("n") == last["data"].get("n")
                                 for row in feed),
                             f"последнее событие до обрезки: {last['kind']} "
                             f"n={last['data'].get('n')}")
                ctx.positive("сама обрезка оставила след: «пусто» не выдаётся за «не было»",
                             "events.pruned" in kinds
                             and any(row["kind"] == "events.pruned"
                                     and row["data"].get("events", 0) > 0 for row in feed),
                             "в журнале есть запись events.pruned с числом удалённых строк")
                ctx.reached_installed_product("bcc.api.create_app + фоновый тик control_plane")

                # --- отрицательные контроли
                async with svc.db.session() as session:
                    oldest = int((await session.execute(
                        sa.select(sa.func.min(events_t.c.id)))).scalar() or 0)
                    newest = int((await session.execute(
                        sa.select(sa.func.max(events_t.c.id)))).scalar() or 0)
                ctx.negative("обрезка унесла СТАРОЕ, а не свежее: младший id сдвинулся, старший нет",
                             oldest > 1 and newest >= before - 1,
                             f"id от {oldest} до {newest}")
                ctx.negative("вырожденный предел возраста не обнуляет журнал целиком",
                             (await svc.bus.prune(max_age_days=0, max_rows=1_000_000))["events"] == 0
                             and int((await client.get("/api/activity?limit=1")).json()[0]["id"]) > 0,
                             "нулевой возраст приведён к суткам, журнал на месте")
                empty = type(svc.bus)(None)
                ctx.negative("журнал без базы ничего не удаляет и не падает",
                             await empty.prune() == {"events": 0, "run_events": 0},
                             "без базы удалено 0")
                repeat = await svc.bus.prune(max_age_days=14, max_rows=200)
                ctx.negative("повторный проход по уложившемуся журналу не уносит остаток",
                             repeat["events"] == 0
                             and len((await client.get("/api/activity?limit=200")).json()) > 1,
                             f"второй проход удалил {repeat['events']} строк")

    asyncio.run(run())
