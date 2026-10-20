"""Send ONE finished Direct-Generator file to the owner's Пульт, on request only (owner rule 18.10).

Generations are never pushed automatically; the owner taps «Отправить в пульт» in the mobile app, the app calls the
backend, and the backend uploads that one file as a Telegram DOCUMENT (original bytes, no compression) through the
Bot API with the token of the existing companion (``companion.env`` beside ``config.json``).

Safety: the recipient is the ONLY person in the companion config (refused when there are none or several, or when
that person is not the owner); the token and the chat id never leave this module (not in logs, errors or answers —
httpx error texts can hold the request URL with the token, so they are never forwarded); the 49 MB document limit is
checked before any network call. There is no de-duplication: each tap sends again.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

_BOT_URL = re.compile(r"/bot\d+:[A-Za-z0-9_-]+")


class _HideBotToken(logging.Filter):
    """httpx logs «HTTP Request: POST https://api.telegram.org/bot<TOKEN>/sendDocument» at INFO: blank the token."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            text = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        if _BOT_URL.search(text):
            record.msg, record.args = _BOT_URL.sub("/bot<hidden>", text), ()
        return True


for _name in ("httpx", "httpcore"):
    _log = logging.getLogger(_name)
    if not any(isinstance(f, _HideBotToken) for f in _log.filters):
        _log.addFilter(_HideBotToken())

MAX_BYTES = 49 * 1024 * 1024          # Bot API accepts documents up to 50 MB; keep a margin


class PultError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def load_target(config_path: Path | None = None) -> tuple[str, int, str]:
    """(bot token, chat id, proxy) of the single owner of the Пульт. Raises PultError with a Russian message."""
    from ..telegram_companion.config import load
    from ..telegram_companion.paths import companion_config_path
    path = Path(config_path) if config_path else companion_config_path(read_fallback=True)
    if not path.is_file():
        raise PultError(409, "pult_not_configured", "Пульт не настроен на этом ПК (нет config.json компаньона)")
    try:
        settings = load(path, env_file=path.parent / "companion.env")
    except (ValueError, OSError, KeyError, TypeError):
        raise PultError(409, "pult_config_invalid", "Не удалось прочитать настройки Пульта (config.json / companion.env)") from None
    if not settings.bot_token:
        raise PultError(409, "pult_not_configured", "В настройках Пульта нет токена бота (TG_COMPANION_BOT_TOKEN)")
    people = tuple(settings.people)
    if len(people) != 1:
        raise PultError(409, "pult_recipient_ambiguous",
                        f"Пульт настроен неоднозначно: получателей {len(people)}, нужен ровно один (владелец) — ничего не отправлено")
    if people[0].role != "owner":
        raise PultError(409, "pult_recipient_not_owner", "Единственный получатель Пульта не владелец — ничего не отправлено")
    return settings.bot_token, int(people[0].chat_id), settings.proxy or ""


def _stamp(job: dict) -> str:
    raw = job.get("finished_at") or job.get("created_at") or ""
    try:
        when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        when = when.astimezone(timezone.utc) if when.tzinfo else when.replace(tzinfo=timezone.utc)
    except ValueError:
        when = datetime.now(timezone.utc)
    return when.strftime("%H:%M UTC")


def _secs(v: Any) -> str:
    v = float(v)
    return f"{v:.0f} с" if v < 90 else f"{int(v // 60)} мин {int(v % 60)} с"


def timing_text(job: dict) -> str:
    """The MEASURED time of this job (bcc.direct_gen.timings): «за 4 мин 6 с (загрузка 40 с, холодная)»; "" if unknown."""
    t = job.get("timing") or {}
    total = t.get("total_s")
    if not isinstance(total, (int, float)):
        return ""
    out = f"за {_secs(total)}"
    load = t.get("load_s")
    if isinstance(load, (int, float)):
        state = "модель уже была загружена" if t.get("load") == "warm" else "холодная"
        out += f" (загрузка {_secs(load)}, {state})"
    return out


def caption_for(job: dict) -> str:
    """«HH:MM UTC · model · WxH · steps · #id · за N с (загрузка M с, холодная)» — no prompt, no paths."""
    p = job.get("params") or {}
    parts = [_stamp(job), str(job.get("model") or "?")]
    if p.get("width") and p.get("height"):
        parts.append(f"{p['width']}x{p['height']}")
    if p.get("steps"):
        parts.append(f"{p['steps']} шагов")
    parts.append(f"#{str(job.get('job_id') or '')[:8]}")
    measured = timing_text(job)
    if measured:
        parts.append(measured)
    lock = job.get("facelock") or {}
    if lock.get("requested"):                      # a FaceLock refusal is never sent silently
        parts.append(str(lock.get("label") or ("FaceLock применён" if lock.get("status") == "applied" else
                                               f"FaceLock не применён: {lock.get('reason') or 'причина не указана'}")))
    return " · ".join(parts)[:900]


def file_name(job: dict, path: Path) -> str:
    return f"bossman-{str(job.get('job_id') or 'job')[:8]}{path.suffix.lower() or '.bin'}"


async def send_document(token: str, chat_id: int, path: Path, name: str, caption: str, mime: str, *,
                        proxy: str = "", transport: httpx.AsyncBaseTransport | None = None) -> Any:
    """sendDocument with the original bytes. Returns the Telegram message id. Never echoes the token or the chat id."""
    from ..telegram_companion.adapters import bot_api_url
    size = path.stat().st_size
    if size > MAX_BYTES:
        raise PultError(413, "pult_file_too_big",
                        f"Файл {size / 1048576:.0f} МБ больше лимита Telegram для документов (49 МБ) — в Пульт не отправлен")
    data = await asyncio.to_thread(path.read_bytes)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(240, connect=20), follow_redirects=False, trust_env=False,
                                     proxy=(proxy or None) if transport is None else None, transport=transport) as client:
            response = await client.post(bot_api_url(token, "sendDocument"),
                                         data={"chat_id": str(chat_id), "caption": caption, "disable_content_type_detection": "true"},
                                         files={"document": (name, data, mime or "application/octet-stream")})
        body = response.json()
    except (httpx.HTTPError, OSError, TimeoutError, ValueError):
        raise PultError(502, "pult_unreachable", "Telegram недоступен с этого ПК — в Пульт не отправлено, попробуйте ещё раз") from None
    if not isinstance(body, dict) or body.get("ok") is not True:
        code = body.get("error_code") if isinstance(body, dict) else None
        raise PultError(502, "pult_rejected", f"Telegram не принял файл (код {code or response.status_code}) — в Пульт не отправлено")
    return (body.get("result") or {}).get("message_id")
