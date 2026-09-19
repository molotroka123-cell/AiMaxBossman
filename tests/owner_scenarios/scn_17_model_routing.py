"""Владельческие сценарии 71–75: маршрутизация моделей и отказоустойчивость.

Владелец платит за то, чтобы задача доходила до конца на ТОЙ модели, которая
сейчас отвечает, а когда не доходит — чтобы он узнал об этом словами, а не по
тишине. Пять свойств, каждое из которых стоит владельцу задачи или денег:

* 71 — недостижимая модель не роняет задачу молча: продукт НАЗЫВАЕТ класс сбоя,
  берёт другую измеренно-здоровую цель, а когда путей больше нет — отдаёт
  решение владельцу с перечислением испробованного;
* 72 — 429, «модель недоступна», «пустой ответ» и «отвергнутый ключ» — РАЗНЫЕ
  исходы с разными лекарствами, и владелец видит какой именно случился;
* 73 — отсутствующий ключ никогда не превращается в выдуманный ответ: продукт
  называет НЕЗАДАННУЮ переменную и не идёт в сеть (измерено транспортом);
* 74 — здоровье модели меряется наблюдением, а не объявляется: молчащая
  перестаёт выбираться ТЕМ путём, которым продукт реально выбирает исполнителя,
  восстановившаяся возвращается, явный выбор владельца остаётся за владельцем;
* 75 — отказоустойчивость Gateway не становится дырой приватности: разомкнутый
  бэкенд пропускается, восстановившийся возвращается, а запрет облака держится
  и на запасном пути — транспорт не видит НИ ОДНОГО запроса наружу.

ГДЕ ИЗМЕРЯЕТСЯ. Главный урок прошлой волны: мерить надо тот слой, которым
продукт ПОЛЬЗУЕТСЯ. Поэтому 71 и 72 идут не по чистому классификатору, а по
лестнице восстановления `bcc/reality/recovery.py`, которую вызывает
`bcc/engine.py:2108` в `_handle_failure` — единственное место, где движок решает,
что делать со сбоем прогона. 74 идёт по `bcc/task_admission.py:81`
(`select_executor`) — тому самому выбору исполнителя, через который проходит
создание задачи владельца. 73 и 75 идут по `bossman/gateway/*` установленного
bossman-core, через который ядро ходит в модели.

Сценарий 72 НЕ повторяет OS-06: там проверен контракт CI-адаптера
(`ADAPTER_CONTRACT`), здесь — собственные классификаторы продукта и то, что
разным классам соответствуют РАЗНЫЕ лестницы лекарств.

ЖИВОГО ШАГА МОДЕЛИ ЗДЕСЬ НЕТ и быть не должно: ключа ИИ в прогоне нет, и все
пять объявлены `model_step: "none"`. Ни один отказ провайдера не подменяется
синтетическим ответом — наоборот, предмет проверки в том, что не подменяется.

Тяжёлые зависимости (`bcc.*`, `bossman.*`) импортируются ВНУТРИ функций, а
способности объявлены В РЕЕСТРЕ: BL-085 — корневой CI ставит только
pytest/pytest-timeout/psutil/httpx/pyyaml, и без способности каркас обязан
отдать честный вердикт вместо исполнения и вместо заглушки.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "command-center"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bossman-core"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

from scenario_runner import INSTALLED_PRODUCT, PRODUCT_CONTRACTS, scenario  # noqa: E402

#: Фиктивное значение ключа владельца. Собрано из кусков: целиком в исходнике не
#: лежит, из окружения владельца не берётся и в улики не попадает.
FAKE_KEY = "sk-" + "or-v1-" + "0000owner0000scenario0000placeholder00"
#: Переменная окружения, которой в среде нет и которую сценарий заводит сам.
#: Имя уникальное: чужую настройку владельца трогать нельзя.
KEY_ENV = "BOSSMAN_OS73_FAKE_PROVIDER_KEY"


# ------------------------------------------------------------------ 71
@scenario(id="OS-71", depth=PRODUCT_CONTRACTS)
def os71_unreachable_model_does_not_kill_the_task(ctx) -> None:
    """Сбой модели → класс сбоя назван → другая достижимая цель → или владелец.

    Измеряется ЛЕСТНИЦА ВОССТАНОВЛЕНИЯ (`bcc/reality/recovery.py`), которую
    движок вызывает в `bcc/engine.py:2108`. Отрицательные контроли стоят рядом
    с каждым утверждением: «взял другую цель» ничего не стоит, если он берёт
    ЛЮБУЮ другую, включая измеренно сломанную, и если отвергнутый ключ он тоже
    пытается «полечить» повтором.
    """
    from bcc import model_health as mh  # noqa: PLC0415
    from bcc.reality import recovery as rc  # noqa: PLC0415

    proven = mh.record_observation(None, mh.HEALTHY, "", latency_ms=20)
    broken = None
    for _ in range(3):
        broken = mh.record_observation(broken, mh.SILENT, "модель ответила пустотой")
    pool = [(7, proven), (9, broken)]

    # 429 по всему аккаунту: ждать бесполезно, лестница сперва меняет модель.
    throttled = rc.Ladder.from_dict(None, rc.classify_failure("HTTP 429: rate limit exceeded"))
    step1 = rc.next_rung(throttled, current_model_id=5, fallback_model_id=None,
                         healthy_models=pool, retries_left=2, max_retries=2)
    ctx.positive("недостижимая модель не роняет задачу: выбрана ДРУГАЯ цель",
                 step1.name == rc.ALTERNATE_MODEL and step1.model_id == 7
                 and step1.terminal is False,
                 f"ступень={step1.name} модель={step1.model_id}")
    ctx.positive("причина названа словами и содержит класс сбоя",
                 rc.THROTTLED in step1.reason and str(step1.model_id) in step1.reason,
                 step1.reason)
    ctx.positive("объявленный владельцем запасной путь идёт первым",
                 rc.next_rung(rc.Ladder.from_dict(None, rc.SILENT), current_model_id=5,
                              fallback_model_id=42, healthy_models=pool,
                              retries_left=0, max_retries=0).model_id == 42)

    # Ступени тратятся: задача приходит к терминалу за ограниченное число шагов.
    step2 = rc.next_rung(step1.ladder, current_model_id=step1.model_id,
                         healthy_models=pool, retries_left=2, max_retries=2)
    ctx.positive("вторая ступень отличается от первой — это другой путь, а не тот же",
                 step2.name != step1.name, f"вторая ступень={step2.name}")
    silent_first = rc.next_rung(rc.Ladder.from_dict(None, rc.SILENT), current_model_id=5,
                                healthy_models=pool, retries_left=0, max_retries=0)
    carried = rc.Ladder.from_dict(silent_first.ladder.to_dict(), rc.CAPABILITY)
    silent_second = rc.next_rung(carried, current_model_id=silent_first.model_id,
                                 healthy_models=pool, retries_left=0, max_retries=0)
    exhausted = rc.next_rung(rc.Ladder.from_dict(silent_second.ladder.to_dict(), rc.SILENT),
                             current_model_id=silent_first.model_id, healthy_models=pool,
                             retries_left=0, max_retries=0)
    ctx.positive("когда путей не осталось, решение уходит ВЛАДЕЛЬЦУ и перечисляет испробованное",
                 exhausted.terminal and rc.ALTERNATE_MODEL in exhausted.reason
                 and rc.DEGRADED_PATH in exhausted.reason, exhausted.reason)

    # --- отрицательные контроли
    ctx.negative("измеренно сломанная модель НЕ берётся как «другая цель»",
                 rc.next_rung(rc.Ladder.from_dict(None, rc.SILENT), current_model_id=5,
                              healthy_models=[(9, broken)], retries_left=0,
                              max_retries=0).model_id is None)
    ctx.negative("та же модель не выдаётся за другую",
                 rc.next_rung(rc.Ladder.from_dict(None, rc.SILENT), current_model_id=7,
                              healthy_models=[(7, proven)], retries_left=0,
                              max_retries=0).model_id != 7)
    rejected_key = rc.next_rung(rc.Ladder.from_dict(None, rc.classify_failure(
        "401 unauthorized: api key expired")), current_model_id=5, fallback_model_id=42,
        healthy_models=pool, retries_left=5, max_retries=5)
    ctx.negative("отвергнутый ключ НЕ лечится повтором и не тратит ключ дальше",
                 rejected_key.terminal and rejected_key.model_id is None
                 and "владел" in rejected_key.reason, rejected_key.reason)
    ctx.negative("потраченная ступень не возвращается со сменой класса сбоя",
                 silent_second.name != silent_first.name
                 and set(silent_second.ladder.spent) >= {rc.ALTERNATE_MODEL, rc.DEGRADED_PATH},
                 f"истрачено={silent_second.ladder.spent}")
    overrun = rc.next_rung(rc.Ladder(rc.TRANSIENT, (), transitions=99, classes=(rc.TRANSIENT,)),
                           current_model_id=5, healthy_models=pool, retries_left=9,
                           max_retries=2)
    ctx.negative("бюджет восстановления ограничен: вечного перебора путей нет",
                 overrun.terminal and rc.recovery_budget(2) == 2 + rc.MAX_STRATEGY_CHANGES,
                 f"бюджет={rc.recovery_budget(2)} исход={overrun.name}")
    ctx.negative("исчерпанный бюджет владельца не даёт повторить тот же маршрут",
                 rc.next_rung(rc.Ladder.from_dict(None, rc.TRANSIENT), current_model_id=5,
                              healthy_models=[], retries_left=0,
                              max_retries=0).name != rc.RETRY_SAME)


# ------------------------------------------------------------------ 72
@scenario(id="OS-72", depth=PRODUCT_CONTRACTS)
def os72_failures_are_distinguishable_to_the_owner(ctx) -> None:
    """429, «нет модели», пустой ответ и отвергнутый ключ — четыре разных исхода.

    Здесь измеряются СОБСТВЕННЫЕ классификаторы продукта, а не CI-адаптер (его
    контракт — OS-06): `bcc/model_health.py` (что записывается в здоровье),
    `bcc/reality/recovery.py::classify_failure` (что видит движок) и
    `bossman/gateway/backends.py::BackendError.failover` (переключаться ли на
    следующую цель). Разные исходы обязаны вести к РАЗНЫМ действиям — иначе
    «различает» означает лишь разные слова в журнале.
    """
    from bcc import model_health as mh  # noqa: PLC0415
    from bcc.reality import recovery as rc  # noqa: PLC0415
    from bossman.gateway.backends import BackendError, _explain_status  # noqa: PLC0415

    by_code = {code: mh.classify_status_code(code)[0] for code in (429, 401, 503, 404, 200)}
    ctx.positive("HTTP-исходы провайдера разведены по именам, а не слиты в «ошибку»",
                 by_code == {429: mh.THROTTLED, 401: mh.UNAUTHORIZED, 503: mh.PROVIDER_DOWN,
                             404: mh.MALFORMED, 200: mh.HEALTHY}, str(by_code))
    silent, silent_detail = mh.classify_answer("   ", tokens_out=0)
    ctx.positive("HTTP 200 с пустым телом — отдельный исход «молчит», а не успех",
                 silent == mh.SILENT and "пустот" in silent_detail, silent_detail)
    ctx.positive("ответ не той формы отличается от пустого ответа",
                 mh.classify_answer(None)[0] == mh.MALFORMED
                 and mh.classify_answer({"a": 1})[0] == mh.MALFORMED
                 and mh.MALFORMED != mh.SILENT)

    classes = {text: rc.classify_failure(text) for text in (
        "HTTP 429: rate limit exceeded",
        "no endpoints found for this model",
        "empty response from provider",
        "401 unauthorized: api key expired",
        "provider returned HTTP 503 backend error",
        "prompt is too long: maximum context 8192")}
    ctx.positive("движок видит ЧЕТЫРЕ и более разных класса сбоя, а не один",
                 len(set(classes.values())) >= 5
                 and classes["HTTP 429: rate limit exceeded"] == rc.THROTTLED
                 and classes["empty response from provider"] == rc.SILENT
                 and classes["401 unauthorized: api key expired"] == rc.UNAUTHORIZED,
                 str(sorted(set(classes.values()))))
    ctx.positive("разным классам соответствуют РАЗНЫЕ лестницы лекарств",
                 rc.LADDERS[rc.UNAUTHORIZED] == (rc.HUMAN,)
                 and rc.LADDERS[rc.THROTTLED][0] == rc.ALTERNATE_MODEL
                 and rc.LADDERS[rc.TRANSIENT][0] == rc.RETRY_SAME
                 and len({rc.LADDERS[c] for c in (rc.UNAUTHORIZED, rc.THROTTLED,
                                                  rc.TRANSIENT, rc.SILENT)}) == 4)
    ctx.positive("каждый класс сбоя ложится в ИМЕНОВАННОЕ измерение здоровья",
                 rc.HEALTH_STATUS[rc.THROTTLED] == mh.THROTTLED
                 and rc.HEALTH_STATUS[rc.SILENT] == mh.SILENT
                 and rc.HEALTH_STATUS[rc.UNAUTHORIZED] == mh.UNAUTHORIZED
                 and set(rc.HEALTH_STATUS.values()) <= mh.ALL_STATUSES)
    ctx.positive("отказ провайдера объясняется владельцу разными словами",
                 len({_explain_status("cloud", code) for code in (401, 402, 403, 404, 429)}) == 5,
                 _explain_status("cloud", 402))

    # --- отрицательные контроли
    ctx.negative("ошибка САМОГО запроса не переключает цель и не гасит бэкенд",
                 BackendError("bad request", status_code=400).failover is False
                 and BackendError("not found", status_code=404).failover is False)
    ctx.negative("а перегрузка и отказ сервера — переключают",
                 BackendError("slow", status_code=429).failover is True
                 and BackendError("boom", status_code=503).failover is True
                 and BackendError("transport").failover is True)
    ctx.negative("429 со словом connection не считается разовым сбоем сети",
                 rc.classify_failure("429 connection pool rate limit") == rc.THROTTLED)
    ctx.negative("неопознанный сбой не выдаётся за разовый: он назван unknown",
                 rc.classify_failure("нечто, чего мы не знаем") == rc.UNKNOWN)
    ctx.negative("битая запись здоровья читается как «не измерено», а не как «здорова»",
                 mh.HealthRecord.from_dict({"status": "totally-fine"}).status == mh.UNMEASURED
                 and mh.HealthRecord.from_dict("healthy").status == mh.UNMEASURED)
    ctx.negative("лекарства для разных классов не совпадают: 429 не лечится как 401",
                 rc.LADDERS[rc.THROTTLED] != rc.LADDERS[rc.UNAUTHORIZED])


# ------------------------------------------------------------------ 73
@scenario(id="OS-73", depth=INSTALLED_PRODUCT)
def os73_missing_key_never_becomes_an_answer(ctx) -> None:
    """Нет ключа → названа НЕЗАДАННАЯ переменная, сети не было, ответа нет.

    Самый дорогой вид лжи — «модель ответила», когда никакого вызова не было.
    Поэтому здесь считается не отсутствие исключения, а НАСТОЯЩИЕ запросы:
    транспорт бэкенда записывает каждый исходящий вызов, и отрицательный
    контроль утверждает пустой список. Счётчик не слепой — это доказано тем,
    что с ключом тот же транспорт запрос видит.
    """
    from bcc.providers import AnthropicAdapter, ProviderError  # noqa: PLC0415
    from bossman.gateway.backends import OpenAIBackend  # noqa: PLC0415
    from bossman.gateway.config import BackendConfig  # noqa: PLC0415

    ctx.reached_installed_product(
        "bossman.gateway.backends и bcc.providers установленного продукта этой ветки")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"data": [{"id": "cloud-big"}]})

    previous = os.environ.get(KEY_ENV)
    os.environ.pop(KEY_ENV, None)
    try:
        cfg = BackendConfig(name="облако", base_url="https://openrouter.ai/api/v1",
                            api_key_env=KEY_ENV, cloud=True)
        backend = OpenAIBackend(cfg, transport=httpx.MockTransport(handler))
        reason = backend.unavailable_reason()
        catalog = asyncio.run(backend.list_models())
        ctx.positive("продукт НАЗЫВАЕТ незаданную переменную окружения владельца",
                     reason is not None and KEY_ENV in reason, reason or "")
        ctx.positive("исход «нет ключа» отличим от «провайдер отказал» и от «моделей ноль»",
                     catalog.status == "unavailable" and catalog.ok is False
                     and KEY_ENV in (catalog.reason or ""),
                     f"status={catalog.status} reason={catalog.reason}")
        ctx.negative("без ключа НИ ОДНОГО запроса наружу не ушло",
                     seen == [], f"запросов={len(seen)}")
        ctx.negative("отсутствие ключа не дало ни одной модели",
                     catalog.models == [])

        os.environ[KEY_ENV] = FAKE_KEY
        with_key = OpenAIBackend(BackendConfig(name="облако2",
                                               base_url="https://openrouter.ai/api/v1",
                                               api_key_env=KEY_ENV, cloud=True),
                                 transport=httpx.MockTransport(handler))
        ok = asyncio.run(with_key.list_models())
        ctx.positive("счётчик запросов НЕ слепой: с ключом тот же транспорт вызов видит",
                     ok.ok and ok.models == ["cloud-big"] and len(seen) == 1,
                     f"запросов={len(seen)}")

        def refuse(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(401, json={"error": "rejected"})

        refused = asyncio.run(OpenAIBackend(
            BackendConfig(name="облако3", base_url="https://openrouter.ai/api/v1",
                          api_key_env=KEY_ENV, cloud=True),
            transport=httpx.MockTransport(refuse)).list_models())
        ctx.positive("отказ провайдера — третий, отдельный исход с причиной",
                     refused.status == "error" and refused.ok is False
                     and "401" in (refused.reason or ""), refused.reason or "")
        ctx.negative("значение ключа не попало ни в причину, ни в подробности",
                     FAKE_KEY not in (refused.reason or "")
                     and FAKE_KEY not in repr(refused.detail)
                     and FAKE_KEY not in (reason or ""))
    finally:
        if previous is None:
            os.environ.pop(KEY_ENV, None)
        else:
            os.environ[KEY_ENV] = previous

    # Второй продуктовый путь к моделям: облачный адаптер Command Center.
    adapter = AnthropicAdapter(api_key=None)
    ctx.refused("облачный адаптер без ключа отказывает, а не сочиняет ответ",
                lambda: asyncio.run(adapter.chat("нет-модели",
                                                 [{"role": "user", "content": "привет"}])),
                ProviderError)
    health = asyncio.run(adapter.health())
    ctx.negative("проверка здоровья без ключа не рапортует «ок»",
                 health.status != "ok" and "api_key" in health.detail,
                 f"{health.status}: {health.detail}")


# ------------------------------------------------------------------ 74
@scenario(id="OS-74", depth=PRODUCT_CONTRACTS)
def os74_model_health_is_measured_not_declared(ctx) -> None:
    """Молчащая перестаёт выбираться, восстановившаяся возвращается.

    Цепочка целиком продуктовая: измерение кладёт `Registry.record_model_health`
    (`command-center/bcc/registry.py:174`), а выбирает исполнителя
    `bcc/task_admission.py:81` `select_executor` — ровно тот путь, которым идёт
    создание задачи владельца. Никакое «здоровье» здесь не объявляется руками:
    запись в БД делает сам продукт из ОДНОГО наблюдения за раз.
    """
    import sqlalchemy as sa  # noqa: PLC0415

    from bcc import model_health as mh  # noqa: PLC0415
    from bcc.db import Database, agents as agents_t, models as models_t  # noqa: PLC0415
    from bcc.db import providers as providers_t, utcnow  # noqa: PLC0415
    from bcc.events import EventBus  # noqa: PLC0415
    from bcc.registry import Registry  # noqa: PLC0415
    from bcc.secrets import Vault  # noqa: PLC0415
    from bcc.task_admission import ExecutorUnavailable, select_executor  # noqa: PLC0415

    data_dir = ctx.path("данные", "держатель").parent
    prompt = "расскажи, что нового"

    async def run() -> dict:
        db = Database(f"sqlite+aiosqlite:///{data_dir / 'bcc.db'}")
        await db.create_all()
        registry = Registry(db, Vault(data_dir), EventBus(db))
        async with db.session() as s:
            pid = int((await s.execute(sa.insert(providers_t).values(
                name="местный", kind="openai_compat",
                base_url="http://127.0.0.1:11434/v1",
                created_at=utcnow()))).inserted_primary_key[0])
            proven = int((await s.execute(sa.insert(models_t).values(
                provider_id=pid, name="qwen-a", alias="доказанная", kind="local",
                status="online"))).inserted_primary_key[0])
            quiet = int((await s.execute(sa.insert(models_t).values(
                provider_id=pid, name="qwen-b", alias="молчащая", kind="local",
                status="online"))).inserted_primary_key[0])
            await s.execute(sa.insert(agents_t).values(
                name="агент-молчун", model_id=quiet, enabled=True, created_at=utcnow()))
            await s.execute(sa.insert(agents_t).values(
                name="агент-рабочий", model_id=proven, enabled=True, created_at=utcnow()))
            await s.commit()

        out: dict = {"quiet_model": quiet, "proven_model": proven}
        # ДО измерений обе модели неизмерены: неизвестность — не сломанность.
        async with db.session() as s:
            out["unmeasured_pick"] = (await select_executor(
                s, prompt=prompt, agent_id=None))["name"]
        out["unmeasured"] = (await registry.model_health(quiet)).status

        # Продукт кладёт НАБЛЮДЕНИЯ, по одному: три молчания и один успех.
        for _ in range(3):
            record = await registry.record_model_health(quiet, mh.SILENT,
                                                        "модель ответила пустотой")
        out["quiet_record"] = record.to_dict()
        await registry.record_model_health(proven, mh.HEALTHY, "", latency_ms=25)
        async with db.session() as s:
            out["after_measure"] = (await select_executor(
                s, prompt=prompt, agent_id=None))["name"]
            # Явный выбор владельца остаётся за владельцем.
            out["explicit"] = (await select_executor(s, prompt=prompt, agent_id=1))["name"]

        # Ломается и вторая: автовыбор обязан ОТКАЗАТЬ, а не взять сломанную.
        for _ in range(3):
            await registry.record_model_health(proven, mh.SILENT, "модель ответила пустотой")
        async with db.session() as s:
            try:
                out["all_broken"] = (await select_executor(
                    s, prompt=prompt, agent_id=None))["name"]
            except ExecutorUnavailable as exc:
                out["all_broken"] = f"ОТКАЗ: {exc}"

        # Восстановление: одно наблюдение успеха возвращает модель в выбор.
        back = await registry.record_model_health(proven, mh.HEALTHY, "", latency_ms=18)
        out["recovered_record"] = back.to_dict()
        async with db.session() as s:
            out["recovered_pick"] = (await select_executor(
                s, prompt=prompt, agent_id=None))["name"]
        await db.close()
        return out

    got = asyncio.run(run())
    ctx.positive("неизмеренная модель не блокирует работу: иначе здоровье не измерить никогда",
                 got["unmeasured"] == mh.UNMEASURED and got["unmeasured_pick"].startswith("агент"),
                 f"статус={got['unmeasured']} выбран={got['unmeasured_pick']}")
    ctx.positive("продукт записал ИЗМЕРЕНИЕ, а не объявление",
                 got["quiet_record"]["status"] == mh.SILENT
                 and got["quiet_record"]["samples"] == 3
                 and got["quiet_record"]["consecutive_failures"] == 3
                 and got["quiet_record"]["usable"] is False,
                 str(got["quiet_record"]["samples"]))
    ctx.positive("молчащая модель ПЕРЕСТАЛА выбираться тем путём, которым выбирает продукт",
                 got["after_measure"] == "агент-рабочий", got["after_measure"])
    ctx.positive("восстановившаяся модель ВОЗВРАЩАЕТСЯ в выбор",
                 got["recovered_pick"] == "агент-рабочий"
                 and got["recovered_record"]["status"] == mh.HEALTHY
                 and got["recovered_record"]["consecutive_failures"] == 0,
                 got["recovered_pick"])
    ctx.positive("уверенность остаётся полосой, а не выдуманным числом",
                 got["quiet_record"]["confidence"] in (mh.CONFIDENCE_LOW, mh.CONFIDENCE_MEDIUM,
                                                       mh.CONFIDENCE_HIGH),
                 got["quiet_record"]["confidence"])

    # --- отрицательные контроли
    ctx.negative("когда ВСЕ измеренно сломаны, автовыбор ОТКАЗЫВАЕТ, а не берёт сломанную",
                 str(got["all_broken"]).startswith("ОТКАЗ:")
                 and "не отвечает" in str(got["all_broken"]), str(got["all_broken"]))
    ctx.negative("явный выбор владельца не отменяется измерением",
                 got["explicit"] == "агент-молчун", got["explicit"])
    ctx.negative("неизмеренная НЕ считается здоровой и не обгоняет доказанную",
                 mh.HealthRecord().usable() is False
                 and mh.HealthRecord().rank_key() > mh.record_observation(
                     None, mh.HEALTHY, "").rank_key())
    broken = None
    for _ in range(3):
        broken = mh.record_observation(broken, mh.SILENT, "пусто")
    ctx.negative("запасной путь возвращает None, а не «хоть что-нибудь»",
                 mh.select_fallback([("сломанная", broken)]) is None)


# ------------------------------------------------------------------ 75
@scenario(id="OS-75", depth=INSTALLED_PRODUCT)
def os75_failover_is_not_a_hole_in_the_cloud_policy(ctx) -> None:
    """Разомкнутый бэкенд пропускается, но запасной путь не открывает облако.

    Два свойства в одном месте намеренно: по отдельности каждое выглядит
    выполненным, а беда рождается на их стыке — «местная модель легла, значит
    можно в облако». Поэтому облачная цель здесь есть, местная умышленно
    разомкнута, а доказательство того, что наружу не ушло ничего, — НЕ отсутствие
    исключения, а пустой список запросов, записанный транспортом бэкенда.

    Это не повтор OS-38: там граница `bossman_shared.privacy` внутри Command
    Center, здесь — маршрут Gateway, по которому ядро ходит в модели.
    """
    import time  # noqa: PLC0415

    from bossman.gateway.backends import CircuitOpenError, OpenAIBackend  # noqa: PLC0415
    from bossman.gateway.config import (AliasConfig, BackendConfig,  # noqa: PLC0415
                                        GatewayConfig, ModelTarget)
    from bossman.gateway.router import CloudPolicyDenied, ModelRouter  # noqa: PLC0415

    ctx.reached_installed_product("bossman.gateway.router установленного bossman-core")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": "ГОТОВ"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1}})

    config = GatewayConfig(
        backends={
            "местный": BackendConfig(name="местный", base_url="http://127.0.0.1:11434/v1",
                                     circuit_failure_threshold=2,
                                     circuit_cooldown_seconds=0.2),
            "запасной": BackendConfig(name="запасной", base_url="http://127.0.0.1:11435/v1",
                                      circuit_failure_threshold=2,
                                      circuit_cooldown_seconds=0.2),
            "облако": BackendConfig(name="облако", base_url="https://openrouter.ai/api/v1",
                                    cloud=True),
            "openrouter": BackendConfig(name="openrouter",
                                        base_url="https://openrouter.ai/api/v1", cloud=True),
        },
        aliases={"умная": AliasConfig(name="умная", targets=[
            ModelTarget("местный", "qwen-местный", 10, set()),
            ModelTarget("запасной", "qwen-запасной", 20, set()),
            ModelTarget("облако", "cloud-big", 90, set())])},
    )
    backends = {name: OpenAIBackend(cfg, transport=httpx.MockTransport(handler))
                for name, cfg in config.backends.items()}
    router = ModelRouter(config, backends=backends)

    local_only = router.resolve("умная", cloud_allowed=False)
    ctx.positive("при запрете облака в маршруте остаются только местные цели",
                 [r.backend_name for r in local_only] == ["местный", "запасной"]
                 and not any(r.is_cloud for r in local_only),
                 str([r.backend_name for r in local_only]))

    # Первая местная цель легла: автомат размыкается после объявленного порога.
    backends["местный"].breaker.record_failure("HTTP 503")
    backends["местный"].breaker.record_failure("HTTP 503")
    after_open = router.resolve("умная", cloud_allowed=False)
    ctx.positive("разомкнутая цель ПРОПУСКАЕТСЯ, и берётся следующая достижимая",
                 [r.backend_name for r in after_open] == ["запасной"],
                 f"автомат={backends['местный'].breaker.state}")

    backends["запасной"].breaker.record_failure("timeout")
    backends["запасной"].breaker.record_failure("timeout")
    denied = _raises(lambda: router.resolve("умная", cloud_allowed=False))
    ctx.positive("когда достижимых целей не осталось, отказ НАЗЫВАЕТ причину",
                 isinstance(denied, (CloudPolicyDenied, CircuitOpenError))
                 and ("облач" in str(denied).lower() or "разомкнут" in str(denied).lower()),
                 f"{type(denied).__name__}: {str(denied)[:120]}")

    # --- отрицательные контроли
    ctx.negative("падение местных целей НЕ открыло дорогу в облако: маршрута нет вовсе",
                 isinstance(denied, BaseException)
                 and not any(getattr(route, "is_cloud", False)
                             for route in (denied if isinstance(denied, list) else [])),
                 f"исход={type(denied).__name__}")
    ctx.negative("ни одного запроса наружу не ушло — измерено транспортом",
                 seen == [], f"запросов={len(seen)}")
    direct = _raises(lambda: router.resolve("openrouter/qwen/qwen3-max", cloud_allowed=False))
    ctx.negative("прямая адресация провайдера не обходит запрет облака",
                 isinstance(direct, CloudPolicyDenied), f"{type(direct).__name__}")
    ctx.negative("и на обходном пути транспорт по-прежнему чист",
                 seen == [], f"запросов={len(seen)}")

    # Счётчик не слепой: при РАЗРЕШЁННОМ облаке запасной путь доходит до цели.
    allowed = router.resolve("умная", cloud_allowed=True)
    cloud_route = next(r for r in allowed if r.is_cloud)
    body, _headers = asyncio.run(cloud_route.backend.json_request(
        "/v1/chat/completions", {"model": cloud_route.model, "messages": []}))
    ctx.positive("счётчик запросов не слепой: с разрешения облако ОТВЕЧАЕТ",
                 body["choices"][0]["message"]["content"] == "ГОТОВ" and len(seen) == 1,
                 f"запросов={len(seen)}")

    time.sleep(0.25)
    recovered = router.resolve("умная", cloud_allowed=False)
    ctx.positive("восстановившаяся местная цель возвращается в маршрут",
                 "местный" in [r.backend_name for r in recovered],
                 str([r.backend_name for r in recovered]))


def _raises(call):
    """Исход вызова: исключение как значение, иначе сам результат."""
    try:
        return call()
    except BaseException as exc:  # noqa: BLE001 — тип исхода и есть предмет проверки
        return exc
