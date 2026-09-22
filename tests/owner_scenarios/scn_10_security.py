"""Владельческие сценарии 36–40: границы безопасности.

Пять границ, каждая из которых защищает владельца от конкретной беды:

* 36 — секрет не попадает ни в журнал, ни в событие, ни в артефакт. Проверяется
  НАСТОЯЩИМ поиском по выводу (`tools/ci_secret_scan.py` + прямой поиск
  подстроки), а не чтением кода чистки;
* 37 — защищённые пути неизменяемы для чужого кода: рабочая область отвергает
  запись, а проверка области — изменённый файл;
* 38 — приватный режим не выпускает данные наружу даже запасным путём;
* 39 — подпись вебхука Telegram проверяется, и чужая даёт ШТАТНЫЙ отказ (BL-095);
* 40 — одобрение потребляется ровно один раз (BL-090/BL-094).

ПОРЯДОК ПОЛОВИН. Там, где положительный случай расходует одноразовый ресурс —
одобрение, кнопку Telegram, — отрицательный контроль стоит ПЕРВЫМ. Иначе
«подпись проверяется» доказывалось бы уже потреблённой кнопкой: отказ мог бы
означать не проверку подписи, а то, что кнопку уже нажали.

Ключи здесь ФИКТИВНЫЕ и собираются из кусков в момент прогона: ни одно значение
не лежит в исходнике целиком, не берётся из окружения владельца и не попадает в
отчёт — в улики идут только места и числа.

Тяжёлые зависимости (`bcc.*`, `bossman.*`) импортируются ВНУТРИ функций, а
способности объявлены в реестре: BL-085 — корневой CI ставит только
pytest/pytest-timeout/psutil/httpx/pyyaml, и без способности раннер обязан
отдать честный вердикт вместо исполнения.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "command-center"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bossman-core"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scenario_runner import PRODUCT_CONTRACTS, scenario  # noqa: E402

#: Фиктивные значения владельца. Собираются из кусков, чтобы ни один «ключ» не
#: лежал в исходнике целиком и не отдавался за настоящий ни человеком, ни
#: сканером секретов.
FAKE_KEY = "sk-" + "or-v1-" + "fake0000deadbeef0000fake0000deadbeef00"
FAKE_OWNER_TOKEN = "bcc-owner-token-" + "0123456789abcdef"


# ------------------------------------------------------------------ 36
@scenario(id="OS-36", depth=PRODUCT_CONTRACTS)
def os36_secret_never_reaches_journal_event_or_artifact(ctx) -> None:
    """Ключ владельца в команде на подтверждение — и поиск по ТРЁМ выводам продукта.

    Цепочка ровно та, что бывает у владельца: команда с заголовком
    `Authorization: Bearer …` требует подтверждения, продукт кладёт её предпросмотр
    в очередь, оттуда — в живую ленту и в историю, а диагностический архив потом
    уезжает в переписку.

    ИЗМЕРЕНО НА ЭТОЙ ВЕТКЕ: архив чист, а лента и история — НЕТ. Чистка шины
    (`command-center/bcc/events.py:41`, `redact(data)`) работает по ИМЕНАМ ключей,
    поэтому `api_key=…` вычищается, а тот же ключ внутри значения `preview`
    (собранного в `command-center/bcc/features/terminal.py:120` из команды
    владельца) уходит подписчикам WS и ложится в таблицу `events` как есть.
    Текстовый чистильщик у продукта УЖЕ есть — `bcc/plugin_security.py:268
    redact_text`, им пользуется диагностический архив. Сценарий остаётся красным
    до тех пор, пока шина им не воспользуется: это пробел продукта, а не повод
    сузить проверку.
    """
    import asyncio  # noqa: PLC0415
    import zipfile  # noqa: PLC0415

    import sqlalchemy as sa  # noqa: PLC0415

    import ci_secret_scan as scanner  # noqa: PLC0415
    from bcc import desktop  # noqa: PLC0415
    from bcc.approvals import Approvals  # noqa: PLC0415
    from bcc.db import Database, events as events_t, rows_dicts  # noqa: PLC0415
    from bcc.events import EventBus  # noqa: PLC0415
    from bcc.features import diag_bundle  # noqa: PLC0415

    # Предпросмотр собирается ровно так, как его собирает продукт
    # (command-center/bcc/features/terminal.py:120): режим, команда, каталог.
    command = f'curl -H "Authorization: Bearer {FAKE_KEY}" https://api.example/v1/models'
    preview = f"[sandbox] {command}\ncwd: /данные/проект"

    # КОНТРОЛЬ САМОГО ПОИСКА. «Не нашли» значит что-то только если тем же поиском
    # находится подсаженный секрет.
    raw_hits = scanner.scan_text(preview, "команда.txt", entropy=True)
    ctx.positive("поиск по выводу НЕ слепой: в сыром тексте ключ находится",
                 any("openrouter key" in hit for hit in raw_hits) and FAKE_KEY in preview,
                 f"находок сканера в сыром тексте={len(raw_hits)}")

    data_dir = ctx.path("данные", "держатель").parent
    log = desktop._run_log_path(data_dir)
    log.write_text(f"[окно] запуск: {command}\n[окно] токен владельца {FAKE_OWNER_TOKEN}\n",
                   encoding="utf-8")

    async def run():
        database = Database(f"sqlite+aiosqlite:///{data_dir / 'bcc.db'}")
        await database.create_all()
        bus = EventBus(database)
        queue = bus.subscribe()
        await Approvals(database, bus).create(kind="terminal", preview=preview)
        live = []
        while not queue.empty():
            live.append(json.dumps(queue.get_nowait(), ensure_ascii=False, default=str))
        async with database.session() as session:
            rows = (await session.execute(sa.select(events_t).order_by(events_t.c.id.desc())
                                          .limit(diag_bundle.EVENTS_LIMIT))).fetchall()
        history = list(reversed(rows_dicts(rows)))
        # Архив собирается тем же вызовом, что и ручка POST /diag/bundle.
        bundle = diag_bundle._build(data_dir, "127.0.0.1", 8765, history, {FAKE_OWNER_TOKEN})
        await database.close()  # Windows cannot remove the work dir under an open SQLite file
        return live, history, bundle

    live, history, bundle = asyncio.run(run())
    history_text = json.dumps(history, ensure_ascii=False, default=str)
    live_text = "\n".join(live)

    members = {}
    with zipfile.ZipFile(bundle["path"]) as archive:
        for info in archive.infolist():
            members[info.filename] = archive.read(info).decode("utf-8", "replace")
    archive_text = "\n".join(members.values())

    ctx.positive("продукт выдал все три вывода: живую ленту, историю и архив",
                 bool(live) and bool(history) and len(members) >= 3,
                 f"событий в ленте={len(live)}, строк истории={len(history)}, "
                 f"частей архива={sorted(members)}")

    def clean(name: str, text: str) -> tuple[bool, str]:
        """Вывод чист, если в нём нет НИ подстроки секрета, НИ находок сканера."""
        hits = scanner.scan_text(text, name, entropy=False)
        leaked = [what for what, value in (("ключ", FAKE_KEY), ("токен", FAKE_OWNER_TOKEN))
                  if value in text]
        return (not leaked and not hits), f"подстроки={leaked or 'нет'}, сканер={hits or 'чисто'}"

    ok_archive, detail_archive = clean("diag-bundle", archive_text)
    ctx.negative("АРТЕФАКТ: в диагностическом архиве секрета нет", ok_archive,
                 f"{detail_archive}; вычищено мест={bundle['redactions']}")
    ctx.negative("АРТЕФАКТ: секрета нет и в сырых байтах архива",
                 FAKE_KEY.encode() not in Path(bundle["path"]).read_bytes()
                 and FAKE_OWNER_TOKEN.encode() not in Path(bundle["path"]).read_bytes())

    ok_live, detail_live = clean("живая-лента", live_text)
    ctx.negative("СОБЫТИЕ: в живой ленте событий секрета нет", ok_live,
                 f"{detail_live}; чистка шины (command-center/bcc/events.py:41) работает "
                 "по ИМЕНАМ ключей, а ключ приехал внутри значения preview")
    ok_history, detail_history = clean("история-событий", history_text)
    ctx.negative("ЖУРНАЛ: в истории событий (таблица events) секрета нет", ok_history,
                 f"{detail_history}; строка легла в базу до какой-либо текстовой чистки")

    ctx.positive("архив остался полезным: журнал окна в нём есть, а не выброшен целиком",
                 any(name.endswith("run.log") for name in members)
                 and "***REDACTED***" in archive_text,
                 f"части={sorted(members)}")


# ------------------------------------------------------------------ 37
@scenario(id="OS-37", depth=PRODUCT_CONTRACTS)
def os37_protected_paths_are_immutable(ctx) -> None:
    """Чужой код не переписывает приёмку: и рабочая область, и проверка области.

    У защищённого пути ровно одна законная дверь — восстановление привязки
    (`write(..., restore=True)` из `AcceptanceBinding.restore`). Обычная запись,
    включая запись из диффа, этой двери не имеет.
    """
    from bossman.apprentice import openhands_client as openhands  # noqa: PLC0415
    from bossman.apprentice.live_workspace import LiveWorkspace, WorkspaceRefused  # noqa: PLC0415

    root = ctx.path("репозиторий", "держатель").parent
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(parents=True, exist_ok=True)
    source = root / "src" / "app.py"
    protected = root / "tests" / "test_acceptance.py"
    source.write_text("значение = 1\n", encoding="utf-8")
    protected.write_text("def test_приёмка():\n    assert True\n", encoding="utf-8")
    guarded = protected.read_bytes()

    workspace = LiveWorkspace(root, allowed_paths=("src", "tests"),
                              protected_paths=("tests/test_acceptance.py",))

    # ПОЛОЖИТЕЛЬНАЯ ПОЛОВИНА: законная правка проходит и МЕНЯЕТ БАЙТЫ.
    workspace.write("src/app.py", "значение = 2\n")
    ctx.positive("законная правка внутри разрешённой области записана на диск",
                 source.read_text(encoding="utf-8") == "значение = 2\n",
                 f"байт={source.stat().st_size}")
    accepted = _raises(lambda: openhands._validate_scope(("src/app.py",), ("src", "tests"),
                                                         ("tests/test_acceptance.py",)),
                       openhands.OpenHandsError)
    ctx.positive("проверка области принимает изменение, которое ей и разрешено",
                 accepted is None,
                 f"_validate_scope(('src/app.py',), ('src','tests'), (защищённый,)) → {accepted}")

    ctx.refused("запись в защищённый путь отвергнута рабочей областью",
                lambda: workspace.write("tests/test_acceptance.py",
                                        "def test_приёмка(): assert False\n"),
                WorkspaceRefused)
    ctx.negative("защищённый файл не изменился ни на байт",
                 protected.read_bytes() == guarded, f"байт={len(guarded)}")
    ctx.refused("запись вне разрешённой области отвергнута",
                lambda: workspace.write("docs/README.md", "чужое"), WorkspaceRefused)
    ctx.refused("выход за корень через `..` отвергнут",
                lambda: workspace.write("../побег.txt", "чужое"), WorkspaceRefused)
    ctx.negative("побег наружу не создал файла",
                 not (root.parent / "побег.txt").exists())
    ctx.refused("дифф, трогающий защищённый путь, отвергнут ДО вызова git",
                lambda: workspace.apply(
                    "--- a/tests/test_acceptance.py\n+++ b/tests/test_acceptance.py\n"
                    "@@ -1,2 +1,2 @@\n-def test_приёмка():\n-    assert True\n"
                    "+def test_приёмка():\n+    assert False\n"),
                WorkspaceRefused)
    ctx.refused("проверка области отвергает ИЗМЕНЁННЫЙ защищённый файл",
                lambda: openhands._validate_scope(("tests/test_acceptance.py",),
                                                  ("src", "tests"),
                                                  ("tests/test_acceptance.py",)),
                openhands.OpenHandsError)
    ctx.refused("проверка области отвергает файл вне области",
                lambda: openhands._validate_scope(("docs/чужое.md",), ("src",), ()),
                openhands.OpenHandsError)
    ctx.refused("пустой список разрешённых путей закрывает ворота, а не открывает",
                lambda: openhands._validate_scope(("src/app.py",), (), ()),
                openhands.OpenHandsError)
    ctx.negative("после всех отказов защищённый файл по-прежнему исходный",
                 protected.read_bytes() == guarded)


# ------------------------------------------------------------------ 38
@scenario(id="OS-38", depth=PRODUCT_CONTRACTS)
def os38_private_mode_never_leaks(ctx) -> None:
    """Приватный режим закрывает облако на КАЖДОМ выводе, включая запасные пути.

    ИЗМЕРЕННАЯ ГРАНИЦА. Режим живёт в `ContextVar`, поэтому он переезжает в
    asyncio-задачу (проверено ниже) и в `asyncio.to_thread`, но НЕ в голый
    `threading.Thread` — у нового потока свой контекст, и там режим снова
    «public». На этой ветке вызовы провайдера идут только async-путём
    (`command-center/bcc/engine.py:1290` оборачивает прогон целиком), поэтому
    дыры нет; она появится в тот день, когда вызов уведут в голый поток.
    """
    import asyncio  # noqa: PLC0415

    from bcc.provider_governance import GovernedAdapter  # noqa: PLC0415
    from bossman_shared.privacy import assert_provider_egress, execution_privacy  # noqa: PLC0415

    class Spy:
        """Адаптер, который считает КАЖДЫЙ дошедший до него вызов."""

        def __init__(self) -> None:
            self.calls = 0

        async def chat(self, *args, **kwargs):
            self.calls += 1
            return "ответ модели"

    spy = Spy()
    cloud = GovernedAdapter(spy, {"kind": "openrouter", "base_url": "https://openrouter.ai/api/v1"},
                            {"kind": "cloud", "pricing_known": True, "price_in": 1, "price_out": 1})
    other_cloud = GovernedAdapter(spy, {"kind": "anthropic",
                                        "base_url": "https://api.anthropic.com"},
                                  {"kind": "cloud", "pricing_known": True,
                                   "price_in": 1, "price_out": 1})
    disguised = GovernedAdapter(spy, {"kind": "openai_compat",
                                      "base_url": "https://openrouter.ai/api/v1"},
                                {"kind": "cloud", "pricing_known": True,
                                 "price_in": 1, "price_out": 1})
    local = GovernedAdapter(spy, {"kind": "ollama", "base_url": "http://127.0.0.1:11434"},
                            {"kind": "local"})

    async def run():
        out = {}
        with execution_privacy("private"):
            out["cloud"] = await _refusal(cloud.chat(), PermissionError)
            out["other"] = await _refusal(other_cloud.chat(), PermissionError)
            out["disguised"] = await _refusal(disguised.chat(), PermissionError)
            # ЗАПАСНОЙ ПУТЬ 1: понизить режим изнутри приватного контекста.
            with execution_privacy("public"):
                out["downgrade"] = await _refusal(cloud.chat(), PermissionError)
            # ЗАПАСНОЙ ПУТЬ 2: уйти в фоновую задачу и позвонить оттуда.
            out["background"] = await asyncio.create_task(_refusal(cloud.chat(), PermissionError))
            out["calls_while_private"] = spy.calls
            out["local"] = await local.chat()
            out["calls_after_local"] = spy.calls
        out["public"] = await cloud.chat()
        out["calls_after_public"] = spy.calls
        return out

    got = asyncio.run(run())
    ctx.negative("облачный провайдер в приватном режиме отвергнут",
                 isinstance(got["cloud"], PermissionError), f"отказ: {got['cloud']}")
    ctx.negative("второй облачный провайдер отвергнут так же — это граница, а не чёрный список",
                 isinstance(got["other"], PermissionError), f"отказ: {got['other']}")
    ctx.negative("облако под видом совместимого API отвергнуто по ХОСТУ",
                 isinstance(got["disguised"], PermissionError), f"отказ: {got['disguised']}")
    ctx.negative("понизить приватность изнутри контекста нельзя",
                 isinstance(got["downgrade"], PermissionError), f"отказ: {got['downgrade']}")
    ctx.negative("фоновая задача наследует приватность, а не теряет её",
                 isinstance(got["background"], PermissionError), f"отказ: {got['background']}")
    ctx.negative("ни один отвергнутый вызов НЕ дошёл до адаптера",
                 got["calls_while_private"] == 0, f"вызовов={got['calls_while_private']}")
    ctx.positive("локальный провайдер в приватном режиме РАБОТАЕТ — это не «выключить всё»",
                 got["local"] == "ответ модели" and got["calls_after_local"] == 1,
                 f"вызовов={got['calls_after_local']}")
    ctx.positive("вне приватного контекста облако доступно — режим включает границу",
                 got["public"] == "ответ модели" and got["calls_after_public"] == 2,
                 f"вызовов={got['calls_after_public']}")
    ctx.negative("сама граница отвергает внешний хост и вне адаптера",
                 _raises(lambda: _in_private(assert_provider_egress, execution_privacy,
                                             "openai_compat", "https://api.example.com"),
                         PermissionError) is not None)
    ctx.positive("та же граница пропускает петлевой адрес",
                 _raises(lambda: _in_private(assert_provider_egress, execution_privacy,
                                             "openai_compat", "http://127.0.0.1:8080"),
                         PermissionError) is None)


# ------------------------------------------------------------------ 39
@scenario(id="OS-39", depth=PRODUCT_CONTRACTS)
def os39_telegram_webhook_signature_is_checked(ctx) -> None:
    """BL-095: чужая подпись даёт CallbackRejected, а не необработанное исключение.

    Отрицательные контроли идут ПЕРВЫМИ: положительный случай расходует кнопку
    одноразово, и после него любой отказ объяснялся бы потреблённой кнопкой.
    Живой бот не нужен: проверка подписи и одноразовость кнопки лежат в
    хранилище и транспорте, сеть здесь не участвует вовсе.
    """
    import asyncio  # noqa: PLC0415
    import os  # noqa: PLC0415

    from bossman.notifications import telegram_transport as transport_module  # noqa: PLC0415
    from bossman.notifications.models import ActionKind, NotificationAction  # noqa: PLC0415
    from bossman.notifications.store import (CallbackRejected,  # noqa: PLC0415
                                             SQLiteNotificationStore)

    chat, owner = "42", "777"
    # Секрет вебхука у Telegram ASCII по определению; не-ASCII приходит СНАРУЖИ,
    # в заголовке, и именно он ломал сравнение строк (BL-095).
    secret = "webhook-secret-fake-42"
    applied: list[tuple[dict, str]] = []

    async def effect(action: dict, actor: str) -> None:
        """Эффект под охраной кнопки: считает КАЖДОЕ применение."""
        applied.append((action, actor))

    async def run():
        store = SQLiteNotificationStore(str(ctx.path("tg", "notifications.db")))
        transport = transport_module.TelegramTransport(
            store, bot_token_provider=lambda: "DETERMINISTIC",
            chat_id_provider=lambda: chat, webhook_secret_provider=lambda: secret,
            action_handler=effect)
        button = store.create_callback(
            NotificationAction(kind=ActionKind.APPROVE, target_type="approval", target_id="1",
                               label="Одобрить", fingerprint="fp-1"), chat)
        update = {"callback_query": {"data": "b:" + button, "from": {"id": int(owner)},
                                     "message": {"chat": {"id": int(chat), "type": "private"}}}}
        out = {"refusals": {}}
        # ОТРИЦАТЕЛЬНЫЕ ПЕРВЫМИ — кнопка ещё не нажата.
        for name, header in (("не-ASCII", "подпись-с-кириллицей"),
                             ("чужая ASCII", "not-the-secret"),
                             ("пустая", ""), ("отсутствует", None)):
            out["refusals"][name] = await _refusal(
                transport.handle_webhook(update, header), CallbackRejected)
        foreign_chat = {"callback_query": {**update["callback_query"],
                                           "message": {"chat": {"id": 43, "type": "private"}}}}
        out["refusals"]["чужой чат"] = await _refusal(
            transport.handle_webhook(foreign_chat, secret), CallbackRejected)
        foreign_user = {"callback_query": {**update["callback_query"], "from": {"id": 999},
                                           "message": {"chat": {"id": int(chat),
                                                                "type": "private"}}}}
        out["refusals"]["чужой пользователь"] = await _refusal(
            transport.handle_webhook(foreign_user, secret), CallbackRejected)
        out["effects_before"] = len(applied)
        # ПОЛОЖИТЕЛЬНЫЙ: та же кнопка с ВЕРНОЙ подписью.
        out["accepted"] = await transport.handle_webhook(update, secret)
        out["effects_after"] = len(applied)
        # Переигрывание уже нажатой кнопки.
        out["replay"] = await _refusal(transport.handle_webhook(update, secret), CallbackRejected)
        out["effects_final"] = len(applied)
        return out

    previous = os.environ.get("TELEGRAM_ALLOWED_USER_IDS")
    os.environ["TELEGRAM_ALLOWED_USER_IDS"] = owner
    try:
        got = asyncio.run(run())
    finally:
        if previous is None:
            os.environ.pop("TELEGRAM_ALLOWED_USER_IDS", None)
        else:
            os.environ["TELEGRAM_ALLOWED_USER_IDS"] = previous

    for name in ("не-ASCII", "чужая ASCII", "пустая", "отсутствует"):
        outcome = got["refusals"][name]
        ctx.negative(f"подпись «{name}» отвергнута ШТАТНЫМ отказом",
                     isinstance(outcome, CallbackRejected),
                     f"{type(outcome).__name__}: {outcome}")
    ctx.negative("чужой чат отвергнут", isinstance(got["refusals"]["чужой чат"], CallbackRejected),
                 f"{got['refusals']['чужой чат']}")
    ctx.negative("чужой пользователь отвергнут",
                 isinstance(got["refusals"]["чужой пользователь"], CallbackRejected),
                 f"{got['refusals']['чужой пользователь']}")
    ctx.negative("ни один отказ не произвёл эффекта", got["effects_before"] == 0,
                 f"эффектов={got['effects_before']}")
    ctx.positive("верная подпись принята, и эффект произошёл РОВНО ОДИН раз",
                 got["accepted"] == {"ok": True} and got["effects_after"] == 1,
                 f"эффектов={got['effects_after']}")
    ctx.positive("личность решившего записана до ПОЛЬЗОВАТЕЛЯ, а не до чата",
                 applied and applied[0][1] == f"tg:user:{owner}@chat:{chat}",
                 f"актор={applied[0][1] if applied else '—'}")
    ctx.negative("переигрывание той же кнопки НЕ даёт второго эффекта",
                 isinstance(got["replay"], CallbackRejected) and got["effects_final"] == 1,
                 f"{got['replay']}; эффектов={got['effects_final']}")


# ------------------------------------------------------------------ 40
@scenario(id="OS-40", depth=PRODUCT_CONTRACTS)
def os40_approval_is_consumed_exactly_once(ctx) -> None:
    """BL-090/BL-094 на ЖИВОЙ базе: одобрение решается один раз и один раз будит задачу.

    Одноразовость держится оговоркой `AND status='pending'` в UPDATE, а событие
    `approval.decided` — это то, на чём задача возобновляется: лишнее событие
    равно лишнему запуску. Проверять это подменой `decide()` бессмысленно (ровно
    так BL-090 и прожил незамеченным), поэтому сценарий требует настоящий
    PostgreSQL: он объявлен в реестре, и без него раннер отдаёт OWNER_REQUIRED,
    а не рисует зелёное.
    """
    import asyncio  # noqa: PLC0415
    import os  # noqa: PLC0415

    # Очередь уведомлений ядра — файл на машине владельца, и заводить в ней
    # сценарные записи нельзя. Путь задаётся СОБСТВЕННОЙ переменной продукта и
    # читается при импорте, поэтому ставится до него.
    previous_store = os.environ.get("BOSSMAN_NOTIFICATION_DB")
    os.environ["BOSSMAN_NOTIFICATION_DB"] = str(ctx.path("уведомления", "notifications.db"))

    from bossman import approvals  # noqa: PLC0415
    from bossman import events as core_events  # noqa: PLC0415

    applied: list[int] = []

    async def run():
        queue = core_events.subscribe()
        try:
            out = {}
            # BL-094: сам вызов create() падал TypeError при КАЖДОМ обращении.
            out["first"] = await approvals.create("shell", "удалить каталог владельца")
            out["control"] = await approvals.create("shell", "контрольное одобрение")
            # ОТРИЦАТЕЛЬНЫЕ ПЕРВЫМИ — на отдельном, контрольном одобрении, чтобы
            # положительный случай не был «доказан» уже потраченной строкой.
            out["rejected"] = await approvals.decide(out["control"], False, "owner:сценарий")
            out["flipped"] = await approvals.decide(out["control"], True, "owner:сценарий")
            out["ghost"] = await approvals.decide(2_000_000_000, True, "owner:сценарий")
            # ПОЛОЖИТЕЛЬНЫЙ: настоящее решение владельца.
            decided = await approvals.decide(out["first"], True, "owner:сценарий")
            out["decided"] = dict(decided) if decided else None
            if decided:
                applied.append(out["first"])
            replay = await approvals.decide(out["first"], True, "owner:сценарий")
            out["replay"] = dict(replay) if replay else None
            if replay:
                applied.append(out["first"])
            out["row"] = dict(await approvals.db.fetchrow(
                "select status, decided_by from approvals where id=$1", out["first"]))
            out["control_row"] = dict(await approvals.db.fetchrow(
                "select status from approvals where id=$1", out["control"]))
            messages = []
            while not queue.empty():
                messages.append(json.loads(queue.get_nowait()))
            out["decided_events"] = [m for m in messages
                                     if m.get("kind") == "approval.decided"
                                     and m.get("id") == out["first"]]
            out["created_events"] = [m for m in messages if m.get("kind") == "approval.created"]
            return out
        finally:
            core_events.unsubscribe(queue)

    try:
        got = asyncio.run(run())
    finally:
        if previous_store is None:
            os.environ.pop("BOSSMAN_NOTIFICATION_DB", None)
        else:
            os.environ["BOSSMAN_NOTIFICATION_DB"] = previous_store
    ctx.positive("одобрение вообще создаётся: create() не падает (BL-094)",
                 isinstance(got["first"], int) and got["first"] > 0
                 and len(got["created_events"]) == 2,
                 f"id={got['first']}, событий approval.created={len(got['created_events'])}")
    ctx.negative("отклонённое одобрение нельзя переголосовать в одобренное",
                 got["flipped"] is None and got["control_row"]["status"] == "rejected",
                 f"после повторного решения status={got['control_row']['status']}")
    ctx.negative("решение по несуществующему одобрению ничего не возвращает",
                 got["ghost"] is None)
    ctx.positive("верное решение потреблено ровно один раз и разбудило задачу один раз",
                 got["decided"] is not None and got["row"]["status"] == "approved"
                 and len(got["decided_events"]) == 1 and applied.count(got["first"]) == 1,
                 f"status={got['row']['status']}, событий approval.decided="
                 f"{len(got['decided_events'])}, эффектов={len(applied)}")
    ctx.negative("переигрывание того же решения не вернуло строки и не дало второго эффекта",
                 got["replay"] is None and len(applied) == 1,
                 f"эффектов={len(applied)}")
    ctx.negative("состояние одобрения после переигрывания не изменилось",
                 got["row"]["status"] == "approved"
                 and "owner:сценарий" in str(got["row"]["decided_by"]),
                 f"status={got['row']['status']}")


# ------------------------------------------------------------- вспомогательное
async def _refusal(awaitable, expected):
    """Вернуть пойманный отказ. Чужой тип отказа возвращается как есть — это
    находка, а не мелочь: BL-095 был ровно таким расхождением типов."""
    try:
        return await awaitable
    except expected as exc:
        return exc
    except BaseException as exc:  # noqa: BLE001
        return exc


def _raises(call, expected):
    try:
        call()
    except expected as exc:
        return exc
    return None


def _in_private(assert_egress, privacy, kind: str, url: str) -> None:
    with privacy("private"):
        assert_egress(kind, url)
