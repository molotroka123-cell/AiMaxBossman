"""BOSSMAN Browser & Computer Control runtime.

Drop-in module for command-center/bcc.

Goals:
- Playwright-based browser sessions controlled by BOSSMAN.
- DOM-first + screenshot/vision-friendly snapshots.
- Per-agent/browser permission policy: auto | ask | deny.
- Human takeover that blocks autonomous actions until Resume.
- Persistent browser profiles are optional.
- No browser dependency at import time: Playwright is loaded lazily.

This module intentionally does NOT execute payments, wallet actions or arbitrary
desktop mouse/keyboard actions. Those remain separate, approval-gated tools.
"""
from __future__ import annotations

import asyncio
import fnmatch
import ipaddress
import os
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from ..browser_runtime import INSTALL_HINT, PREINSTALLED_CHROMIUM, chromium_executable

Decision = Literal["auto", "ask", "deny"]

READ_ACTIONS = {"snapshot", "screenshot", "read_dom"}
NAV_ACTIONS = {"navigate", "back", "reload"}
INTERACTION_ACTIONS = {"click", "type", "select", "download"}
SENSITIVE_ACTIONS = {"upload", "submit", "login"}
HARD_DENY_ACTIONS = {"purchase", "payment", "wallet", "bank_transfer"}

DEFAULT_RULES: dict[str, Decision] = {
    "navigate": "auto",
    "back": "auto",
    "reload": "auto",
    "read_dom": "auto",
    "snapshot": "auto",
    "screenshot": "auto",
    "click": "auto",
    "type": "auto",
    "select": "auto",
    "download": "ask",
    "upload": "ask",
    "submit": "ask",
    "login": "ask",
    "purchase": "deny",
    "payment": "deny",
    "wallet": "deny",
    "bank_transfer": "deny",
}


# ------------------------------------------------ F-010: куда браузеру НЕЛЬЗЯ
#
# Пустой allowed_domains значил «куда угодно» — включая metadata-endpoint облака,
# loopback с нашим же API и внутреннюю сеть. Теперь такие цели запрещены по
# умолчанию; владелец включает их осознанно (локальная разработка) через
# BCC_BROWSER_ALLOW_PRIVATE=1. Схемы вне http(s) и URL с userinfo не открываются
# никогда: file:// читает диск, а user:pw@host — способ спрятать настоящий хост.

ALLOW_PRIVATE_ENV = "BCC_BROWSER_ALLOW_PRIVATE"
_BLOCKED_HOSTNAMES = frozenset({"localhost", "localhost.localdomain", "metadata",
                                "metadata.google.internal", "instance-data",
                                "instance-data.ec2.internal"})
_BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".localdomain", ".internal", ".home.arpa",
                          ".intranet", ".lan", ".corp", ".home")


def owner_allows_private() -> bool:
    return os.environ.get(ALLOW_PRIVATE_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _ip_is_public(ip: ipaddress._BaseAddress) -> bool:
    return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_unspecified
                                 or ip.is_loopback or ip.is_link_local or ip.is_private)


def _literal_ip(host: str) -> ipaddress._BaseAddress | None:
    """IP из строки БЕЗ DNS. Понимает и «нечестные» формы: 2130706433, 0x7f000001,
    127.1, 0177.0.0.1 — их принимает inet_aton (а значит и резолвер браузера)."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    try:
        return ipaddress.IPv4Address(socket.inet_aton(host))
    except (OSError, ValueError):
        return None


def target_refusal(url: str, *, allow_private: bool | None = None) -> str:
    """Литеральная проверка цели навигации (без DNS). "" = можно.

    Схема и userinfo проверяются всегда; локальные имена и непубличные IP —
    если владелец не включил BCC_BROWSER_ALLOW_PRIVATE."""
    try:
        p = urlparse(str(url or ""))
        host = (p.hostname or "").lower().rstrip(".")
        userinfo = p.username is not None or p.password is not None
    except ValueError:
        return "некорректный URL"
    scheme = (p.scheme or "").lower()
    if scheme not in ("http", "https"):
        return f"схема {scheme or '(нет)'} запрещена — браузеру доступны только http(s)"
    if userinfo:
        return "URL с учётными данными (user:pw@host) запрещён"
    if not host:
        return "в URL нет хоста"
    if allow_private is None:
        allow_private = owner_allows_private()
    if allow_private:
        return ""
    if host in _BLOCKED_HOSTNAMES or host.endswith(_BLOCKED_HOST_SUFFIXES):
        return f"локальный/служебный хост {host!r} запрещён (loopback/metadata/внутренняя сеть)"
    ip = _literal_ip(host)
    if ip is not None and not _ip_is_public(ip):
        return (f"адрес {ip} непубличный (loopback/private/link-local/metadata) — "
                f"запрещён без {ALLOW_PRIVATE_ENV}=1")
    return ""


def resolved_target_refusal(url: str, *, allow_private: bool | None = None) -> str:
    """Литеральная проверка + DNS: ВСЕ адреса имени должны быть публичными.

    Одна приватная A-запись среди публичных — отказ: иначе rebinding на второй
    записи. NXDOMAIN — тоже отказ: браузер туда всё равно не попадёт, а «имя
    появится позже» — классика rebinding."""
    refusal = target_refusal(url, allow_private=allow_private)
    if refusal:
        return refusal
    if allow_private is None:
        allow_private = owner_allows_private()
    if allow_private:
        return ""
    host = (urlparse(str(url)).hostname or "").lower().rstrip(".")
    if _literal_ip(host) is not None:
        return ""                       # литерал уже проверен, DNS не нужен
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        return f"хост {host!r} не резолвится: {exc}"
    addrs = {str(ai[4][0]) for ai in infos if ai and ai[4]}
    if not addrs:
        return f"хост {host!r} не имеет адресов"
    for raw in sorted(addrs):
        try:
            ip = ipaddress.ip_address(raw.split("%", 1)[0])
        except ValueError:
            return f"хост {host!r} резолвится в непонятный адрес {raw!r}"
        if not _ip_is_public(ip):
            return (f"хост {host!r} резолвится в непубличный адрес {ip} — запрещён "
                    f"(проверяются все адреса, анти-rebinding)")
    return ""


class BrowserUnavailable(RuntimeError):
    pass


class BrowserPolicyDenied(RuntimeError):
    def __init__(self, action: str, detail: str = ""):
        super().__init__(detail or f"browser action denied: {action}")
        self.action = action


class BrowserApprovalRequired(RuntimeError):
    def __init__(self, action: str, detail: str = ""):
        super().__init__(detail or f"browser action requires approval: {action}")
        self.action = action


class BrowserTakeoverActive(RuntimeError):
    pass


class BrowserDownloadApprovalRequired(BrowserApprovalRequired):
    """Страница начала загрузку, а права скачивать у этого действия нет.

    Загрузка отменена ДО записи на диск: файл без решения человека не остаётся
    ни в папке загрузок, ни во временном каталоге Playwright."""

    def __init__(self, filename: str, url: str):
        super().__init__("download", f"страница начала загрузку файла {filename!r} — "
                                     f"скачивание требует подтверждения (browser.download)")
        self.filename = filename
        self.url = url


class BrowserDownloadFailed(RuntimeError):
    """Загрузка началась, но настоящего файла нет: обрыв, отмена, лимит, пустой ответ.

    Никогда не превращается в «успешно скачано» — B4 (owner audit 2026-09-21)
    был ровно об этом: навигация на файл давала 500 и ни одного байта на диске."""

    def __init__(self, detail: str, *, record: dict[str, Any] | None = None):
        super().__init__(detail)
        self.record = record or {}


# ------------------------------------------------ B4: загрузки браузера
#
# Chromium не «открывает» ответ с Content-Disposition: attachment или бинарный
# тип — он начинает ЗАГРУЗКУ, и Playwright бросает из goto() «Download is
# starting». Раньше это исключение улетало в HTTP 500, файл не сохранялся.
# Теперь каждая страница сессии слушает событие download, а действие, которое
# его вызвало, ждёт его, сохраняет в безопасный каталог сессии и проверяет файл.

DOWNLOAD_MAX_MB_ENV = "BCC_BROWSER_DOWNLOAD_MAX_MB"
# Сколько клик ждёт, не началась ли загрузка. Больше — медленнее каждый клик;
# меньше — медленный сервер не успеет. Явное действие download ждёт полный таймаут.
CLICK_DOWNLOAD_GRACE_S = 0.75
DOWNLOAD_START_TIMEOUT_S = 30.0
DOWNLOAD_FINISH_TIMEOUT_S = 600.0
DOWNLOAD_TIMEOUT_ENV = "BCC_BROWSER_DOWNLOAD_TIMEOUT_S"
EXECUTABLE_SUFFIXES = frozenset({
    ".exe", ".msi", ".msix", ".bat", ".cmd", ".com", ".scr", ".ps1", ".psm1", ".vbs",
    ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta", ".jar", ".dll", ".lnk", ".reg",
    ".cpl", ".sh", ".app", ".dmg", ".pkg", ".apk", ".appx", ".appxbundle", ".iso",
})
_MAGIC = [
    (b"%PDF-", "application/pdf"),
    (b"PK\x03\x04", "application/zip"),
    (b"PK\x05\x06", "application/zip"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF8", "image/gif"),
    (b"MZ", "application/x-msdownload"),
]


def _download_finish_timeout() -> float:
    try:
        return max(1.0, float(os.environ.get(DOWNLOAD_TIMEOUT_ENV, "") or DOWNLOAD_FINISH_TIMEOUT_S))
    except ValueError:
        return DOWNLOAD_FINISH_TIMEOUT_S


def _download_max_bytes() -> int:
    try:
        mb = float(os.environ.get(DOWNLOAD_MAX_MB_ENV, "") or 500)
    except ValueError:
        mb = 500.0
    return int(max(1.0, mb) * 1024 * 1024)


def safe_download_name(name: str) -> str:
    """Имя файла без путей, управляющих символов и зарезервированных имён Windows."""
    name = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f<>:\"|?*]+", "_", name).strip().strip(".")
    stem = name.split(".", 1)[0].upper()
    if stem in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}:
        name = "_" + name
    return name[:180] or "download.bin"


def _unique_path(folder: Path, name: str) -> Path:
    """Одноимённые загрузки не затирают друг друга: report.pdf, report (1).pdf …"""
    path = folder / name
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    i = 1
    while path.exists() or path.with_name(path.name + ".part").exists():
        path = folder / (f"{stem} ({i})" + (f".{ext}" if ext else ""))
        i += 1
    return path


_HTML_HEADS = (b"<!doctype html", b"<html", b"<head", b"<body", b"<?xml", b"<!--")


def _sniff_mime(path: Path) -> str:
    """Type from the BYTES. The extension is used only for types that have no
    signature (text, csv, json…); a name that promises a signed type (pdf, zip,
    png…) without its signature is NOT given that type — an HTML error page
    saved as `form.pdf` must not be recorded as a PDF (HW-10 MVČR risk)."""
    import mimetypes
    try:
        with path.open("rb") as fh:
            head = fh.read(512)
    except OSError:
        head = b""
    for magic, mime in _MAGIC:
        if head.startswith(magic):
            return mime
    lowered = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if lowered.startswith(_HTML_HEADS):
        return "text/html"
    guessed = mimetypes.guess_type(path.name)[0]
    if guessed and guessed in {m for _magic, m in _MAGIC}:
        return "application/octet-stream"
    return guessed or "application/octet-stream"


def _mime_mismatch(path: Path) -> bool:
    """True when the name promises a different type than the bytes carry."""
    import mimetypes
    guessed = mimetypes.guess_type(path.name)[0]
    return bool(guessed) and guessed != _sniff_mime(path)


def _sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_download_navigation(exc: BaseException) -> bool:
    return "download is starting" in str(exc).lower()


# Типы ответа, которые для владельца — ФАЙЛ, а не страница. Полный Chromium в
# новом headless-режиме (и обычный настольный) показывает PDF встроенным
# просмотрщиком вместо загрузки: событие download не приходит, goto
# возвращает 200, а «Файл скачан» не наступает (B4 на Linux CI, 2026-09-21).
# Такой ответ забирается байтами того же ответа и идёт через ТУ ЖЕ проверку
# и запись, что и настоящая загрузка: политика, лимит, .part → sha256 → имя.
_DOCUMENT_MIME_PREFIXES = ("application/pdf", "application/octet-stream", "application/zip",
                           "application/x-zip", "application/msword", "application/vnd.",
                           "application/x-7z", "application/x-rar", "application/gzip",
                           "application/x-tar", "application/x-msdownload", "audio/", "video/")


def _document_content_type(headers: dict | None) -> str:
    ctype = str((headers or {}).get("content-type") or "").split(";", 1)[0].strip().lower()
    return ctype if ctype.startswith(_DOCUMENT_MIME_PREFIXES) else ""


def _disposition_filename(headers: dict | None) -> str:
    disp = str((headers or {}).get("content-disposition") or "")
    m = re.search(r"filename\*=(?:UTF-8|utf-8)''([^;]+)", disp)
    if m:
        from urllib.parse import unquote
        return unquote(m.group(1).strip())
    m = re.search(r'filename\s*=\s*"?([^";]+)"?', disp)
    return m.group(1).strip() if m else ""


class _InlineDocument:
    """Ответ навигации, который браузер показал, а не скачал, в форме
    download-объекта Playwright: url, suggested_filename, cancel, failure, save_as."""

    def __init__(self, url: str, body: bytes, headers: dict | None):
        from urllib.parse import unquote, urlparse
        self.url = url
        self._body = body
        name = _disposition_filename(headers) or unquote(urlparse(url).path.rsplit("/", 1)[-1])
        if "." not in name:
            import mimetypes
            ext = mimetypes.guess_extension(_document_content_type(headers) or "") or ".bin"
            name = (name or "download") + ext
        self.suggested_filename = name

    async def cancel(self) -> None:
        self._body = b""

    async def failure(self):
        return None

    async def save_as(self, path: str) -> None:
        Path(path).write_bytes(self._body)


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("байт", "КБ", "МБ", "ГБ"):
        if value < 1024 or unit == "ГБ":
            return f"{int(value)} {unit}" if unit == "байт" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} байт"


@dataclass(slots=True)
class BrowserPolicy:
    enabled: bool = True
    mode: str = "dom_vision"  # dom_vision | dom_only | vision_only
    allowed_domains: list[str] = field(default_factory=list)
    blocked_domains: list[str] = field(default_factory=list)
    rules: dict[str, Decision] = field(default_factory=lambda: dict(DEFAULT_RULES))
    max_tabs: int = 5
    persistent_profile: bool = False
    screenshots: str = "errors"  # never | errors | each_step
    max_runtime_minutes: int = 480

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "BrowserPolicy":
        raw = dict(raw or {})
        rules = dict(DEFAULT_RULES)
        rules.update({k: v for k, v in (raw.get("rules") or {}).items()
                      if v in ("auto", "ask", "deny")})
        return cls(
            enabled=bool(raw.get("enabled", True)),
            mode=str(raw.get("mode") or "dom_vision"),
            allowed_domains=_patterns(raw.get("allowed_domains")),
            blocked_domains=_patterns(raw.get("blocked_domains")),
            rules=rules,
            max_tabs=max(1, min(int(raw.get("max_tabs") or 5), 25)),
            persistent_profile=bool(raw.get("persistent_profile", False)),
            screenshots=str(raw.get("screenshots") or "errors"),
            max_runtime_minutes=max(1, min(int(raw.get("max_runtime_minutes") or 480), 10080)),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "allowed_domains": self.allowed_domains,
            "blocked_domains": self.blocked_domains,
            "rules": self.rules,
            "max_tabs": self.max_tabs,
            "persistent_profile": self.persistent_profile,
            "screenshots": self.screenshots,
            "max_runtime_minutes": self.max_runtime_minutes,
        }

    def domain_allowed(self, url: str) -> bool:
        # F-010: сначала «куда нельзя вообще» (схема, userinfo, loopback/private/
        # metadata), и только потом allowlist владельца. Явно перечисленный
        # 127.0.0.1 в allowed_domains — не обход: включение приватных целей
        # делается одним осознанным переключателем окружения, а не списком.
        if target_refusal(url):
            return False
        host = (urlparse(url).hostname or "").lower().rstrip(".")
        if any(_host_match(host, p) for p in self.blocked_domains):
            return False
        if not self.allowed_domains:
            return True
        return any(_host_match(host, p) for p in self.allowed_domains)

    def navigation_refusal(self, url: str) -> str:
        """Причина, по которой навигацию на url делать нельзя ("" = можно).

        В отличие от `decision` делает DNS-резолв, поэтому вызывается в момент
        навигации, а не при каждом клике по текущей странице."""
        if not self.enabled:
            return "браузер выключен политикой"
        if not self.domain_allowed(url):
            return target_refusal(url) or "домен вне allowlist политики"
        return resolved_target_refusal(url)

    def decision(self, action: str, *, url: str = "") -> Decision:
        if not self.enabled:
            return "deny"
        action = action.strip().lower()
        if action in HARD_DENY_ACTIONS:
            return "deny"
        if url and action in NAV_ACTIONS | INTERACTION_ACTIONS | SENSITIVE_ACTIONS:
            if not self.domain_allowed(url):
                return "deny"
        return self.rules.get(action, "ask")


class CaptchaBlocked(RuntimeError):
    """На странице капча — действия агента остановлены до человека.

    Отдельное исключение, а не takeover: ЧТЕНИЕ страницы остаётся доступным,
    иначе модель не смогла бы узнать саму причину остановки и билась бы вслепую.
    """

    def __init__(self, provider: str):
        super().__init__(f"на странице капча ({provider}) — нужен человек")
        self.provider = provider


class StaleElementReference(RuntimeError):
    """Ссылка указывает не на тот элемент, что видела модель.

    Никогда не превращается в клик по соседу: лучше честный отказ и просьба
    перечитать страницу, чем «успешно нажато» не по тому месту.
    """

    def __init__(self, ref: str, reason: str):
        super().__init__(f"ссылка {ref} устарела: {reason}")
        self.ref = ref
        self.reason = reason


class AmbiguousSelector(RuntimeError):
    """Селектор попал в несколько элементов. `.first` — это выбор наугад."""

    def __init__(self, selector: str, count: int):
        super().__init__(f"селектор {selector!r} совпал с {count} элементами")
        self.selector = selector
        self.count = count


@dataclass
class BrowserRuntimeSession:
    id: int
    policy: BrowserPolicy
    context: Any
    page: Any
    browser: Any = None
    takeover: bool = False
    paused: bool = False
    profile_name: str = "default"
    # V2.2+: поколение снимка. Каждый новый снимок увеличивает его, и ссылки
    # из прошлых поколений становятся недействительными — DOM мог перерисоваться
    # между тем, что видела модель, и моментом действия.
    generation: int = 0
    refs: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Секреты, которые рантайм подставлял в эту сессию. Никогда не уходят
    # модели: используются только для вычищения их из видимого ей текста.
    secrets: set[str] = field(default_factory=set)
    # Обнаруженная капча. Агент её не решает — она передаётся человеку.
    captcha: dict[str, Any] = field(default_factory=dict)
    # B4: загрузки, начатые любой страницей сессии (включая popup). Действие,
    # вызвавшее загрузку, забирает её отсюда; незабранные остаются во временном
    # каталоге Playwright и удаляются вместе с контекстом — на диск владельца
    # без решения действия они не попадают.
    download_queue: Any = None
    downloads: list[dict[str, Any]] = field(default_factory=list)


MASK = "***"
# Короче этого куска совпадение уликой не считаем: секрет «password2026» иначе
# вычистил бы со страницы каждое слово на «pass», и модель осталась бы слепой.
MIN_PARTIAL_LEN = 12


def _secret_variants(secret: str) -> list[str]:
    """Формы, в которых секрет РЕАЛЬНО встречается в тексте для модели.

    Точное совпадение — наивное допущение: по дороге к модели строка успевает
    измениться. Снимок схлопывает пробелы (`clean()`), адрес приезжает
    percent-кодированным, разметка — с HTML-экранированием. Каждая такая форма
    остаётся тем же секретом.
    """
    import html as _html
    from urllib.parse import quote, quote_plus

    out: list[str] = [secret]
    for value in (re.sub(r"\s+", " ", secret).strip(),     # ровно то, что делает clean()
                  secret.strip(),
                  quote(secret, safe=""), quote_plus(secret),
                  _html.escape(secret)):
        if value and value not in out:
            out.append(value)
    # Длинные варианты маскируем первыми: короткий иначе съел бы хвост длинного.
    return sorted(out, key=len, reverse=True)


def _mask_variant(text: str, variant: str) -> str:
    """Замаскировать вариант секрета, включая его обрезанный вид.

    Обрезка встречается на каждом шагу: `slice(0, 220)` в снимке, `[:500]` в
    превью инструмента, `[:200]` в событии шины. Обрезанный ключ API — это
    по-прежнему ключ API, поэтому ищем самый длинный префикс, который в тексте
    действительно есть.
    """
    if not variant:
        return text
    if variant in text:
        text = text.replace(variant, MASK)
    if len(variant) < MIN_PARTIAL_LEN:
        return text
    while True:
        lo, hi, best = MIN_PARTIAL_LEN, len(variant) - 1, 0
        while lo <= hi:                      # «префикс есть» монотонно по длине
            mid = (lo + hi) // 2
            if variant[:mid] in text:
                best, lo = mid, mid + 1
            else:
                hi = mid - 1
        if not best:
            return text
        # MASK короче MIN_PARTIAL_LEN, поэтому замена не порождает новых совпадений
        text = text.replace(variant[:best], MASK)


def redact_secrets(value: Any, secrets: set[str]) -> Any:
    """Убрать известные секреты из всего, что уйдёт модели или в лог.

    Вторая линия обороны. Первая — не класть значение секретного поля в снимок
    вовсе (см. `snapshot`), но секрет мог быть введён и в обычное поле, и тогда
    его вернул бы `el.value`.
    """
    if not secrets:
        return value
    # Варианты считаем ОДИН раз на вызов, а не на каждую строку: снимок — это
    # сотни полей, и пересчёт кодировок на каждом был бы заметен.
    variants = [v for secret in secrets for v in _secret_variants(secret)]

    def walk(node: Any) -> Any:
        if isinstance(node, str):
            for variant in variants:
                node = _mask_variant(node, variant)
            return node
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(value)


# Признаки капчи на странице. Мы её НЕ решаем: капча — это осознанно
# поставленный владельцем сайта контроль доступа, и обходить его агент не
# должен. Но и молча биться о неё до таймаута он тоже не должен: правильное
# поведение — распознать, остановиться и позвать человека (Take Over).
CAPTCHA_MARKERS = [
    ("recaptcha", "Google reCAPTCHA"),
    ("g-recaptcha", "Google reCAPTCHA"),
    ("hcaptcha", "hCaptcha"),
    ("h-captcha", "hCaptcha"),
    ("cf-turnstile", "Cloudflare Turnstile"),
    ("challenges.cloudflare.com", "Cloudflare Challenge"),
    ("funcaptcha", "Arkose FunCaptcha"),
    ("arkoselabs", "Arkose FunCaptcha"),
    ("geetest", "GeeTest"),
]
# Текстовые маркеры — последняя линия: самописная капча без известного провайдера.
CAPTCHA_TEXT = [
    ("i'm not a robot", "проверка «я не робот»"),
    ("я не робот", "проверка «я не робот»"),
    ("nejsem robot", "проверка «я не робот» (cs)"),
    ("verify you are human", "проверка «вы человек»"),
    ("enter the characters", "ввод символов с картинки"),
    ("введите символы", "ввод символов с картинки"),
    ("opište kód", "ввод символов с картинки (cs)"),
]


def detect_captcha(html: str, text: str) -> dict[str, Any]:
    """Есть ли на странице капча и какая. Ничего не решает и не обходит."""
    low_html = (html or "").lower()
    for marker, name in CAPTCHA_MARKERS:
        if marker in low_html:
            return {"present": True, "provider": name, "matched": marker,
                    "evidence": "разметка страницы"}
    low_text = (text or "").lower()
    for marker, name in CAPTCHA_TEXT:
        if marker in low_text:
            return {"present": True, "provider": name, "matched": marker,
                    "evidence": "текст страницы"}
    return {"present": False}


# Признак «секретное поле» — ОДИН на снимок и на проверку ссылки.
#
# Раньше он был ровно `type === 'password'`, и мимо проходили: CSRF/сессионный
# токен в `type=hidden`, код из SMS (`autocomplete=one-time-code`), пароль в
# `type=text` с `autocomplete=current-password`. Ни одно из этих значений модели
# не нужно ни для одного сценария — ей достаточно знать, что поле есть и
# заполнено.
#
# Держать признак в одном месте обязательно: если снимок прячет значение, а
# проверка `ref` его читает, отпечатки расходятся и каждый клик по такому полю
# падает ложным StaleElementReference.
_JS_IS_SECRET = """((el) => {
  if ((el.tagName || '').toLowerCase() !== 'input') return false;
  const type = (el.getAttribute('type') || '').toLowerCase();
  const ac = (el.getAttribute('autocomplete') || '').toLowerCase();
  const ident = ((el.getAttribute('name') || '') + ' '
                 + (el.getAttribute('id') || '')).toLowerCase();
  return type === 'password' || type === 'hidden'
      || ac.indexOf('password') >= 0 || ac.indexOf('one-time-code') >= 0
      || /pass|pwd|secret|token|otp|2fa|mfa|cvv|csrf|api[-_]?key/.test(ident);
})"""


def _js(source: str) -> str:
    """Подставить общий признак секретности в JS-выражение."""
    return source.replace("__IS_SECRET__", _JS_IS_SECRET)


def _fingerprint(item: dict[str, Any]) -> str:
    """Отпечаток элемента: тег, роль, имя, тип, ссылка и видимый текст.

    Значение поля пароля в отпечаток не входит — оно и не доходит сюда.
    """
    import hashlib
    parts = "|".join(str(item.get(k) or "") for k in
                     ("tag", "role", "name", "type", "href", "text"))
    return hashlib.sha256(parts.encode("utf-8", "replace")).hexdigest()[:16]


def _patterns(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = re.split(r"[\s,]+", value)
    return [str(v).strip().lower() for v in value if str(v).strip()]


def _host_match(host: str, pattern: str) -> bool:
    pattern = pattern.strip().lower()
    if not pattern:
        return False
    if pattern.startswith("*."):
        root = pattern[2:]
        return host == root or host.endswith("." + root)
    if any(ch in pattern for ch in "*?["):
        return fnmatch.fnmatch(host, pattern)
    # «example.com» покрывает и сам домен, и поддомены (суффиксное совпадение)
    return host == pattern or host.endswith("." + pattern)


class BrowserManager:
    """In-process Playwright manager.

    Persistence of profile/session metadata belongs to the Control API/DB layer.
    A process restart closes live Chromium contexts; the DB should mark such
    sessions interrupted and let the user/mission reopen from a checkpoint.
    """

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.profile_dir = self.data_dir / "profiles"
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw: Any = None
        self._sessions: dict[int, BrowserRuntimeSession] = {}
        self._lock = asyncio.Lock()
        self.preinstalled_executable = PREINSTALLED_CHROMIUM

    @property
    def available(self) -> bool:
        return self.executable_path() is not None

    def executable_path(self, *, headless: bool = True) -> str | None:
        return chromium_executable(preinstalled=self.preinstalled_executable, headless=headless)

    async def _playwright(self):
        if self._pw is not None:
            return self._pw
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:
            raise BrowserUnavailable(
                "Playwright не установлен. Установите dependency и Chromium: "
                "`pip install playwright && playwright install chromium`"
            ) from exc
        self._pw = await async_playwright().start()
        return self._pw

    async def start(self, session_id: int, policy: BrowserPolicy,
                    *, profile_name: str = "default", headless: bool = True) -> dict[str, Any]:
        if not policy.enabled:
            raise BrowserPolicyDenied("start", "Browser access выключен политикой")
        async with self._lock:
            if session_id in self._sessions:
                return await self.status(session_id)
            executable = self.executable_path(headless=headless)
            if executable is None:
                raise BrowserUnavailable(INSTALL_HINT)
            pw = await self._playwright()
            browser = None
            if policy.persistent_profile:
                safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "-", profile_name)[:80] or "default"
                user_data = self.profile_dir / safe_name
                context = await pw.chromium.launch_persistent_context(
                    str(user_data), headless=headless, executable_path=executable,
                    viewport={"width": 1440, "height": 900}
                )
                pages = context.pages
                page = pages[0] if pages else await context.new_page()
            else:
                browser = await pw.chromium.launch(headless=headless, executable_path=executable)
                context = await browser.new_context(viewport={"width": 1440, "height": 900},
                                                    accept_downloads=True)
                page = await context.new_page()
            sess = BrowserRuntimeSession(
                id=session_id, policy=policy, context=context, page=page,
                browser=browser, profile_name=profile_name
            )
            self._watch_downloads(sess)
            self._sessions[session_id] = sess
        return await self.status(session_id)

    # ------------------------------------------------ B4: загрузки

    def downloads_dir(self, session_id: int) -> Path:
        return self.data_dir / "downloads" / f"session-{int(session_id)}"

    def _watch_downloads(self, sess: BrowserRuntimeSession) -> None:
        sess.download_queue = asyncio.Queue()

        def attach(page: Any) -> None:
            try:
                page.on("download", sess.download_queue.put_nowait)
            except Exception:  # noqa: BLE001 — фейковая страница в юнит-тестах
                pass

        for page in list(getattr(sess.context, "pages", []) or []):
            attach(page)
        if sess.page not in (getattr(sess.context, "pages", None) or []):
            attach(sess.page)
        try:
            sess.context.on("page", attach)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _drain_downloads(sess: BrowserRuntimeSession) -> None:
        """Забыть загрузки, начатые ДО текущего действия: их не просили."""
        q = sess.download_queue
        while q is not None and not q.empty():
            q.get_nowait()

    @staticmethod
    async def _next_download(sess: BrowserRuntimeSession, timeout: float) -> Any:
        q = sess.download_queue
        if q is None:
            return None
        if timeout <= 0:
            return None if q.empty() else q.get_nowait()
        try:
            return await asyncio.wait_for(q.get(), timeout)
        except asyncio.TimeoutError:
            return None

    def _download_allowed(self, sess: BrowserRuntimeSession, url: str, *, actor: str,
                          approved: bool, allow_download: bool | None) -> bool:
        decision = sess.policy.decision("download", url=url)
        if decision == "deny":
            raise BrowserPolicyDenied("download", "browser action denied: download"
                                      + (f" — {target_refusal(url)}" if target_refusal(url) else ""))
        if allow_download is not None:
            return allow_download
        return decision == "auto" or actor == "human" or approved

    async def _save_download(self, sess: BrowserRuntimeSession, download: Any, *,
                             trigger: str, source_url: str, actor: str,
                             approved: bool, allow_download: bool | None) -> dict[str, Any]:
        """download event → разрешение → .part → проверка → окончательное имя.

        Успех — только когда файл реально лежит на диске и его размер совпал с
        прочитанным. Всё остальное — BrowserDownloadFailed с записью в журнале
        сессии, а не «скачано»."""
        import time
        file_url = str(getattr(download, "url", "") or source_url)
        name = safe_download_name(getattr(download, "suggested_filename", "") or "")
        record: dict[str, Any] = {
            "session_id": sess.id, "trigger": trigger, "source_url": source_url,
            "url": file_url, "filename": name, "status": "started",
            "started_at": time.time(),
        }
        try:
            # Редирект мог увести загрузку на приватный адрес — там она и умрёт.
            why = target_refusal(file_url) if file_url.startswith(("http:", "https:")) else ""
            if why:
                await download.cancel()
                raise BrowserPolicyDenied("download", f"browser action denied: download — {why}")
            try:
                allowed = self._download_allowed(sess, file_url, actor=actor, approved=approved,
                                                 allow_download=allow_download)
            except BrowserPolicyDenied:
                await download.cancel()
                raise
            if not allowed:
                await download.cancel()
                record["status"] = "needs_approval"
                raise BrowserDownloadApprovalRequired(name, file_url)
            failure = await asyncio.wait_for(download.failure(), _download_finish_timeout())
            if failure:
                raise BrowserDownloadFailed(f"загрузка «{name}» прервана: {failure}")
            folder = self.downloads_dir(sess.id)
            folder.mkdir(parents=True, exist_ok=True)
            executable = Path(name).suffix.lower() in EXECUTABLE_SUFFIXES
            if executable:
                folder = folder / "quarantine"
                folder.mkdir(parents=True, exist_ok=True)
            path = _unique_path(folder, name)
            part = path.with_name(path.name + ".part")
            try:
                await download.save_as(str(part))
                size = part.stat().st_size if part.exists() else -1
                if size < 0:
                    raise BrowserDownloadFailed(f"файл «{name}» не появился на диске")
                if size == 0:
                    raise BrowserDownloadFailed(f"загрузка «{name}» пустая (0 байт) — файла нет")
                limit = _download_max_bytes()
                if size > limit:
                    raise BrowserDownloadFailed(
                        f"файл «{name}» {human_size(size)} больше лимита {human_size(limit)} "
                        f"({DOWNLOAD_MAX_MB_ENV})")
                digest = _sha256(part)
                os.replace(part, path)
            finally:
                if part.exists():
                    part.unlink(missing_ok=True)
            if not path.is_file() or path.stat().st_size != size:
                raise BrowserDownloadFailed(f"файл «{name}» не подтвердился на диске после записи")
            record.update(status="saved", path=str(path), filename=path.name, bytes=size,
                          size_human=human_size(size), sha256=digest, mime=_sniff_mime(path),
                          mime_mismatch=_mime_mismatch(path),
                          quarantined=executable, finished_at=time.time())
            return record
        except BrowserDownloadFailed as exc:
            record.update(status="failed", error=str(exc), finished_at=time.time())
            exc.record = record
            raise
        except (BrowserPolicyDenied, BrowserApprovalRequired) as exc:
            record.setdefault("error", str(exc))
            if record["status"] == "started":
                record["status"] = "denied"
            raise
        except asyncio.TimeoutError as exc:
            try:
                await download.cancel()
            except Exception:  # noqa: BLE001
                pass
            record.update(status="failed", error="загрузка не завершилась вовремя",
                          finished_at=time.time())
            raise BrowserDownloadFailed(record["error"], record=record) from exc
        except Exception as exc:  # noqa: BLE001 — любая ошибка Playwright = честный отказ
            record.update(status="failed", error=f"{type(exc).__name__}: {exc}"[:300],
                          finished_at=time.time())
            raise BrowserDownloadFailed(f"загрузка «{name}» не удалась: {record['error']}",
                                        record=record) from exc
        finally:
            sess.downloads.append(record)
            del sess.downloads[:-100]

    async def _download_result(self, sess: BrowserRuntimeSession, record: dict[str, Any]) -> dict[str, Any]:
        status = await self.status(sess.id)
        where = " (в карантине: исполняемый файл)" if record.get("quarantined") else ""
        return {**status, "download": record,
                "message": f"Файл скачан: {record['filename']} — {record['size_human']}{where}. "
                           f"Путь: {record['path']}"}

    async def download(self, session_id: int, *, url: str = "", selector: str = "",
                       ref: str = "", actor: str = "agent", approved: bool = False,
                       timeout: float = DOWNLOAD_START_TIMEOUT_S) -> dict[str, Any]:
        """Явная загрузка: по URL файла или кликом по ссылке/кнопке. Ждёт файл."""
        sess = self._session(session_id)
        if url:
            self._guard(sess, "navigate", url=url, actor=actor, approved=approved)
        self._guard(sess, "download", url=url, actor=actor, approved=approved)
        if url:
            return await self.navigate(session_id, url, actor=actor, approved=approved,
                                       allow_download=True, expect_download=True,
                                       download_timeout=timeout)
        loc = await self._target(sess, selector, ref)
        self._drain_downloads(sess)
        source = str(sess.page.url or "")
        await loc.click(timeout=30000)
        dl = await self._next_download(sess, timeout)
        if dl is None:
            rec = {"session_id": session_id, "trigger": "click", "source_url": source,
                   "status": "failed", "error": "клик не начал загрузку"}
            sess.downloads.append(rec)
            raise BrowserDownloadFailed(f"за {timeout:.0f} с после клика загрузка не началась",
                                        record=rec)
        record = await self._save_download(sess, dl, trigger="click", source_url=source,
                                           actor=actor, approved=approved, allow_download=True)
        return await self._download_result(sess, record)

    async def list_downloads(self, session_id: int) -> list[dict[str, Any]]:
        return list(self._session(session_id).downloads)

    async def stop(self, session_id: int) -> None:
        sess = self._sessions.pop(session_id, None)
        if not sess:
            return
        try:
            await sess.context.close()
        finally:
            if sess.browser is not None:
                try:
                    await sess.browser.close()
                except Exception:
                    pass

    async def close(self) -> None:
        for sid in list(self._sessions):
            await self.stop(sid)
        if self._pw is not None:
            try:
                await self._pw.stop()
            finally:
                self._pw = None

    def _session(self, session_id: int) -> BrowserRuntimeSession:
        sess = self._sessions.get(session_id)
        if not sess:
            raise LookupError(f"browser session {session_id} is not running")
        return sess

    def is_live(self, session_id: int) -> bool:
        """Без сети и без правки состояния: есть ли в ЭТОМ процессе живой
        Playwright-контекст для session_id. Строка в БД переживает рестарт,
        рантайм-сессия — нет; список сессий обязан говорить правду про оба факта
        (см. BCC-V2-SESSION-20783913FA36-P1-FIX-001, P1-D)."""
        return session_id in self._sessions

    def _guard(self, sess: BrowserRuntimeSession, action: str,
               *, url: str = "", actor: str = "agent", approved: bool = False) -> None:
        if sess.paused:
            raise BrowserPolicyDenied(action, "Browser session paused")
        if sess.takeover and actor != "human":
            raise BrowserTakeoverActive("Human takeover active")
        if (sess.captcha.get("present") and actor != "human"
                and action not in READ_ACTIONS | NAV_ACTIONS):
            # Читать и уходить со страницы можно, взаимодействовать — нет.
            raise CaptchaBlocked(str(sess.captcha.get("provider") or "неизвестная"))
        target = url or sess.page.url
        decision = sess.policy.decision(action, url=target)
        if decision == "deny":
            why = target_refusal(target) if (url and action in NAV_ACTIONS) else ""
            raise BrowserPolicyDenied(action, f"browser action denied: {action}"
                                      + (f" — {why}" if why else ""))
        if decision == "ask" and actor != "human" and not approved:
            raise BrowserApprovalRequired(action)

    async def status(self, session_id: int) -> dict[str, Any]:
        sess = self._session(session_id)
        # Редакция обязательна и здесь, а не только в `snapshot`: `status()` —
        # это то, что возвращают click / type / select / back / reload, а форма
        # входа с `method=GET` уносит пароль прямо в адресную строку. Без этой
        # чистки он уходил модели строкой «URL:», в шину событий и в колонку
        # `browser_sessions.current_url` — то есть оседал в журнале навсегда.
        return redact_secrets({
            "id": session_id,
            "url": sess.page.url,
            "title": await sess.page.title(),
            "takeover": sess.takeover,
            "paused": sess.paused,
            "profile_name": sess.profile_name,
            "mode": sess.policy.mode,
            "pages": len(sess.context.pages),
            "downloads": len(sess.downloads),
        }, sess.secrets)

    async def pause(self, session_id: int) -> dict[str, Any]:
        sess = self._session(session_id)
        sess.paused = True
        return await self.status(session_id)

    async def resume(self, session_id: int) -> dict[str, Any]:
        sess = self._session(session_id)
        sess.paused = False
        sess.takeover = False
        sess.captcha = {}
        return await self.status(session_id)

    async def takeover(self, session_id: int) -> dict[str, Any]:
        sess = self._session(session_id)
        sess.takeover = True
        return await self.status(session_id)

    async def navigate(self, session_id: int, url: str, *,
                       actor: str = "agent", approved: bool = False,
                       allow_download: bool | None = None, expect_download: bool = False,
                       download_timeout: float = DOWNLOAD_START_TIMEOUT_S) -> dict[str, Any]:
        """Перейти по адресу. Если адрес отдаёт файл — скачать его (B4).

        allow_download=None: решает политика сессии (download=ask → человек или
        одобрение). Инструменты агента передают False, чтобы загрузка шла только
        через отдельный ASK-инструмент browser.download."""
        sess = self._session(session_id)
        self._guard(sess, "navigate", url=url, actor=actor, approved=approved)
        # F-010: DNS-проверка цели (все адреса публичные) — до первого касания
        # страницы. Резолв блокирующий, поэтому в executor.
        loop = asyncio.get_running_loop()
        refusal = await loop.run_in_executor(None, sess.policy.navigation_refusal, url)
        if refusal:
            raise BrowserPolicyDenied("navigate", f"browser action denied: navigate — {refusal}")
        self._drain_downloads(sess)
        response = None
        try:
            response = await sess.page.goto(url, wait_until="domcontentloaded", timeout=60000)
            started = False
        except Exception as exc:
            # «Download is starting» — не ошибка навигации, а начало загрузки.
            # Любая другая ошибка goto остаётся ошибкой, если загрузки нет.
            started = _is_download_navigation(exc)
            dl = await self._next_download(sess, download_timeout if started else 1.0)
            if dl is None:
                if started:
                    rec = {"session_id": session_id, "trigger": "navigate", "source_url": url,
                           "status": "failed", "error": "браузер объявил загрузку, файл не пришёл"}
                    sess.downloads.append(rec)
                    raise BrowserDownloadFailed(
                        "браузер начал загрузку, но файл так и не был получен", record=rec) from exc
                raise
            record = await self._save_download(sess, dl, trigger="navigate", source_url=url,
                                               actor=actor, approved=approved,
                                               allow_download=allow_download)
            return await self._download_result(sess, record)
        dl = await self._next_download(sess, download_timeout if expect_download else 0)
        if dl is not None:
            record = await self._save_download(sess, dl, trigger="navigate", source_url=url,
                                               actor=actor, approved=approved,
                                               allow_download=allow_download)
            return await self._download_result(sess, record)
        inline = await self._inline_document(response, url, sess)
        if inline is not None:
            record = await self._save_download(sess, inline, trigger="navigate", source_url=url,
                                               actor=actor, approved=approved,
                                               allow_download=allow_download)
            return await self._download_result(sess, record)
        if expect_download:
            raise BrowserDownloadFailed("адрес открылся как страница, а не как файл — "
                                        "загрузки не было")
        # Редирект с публичного сайта на приватную цель обходит проверку до goto:
        # проверяем, куда реально приехали, и уходим с такой страницы, не читая её.
        landed = str(getattr(sess.page, "url", "") or "")
        why = target_refusal(landed) if landed and landed != "about:blank" else ""
        if why:
            try:
                await sess.page.goto("about:blank")
            except Exception:
                pass
            raise BrowserPolicyDenied("navigate", f"browser action denied: navigate — "
                                      f"редирект на {landed[:120]!r}: {why}")
        return await self.snapshot(session_id, actor=actor, approved=True)

    @staticmethod
    async def _inline_document(response: Any, url: str, sess: Any = None) -> "_InlineDocument | None":
        """Документ (PDF/архив/бинарник), который браузер показал вместо загрузки.

        Байты берутся НЕ из `response.body()` главного кадра — у встроенного
        просмотрщика PDF это HTML-обёртка, а не документ, — а повторным запросом
        того же адреса через request-контекст сессии (те же cookies, тот же
        адрес после редиректов)."""
        if response is None:
            return None
        try:
            headers = {str(k).lower(): str(v) for k, v in (await response.all_headers()).items()}
        except Exception:  # noqa: BLE001 — фейковая страница/ответ в юнит-тестах
            try:
                headers = {str(k).lower(): str(v) for k, v in dict(getattr(response, "headers", {}) or {}).items()}
            except Exception:  # noqa: BLE001
                return None
        if not _document_content_type(headers):
            return None
        final_url = str(getattr(response, "url", "") or url)
        body = None
        request_ctx = getattr(getattr(sess, "context", None), "request", None)
        if request_ctx is not None:
            try:
                fetched = await request_ctx.get(final_url, max_redirects=0, timeout=60000)
                fetched_headers = {str(k).lower(): str(v) for k, v in dict(fetched.headers or {}).items()}
                if fetched.ok and _document_content_type(fetched_headers):
                    body = await fetched.body()
                    headers = fetched_headers
            except Exception as exc:  # noqa: BLE001
                raise BrowserDownloadFailed(f"браузер показал файл, но его байты недоступны: "
                                            f"{type(exc).__name__}: {exc}") from exc
        if body is None:
            try:
                body = await response.body()
            except Exception as exc:  # noqa: BLE001
                raise BrowserDownloadFailed(f"браузер показал файл, но его байты недоступны: "
                                            f"{type(exc).__name__}") from exc
        return _InlineDocument(final_url, bytes(body), headers)

    async def _target(self, sess: BrowserRuntimeSession, selector: str = "",
                      ref: str = ""):
        """Локатор действия. Ссылка надёжнее селектора и проверяется на устаревание.

        Порядок намеренный: если дана `ref`, селектор игнорируется. Модель
        видела конкретный элемент — по нему и работаем.
        """
        if ref:
            known = sess.refs.get(ref)
            if known is None or known["generation"] != sess.generation:
                raise StaleElementReference(
                    ref, "ссылка из прошлого снимка страницы; перечитайте DOM")
            loc = sess.page.locator(f'[data-bcc-ref="{ref}"]')
            if await loc.count() != 1:
                raise StaleElementReference(ref, "элемент исчез со страницы")
            item = await loc.evaluate(_js(
                """(el) => ({
                  tag: el.tagName.toLowerCase(),
                  role: el.getAttribute('role') || '',
                  name: el.getAttribute('name') || '',
                  type: el.getAttribute('type') || '',
                  href: el.href || '',
                  text: __IS_SECRET__(el)
                        ? '' : (el.innerText || el.value || '')
                             .replace(/\\s+/g, ' ').trim().slice(0, 220),
                })"""))
            if _fingerprint(item) != known["fingerprint"]:
                raise StaleElementReference(
                    ref, "на месте элемента теперь другой — страница изменилась")
            return loc

        if not selector:
            raise ValueError("нужен selector или ref")
        loc = sess.page.locator(selector)
        count = await loc.count()
        if count > 1:
            # `.first` здесь был бы выбором наугад: на странице с двумя
            # одинаковыми кнопками агент считал бы, что нажал нужную.
            raise AmbiguousSelector(selector, count)
        return loc.first

    async def click(self, session_id: int, selector: str = "", *,
                    ref: str = "", actor: str = "agent",
                    approved: bool = False,
                    allow_download: bool | None = None) -> dict[str, Any]:
        sess = self._session(session_id)
        self._guard(sess, "click", actor=actor, approved=approved)
        loc = await self._target(sess, selector, ref)
        self._drain_downloads(sess)
        source = str(sess.page.url or "")
        await loc.click(timeout=30000)
        # Клик по ссылке-файлу начинает загрузку: не теряем её молча (B4).
        dl = await self._next_download(sess, CLICK_DOWNLOAD_GRACE_S)
        if dl is not None:
            record = await self._save_download(sess, dl, trigger="click", source_url=source,
                                               actor=actor, approved=approved,
                                               allow_download=allow_download)
            return await self._download_result(sess, record)
        return await self.status(session_id)

    async def type_text(self, session_id: int, selector: str = "", text: str = "", *,
                        ref: str = "", actor: str = "agent",
                        approved: bool = False) -> dict[str, Any]:
        sess = self._session(session_id)
        self._guard(sess, "type", actor=actor, approved=approved)
        loc = await self._target(sess, selector, ref)
        await loc.fill(text, timeout=30000)
        return await self.status(session_id)

    async def select(self, session_id: int, selector: str = "", value: str = "", *,
                     ref: str = "", actor: str = "agent",
                     approved: bool = False) -> dict[str, Any]:
        sess = self._session(session_id)
        self._guard(sess, "select", actor=actor, approved=approved)
        loc = await self._target(sess, selector, ref)
        await loc.select_option(value, timeout=30000)
        return await self.status(session_id)

    async def fill_secret(self, session_id: int, selector: str = "", *,
                          secret: str, ref: str = "", actor: str = "agent",
                          approved: bool = False) -> dict[str, Any]:
        """Ввести секрет, которого модель не видела и не увидит.

        Значение приходит из хранилища учётных данных рантайма, а не из
        аргументов инструмента: в `tool_calls.args` его нет, в контексте модели
        его нет, и в снимок оно не попадёт (см. `snapshot` и `redact_secrets`).
        """
        sess = self._session(session_id)
        self._guard(sess, "type", actor=actor, approved=approved)
        loc = await self._target(sess, selector, ref)
        if secret:
            sess.secrets.add(secret)
        await loc.fill(secret, timeout=30000)
        return await self.status(session_id)

    async def back(self, session_id: int, *, actor: str = "agent") -> dict[str, Any]:
        sess = self._session(session_id)
        self._guard(sess, "back", actor=actor)
        await sess.page.go_back(wait_until="domcontentloaded", timeout=30000)
        return await self.status(session_id)

    async def reload(self, session_id: int, *, actor: str = "agent") -> dict[str, Any]:
        sess = self._session(session_id)
        self._guard(sess, "reload", actor=actor)
        await sess.page.reload(wait_until="domcontentloaded", timeout=30000)
        return await self.status(session_id)

    async def screenshot(self, session_id: int, *,
                         actor: str = "agent", approved: bool = False) -> bytes:
        sess = self._session(session_id)
        self._guard(sess, "screenshot", actor=actor, approved=approved)
        return await sess.page.screenshot(type="png", full_page=False)

    async def snapshot(self, session_id: int, *,
                       actor: str = "agent", approved: bool = False,
                       max_text: int = 20000, max_interactive: int = 200) -> dict[str, Any]:
        sess = self._session(session_id)
        self._guard(sess, "snapshot", actor=actor, approved=approved)
        page = sess.page
        # DOM-first snapshot: cheap and deterministic. Vision only receives screenshot when needed.
        sess.generation += 1
        generation = sess.generation
        data = await page.evaluate(_js(
            """(limits) => {
              const isSecret = __IS_SECRET__;
              const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
              const text = clean(document.body ? document.body.innerText : '').slice(0, limits.maxText);
              const selectors = 'a,button,input,textarea,select,[role="button"],[role="link"],[tabindex]';
              const nodes = Array.from(document.querySelectorAll(selectors)).slice(0, limits.maxInteractive);
              // Ссылки прошлого поколения снимаем: иначе устаревший атрибут
              // остался бы на элементе и выглядел действительным.
              document.querySelectorAll('[data-bcc-ref]').forEach((el) => {
                el.removeAttribute('data-bcc-ref');
              });
              const interactive = nodes.map((el, i) => {
                const tag = el.tagName.toLowerCase();
                // ЗНАЧЕНИЕ СЕКРЕТНОГО ПОЛЯ НЕ ПОКИДАЕТ СТРАНИЦУ. Модели
                // достаточно знать, что поле заполнено, — само значение ей не
                // нужно ни для одного сценария. Признак — общий с проверкой
                // ссылки (см. _JS_IS_SECRET): не только `type=password`, но и
                // `type=hidden`, коды из SMS и пароли в текстовых полях.
                const secret = isSecret(el);
                const raw = secret ? '' : clean(el.innerText || el.value || '');
                const ref = 'e' + limits.generation + '-' + i;
                el.setAttribute('data-bcc-ref', ref);
                return {
                  i,
                  ref,
                  tag,
                  role: el.getAttribute('role') || '',
                  text: raw.slice(0, 220),
                  aria: clean(el.getAttribute('aria-label') || '').slice(0, 220),
                  name: el.getAttribute('name') || '',
                  type: el.getAttribute('type') || '',
                  placeholder: clean(el.getAttribute('placeholder') || '').slice(0, 220),
                  href: el.href || '',
                  disabled: !!el.disabled,
                  secret,
                  filled: secret ? !!el.value : undefined,
                };
              });
              return { text, interactive };
            }"""),
            {"maxText": max_text, "maxInteractive": max_interactive,
             "generation": generation},
        )
        interactive = data.get("interactive") or []
        # Капча: распознаём и зовём человека. Автоматически не решаем — см.
        # CAPTCHA_MARKERS. Дальнейшие действия агента блокируются takeover'ом,
        # чтобы он не «дожимал» страницу вслепую и не тратил бюджет шагов.
        captcha = detect_captcha(await page.content(), data.get("text") or "")
        # Метка живёт ровно пока капча на странице: человек её прошёл — следующий
        # снимок снимет блокировку сам, без ручного Resume.
        sess.captcha = captcha if captcha["present"] else {}
        # Отпечаток элемента: по нему при действии проверяется, что под ссылкой
        # тот же элемент, а не сосед, занявший его место после перерисовки.
        sess.refs = {
            str(item.get("ref")): {
                "generation": generation,
                "fingerprint": _fingerprint(item),
                "tag": item.get("tag"), "name": item.get("name"),
            }
            for item in interactive if item.get("ref")
        }
        return redact_secrets({
            "session_id": session_id,
            "url": page.url,
            "title": await page.title(),
            "generation": generation,
            "text": data.get("text") or "",
            "interactive": interactive,
            "takeover": sess.takeover,
            "paused": sess.paused,
            "mode": sess.policy.mode,
            "captcha": captcha,
        }, sess.secrets)

    # ------------------------------------------------ Jev fast path (read-only)
    #
    # Two READ actions for bcc.jev.browser_fastpath. They never click, type or
    # navigate, and go through the same _guard as every read. The probe only
    # annotates elements the regular snapshot already indexed (data-bcc-ref) and
    # counts page features the fast path must NOT handle (frames, shadow roots,
    # canvas, uploads, nested scroll, popups) — those escalate.

    _JEV_PROBE = """() => {
      const isSecret = __IS_SECRET__;
      const vis = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
      const big = (el) => { const r = el.getBoundingClientRect(); return r.width >= 50 && r.height >= 50; };
      const items = {};
      document.querySelectorAll('[data-bcc-ref]').forEach((el) => {
        const ref = el.getAttribute('data-bcc-ref');
        const tag = el.tagName.toLowerCase();
        const it = { visible: vis(el) };
        if (tag === 'select') {
          it.options = Array.from(el.options).slice(0, 100).map((o) => ({
            value: o.value, label: (o.label || o.text || '').replace(/\\s+/g, ' ').trim().slice(0, 120),
            selected: !!o.selected }));
          it.value = el.value;
        } else if ((tag === 'input' || tag === 'textarea') && !isSecret(el)) {
          it.value = (el.value || '').slice(0, 200);
          it.checked = !!el.checked;
          it.editable = !el.readOnly && !el.disabled;
        }
        items[ref] = it;
      });
      const all = Array.from(document.querySelectorAll('body *')).slice(0, 5000);
      let shadow = 0, nested = 0;
      for (const el of all) {
        if (el.shadowRoot) shadow++;
        const s = getComputedStyle(el);
        if ((s.overflowY === 'auto' || s.overflowY === 'scroll') && el.clientHeight > 50
            && el.scrollHeight > el.clientHeight + 20) nested++;
      }
      return { items, features: {
        iframes: Array.from(document.querySelectorAll('iframe,frame')).filter((e) => vis(e) && big(e)).length,
        shadow_roots: shadow,
        canvas: Array.from(document.querySelectorAll('canvas')).filter((e) => vis(e) && big(e)).length,
        file_inputs: document.querySelectorAll('input[type=file]').length,
        nested_scroll: nested,
      } };
    }"""

    async def _jev_probe(self, sess: BrowserRuntimeSession) -> dict[str, Any]:
        data = await sess.page.evaluate(_js(self._JEV_PROBE))
        data = data if isinstance(data, dict) else {}
        features = dict(data.get("features") or {})
        try:
            features["popups"] = max(0, len(sess.context.pages) - 1)
        except Exception:  # noqa: BLE001 — unknown → the adapter treats it as unsupported
            features["popups"] = -1
        return {"items": data.get("items") or {}, "features": features}

    async def jev_observe(self, session_id: int, *, actor: str = "agent",
                          approved: bool = False) -> dict[str, Any]:
        """Regular DOM snapshot + per-ref values/options + unsupported-feature counts."""
        snap = await self.snapshot(session_id, actor=actor, approved=approved)
        sess = self._session(session_id)
        probe = await self._jev_probe(sess)
        items = probe["items"]
        for item in snap.get("interactive") or []:
            extra = items.get(str(item.get("ref"))) or {}
            for key in ("visible", "options", "value", "checked", "editable"):
                if key in extra:
                    item[key] = extra[key]
        snap["features"] = probe["features"]
        return redact_secrets(snap, sess.secrets)

    async def jev_current(self, session_id: int, *, actor: str = "agent",
                          approved: bool = False) -> dict[str, Any]:
        """Freshness read WITHOUT a new generation: url, generation and per-ref state."""
        sess = self._session(session_id)
        self._guard(sess, "read_dom", actor=actor, approved=approved)
        probe = await self._jev_probe(sess)
        return redact_secrets({"url": sess.page.url, "generation": sess.generation,
                               "refs": sorted(sess.refs), "items": probe["items"],
                               "features": probe["features"]}, sess.secrets)
