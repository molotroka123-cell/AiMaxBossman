"""`bossman gmail …` — connect the OWNER's Gmail to the running Bossman.

Secrets (OAuth client secret, Gmail app password) never travel in argv: they come from a
downloaded client JSON file, hidden input or --stdin, go once to the backend in a request body
and are stored there encrypted (vault). Nothing secret is printed. The consent itself happens
in the owner's browser; this command only opens Google's page and waits for Bossman to report
the connection. Guide: docs/owner/GMAIL_CONNECT_RU.md.
"""
from __future__ import annotations

import getpass
import json
import sys
import time
import webbrowser
from pathlib import Path

from .api_client import BossmanError, Client
from .records import EXIT_OK, EXIT_USAGE, record


class GmailUsage(BossmanError):
    def __init__(self, message: str):
        super().__init__(message, kind="usage")


def _secret(prompt: str, use_stdin: bool) -> str:
    if use_stdin:
        return (sys.stdin.readline() or "").strip()
    return getpass.getpass(prompt).strip()


def _show(out, st: dict) -> None:
    if out.machine:
        out.json(record("gmail.status", ok=True, **st, exit_code=EXIT_OK))
        return
    if st.get("connected"):
        out.say(f"Gmail подключён: {st.get('address')} (режим {st.get('mode')})")
        out.say("Разрешения: " + ", ".join(s.rsplit("/", 1)[-1] for s in st.get("scopes") or []))
    else:
        out.say("Gmail не подключён." + (" OAuth-клиент сохранён." if st.get("client_configured") else ""))
    out.say(f"Отправка: {'вкл' if st.get('send_enabled') else 'выкл'} · черновики: "
            f"{'вкл' if st.get('drafts_enabled') else 'выкл'} · агенты с доступом: "
            f"{', '.join('#' + str(a) for a in st.get('agent_ids') or []) or 'нет'}")
    if st.get("needs_reconsent"):
        out.say("Нужно переподключить (bossman gmail connect): включены новые разрешения.")
    res = st.get("last_oauth_result")
    if isinstance(res, dict) and not res.get("ok"):
        out.say("Последняя попытка входа: " + str(res.get("error")))


def run_gmail(client: Client, out, args) -> int:
    action = args.gmail_cmd or "status"
    if action == "status":
        _show(out, client.get("/api/gmail/status"))
        return EXIT_OK
    if action == "client":
        if args.file:
            try:
                body = json.loads(Path(args.file).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise GmailUsage(f"не читается файл клиента: {type(exc).__name__}") from None
            if not isinstance(body, dict):
                raise GmailUsage("файл клиента — не JSON-объект")
        else:
            cid = _secret("Client ID: ", args.stdin) if args.stdin else input("Client ID: ").strip()
            body = {"client_id": cid, "client_secret": _secret("Client secret (скрыт): ", args.stdin)}
        client.request("PUT", "/api/gmail/oauth-client", json=body)
        out.say("OAuth-клиент сохранён в Bossman (зашифрован). Дальше: bossman gmail connect")
        return EXIT_OK
    if action == "settings":
        body: dict = {}
        if args.send is not None:
            body["send_enabled"] = args.send == "on"
        if args.drafts is not None:
            body["drafts_enabled"] = args.drafts == "on"
        if args.address:
            body["owner_address"] = args.address
        if not body:
            raise GmailUsage("укажите --send on|off, --drafts on|off или --address")
        _show(out, client.request("PUT", "/api/gmail/settings", json=body))
        return EXIT_OK
    if action in ("allow-agent", "deny-agent"):
        st = client.get("/api/gmail/status")
        ids = set(st.get("agent_ids") or [])
        (ids.add if action == "allow-agent" else ids.discard)(int(args.agent_id))
        _show(out, client.request("PUT", "/api/gmail/settings", json={"agent_ids": sorted(ids)}))
        return EXIT_OK
    if action == "imap":
        address = args.address or (input("Адрес Gmail: ").strip() if not args.stdin else
                                   (sys.stdin.readline() or "").strip())
        pwd = _secret("Пароль приложения Google (скрыт): ", args.stdin)
        _show(out, client.request("PUT", "/api/gmail/imap", json={"address": address, "app_password": pwd}))
        return EXIT_OK
    if action == "connect":
        start = client.post("/api/gmail/oauth/start", {})
        url = start["auth_url"]
        out.say("Откройте страницу Google и разрешите доступ (войдите в СВОЙ аккаунт):")
        out.say(url)
        if not args.no_browser:
            try:
                webbrowser.open(url)
            except Exception:                           # noqa: BLE001 — no browser: the URL is printed
                pass
        deadline = time.monotonic() + min(float(args.seconds), float(start.get("expires_in") or 600))
        while time.monotonic() < deadline:
            time.sleep(2.0)
            st = client.get("/api/gmail/status")
            res = st.get("last_oauth_result")
            if isinstance(res, dict):
                _show(out, st)
                return EXIT_OK if res.get("ok") else 5
        out.say("Не дождался ответа Google. Если браузер открыл адрес 127.0.0.1 с ошибкой — "
                "выполните `bossman gmail complete` и вставьте этот адрес.")
        return 5
    if action == "complete":
        redirect = args.redirect_url or input("Адрес из браузера (http://127.0.0.1:…): ").strip()
        _show(out, client.post("/api/gmail/oauth/complete", {"redirect_url": redirect}))
        return EXIT_OK
    if action == "test":
        res = client.post("/api/gmail/test", {})
        out.say(("Проверка: " + str(res.get("summary"))) if res.get("ok") else
                ("Проверка не прошла: " + str(res.get("error"))))
        return EXIT_OK if res.get("ok") else 5
    if action == "disconnect":
        res = client.post("/api/gmail/disconnect", {"revoke": not args.keep_grant})
        out.say("Gmail отключён, ключи удалены из Bossman."
                + (" Доступ отозван в Google." if res.get("revoked_at_google") else ""))
        return EXIT_OK
    raise GmailUsage(f"неизвестное действие {action!r}")


def configure_parser(g, common, fmt) -> None:
    """Sub-actions of `bossman gmail` (the parser itself is created in cli.build_parser)."""
    common(g)
    fmt(g)
    gs = g.add_subparsers(dest="gmail_cmd")

    def leaf(name: str, helptext: str):
        p = gs.add_parser(name, help=helptext)
        common(p)
        fmt(p)
        return p

    leaf("status", "подключено ли, какой адрес, какие разрешения")
    p = leaf("client", "сохранить OAuth-клиент Desktop из Google Cloud: client --file client_secret.json")
    p.add_argument("--file", help="скачанный из Google Cloud JSON клиента (Desktop app)")
    p.add_argument("--stdin", action="store_true", help="client_id и client_secret построчно из stdin")
    p = leaf("connect", "войти через браузер (Google OAuth, по умолчанию только чтение)")
    p.add_argument("--no-browser", action="store_true", help="только напечатать адрес")
    p.add_argument("--seconds", type=float, default=600.0)
    p = leaf("complete", "вставить адрес 127.0.0.1, на котором остановился браузер")
    p.add_argument("redirect_url", nargs="?")
    p = leaf("imap", "запасной путь: адрес + пароль приложения Google (скрытый ввод)")
    p.add_argument("--address")
    p.add_argument("--stdin", action="store_true")
    p = leaf("settings", "отправка/черновики (по умолчанию выключены), адрес владельца")
    p.add_argument("--send", choices=("on", "off"))
    p.add_argument("--drafts", choices=("on", "off"))
    p.add_argument("--address")
    p = leaf("allow-agent", "разрешить агенту #id читать почту (и просить отправку через подтверждение)")
    p.add_argument("agent_id", type=int)
    p = leaf("deny-agent", "закрыть почту для агента #id")
    p.add_argument("agent_id", type=int)
    leaf("test", "проверить чтение: заголовок последнего письма")
    p = leaf("disconnect", "удалить ключи из Bossman и отозвать доступ в Google")
    p.add_argument("--keep-grant", action="store_true", help="не отзывать доступ в Google")


__all__ = ["configure_parser", "run_gmail", "EXIT_USAGE"]
