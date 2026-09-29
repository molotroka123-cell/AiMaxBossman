"""`bossman call ...` - a thin terminal client of /api/telegram/calls/*.

The terminal owns no state: every subcommand is one (or a few) API requests to the running Bossman. Rules:

* api_id, api_hash, phone, login code and 2FA password are asked ONLY with getpass (no echo) and are never
  accepted in argv, never printed, never written to a file by the terminal. No terminal -> refusal.
* `dial` has NO peer argument: the only callee is the confirmed second account stored in Bossman.
* `stop` = the same STOP as the web page (hangs up, sets the call-STOP flag); `resume` clears only that flag.
"""
from __future__ import annotations

import getpass
import sys
from typing import Any

from .api_client import BossmanError, Client
from .console import sanitize
from .records import EXIT_FAIL, EXIT_OK, record

BASE = "/api/telegram/calls"
SUBCOMMANDS = ("login", "status", "contacts", "peer", "dial", "hangup", "stop", "resume", "history",
               "doctor", "selftest", "install", "logout")


class NoTerminal(BossmanError):
    def __init__(self):
        super().__init__("нет терминала для скрытого ввода секрета", kind="usage",
                         hint="запустите `bossman call login` в обычном окне терминала")


def _secret(prompt: str) -> str:
    """Hidden prompt. The ONLY way a secret enters the terminal client."""
    if not (sys.stdin and sys.stdin.isatty()):
        raise NoTerminal()
    return getpass.getpass(prompt).strip()


def _ask(prompt: str) -> str:
    if not (sys.stdin and sys.stdin.isatty()):
        return ""
    return input(prompt).strip()


def _say(out, text: str) -> None:
    out.say(text)


def _emit(out, what: str, ok: bool, text: str, **fields: Any) -> int:
    code = EXIT_OK if ok else EXIT_FAIL
    if out.machine:
        out.json(record(what, ok=ok, exit_code=code, **fields))
    else:
        _say(out, text)
    return code


def _account_line(account: dict) -> str:
    return {"no_credentials": "нет api_id/api_hash", "logged_out": "не вошли", "code_sent": "ждём код",
            "password_needed": "нужен пароль 2FA", "ready": "подключён", "error": "ошибка"
            }.get(str(account.get("state")), str(account.get("state")))


def _status_text(st: dict) -> str:
    s = st.get("settings") or {}
    peer = s.get("peer") or {}
    stop = st.get("stop") or {}
    call = st.get("call")
    lines = [
        f"аккаунт: {_account_line(st.get('account') or {})}",
        f"звонки: {'разрешены' if s.get('enabled') else 'выключены'}",
        f"собеседник: {sanitize(peer.get('label') or peer.get('user_id') or '—')}"
        f"{' (подтверждён)' if s.get('peer_confirmed') else (' (НЕ подтверждён)' if peer else '')}",
        f"процесс звонков: {'запущен' if (st.get('worker') or {}).get('running') else 'не запущен'}",
        f"звонок: {'идёт ' + sanitize(call.get('call_id') or '') if call else 'нет'}",
        f"STOP: {'звонков' if stop.get('call_stop') else '—'}{', глобальный' if stop.get('global_stop') else ''}",
    ]
    last = st.get("last_call")
    if last:
        lines.append(f"последний звонок: {sanitize(last.get('outcome'))}")
    return "\n".join(lines)


def _status(client: Client) -> dict:
    return client.get(f"{BASE}/status") or {}


def _login(client: Client, out) -> int:
    st = _status(client)
    account = st.get("account") or {}
    state = account.get("state")
    if state == "ready":
        return _emit(out, "call_login", True, "Уже подключено. Чтобы сменить аккаунт: bossman call logout",
                     state=state)
    if state == "no_credentials":
        raw_id = _secret("api_id (my.telegram.org, ввод скрыт): ")
        api_hash = _secret("api_hash (ввод скрыт): ")
        try:
            api_id = int(raw_id)
        except ValueError:
            raise BossmanError("api_id — это число", kind="usage") from None
        account = client.post(f"{BASE}/login/credentials", {"api_id": api_id, "api_hash": api_hash})
        state = (account or {}).get("state")
    if state in ("logged_out", "error"):
        phone = _secret("Номер телефона в международном формате (ввод скрыт): ")
        account = client.post(f"{BASE}/login/start", {"phone": phone})
        state = (account or {}).get("state")
    if state == "code_sent":
        code = _secret("Код из Telegram (ввод скрыт): ")
        account = client.post(f"{BASE}/login/code", {"code": code})
        state = (account or {}).get("state")
    if state == "password_needed":
        password = _secret("Пароль двухэтапной защиты (ввод скрыт): ")
        account = client.post(f"{BASE}/login/password", {"password": password})
        state = (account or {}).get("state")
    ok = state == "ready"
    return _emit(out, "call_login", ok, "Подключено." if ok else f"Вход не завершён: {_account_line(account or {})}",
                 state=state)


def _peer(client: Client, out, args) -> int:
    if args.clear:
        client.delete(f"{BASE}/peer")
        return _emit(out, "call_peer", True, "Собеседник сброшен.", cleared=True)
    if args.user_id is None:
        s = (_status(client).get("settings")) or {}
        peer = s.get("peer") or {}
        text = (f"собеседник: {sanitize(peer.get('label') or '')} #{peer.get('user_id')}"
                f" ({'подтверждён' if s.get('peer_confirmed') else 'НЕ подтверждён'})") if peer else "собеседник не выбран"
        return _emit(out, "call_peer", True, text, peer=peer or None, confirmed=bool(s.get("peer_confirmed")))
    res = client.post(f"{BASE}/peer", {"user_id": args.user_id}) or {}
    peer = res.get("peer") or {}
    label = sanitize(peer.get("label") or peer.get("user_id") or args.user_id)
    confirmed = False
    if args.yes or (not out.machine and _ask(f"Разрешить звонки ТОЛЬКО этому аккаунту: {label}? [y/N] ").lower() in ("y", "yes", "д", "да")):
        client.post(f"{BASE}/peer/confirm", {"user_id": args.user_id})
        confirmed = True
    return _emit(out, "call_peer", True,
                 f"Собеседник {label}: {'подтверждён' if confirmed else 'выбран, но НЕ подтверждён (повторите с --yes)'}",
                 peer=peer, confirmed=confirmed)


def run_call(client: Client, out, args) -> int:
    sub = args.sub
    if sub == "login":
        return _login(client, out)
    if sub == "status":
        st = _status(client)
        return _emit(out, "call_status", True, _status_text(st), status=st)
    if sub == "contacts":
        items = (client.get(f"{BASE}/contacts") or {}).get("contacts", [])
        text = "\n".join(f"  {c.get('user_id')}  {sanitize(c.get('label') or '')}"
                         f"{'  @' + sanitize(c['username']) if c.get('username') else ''}" for c in items) or "  (пусто)"
        return _emit(out, "call_contacts", True, text, contacts=items)
    if sub == "peer":
        return _peer(client, out, args)
    if sub == "dial":
        res = client.post(f"{BASE}/dial", {"confirm_unknown": bool(args.confirm_unknown)}) or {}
        return _emit(out, "call_dial", bool(res.get("accepted")),
                     "Звонок запущен." if res.get("accepted") else "Звонок не принят.", **res)
    if sub in ("hangup", "stop"):
        res = client.post(f"{BASE}/{sub}") or {}
        return _emit(out, f"call_{sub}", True, "STOP: звонки остановлены." if sub == "stop" else "Положили трубку.", **res)
    if sub == "resume":
        st = client.post(f"{BASE}/resume") or {}
        return _emit(out, "call_resume", True, "STOP звонков снят (глобальный STOP компьютера не затронут).", status=st)
    if sub == "history":
        calls = (client.get(f"{BASE}/history", params={"limit": args.limit}) or {}).get("calls", [])
        text = "\n".join(f"  {sanitize(c.get('call_id'))}  {sanitize(c.get('outcome'))}  "
                         f"{sanitize(c.get('duration_s') if c.get('duration_s') is not None else '')}" for c in calls) or "  (пусто)"
        return _emit(out, "call_history", True, text, calls=calls)
    if sub == "doctor":
        checks = (client.get(f"{BASE}/doctor") or {}).get("checks", [])
        text = "\n".join(f"  {sanitize(c.get('status'))}  {sanitize(c.get('id'))}: {sanitize(c.get('message') or '')}" for c in checks)
        return _emit(out, "call_doctor", not any(c.get("status") == "BLOCKED" for c in checks), text, checks=checks)
    if sub == "selftest":
        res = client.post(f"{BASE}/selftest") or {}
        return _emit(out, "call_selftest", bool(res.get("ok")), f"{sanitize(res.get('status'))}: {sanitize(res.get('message') or '')}",
                     result=res)
    if sub == "install":
        if not args.yes and _ask("Установить зависимости звонков (может скачать пакеты)? [y/N] ").lower() not in ("y", "yes", "д", "да"):
            return _emit(out, "call_install", False, "Установка отменена (нужно подтверждение: --yes).", cancelled=True)
        res = client.post(f"{BASE}/install", {"confirm": True}) or {}
        return _emit(out, "call_install", bool(res.get("ok")), f"{sanitize(res.get('status'))}: {sanitize(res.get('message') or '')}",
                     result=res)
    if sub == "logout":
        if not args.yes and _ask("Выйти из аккаунта и стереть сессию? [y/N] ").lower() not in ("y", "yes", "д", "да"):
            return _emit(out, "call_logout", False, "Выход отменён (нужно подтверждение: --yes).", cancelled=True)
        account = client.post(f"{BASE}/logout") or {}
        return _emit(out, "call_logout", True, "Вышли, сессия стёрта.", state=account.get("state"))
    raise BossmanError(f"неизвестная подкоманда call {sanitize(sub)}", kind="usage")
