"""Владельческие сценарии 107–110: Telegram Companion глубже круга одобрения.

Круг одобрения целиком и чужая подпись на табло уже есть — OS-15 и OS-39.
Здесь другое: что происходит с мостом, пока владелец СПИТ. Четыре вопроса,
каждый из которых стоит либо утечки, либо второго исполнения:

* 107 — сообщение владельцу не несёт ни одного из его ключей и не выдаёт
  чужого приватного поля: проверяется НАСТОЯЩИМ адаптером через
  `httpx.MockTransport`, то есть тем самым кодом, который пишет в сеть;
* 108 — связь оборвалась посреди поручения: ожидающее одобрение ДОЖДАЛОСЬ
  владельца, а оборванная отправка помечена «неизвестно» и не переиграна;
* 109 — два устройства владельца не дают двух исполнений одного одобрения, и
  отказ второму — ШТАТНЫЙ, а не исключение наружу;
* 110 — исполнитель, сменившийся пока владелец спал, даёт штатный отказ, а не
  тихую подмену: поручение уходит ровно тому агенту, которого владелец видел.

ЧТО ЗДЕСЬ НАСТОЯЩЕЕ. Хранилище компаньона — настоящий SQLite с настоящим
`Vault`; перезапуск изображается закрытием соединения и открытием нового с тем
же каталогом. Транспорт Telegram и транспорт к ядру — настоящие классы
`bcc.telegram_companion.adapters`, у которых подменён только HTTP-перенос
(`httpx.MockTransport`): живого бота и живого ядра в прогоне нет, а весь код
проверок, отпечатков и редакции — тот самый.

ЧЕГО ЗДЕСЬ НЕТ. Двойника вместо проверяемого: ни одна проверка не зеленеет от
заглушки. Подменён исключительно перенос байтов.

ГЛУБИНА УЛИКИ — `product_contracts`: цепочка идёт по классам ветки, а не через
установленный у владельца продукт с живым ботом.

ЖИВОГО ШАГА МОДЕЛИ НЕТ: все четыре объявлены `model_step: "none"`.

Зависимости модуля — стандартная библиотека. `bcc.*`, `bossman.*` и `httpx`
импортируются ВНУТРИ функций и объявлены в реестре (BL-085).
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(REPO_ROOT / "command-center"))
sys.path.insert(0, str(REPO_ROOT / "bossman-core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scenario_runner import PRODUCT_CONTRACTS, scenario  # noqa: E402

OWNER_ID = 11_111
GUEST_ID = 22_222
AGENT_ID = 10
TASK_ID = 42
CREATED_AT = "2026-09-19T03:00:00+00:00"


def _assembled(*parts: str) -> str:
    """Подставное значение СОБИРАЕТСЯ из кусков.

    Настоящие ключи берутся только из окружения. Подставные не лежат в
    исходнике целиком намеренно: строка, похожая на ключ, в репозитории
    неотличима от настоящей ни для сканера, ни для человека, а проверка
    «токен не утёк» обязана искать то, чего в файле нет одним куском.
    """
    return "".join(parts)


def _secrets() -> dict[str, str]:
    return {
        "bot_token": _assembled("13579", "2468", ":", "Qw" * 12, "zZ9"),
        "core_token": _assembled("cor", "e-", "Mm" * 14),
        "cloud_token": _assembled("sk-", "or-", "Cc" * 14),
        "local_token": _assembled("loc", "al-", "Ll" * 14),
    }


def _people():
    from bcc.telegram_companion.config import Person  # noqa: PLC0415

    return (Person(OWNER_ID, OWNER_ID, "owner", AGENT_ID),
            Person(GUEST_ID, GUEST_ID, "guest"))


def _settings(**extra):
    from bcc.telegram_companion.config import Settings  # noqa: PLC0415

    return Settings(_people(), local_model="локальная-фикстура", **extra)


def _message(person, text: str, update_id: int) -> dict:
    return {"from": {"id": person.user_id, "is_bot": False},
            "chat": {"id": person.chat_id, "type": "private"},
            "text": text, "_update_id": update_id}


def _nonce_from(preview: str) -> str:
    """Код подтверждения из того самого текста, который увидел владелец."""
    return preview.split("/confirm ")[1].split()[0]


# ------------------------------------------------------- настоящий адаптер ядра
def _core(state: dict):
    """Настоящий `Core` поверх подменённого переноса байтов.

    Подменена ТОЛЬКО сеть: отпечаток исполнителя, привязка задачи и проверка
    личности ядра считаются кодом продукта.
    """
    import httpx  # noqa: PLC0415

    from bcc.telegram_companion.adapters import Core  # noqa: PLC0415

    def serve(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health/live":
            return httpx.Response(200, json={"app": "bossman-command-center",
                                             "alive": True, "source_sha": "ветка"})
        if path == "/api/agents":
            return httpx.Response(200, json=[state["agent"]])
        if path == "/api/tasks" and request.method == "POST":
            body = json.loads(request.content)
            task = {"id": TASK_ID, "agent_id": body["agent_id"],
                    "prompt": body["prompt"], "created_at": state["created_at"],
                    "status": "queued"}
            state["tasks"][TASK_ID] = task
            state["submitted"].append(body["prompt"])
            return httpx.Response(200, json={"task": task})
        if path.startswith("/api/tasks/"):
            task = state["tasks"].get(int(path.rsplit("/", 1)[1]))
            if task is None:
                return httpx.Response(404, json={"error": "нет такой задачи"})
            served = dict(task, status="completed", created_at=state["created_at"])
            return httpx.Response(200, json={"task": served,
                                             "result": state["result"]})
        return httpx.Response(404, json={"error": "неизвестный путь"})

    return Core(_settings(**_secrets()), transport=httpx.MockTransport(serve))


def _core_state() -> dict:
    return {"agent": {"id": AGENT_ID, "enabled": True, "model_id": "локальная-1",
                      "fallback_model_id": None, "tools": [], "permissions": [],
                      "budget_usd": 1.0},
            "tasks": {}, "submitted": [], "created_at": CREATED_AT,
            "result": "приватный результат владельца"}


class _NoModels:
    """Модели в этих четырёх сценариях не участвуют вовсе."""

    async def answer(self, text, history, *, cloud_consent):  # noqa: D102
        raise AssertionError("шаг модели в этом сценарии не объявлен и не нужен")

    async def search(self, query):  # noqa: D102
        raise AssertionError("поиск в этом сценарии не объявлен и не нужен")


# ------------------------------------------------------------------ OS-107
@scenario(id="OS-107", depth=PRODUCT_CONTRACTS)
def os107_the_message_to_the_owner_carries_no_key(ctx) -> None:
    """Сообщение владельцу: ни одного ключа и ни одного чужого приватного поля.

    Проверяется то, что РЕАЛЬНО уходит в сеть: тело запроса перехватывается на
    уровне переноса, уже после `scrub` и `_egress_guard_text`. Проверка
    «функция редакции работает» доказывала бы функцию, а не отправку.
    """
    import httpx  # noqa: PLC0415

    from bcc.telegram_companion.adapters import Telegram  # noqa: PLC0415
    from bcc.telegram_companion.config import CompanionError  # noqa: PLC0415
    from bcc.telegram_companion.service import Companion  # noqa: PLC0415
    from bcc.telegram_companion.store import Store  # noqa: PLC0415

    owner, guest = _people()
    secrets = _secrets()
    settings = _settings(**secrets)
    sent: list[dict] = []

    def serve(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    telegram = Telegram(settings, transport=httpx.MockTransport(serve))
    leaky = (f"Отчёт за ночь. Бот {secrets['bot_token']}, ядро {secrets['core_token']}, "
             f"облако {secrets['cloud_token']}, локальный {secrets['local_token']}. "
             f"Адрес https://api.telegram.org/bot{secrets['bot_token']}/getMe")

    async def deliver() -> None:
        try:
            await telegram.send(owner, leaky)
        finally:
            await telegram.close()

    asyncio.run(deliver())
    body = sent[0] if sent else {}
    flat = json.dumps(body, ensure_ascii=False)
    ctx.positive("сообщение владельцу ушло и дошло до переноса",
                 len(sent) == 1 and bool(body.get("text")),
                 f"отправок={len(sent)}")
    ctx.positive("адресат — ровно тот чат, которому писали",
                 body.get("chat_id") == owner.chat_id,
                 f"chat_id={body.get('chat_id')}")
    ctx.positive("смысл сообщения уцелел: вычищены ключи, а не текст",
                 "Отчёт за ночь" in body.get("text", ""),
                 body.get("text", "")[:80])

    # --- отрицательные контроли
    for name, value in secrets.items():
        ctx.negative(f"в теле запроса нет значения {name}",
                     value not in flat, f"{name}: {len(value)} символов, не найдено")
    ctx.negative("адрес с токеном бота тоже вычищен, а не только голый токен",
                 "/bot" + secrets["bot_token"] not in flat
                 and "[REDACTED]" in body.get("text", ""),
                 body.get("text", "")[-60:])
    ctx.negative("разметка не включена: текст владельца уходит буквально",
                 "parse_mode" not in body and body.get("disable_web_page_preview") is True,
                 f"ключи тела={sorted(body)}")
    ctx.negative("ключи не показываются и в самом описании настроек",
                 not any(value in repr(settings) for value in secrets.values()),
                 repr(settings)[:90])

    # Чужие приватные поля: гостю не выдаются ни компьютер владельца, ни его задачи.
    state = _core_state()
    store = Store(ctx.path("компаньон", "чужие-поля"))
    app = Companion(settings, store, telegram, _core(state), _NoModels())

    async def foreign() -> tuple[str, str, str]:
        preview = await app.handle(owner, _message(owner, "/task ночная сводка", 1))
        await app.handle(owner, _message(owner, "/confirm " + _nonce_from(preview), 2))
        mine = await app.handle(owner, _message(owner, f"/result {TASK_ID}", 3))
        theirs = await app.handle(guest, _message(guest, f"/result {TASK_ID}", 4))
        status = await app.handle(guest, _message(guest, "/status", 5))
        return mine, theirs, status

    mine, theirs, status = asyncio.run(foreign())
    ctx.positive("владелец свой результат получает",
                 state["result"] in mine, mine[:70])
    ctx.negative("чужую задачу гостю не выдают, и результат в ответ не попал",
                 state["result"] not in theirs and "Чужие задачи" in theirs,
                 theirs[:90])
    ctx.negative("состояние компьютера владельца гостю не выдают",
                 "только владельцу" in status, status[:90])

    # Отозванный получатель: до отправки дело не доходит.
    revoked = Companion(settings, store, telegram, _core(state), _NoModels(),
                        policy_provider=lambda: _settings(**secrets).__class__(
                            (guest,), local_model="локальная-фикстура", **secrets))
    before = len(sent)

    async def to_revoked() -> None:
        await revoked.telegram.send(owner, "это не должно уйти")

    ctx.refused("отозванному получателю сообщение не отправляется вовсе",
                lambda: asyncio.run(to_revoked()), CompanionError)
    ctx.negative("отправок не прибавилось: отказ случился ДО запроса",
                 len(sent) == before, f"было {before}, стало {len(sent)}")
    store.close()


# ------------------------------------------------------------------ OS-108
@scenario(id="OS-108", depth=PRODUCT_CONTRACTS)
def os108_a_pending_approval_waits_for_the_owner(ctx) -> None:
    """Потерянная связь не теряет ожидающее одобрение и не исполняет его сама.

    Перезапуск НАСТОЯЩИЙ: соединение с базой компаньона закрывается, новое
    открывается с тем же каталогом и ключом. Две половины проверяются вместе —
    «ничего не терять» удовлетворяется тем, кто всё переигрывает, а «ничего не
    переигрывать» — тем, кто всё теряет.
    """
    from bcc.telegram_companion.config import CompanionError  # noqa: PLC0415
    from bcc.telegram_companion.store import Store  # noqa: PLC0415

    owner, _guest = _people()
    home = ctx.path("компаньон", "обрыв")
    store = Store(home)
    waiting = store.propose(owner.key, {"prompt": "опубликовать утром",
                                        "executor": "отпечаток", "agent_id": AGENT_ID})
    store.ingest(900, owner.key, _message(owner, "привет", 900))
    claimed = store.claim(owner.key, "chat")
    burnt = store.propose(owner.key, {"prompt": "уже отправленное",
                                      "executor": "отпечаток", "agent_id": AGENT_ID})
    store.consume(owner.key, burnt)          # отправка пошла — и связь оборвалась
    store.close()

    # --- смерть процесса
    restarted = Store(home)
    restarted.recover()

    def phase(nonce: str) -> str:
        row = restarted.db.execute("SELECT phase FROM proposals WHERE id=?",
                                   (nonce,)).fetchone()
        return row["phase"] if row else "строки нет"

    inbox_phase = restarted.db.execute("SELECT phase FROM inbox WHERE id=?",
                                       (900,)).fetchone()["phase"]
    ctx.positive("ожидающее одобрение пережило обрыв и всё ещё ждёт владельца",
                 phase(waiting) == "pending", f"фаза={phase(waiting)}")
    ctx.positive("владелец по-прежнему может подтвердить именно его",
                 restarted.consume(owner.key, waiting)["prompt"] == "опубликовать утром",
                 "поручение прочитано из пережившей обрыв записи")
    ctx.positive("оборванная отправка названа НЕИЗВЕСТНОЙ, а не успешной",
                 phase(burnt) == "dispatch_unknown", f"фаза={phase(burnt)}")
    ctx.positive("сообщение, застигнутое в работе, помечено как прерванное",
                 claimed is not None and inbox_phase == "interrupted_unknown",
                 f"фаза входящего={inbox_phase}")

    # --- отрицательные контроли
    ctx.negative("оборванная отправка НЕ стала задачей сама собой",
                 restarted.db.execute("SELECT task_id FROM proposals WHERE id=?",
                                      (burnt,)).fetchone()["task_id"] is None
                 and restarted.owns_task(owner.key, TASK_ID) is False,
                 "task_id пуст, задача чату не принадлежит")
    ctx.refused("сгоревший код после перезапуска не подтверждается заново",
                lambda: restarted.consume(owner.key, burnt), CompanionError)
    ctx.negative("прерванное сообщение не переигрывается новым работником",
                 restarted.claim(owner.key, "chat") is None,
                 "очередь пуста: повтор внешнего эффекта не устраивается")
    expired = restarted.propose(owner.key, {"prompt": "протухшее"})
    restarted.db.execute("UPDATE proposals SET expires=0 WHERE id=?", (expired,))
    ctx.refused("протухшее одобрение не оживает перезапуском",
                lambda: restarted.consume(owner.key, expired), CompanionError)
    restarted.close()


# ------------------------------------------------------------------ OS-109
@scenario(id="OS-109", depth=PRODUCT_CONTRACTS)
def os109_two_devices_give_one_execution(ctx) -> None:
    """Два устройства владельца — одно исполнение. Отказ второму ШТАТНЫЙ.

    Оба устройства представлены НЕЗАВИСИМЫМИ соединениями с одним файлом базы:
    один объект в памяти доказывал бы работу переменной, а не транзакции.
    """
    from bcc.telegram_companion.config import CompanionError  # noqa: PLC0415
    from bcc.telegram_companion.service import Companion  # noqa: PLC0415
    from bcc.telegram_companion.store import Store  # noqa: PLC0415

    owner, guest = _people()
    home = ctx.path("компаньон", "два-устройства")
    phone = Store(home)
    laptop = Store(home)          # второе соединение = второй процесс
    state = _core_state()
    app = Companion(_settings(**_secrets()), phone, None, _core(state), _NoModels())

    preview = asyncio.run(app.handle(owner, _message(owner, "/task опубликовать пост", 1)))
    nonce = _nonce_from(preview)
    ctx.positive("до подтверждения не отправлено ничего",
                 state["submitted"] == [] and "опубликовать пост" in preview,
                 f"отправок={len(state['submitted'])}")

    first = phone.consume(owner.key, nonce)
    ctx.positive("первое устройство потребило код и получило поручение",
                 first["prompt"] == "опубликовать пост", first["prompt"])
    dispatching = phone.db.execute(
        "SELECT count(*) AS n FROM proposals WHERE phase='dispatching'").fetchone()["n"]
    ctx.positive("ровно ОДНА запись ушла в отправку",
                 dispatching == 1, f"в отправке={dispatching}")

    # --- отрицательные контроли
    ctx.refused("второе устройство получает ШТАТНЫЙ отказ, а не второе исполнение",
                lambda: laptop.consume(owner.key, nonce), CompanionError)
    ctx.negative("после отказа второму в отправке по-прежнему одна запись",
                 laptop.db.execute(
                     "SELECT count(*) AS n FROM proposals WHERE phase='dispatching'"
                 ).fetchone()["n"] == 1,
                 "вторая отправка не заведена")
    ctx.refused("чужой чат тем же кодом не пользуется",
                lambda: laptop.consume(guest.key, nonce), CompanionError)

    fresh = phone.propose(owner.key, {"prompt": "протухнет", "agent_id": AGENT_ID})
    phone.db.execute("UPDATE proposals SET expires=0 WHERE id=?", (fresh,))
    ctx.refused("истёкший код не потребляется ни одним устройством",
                lambda: laptop.consume(owner.key, fresh), CompanionError)

    for bad in ("короткий", _assembled("z", "z" * 11), _assembled("A", "b" * 11)):
        ctx.refused(f"мусорный код {bad[:6]!r} отвергается до хранилища",
                    lambda bad=bad: asyncio.run(
                        app.handle(owner, _message(owner, "/confirm " + bad, 9))),
                    CompanionError)
    ctx.negative("ни один отказ не превратился в отправку",
                 state["submitted"] == [], f"отправок={len(state['submitted'])}")
    phone.close()
    laptop.close()


# ------------------------------------------------------------------ OS-110
@scenario(id="OS-110", depth=PRODUCT_CONTRACTS)
def os110_a_changed_executor_refuses_by_name(ctx) -> None:
    """Сменившийся исполнитель — штатный отказ, а не тихая подмена.

    Между показом поручения и подтверждением проходит ночь. За неё настройки
    исполнителя могли поменяться: другая модель, другие права, другой бюджет.
    Отправить «примерно то же самое» другому агенту хуже, чем не отправить: у
    него другие права, и владелец этого не видел.
    """
    from bcc.telegram_companion.adapters import task_identity  # noqa: PLC0415
    from bcc.telegram_companion.config import CompanionError  # noqa: PLC0415
    from bcc.telegram_companion.service import Companion  # noqa: PLC0415
    from bcc.telegram_companion.store import Store  # noqa: PLC0415

    owner, _guest = _people()
    state = _core_state()
    store = Store(ctx.path("компаньон", "смена-исполнителя"))
    app = Companion(_settings(**_secrets()), store, None, _core(state), _NoModels())

    preview = asyncio.run(app.handle(owner, _message(owner, "/task опубликовать в 3 ночи", 1)))
    nonce = _nonce_from(preview)
    ctx.positive("поручение показано ЦЕЛИКОМ и ещё не отправлено",
                 "опубликовать в 3 ночи" in preview and "НЕ отправлено" in preview
                 and state["submitted"] == [],
                 f"отправок={len(state['submitted'])}")

    state["agent"]["model_id"] = "другая-модель"   # владелец переставил исполнителя

    def confirm(code: str) -> str:
        return asyncio.run(app.handle(owner, _message(owner, "/confirm " + code, 2)))

    ctx.refused("сменившийся исполнитель даёт ШТАТНЫЙ отказ",
                lambda: confirm(nonce), CompanionError)
    ctx.negative("ни одной отправки при этом не состоялось",
                 state["submitted"] == [] and state["tasks"] == {},
                 f"отправок={len(state['submitted'])}, задач={len(state['tasks'])}")
    ctx.negative("одноразовый код сгорел: переигрывание не проходит",
                 store.db.execute("SELECT phase FROM proposals WHERE id=?",
                                  (nonce,)).fetchone()["phase"] == "dispatch_unknown",
                 "фаза dispatch_unknown")
    ctx.refused("тот же код второй раз не подтверждается",
                lambda: confirm(nonce), CompanionError)

    # Положительная половина: с тем же исполнителем поручение уходит РОВНО РАЗ.
    good = _nonce_from(asyncio.run(
        app.handle(owner, _message(owner, "/task обычная работа", 3))))
    accepted = asyncio.run(app.handle(owner, _message(owner, "/confirm " + good, 4)))
    ctx.positive("при неизменном исполнителе поручение принято и отправлено один раз",
                 state["submitted"] == ["обычная работа"]
                 and f"#{TASK_ID}" in accepted, accepted[:80])
    ctx.positive("«принято» не выдаётся за «выполнено»",
                 "ещё не завершение" in accepted and "queued" in accepted,
                 accepted[:110])

    binding = store.task_binding(owner.key, TASK_ID)
    ctx.positive("задача привязана отпечатком её НЕИЗМЕНЯЕМЫХ полей создания",
                 len(binding) == 64
                 and binding == task_identity(state["tasks"][TASK_ID]),
                 f"отпечаток из {len(binding)} символов")

    # Подменённая задача под тем же номером не выдаётся.
    state["created_at"] = "2026-09-19T04:00:00+00:00"
    ctx.refused("задача с подменёнными полями создания владельцу не выдаётся",
                lambda: asyncio.run(
                    app.handle(owner, _message(owner, f"/result {TASK_ID}", 5))),
                CompanionError)
    state["created_at"] = CREATED_AT
    ctx.negative("отпечаток различает подмену полей, а не только номер",
                 task_identity(dict(state["tasks"][TASK_ID],
                                    prompt="другое поручение")) != binding
                 and task_identity(dict(state["tasks"][TASK_ID],
                                        agent_id=AGENT_ID + 1)) != binding,
                 "подпись меняется от prompt и от agent_id")
    ctx.refused("задача без обязательных полей создания опознанию не подлежит",
                lambda: task_identity({"id": TASK_ID, "agent_id": AGENT_ID,
                                       "prompt": "x", "created_at": ""}),
                CompanionError)
    store.close()
