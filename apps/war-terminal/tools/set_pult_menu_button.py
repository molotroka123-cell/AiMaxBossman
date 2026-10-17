"""Set the pult (companion bot) menu button "Оборонка" -> War Terminal Mini App, ONLY for the owner's chat.

Bot API: setChatMenuButton {chat_id: <owner>, menu_button: {type: "web_app", text: "Оборонка", web_app: {url: <URL>/tg/}}}
A chat-scoped button: nobody else's chat is touched; the bot's default menu button stays as it is.

    python tools/set_pult_menu_button.py --url https://win-iqprhtsajl9.tail0f845b.ts.net           # dry run (default)
    python tools/set_pult_menu_button.py --url https://win-iqprhtsajl9.tail0f845b.ts.net --apply   # really send

The bot token and owner id are read at runtime from the companion files (same as warterminal.tgapp) and are never printed.
Only https URLs are accepted (Telegram requires HTTPS for Mini Apps).
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from warterminal.tgapp import load_bot_token, load_owner_id  # noqa: E402

BUTTON_TEXT = "Оборонка"


def webapp_url(base: str) -> str:
    """https://host[/prefix] -> https://host[/prefix]/tg/ ; refuses anything that is not a clean https URL."""
    u = urlsplit((base or "").strip())
    if u.scheme != "https" or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise ValueError("нужен https URL без логина, query и fragment (Mini Apps работают только по HTTPS)")
    path = u.path.rstrip("/")
    if not path.endswith("/tg"):
        path += "/tg"
    return f"https://{u.netloc}{path}/"


def build_payload(base: str, owner_id: int) -> dict:
    return {"chat_id": owner_id, "menu_button": {"type": "web_app", "text": BUTTON_TEXT, "web_app": {"url": webapp_url(base)}}}


def redacted(payload: dict) -> dict:
    return dict(payload, chat_id="<owner chat_id from companion config>")


def send(token: str, payload: dict, timeout: float = 20.0) -> dict:
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/setChatMenuButton", data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https Bot API host
        return json.loads(r.read().decode("utf-8"))


def main(argv: list[str] | None = None, sender=send) -> int:
    ap = argparse.ArgumentParser(description="pult menu button -> War Terminal Mini App (owner chat only)")
    ap.add_argument("--url", required=True, help="https base URL reachable from the phone, e.g. the tailnet URL")
    ap.add_argument("--apply", action="store_true", help="really call the Bot API (default: dry run, nothing is sent)")
    args = ap.parse_args(argv)
    try:
        url = webapp_url(args.url)
    except ValueError as exc:
        print(f"ОТКАЗ: {exc}", file=sys.stderr)
        return 2
    owner_id = load_owner_id()
    if not owner_id:
        print("ОТКАЗ: owner id не найден в config.json пульта (people[0], role=owner)", file=sys.stderr)
        return 3
    payload = build_payload(url, owner_id)
    print("POST https://api.telegram.org/bot<TOKEN>/setChatMenuButton")
    print(json.dumps(redacted(payload), ensure_ascii=False, indent=2))
    if not args.apply:
        print("DRY RUN: ничего не отправлено. Для отправки добавьте --apply.")
        return 0
    token = load_bot_token()
    if not token:
        print("ОТКАЗ: TG_COMPANION_BOT_TOKEN не найден в companion.env", file=sys.stderr)
        return 3
    try:
        res = sender(token, payload)
    except Exception as exc:  # noqa: BLE001 - report without the token (urllib errors may contain the URL)
        print(f"ОШИБКА: {type(exc).__name__}", file=sys.stderr)
        return 4
    ok = bool(isinstance(res, dict) and res.get("ok"))
    print("OK: кнопка установлена для чата владельца" if ok else f"Telegram ответил: {str(res.get('description') if isinstance(res, dict) else res)[:200]}")
    return 0 if ok else 5


if __name__ == "__main__":
    sys.exit(main())
