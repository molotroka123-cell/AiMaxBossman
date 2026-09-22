"""Русский пульт владельца: ОДИН путь исполнения.

Telegram → проверка личности → задача Bossman → policy/approval → executor →
проверка результата. Второго агента с прямым доступом к shell и мыши здесь нет
и быть не может: этот модуль не запускает процессы, не двигает мышь и не пишет
в файловую систему. Всё, что он умеет, — читать состояние Bossman, готовить
поручение (которое всё равно проходит /confirm и подтверждения ядра) и
применять решение владельца к УЖЕ существующему подтверждению Bossman.

Привязка решения (раздел 5 ТЗ):
  * владелец + чат        — opaque-токен кнопки принадлежит ровно одному
    `Person.key`, а перед эффектом личность перепроверяется заново;
  * конкретное действие   — approval_id И его kind сохраняются в «воротах»;
  * digest аргументов     — sha256 по (id, kind, preview, task_id, run_id,
    created_at); изменение цели, аргументов или контекста меняет digest и
    аннулирует решение — сверка идёт и при показе, и НЕПОСРЕДСТВЕННО перед
    POST /api/approvals/{id};
  * TTL                   — ворота живут APPROVE_TTL_S секунд;
  * одноразовый nonce     — consume атомарный (CAS в SQLite): повтор нажатия
    или пересланное сообщение не находят, что потреблять.
Свежее наблюдение экрана (/api/computer/status + /api/computer/observe)
обязательно ПЕРЕД одобрением; «Стоп», неизвестный исход прошлого шага или
протухшее наблюдение — отказ. Слово «approved» в тексте сообщения, в памяти
или в ответе модели прав не даёт: право даёт только потреблённый nonce.
"""
from __future__ import annotations

import asyncio
import re
import time

from .adapters import approval_digest
from .config import CompanionError, Person

# Ворота живут недолго: подтверждение — это решение о ТЕКУЩЕМ состоянии экрана
# и очереди, а не бессрочная доверенность.
APPROVE_TTL_S = 180.0
# Наблюдение старше этого не является основанием для эффекта (тот же предел,
# что у Computer Use в ядре: bcc/features/tools_computer.py MAX_OBS_AGE_S).
OBSERVATION_MAX_AGE_S = 45.0
NONCE_RE = re.compile(r"^[0-9a-f]{12}$")
# Кадр наблюдения, который отдаёт LocalScreenshotProvider ядра. Принимаем только
# такое имя: путь приходит от локального Bossman, но доверять произвольному
# пути из ответа сети — не то же самое, что доверять Bossman.
FRAME_RE = re.compile(r"^screen-\d{1,25}\.png$")
FRAME_MAX_BYTES = 10 * 1024 * 1024

CONSOLE_COMMANDS = {"/queue", "/approvals", "/approve", "/reject", "/stop", "/pause",
                    "/resume", "/screen", "/open", "/files", "/diag", "/lessons", "/fix", "/bossman"}
# Команды пульта, которым нужен владелец + включённый тумблер управления.
OWNER_CONSOLE = {"/queue", "/approvals", "/approve", "/reject", "/stop", "/pause", "/resume",
                 "/screen", "/open", "/files", "/diag", "/lessons", "/fix", "/bossman"}

CONSOLE_OFF = ("Пульт управления компьютером из Telegram выключен или доступен только владельцу. "
               "Владелец включает его локально: pc_control в config.json компаньона. "
               "Прямого доступа к PowerShell и мыши из Telegram нет ни у кого.")
NO_DIRECT_SHELL = ("Прямого shell из Telegram нет: /sh удалён. Владельцу доступны /claude и /codex. "
                   "Всё, что нужно сделать на компьютере, оформляется как задача Bossman "
                   "(/task или «Новая задача») и проходит подтверждение. /menu — пульт.")
STOPPED_NOTE = ("Уже совершённые внешние действия этим не отменяются: остановлено только то, "
                "что ещё можно остановить.")


def _short(value, limit: int = 300) -> str:
    return str(value or "").replace("\r", " ").strip()[:limit]


class ConsoleMixin:
    """Пульт владельца. Подмешивается в Companion; своего состояния не заводит."""

    # ------------------------------------------------------------------ меню
    def console_allowed(self, person: Person) -> bool:
        return person.role == "owner" and self.settings.pc_control is True

    def console_menu(self, person: Person):
        """Главный экран. Кнопки — те же opaque-токены, привязанные к человеку."""
        b = lambda label, cmd: self.button(person, label, cmd)  # noqa: E731
        rows = [[b("📊 Статус", "/status"), b("🗂 Очередь", "/queue")],
                [b("📝 Новая задача", "/task"), b("✅ Подтвердить / ⛔ Отклонить", "/approvals")],
                [b("📸 Экран", "/screen"), b("🚀 Открыть приложение/папку", "/open")],
                [b("📂 Файлы проекта", "/files"), b("🩺 Диагностика", "/diag")],
                [b("🎨 Фото", "/img"), b("🎬 Видео / TestRun", "/video")],
                [b("📚 Найденные уроки", "/lessons"), b("🛠 Предложить исправление", "/fix")],
                [b("⏸ Пауза", "/pause"), b("⛔ СТОП", "/stop"), b("▶️ Продолжить", "/resume")]]
        return rows

    # --------------------------------------------------------------- разбор
    async def console(self, person: Person, command: str, arg: str, message: dict):
        """Единственная точка входа пульта. Всё, что не разрешено, — отказ без эффекта."""
        if command in OWNER_CONSOLE and not self.console_allowed(person):
            return CONSOLE_OFF
        if command == "/queue":
            return await self.console_queue(person)
        if command == "/approvals":
            return await self.console_approvals(person)
        if command in {"/approve", "/reject"}:
            return await self.console_decide(person, command == "/approve", arg, message)
        if command in {"/stop", "/pause"}:
            return await self.console_halt(person, full=command == "/stop")
        if command == "/resume":
            return await self.console_resume(person)
        if command == "/screen":
            return await self.console_screen(person)
        if command == "/diag":
            return await self.console_diag(person)
        if command == "/lessons":
            return await self.console_lessons(person)
        if command in {"/open", "/files", "/fix"}:
            return await self.console_task(person, command, arg, message)
        if command == "/bossman":
            return await self.console_bossman(person)
        return NO_DIRECT_SHELL

    # ------------------------------------------- низкорисковые чтения (без диалогов)
    async def console_queue(self, person: Person):
        """Очередь: ожидающие подтверждения Bossman + задачи этого чата. Только чтение."""
        try:
            pending = await self.core.approvals("pending")
        except CompanionError as exc:
            return f"Очередь подтверждений не прочитана: {exc}. Это не значит, что она пуста."
        locked = self.store.get("delegation_locked", False)
        lines = [f"🗂 Ожидают подтверждения: {len(pending)}",
                 "Новые поручения: " + ("ЗАБЛОКИРОВАНЫ (пауза/СТОП)" if locked else "по подтверждению")]
        for row in pending[:10]:
            lines.append(f"• #{row['id']} · {_short(row.get('kind'), 40)} · {_short(row.get('preview'), 120) or '(без описания)'}")
        keyboard = [[self.button(person, "✅ Подтвердить / ⛔ Отклонить", "/approvals")]] if pending else []
        from .service import Reply
        return Reply("\n".join(lines), keyboard + [[self.button(person, "🏠 Меню", "/menu")]])

    async def console_lessons(self, person: Person):
        try:
            rows = await self.core.lessons()
        except CompanionError as exc:
            return f"Уроки не прочитаны: {exc}. Пустым списком это не подменяю."
        if not rows:
            return "Bossman пока не записал ни одного урока (раздел обучения пуст)."
        lines = ["📚 Найденные уроки (последние):"]
        for row in rows[:10]:
            lines.append(f"• агент {row.get('agent_id')} · {_short(row.get('capability'), 60)} · "
                         f"{_short(row.get('lesson') or row.get('note') or row.get('summary'), 160) or '—'}")
        return "\n".join(lines)

    async def console_diag(self, person: Person):
        """Диагностика: что сейчас точно известно, и что именно НЕ проверено."""
        lines = ["🩺 Диагностика (проверка доступности, не готовности всех функций)"]
        try:
            await self.core.status()
            lines.append("• Bossman: отвечает")
        except CompanionError as exc:
            lines.append(f"• Bossman: НЕ отвечает ({exc}). Остановку задач это не подтверждает.")
        try:
            st = await self.core.computer_status()
            lines.append(f"• Управление компьютером: {'доступно' if st.get('available') else 'недоступно'} — "
                         f"{_short(st.get('detail'), 120)}")
            lines.append(f"• СТОП: {'ВКЛЮЧЁН, новые действия заблокированы' if st.get('stopped') else 'выключен'}")
            if st.get("outcome_unknown"):
                lines.append(f"• Исход прошлого шага НЕИЗВЕСТЕН: {_short(st['outcome_unknown'], 120)}")
        except CompanionError as exc:
            lines.append(f"• Управление компьютером: состояние не прочитано ({exc})")
        try:
            lines.append(f"• Ожидают подтверждения: {len(await self.core.approvals('pending'))}")
        except CompanionError:
            lines.append("• Ожидают подтверждения: не прочитано")
        lines.append("• Новые поручения: " + ("заблокированы" if self.store.get("delegation_locked", False) else "по подтверждению"))
        lines.append(f"• Локальная модель: {'настроена' if self.settings.local_model else 'не настроена'} "
                     "(готовность подтверждает только реальный ответ)")
        error = self.store.get("transport_error")
        if error:
            lines.append(f"• Последняя ошибка транспорта: {_short(error, 80)}")
        return "\n".join(lines)

    async def console_screen(self, person: Person):
        """Разрешённый снимок экрана — кадр наблюдения Bossman, не свой скриншотер."""
        try:
            obs = await self.core.computer_observe()
        except CompanionError as exc:
            return (f"Снимок экрана не получен: {exc}. Своего скриншотера в обход Bossman у меня нет.")
        window = _short((obs.get("window") or {}).get("title"), 120) or "(окно не определено)"
        caption = (f"🖥 {window}\nЭлементов: {len(obs.get('elements') or [])} · наблюдение #{obs.get('generation')}"
                   + (" · СТОП включён" if obs.get("stopped") else ""))
        data = self.read_frame(obs.get("screenshot"))
        keyboard = [[self.button(person, "🔄 Ещё раз", "/screen"), self.button(person, "🏠 Меню", "/menu")]]
        if data is None:
            from .service import Reply
            return Reply(caption + "\nКадр не приложен (скриншотер не отдал файл). Текст наблюдения — выше.", keyboard)
        await self.telegram.send_photo(person, data, caption, keyboard)
        return None

    @staticmethod
    def read_frame(path) -> bytes | None:
        """Кадр локального наблюдения. Только ожидаемое имя файла и настоящий PNG."""
        if not isinstance(path, str) or not path:
            return None
        from pathlib import Path
        file = Path(path)
        if not FRAME_RE.match(file.name) or file.is_symlink():
            return None
        try:
            if file.stat().st_size > FRAME_MAX_BYTES:
                return None
            data = file.read_bytes()
        except OSError:
            return None
        return data if data[:8] == b"\x89PNG\r\n\x1a\n" else None

    async def console_bossman(self, person: Person):
        from .service import Reply
        try:
            await self.core.status()
        except CompanionError:
            return Reply("📊 Bossman сейчас не отвечает. Запустить его я могу только с компьютера владельца — "
                         "из Telegram процессы не стартую.", [[self.button(person, "🔄 Проверить ещё раз", "/bossman")]])
        return "📊 Bossman запущен и отвечает (" + self.settings.core_url + ")."

    # -------------------------------------------------- действия → задача Bossman
    async def console_task(self, person: Person, command: str, arg: str, message: dict):
        """Открыть приложение/папку, показать файлы, предложить исправление —
        всё это поручения Bossman, а не прямые действия компаньона."""
        hint = {"/open": ("Что открыть? Напишите, например: /open Блокнот, или /open папку загрузок.",
                          "Открой на компьютере: "),
                "/files": ("Какие файлы показать? Например: /files список файлов проекта Bossman.",
                           "Покажи файлы проекта (только чтение): "),
                "/fix": ("Что исправить? Опишите проблему: /fix бот не отвечает на /status.",
                         "Предложи исправление (сначала план, без изменений): ")}[command]
        if not arg:
            return hint[0]
        return await self.propose_task(person, hint[1] + arg, message)

    # ------------------------------------------------------- подтверждения
    async def console_approvals(self, person: Person):
        """Показать ожидающие подтверждения Bossman и выдать одноразовые ворота."""
        from .service import Reply
        try:
            rows = await self.core.approvals("pending")
        except CompanionError as exc:
            return f"Список подтверждений не прочитан: {exc}. Решать вслепую не буду."
        if not rows:
            return Reply("Ожидающих подтверждений нет.", [[self.button(person, "🏠 Меню", "/menu")]])
        lines, keyboard = ["Ожидают вашего решения (действует "
                           f"{int(APPROVE_TTL_S // 60)} мин, одноразово):"], []
        for row in rows[:5]:
            try:
                digest = approval_digest(row)
            except CompanionError:
                lines.append(f"• #{row.get('id')}: строка подтверждения не распознана, решение не предлагаю")
                continue
            payload = {"approval_id": row["id"], "kind": row["kind"], "digest": digest,
                       "chat_id": person.chat_id, "user_id": person.user_id}
            yes = self.store.gate(person.key, {**payload, "decision": True}, APPROVE_TTL_S)
            no = self.store.gate(person.key, {**payload, "decision": False}, APPROVE_TTL_S)
            lines.append(f"\n#{row['id']} · {_short(row.get('kind'), 40)}\n{_short(row.get('preview'), 600) or '(без описания)'}")
            keyboard.append([self.button(person, f"✅ Разрешить #{row['id']}", "/approve " + yes),
                             self.button(person, f"⛔ Отклонить #{row['id']}", "/reject " + no)])
        lines.append("\nОдобрение применяется только после повторной проверки: та же цель, те же аргументы, "
                     "свежий экран. Любое изменение — отказ.")
        return Reply("\n".join(lines), keyboard + [[self.button(person, "🔄 Обновить", "/approvals")]])

    async def console_decide(self, person: Person, approve: bool, nonce: str, message: dict):
        """Применить решение владельца ровно к одному подтверждению Bossman."""
        if not NONCE_RE.match(nonce or ""):
            return ("Решение принимается только кнопкой из /approvals: номер подтверждения, слово «approved» "
                    "или ссылка правами не являются.")
        gate = self.store.open_gate(person.key, nonce)      # одноразово, с TTL
        if gate.get("decision") is not approve or gate.get("chat_id") != person.chat_id or gate.get("user_id") != person.user_id:
            self.store.close_gate(nonce, "refused")
            return "Эта кнопка принадлежит другому решению, человеку или чату. Откройте /approvals заново."
        actor = f"tg:user:{person.user_id}@chat:{person.chat_id}"
        try:
            # Личность — ещё раз, на границе эффекта, а не по старому сообщению.
            if self.authorized(message) != person:
                raise CompanionError("IDENTITY_REVOKED")
            row = await self.core.approval(gate["approval_id"])
            if row is None:
                raise CompanionError("APPROVAL_GONE")
            if row.get("status") != "pending":
                raise CompanionError("APPROVAL_ALREADY_DECIDED")
            # Цель, аргументы, контекст — те же, что владелец видел.
            if row.get("kind") != gate["kind"] or approval_digest(row) != gate["digest"]:
                raise CompanionError("APPROVAL_CHANGED_REVIEW_AGAIN")
            if approve:
                await self.fresh_screen()      # отказ — до эффекта, не после
            decided = await self.core.decide_approval(gate["approval_id"], approve, actor)
        except (CompanionError, asyncio.CancelledError):
            self.store.close_gate(nonce, "decide_unknown")
            raise
        self.store.close_gate(nonce, "decided")
        want = "approved" if approve else "rejected"
        if decided.get("status") != want or decided.get("decided_by") != actor:
            return (f"Решение отправлено, но Bossman показывает состояние «{_short(decided.get('status'), 30)}». "
                    "Автоповтора нет: сверьте подтверждение в Bossman.")
        head = "✅ Разрешено" if approve else "⛔ Отклонено"
        return (f"{head}: подтверждение #{gate['approval_id']} ({_short(gate['kind'], 40)}).\n"
                f"Решение записано от вашего имени: {actor}.\n"
                "Это разрешение ровно на это действие; повторное нажатие ничего не повторит.")

    async def fresh_screen(self) -> dict:
        """Свежее наблюдение экрана и актуальное последствие ПЕРЕД эффектом."""
        state = await self.core.computer_status()
        if state.get("stopped") is True:
            raise CompanionError("COMPUTER_STOPPED")
        if state.get("outcome_unknown"):
            raise CompanionError("LAST_STEP_OUTCOME_UNKNOWN")
        if state.get("available") is not True:
            raise CompanionError("SCREEN_NOT_OBSERVABLE")
        obs = await self.core.computer_observe()
        if obs.get("stopped") is True:
            raise CompanionError("COMPUTER_STOPPED")
        seen = obs.get("observed_at")
        if not isinstance(seen, (int, float)) or isinstance(seen, bool) or time.time() - seen > OBSERVATION_MAX_AGE_S:
            raise CompanionError("OBSERVATION_STALE")
        return obs

    # ----------------------------------------------------------- СТОП / пауза
    async def console_halt(self, person: Person, *, full: bool):
        """СТОП и пауза работают без модели и без генератора: только локальный
        замок и POST /api/computer/stop. Ни одна строка здесь не ждёт LLM."""
        from .service import Reply
        self.store.put("delegation_locked", True)
        lines = ["⛔ СТОП" if full else "⏸ Пауза",
                 "• Новые поручения из Telegram заблокированы."]
        cancelled = False
        if full:
            job = self.image_job
            if job is not None:
                job["cancel"] = True
                cancelled = True
            if self.cancel_agents():
                lines.append("• Claude/Codex остановлены, их процессы завершены.")
        try:
            await self.core.computer_stop()
            lines.append("• Управление компьютером остановлено: набор текста прерван, новые действия "
                         "запрещены до «Продолжить» (переживает перезапуск Bossman).")
        except CompanionError as exc:
            lines.append(f"• Остановку управления компьютером Bossman НЕ подтвердил ({exc}). "
                         "Локальный запрет поручений уже действует; проверьте Bossman на компьютере.")
        if cancelled:
            lines.append("• Текущая генерация отменяется.")
        elif full:
            lines.append("• Отменяемых генераций сейчас нет.")
        lines.append(STOPPED_NOTE)
        if not full:
            lines.append("Идущие задачи не отменялись — это пауза на новое.")
        return Reply("\n".join(lines), [[self.button(person, "▶️ Продолжить", "/resume"),
                                         self.button(person, "🩺 Диагностика", "/diag")]])

    async def console_resume(self, person: Person):
        """Только владелец и только с повторным наблюдением."""
        from .service import Reply
        try:
            await self.core.computer_resume()
        except CompanionError as exc:
            return (f"«Продолжить» не подтверждено Bossman ({exc}). Запрет остаётся в силе — "
                    "молча снимать его я не буду.")
        try:
            obs = await self.fresh_screen()
        except CompanionError as exc:
            return (f"Управление разблокировано в Bossman, но свежее наблюдение экрана не получено ({exc}). "
                    "Новые поручения из Telegram оставляю заблокированными до успешного наблюдения: /resume ещё раз.")
        self.store.put("delegation_locked", False)
        window = _short((obs.get("window") or {}).get("title"), 120) or "(окно не определено)"
        return Reply("▶️ Продолжено. Прежние наблюдения обесценены, экран перечитан.\n"
                     f"Сейчас на экране: {window} · наблюдение #{obs.get('generation')}.\n"
                     "Новые поручения снова принимаются по подтверждению.",
                     [[self.button(person, "📸 Экран", "/screen"), self.button(person, "🏠 Меню", "/menu")]])

    # --------------------------------------------------------------- задачи
    async def propose_task(self, person: Person, prompt: str, message: dict):
        """Общий путь «кнопка → поручение Bossman»: тот же /task → /confirm с nonce.

        Побочного эффекта здесь нет: готовится предложение, которое владелец
        подтверждает отдельным сообщением, а исполняет назначенный агент со
        своими правами и подтверждениями ядра."""
        return await self.handle(person, {**message, "text": "/task " + prompt[:2500]})
