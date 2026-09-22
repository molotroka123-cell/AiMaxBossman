"""Private conversations, explicit delegation, and a model-independent status lane."""
from __future__ import annotations

import asyncio
import contextlib
import time

from .adapters import Core, Models, RateLimited, Telegram, scrub
from .config import CompanionError, Person, Settings
from .store import Store

HELP = ("Я Bossman, ваш ИИ-помощник. Можно просто написать мне.\n\n"
        "Обычное сообщение отвечает основная локальная модель (MAIN).\n"
        "/fast вопрос — быстрый ответ быстрой локальной моделью (FAST), если она настроена\n"
        "/model — какие локальные модели подключены\n"
        "/status — связь с компьютером (владелец)\n"
        "/task описание — подготовить поручение агенту Bossman\n"
        "/confirm код — подтвердить ровно это поручение\n"
        "/result ID — состояние и результат своей задачи\n"
        "/search запрос — поиск через настроенный SearXNG\n"
        "/cloud on|off — резерв Claude: только ваше текущее сообщение, без истории и файлов\n"
        "/watch on|off — уведомления о потере связи с Bossman (владелец)\n"
        "/lock — запретить новые поручения (разблокировка локально)\n"
        "/forget — удалить локальную память беседы и выключить облачный резерв.\n\n"
        "Я не нажимаю кнопки на компьютере сам. Поручения исполняют другие агенты "
        "с их обычными правами и подтверждениями. Telegram — внешний сервис, не локальный секретный чат.")


def failure_text(code: str) -> str:
    known = {
        "LOCAL_MODEL_UNAVAILABLE_CLOUD_NOT_AUTHORIZED": "Я на связи, но локальная модель сейчас не отвечает. Облачный резерв не разрешён или недоступен. /status продолжает работать.",
        "CLOUD_NOT_CONFIGURED": "Резерв Claude пока не настроен локально: нужны ключ, точная модель и предел расходов. Не присылайте ключ в этот чат.",
        "CLOUD_DAILY_CAP_EXCEEDED": "Облачный бюджет на сегодня исчерпан. Связь и /status остаются доступны без модели.",
        "CLOUD_REQUEST_CAP_EXCEEDED": "Запрос не отправлен в облако: превышает разрешённый предел стоимости.",
        "DELEGATION_NOT_CONFIGURED": "Для этого чата ещё не назначен исполнитель. Владелец настраивает отдельного агента локально; чужие права не выдаются автоматически.",
        "PROPOSAL_EXPIRED_OR_USED": "Это подтверждение уже использовано, истекло или принадлежит другому чату. Подготовьте новое через /task.",
        "EXECUTOR_CHANGED_REVIEW_AGAIN": "Настройки исполнителя изменились. Поручение не отправлено: сначала подготовьте и проверьте его заново.",
        "SECRET_IN_MESSAGE_CLOUD_REFUSED": "В сообщении обнаружен похожий на секрет фрагмент. Во внешнюю модель или поиск оно не отправлено.",
        "NETWORK_UNAVAILABLE": "Сервис не ответил в отведённое время. Это не доказательство выключенного компьютера; проверьте локальные сервисы и сеть.",
        "SEARCH_NOT_CONFIGURED": "Поиск ещё не подключён. Нужен существующий локальный SearXNG, отдельный поисковый движок я не устанавливаю.",
        "FAST_MODEL_NOT_CONFIGURED": "Быстрая модель (FAST) не настроена. Обычные сообщения отвечает основная модель; FAST добавляется локально в config.json (fast_url, fast_model).",
        "FAST_MODEL_UNAVAILABLE": "Быстрая модель (FAST) сейчас не отвечает (не загружена, занята или не та модель). Облако не использовано. Попробуйте обычным сообщением.",
    }
    return known.get(code, f"Действие не подтверждено: {code}. /status и /help помогут продолжить.")


ATTACHMENT_KINDS = ("photo", "document", "voice", "audio", "video", "video_note", "sticker",
                    "animation", "contact", "location", "venue", "poll")
REJECTED_TEXT = {
    "attachment": "Вложения (фото, файлы, голосовые) пока не поддерживаются: я их не скачиваю и не открываю. Напишите вопрос текстом.",
    "too_long": "Сообщение длиннее 4000 символов и не обработано. Сократите его или разбейте на части.",
}


class Companion:
    def __init__(self, settings: Settings, store: Store, telegram: Telegram, core: Core, models: Models,
                 *, policy_provider=None):
        self.settings, self.store = settings, store
        self.telegram, self.core, self.models = telegram, core, models
        self.policy_provider = policy_provider or (lambda: self.settings)
        self.wake = {(p.key, lane): asyncio.Event() for p in settings.people for lane in ("chat", "control")}
        self.last_message = {}
        self.monitor_state = None
        self.monitor_failures = 0
        if self.telegram is not None:
            self.telegram.authorize_delivery = self.delivery_allowed

    def delivery_allowed(self, person: Person) -> bool:
        try:
            return person in self.policy_provider().people
        except (OSError, ValueError, TypeError):
            return False

    def authorized(self, message) -> Person | None:
        # Re-read authorization from local config; removed users lose access even
        # if their old message was already queued. Invalid config fails closed.
        try:
            return self.policy_provider().authorize(message)
        except (OSError, ValueError, TypeError):
            return None

    def cloud_allowed(self, person: Person, message: dict) -> bool:
        try:
            current = self.policy_provider()
            # A live local configuration edit may revoke or reduce a policy;
            # changed pricing/credential settings require restart, never stale authority.
            same = all(getattr(current, field) == getattr(self.settings, field) for field in
                       ("cloud_daily_usd", "cloud_request_usd", "cloud_model", "cloud_token"))
            return (same and current.authorize(message) == person and
                    self.store.get("cloud:" + person.key, False) is True and
                    message.get("_update_id", 0) > self.store.get("cloud_after:" + person.key, -1))
        except (OSError, ValueError, TypeError):
            return False

    async def ingest(self, update: dict):
        if not isinstance(update, dict) or type(update.get("update_id")) is not int or update['update_id'] < 0:
            return
        message = update.get("message")
        person = self.authorized(message)
        text = message.get("text") if isinstance(message, dict) else None
        valid = person and (person.key, "chat") in self.wake and isinstance(text, str) and 0 < len(text) <= 4000
        body = {**message, "_update_id": update["update_id"]} if valid else None
        rejected = None
        if person and (person.key, "chat") in self.wake and not valid:
            if isinstance(text, str) and len(text) > 4000:
                rejected = "too_long"
            elif text is None and any(k in message for k in ATTACHMENT_KINDS):
                rejected = "attachment"
        if rejected:
            # Only identity fields are kept; attachment metadata is never stored or fetched.
            valid = True
            body = {"from": message["from"], "chat": message["chat"], "text": "",
                    "_rejected": rejected, "_update_id": update["update_id"]}
        accepted = self.store.ingest(update['update_id'], person.key if valid else None, body)
        if accepted:
            self.wake[(person.key, self.store.lane(body))].set()

    async def handle(self, person: Person, message: dict) -> str:
        # Authorization is checked again at the effect boundary, not inferred
        # from a model's claimed role, forwarded name or chat title.
        current = self.authorized(message)
        if current is None or current.key != person.key:
            raise CompanionError("IDENTITY_REVOKED")
        person = current
        if message.get("_rejected") in REJECTED_TEXT:
            return REJECTED_TEXT[message["_rejected"]]
        text = message['text'].strip()
        if not text:
            return HELP
        command, _, arg = text.partition(" ")
        command, arg = command.lower(), arg.strip()
        if command in {"/start", "/help"}:
            return HELP
        if command == "/forget":
            self.store.forget(person.key)
            return "Локальная память беседы очищена; облачный резерв выключен. Историю самого Telegram удаляйте в Telegram. Выполненные задачи Bossman не удалены."
        if command == "/cloud":
            if arg not in {"on", "off"}:
                return "Для резерва Claude: /cloud on. При сбое локальной модели только новое ваше сообщение будет передано OpenRouter/Claude. Локальная история, результаты задач и файлы не передаются. Плата — в пределах локально заданного бюджета. /cloud off — отключить."
            if arg == "on" and (self.settings.cloud_daily_usd <= 0 or not self.settings.cloud_token):
                raise CompanionError("CLOUD_NOT_CONFIGURED")
            self.store.put("cloud:" + person.key, arg == "on")
            self.store.put("cloud_after:" + person.key, message.get("_update_id", -1))
            return "Резерв Claude включён для новых сообщений этого чата." if arg == "on" else "Облачный резерв этого чата выключен."
        if command in {"/status", "/watch", "/lock"}:
            if person.role != "owner":
                return "Состояние компьютера и управление мостом доступны только владельцу. Ваши беседы и задачи отделены от его данных."
            if command == "/lock":
                self.store.put("delegation_locked", True)
                return "Новые поручения заблокированы. Уже запущенные задачи не остановлены. Разблокировка — только локально."
            if command == "/watch":
                if arg not in {"on", "off"}:
                    return "/watch on — включить уведомления; /watch off — выключить."
                self.store.put("watch", arg == "on")
                return "Наблюдение включено." if arg == "on" else "Наблюдение выключено."
            try:
                state = await self.core.status()
            except CompanionError:
                return "Telegram-мост на связи. Приложение Bossman сейчас не отвечает или его личность не подтверждена. Это не подтверждает остановку задач. Если модель доступна, беседа продолжится независимо."
            return ("Telegram-мост на связи. Процесс Bossman отвечает. "
                    "Это проверка доступности, не полной готовности всех функций.\n"
                    f"Локальная модель: {'настроена, результат проверяется запросом' if self.settings.local_model else 'не настроена'}.\n"
                    f"Делегирование: {'заблокировано' if self.store.get('delegation_locked', False) else 'по подтверждению'}.")
        if command == "/task":
            if self.store.get("delegation_locked", False):
                return "Новые поручения заблокированы владельцем."
            if not arg:
                return "Напишите /task и точное поручение. Сначала покажу его для подтверждения."
            if len(arg) > 2500:
                return "Поручение слишком длинное: до 2500 символов, чтобы весь текст был виден перед подтверждением. Сократите его; ничего не отправлено."
            fp = await self.core.executor(person)
            nonce = self.store.propose(person.key, {"prompt": arg, "executor": fp, "agent_id": person.agent_id})
            return (f"Поручение агенту #{person.agent_id}:\n{arg[:2500]}\n\n"
                    f"Пока НЕ отправлено. Подтвердить в течение 5 минут: /confirm {nonce}\n"
                    "Права агента не меняются. Опасные действия по-прежнему требуют разрешения Bossman.")
        if command == "/confirm":
            if self.store.get("delegation_locked", False):
                return "Новые поручения заблокированы владельцем."
            if len(arg) != 12 or any(c not in "0123456789abcdef" for c in arg):
                raise CompanionError("PROPOSAL_EXPIRED_OR_USED")
            proposal = self.store.consume(person.key, arg)
            try:
                if proposal['agent_id'] != person.agent_id:
                    raise CompanionError("EXECUTOR_CHANGED_REVIEW_AGAIN")
                def authorize_effect():
                    latest = self.authorized(message)
                    if latest != person or self.store.get("delegation_locked", False):
                        raise CompanionError("DELEGATION_PERMISSION_REVOKED")
                task_id, status, identity = await self.core.delegate(person, proposal['prompt'], proposal['executor'],
                                                           before_submit=authorize_effect)
            except (CompanionError, asyncio.CancelledError):
                self.store.delegated(person.key, arg, None)
                raise
            self.store.delegated(person.key, arg, task_id, identity)
            return f"Поручение принято Bossman: задача #{task_id}, состояние {status}. Это ещё не завершение. Проверить: /result {task_id}"
        if command == "/result":
            if not arg.isdecimal() or len(arg) > 12 or not self.store.owns_task(person.key, int(arg)):
                return "В этом чате нет такой подтверждённой задачи. Чужие задачи и внутренние данные не выдаются."
            data = await self.core.task(int(arg), self.store.task_binding(person.key, int(arg)))
            status = str(data['task'].get('status', 'unknown'))
            if status == "completed" and isinstance(data.get('result'), str):
                return f"Задача #{arg}: {status}\n\n{data['result'][:2800]}"
            return f"Задача #{arg}: {status}. Подтверждённого результата пока нет."
        if command == "/search":
            if not arg:
                return "Напишите /search и запрос. Он уйдёт в настроенный поисковый сервис."
            if scrub(arg, (self.settings.bot_token, self.settings.core_token, self.settings.cloud_token, self.settings.local_token)) != arg:
                raise CompanionError("SECRET_IN_MESSAGE_CLOUD_REFUSED")
            return await self.models.search(arg)
        if command == "/model":
            main = "подключена" if self.settings.local_model else "не настроена"
            fast = "подключена (/fast вопрос)" if self.settings.fast_model else "не настроена"
            return (f"Основная локальная модель (MAIN): {main}.\nБыстрая локальная модель (FAST): {fast}.\n"
                    "Готовность проверяется только реальным ответом; облачный резерв по умолчанию выключен.")
        if command == "/fast":
            if not arg:
                return "Напишите /fast и вопрос — ответит быстрая локальная модель."
            answer, _ = await self.models.answer(arg, self.store.history(person.key),
                                                 cloud_consent=False, route="fast")
            self.store.remember(person.key, arg, answer)
            return "⚡ FAST\n" + answer
        if command.startswith("/"):
            return "Неизвестная команда. /help — доступные действия."
        answer, route = await self.models.answer(text, self.store.history(person.key),
            cloud_consent=lambda: self.cloud_allowed(person, message))
        self.store.remember(person.key, text, answer)
        prefix = {"cloud": "☁️ Облачный резерв Claude\n", "fast": "⚡ FAST (основная модель не ответила вовремя)\n"}
        return prefix.get(route, "") + answer

    async def typing(self, person: Person):
        """Cosmetic 'typing…' while a slow local model answers; never blocks or fails the reply."""
        while True:
            try:
                if self.delivery_allowed(person):
                    await self.telegram.call("sendChatAction", {"chat_id": person.chat_id, "action": "typing"})
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(4.5)

    async def worker(self, original_person: Person, lane: str = "chat"):
        event = self.wake[(original_person.key, lane)]
        while True:
            item = self.store.claim(original_person.key, lane)
            if item is None:
                event.clear()
                await event.wait()
                continue
            update_id, message = item
            person = self.authorized(message)
            if person is None:
                self.store.finish(update_id, "failed")
                continue
            # Per-chat anti-flood; persisted inbox remains bounded.
            pause = max(0.0, 0.75 - (time.monotonic() - self.last_message.get(person.key, 0)))
            await asyncio.sleep(pause)
            text = str(message.get("text", "")).strip()
            slow = lane == "chat" and bool(text) and (not text.startswith("/") or text.lower().startswith("/fast "))
            indicator = asyncio.create_task(self.typing(person)) if slow and self.telegram is not None else None
            try:
                answer = await self.handle(person, message)
            except CompanionError as exc:
                answer = failure_text(str(exc))
                if message.get('text', '').startswith('/confirm '):
                    answer += "\nАвтоповтора нет: при потере ответа задача могла быть создана. Проверьте Bossman перед новым поручением."
            except asyncio.CancelledError:
                raise
            except Exception:
                answer = "Не удалось подтвердить результат. Подробности доступны локально; повтор опасного действия автоматически не выполняется."
            finally:
                if indicator is not None:
                    indicator.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await indicator
            # Revocation also applies to the outgoing message.
            if self.authorized(message) != person:
                self.store.finish(update_id, "failed")
                continue
            try:
                await self.telegram.send(person, answer)
            except CompanionError:
                self.store.finish(update_id, "delivery_unknown")
            else:
                self.store.finish(update_id, "done")
                self.store.put("last_roundtrip:" + person.key, time.time())
            self.last_message[person.key] = time.monotonic()

    async def poll(self):
        delay = 1.0
        while True:
            try:
                updates = await self.telegram.call("getUpdates", {"offset": self.store.get("offset", 0),
                    "timeout": 25, "limit": 20, "allowed_updates": ["message"]})
                if not isinstance(updates, list):
                    raise CompanionError("TELEGRAM_UPDATES_INVALID")
                for update in updates:
                    await self.ingest(update)
                delay = 1.0
            except CompanionError as exc:
                self.store.put("transport_error", str(exc))
                if str(exc) in {"AUTH_DENIED", "CONFLICT"}:
                    raise
                await asyncio.sleep(max(delay, exc.retry_after if isinstance(exc, RateLimited) else 0))
                delay = min(delay * 2, 30)

    async def notify_tasks(self):
        # Read only task ids created by this exact principal; never consume the
        # global event feed, which may contain another person's private data.
        try:
            current = {p.key: p for p in self.policy_provider().people}
        except (OSError, ValueError, TypeError):
            return
        cursor = self.store.get("notification_cursor", 0)
        query = "SELECT rowid AS seq,id,who,task_id,body FROM proposals WHERE phase='submitted' AND rowid>? ORDER BY rowid LIMIT 16"
        rows = self.store.db.execute(query, (cursor,)).fetchall()
        if not rows:
            rows = self.store.db.execute(query, (0,)).fetchall()
        for row in rows:
            self.store.put("notification_cursor", row['seq'])
            person = current.get(row['who'])
            key = "notified:" + row['id']
            if person is None or self.store.get(key) is not None:
                continue
            try:
                data = await self.core.task(row['task_id'], self.store.task_binding(row['who'], row['task_id']))
            except CompanionError:
                continue
            status = data['task'].get('status')
            if status == 'waiting_approval':
                # Tell the owner where to act; the approval itself stays in the Bossman app.
                wait_key = "notified_wait:" + row['id']
                if self.store.get(wait_key) is not None:
                    continue
                self.store.put(wait_key, 'delivery_pending_or_unknown')
                try:
                    if person not in self.policy_provider().people:
                        continue
                    await self.telegram.send(person, f"Задача #{row['task_id']} ждёт подтверждения в приложении Bossman "
                                                     "(раздел подтверждений). Из Telegram подтвердить нельзя — это намеренно.")
                except CompanionError:
                    continue
                self.store.put(wait_key, 'delivered')
                continue
            if status not in {'completed', 'failed', 'stopped', 'blocked'}:
                continue
            content = f"Задача #{row['task_id']}: {status}."
            if status == 'completed' and isinstance(data.get('result'), str):
                content += "\n\n" + data['result'][:2800]
            self.store.put(key, 'delivery_pending_or_unknown')
            try:
                # Authorization can be revoked while the request was in flight.
                if person not in self.policy_provider().people:
                    continue
                await self.telegram.send(person, content)
            except CompanionError:
                continue  # uncertain send is not replayed on restart
            self.store.put(key, 'delivered')

    async def monitor(self):
        owner = next(p for p in self.settings.people if p.role == "owner")
        ticks = 0
        while True:
            await asyncio.sleep(self.settings.monitor_seconds)
            ticks += 1
            if ticks % 60 == 0:
                self.store.prune()
            await self.notify_tasks()
            if not self.store.get("watch", False):
                continue
            try:
                current = self.policy_provider()
            except (OSError, ValueError, TypeError):
                continue
            if owner not in current.people:
                continue
            try:
                await self.core.status()
                online = True
            except CompanionError:
                online = False
            self.monitor_failures = 0 if online else self.monitor_failures + 1
            if not online and self.monitor_failures < 2:
                continue
            if self.monitor_state == online:
                continue
            self.monitor_state = online
            message = ("Bossman снова отвечает. Это доступность процесса, не проверка каждой задачи." if online else
                       "Telegram-мост на связи, но Bossman не отвечает двум проверкам подряд. Задачи автоматически не перезапускаю.")
            with contextlib.suppress(CompanionError):
                await self.telegram.send(owner, message)

    async def run(self):
        await self.telegram.preflight()
        self.store.recover()
        tasks = [asyncio.create_task(self.worker(p, lane)) for p in self.settings.people for lane in ("chat", "control")]
        tasks += [asyncio.create_task(self.poll()), asyncio.create_task(self.monitor())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
