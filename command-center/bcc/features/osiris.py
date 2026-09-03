"""OSIRIS — слой происхождения данных и реестр источников. Флаг OFF по умолчанию.

Это пункты 1 и 2 «порядка работ» из `docs/osiris/OSIRIS_DATA_ACQUISITION_PROMPT.md`
(раздел 7): сперва происхождение, только потом граф. Витрина без этого слоя —
картинка, за которую нельзя отвечать, поэтому здесь нет ни графа, ни UI.

Из чего состоит модуль:

  * `Observation` — факт вместе с паспортом. Конструктор ОТКАЗЫВАЕТ (исключение,
    не запись в лог), если нет `source_id`, `observed_at` или `method`.
    Предупреждение в логе можно не заметить — исключение нельзя, поэтому запись
    без паспорта не существует как объект, а не «создаётся с оговоркой»;
  * реестр источников — декларативные данные (`Source`), а не код в обработчике.
    Его можно показать владельцу и ответить на вопрос «откуда у тебя это»:
    категория, адрес, режим авторизации, лимит, лицензия, что отдаёт, чего НЕ
    отдаёт, дата последней проверки условий использования;
  * запреты раздела 2 ТЗ — код, а не текст. `_forbidden_reason` отклоняет
    декларацию источника с признаками запрещённого способа сбора, категория C
    без разрешающего `robots.txt` не собирается, а любой исходящий адрес
    проходит через УЖЕ СУЩЕСТВУЮЩУЮ проверку egress
    (`plugin_security.validate_url`: SSRF, приватные диапазоны, метаданные
    облака, короткие формы IPv4). Второго такого слоя здесь не появляется —
    вызов идёт через модуль `psec`, чтобы подмена в тесте доказывала именно
    переиспользование;
  * один источник категории A целиком — открытый REST API Викимедиа
    (`en.wikipedia.org/api/rest_v1`): без ключа и без оплаты, лицензия
    CC BY-SA 4.0. Конфигурация, лимит запросов, кэш сырья с TTL, обработка
    ошибок и превращение ответа в `Observation` с настоящим `source_url`;
  * сеть — только через типизированный адаптер `FetchAdapter`. В тестах он
    подменяется, при выключенном флаге не вызывается вовсе. Подменённый
    адаптер НЕ ИМЕЕТ ПРАВА объявить источник рабочим: пометку «проверен живьём»
    ставит только транспорт с `live = True`, поэтому статус источника,
    который живьём не дёргали, честно остаётся `not_verified_live`.

Хранилище — файлы в `settings.data_dir/osiris`, никогда не в репозитории:
сырьё (`raw/`) отдельно от выводов (`observations/`), связь через `raw_ref`,
плюс `index.json` для права на удаление одной операцией.

Флаг `BOSSMAN_OSIRIS_ENABLED` выключен по умолчанию: ручки, меняющие состояние,
отвечают 409 и не создают ни одного файла; читающие отвечают `enabled: false`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, urlparse
from urllib.robotparser import RobotFileParser

from fastapi import APIRouter, HTTPException, Query, Request

from .. import plugin_security as psec
from ..db import utcnow
from ..plugin_security import PluginSecurityError
from . import Feature

FLAG = "BOSSMAN_OSIRIS_ENABLED"
DIRNAME = "osiris"                       # внутри settings.data_dir, никогда не в git
router = APIRouter()

# Способ получения. Список закрыт намеренно: «прочее» превращает паспорт в
# свободный текст, по которому нельзя судить о том, разрешён ли был способ.
METHODS = ("api", "dataset", "fetch", "user_upload")

# Категория источника задаёт метод. Это не украшение: «официальный API» и
# «страница без API» дают разные обязанности (robots.txt, вежливый UA), и
# перепутать их — значит собирать не тем способом, который разрешён.
CATEGORY_METHOD = {"A": "api", "B": "dataset", "C": "fetch", "D": "user_upload"}
AUTH_MODES = ("none", "api_key", "oauth", "user_upload")

MAX_BYTES = 1_000_000                    # потолок тела ответа: сырьё, а не зеркало сайта
MAX_SUBJECT = 200
USER_AGENT = "BossmanOsiris/1.0 (+https://github.com/molotroka123-cell/AiMaxBossman)"


def enabled() -> bool:
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes")


class OsirisError(ValueError):
    """Отказ слоя происхождения (fail-closed): при сомнении отклоняем.

    Код и HTTP-статус живут на самом исключении, чтобы обработчик не занимался
    разбором текста ошибки: у отказа есть машинно-читаемая причина.
    """

    code = "osiris_error"
    http_status = 400


class PassportError(OsirisError):
    """Паспорт факта неполон или противоречив — запись не сохраняется."""

    code = "passport_incomplete"


class ForbiddenSourceError(OsirisError):
    """Источник описан признаками запрещённого способа сбора (раздел 2 ТЗ)."""

    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


class SourceUnknownError(OsirisError):
    """Собирать нечем: источника нет в реестре."""

    code = "source_unknown"
    http_status = 404


class RobotsDisallowError(OsirisError):
    """robots.txt не разрешил обход (или недоступен) — сбор не выполняется."""

    code = "robots_disallow"
    http_status = 403


class RateLimitedError(OsirisError):
    """Исчерпан объявленный в реестре лимит запросов к источнику."""

    code = "rate_limited"
    http_status = 429


class SourceUnavailableError(OsirisError):
    """Источник не ответил или ответил не тем — наблюдений не появляется."""

    code = "source_unavailable"
    http_status = 502


# ------------------------------------------------------------------ паспорт


@dataclass(frozen=True)
class Observation:
    """Факт вместе с происхождением (раздел 3 ТЗ).

    Заморожен: паспорт нельзя переписать после создания — иначе «откуда взято»
    перестаёт быть свойством записи и становится мнением того, кто её последним
    трогал. Обязательные поля проверяются в конструкторе, поэтому объекта без
    паспорта в системе не бывает вовсе.

    Поля `attribute` и `id` — сверх минимума ТЗ: без имени наблюдаемого поля
    значение непонятно, без идентификатора запись нечем адресовать в файлах.
    """

    value: Any
    subject: str
    source_id: str
    source_url: str
    method: str
    license: str
    observed_at: datetime
    collected_at: datetime = dc_field(default_factory=utcnow)
    confidence: float = 0.5
    raw_ref: str | None = None
    attribute: str = ""
    id: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.subject, str) or not self.subject.strip():
            raise PassportError("subject обязателен: наблюдение без сущности не адресуемо")
        if not isinstance(self.source_id, str) or not self.source_id.strip():
            raise PassportError("source_id обязателен: факт без названного источника "
                               "нельзя проверить и нельзя за него отвечать")
        if self.method not in METHODS:
            raise PassportError(f"method обязателен и должен быть одним из {METHODS}: "
                                f"получено {self.method!r}")
        if not isinstance(self.observed_at, datetime):
            raise PassportError("observed_at обязателен и должен быть datetime: "
                                "без времени наблюдения свежий факт не отличить от прошлогоднего")
        if not isinstance(self.collected_at, datetime):
            raise PassportError("collected_at должен быть datetime")
        if not isinstance(self.source_url, str) or not self.source_url.strip():
            raise PassportError("source_url обязателен: адрес должен открываться человеком")
        if not isinstance(self.license, str) or not self.license.strip():
            raise PassportError("license обязательна: без лицензии неизвестно, "
                                "что с этими данными вообще можно делать")
        if not isinstance(self.confidence, (int, float)) or isinstance(self.confidence, bool) \
                or not 0.0 <= float(self.confidence) <= 1.0:
            raise PassportError("confidence — число 0..1")
        # Сетевые методы обязаны ссылаться на сырьё: перепроверка без повторного
        # обращения к источнику — требование раздела 3 п.2, а не удобство.
        if self.method in ("api", "fetch") and not (self.raw_ref or "").strip():
            raise PassportError(f"raw_ref обязателен для method={self.method}: "
                                "вывод без сырья нечем перепроверить")
        if self.source_url.lower().startswith(("http://", "https://")):
            # Адрес в паспорте — тот же самый, по которому ходили: проверяем той
            # же функцией egress, а не «похожей» (fail-closed).
            psec.validate_url(self.source_url)
        try:
            json.dumps(self.value, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise PassportError(f"value должно сериализоваться в JSON: {exc}") from exc
        if not self.id:
            object.__setattr__(self, "id", _observation_id(self))

    def as_dict(self) -> dict:
        """Полный паспорт наружу: ни одно поле не прячется от владельца."""
        return {"id": self.id, "value": self.value, "subject": self.subject,
                "attribute": self.attribute, "source_id": self.source_id,
                "source_url": self.source_url, "method": self.method,
                "license": self.license, "observed_at": self.observed_at.isoformat(),
                "collected_at": self.collected_at.isoformat(),
                "confidence": float(self.confidence), "raw_ref": self.raw_ref}


def _observation_id(obs: Observation) -> str:
    """Сортируемый идентификатор: время сбора + хэш содержимого. Время впереди,
    чтобы история наблюдений читалась по именам файлов без разбора JSON."""
    stamp = obs.collected_at.strftime("%Y%m%dT%H%M%S%f")
    digest = hashlib.sha256(
        f"{obs.subject}|{obs.source_id}|{obs.attribute}|{obs.value!r}|{stamp}".encode("utf-8")
    ).hexdigest()[:10]
    return f"{stamp}-{digest}"


def subject_key(subject: str) -> str:
    """Имя каталога субъекта: читаемый префикс + хэш. Хэш нужен, потому что
    разные субъекты («Иванов И.» и «иванов и»!) могут дать одинаковый слаг."""
    slug = re.sub(r"[^a-z0-9._-]+", "-", subject.strip().lower()).strip("-")[:40]
    digest = hashlib.sha256(subject.strip().encode("utf-8")).hexdigest()[:12]
    return f"{slug or 'subject'}-{digest}"


# ------------------------------------------------------- реестр источников


@dataclass
class Source:
    """Декларация источника — данные, а не код (раздел 4 ТЗ).

    Добавление источника — отдельное осознанное действие с проверкой условий
    использования, поэтому `tos_checked_at` обязателен и хранится вместе с
    остальным: по реестру владелец отвечает на вопрос «откуда у тебя это».
    """

    id: str
    category: str
    base_url: str
    auth_mode: str
    rate_limit_per_min: int
    license: str
    provides: tuple[str, ...]
    not_provides: tuple[str, ...]
    tos_checked_at: str
    method: str
    parser: str = "generic_json"
    path_template: str = "/{subject}"
    observed_at_field: str = ""
    cache_ttl_seconds: int = 3600
    default_confidence: float = 0.5
    contact: str = ""
    notes: str = ""
    # Живая проверка: ставится ТОЛЬКО транспортом с live=True. Стенд подменяет
    # адаптер и обязан оставить источник непроверенным, иначе «работает» в
    # отчёте означало бы «фикстура сработала».
    live_status: str = "not_verified_live"
    live_checked_at: str | None = None
    live_error: str = ""

    def url_for(self, subject: str) -> str:
        path = (self.path_template or "/{subject}").replace("{subject}", quote(subject, safe=""))
        if not path.startswith("/"):
            path = "/" + path
        return self.base_url.rstrip("/") + path

    def as_dict(self) -> dict:
        return {"id": self.id, "category": self.category, "base_url": self.base_url,
                "auth_mode": self.auth_mode, "rate_limit_per_min": self.rate_limit_per_min,
                "license": self.license, "provides": list(self.provides),
                "not_provides": list(self.not_provides), "tos_checked_at": self.tos_checked_at,
                "method": self.method, "parser": self.parser,
                "path_template": self.path_template,
                "observed_at_field": self.observed_at_field,
                "cache_ttl_seconds": self.cache_ttl_seconds,
                "default_confidence": self.default_confidence, "contact": self.contact,
                "notes": self.notes, "live_status": self.live_status,
                "live_checked_at": self.live_checked_at, "live_error": self.live_error}


# Встроенный источник категории A: официальный открытый REST API Викимедиа.
# Ключа не требует, денег не стоит, лицензия текста — CC BY-SA 4.0. Выбран
# именно он, потому что отдаёт время последней правки (`timestamp`) — значит
# `observed_at` берётся из источника, а не выдумывается нами.
BUILTIN_SOURCES: tuple[dict, ...] = (
    {
        "id": "wikipedia-rest-en",
        "category": "A",
        "base_url": "https://en.wikipedia.org",
        "auth_mode": "none",
        "rate_limit_per_min": 30,
        "license": "CC BY-SA 4.0",
        "provides": ["title", "description", "extract", "coordinates", "wikibase_item", "lang"],
        "not_provides": ["контакты", "домашние адреса", "закрытые разделы", "лицевые снимки"],
        "tos_checked_at": "2026-09-03",
        "method": "api",
        "parser": "wikipedia_summary",
        "path_template": "/api/rest_v1/page/summary/{subject}",
        "cache_ttl_seconds": 3600,
        "default_confidence": 0.9,
        "notes": "Открытый REST API Викимедиа: без ключа и без оплаты. "
                 "Текст под CC BY-SA 4.0, требуется указание авторства.",
    },
)


# ------------------------------------------------- запреты как код (раздел 2)

# Ключи, которые описывают источник ПРОЗОЙ. Их не сканируем словарём запретов:
# честное «чего источник НЕ отдаёт» («лицевые снимки», «закрытые разделы»)
# иначе запретило бы само себя. Структурные проверки на них всё равно
# распространяются — исключение касается только словаря.
_PROSE_KEYS = {"not_provides", "notes"}

# Флаги «а давай всё-таки обойдём»: наличие любого из них с истинным значением —
# отказ. Обход robots.txt «потому что технически получается» запрещён списком.
_ROBOTS_OVERRIDE_KEYS = {"ignore_robots", "robots_override", "bypass_robots",
                         "skip_robots", "force_fetch", "obey_robots_txt_off"}

# Словарь признаков. Каждый пункт — строка из раздела «Запрещено by design»,
# и на каждый есть тест, доказывающий отказ. Формулировки намеренно широкие:
# fail-closed означает, что при сомнении источник отклоняется, а оператор
# переименует поле, если совпадение было случайным.
FORBIDDEN_PATTERNS: tuple[tuple[str, str, str], ...] = (
    ("foreign_session",
     r"cookie|set-cookie|session[_-]?id|sessionid|phpsessid|jsessionid|"
     r"auth[_-]?session|logged[_-]?in[_-]?as|stolen[_-]?account|чуж",
     "вход под чужой учётной записью и использование чужих cookie/сессий"),
    # Порядок значим только для качества объяснения: «paywall» стоит раньше
    # намеренно широкого «bypass», иначе обход paywall объяснялся бы капчей.
    # На сам отказ порядок не влияет — сработает любой из пунктов.
    ("paywall_bypass",
     r"paywall|12ft\.io|removepaywall",
     "обход paywall"),
    ("captcha_bypass",
     r"captcha|anti-?bot|waf[_-]?bypass|cloudflare[_-]?bypass|bypass|"
     r"rate[_-]?limit[_-]?evasion|обход",
     "обход капчи, anti-bot защиты или лимита запросов"),
    ("leaked_database",
     r"combo-?list|breach(ed)?[_-]?(db|base|data|dump)|leak(ed)?[_-]?(db|base|data|dump)|"
     r"stealer|dehashed|слит|утечк",
     "«слитые базы», комбо-листы и купленные дампы персональных данных"),
    ("biometrics",
     r"biometri|facial[_-]?recognition|face[_-]?(search|match|recognition|print)|"
     r"pimeyes|findclone|биометри|распознавание лиц",
     "распознавание лиц и биометрия по фото людей"),
    ("private_scope",
     r"private[_-]?(profile|group|chat|message|inbox)|closed[_-]?group|"
     r"members[_-]?only[_-]?dump|приватн|закрыт",
     "сбор из закрытых и приватных профилей и групп"),
    ("robots_override",
     r"ignore[_-]?robots|robots[_-]?override|skip[_-]?robots|disregard[_-]?robots",
     "обход запрета в robots.txt «потому что технически получается»"),
    ("person_tracking",
     r"deanon|doxx?ing|dox\b|stalk|geo[_-]?track|location[_-]?history[_-]?of|"
     r"деанон|слежк",
     "деанонимизация частных лиц, слежка и профилирование по местоположению"),
)
_COMPILED = tuple((code, re.compile(pattern), why) for code, pattern, why in FORBIDDEN_PATTERNS)


def _flatten(value: Any, prefix: str = "") -> list[str]:
    """Декларация в плоский список «ключ» и «ключ=значение»: словарь запретов
    смотрит и на имена полей, и на содержимое, на любой глубине."""
    out: list[str] = []
    if isinstance(value, dict):
        for key, sub in value.items():
            if str(key) in _PROSE_KEYS:
                continue
            out.append(f"{prefix}{key}")
            out.extend(_flatten(sub, prefix=f"{prefix}{key}."))
    elif isinstance(value, (list, tuple)):
        for item in value:
            out.extend(_flatten(item, prefix=prefix))
    else:
        out.append(f"{prefix}={value}")
    return out


def _forbidden_reason(decl: dict) -> tuple[str, str] | None:
    """(код, объяснение) если декларация несёт признак запрещённого способа."""
    for key in _ROBOTS_OVERRIDE_KEYS:
        if decl.get(key):
            return ("robots_override",
                    "обход запрета в robots.txt «потому что технически получается»")
    blob = " ".join(_flatten(decl)).lower()
    for code, pattern, why in _COMPILED:
        if pattern.search(blob):
            return code, why
    return None


def checked_url(url: str) -> str:
    """Единственная дверь наружу: адрес проходит существующую проверку egress.

    Своей проверки SSRF/приватных диапазонов/коротких форм IPv4 здесь нет и не
    будет — `plugin_security.validate_url` уже умеет всё это, и второй такой
    слой означал бы две разные границы вместо одной.
    """
    psec.validate_url(url)
    return url


def normalize_source(decl: dict) -> Source:
    """Декларация → `Source` с полной проверкой. Бросает при любом сомнении."""
    if not isinstance(decl, dict):
        raise OsirisError("декларация источника должна быть объектом")
    source_id = str(decl.get("id") or "").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,63}", source_id):
        raise OsirisError("id источника: 2..64 символов [a-z0-9._-], начиная с буквы или цифры")
    category = str(decl.get("category") or "").strip().upper()
    if category not in CATEGORY_METHOD:
        raise OsirisError("категория обязана быть A, B, C или D (раздел 2 ТЗ)")

    # Запреты проверяются ДО всего остального содержательного: fail-closed не
    # должен зависеть от того, дошли ли мы до разбора адреса.
    forbidden = _forbidden_reason(decl)
    if forbidden is not None:
        code, why = forbidden
        raise ForbiddenSourceError(
            f"источник отклонён: {why}. Это запрещено by design (раздел 2 ТЗ), "
            f"исключений «на один раз» не бывает", code=code)

    method = str(decl.get("method") or CATEGORY_METHOD[category]).strip()
    if method != CATEGORY_METHOD[category]:
        raise OsirisError(f"категория {category} собирается методом "
                          f"{CATEGORY_METHOD[category]}, а не {method!r}")
    auth_mode = str(decl.get("auth_mode") or "none").strip()
    if auth_mode not in AUTH_MODES:
        raise OsirisError(f"auth_mode обязан быть одним из {AUTH_MODES}")
    license_ = str(decl.get("license") or "").strip()
    if not license_:
        raise OsirisError("license обязательна: без неё неизвестно, что можно делать с данными")
    tos = str(decl.get("tos_checked_at") or "").strip()
    try:
        checked = datetime.fromisoformat(tos).date()
    except ValueError as exc:
        raise OsirisError("tos_checked_at обязателен: дата последней проверки условий "
                          "использования в формате ГГГГ-ММ-ДД") from exc
    if checked > utcnow().date():
        raise OsirisError("tos_checked_at не может быть в будущем")

    base_url = str(decl.get("base_url") or "").strip()
    if category == "D":
        # Категория D — то, что владелец принёс сам. Наружу не ходим вовсе.
        if base_url and not base_url.startswith("upload://"):
            raise OsirisError("категория D не имеет сетевого адреса: ожидается upload://…")
    else:
        checked_url(base_url)              # приватные адреса и метаданные облака — сюда

    try:
        rate = int(decl.get("rate_limit_per_min") or 0)
    except (TypeError, ValueError) as exc:
        raise OsirisError("rate_limit_per_min — целое число") from exc
    if not 1 <= rate <= 600:
        raise OsirisError("rate_limit_per_min обязателен: 1..600 запросов в минуту")

    provides = tuple(str(x) for x in (decl.get("provides") or ()))
    if not provides and category != "D":
        raise OsirisError("provides обязателен: какие поля источник отдаёт")
    parser = str(decl.get("parser") or "generic_json").strip()
    if parser not in PARSERS:
        raise OsirisError(f"неизвестный parser {parser!r}: доступны {tuple(PARSERS)}")
    try:
        confidence = float(decl.get("default_confidence", 0.5))
    except (TypeError, ValueError) as exc:
        raise OsirisError("default_confidence — число 0..1") from exc
    if not 0.0 <= confidence <= 1.0:
        raise OsirisError("default_confidence — число 0..1")

    return Source(
        id=source_id, category=category, base_url=base_url, auth_mode=auth_mode,
        rate_limit_per_min=rate, license=license_,
        provides=provides,
        not_provides=tuple(str(x) for x in (decl.get("not_provides") or ())),
        tos_checked_at=checked.isoformat(), method=method, parser=parser,
        path_template=str(decl.get("path_template") or "/{subject}"),
        observed_at_field=str(decl.get("observed_at_field") or ""),
        cache_ttl_seconds=max(0, int(decl.get("cache_ttl_seconds") or 0)),
        default_confidence=confidence, contact=str(decl.get("contact") or ""),
        notes=str(decl.get("notes") or ""),
        live_status=str(decl.get("live_status") or "not_verified_live"),
        live_checked_at=decl.get("live_checked_at"),
        live_error=str(decl.get("live_error") or ""))


# ------------------------------------------------------- транспорт наружу


@dataclass(frozen=True)
class FetchResult:
    """Ответ источника в терминах слоя, а не библиотеки: адаптер подменяем."""

    status: int
    body: str
    url: str
    headers: dict[str, str] = dc_field(default_factory=dict)


class FetchAdapter(Protocol):
    """Единственная точка выхода наружу. `live` отвечает на вопрос «это была
    настоящая сеть?» — от него зависит право пометить источник проверенным."""

    live: bool

    async def fetch(self, url: str, *, headers: dict[str, str] | None = None,
                    timeout: float = 15.0) -> FetchResult: ...


class HttpFetchAdapter:
    """Настоящая сеть — через `plugin_security.safe_get` (SSRF, редиректы,
    pinned-резолв, потолок тела). Своего HTTP-клиента слой не заводит."""

    live = True

    async def fetch(self, url: str, *, headers: dict[str, str] | None = None,
                    timeout: float = 15.0) -> FetchResult:
        resp = await psec.safe_get(url, headers={"User-Agent": USER_AGENT, **(headers or {})},
                                   timeout=timeout, max_bytes=MAX_BYTES)
        return FetchResult(status=resp.status_code, body=resp.text, url=url,
                           headers={k: v for k, v in resp.headers.items()})


# --------------------------------------------------------------- парсеры


def _dig(payload: Any, path: str) -> Any:
    """Значение по точечному пути; None, если пути нет. Отсутствие поля — не
    ошибка: источник имеет право не знать про эту сущность всего."""
    cur = payload
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _parse_generic_json(source: Source, subject: str, payload: Any, *, url: str,
                        raw_ref: str, collected_at: datetime,
                        fetched_at: datetime) -> list[Observation]:
    """Общий разбор: берём ровно те поля, которые источник ЗАЯВИЛ в `provides`.

    Незаявленное поле не попадает в наблюдения, даже если пришло в ответе: реестр
    — обещание владельцу, и оно должно быть проверяемым.
    """
    observed = _parse_iso(_dig(payload, source.observed_at_field)) if source.observed_at_field \
        else None
    # Источник не сказал, когда факт появился, — честное «мы видели его в момент
    # обращения». Выдумывать более раннее время нельзя, это подделка паспорта.
    observed_at = observed or fetched_at
    out: list[Observation] = []
    for name in source.provides:
        value = _dig(payload, name)
        if value is None:
            continue
        out.append(Observation(
            value=value, subject=subject, source_id=source.id, source_url=url,
            method=source.method, license=source.license, observed_at=observed_at,
            collected_at=collected_at, confidence=source.default_confidence,
            raw_ref=raw_ref, attribute=name))
    return out


def _parse_wikipedia_summary(source: Source, subject: str, payload: Any, *, url: str,
                             raw_ref: str, collected_at: datetime,
                             fetched_at: datetime) -> list[Observation]:
    """Разбор ответа REST-сводки Викимедиа.

    `source_url` в паспорте — человекочитаемая страница (`/wiki/…`), а не адрес
    API: владелец должен иметь возможность открыть её и увидеть то же самое.
    `observed_at` — время последней правки: именно с него факт виден в источнике.
    """
    if not isinstance(payload, dict):
        raise OsirisError("ответ источника не является объектом JSON")
    title = str(payload.get("title") or subject)
    human_url = _dig(payload, "content_urls.desktop.page") or \
        f"{source.base_url.rstrip('/')}/wiki/{quote(title, safe='')}"
    observed_at = _parse_iso(payload.get("timestamp")) or fetched_at
    out: list[Observation] = []
    for name in source.provides:
        value = payload.get(name)
        if name == "coordinates" and isinstance(value, dict):
            value = {"lat": value.get("lat"), "lon": value.get("lon")}
        if value in (None, "", {}, []):
            continue
        out.append(Observation(
            value=value, subject=subject, source_id=source.id, source_url=human_url,
            method=source.method, license=source.license, observed_at=observed_at,
            collected_at=collected_at, confidence=source.default_confidence,
            raw_ref=raw_ref, attribute=name))
    return out


PARSERS = {"generic_json": _parse_generic_json, "wikipedia_summary": _parse_wikipedia_summary}


# --------------------------------------------------------------- хранилище


class OsirisStore:
    """Файловое хранилище слоя: сырьё, наблюдения, индекс субъектов.

    Сырьё отдельно от выводов — не из аккуратности, а чтобы перепроверка не
    требовала повторного обращения к источнику (раздел 3 п.2). Индекс нужен для
    права на удаление: снести субъекта одной операцией, не оставив следов.
    """

    def __init__(self, root: Path, *, bus=None):
        self.root = Path(root)
        self.bus = bus
        self.adapter: FetchAdapter = HttpFetchAdapter()
        self._hits: dict[str, list[float]] = {}
        self._last_collected: datetime | None = None

    # ---- пути (ни один из них не создаёт каталог: чтение не пишет на диск)

    @property
    def sources_dir(self) -> Path:
        return self.root / "sources"

    @property
    def raw_dir(self) -> Path:
        return self.root / "raw"

    @property
    def obs_dir(self) -> Path:
        return self.root / "observations"

    @property
    def index_path(self) -> Path:
        return self.root / "index.json"

    def _write_json(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
                       encoding="utf-8")
        os.replace(tmp, path)

    @staticmethod
    def _read_json(path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    # ---- реестр

    def sources(self) -> dict[str, Source]:
        """Встроенные декларации плюс сохранённые на диске (диск главнее: там
        лежит в том числе честный статус живой проверки)."""
        out: dict[str, Source] = {}
        for decl in BUILTIN_SOURCES:
            src = normalize_source(decl)
            out[src.id] = src
        if self.sources_dir.exists():
            for path in sorted(self.sources_dir.glob("*.json")):
                decl = self._read_json(path)
                if not isinstance(decl, dict):
                    continue
                try:
                    src = normalize_source(decl)
                except (OsirisError, PluginSecurityError):
                    # Правленый вручную файл не попадает в реестр и не роняет
                    # ответ: источник, который сейчас не проходит проверку, —
                    # не источник, о нём просто нечего сказать владельцу.
                    continue
                out[src.id] = src
        return out

    def get_source(self, source_id: str) -> Source:
        src = self.sources().get(str(source_id))
        if src is None:
            raise KeyError(source_id)
        return src

    def save_source(self, source: Source) -> None:
        self._write_json(self.sources_dir / f"{source.id}.json", source.as_dict())

    # ---- сырьё

    def raw_key(self, source: Source, url: str) -> str:
        return hashlib.sha256(f"{source.id}|{url}".encode("utf-8")).hexdigest()

    def read_raw(self, digest: str) -> dict | None:
        record = self._read_json(self.raw_dir / f"{digest}.json")
        return record if isinstance(record, dict) else None

    def raw_is_fresh(self, record: dict) -> bool:
        """TTL управляет ПОВТОРНЫМ ИСПОЛЬЗОВАНИЕМ кэша при сборе, а не сроком
        жизни доказательства: протухшее сырьё остаётся на диске, потому что на
        него ссылаются старые наблюдения. Удаляет его только право на удаление."""
        expires = _parse_iso(record.get("expires_at"))
        return expires is not None and utcnow() < expires

    def write_raw(self, source: Source, subject: str, url: str, result: FetchResult, *,
                  transport: str) -> str:
        digest = self.raw_key(source, url)
        fetched_at = utcnow()
        record = {"hash": digest, "source_id": source.id, "subject": subject, "url": url,
                  "status": result.status, "fetched_at": fetched_at.isoformat(),
                  "expires_at": (fetched_at
                                 + timedelta(seconds=source.cache_ttl_seconds)).isoformat(),
                  "ttl_seconds": source.cache_ttl_seconds, "transport": transport,
                  "body_sha256": hashlib.sha256(result.body.encode("utf-8")).hexdigest(),
                  "body": result.body}
        self._write_json(self.raw_dir / f"{digest}.json", record)
        return digest

    # ---- наблюдения и индекс

    def next_collected_at(self) -> datetime:
        """Один акт сбора — одно `collected_at`, и оно строго растёт.

        Строгий рост нужен не для красоты: на быстрой машине два сбора подряд
        попадают в один такт часов, и история наблюдений перестала бы быть
        упорядоченной — а история и есть ценность (раздел 3 п.3).
        """
        now = utcnow()
        if self._last_collected is not None and now <= self._last_collected:
            now = self._last_collected + timedelta(microseconds=1)
        self._last_collected = now
        return now

    def index(self) -> dict:
        data = self._read_json(self.index_path)
        return data if isinstance(data, dict) else {"subjects": {}}

    def save_observations(self, subject: str, observations: list[Observation],
                          raw_refs: list[str]) -> None:
        """Каждое наблюдение — новый файл. Перезаписи существующего не бывает:
        повторный сбор того же факта — новая запись, а не правка старой."""
        key = subject_key(subject)
        for obs in observations:
            self._write_json(self.obs_dir / key / f"{obs.id}.json", obs.as_dict())
        idx = self.index()
        entry = idx.setdefault("subjects", {}).setdefault(
            key, {"subject": subject, "observations": [], "raw": []})
        entry["subject"] = subject
        entry["observations"] = sorted({*entry.get("observations", []),
                                        *(o.id for o in observations)})
        entry["raw"] = sorted({*entry.get("raw", []), *raw_refs})
        entry["updated_at"] = utcnow().isoformat()
        self._write_json(self.index_path, idx)

    def observations(self, subject: str) -> list[dict]:
        directory = self.obs_dir / subject_key(subject)
        if not directory.exists():
            return []
        rows = [self._read_json(p) for p in sorted(directory.glob("*.json"))]
        rows = [r for r in rows if isinstance(r, dict)]
        rows.sort(key=lambda r: str(r.get("collected_at") or ""), reverse=True)
        return rows

    def counts(self) -> dict:
        subjects = self.index().get("subjects", {}) if self.index_path.exists() else {}
        total = 0
        if self.obs_dir.exists():
            total = sum(1 for _ in self.obs_dir.glob("*/*.json"))
        return {"observations": total, "subjects": len(subjects),
                "raw_records": sum(1 for _ in self.raw_dir.glob("*.json"))
                if self.raw_dir.exists() else 0}

    def delete_subject(self, subject: str) -> dict:
        """Право на удаление: сырьё, производные и след в индексе — одной
        операцией. Сырьё чистится и по индексу, и сплошным проходом по файлам:
        если индекс разошёлся с диском, останки не должны пережить удаление.
        """
        key = subject_key(subject)
        directory = self.obs_dir / key
        removed_obs = 0
        if directory.exists():
            removed_obs = sum(1 for _ in directory.glob("*.json"))
            shutil.rmtree(directory)
        idx = self.index()
        entry = idx.get("subjects", {}).pop(key, None)
        removed_raw = 0
        wanted = set(entry.get("raw", [])) if isinstance(entry, dict) else set()
        if self.raw_dir.exists():
            for path in list(self.raw_dir.glob("*.json")):
                record = self._read_json(path)
                belongs = isinstance(record, dict) and record.get("subject") == subject
                if belongs or path.stem in wanted:
                    path.unlink(missing_ok=True)
                    removed_raw += 1
        if self.index_path.exists():
            self._write_json(self.index_path, idx)
        return {"subject": subject, "subject_key": key, "observations_deleted": removed_obs,
                "raw_deleted": removed_raw, "index_entry_removed": entry is not None}

    # ---- лимит запросов

    def rate_allows(self, source: Source) -> bool:
        now = time.monotonic()
        hits = [t for t in self._hits.get(source.id, []) if now - t < 60.0]
        if len(hits) >= source.rate_limit_per_min:
            self._hits[source.id] = hits
            return False
        hits.append(now)
        self._hits[source.id] = hits
        return True

    # ---- robots.txt (категория C)

    async def robots_allows(self, source: Source, url: str) -> tuple[bool, str]:
        """Разрешает ли `robots.txt` этот адрес. Fail-closed: недоступный или
        нечитаемый robots.txt — это ОТКАЗ, а не «раз не запрещено, значит можно».

        Запрос robots.txt не расходует объявленный лимит: это не сбор данных, а
        выяснение, есть ли вообще разрешение собирать. Иначе источник с лимитом
        в один запрос нельзя было бы обойти вежливо ни разу.
        """
        parts = urlparse(url)
        robots_url = checked_url(f"{parts.scheme}://{parts.netloc}/robots.txt")
        try:
            result = await self.adapter.fetch(robots_url, headers={"User-Agent": USER_AGENT})
        except Exception as exc:               # noqa: BLE001 — любая беда = отказ
            return False, f"robots.txt недоступен ({exc.__class__.__name__})"
        if result.status != 200:
            return False, f"robots.txt недоступен (HTTP {result.status})"
        parser = RobotFileParser()
        parser.parse(result.body.splitlines())
        if not parser.can_fetch(USER_AGENT, url):
            return False, "robots.txt запрещает обход этого адреса"
        return True, "robots.txt разрешает"


def store(svc) -> OsirisStore:
    """Хранилище на процесс. Создание НЕ трогает диск: при выключенном флаге
    ручки обязаны отвечать, не создавая ни одного файла."""
    existing = getattr(svc, "osiris", None)
    if isinstance(existing, OsirisStore):
        return existing
    created = OsirisStore(Path(svc.settings.data_dir) / DIRNAME, bus=getattr(svc, "bus", None))
    svc.osiris = created
    return created


async def _emit(svc, kind: str, **data: Any) -> None:
    bus = getattr(svc, "bus", None)
    if bus is not None:
        await bus.emit(kind, **data)


# ------------------------------------------------------------------- сбор


async def collect(svc, source_id: str, subject: str, *, force_refresh: bool = False) -> dict:
    """Собрать наблюдения по источнику и субъекту. Любой отказ — исключение.

    Порядок проверок — от самого дешёвого и самого принципиального к сети:
    субъект → источник → адрес через egress → robots.txt (категория C) →
    кэш → лимит запросов → адаптер. До сети доходит только то, что разрешено.
    """
    st = store(svc)
    subject = (subject or "").strip()
    if not subject or len(subject) > MAX_SUBJECT or "/" in subject:
        raise OsirisError(f"subject обязателен, без «/», не длиннее {MAX_SUBJECT} символов")
    try:
        source = st.get_source(source_id)
    except KeyError as exc:
        raise SourceUnknownError(f"источник {source_id!r} не зарегистрирован") from exc
    if source.category == "D":
        raise OsirisError("категория D не собирается сетью: эти данные приносит владелец")

    url = checked_url(source.url_for(subject))
    if source.category == "C":
        allowed, why = await st.robots_allows(source, url)
        if not allowed:
            await _emit(svc, "osiris.collect_refused", source_id=source.id, reason=why)
            raise RobotsDisallowError(why)

    digest = st.raw_key(source, url)
    cached = None if force_refresh else st.read_raw(digest)
    from_cache = bool(cached and st.raw_is_fresh(cached))
    if from_cache:
        body, status, transport = cached["body"], cached["status"], cached.get("transport", "")
        fetched_at = _parse_iso(cached.get("fetched_at")) or utcnow()
        live_call = False
    else:
        if not st.rate_allows(source):
            raise RateLimitedError(f"лимит {source.rate_limit_per_min} запросов в минуту "
                                   f"для источника {source.id} исчерпан")
        live_call = bool(getattr(st.adapter, "live", False))
        transport = "live" if live_call else "stub"
        try:
            result = await st.adapter.fetch(url, headers={"User-Agent": USER_AGENT})
        except PluginSecurityError:
            # Отказ egress — это не «источник сломался», а «нам туда нельзя».
            # Пропускаем наверх как есть, чтобы причина не растворилась в 502.
            raise
        except Exception as exc:               # noqa: BLE001 — источник ответил бедой
            _mark_live(st, source, ok=False, error=f"{exc.__class__.__name__}: {exc}",
                       live_call=live_call)
            await _emit(svc, "osiris.collect_failed", source_id=source.id,
                        error=exc.__class__.__name__)
            raise SourceUnavailableError(
                f"источник недоступен: {exc.__class__.__name__}") from exc
        status, body = result.status, result.body
        if status != 200:
            _mark_live(st, source, ok=False, error=f"HTTP {status}", live_call=live_call)
            await _emit(svc, "osiris.collect_failed", source_id=source.id, status=status)
            raise SourceUnavailableError(f"источник ответил HTTP {status}")
        digest = st.write_raw(source, subject, url, result, transport=transport)
        fetched_at = utcnow()

    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise OsirisError(f"ответ источника не разбирается как JSON: {exc}") from exc

    collected_at = st.next_collected_at()
    observations = PARSERS[source.parser](
        source, subject, payload, url=url, raw_ref=f"raw:{digest}",
        collected_at=collected_at, fetched_at=fetched_at)
    if not observations:
        raise OsirisError("источник не отдал ни одного заявленного поля для этого субъекта")
    st.save_observations(subject, observations, [digest])
    if not from_cache:
        _mark_live(st, source, ok=True, error="", live_call=live_call)
    await _emit(svc, "osiris.collected", source_id=source.id, subject=subject,
                observations=len(observations), from_cache=from_cache, transport=transport)
    return {"source_id": source.id, "subject": subject, "from_cache": from_cache,
            "transport": transport, "raw_ref": f"raw:{digest}",
            "observations": [o.as_dict() for o in observations]}


def _mark_live(st: OsirisStore, source: Source, *, ok: bool, error: str, live_call: bool) -> None:
    """Пометить источник проверенным может ТОЛЬКО настоящий сетевой вызов.

    Подменённый в тесте адаптер (`live = False`) не меняет статус вовсе: иначе
    зелёный стенд означал бы «источник работает», хотя наружу никто не ходил.
    """
    if not live_call:
        return
    source.live_status = "live_ok" if ok else "live_failed"
    source.live_checked_at = utcnow().isoformat()
    source.live_error = "" if ok else error
    st.save_source(source)


# ------------------------------------------------------------------ ручки


def _require_enabled() -> None:
    if not enabled():
        raise HTTPException(409, {"message": f"OSIRIS выключен: {FLAG} не установлен",
                                  "flag": FLAG})


@router.get("/osiris")
async def state(request: Request):
    """Состояние слоя: флаг, реестр, сколько наблюдений и субъектов."""
    svc = request.app.state.svc
    st = store(svc)
    counts = st.counts()
    return {"enabled": enabled(), "flag": FLAG,
            "sources": [s.as_dict() for s in st.sources().values()],
            "observations": counts["observations"], "subjects": counts["subjects"],
            "raw_records": counts["raw_records"], "methods": list(METHODS),
            "forbidden_checks": [{"code": c, "why": w} for c, _p, w in FORBIDDEN_PATTERNS]}


@router.get("/osiris/sources")
async def list_sources(request: Request):
    """Реестр целиком — то, чем отвечают на вопрос «откуда у тебя это»."""
    svc = request.app.state.svc
    return {"enabled": enabled(),
            "sources": [s.as_dict() for s in store(svc).sources().values()]}


@router.post("/osiris/sources")
async def register_source(request: Request, body: dict):
    """Зарегистрировать источник. Флаг обязателен, декларация проходит запреты."""
    _require_enabled()
    svc = request.app.state.svc
    try:
        source = normalize_source(body)
    except ForbiddenSourceError as exc:
        await _emit(svc, "osiris.source_rejected", source_id=str(body.get("id") or ""),
                    code=exc.code)
        raise HTTPException(400, {"message": str(exc), "code": exc.code}) from exc
    except PluginSecurityError as exc:
        await _emit(svc, "osiris.source_rejected", source_id=str(body.get("id") or ""),
                    code="egress_blocked")
        raise HTTPException(400, {"message": f"адрес источника отклонён проверкой egress: {exc}",
                                  "code": "egress_blocked"}) from exc
    except OsirisError as exc:
        raise HTTPException(exc.http_status, {"message": str(exc), "code": exc.code}) from exc
    st = store(svc)
    st.save_source(source)
    await _emit(svc, "osiris.source_registered", source_id=source.id, category=source.category)
    return {"ok": True, "source": source.as_dict()}


@router.post("/osiris/collect")
async def collect_endpoint(request: Request, body: dict):
    """Собрать по источнику и субъекту. Флаг обязателен."""
    _require_enabled()
    svc = request.app.state.svc
    try:
        return await collect(svc, str(body.get("source_id") or ""),
                             str(body.get("subject") or ""),
                             force_refresh=bool(body.get("force_refresh")))
    except PluginSecurityError as exc:
        # Отказ egress отдаём отдельно: это не «источник плохой», а адрес, до
        # которого нам ходить нельзя, — и причина должна читаться в ответе.
        raise HTTPException(403, {"message": f"адрес отклонён проверкой egress: {exc}",
                                  "code": "egress_blocked"}) from exc
    except OsirisError as exc:
        raise HTTPException(exc.http_status, {"message": str(exc), "code": exc.code}) from exc


@router.get("/osiris/observations")
async def list_observations(request: Request, subject: str = Query(..., min_length=1)):
    """Наблюдения по субъекту — каждое с полным паспортом."""
    svc = request.app.state.svc
    rows = store(svc).observations(subject)
    return {"enabled": enabled(), "subject": subject, "count": len(rows), "observations": rows}


@router.delete("/osiris/subjects/{subject_id}")
async def delete_subject(subject_id: str, request: Request):
    """Право на удаление: сырьё, производные и след в индексе — одной операцией."""
    _require_enabled()
    svc = request.app.state.svc
    report = store(svc).delete_subject(subject_id)
    await _emit(svc, "osiris.subject_deleted", subject=subject_id,
                observations=report["observations_deleted"], raw=report["raw_deleted"])
    return {"ok": True, **report}


FEATURE = Feature(name="osiris", router=router)
