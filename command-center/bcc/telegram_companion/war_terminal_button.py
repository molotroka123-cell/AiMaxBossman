"""«🛰 Терминал» — owner-only button next to «Покер» that opens the War Terminal (apps/war-terminal, :8830) (owner 17.10).

Telegram opens a Mini App (web_app button) only from an HTTPS URL. The terminal is exposed tailnet-only through
`tailscale serve`; never through a public tunnel (owner-only defence data). So:

  * BOSSMAN_WAR_TERMINAL_URL=https://<host>[:port]  -> web_app button to <base>/tg/ (the initData-checked Mini App);
  * otherwise (default: the tailnet HTTP address)    -> ordinary url button, the terminal opens in the browser.

Setting the env var to the HTTPS address and restarting the pult is the only step once Tailscale HTTPS is enabled.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

from .config import Person

TERMINAL_COMMANDS = {"/terminal"}
TERMINAL_LABEL = "🛰 Терминал"
ENV = "BOSSMAN_WAR_TERMINAL_URL"
DEFAULT_URL = "http://win-iqprhtsajl9.tail0f845b.ts.net:8830"


def _clean(base: str) -> tuple[str, str] | None:
    """(scheme, url without trailing slash) for a plain http(s) URL; anything with login/query/fragment is refused."""
    u = urlsplit((base or "").strip())
    if u.scheme not in {"http", "https"} or not u.hostname or u.username or u.password or u.query or u.fragment:
        return None
    return u.scheme, f"{u.scheme}://{u.netloc}{u.path.rstrip('/')}"


def terminal_button(environ=None):
    """(label, {"web_app": {...}}) over HTTPS, else (label, {"url": ...}). Rendered by adapters.markup."""
    env = os.environ if environ is None else environ
    parsed = _clean(env.get(ENV, "")) or _clean(DEFAULT_URL)
    scheme, base = parsed
    if scheme == "https":
        url = base if base.endswith("/tg") else base + "/tg"
        return (TERMINAL_LABEL, {"web_app": {"url": url + "/"}})
    return (TERMINAL_LABEL, {"url": base + "/"})


def terminal_text(person: Person, environ=None):
    from .service import Reply

    if person.role != "owner":
        return "Терминал доступен только владельцу."
    label, target = terminal_button(environ)
    if "web_app" in target:
        note = "Откроется внутри Telegram (Mini App, вход по подписи Telegram, только владелец)."
    else:
        note = ("Откроется в браузере телефона (нужен включённый Tailscale). Внутри Telegram (Mini App) — "
                "после включения HTTPS-сертификатов Tailscale.")
    return Reply(f"🛰 War Terminal — тендеры оборонки. {note}", [[(label, target)]])
