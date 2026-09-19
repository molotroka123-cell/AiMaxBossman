"""Владельческие сценарии 101–106: Social Farm работает, пока владелец спит.

Шесть вопросов о подсистеме, которой на табло не было вовсе, а она как раз и
есть «долгая автономная работа от имени владельца»: браузерный аккаунт и
очередь публикаций живут часами без человека, и цена ошибки тут не красный
тест, а вторая публикация от имени владельца или вход в чужой аккаунт.

* 101 — работа переживает смерть процесса и продолжается С ДОСТИГНУТОЙ ТОЧКИ:
  перезапуск изображается ЧЕСТНО, новым соединением с тем же файлом базы;
* 102 — та же работа, поданная дважды, остаётся ОДНОЙ, и это доказывается
  вставкой МИМО хранилища: решает уникальный индекс, а не вежливость
  вызывающего;
* 103 — работник умер посреди работы: следующему она не достаётся до сверки,
  а сверка может её ЗАБРАТЬ и довести (BL-114/BL-115/BL-117 — до них
  восстановление было тупиком: пережить срыв и замереть навсегда);
* 104 — дрейф интерфейса доходит до реестра возможностей и ПОНИЖАЕТ её, а
  гонка и передача человеку счётчик не двигают (BL-105);
* 105 — учётная запись владельца не смешивается с другой: работы, каталоги
  контекстов, сессия и конверт к воркеру разделены каждый своей проверкой;
* 106 — потребовалась проверка «ты человек»: продукт ОСТАНАВЛИВАЕТСЯ, НАЗЫВАЕТ
  её и зовёт владельца, а не делает вид, что получилось.

ГЛУБИНА УЛИКИ — `product_contracts`, и это не скромность. Social Farm не
ставится ни одним заданием: ни корневой CI, ни владельческие сценарии не
выполняют для него `pip install`. Здесь работают классы приложения с этой
ветки поверх НАСТОЯЩЕЙ базы SQLite и НАСТОЯЩЕГО порта страницы приложения
(`FixtureDom` — часть `src/`, а не тестовый двойник), но цепочка не проходит
через установленный продукт владельца, и называть её `installed_product`
значило бы приписать себе чужую глубину.

ЖИВОГО ШАГА МОДЕЛИ НЕТ: ни один из шести не требует модели, все объявлены
`model_step: "none"`.

Зависимости модуля — стандартная библиотека. Всё из `social_farm` импортируется
ВНУТРИ функций и объявлено в реестре как `social_farm` (BL-085: корневой CI
ставит только pytest/pytest-timeout/psutil/httpx/pyyaml, и приложения в нём нет
вовсе — тогда шесть строк честно не исполняются).
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(REPO_ROOT / "apps" / "social-farm" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scenario_runner import PRODUCT_CONTRACTS, scenario  # noqa: E402

#: Отсчёт времени задаётся сценарием, а не часами машины: «работник умер»
#: должно наступать по решению проверки, а не по ожиданию в пятнадцать минут.
T0 = datetime(2026, 9, 19, 3, 0, tzinfo=timezone.utc)
OWNER_ACCOUNT = "acc-владельца"
OTHER_ACCOUNT = "acc-чужой"
IDENTITY = "nashe_ateljie"
FOREIGN_IDENTITY = "chuzhoj_akkaunt"


# --------------------------------------------------------------- база работ
def _open_store(path: Path, accounts: tuple[str, ...] = (OWNER_ACCOUNT,)):
    """Настоящая база приложения по его собственной схеме и миграциям."""
    from social_farm.jobs import JobStore  # noqa: PLC0415
    from social_farm.storage.schema import open_database  # noqa: PLC0415

    conn = open_database(path)
    for account in accounts:
        conn.execute(
            "INSERT OR IGNORE INTO social_accounts (id, provider, provider_account_id,"
            " created_at, updated_at) VALUES (?, 'fixture', ?, ?, ?)",
            (account, account, T0.isoformat(), T0.isoformat()))
    return JobStore(conn)


def _restart(store, path: Path):
    """Смерть процесса изображается ЧЕСТНО: соединение закрыто, открыто новое.

    Хранилище без перезапуска доказывает только работу словаря в памяти.
    """
    from social_farm.jobs import JobStore  # noqa: PLC0415
    from social_farm.storage.schema import open_database  # noqa: PLC0415

    store.conn.close()
    return JobStore(open_database(path))


# ------------------------------------------------------------ страница и пакет
def _pack_document() -> dict:
    """Пакет селекторов фикстуры. Классы безопасности приходят из домена."""
    return {
        "provider": "fixture", "version": "1.0.0", "ui_revision": "2026-09-19",
        "locale": "ru",
        "actions": [
            {"action": "account.identity.read", "target": "имя владельца сессии",
             "capability": "account.read",
             "strategies": [{"kind": "stable_attribute", "value": "data-testid=viewer"}]},
            {"action": "media.publish.image", "target": "кнопка «Поделиться»",
             "capability": "media.publish.image",
             "strategies": [{"kind": "role", "value": "button|Поделиться"}],
             "postconditions": ["text_contains:опубликовано"]},
        ],
    }


_FEED_MARKUP = ("<!doctype html><html lang=ru><body><h1>Публикации</h1>"
                "<div data-testid=viewer>{identity}</div>"
                "<button id=share>Поделиться</button></body></html>")


def _feed_page(*, identity: str = IDENTITY, share_buttons: int = 1,
               text: str = "", markup: str = ""):
    from social_farm.browser import FixtureElement, FixturePage  # noqa: PLC0415

    elements = [FixtureElement(tag="h1", text="Публикации"),
                FixtureElement(tag="div", text=identity,
                               attributes={"data-testid": "viewer"})]
    for index in range(share_buttons):
        elements.append(FixtureElement(tag="button", text="Поделиться",
                                       attributes={"id": f"share{index}"}))
    return FixturePage(url=f"https://fixture.local/{identity}/", title="Лента",
                       text=text or f"Публикации {identity} Поделиться",
                       markup=markup or _FEED_MARKUP.format(identity=identity),
                       elements=elements)


def _session(page, *, account_id: str = OWNER_ACCOUNT, identity: str = IDENTITY):
    from social_farm.browser import (AccountBrowserSession, BrowserConfig,  # noqa: PLC0415
                                     FixtureDom, SelectorRegistry)

    dom = FixtureDom(page)
    registry = SelectorRegistry()
    registry.register_document(_pack_document())
    return dom, AccountBrowserSession(
        account_id=account_id, expected_identity=identity, dom=dom,
        registry=registry, provider="fixture", config=BrowserConfig())


# ------------------------------------------------------------------ OS-101
@scenario(id="OS-101", depth=PRODUCT_CONTRACTS)
def os101_work_survives_process_death_and_continues(ctx) -> None:
    """Работа переживает смерть процесса и продолжается с достигнутой точки.

    Перезапуск изображается НАСТОЯЩИЙ: соединение с файлом базы закрывается и
    открывается заново. Проверка «состояние переживает перезапуск» на том же
    объекте в памяти доказывала бы работу словаря, а не долговечность.
    """
    from social_farm.domain.jobs import Checkpoint, JobState  # noqa: PLC0415
    from social_farm.jobs import JobStoreError  # noqa: PLC0415

    path = ctx.path("ферма", "работы.sqlite3")
    store = _open_store(path)
    job = store.enqueue(account_id=OWNER_ACCOUNT, capability="media.publish",
                        idempotency_key="ночная-публикация",
                        payload={"подпись": "доброе утро"}, now=T0)
    ctx.positive("расписка записана ДО того, как хоть что-то ушло наружу",
                 job.state is JobState.QUEUED and job.attempts == 0
                 and job.lease_owner == "",
                 f"состояние={job.state.value}, попыток={job.attempts}")

    taken = store.acquire(worker="работник-ночной", lease_seconds=900, now=T0)
    store.checkpoint(job.id, "POLICY_EVALUATED", worker="работник-ночной", now=T0)
    store.checkpoint(job.id, "MEDIA_RENDERED", worker="работник-ночной", now=T0)
    before = store.remaining_steps(job.id)

    # --- смерть процесса
    store = _restart(store, path)
    after = store.get(job.id)
    ctx.positive("после перезапуска работа НА МЕСТЕ, а не пропала",
                 after.id == taken.id and after.payload == {"подпись": "доброе утро"},
                 f"полезная нагрузка={after.payload}")
    ctx.positive("достигнутая точка пережила перезапуск",
                 after.checkpoint == "MEDIA_RENDERED", f"этап={after.checkpoint!r}")
    ctx.positive("продолжение идёт С ЭТОЙ точки, а не с начала",
                 store.remaining_steps(job.id) == before
                 and "MEDIA_RENDERED" not in store.remaining_steps(job.id)
                 and store.remaining_steps(job.id)[0] == "PROVIDER_CONTAINER_CREATED",
                 f"осталось={store.remaining_steps(job.id)}")
    ctx.positive("счётчик попыток не обнулился перезапуском",
                 after.attempts == 1, f"попыток={after.attempts}")

    # --- отрицательные контроли
    ctx.negative("после срыва работа НЕ лежит в очереди как новая",
                 after.state is not JobState.QUEUED
                 and store.acquire(worker="работник-утренний",
                                   now=T0 + timedelta(seconds=5_000)) is None,
                 f"состояние={after.state.value}; очередь пуста")
    fresh = store.enqueue(account_id=OWNER_ACCOUNT, capability="media.publish",
                          idempotency_key="без-этапов", now=T0)
    ctx.negative("работа без отметки НЕ считается частично сделанной",
                 store.remaining_steps(fresh.id) == tuple(Checkpoint.ORDER),
                 f"осталось всё: {len(store.remaining_steps(fresh.id))} этапов")
    ctx.refused("невыдуманный этап: неизвестное имя отвергается, а не пишется",
                lambda: store.checkpoint(fresh.id, "ПОЧТИ_ГОТОВО",
                                         worker="работник-ночной", now=T0),
                JobStoreError)
    ctx.negative("после отказа отметка НЕ появилась",
                 store.get(fresh.id).checkpoint == "",
                 f"этап={store.get(fresh.id).checkpoint!r}")


# ------------------------------------------------------------------ OS-102
@scenario(id="OS-102", depth=PRODUCT_CONTRACTS)
def os102_the_same_intent_is_one_job_by_the_database(ctx) -> None:
    """Та же работа дважды — ОДНА работа, и гарантию даёт индекс, а не код.

    Проверка сравнением внутри хранилища доказывала бы только вежливость
    вызывающего: войти с другой стороны (другой процесс, прямой SQL, чинёная
    миграция) — и двойная публикация состоялась бы. Поэтому здесь есть вставка
    МИМО хранилища, и она обязана быть отвергнута самой базой.
    """
    import sqlite3  # noqa: PLC0415

    path = ctx.path("ферма", "идемпотентность.sqlite3")
    store = _open_store(path, accounts=(OWNER_ACCOUNT, OTHER_ACCOUNT))
    first = store.enqueue(account_id=OWNER_ACCOUNT, capability="media.publish",
                          idempotency_key="пост-1", payload={"текст": "первый"}, now=T0)
    second = store.enqueue(account_id=OWNER_ACCOUNT, capability="media.publish",
                           idempotency_key="пост-1", payload={"текст": "второй"}, now=T0)
    ctx.positive("то же намерение вернуло ТУ ЖЕ работу, а не вторую",
                 second.id == first.id and len(store.by_account(OWNER_ACCOUNT)) == 1,
                 f"работ на аккаунте={len(store.by_account(OWNER_ACCOUNT))}")
    ctx.positive("«уже принято» не означает «принято заново»: нагрузка первой цела",
                 second.payload == {"текст": "первый"}, f"нагрузка={second.payload}")

    other_key = store.enqueue(account_id=OWNER_ACCOUNT, capability="media.publish",
                              idempotency_key="пост-2", now=T0)
    ctx.positive("запрет не тотальный: другой ключ заводит другую работу",
                 other_key.id != first.id and len(store.by_account(OWNER_ACCOUNT)) == 2,
                 f"работ={len(store.by_account(OWNER_ACCOUNT))}")
    neighbour = store.enqueue(account_id=OTHER_ACCOUNT, capability="media.publish",
                              idempotency_key="пост-1", now=T0)
    ctx.positive("тот же ключ на ДРУГОМ аккаунте — другая работа",
                 neighbour.id != first.id and len(store.by_account(OTHER_ACCOUNT)) == 1,
                 f"чужих работ={len(store.by_account(OTHER_ACCOUNT))}")

    # --- отрицательные контроли: решает база
    ctx.refused("вставка МИМО хранилища отвергнута самой базой",
                lambda: store.conn.execute(
                    "INSERT INTO social_jobs (id, account_id, capability, state,"
                    " idempotency_key, created_at, updated_at)"
                    " VALUES ('подложная', ?, 'media.publish', 'QUEUED', 'пост-1', ?, ?)",
                    (OWNER_ACCOUNT, T0.isoformat(), T0.isoformat())),
                sqlite3.IntegrityError)
    index = store.conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='ux_jobs_account_idem'"
    ).fetchone()
    ctx.negative("единственность объявлена УНИКАЛЬНЫМ индексом, а не соглашением",
                 index is not None and "UNIQUE" in (index["sql"] or "").upper()
                 and "account_id" in index["sql"] and "idempotency_key" in index["sql"],
                 (index["sql"] if index else "индекса нет"))
    ctx.negative("после отвергнутой вставки второй работы не появилось",
                 len(store.by_account(OWNER_ACCOUNT)) == 2,
                 f"работ на аккаунте владельца={len(store.by_account(OWNER_ACCOUNT))}")


# ------------------------------------------------------------------ OS-103
@scenario(id="OS-103", depth=PRODUCT_CONTRACTS)
def os103_a_dead_workers_job_waits_for_reconciliation(ctx) -> None:
    """Работа мертвеца не уходит следующему — и сверка может её ЗАБРАТЬ.

    Половина «не отдавать следующему» на ветке была, а второй половины не
    было: работа уходила в `RECONCILING` со снятой арендой, и взять её обратно
    было НЕЧЕМ — `acquire` отбирает только `QUEUED`, а всё остальное требует
    аренды. Работа владельца переживала срыв и замирала навсегда (BL-114).
    Здесь проверяются обе половины сразу: по отдельности каждая
    удовлетворяется вырожденно — «никому не отдавать» проходит первую,
    «отдавать кому угодно» вторую.
    """
    from social_farm.domain.jobs import (ExternalState, JobState,  # noqa: PLC0415
                                         UnsafeRetry, reconciliation_outcome)
    from social_farm.jobs import JobStoreError  # noqa: PLC0415

    path = ctx.path("ферма", "смерть-работника.sqlite3")
    store = _open_store(path)
    job = store.enqueue(account_id=OWNER_ACCOUNT, capability="media.publish",
                        idempotency_key="ночной-пост", now=T0)
    store.acquire(worker="работник-A", lease_seconds=30, now=T0)
    store.checkpoint(job.id, "PROVIDER_CONTAINER_CREATED", worker="работник-A", now=T0)
    store.transition(job.id, JobState.WAITING_PROVIDER, worker="работник-A",
                     external_state=ExternalState.UNKNOWN, now=T0)
    dead = T0 + timedelta(seconds=999)

    # Отрицательные контроли ПЕРВЫМИ: сверка потребляет состояние, и после неё
    # «следующему не отдаётся» доказывалось бы уже разобранной работой.
    ctx.negative("до разбора работа мертвеца НЕ выдаётся следующему работнику",
                 store.acquire(worker="работник-B", now=dead) is None
                 and store.get(job.id).state is JobState.WAITING_PROVIDER,
                 f"состояние={store.get(job.id).state.value}")
    ctx.refused("двигать чужую работу нельзя даже после смерти арендатора",
                lambda: store.transition(job.id, JobState.SUCCEEDED,
                                         worker="работник-B", now=dead),
                JobStoreError)
    ctx.refused("работник без имени не проходит за «аренды нет» (BL-115)",
                lambda: store.transition(job.id, JobState.SUCCEEDED, worker="",
                                         now=dead),
                JobStoreError)

    store = _restart(store, path)
    recovered = store.recover_stale(now=dead)
    ctx.positive("после срыва работа уходит на СВЕРКУ, а не на повтор и не в отказ",
                 [r.state for r in recovered] == [JobState.RECONCILING]
                 and store.get(job.id).lease_owner == "",
                 f"состояние={store.get(job.id).state.value}, аренда мертвеца снята")
    ctx.negative("и после снятия аренды она всё ещё не в очереди",
                 store.acquire(worker="работник-B", now=dead) is None,
                 "acquire отбирает только QUEUED")
    ctx.refused("повтор вслепую запрещён, пока внешнее состояние неизвестно",
                lambda: store.transition(job.id, JobState.RUNNING,
                                         worker="работник-B", now=dead),
                (UnsafeRetry, JobStoreError))

    claimed = store.claim_reconciliation(worker="сверщик", now=dead)
    ctx.positive("сверка МОЖЕТ забрать работу: из восстановления есть выход (BL-114)",
                 claimed is not None and claimed.id == job.id
                 and claimed.state is JobState.RECONCILING,
                 f"взял сверщик, состояние={claimed.state.value if claimed else '—'}")
    ctx.positive("достигнутая точка не потеряна сверкой",
                 claimed.checkpoint == "PROVIDER_CONTAINER_CREATED"
                 and claimed.attempts == 1,
                 f"этап={claimed.checkpoint}, попыток={claimed.attempts}")
    ctx.negative("второй сверщик ту же работу не получает",
                 store.claim_reconciliation(worker="сверщик-2", now=dead) is None,
                 "захват — один UPDATE с условием на «аренды нет»")

    state, external = reconciliation_outcome(effect_found=True)
    finished = store.transition(job.id, state, worker="сверщик",
                                external_state=external, now=dead)
    ctx.positive("исход сверки записан, и работа доведена до конца",
                 finished.state is JobState.SUCCEEDED and finished.terminal
                 and finished.external_state is ExternalState.CONFIRMED,
                 f"{finished.state.value}/{finished.external_state.value}")

    queued = store.enqueue(account_id=OWNER_ACCOUNT, capability="media.publish",
                           idempotency_key="обычная", now=T0)
    ctx.negative("дверь сверки открывается НЕ в очередь: обычную работу она не берёт",
                 store.claim_reconciliation(worker="сверщик", now=T0) is None
                 and store.get(queued.id).state is JobState.QUEUED,
                 f"состояние обычной работы={store.get(queued.id).state.value}")


# ------------------------------------------------------------------ OS-104
@scenario(id="OS-104", depth=PRODUCT_CONTRACTS)
def os104_ui_drift_downgrades_the_capability(ctx) -> None:
    """Дрейф интерфейса ПОНИЖАЕТ возможность, а не глотается молча.

    BL-105: пропавшая и неоднозначная цель поднимались мимо учёта, и понижение
    по самому частому виду дрейфа не срабатывало ВООБЩЕ. Обе половины нужны
    вместе: «понижать от всего» удовлетворяет первую (и отключает работающую
    возможность от одной перерисовки страницы), «не понижать никогда» —
    вторую.
    """
    from social_farm.browser import BrokenUi  # noqa: PLC0415
    from social_farm.browser.capabilities import (BrowserCapabilityState,  # noqa: PLC0415
                                                  FailureKind)
    from social_farm.domain.capability import CapabilityStatus  # noqa: PLC0415
    from social_farm.domain.errors import ErrorClass, ProviderError  # noqa: PLC0415

    async def drift() -> dict:
        out: dict = {}
        # Цель пропала: кнопки «Поделиться» на странице больше нет.
        dom, sess = _session(_feed_page(share_buttons=0))
        await sess.start()
        kinds = []
        for _ in range(3):
            try:
                await sess.click("media.publish.image")
            except BrokenUi as exc:
                kinds.append(exc.kind)
        record = sess.ledger.records.get("media.publish.image")
        out["missing"] = (kinds, record, sess.ledger.cooling_down("media.publish.image"),
                          sess.ledger.status_of("media.publish.image"),
                          sess.ledger.reason_of("media.publish.image"), list(dom.clicks))
        snapshot = sess.ledger.snapshot(adapter_version="fixture-1")
        out["expires"] = snapshot.capabilities["media.publish.image"].expires_at

        # Цель неоднозначна: две одинаковые кнопки, и выбирать наугад нельзя.
        dom2, sess2 = _session(_feed_page(share_buttons=2))
        await sess2.start()
        try:
            await sess2.click("media.publish.image")
            out["ambiguous"] = (None, None, list(dom2.clicks))
        except BrokenUi as exc:
            out["ambiguous"] = (exc.kind,
                                sess2.ledger.records["media.publish.image"],
                                list(dom2.clicks))

        # Гонка: цель подменилась между планом и нажатием. Это НЕ дрейф.
        from social_farm.browser import FixtureElement  # noqa: PLC0415
        dom3, sess3 = _session(_feed_page())
        await sess3.start()
        target = await sess3.plan("media.publish.image")
        dom3.page.elements[-1] = FixtureElement(tag="button", text="Удалить",
                                                attributes={"id": "share0"})
        try:
            await sess3.act(target)
            out["stale"] = (None, None, list(dom3.clicks))
        except ProviderError as exc:
            out["stale"] = (exc.error_class,
                            sess3.ledger.records["media.publish.image"],
                            list(dom3.clicks))

        # Успех обнуляет счётчик — и НЕ возвращает возможность в подтверждённые.
        dom4, sess4 = _session(_feed_page(share_buttons=0))
        await sess4.start()
        for _ in range(3):
            try:
                await sess4.click("media.publish.image")
            except BrokenUi:
                pass
        before = sess4.ledger.records["media.publish.image"].state
        dom4.page.elements.append(FixtureElement(tag="button", text="Поделиться",
                                                 attributes={"id": "share0"}))

        def landed(page, _element):
            page.text = "Публикации опубликовано"

        dom4.on_click = landed
        if sess4.state.value == "BROKEN_UI":
            sess4.recover_from_broken_ui()
        result = await sess4.click("media.publish.image")
        out["recovered"] = (before, sess4.ledger.records["media.publish.image"], result)

        # Отказы на СТАРОЙ версии пакета ничего не говорят о новой.
        dom5, sess5 = _session(_feed_page(share_buttons=0))
        await sess5.start()
        for _ in range(2):
            try:
                await sess5.click("media.publish.image")
            except BrokenUi:
                pass
        was = sess5.ledger.records["media.publish.image"].consecutive_failures
        sess5.ledger.record_failure("media.publish.image",
                                    selector_pack_version="2.0.0",
                                    kind=FailureKind.TARGET_MISSING)
        out["version"] = (was, sess5.ledger.records["media.publish.image"])
        return out

    got = asyncio.run(drift())

    kinds, record, cooling, status, reason, clicks = got["missing"]
    ctx.positive("пропавшая цель признаётся дрейфом интерфейса, а не невезением",
                 kinds == [FailureKind.TARGET_MISSING] * 3,
                 f"виды отказов={[k.value for k in kinds]}")
    ctx.positive("три отказа подряд ПОНИЖАЮТ возможность до BROKEN_UI_VERSION",
                 record.state is BrowserCapabilityState.BROKEN_UI_VERSION
                 and record.consecutive_failures == 3 and cooling,
                 f"{record.state.value}, подряд={record.consecutive_failures}")
    ctx.positive("понижение видно в матрице владельца и НАЗЫВАЕТ версию пакета",
                 status is CapabilityStatus.TEMPORARILY_DISABLED
                 and "1.0.0" in reason and "интерфейс провайдера сменился" in reason,
                 reason[:150])
    ctx.positive("срок отключения проставлен, а не «когда-нибудь»",
                 bool(got["expires"]) and got["expires"] == record.disabled_until,
                 f"до {got['expires']}")

    kind_amb, record_amb, clicks_amb = got["ambiguous"]
    ctx.positive("неоднозначная цель — тоже дрейф, и тоже считается",
                 kind_amb is FailureKind.TARGET_AMBIGUOUS
                 and record_amb.consecutive_failures == 1,
                 f"{kind_amb.value if kind_amb else '—'}, подряд={record_amb.consecutive_failures}")

    before_state, after_record, result = got["recovered"]
    ctx.positive("узнанный интерфейс обнуляет счётчик и снимает паузу",
                 before_state is BrowserCapabilityState.BROKEN_UI_VERSION
                 and after_record.consecutive_failures == 0
                 and after_record.disabled_until is None and result["ok"],
                 f"{before_state.value} → {after_record.state.value}")

    # --- отрицательные контроли
    ctx.negative("ни одного нажатия по дрейфующей странице не случилось",
                 clicks == [] and clicks_amb == [],
                 f"нажатий={clicks}/{clicks_amb}")
    ctx.negative("«ближайший» вариант не выбирается: неоднозначность — отказ",
                 kind_amb is FailureKind.TARGET_AMBIGUOUS and clicks_amb == [],
                 "выбор первой попавшейся означал бы «удалили не ту запись»")
    kind_stale, record_stale, clicks_stale = got["stale"]
    ctx.negative("гонка (устаревшая цель) счётчик НЕ двигает и нажатия не даёт",
                 kind_stale is ErrorClass.BROWSER_STALE_TARGET
                 and record_stale.consecutive_failures == 0 and clicks_stale == [],
                 f"{record_stale.reason[:90]}")
    ctx.negative("один удачный раз НЕ возвращает возможность в подтверждённые",
                 after_record.state is BrowserCapabilityState.EXPERIMENTAL,
                 f"состояние после успеха={after_record.state.value}")
    was, record_version = got["version"]
    ctx.negative("счётчик привязан к версии пакета: новая версия начинает с нуля",
                 was == 2 and record_version.consecutive_failures == 1
                 and record_version.failing_pack_version == "2.0.0",
                 f"было {was} на 1.0.0 → {record_version.consecutive_failures} на 2.0.0")


# ------------------------------------------------------------------ OS-105
@scenario(id="OS-105", depth=PRODUCT_CONTRACTS)
def os105_the_owners_account_never_mixes_with_another(ctx) -> None:
    """Аккаунт владельца не смешивается с чужим: работы, каталоги, сессия, конверт.

    Ошибка здесь стоит публикации от чужого имени, и откатить её нельзя.
    Поэтому проверяются все четыре опоры, а не одна: каждая по отдельности
    обходится, и ровно поэтому их четыре.
    """
    import os  # noqa: PLC0415
    import stat  # noqa: PLC0415

    from social_farm.browser import IdentityMismatch  # noqa: PLC0415
    from social_farm.browser.isolation import (MARKER_NAME,  # noqa: PLC0415
                                               AccountContextRoot,
                                               CrossAccountViolation, account_slug)
    from social_farm.browser.worker import (WorkerRequest,  # noqa: PLC0415
                                            guard_account)

    # 1. Работы. Две учётные записи в одной базе не видят работ друг друга.
    path = ctx.path("ферма", "два-аккаунта.sqlite3")
    store = _open_store(path, accounts=(OWNER_ACCOUNT, OTHER_ACCOUNT))
    mine = store.enqueue(account_id=OWNER_ACCOUNT, capability="media.publish",
                         idempotency_key="общий-ключ", now=T0)
    theirs = store.enqueue(account_id=OTHER_ACCOUNT, capability="media.publish",
                           idempotency_key="общий-ключ", now=T0)
    ctx.positive("работы владельца и чужие разделены, даже при одном ключе",
                 [r.id for r in store.by_account(OWNER_ACCOUNT)] == [mine.id]
                 and [r.id for r in store.by_account(OTHER_ACCOUNT)] == [theirs.id],
                 f"у владельца {len(store.by_account(OWNER_ACCOUNT))}, "
                 f"у чужого {len(store.by_account(OTHER_ACCOUNT))}")

    # 2. Каталоги контекстов: путь, маркер владельца, права.
    root = AccountContextRoot(root=ctx.path("контексты", "корень"))
    mine_dir = root.prepare(OWNER_ACCOUNT)
    theirs_dir = root.prepare(OTHER_ACCOUNT)
    ctx.positive("у каждого аккаунта свой каталог с маркером владельца и правами 0700",
                 mine_dir != theirs_dir
                 and root.owner_of(mine_dir) == OWNER_ACCOUNT
                 and root.owner_of(theirs_dir) == OTHER_ACCOUNT
                 and stat.S_IMODE(mine_dir.stat().st_mode) == 0o700,
                 f"{mine_dir.name} / {theirs_dir.name}, "
                 f"права {oct(stat.S_IMODE(mine_dir.stat().st_mode))}")
    ctx.positive("имена, различимые только запрещёнными символами, не делят каталог",
                 account_slug("акк/1") != account_slug("акк:1"),
                 f"{account_slug('акк/1')} ≠ {account_slug('акк:1')}")

    # 3. Сессия: личность проверена ДО готовности к действиям.
    dom, sess = _session(_feed_page())
    ctx.positive("сессия доходит до READY, только увидев ожидаемую личность",
                 asyncio.run(sess.start()).value == "READY"
                 and sess.observed_identity == IDENTITY
                 and any(r.action == "identity.verify" and r.result == "ok"
                         for r in sess.audit.records),
                 f"личность={sess.observed_identity}, запись в аудите есть")

    # --- отрицательные контроли
    ctx.refused("каталог чужого аккаунта не обслуживает владельца",
                lambda: root.assert_owned(OTHER_ACCOUNT, mine_dir),
                CrossAccountViolation)
    (mine_dir / MARKER_NAME).write_text(OTHER_ACCOUNT, encoding="utf-8")
    ctx.refused("подменённый маркер отвергается перед КАЖДЫМ открытием контекста",
                lambda: root.assert_owned(OWNER_ACCOUNT, mine_dir),
                CrossAccountViolation)
    (mine_dir / MARKER_NAME).unlink()
    ctx.refused("каталог без маркера владельца не открывается вовсе",
                lambda: root.assert_owned(OWNER_ACCOUNT, mine_dir),
                CrossAccountViolation)
    (mine_dir / MARKER_NAME).write_text(OWNER_ACCOUNT, encoding="utf-8")
    os.chmod(mine_dir, 0o755)
    ctx.refused("каталог сессии, открытый другим пользователям машины, отвергается",
                lambda: root.assert_private(mine_dir), PermissionError)
    os.chmod(mine_dir, 0o700)

    def reassign_account() -> None:
        sess.account_id = OTHER_ACCOUNT

    ctx.refused("аккаунт сессии не переставляется присваиванием",
                reassign_account, AttributeError)
    ctx.negative("аккаунт и ожидаемая личность сессии остались прежними",
                 sess.account_id == OWNER_ACCOUNT and sess.expected_identity == IDENTITY,
                 f"{sess.account_id}/{sess.expected_identity}")

    foreign_dom, foreign_sess = _session(_feed_page(identity=FOREIGN_IDENTITY))
    ctx.refused("чужой аккаунт в контексте ОСТАНАВЛИВАЕТ сессию, а не «на всякий случай»",
                lambda: asyncio.run(foreign_sess.start()), IdentityMismatch)
    ctx.negative("остановленная сессия не сделала ни одного действия",
                 foreign_sess.state.value == "STOPPED"
                 and foreign_dom.clicks == [] and foreign_dom.fills == []
                 and any(r.result == "mismatch" for r in foreign_sess.audit.records),
                 f"состояние={foreign_sess.state.value}, нажатий={foreign_dom.clicks}")
    ctx.refused("конверт с чужим аккаунтом отвергается воркером до исполнения",
                lambda: guard_account(OWNER_ACCOUNT, WorkerRequest(
                    id="1", account_id=OTHER_ACCOUNT, op="click")),
                CrossAccountViolation)


# ------------------------------------------------------------------ OS-106
@scenario(id="OS-106", depth=PRODUCT_CONTRACTS)
def os106_a_human_check_stops_the_product_and_is_named(ctx) -> None:
    """Проверка «ты человек» ОСТАНАВЛИВАЕТ работу и НАЗЫВАЕТСЯ владельцу.

    Капча — осознанно поставленный владельцем площадки контроль доступа, и
    проходить её автоматом приложение обещало не делать. Проверяется не только
    то, что она не пройдена, но и то, что владелец узнаёт, ИЗ-ЗА ЧЕГО его
    позвали: остановленная работа без причины — это тот же молчаливый тупик.
    """
    from social_farm.browser import ChallengeKind, detect_challenge  # noqa: PLC0415
    from social_farm.browser.capabilities import FailureKind  # noqa: PLC0415
    from social_farm.domain.errors import ErrorClass, ProviderError  # noqa: PLC0415

    captcha_markup = '<div class="g-recaptcha" data-sitekey="x"></div>'

    async def run() -> dict:
        out: dict = {}
        dom, sess = _session(_feed_page())
        await sess.start()
        dom.page.markup = captcha_markup
        try:
            await sess.click("media.publish.image")
            out["stopped"] = None
        except ProviderError as exc:
            out["stopped"] = exc
        out["state"] = sess.state
        out["challenge"] = sess.challenge
        out["clicks"], out["fills"] = list(dom.clicks), list(dom.fills)
        out["ledger"] = sess.ledger.records.get("media.publish.image")
        out["audit"] = [r for r in sess.audit.records if r.action == "takeover.request"]
        # Человек ещё не закончил — проверка на странице осталась.
        out["early"] = await sess.complete_takeover()
        dom.page.markup = _FEED_MARKUP.format(identity=IDENTITY)
        out["after_human"] = await sess.complete_takeover()

        # Чистая страница: детектор обязан молчать, иначе он говорит «да» всему.
        clean_dom, clean_sess = _session(_feed_page())
        await clean_sess.start()
        out["clean"] = (await clean_sess.snapshot()).challenge
        out["clean_state"] = clean_sess.state
        return out

    got = asyncio.run(run())
    failure = got["stopped"]
    ctx.positive("продукт ОСТАНОВИЛСЯ штатным отказом, а не исключением наружу",
                 isinstance(failure, ProviderError)
                 and failure.error_class is ErrorClass.BROWSER_REQUIRES_TAKEOVER,
                 f"{type(failure).__name__}: "
                 f"{failure.error_class.value if failure else '—'}")
    ctx.positive("сессия ждёт человека, а не крутится по странице",
                 got["state"].value == "TAKEOVER_REQUIRED", got["state"].value)
    ctx.positive("проверка НАЗВАНА: вид, сервис и чем именно опознана",
                 got["challenge"].present
                 and got["challenge"].kind is ChallengeKind.CAPTCHA
                 and got["challenge"].provider == "Google reCAPTCHA"
                 and got["challenge"].evidence == "разметка страницы",
                 f"{got['challenge'].kind.value}/{got['challenge'].provider}")
    ctx.positive("владельцу сказано, ЧТО делать, и что автоматом это не проходится",
                 "не проходится" in (failure.user_action or "")
                 and "reCAPTCHA" in (failure.safe_detail or ""),
                 (failure.user_action or "")[:110])
    ctx.positive("вызов человека остался в аудите отдельной записью",
                 len(got["audit"]) == 1
                 and got["audit"][0].error_class
                 == ErrorClass.BROWSER_REQUIRES_TAKEOVER.value,
                 f"записей takeover.request={len(got['audit'])}")
    ctx.positive("после того как человек закончил, сессия возвращается в работу",
                 got["after_human"].value == "READY", got["after_human"].value)

    for text, expected in (("Подтвердите, что вы человек", ChallengeKind.CAPTCHA),
                           ("Введите код подтверждения", ChallengeKind.TWO_FACTOR),
                           ("Подозрительный вход в аккаунт",
                            ChallengeKind.SECURITY_CHECKPOINT)):
        found = detect_challenge(text=text)
        ctx.positive(f"самописная проверка «{text[:24]}…» тоже опознана",
                     found.present and found.kind is expected,
                     f"{found.kind.value}/{found.provider}")

    # --- отрицательные контроли
    ctx.negative("по странице с проверкой не было НИ ОДНОГО нажатия и ввода",
                 got["clicks"] == [] and got["fills"] == [],
                 f"нажатий={got['clicks']}, вводов={got['fills']}")
    ctx.negative("проверка человека не считается поломкой интерфейса",
                 got["ledger"] is not None
                 and got["ledger"].consecutive_failures == 0
                 and FailureKind.TAKEOVER_REQUIRED.value in got["ledger"].reason,
                 got["ledger"].reason[:110] if got["ledger"] else "записи нет")
    ctx.negative("пока проверка на экране, «человек закончил» не срабатывает",
                 got["early"].value == "TAKEOVER_REQUIRED", got["early"].value)
    ctx.negative("на обычной странице проверок НЕ находится: детектор не говорит «да» всему",
                 got["clean"].present is False
                 and got["clean"].kind is ChallengeKind.NONE
                 and got["clean_state"].value == "READY",
                 f"{got['clean'].kind.value}, состояние={got['clean_state'].value}")
