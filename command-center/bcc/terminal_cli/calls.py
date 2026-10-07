"""`bossman call …` — Telegram-звонки ассистента на ваш ВТОРОЙ аккаунт: тонкий клиент ТОГО ЖЕ Command Center API.

Никакого второго движка: те же /api/telegram/calls/*, что и у панели «Telegram-звонки» в дашборде. Терминал ничего не
хранит: ни ключей, ни номера, ни кода, ни пароля.

Секреты (api_id, api_hash, номер, код входа, пароль 2FA) НИКОГДА не принимаются в аргументах командной строки
(история оболочки и список процессов): только скрытый ввод или `--stdin` (по строке на запрос). Аргумент, похожий на
секрет, отклоняется, не попадая ни в вывод, ни в журнал.

Машинный режим (`--json`): по записи на строку (`bossman.events.v1`, type `call_*`); слова — в stderr. Коды выхода —
records.EXIT_*; «Telegram не подключён» — это kind=blocked (EXIT_BLOCKED), а не auth: auth — это токен самого Bossman.
Звонок с `--wait`: 0 — разговор завершён; 6 — остановлен (STOP); 8 — исход неизвестен или связь потеряна
(проверьте второй аккаунт, автоперезвона нет); 1 — звонок не состоялся.
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Any, Callable

from .api_client import BossmanError, Client
from .console import sanitize
from .keys import read_secret
from .records import (EXIT_BLOCKED, EXIT_FAIL, EXIT_INTERRUPTED, EXIT_NOT_SUPPORTED, EXIT_OK, EXIT_PARTIAL,
                      EXIT_STOPPED, EXIT_USAGE, record)

BASE = "/api/telegram/calls"   # the ONE canonical path (the former /api/calls alias was removed from the backend)
TEST_LABEL = "ТЕСТ БЕЗ TELEGRAM"

#: refusals of the guard / of the account state: kind=blocked (exit 5), never "auth" (that is Bossman's own token)
BLOCKED_CODES = frozenset({
    "NOT_ENABLED", "NO_CREDENTIALS", "NOT_LOGGED_IN", "PEER_NOT_SELECTED", "PEER_NOT_ALLOWED", "PEER_IS_SELF", "STOP_ACTIVE",
    "UNCERTAIN_PREVIOUS_CALL", "CALL_IN_PROGRESS", "DEPENDENCIES_MISSING", "SESSION_REVOKED", "LOGIN_NOT_PENDING",
    "PEER_PRIVACY", "BRAIN_NOT_CONFIGURED", "PEER_NOT_CONFIRMED", "CONFIRM_REQUIRED", "MEMORY_NOT_CONFIGURED", "NO_PROPOSED_TASKS",
    "ANSWERING_NOT_ENABLED", "ANSWERING_NOT_READY", "INCOMING_NOT_SUPPORTED", "REPORT_NOT_FOUND"})

STATE_WORDS = {"idle": "нет звонка", "dialing": "набираем", "ringing": "звонит", "active": "идёт разговор",
               "ending": "завершаем", "ended": "нет звонка"}
PHASE_WORDS = {"listening": "слушает", "thinking": "думает", "speaking": "говорит"}
OUTCOME_WORDS = {"completed": "разговор завершён", "declined": "собеседник отклонил", "busy": "собеседник занят",
                 "no_answer": "не ответил", "connection_lost": "связь потеряна", "stopped": "остановлен (STOP)",
                 "max_duration": "достигнут лимит времени", "silence_timeout": "долгая тишина", "failed": "звонок не удался",
                 "unknown": "исход неизвестен"}
ACCOUNT_WORDS = {"no_credentials": "ключи не сохранены", "logged_out": "ключи сохранены, вход не выполнен",
                 "code_sent": "ждём код из Telegram", "password_needed": "нужен пароль двухэтапной защиты",
                 "ready": "подключён", "error": "сохранённые ключи не читаются"}
WAIT_EXIT = {"completed": EXIT_OK, "stopped": EXIT_STOPPED, "unknown": EXIT_PARTIAL, "connection_lost": EXIT_PARTIAL,
             "max_duration": EXIT_OK, "silence_timeout": EXIT_OK}


# ----------------------------------------------------------------- plumbing


def _cli():
    from . import cli
    return cli


def _fail(out, exc: BossmanError, what: str) -> int:
    """API failure -> words + (machine) record with the stable code; guard refusals are kind=blocked."""
    cli = _cli()
    kind = "blocked" if exc.code in BLOCKED_CODES else exc.kind
    code = cli.ERROR_EXIT.get(kind, EXIT_FAIL)
    if out.machine:
        out.json(record("error", ok=False, operation=what, error=sanitize(exc.message), hint=sanitize(exc.hint) if exc.hint else None,
                        code=exc.code, kind=kind, exit_code=code))
    sys.stderr.write(f"bossman: {sanitize(exc.message)}\n")
    if exc.hint:
        sys.stderr.write(f"  подсказка: {sanitize(exc.hint)}\n")
    sys.stderr.flush()
    return code


def _run(args, fn: Callable[[Client, Any], int], *, what: str) -> int:
    cli = _cli()
    out = cli.Out(getattr(args, "output_format", None) or "text")
    try:
        client = cli.connect(args)
    except BossmanError as exc:
        return cli.fail(out, exc, what="connect")
    with client:
        try:
            return fn(client, out)
        except BossmanError as exc:
            return _fail(out, exc, what)
        except cli.UsageError as exc:
            return cli.fail(out, exc, what=what)
        except KeyboardInterrupt:
            out.say("прервано; звонок не тронут. Завершить: bossman call hangup · остановить: bossman call stop")
            return EXIT_INTERRUPTED


def _emit(out, type_: str, human: list[str] | None = None, exit_code: int = EXIT_OK, **fields: Any) -> int:
    if out.machine:
        out.json(record(type_, ok=exit_code == EXIT_OK, exit_code=exit_code, **fields))
    else:
        for line in human or []:
            out.say(line)
    return exit_code


def _usage(message: str):
    return _cli().UsageError(message)


def _is_test(st: dict) -> bool:
    return bool(st.get("mode") == "offline_test" or st.get("transport") == "loopback" or st.get("test_label"))


def _ms(v: Any) -> str:
    return f"{round(v)} мс" if isinstance(v, (int, float)) and not isinstance(v, bool) else "—"


def _get_status(client: Client) -> dict:
    return client.get(f"{BASE}/status") or {}


# ----------------------------------------------------------------- status


def status_lines(st: dict) -> list[str]:
    a = st.get("account") or {}
    lines = []
    if _is_test(st):
        lines.append(f"!!! {TEST_LABEL}: проверка нашего тракта на тестовом собеседнике, настоящий звонок не совершается !!!")
    lines.append("Подключение")
    lines.append(f"  ключи api_id/api_hash: {'сохранены (api_id ' + str(a.get('api_id') or '…') + ')' if a.get('has_api') else 'не сохранены'}")
    phone = f" ({a['phone']})" if a.get("phone") else ""
    lines.append(f"  аккаунт: {ACCOUNT_WORDS.get(a.get('state'), 'нет данных')}{phone}")
    deps = st.get("deps") or {}
    lines.append("  зависимости звонков: " + ("готовы" if deps.get("ready") else "не хватает: " + ", ".join(deps.get("missing") or ["?"])))
    peer = st.get("peer")
    lines.append("Тестовый собеседник: " + (f"{peer.get('label') or 'без имени'} (id {peer.get('user_id')})" if peer else "не выбран"))
    lines.append(answer_line(st.get("answering") or {}))
    stop = st.get("stop") or {}
    stop_txt = "нет" if not stop.get("active") else ("STOP звонков" if stop.get("call") else "общий STOP Bossman")
    lines.append(f"Звонки: {'включены' if st.get('enabled') else 'выключены'} · STOP: {stop_txt}")
    call = st.get("call")
    if call:
        phase = PHASE_WORDS.get(call.get("phase") or "")
        lines.append(f"Звонок: {STATE_WORDS.get(call.get('state'), call.get('state') or '?')}" + (f", ассистент {phase}" if phase else ""))
    else:
        lines.append("Звонок: нет")
    lat = st.get("latency") or {}
    if lat.get("n"):
        lines.append(f"Задержка ответа (конец реплики → первый звук): последняя {_ms(lat.get('last'))}, p50 {_ms(lat.get('p50'))}, "
                     f"p95 {_ms(lat.get('p95'))}, замеров {lat.get('n')}")
    else:
        lines.append("Задержка ответа: замеров ещё нет")
    m = st.get("models") or {}
    if m:
        lines.append(f"Модели (как сообщает движок): распознавание {m.get('stt') or '—'} · ответ {m.get('llm') or '—'} · голос {m.get('tts') or '—'}")
    last = st.get("last_call")
    if last:
        lines.append(f"Последний звонок: {OUTCOME_WORDS.get(last.get('outcome'), last.get('outcome') or '?')}, реплик {last.get('turns', 0)}")
    err = st.get("last_error")
    if err:
        lines.append(f"Последняя ошибка: {err.get('message')} {err.get('hint') or ''}".rstrip())
    if st.get("uncertain_previous"):
        lines.append("ВНИМАНИЕ: исход предыдущего звонка неизвестен; следующий набор — только с --confirm-unknown. Автоперезвона нет.")
    acl = st.get("acl") or {}
    if acl and not acl.get("ok", True):
        lines.append("Права на файлы звонков шире нужного: bossman call doctor")
    return lines


def answer_line(a: dict) -> str:
    if not a.get("enabled"):
        return "Автоответчик: выключен (так задано по умолчанию)"
    if not a.get("armed"):
        return "Автоответчик: включён в настройках, но не запущен (bossman call answer on) — входящие не принимаются"
    state = {"ready": "готов", "loading": "модели загружаются — входящий будет пропущен", "failed": "модели не загрузились — входящий будет пропущен",
             "idle": "готовится"}.get(a.get("ready_state") or "", "готовится")
    extra = ""
    if a.get("in_call"):
        extra = ", сейчас отвечает на звонок"
    elif a.get("ringing"):
        extra = ", идёт звонок (ждёт, ответите ли вы сами)"
    if a.get("stopped"):
        extra += ", ОСТАНОВЛЕН (STOP): не отвечает до `bossman call resume`"
    pending = f", отчётов ждут отправки: {a['pending_reports']}" if a.get("pending_reports") else ""
    live = "" if a.get("live_tested") else " [приём настоящих входящих ещё не проверен в живую]"
    return f"Автоответчик: слушает, {state}; пауза до ответа {a.get('ring_delay_s')} с{extra}{pending}{live}"


def cmd_status(args) -> int:
    def run(client: Client, out) -> int:
        st = _get_status(client)
        return _emit(out, "call_status", status_lines(st), **{k: v for k, v in st.items() if k != "settings"})
    return _run(args, run, what="call status")


# ----------------------------------------------------------------- setup (secrets: hidden prompt or --stdin, never argv)

_SECRET_FLAGS = ("api_id", "api_hash", "phone", "code", "password")


def _refuse_argv_secrets(args) -> None:
    if getattr(args, "forbidden_value", None) or any(getattr(args, f"opt_{name}", None) is not None for name in _SECRET_FLAGS):
        raise _usage("ключи, номер телефона, код и пароль в командной строке не принимаю: они остаются в истории оболочки и "
                     "списке процессов. Введите их в скрытом запросе `bossman call setup` или подайте построчно через --stdin "
                     "(api_id, api_hash, номер, код, при необходимости пароль 2FA)")


def cmd_setup(args) -> int:
    cli = _cli()
    out = cli.Out(getattr(args, "output_format", None) or "text")
    try:
        _refuse_argv_secrets(args)
    except cli.UsageError as exc:
        return cli.fail(out, exc, what="call setup")

    def ask(prompt: str) -> str:
        value = read_secret(prompt + " (ввод скрыт): ", from_stdin=bool(getattr(args, "stdin", False)))
        if not value:
            raise _usage("пустой ввод: настройка прервана, ничего не отправлено")
        return value

    def run(client: Client, out) -> int:
        stages: list[str] = []
        st = _get_status(client)
        if _is_test(st):
            out.say(f"{TEST_LABEL}: вход выполняется на тестовом контуре; тестовые номер и код — в docs/owner/TELEGRAM_CALLS.md")
        state = (st.get("account") or {}).get("state")
        if state in ("no_credentials", "error", None):
            out.say("Шаг 1 из 4. api_id и api_hash: my.telegram.org → API development tools")
            api_id_text = ask("api_id")
            api_hash = ask("api_hash")
            if not api_id_text.isdigit():
                raise _usage("api_id — число")
            client.post(f"{BASE}/credentials", {"api_id": int(api_id_text), "api_hash": api_hash})
            api_hash = ""
            stages.append("credentials_saved")
            out.say("  ключи сохранены (зашифрованы хранилищем Bossman, обратно не показываются)")
            state = "logged_out"
        if state in ("logged_out", "code_sent"):
            out.say("Шаг 2 из 4. Номер телефона основного аккаунта (международный формат)")
            client.post(f"{BASE}/login/start", {"phone": ask("номер")})
            stages.append("code_sent")
            out.say("  код отправлен в приложение Telegram")
            state = "code_sent"
        attempts = 0
        while state == "code_sent":
            out.say("Шаг 3 из 4. Код из Telegram (не пересылайте его в чатах: Telegram его аннулирует)")
            try:
                res = client.post(f"{BASE}/login/code", {"code": ask("код")}) or {}
            except BossmanError as exc:
                attempts += 1
                if exc.code == "LOGIN_CODE_INVALID" and attempts < 3 and not getattr(args, "stdin", False):
                    sys.stderr.write(f"  {sanitize(exc.message)} Попробуйте ещё раз.\n")
                    continue
                raise
            state = res.get("state") or "ready"
            stages.append("code_accepted")
        if state == "password_needed":
            out.say("Шаг 4 из 4. Пароль двухэтапной защиты")
            res = client.post(f"{BASE}/login/password", {"password": ask("пароль")}) or {}
            state = res.get("state") or "ready"
            stages.append("password_accepted")
        after = _get_status(client)
        ready = (after.get("account") or {}).get("state") == "ready"
        if ready:
            stages.append("connected")
        human = ["Аккаунт подключён. Дальше: bossman call contacts → bossman call peer set <id> --confirm → bossman call enable"] if ready else \
            [f"Вход не завершён: {ACCOUNT_WORDS.get((after.get('account') or {}).get('state'), 'неизвестное состояние')}"]
        return _emit(out, "call_setup", human, EXIT_OK if ready else EXIT_BLOCKED, stages=stages,
                     account=after.get("account"), mode=after.get("mode"), test_label=_is_test(after))
    return _run(args, run, what="call setup")


def cmd_logout(args) -> int:
    def run(client: Client, out) -> int:
        res = client.post(f"{BASE}/logout") or {}
        return _emit(out, "call_logout", ["Вы вышли из аккаунта: сессия забыта, тестовый собеседник сброшен, звонки выключены"],
                     state=res.get("state"), peer_cleared=res.get("peer_cleared"))
    return _run(args, run, what="call logout")


# ----------------------------------------------------------------- contacts / peer / enable


def cmd_contacts(args) -> int:
    def run(client: Client, out) -> int:
        q = (getattr(args, "query", None) or "").strip()
        res = client.get(f"{BASE}/contacts", params={"q": q} if q else None) or {}
        items = res.get("contacts") or []
        human = [f"  {c.get('id')}\t{sanitize(c.get('label') or 'без имени')}" + (f"  @{sanitize(c['username'])}" if c.get("username") else "")
                 for c in items] or ["  никого не нашли: проверьте, что второй аккаунт есть в контактах"]
        return _emit(out, "call_contacts", human, contacts=items)
    return _run(args, run, what="call contacts")


def cmd_peer(args) -> int:
    action = getattr(args, "peer_cmd", None)
    cli = _cli()
    out = cli.Out(getattr(args, "output_format", None) or "text")
    if action is None:
        return cli.fail(out, _usage("нужно действие: peer show | peer set <id|@юзернейм> --confirm | peer clear"), what="call peer")
    if action == "set" and not getattr(args, "confirm", False):
        return cli.fail(out, _usage("выбор тестового собеседника нужно подтвердить: добавьте --confirm — это ВАШ второй аккаунт, "
                                    "звонок возможен только на него"), what="call peer")

    def run(client: Client, out) -> int:
        if action == "show":
            peer = _get_status(client).get("peer")
            return _emit(out, "call_peer", [("Тестовый собеседник: " + f"{peer.get('label') or 'без имени'} (id {peer.get('user_id')})")
                                            if peer else "Тестовый собеседник не выбран"], peer=peer)
        if action == "clear":
            client.delete(f"{BASE}/peer")
            return _emit(out, "call_peer", ["Выбор собеседника сброшен"], peer=None)
        target = str(args.target).strip()
        body: dict[str, Any] = {"user_id": int(target)} if target.isdigit() else {"username": target.lstrip("@")}
        res = client.request("PUT", f"{BASE}/peer", json={**body, "confirm": True}) or {}
        peer = res.get("peer") or {}
        return _emit(out, "call_peer", [f"Тестовый собеседник: {peer.get('label') or 'без имени'} (id {peer.get('user_id')})",
                                        "Звонки уже включены для этого собеседника." if res.get("enabled")
                                        else "Звонки при этом не включались (при смене собеседника они выключаются): bossman call enable"],
                     peer=peer, enabled=res.get("enabled"))
    return _run(args, run, what="call peer")


def _set_enabled(args, on: bool) -> int:
    def run(client: Client, out) -> int:
        res = client.request("PUT", f"{BASE}/settings", json={"enabled": on}) or {}
        note = ""
        if on:
            st = _get_status(client)
            if not st.get("peer"):
                note = " Тестовый собеседник ещё не выбран: bossman call peer set <id> --confirm."
        return _emit(out, "call_enabled", [("Звонки включены." if on else "Звонки выключены.") + note], enabled=bool(res.get("enabled")))
    return _run(args, run, what="call enable" if on else "call disable")


def cmd_enable(args) -> int:
    return _set_enabled(args, True)


def cmd_disable(args) -> int:
    return _set_enabled(args, False)


# ----------------------------------------------------------------- call control


def _event_line(ev: dict) -> str:
    kind = ev.get("kind")
    if kind == "state":
        return f"состояние: {STATE_WORDS.get(ev.get('state'), ev.get('state'))}"
    if kind == "phase":
        return f"ассистент {PHASE_WORDS.get(ev.get('phase'), ev.get('phase'))}"
    if kind == "turn":
        return f"реплика собеседника №{ev.get('turn')}"
    if kind == "metric":
        return f"задержка ответа №{ev.get('turn')}: {_ms(ev.get('response_latency_ms'))}"
    if kind == "barge_in":
        return "собеседник перебил"
    if kind == "echo":
        return "эхо подавлено"
    if kind == "error":
        return f"ошибка: {ev.get('code')}"
    if kind == "stop":
        return "STOP"
    if kind == "record":
        return f"звонок завершён: {OUTCOME_WORDS.get(ev.get('outcome'), ev.get('outcome'))}"
    if kind == "dial":
        return "набор начат"
    if kind == "worker":
        return f"процесс звонков: {ev.get('state')}"
    return str(kind)


def follow_events(client: Client, out, *, after: int, seconds: float, until_record: bool) -> tuple[int, dict | None]:
    """Print events until the call record arrives (or time is up / Ctrl+C). Returns (last seq, the record event)."""
    deadline = time.monotonic() + seconds
    ended = None
    while time.monotonic() < deadline:
        page = client.get(f"{BASE}/events", params={"after": after, "limit": 200}) or {}
        for ev in page.get("events") or []:
            after = max(after, int(ev.get("seq") or 0))
            if out.machine:
                out.json(record("call_event", **{k: v for k, v in ev.items() if k != "type"}))
            else:
                out.say("  " + _event_line(ev))
            if ev.get("kind") == "record":
                ended = ev
        if ended is not None and until_record:
            break
        time.sleep(0.7)
    return after, ended


def _final_summary(st: dict) -> list[str]:
    lat, m, last = st.get("latency") or {}, st.get("models") or {}, st.get("last_call") or {}
    lines = [f"Итог: {OUTCOME_WORDS.get(last.get('outcome'), last.get('outcome') or 'нет данных')}, реплик {last.get('turns', 0)}"]
    lines.append(f"Задержка ответа: p50 {_ms(lat.get('p50'))}, p95 {_ms(lat.get('p95'))}, замеров {lat.get('n', 0)}"
                 if lat.get("n") else "Задержка ответа: замеров нет")
    if m:
        lines.append(f"Модели: распознавание {m.get('stt') or '—'} · ответ {m.get('llm') or '—'} · голос {m.get('tts') or '—'}")
    if _is_test(st):
        lines.append(f"{TEST_LABEL}: настоящий звонок не совершался")
    return lines


def cmd_dial(args) -> int:
    def run(client: Client, out) -> int:
        before = _get_status(client)
        after = int(before.get("last_seq") or 0)
        body = {"confirm_unknown": True} if getattr(args, "confirm_unknown", False) else {}
        res = client.post(f"{BASE}/call", body) or {}
        test = bool(res.get("test_label")) or res.get("transport") == "loopback"
        code = _emit(out, "call_dial", [("Звонок начат" + (f" ({TEST_LABEL})" if test else "") + f": {res.get('call_id')}"),
                                        "Ход звонка: bossman call events --follow · завершить: bossman call hangup · STOP: bossman call stop"],
                     call_id=res.get("call_id"), transport=res.get("transport"), models=res.get("models"), test_label=test)
        if not getattr(args, "wait", False):
            return code
        try:
            follow_events(client, out, after=after, seconds=float(getattr(args, "seconds", 900) or 900), until_record=True)
        except KeyboardInterrupt:
            out.say("наблюдение прервано; звонок продолжается. Завершить: bossman call hangup · остановить: bossman call stop")
            return EXIT_INTERRUPTED
        final = _get_status(client)
        last = final.get("last_call") or {}
        exit_code = WAIT_EXIT.get(last.get("outcome"), EXIT_FAIL)
        return _emit(out, "call_result", _final_summary(final), exit_code, outcome=last.get("outcome"), call_id=last.get("call_id"),
                     latency=final.get("latency"), models=final.get("models"), turns=last.get("turns"), test_label=_is_test(final),
                     error=final.get("last_error"))
    return _run(args, run, what="call dial")


def cmd_hangup(args) -> int:
    def run(client: Client, out) -> int:
        res = client.post(f"{BASE}/hangup") or {}
        return _emit(out, "call_hangup", ["Разговор завершён" if res.get("ended") else "Сейчас нет звонка"], ended=bool(res.get("ended")))
    return _run(args, run, what="call hangup")


def cmd_stop(args) -> int:
    def run(client: Client, out) -> int:
        res = client.post(f"{BASE}/stop") or {}
        persisted = bool(res.get("persisted", res.get("stop_flag")))
        lines = ["STOP звонков: " + ("сохранён, набор заблокирован до `bossman call resume`" if persisted else "НЕ подтверждён — закройте звонок на втором аккаунте вручную")]
        if res.get("terminated"):
            lines.append("Процесс звонков не ответил вовремя и был остановлен принудительно")
        elif res.get("hangup_confirmed") is False:
            lines.append("Завершение звонка пока не подтверждено: проверьте второй аккаунт")
        return _emit(out, "call_stop", lines, EXIT_OK if persisted else EXIT_FAIL, persisted=persisted, worker=res.get("worker"),
                     hangup_confirmed=res.get("hangup_confirmed"), terminated=bool(res.get("terminated")))
    return _run(args, run, what="call stop")


def cmd_resume(args) -> int:
    def run(client: Client, out) -> int:
        res = client.post(f"{BASE}/resume") or {}
        lines = ["STOP звонков снят"]
        if res.get("global_stop"):
            lines.append("Общий STOP Bossman ещё действует: звонить нельзя, пока его не снимут (`/computer resume` в чате или панель)")
        return _emit(out, "call_resume", lines, stop_flag=bool(res.get("stop_flag")), global_stop=bool(res.get("global_stop")))
    return _run(args, run, what="call resume")


def cmd_events(args) -> int:
    def run(client: Client, out) -> int:
        after = int(getattr(args, "after", 0) or 0)
        if not getattr(args, "follow", False):
            page = client.get(f"{BASE}/events", params={"after": after, "limit": 200}) or {}
            for ev in page.get("events") or []:
                if out.machine:
                    out.json(record("call_event", **{k: v for k, v in ev.items() if k != "type"}))
                else:
                    out.say(f"  #{ev.get('seq')} {_event_line(ev)}")
            if out.machine:
                out.json(record("call_cursor", ok=True, last_seq=page.get("last_seq"), exit_code=EXIT_OK))
            else:
                out.say(f"(курсор {page.get('last_seq')})")
            return EXIT_OK
        try:
            last, _ = follow_events(client, out, after=after, seconds=float(getattr(args, "seconds", 600) or 600), until_record=False)
        except KeyboardInterrupt:
            out.say("наблюдение прервано; звонок не тронут")
            return EXIT_INTERRUPTED
        return _emit(out, "call_cursor", [f"(курсор {last})"], last_seq=last)
    return _run(args, run, what="call events")


def history_lines(items: list[dict]) -> list[str]:
    lines = []
    for it in items:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(it["started_at"])) if isinstance(it.get("started_at"), (int, float)) else "?"
        lat = it.get("latency_ms") or {}
        test = f" [{TEST_LABEL}]" if it.get("transport") not in (None, "telegram") else ""
        lines.append(f"  {it.get('call_id')}  {when}  {OUTCOME_WORDS.get(it.get('outcome'), it.get('outcome') or '?')}{test}  "
                     f"реплик {len(it.get('turns') or [])}  p50 {_ms(lat.get('p50'))}")
        summary = (it.get("summary") or {}).get("text")
        if summary:
            lines.append("      " + sanitize(summary)[:200])
        post = it.get("postcall") or {}
        mem = (post.get("memory") or {}).get("status")
        if mem in ("written", "exists"):
            lines.append("      итог записан в память Bossman")
        proposals = len((it.get("summary") or {}).get("agreed_tasks") or [])
        if proposals:
            lines.append(f"      предложено задач: {proposals}" + (f", черновиков: {(post.get('drafts') or {}).get('count', 0)}"))
    return lines


def cmd_history(args) -> int:
    def run(client: Client, out) -> int:
        limit = max(1, min(int(getattr(args, "limit", 10) or 10), 200))
        res = client.get(f"{BASE}/history", params={"limit": limit}) or {}
        items = res.get("items") or []
        return _emit(out, "call_history", history_lines(items) or ["  звонков ещё не было"], items=items)
    return _run(args, run, what="call history")


def cmd_save_memory(args) -> int:
    def run(client: Client, out) -> int:
        res = client.post(f"{BASE}/history/{args.call_id}/save-memory") or {}
        mem = res.get("memory") or {}
        return _emit(out, "call_remember", [f"Итог записан в память Bossman ({mem.get('file') or mem.get('status')})"], memory=mem)
    return _run(args, run, what="call save-memory")


def cmd_draft_tasks(args) -> int:
    def run(client: Client, out) -> int:
        res = client.post(f"{BASE}/history/{args.call_id}/draft-tasks") or {}
        drafts = res.get("drafts") or {}
        return _emit(out, "call_drafts", [f"Черновиков задач: {drafts.get('count', 0)} (ничего не запущено; решает владелец)"], drafts=drafts)
    return _run(args, run, what="call draft-tasks")


# ----------------------------------------------------------------- answering machine (incoming calls)


def _answer_switch(args, on: bool) -> int:
    def run(client: Client, out) -> int:
        res = client.request("PUT", f"{BASE}/settings", json={"answering_machine": on}) or {}
        ans = res.get("answering") or {}
        if not on:
            return _emit(out, "call_answer", ["Автоответчик выключен: входящие звонит вам, как обычно."], answering=ans, enabled=False)
        err = ans.get("error") or {}
        if err:
            return _emit(out, "call_answer", ["Автоответчик включён в настройках, но не запущен: " + sanitize(str(err.get("message") or err.get("code"))),
                                              ("Что сделать: " + sanitize(str(err["hint"]))) if err.get("hint") else ""],
                         EXIT_BLOCKED, answering=ans, enabled=True, error=err.get("code"))
        return _emit(out, "call_answer", ["Автоответчик включён и слушает. Если вы не возьмёте трубку за паузу, ответит Джефф "
                                          "(представится ассистентом), а итог придёт вам в Telegram.",
                                          "Приём настоящих входящих ещё НЕ проверен в живую: проверка — `bossman call selftest answering-machine`."],
                     answering=ans, enabled=True)
    return _run(args, run, what="call answer on" if on else "call answer off")


def _answer_status(args) -> int:
    def run(client: Client, out) -> int:
        a = client.get(f"{BASE}/answering") or {}
        return _emit(out, "call_answer_status", [answer_line(a)], **{k: v for k, v in a.items() if k != "stop"})
    return _run(args, run, what="call answer status")


def _answer_config(args) -> int:
    cli = _cli()
    out = cli.Out(getattr(args, "output_format", None) or "text")
    body: dict[str, Any] = {}
    if getattr(args, "ring_delay", None) is not None:
        body["answer_ring_delay_s"] = args.ring_delay
    if getattr(args, "max_call", None) is not None:
        body["answer_max_call_s"] = args.max_call
    if getattr(args, "allow_unknown", None) is not None:
        body["answer_allow_unknown"] = args.allow_unknown == "yes"
    if getattr(args, "greeting", None) is not None:
        body["answer_greeting"] = args.greeting
    if getattr(args, "clear_allow", False) and getattr(args, "allow", None):
        return cli.fail(out, _usage("--clear-allow и --allow вместе не имеют смысла"), what="call answer config")
    if getattr(args, "clear_deny", False) and getattr(args, "deny", None):
        return cli.fail(out, _usage("--clear-deny и --deny вместе не имеют смысла"), what="call answer config")

    def run(client: Client, out) -> int:
        patch = dict(body)
        if getattr(args, "allow", None) or getattr(args, "deny", None) or getattr(args, "clear_allow", False) or getattr(args, "clear_deny", False):
            current = client.get(f"{BASE}/settings") or {}
            if getattr(args, "allow", None) or getattr(args, "clear_allow", False):
                patch["answer_allow_ids"] = [] if args.clear_allow else sorted({*(current.get("answer_allow_ids") or []), *args.allow})
            if getattr(args, "deny", None) or getattr(args, "clear_deny", False):
                patch["answer_deny_ids"] = [] if args.clear_deny else sorted({*(current.get("answer_deny_ids") or []), *args.deny})
        if not patch:
            raise _usage("нечего менять: укажите --ring-delay, --max-call, --allow, --deny, --allow-unknown или --greeting")
        res = client.request("PUT", f"{BASE}/settings", json=patch) or {}
        lines = [f"Пауза до ответа: {res.get('answer_ring_delay_s')} с · максимум звонка: {res.get('answer_max_call_s')} с · "
                 f"неопознанные: {'отвечаем' if res.get('answer_allow_unknown') else 'не отвечаем'}",
                 f"Разрешённые: {res.get('answer_allow_ids') or 'любой звонящий'} · чёрный список: {res.get('answer_deny_ids') or 'пусто'}"]
        return _emit(out, "call_answer_config", lines, settings={k: v for k, v in res.items() if k.startswith("answer")})
    return _run(args, run, what="call answer config")


def _answer_reports(args) -> int:
    from ..telegram_calls.answering_store import render_notice

    def run(client: Client, out) -> int:
        rid = getattr(args, "report_id", None)
        if rid:
            report = client.get(f"{BASE}/answering/reports/{rid}") or {}
            return _emit(out, "call_answer_report", [sanitize(line) if line else "" for line in render_notice(report).splitlines()], report=report)
        limit = max(1, min(int(getattr(args, "limit", 10) or 10), 200))
        params = {"limit": limit}
        if getattr(args, "pending", False):
            params["pending"] = "true"
        items = (client.get(f"{BASE}/answering/reports", params=params) or {}).get("items") or []
        lines = []
        for r in items:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["received_at"])) if isinstance(r.get("received_at"), (int, float)) else "?"
            who = sanitize(str((r.get("caller") or {}).get("label") or "") or ("id " + str((r.get("caller") or {}).get("id")) if (r.get("caller") or {}).get("known") else "неизвестный"))
            mark = " [ТЕСТ БЕЗ TELEGRAM]" if r.get("test") else ""
            lines.append(f"  {r.get('id')}  {when}  {who}  {sanitize(str(r.get('outcome_label') or r.get('outcome')))}{mark}"
                         + ("  (не отправлен владельцу)" if r.get("notify") and not r.get("delivered") else ""))
        return _emit(out, "call_answer_reports", lines or ["  входящих звонков в журнале нет"], items=items)
    return _run(args, run, what="call answer reports")


def cmd_answer(args) -> int:
    action = getattr(args, "answer_cmd", None)
    if action is None:
        cli = _cli()
        return cli.fail(cli.Out(getattr(args, "output_format", None) or "text"),
                        _usage("нужно действие: answer on | off | status | config | reports"), what="call answer")
    return {"on": lambda a: _answer_switch(a, True), "off": lambda a: _answer_switch(a, False), "status": _answer_status,
            "config": _answer_config, "reports": _answer_reports}[action](args)


# ----------------------------------------------------------------- checks


def cmd_doctor(args) -> int:
    def run(client: Client, out) -> int:
        res = client.post(f"{BASE}/doctor") or {}
        rows = res.get("rows") or []
        lines = [f"  [{r.get('status')}] {r.get('check')}: {sanitize(r.get('detail') or '')}"
                 + (f"\n        что делать: {sanitize(r['remedy'])}" if r.get("remedy") else "") for r in rows]
        blocked = any(r.get("status") == "BLOCKED" for r in rows)
        return _emit(out, "call_doctor", lines + [f"Итог диагностики: {res.get('verdict')}"], EXIT_BLOCKED if blocked else EXIT_OK,
                     rows=rows, verdict=res.get("verdict"))
    return _run(args, run, what="call doctor")


def cmd_selftest(args) -> int:
    def run(client: Client, out) -> int:
        scenario = (getattr(args, "scenario", None) or "all").replace("-", "_")        # `answering-machine` == `answering_machine`
        out.say(f"{TEST_LABEL}: проверяю аудиоконтур ({scenario}); настоящий звонок не совершается. Это займёт до минуты…")
        res = client.request("POST", f"{BASE}/selftest", json={"scenario": scenario}, timeout=480) or {}
        lines = [f"  [{r.get('verdict')}] {r.get('scenario')}: " + ", ".join(f"{'✓' if v else '✗'} {k}" for k, v in (r.get("checks") or {}).items())
                 for r in res.get("results") or []]
        ok = res.get("verdict") == "PASS"
        return _emit(out, "call_selftest", lines + [f"Итог: {res.get('verdict')} — {TEST_LABEL}"], EXIT_OK if ok else EXIT_FAIL,
                     verdict=res.get("verdict"), results=res.get("results"), label=TEST_LABEL, evidence_level=res.get("evidence_level"))
    return _run(args, run, what="call selftest")


def cmd_install(args) -> int:
    """Server-side add-on install (the backend downloads, verifies sha256 BEFORE writing, unpacks): this is a thin client."""
    def run(client: Client, out) -> int:
        try:
            cur = client.get(f"{BASE}/install") or {}
        except BossmanError as exc:
            if exc.code == "NOT_AVAILABLE" or exc.kind == "not_supported":
                return _emit(out, "call_install", ["NOT_AVAILABLE: установщик зависимостей звонков в этой сборке отсутствует.",
                                                   'Установите вручную: pip install "bossman-command-center[calls]"'], EXIT_NOT_SUPPORTED,
                             state="NOT_AVAILABLE", remedy='pip install "bossman-command-center[calls]"')
            raise
        addon = cur.get("addon") or {}
        if addon.get("complete"):
            return _emit(out, "call_install", ["Зависимости звонков уже на месте."], state="already_satisfied", missing=[])
        if not addon.get("installer_supported"):
            return _emit(out, "call_install", [f"Встроенный установщик не поддерживает эту платформу ({addon.get('this_platform')}).",
                                               f"Выполните: {addon.get('remedy')}"], EXIT_NOT_SUPPORTED, state="NOT_AVAILABLE",
                         missing=addon.get("missing"), remedy=addon.get("remedy"))
        if getattr(args, "dry_run", False):
            return _emit(out, "call_install", ["Будет скачано (sha256 проверяется до записи, только по HTTPS из зафиксированного списка): "
                                               + ", ".join(addon.get("missing") or []),
                                               f"Каталог: {addon.get('path')}. Установка: bossman call install"],
                         state="dry_run", missing=addon.get("missing"), path=addon.get("path"))
        out.say("Устанавливаю зависимости звонков на стороне Bossman (сеть используется только сейчас, по вашей команде)…")
        try:
            cur = client.post(f"{BASE}/install") or {}
        except BossmanError as exc:
            if exc.code == "NOT_AVAILABLE":
                return _emit(out, "call_install", ["NOT_AVAILABLE: " + exc.message, exc.hint or ""], EXIT_NOT_SUPPORTED, state="NOT_AVAILABLE")
            raise
        shown = 0
        deadline = time.monotonic() + float(getattr(args, "seconds", 900) or 900)
        while cur.get("state") == "running" and time.monotonic() < deadline:
            for line in (cur.get("progress") or [])[shown:]:
                out.say("  " + sanitize(str(line)))
            shown = len(cur.get("progress") or [])
            time.sleep(1.0)
            cur = client.get(f"{BASE}/install") or {}
        for line in (cur.get("progress") or [])[shown:]:
            out.say("  " + sanitize(str(line)))
        if cur.get("state") == "done":
            result = cur.get("result") or {}
            return _emit(out, "call_install", ["Готово. Процесс звонков подхватит пакеты при следующем запуске."],
                         state=result.get("status"), installed=result.get("installed"), path=result.get("path"))
        if cur.get("state") == "running":
            return _emit(out, "call_install", ["Установка ещё идёт на стороне Bossman: проверьте позже `bossman call install --dry-run`."],
                         EXIT_PARTIAL, state="running")
        err = cur.get("error") or {}
        return _emit(out, "call_install", [f"Установка не удалась ({err.get('code') or 'ошибка'}). Ничего не записано.",
                                           f"Вручную: {addon.get('remedy')}"], EXIT_FAIL, state="failed", error=err.get("code"))
    return _run(args, run, what="call install")


# ----------------------------------------------------------------- dispatch

HANDLERS = {
    "setup": cmd_setup, "status": cmd_status, "contacts": cmd_contacts, "peer": cmd_peer, "enable": cmd_enable,
    "disable": cmd_disable, "dial": cmd_dial, "hangup": cmd_hangup, "stop": cmd_stop, "resume": cmd_resume,
    "events": cmd_events, "history": cmd_history, "save-memory": cmd_save_memory, "draft-tasks": cmd_draft_tasks, "doctor": cmd_doctor,
    "selftest": cmd_selftest, "install": cmd_install, "logout": cmd_logout, "answer": cmd_answer,
}


def run_call(args: argparse.Namespace) -> int:
    cli = _cli()
    name = getattr(args, "call_cmd", None)
    if name is None:
        out = cli.Out(getattr(args, "output_format", None) or "text")
        return cli.fail(out, _usage("нужна подкоманда: " + " | ".join(HANDLERS) + " (bossman call --help)"), what="call")
    return HANDLERS[name](args)
