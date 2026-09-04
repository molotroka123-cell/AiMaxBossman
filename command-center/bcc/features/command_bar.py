"""Командная строка владельца и фоновые задачи под ней, флаг OFF.

Две связанные вещи, которых системе не хватало.

1. КОМАНДНАЯ СТРОКА. Возможностей у Command Center больше двух сотен, и все
   они разложены по страницам. Одно поле ввода на главной избавляет от поиска
   нужной страницы: владелец пишет команду, видит РАЗОБРАННОЕ намерение
   («какая возможность, с какими параметрами, обратимо ли») и только потом
   подтверждает выполнение.

2. ФОНОВЫЕ ЗАДАЧИ. Долгая работа не должна держать владельца на странице:
   команда уходит в фоновую задачу, её состояние живёт на СЕРВЕРЕ, поэтому
   уход со страницы и возврат на неё ничего не теряют.

Почему сделано именно так:

  * каталог возможностей собирается из `app.routes` — из настоящих маршрутов
    приложения. Рукописный список устаревает молча: возможность переименовали,
    а строка предлагает её дальше и падает при вызове. Здесь предложить то,
    чего нет, невозможно по построению;

  * разбор ввода ДЕТЕРМИНИРОВАННЫЙ: точное имя, псевдоним, однозначный
    префикс. Ни одного вызова модели — ни платного, ни бесплатного. Модель в
    роли разборщика превращает опечатку в чужое действие; здесь непонятый ввод
    честно называется непонятым, а рядом кладутся ближайшие варианты
    (difflib, stdlib) — но НЕ выполняется;

  * предпросмотр не пишется второй раз: для действий, которые умеет
    `action_preview`, намерение показывает его настоящий Preview (какие строки
    каких таблиц изменятся). Обратимость для остальных берётся из каталога
    `second_opinion.classify` — неизвестный вид действия там считается
    НЕОБРАТИМЫМ, и это ровно то поведение, которое нужно гейту: незнакомое не
    равно безопасному;

  * необратимое действие требует ОТДЕЛЬНОГО подтверждения владельца
    (`confirm=true`). Без него /command-bar/run не выполняет ничего и отвечает
    412 — это доказано тестом, а не заявлено;

  * значения параметров могут быть секретами (ключ, пароль, приватный путь),
    поэтому НИЧЕГО из введённого не возвращается и не уходит в события шины
    как есть. Наружу выходит только то, что сервер и так знал: ссылка на
    строку (целое id), булево значение, слово из закрытого списка и
    подстановка из псевдонима. Всё остальное — отпечаток
    (длина + sha256:8), по которому владелец узнаёт своё значение, а
    посторонний не восстановит его;

  * своей таблицы нет (схему меняет только ведущий): список фоновых задач
    живёт в памяти сервиса и дублируется одним JSON-файлом в
    `settings.data_dir`, поэтому переживает перезапуск процесса.

Выполнение идёт внутренним ASGI-запросом к тому же приложению с учётными
данными владельца, которые он предъявил на /command-bar/run. Так команда
исполняется ровно тем же кодом и с теми же проверками, что и нажатие кнопки на
странице, — второй реализации действия не появляется.
"""
from __future__ import annotations

import asyncio
import contextlib
import difflib
import hashlib
import json
import os
import re
import shlex
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from typing import get_args
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..db import utcnow
from ..sessions import CSRF_HEADER
from . import Feature, action_preview, second_opinion

FLAG = "BOSSMAN_COMMAND_BAR_ENABLED"
router = APIRouter()

#: Куда кладём список фоновых задач. Один файл, не таблица: схему БД меняет
#: ведущий, а список обязан пережить перезапуск и без миграции.
STORE_DIRNAME = "command_bar"
STORE_FILENAME = "tasks.json"

#: Сколько задач держим. Список для человека, а не архив: старые завершённые
#: вытесняются, идущие — никогда.
MAX_TASKS = 200
#: Ответ исполнения обрезается: в списке задач не место мегабайтам.
MAX_RESULT_CHARS = 4000
#: Разобранное намерение живёт недолго: подтверждение относится к тому
#: состоянию системы, которое владелец видел, а не к вчерашнему.
INTENT_TTL_SECONDS = 900.0
MAX_INTENTS = 100
#: Фоновая задача может работать долго — на то она и фоновая.
RUN_TIMEOUT_SECONDS = 600.0

#: Состояния фоновой задачи. Закрытый список: «пропала» состоянием не является.
STATES = ("queued", "running", "done", "failed", "stopped")

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def enabled() -> bool:
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes")


# ======================================================================
# 1. Каталог возможностей — из настоящих маршрутов приложения
# ======================================================================

@dataclass(frozen=True)
class Capability:
    """Одна возможность системы = один настоящий маршрут приложения."""

    id: str
    method: str
    path: str
    title: str
    group: str
    path_params: tuple[str, ...] = ()
    query_params: tuple[str, ...] = ()
    body_fields: tuple[str, ...] = ()
    mutates: bool = False
    runnable: bool = True
    blocked_reason: str = ""

    def as_dict(self) -> dict:
        return {"id": self.id, "method": self.method, "path": self.path, "title": self.title,
                "group": self.group, "path_params": list(self.path_params),
                "query_params": list(self.query_params), "body_fields": list(self.body_fields),
                "mutates": self.mutates, "runnable": self.runnable,
                "blocked_reason": self.blocked_reason,
                "confirm_required": confirm_required_static(self)}


def _walk_routes(routes: Iterable[Any]) -> Iterable[Any]:
    """Все конечные маршруты приложения, включая вложенные роутеры.

    FastAPI 0.14x не раскладывает `include_router` в плоский список: включённый
    роутер лежит одним объектом, а настоящие пути отдаёт своими «эффективными
    кандидатами». Обходим и то и другое — иначе каталог видел бы четыре
    служебных маршрута вместо двух с половиной сотен реальных.
    """
    for route in routes:
        path = getattr(route, "path", None)
        if isinstance(path, str) and getattr(route, "methods", None) \
                and getattr(route, "endpoint", None) is not None:
            yield route
        for attr in ("effective_candidates", "effective_low_priority_routes"):
            expand = getattr(route, attr, None)
            if callable(expand):
                with contextlib.suppress(Exception):
                    yield from _walk_routes(expand())


def _split_path(path: str) -> tuple[list[str], list[str]]:
    """Литеральные сегменты и имена параметров пути ({id:int} → id)."""
    literals: list[str] = []
    params: list[str] = []
    for segment in path.strip("/").split("/"):
        if not segment:
            continue
        if segment.startswith("{"):
            params.append(segment.strip("{}").split(":")[0])
        else:
            literals.append(segment)
    return literals, params


def _singular(token: str) -> str:
    return token.rstrip("s")


def _capability_id(literals: list[str], endpoint_name: str, method: str) -> str:
    """Имя возможности: путь + глагол, выведенный из имени обработчика.

    Имя обработчика повторяет путь («list_tasks» на /api/tasks), поэтому
    повторяющиеся слова выбрасываются, а остаётся именно действие: list,
    create, delete. Если не осталось ничего — глаголом становится HTTP-метод.
    Правило чистое: одинаковый маршрут всегда даёт одинаковое имя.
    """
    base = ".".join(literals)
    known = {_singular(t.lower()) for seg in literals for t in re.split(r"[-_]", seg) if t}
    rest = [t for t in re.split(r"[-_]", endpoint_name or "") if t
            and _singular(t.lower()) not in known]
    return f"{base}.{'_'.join(rest) or method.lower()}"


def _endpoint_title(route: Any, path: str) -> str:
    """Короткое человеческое описание — первая строка docstring обработчика."""
    doc = (getattr(getattr(route, "endpoint", None), "__doc__", "") or "").strip()
    first = doc.splitlines()[0].strip() if doc else ""
    return (first or path)[:160]


def _query_params(route: Any) -> tuple[str, ...]:
    names: list[str] = []
    with contextlib.suppress(Exception):
        dependant = getattr(route, "dependant", None)
        for field_ in (getattr(dependant, "query_params", None) or []):
            name = getattr(field_, "name", "")
            if name:
                names.append(str(name))
    return tuple(dict.fromkeys(names))


def _annotation_fields(annotation: Any) -> list[str]:
    """Имена полей модели тела запроса, включая случай `Model | None`."""
    candidates = [annotation, *[a for a in get_args(annotation) if a is not type(None)]]
    for item in candidates:
        fields = getattr(item, "model_fields", None)
        if fields:
            return [str(name) for name in fields]
    return []


def _body_fields(route: Any) -> tuple[str, ...]:
    """Поля тела запроса. Спрашиваем сам маршрут, а не свою память о нём:
    иначе командная строка предлагает параметр, которого у ручки уже нет."""
    names: list[str] = []
    with contextlib.suppress(Exception):
        body = getattr(route, "body_field", None)
        if body is not None:
            model = getattr(body, "type_", None)
            if model is None:
                model = getattr(getattr(body, "field_info", None), "annotation", None)
            names += _annotation_fields(model)
        if not names:
            # Тело, собранное из отдельных параметров (embed): полями служат
            # сами имена параметров обработчика.
            dependant = getattr(route, "dependant", None)
            for field_ in (getattr(dependant, "body_params", None) or []):
                name = str(getattr(field_, "name", "") or "")
                if name and name != "body":
                    names.append(name)
    return tuple(dict.fromkeys(names))


#: Маршруты, которые каталог показывает, но выполнять отказывается.
#: Молчаливое исключение из каталога было бы враньём про состав приложения,
#: поэтому они видны и снабжены причиной.
def _blocked_reason(group: str, path: str) -> str:
    if group == "command-bar":
        return "командная строка не запускает саму себя"
    if path in ("/api/login", "/api/logout"):
        return "вход и выход выполняются формой входа: там секрет, а не команда"
    return ""


def build_catalog(app: Any) -> dict[str, Capability]:
    """Каталог возможностей из app.routes. Ничего выдуманного здесь быть не может."""
    seen: dict[tuple[str, str], Any] = {}
    for route in _walk_routes(getattr(app, "routes", []) or []):
        for method in sorted(getattr(route, "methods", set()) or set()):
            if method in ("HEAD", "OPTIONS"):
                continue
            seen.setdefault((route.path, method), route)

    draft: list[tuple[str, str, Any, list[str], list[str]]] = []
    for (path, method), route in sorted(seen.items()):
        literals, params = _split_path(path)
        if literals[:1] != ["api"] or len(literals) < 2:
            continue                       # документация и статика — не возможности
        literals = literals[1:]
        draft.append((_capability_id(literals, getattr(route, "name", ""), method),
                      method, route, literals, params))

    # Столкновение имён разрешается добавлением параметра пути, а не случайным
    # номером: два маршрута с одинаковым именем — редкость, но имя обязано
    # оставаться воспроизводимым между запусками.
    counts: dict[str, int] = {}
    for cid, *_ in draft:
        counts[cid] = counts.get(cid, 0) + 1

    catalog: dict[str, Capability] = {}
    for cid, method, route, literals, params in draft:
        if counts[cid] > 1 and params:
            cid = f"{cid}.by_{params[-1]}"
        while cid in catalog:              # последний рубеж, чтобы не потерять маршрут
            cid = f"{cid}.{method.lower()}"
        group = literals[0]
        reason = _blocked_reason(group, route.path)
        catalog[cid] = Capability(
            id=cid, method=method, path=route.path,
            title=_endpoint_title(route, route.path), group=group,
            path_params=tuple(params), query_params=_query_params(route),
            body_fields=_body_fields(route), mutates=method not in SAFE_METHODS,
            runnable=not reason, blocked_reason=reason)
    return catalog


def catalog_for(app: Any) -> dict[str, Capability]:
    """Каталог с кэшем на приложении: маршруты за время жизни процесса не меняются,
    но число маршрутов проверяется — фича, добавленная позже, не потеряется."""
    cached = getattr(app.state, "command_bar_catalog", None)
    size = sum(1 for _ in _walk_routes(getattr(app, "routes", []) or []))
    if cached is not None and getattr(app.state, "command_bar_catalog_size", -1) == size:
        return cached
    catalog = build_catalog(app)
    app.state.command_bar_catalog = catalog
    app.state.command_bar_catalog_size = size
    return catalog


# ======================================================================
# 2. Псевдонимы, закрытые списки значений и обратимость
# ======================================================================

@dataclass(frozen=True)
class Alias:
    """Человеческое слово → возможность и заранее известные значения.

    Значения из псевдонима сервер задал сам, поэтому их можно показывать: они
    не пришли из ввода владельца.
    """
    target: str
    preset: dict[str, Any] = field(default_factory=dict)


ALIASES: dict[str, Alias] = {
    "задачи": Alias("tasks.list"),
    "задача": Alias("tasks.get"),
    "запустить": Alias("tasks.action", {"action": "run"}),
    "остановить": Alias("tasks.action", {"action": "stop"}),
    "пауза": Alias("tasks.action", {"action": "pause"}),
    "продолжить": Alias("tasks.action", {"action": "resume"}),
    "повторить": Alias("tasks.action", {"action": "retry"}),
    "агенты": Alias("agents.list"),
    "модели": Alias("models.list"),
    "провайдеры": Alias("providers.list"),
    "расписания": Alias("schedules.list"),
    "подтверждения": Alias("approvals.list"),
    "разрешить": Alias("approvals.decide", {"approve": True}),
    "отклонить": Alias("approvals.decide", {"approve": False}),
    "система": Alias("system.get"),
    "активность": Alias("activity.get"),
    "здоровье": Alias("system.get"),
    "поиск": Alias("search.search"),
}

#: Закрытые списки допустимых значений. Значение из такого списка сервер знает
#: и без владельца, поэтому его можно показать в намерении целиком.
ENUMS: dict[str, dict[str, tuple[str, ...]]] = {
    "tasks.action": {"action": ("run", "stop", "pause", "resume", "retry")},
}

#: Соответствие возможности виду действия из second_opinion. Нужно только там,
#: где метод HTTP врёт: POST /api/tasks создаёт задачу, а её можно остановить и
#: удалить — это обратимое действие, и оно описано в каталоге second_opinion.
SECOND_OPINION_KIND: dict[str, str] = {
    "tasks.create": "task.create",
    "schedules.create": "task.create",
}


def _second_opinion_kind(cap: Capability) -> str:
    explicit = SECOND_OPINION_KIND.get(cap.id)
    if explicit:
        return explicit
    if not cap.mutates:
        return "db.select"
    if cap.method == "DELETE":
        return "db.delete_rows"
    # Намеренно НЕ описанный вид: second_opinion.classify считает незнакомое
    # необратимым, и это правильный ответ для произвольного POST/PATCH.
    return f"http.{cap.method.lower()}"


def confirm_required_static(cap: Capability) -> bool:
    """Нужно ли подтверждение, если о параметрах ещё ничего не известно.

    Каталог отвечает осторожно: точный ответ даёт разбор конкретного ввода,
    где подключается предпросмотр действия.
    """
    return cap.mutates and not second_opinion.classify(_second_opinion_kind(cap)).reversible


#: Возможность + значения → вид действия, у которого УЖЕ есть предпросмотр в
#: action_preview. Второй предпросмотр здесь не пишется намеренно.
def _preview_target(cap: Capability, args: dict[str, Any]) -> tuple[str, Any, dict] | None:
    if cap.id == "tasks.action" and str(args.get("action") or "") == "stop":
        return "task.stop", args.get("task_id"), {}
    if cap.id == "approvals.decide" and "approve" in args:
        return "approval.decide", args.get("approval_id"), {"approve": bool(args["approve"]),
                                                            "by": "command-bar"}
    if cap.id == "agents.delete":
        return "agent.delete", args.get("agent_id"), {}
    if cap.id == "schedules.delete":
        return "schedule.delete", args.get("schedule_id"), {}
    return None


# ======================================================================
# 3. Значения: наружу выходит только то, что сервер знал и без владельца
# ======================================================================

def fingerprint(value: Any) -> str:
    """Отпечаток значения вместо значения.

    Владелец узнаёт своё («12 симв.» и хвост хэша сходятся), посторонний не
    восстановит: sha256 необратим, а длину и восемь знаков хэша подобрать по
    короткому секрету всё равно нельзя без самого секрета.
    """
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False,
                                                           sort_keys=True, default=str)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return f"{len(text)} симв. · sha256:{digest}"


def _is_reference(cap: Capability, name: str, value: Any) -> bool:
    """Ссылка на строку — целое в параметре пути с именем id/*_id.

    Такое значение не содержимое, а адрес строки: показать его безопасно и
    необходимо — иначе владелец не увидит, ЧТО именно он останавливает.
    """
    return (name in cap.path_params and isinstance(value, int) and not isinstance(value, bool)
            and (name == "id" or name.endswith("_id")))


def describe_argument(cap: Capability, name: str, value: Any, source: str) -> dict:
    """Как параметр выглядит в ответе. Свободный текст сюда не попадает."""
    where = ("path" if name in cap.path_params
             else "query" if name in cap.query_params
             else "body" if name in cap.body_fields else "unknown")
    allowed = ENUMS.get(cap.id, {}).get(name, ())
    shown: Any = None
    kind = "fingerprint"
    if source == "alias":
        shown, kind = value, "preset"          # значение задал сервер, не владелец
    elif isinstance(value, bool):
        shown, kind = value, "boolean"
    elif _is_reference(cap, name, value):
        shown, kind = value, "reference"
    elif allowed and isinstance(value, str) and value in allowed:
        shown, kind = value, "enum"
    return {"name": name, "where": where, "source": source, "kind": kind,
            "value": shown, "shown": kind != "fingerprint", "fingerprint": fingerprint(value)}


def _redact_known_secrets(text: str, svc: Any) -> str:
    """Вторая линия для ответа исполнения: точные секреты процесса вычищаются.

    Первая линия — просто не класть в ответ ввод владельца; здесь снимается
    риск того, что секрет вернёт САМО приложение.
    """
    secrets = set()
    with contextlib.suppress(Exception):
        token = getattr(getattr(svc, "auth", None), "token", "")
        if isinstance(token, str) and len(token) >= 6:
            secrets.add(token)
    for value in secrets:
        text = text.replace(value, "[секрет вырезан]")
    return text


# ======================================================================
# 4. Разбор ввода — детерминированный, без единого вызова модели
# ======================================================================

#: «1» и «0» в эти списки НЕ входят намеренно: чаще всего это идентификатор
#: строки, и превращение «agents.delete 1» в булево значение — тихая порча
#: команды владельца. Число остаётся числом.
BOOLEAN_TRUE = {"true", "yes", "да", "on"}
BOOLEAN_FALSE = {"false", "no", "нет", "off"}


def _coerce(raw: str) -> Any:
    """Строка ввода → значение параметра. Число раньше булева: см. выше."""
    text = raw.strip()
    if re.fullmatch(r"-?\d{1,15}", text):
        return int(text)
    low = text.lower()
    if low in BOOLEAN_TRUE:
        return True
    if low in BOOLEAN_FALSE:
        return False
    return raw


def _tokenize(text: str) -> list[str]:
    """Разбор строки на слова с уважением к кавычкам (значение может быть с пробелами)."""
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


@dataclass
class Match:
    """Чем именно опознана команда. `how` попадает в ответ: владелец должен
    видеть, было ли это точное имя или догадка по префиксу."""
    cap_id: str
    how: str                       # exact | alias | prefix | alias_prefix
    preset: dict[str, Any] = field(default_factory=dict)
    alias: str = ""


def match_command(word: str, catalog: dict[str, Capability]) -> tuple[Match | None, list[str]]:
    """Опознание команды: точное имя → псевдоним → однозначный префикс.

    Неоднозначность и непонимание не выполняют ничего: возвращается список
    вариантов. Похожее действие вместо названного — худший из возможных
    ответов командной строки.
    """
    word = (word or "").strip().lower()
    if not word:
        return None, []
    if word in catalog:
        return Match(word, "exact"), []
    alias = ALIASES.get(word)
    if alias is not None and alias.target in catalog:
        return Match(alias.target, "alias", dict(alias.preset), word), []

    prefixed = sorted(cid for cid in catalog if cid.startswith(word))
    alias_prefixed = sorted(name for name, a in ALIASES.items()
                            if name.startswith(word) and a.target in catalog)
    if len(prefixed) == 1 and not alias_prefixed:
        return Match(prefixed[0], "prefix"), []
    if len(alias_prefixed) == 1 and not prefixed:
        a = ALIASES[alias_prefixed[0]]
        return Match(a.target, "alias_prefix", dict(a.preset), alias_prefixed[0]), []
    if prefixed or alias_prefixed:
        return None, (prefixed + alias_prefixed)[:10]

    pool = list(catalog) + [name for name, a in ALIASES.items() if a.target in catalog]
    close = difflib.get_close_matches(word, pool, n=6, cutoff=0.5)
    # Пусто быть не должно: «не понял» без единого варианта оставляет владельца
    # ни с чем. Тогда показываем человеческие псевдонимы — с них и начинают.
    return None, close or sorted(name for name, a in ALIASES.items() if a.target in catalog)[:8]


@dataclass
class Parsed:
    """Результат разбора. `args` — сырые значения, они НИКОГДА не уходят наружу."""
    understood: bool
    message: str = ""
    suggestions: list[str] = field(default_factory=list)
    cap: Capability | None = None
    match: Match | None = None
    args: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)


def parse_text(text: str, catalog: dict[str, Capability]) -> Parsed:
    tokens = _tokenize(text or "")
    if not tokens:
        return Parsed(False, "пустая команда", [])
    match, suggestions = match_command(tokens[0], catalog)
    if match is None:
        return Parsed(False, f"не понял команду «{tokens[0][:60]}»", suggestions)

    cap = catalog[match.cap_id]
    args: dict[str, Any] = dict(match.preset)
    sources: dict[str, str] = {k: "alias" for k in match.preset}
    positional: list[str] = []
    known = set(cap.path_params) | set(cap.query_params) | set(cap.body_fields)

    for token in tokens[1:]:
        if "=" in token and not token.startswith("="):
            name, _, raw = token.partition("=")
            name = name.strip()
            if name not in known:
                return Parsed(False, f"неизвестный параметр «{name[:40]}» "
                                     f"для возможности {cap.id}",
                              sorted(known)[:10], cap=cap, match=match)
            args[name] = _coerce(raw)
            sources[name] = "input"
        else:
            positional.append(token)

    # Позиционные значения занимают незаполненные параметры пути по порядку:
    # «остановить 12» должно работать, а гадать, какому полю тела принадлежит
    # свободное слово, командная строка не имеет права.
    free = [p for p in cap.path_params if p not in args]
    for name, raw in zip(free, positional):
        args[name] = _coerce(raw)
        sources[name] = "input"
    extra = positional[len(free):]
    if extra:
        return Parsed(False, f"лишние значения ({len(extra)}): передавайте их как имя=значение",
                      sorted(known)[:10], cap=cap, match=match)

    missing = [p for p in cap.path_params if p not in args]
    return Parsed(True, "", [], cap=cap, match=match, args=args, sources=sources, missing=missing)


# ======================================================================
# 5. Намерение: что именно будет сделано
# ======================================================================

async def build_intent(svc: Any, parsed: Parsed) -> dict:
    """Намерение для показа владельцу. Ничего не выполняет и ничего не меняет."""
    cap = parsed.cap
    assert cap is not None
    arguments = [describe_argument(cap, name, value, parsed.sources.get(name, "input"))
                 for name, value in sorted(parsed.args.items())]

    verdict = second_opinion.classify(_second_opinion_kind(cap))
    reversible, why, source = verdict.reversible, verdict.why, "second_opinion"
    preview: dict | None = None

    target = _preview_target(cap, parsed.args)
    if target is not None and target[1] is not None:
        action, target_id, params = target
        # Предпросмотр не пишется второй раз: у action_preview он уже есть и по
        # построению только читает БД. Его собственный флаг гасит его РУЧКУ,
        # а не эту функцию — читать состояние можно всегда.
        try:
            built = await action_preview.build(svc, action, int(target_id), params)
        except action_preview.PreviewError as exc:
            preview = {"available": False, "action": action, "reason": exc.message}
            built = None
        except Exception as exc:           # noqa: BLE001 — предпросмотр не обязан удаться
            preview = {"available": False, "action": action,
                       "reason": f"предпросмотр не построен: {type(exc).__name__}"}
            built = None
        if built is not None:
            preview = built.as_dict()
            reversible, why, source = built.reversible, built.reversible_note, "action_preview"

    runnable = cap.runnable and not parsed.missing
    blocked = cap.blocked_reason or (
        f"не хватает параметров: {', '.join(parsed.missing)}" if parsed.missing else "")
    return {
        "capability": cap.as_dict(),
        "match": {"how": parsed.match.how if parsed.match else "",
                  "alias": parsed.match.alias if parsed.match else ""},
        "arguments": arguments,
        "missing": list(parsed.missing),
        "reversible": bool(reversible),
        "reversible_why": why,
        "reversible_source": source,
        "requires_confirmation": bool(cap.mutates and not reversible),
        "preview": preview,
        "runnable": runnable,
        "blocked_reason": blocked,
        "summary": _summary(cap, arguments, reversible),
    }


def _summary(cap: Capability, arguments: list[dict], reversible: bool) -> str:
    """Одна строка «что будет сделано». Свободного текста владельца здесь нет."""
    shown = ", ".join(f"{a['name']}={a['value']}" for a in arguments if a["shown"])
    hidden = [a["name"] for a in arguments if not a["shown"]]
    parts = [f"{cap.title}", f"{cap.method} {cap.path}"]
    if shown:
        parts.append(f"параметры: {shown}")
    if hidden:
        parts.append(f"скрытые значения: {', '.join(hidden)}")
    parts.append("обратимо" if reversible else "НЕОБРАТИМО — нужно подтверждение")
    return " · ".join(parts)


# ======================================================================
# 6. Фоновые задачи: состояние на сервере, файл рядом с ним
# ======================================================================

def _now() -> str:
    return utcnow().isoformat()


class TaskStore:
    """Список фоновых задач: память сервиса + один JSON-файл в data_dir.

    Файл нужен ровно для одного свойства: список переживает перезапуск
    процесса. Задача, застигнутая перезапуском в работе, не притворяется живой
    и не исчезает — она помечается failed с честной причиной.
    """

    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / STORE_DIRNAME / STORE_FILENAME
        self.tasks: dict[str, dict] = {}
        self._running: dict[str, asyncio.Task] = {}
        self._stop_requested: set[str] = set()

    # ---------- хранение ----------

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except Exception:                  # noqa: BLE001 — битый файл не мешает работе
            return
        for item in (raw.get("tasks") or []) if isinstance(raw, dict) else []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            task = dict(item)
            if task.get("state") in ("queued", "running"):
                task["state"] = "failed"
                task["error"] = "прервана перезапуском процесса"
                task["finished_at"] = task.get("finished_at") or _now()
            self.tasks[str(task["id"])] = task

    def save(self) -> None:
        """Запись атомарная: половина файла хуже отсутствующего файла.

        Отказ записи не должен ронять команду владельца — список останется
        в памяти и переживёт всё, кроме перезапуска.
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps({"tasks": list(self.tasks.values())},
                                 ensure_ascii=False, default=str)
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".tasks-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(payload)
                os.replace(tmp, self.path)
            finally:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(tmp)
        except Exception:                  # noqa: BLE001
            return

    def _evict(self) -> None:
        finished = [t for t in self.tasks.values() if t["state"] in ("done", "failed", "stopped")]
        while len(self.tasks) > MAX_TASKS and finished:
            oldest = min(finished, key=lambda t: t.get("created_at") or "")
            finished.remove(oldest)
            self.tasks.pop(oldest["id"], None)

    # ---------- жизненный цикл ----------

    def create(self, *, capability: str, title: str, arguments: list[dict],
               confirmed: bool) -> dict:
        task = {"id": uuid.uuid4().hex[:12], "capability": capability, "title": title,
                "state": "queued", "created_at": _now(), "started_at": None,
                "finished_at": None, "result": None, "error": "", "confirmed": bool(confirmed),
                "arguments": arguments}
        self.tasks[task["id"]] = task
        self._evict()
        self.save()
        return task

    def mark(self, task_id: str, state: str, **fields: Any) -> dict | None:
        task = self.tasks.get(task_id)
        if task is None:
            return None
        task["state"] = state
        task.update(fields)
        self.save()
        return task

    def spawn(self, svc: Any, task_id: str, factory: Any) -> None:
        """Запускает работу в фоне. Отказ одной задачи не касается ни соседних,
        ни приложения: исключение остаётся здесь и становится состоянием failed."""
        async def _runner() -> None:
            self.mark(task_id, "running", started_at=_now())
            try:
                result = await factory()
            except asyncio.CancelledError:
                by_owner = task_id in self._stop_requested
                self.mark(task_id, "stopped", finished_at=_now(),
                          error="остановлена владельцем" if by_owner
                                else "прервана остановкой процесса")
                raise
            except Exception as exc:       # noqa: BLE001 — падение задачи не падение системы
                self.mark(task_id, "failed", finished_at=_now(),
                          error=f"{type(exc).__name__}: {exc}"[:400])
                return
            ok = bool(result.get("ok"))
            self.mark(task_id, "done" if ok else "failed", finished_at=_now(),
                      result=result, error="" if ok else str(result.get("error") or "")[:400])

        task = asyncio.create_task(_runner(), name=f"bcc-cmdbar-{task_id}")
        self._running[task_id] = task
        task.add_done_callback(lambda _t: self._running.pop(task_id, None))
        # Остановка сервиса обязана дотянуться до фоновой работы, иначе она
        # доживает до закрытия пула БД и шумит. Свои завершённые ручки при этом
        # подчищаем, чтобы список Services не рос от каждой команды.
        holder = getattr(svc, "_tasks", None)
        if isinstance(holder, list):
            holder[:] = [t for t in holder
                         if not (t.done() and (t.get_name() or "").startswith("bcc-cmdbar-"))]
            holder.append(task)

    async def stop(self, task_id: str) -> dict | None:
        """Остановка по требованию владельца. Остановленная задача не исчезает —
        она получает состояние stopped и время окончания."""
        task = self.tasks.get(task_id)
        if task is None:
            return None
        if task["state"] in ("done", "failed", "stopped"):
            return task
        self._stop_requested.add(task_id)
        running = self._running.get(task_id)
        if running is not None and not running.done():
            running.cancel()
            # asyncio.wait, а не await задачи: отменённая задача бросила бы
            # CancelledError в обработчик запроса владельца — а он не отменён.
            with contextlib.suppress(Exception):
                await asyncio.wait({running}, timeout=5.0)
        if self.tasks[task_id]["state"] not in ("done", "failed", "stopped"):
            self.mark(task_id, "stopped", finished_at=_now(), error="остановлена владельцем")
        return self.tasks[task_id]

    def listing(self, limit: int = 50) -> list[dict]:
        rows = sorted(self.tasks.values(), key=lambda t: t.get("created_at") or "", reverse=True)
        return rows[:max(1, min(limit, MAX_TASKS))]


def store_for(svc: Any) -> TaskStore:
    """Хранилище задач сервиса. Создаётся лениво: флаг могут включить и после
    старта процесса, а список задач при этом обязан подняться из файла."""
    store = getattr(svc, "command_bar_tasks", None)
    if store is None:
        store = TaskStore(svc.settings.data_dir)
        store.load()
        svc.command_bar_tasks = store
    return store


# ======================================================================
# 7. Исполнение: тот же код, что и у кнопки на странице
# ======================================================================

def _auth_headers(request: Request) -> dict[str, str]:
    """Учётные данные владельца из его же запроса.

    Команда исполняется от имени того, кто её отдал, — своей «системной»
    учётной записи у командной строки нет и быть не должно.
    """
    headers: dict[str, str] = {}
    for name in ("x-bcc-token", CSRF_HEADER.lower(), "cookie"):
        value = request.headers.get(name)
        if value:
            headers[name] = value
    return headers


def _fill_path(cap: Capability, args: dict[str, Any]) -> str:
    """Подстановка значений в шаблон пути с экранированием: значение параметра
    не имеет права стать лишним сегментом чужого маршрута."""
    path = cap.path
    for name in cap.path_params:
        value = quote(str(args.get(name, "")), safe="")
        path = re.sub(r"\{" + re.escape(name) + r"(:[^}]+)?\}", value, path)
    return path


async def execute(app: Any, svc: Any, cap: Capability, args: dict[str, Any],
                  headers: dict[str, str]) -> dict:
    """Внутренний ASGI-запрос к тому же приложению. Второй реализации действия нет."""
    query = {k: v for k, v in args.items() if k in cap.query_params}
    body = {k: v for k, v in args.items() if k in cap.body_fields}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://command-bar.internal",
                                 timeout=httpx.Timeout(RUN_TIMEOUT_SECONDS),
                                 headers=headers) as client:
        response = await client.request(cap.method, _fill_path(cap, args),
                                        params=query or None,
                                        json=(body or {}) if cap.mutates else None)
    text = _redact_known_secrets(response.text or "", svc)
    truncated = len(text) > MAX_RESULT_CHARS
    payload: Any
    try:
        payload = json.loads(text) if not truncated and text else None
    except ValueError:
        payload = None
    return {"ok": response.status_code < 400, "status": response.status_code,
            "body": payload, "text": None if payload is not None else text[:MAX_RESULT_CHARS],
            "truncated": truncated,
            "error": "" if response.status_code < 400 else f"HTTP {response.status_code}"}


# ======================================================================
# 8. Разобранные намерения (билеты) — сырые значения не покидают сервер
# ======================================================================

class IntentCache:
    """Разобранные намерения по идентификатору.

    Хранится ЗДЕСЬ, а не у клиента: иначе сырые значения параметров (а в них
    бывают секреты) пришлось бы отдать в браузер и принять обратно.
    """

    def __init__(self) -> None:
        self.items: dict[str, tuple[float, Parsed]] = {}

    def put(self, parsed: Parsed) -> str:
        intent_id = uuid.uuid4().hex[:12]
        now = time.monotonic()
        self.items[intent_id] = (now, parsed)
        self._sweep(now)
        return intent_id

    def get(self, intent_id: str) -> Parsed | None:
        entry = self.items.get(intent_id)
        if entry is None:
            return None
        born, parsed = entry
        if time.monotonic() - born > INTENT_TTL_SECONDS:
            self.items.pop(intent_id, None)
            return None
        return parsed

    def _sweep(self, now: float) -> None:
        for key in [k for k, (born, _) in self.items.items() if now - born > INTENT_TTL_SECONDS]:
            self.items.pop(key, None)
        while len(self.items) > MAX_INTENTS:
            self.items.pop(next(iter(self.items)), None)


def intents_for(svc: Any) -> IntentCache:
    cache = getattr(svc, "command_bar_intents", None)
    if cache is None:
        cache = IntentCache()
        svc.command_bar_intents = cache
    return cache


# ======================================================================
# 9. Ручки
# ======================================================================

class ParseIn(BaseModel):
    text: str = ""


class RunIn(BaseModel):
    intent_id: str = ""
    text: str = ""
    confirm: bool = False


def _require_enabled() -> None:
    """Меняющая состояние ручка при выключенном флаге отказывает, а не работает тихо."""
    if not enabled():
        raise HTTPException(409, {"message": "командная строка выключена",
                                  "hint": f"включается переменной окружения {FLAG}=1"})


@router.get("/command-bar")
async def catalog(request: Request, limit: int = 500):
    """Каталог возможностей и состояние командной строки."""
    if not enabled():
        return {"enabled": False, "capabilities": [], "aliases": {}, "tasks": []}
    svc = request.app.state.svc
    caps = catalog_for(request.app)
    store = store_for(svc)
    return {
        "enabled": True,
        "count": len(caps),
        "capabilities": [c.as_dict() for c in sorted(caps.values(), key=lambda c: c.id)][:limit],
        "aliases": {name: a.target for name, a in sorted(ALIASES.items())
                    if a.target in caps},
        "groups": sorted({c.group for c in caps.values()}),
        "states": list(STATES),
        "tasks": store.listing(20),
    }


@router.post("/command-bar/parse")
async def parse(body: ParseIn, request: Request):
    """Разобрать ввод и показать намерение. НИЧЕГО не выполняет и не меняет.

    Единственное, что запоминается, — сам разбор (чтобы сырые значения не
    ездили через браузер). Состояние приложения при этом не меняется, и это
    проверяется снимком до/после.
    """
    _require_enabled()
    svc = request.app.state.svc
    caps = catalog_for(request.app)
    parsed = parse_text(body.text, caps)
    if not parsed.understood:
        return {"enabled": True, "understood": False, "message": parsed.message,
                "suggestions": parsed.suggestions, "intent": None, "intent_id": None,
                "executed": False}
    intent = await build_intent(svc, parsed)
    intent_id = intents_for(svc).put(parsed)
    return {"enabled": True, "understood": True, "message": "", "suggestions": [],
            "intent_id": intent_id, "intent": intent, "executed": False}


@router.post("/command-bar/run")
async def run_command(body: RunIn, request: Request):
    """Выполнить намерение фоновой задачей. Необратимое — только с подтверждением."""
    _require_enabled()
    svc = request.app.state.svc
    caps = catalog_for(request.app)

    parsed: Parsed | None = None
    if body.intent_id:
        parsed = intents_for(svc).get(body.intent_id)
        if parsed is None:
            raise HTTPException(404, {"message": "намерение не найдено или устарело",
                                      "hint": "разберите команду заново"})
    elif body.text:
        parsed = parse_text(body.text, caps)
    if parsed is None:
        raise HTTPException(400, {"message": "нечего выполнять",
                                  "hint": "передайте intent_id из /command-bar/parse"})
    if not parsed.understood:
        raise HTTPException(400, {"message": parsed.message,
                                  "hint": "уточните команду: " + ", ".join(parsed.suggestions[:5])
                                          if parsed.suggestions else "команда не опознана"})

    cap = parsed.cap
    assert cap is not None
    intent = await build_intent(svc, parsed)
    if not intent["runnable"]:
        raise HTTPException(400, {"message": intent["blocked_reason"] or "выполнение невозможно",
                                  "hint": cap.blocked_reason or ""})
    if intent["requires_confirmation"] and not body.confirm:
        # Отдельный шаг, а не галочка в том же запросе «по умолчанию true»:
        # подтверждение обязано быть решением, а не значением по умолчанию.
        raise HTTPException(412, {"message": "необратимое действие требует подтверждения "
                                             "владельца",
                                  "hint": "повторите с confirm=true, если согласны"})

    store = store_for(svc)
    task = store.create(capability=cap.id, title=cap.title, arguments=intent["arguments"],
                        confirmed=bool(body.confirm))
    app, args, headers = request.app, dict(parsed.args), _auth_headers(request)
    store.spawn(svc, task["id"], lambda: execute(app, svc, cap, args, headers))
    with contextlib.suppress(Exception):
        # В событие уходит только имя возможности: ввод владельца в журнал не
        # попадает даже в виде отпечатка.
        await svc.bus.emit("command_bar.started", task_id=task["id"], capability=cap.id,
                           confirmed=bool(body.confirm))
    return {"enabled": True, "executed": True, "task": task, "intent": intent}


@router.get("/command-bar/tasks")
async def list_tasks(request: Request, limit: int = 50):
    """Идущие и завершённые фоновые задачи. Состояние живёт на сервере, поэтому
    уход со страницы и возврат на неё ничего не теряют."""
    if not enabled():
        return {"enabled": False, "tasks": []}
    store = store_for(request.app.state.svc)
    rows = store.listing(limit)
    return {"enabled": True, "tasks": rows, "states": list(STATES),
            "active": sum(1 for t in rows if t["state"] in ("queued", "running"))}


@router.get("/command-bar/tasks/{task_id}")
async def get_task(task_id: str, request: Request):
    """Одна задача целиком: состояние, время, результат или причина отказа."""
    if not enabled():
        return {"enabled": False, "task": None}
    task = store_for(request.app.state.svc).tasks.get(task_id)
    if task is None:
        raise HTTPException(404, {"message": "задача не найдена"})
    return {"enabled": True, "task": task}


@router.post("/command-bar/tasks/{task_id}/stop")
async def stop_task(task_id: str, request: Request):
    """Остановить задачу. Остановленная получает состояние stopped, а не исчезает."""
    _require_enabled()
    store = store_for(request.app.state.svc)
    task = await store.stop(task_id)
    if task is None:
        raise HTTPException(404, {"message": "задача не найдена"})
    return {"enabled": True, "task": task}


# ------------------------------------------------------------------ подключение

async def _setup(svc: Any) -> None:
    """При включённом флаге поднимаем список задач из файла сразу на старте:
    владелец должен увидеть вчерашние задачи, не дожидаясь первого запроса."""
    if not enabled():
        return
    store_for(svc)


FEATURE = Feature(name="command_bar", router=router, setup=_setup)
