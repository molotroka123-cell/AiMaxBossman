"""`cloud.ask` — локальный агент (Gemma на телефоне) советуется с СИЛЬНОЙ облачной моделью по просьбе владельца.

Владелец 21.10: «Запрещено читать локальные переписки данные и фото»; контекст чата остаётся внутри этого чата. Поэтому
инструмент отправляет в облако ТОЛЬКО явный текст аргумента `prompt` - ни историю беседы, ни вложения, ни память, ни пути
к фото/видео. Каждый вызов - отдельное одобрение владельца (ASK, пол политики): в одобрении видны модель и провайдер, точный
текст после удаления секретов, число знаков, оценка стоимости и остаток бюджета.

Что именно сделано, и почему так:

  * ASK всегда. `effect_hook` возвращает ask или deny (никогда None), `hook_is_floor=True` - правило владельца и выданное право
    его не снимают. Права `permission` у инструмента нет сознательно: ни одно из существующих не про облачный вывод, а
    выдаваемое право было бы лишь способом ослабить ASK.
  * Маршрут выбирается один раз, ДО одобрения (`bind_args`), и записывается в настоящие аргументы (`_cloud`): одобряется
    конкретная модель и конкретный текст (sha256 редактированного текста), исполняется ровно они. Обработчик повторно
    проверяет STOP, бюджет и готовность модели (`context_deny` работает и до одобрения, и перед исполнением).
  * Окно предпросмотра. Движок режет аргументы в одобрении на 2000 знаках (engine._execute_tool_calls). Текст в одобрении
    обязан быть виден ЦЕЛИКОМ, поэтому слишком длинный текст - deny с просьбой сократить, а не молчаливое усечение.
  * Модель, цена и бюджет - из каталога роутера мобильного агента (`build_model_router_catalog`, облачные исполнители
    `WORKERS`). Вызов идёт через тот же управляемый адаптер, что и `Registry.adapter_for` (Fable-потолок, режим «только
    бесплатное», длинный прогон, STOP, egress), а не напрямую. Транспорт подменяем (`transport`): тесты сети не касаются.
  * Расход пишется в тот же учёт, что и у роутера (`AgentSessionRuntime.spent_usd`), а в журнал аудита - только метаданные:
    время, модель, число знаков туда/обратно, стоимость, id одобрения. Ни текста вопроса, ни ответа.
  * Ответ - внешние данные (`external_output=True`) с первой строкой-происхождением «☁️ <модель> (облако, по запросу
    владельца)»: локальная модель не может выдать его за свой.
"""
from __future__ import annotations

import asyncio
import contextlib
import functools
import hashlib
import json
import math
import re
import time
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from .. import conversation_context
from ..db import task_runs as runs_t
from ..tools import REGISTRY, ToolContext, ToolResult, ToolSpec
from ..trace import current_trace_id
from . import Feature
from .agent_opencode import (DEFAULT_SESSION_BUDGET_USD, _orch, _session_detail, build_model_router_catalog,
                             redact_text)
from .chat_threads import _thread_of_task
from .coding_tasks import WORKERS, _worker_key

TOOL_NAME = "cloud.ask"
PROVENANCE = "☁️ {model} (облако, по запросу владельца)"

MAX_PROMPT_CHARS = 6000          # предел аргумента; практически меньше - см. PREVIEW_WINDOW
MAX_REASON_CHARS = 160
MAX_ANSWER_TOKENS = 1200         # потолок ответа: и для оценки худшего случая, и для провайдера
MAX_ANSWER_CHARS = 8000
CALL_TIMEOUT_S = 90.0
#: Движок показывает владельцу аргументы вызова первыми 2000 знаками (engine._execute_tool_calls).
PREVIEW_WINDOW = 2000

#: Подсказка о силе при равном ярусе роутера: от сильной к слабой. Это оценка, а не бенчмарк; явный `model` её перекрывает.
STRENGTH_HINT = ("claude-sonnet-4", "haiku-5.5", "nemotron-ultra-free", "qwen3-coder-paid", "deepseek-v4.1-flash",
                 "groq-gpt-oss-120b-free", "gemini-3-flash-free", "nvidia-nim", "openrouter-free", "minimax-m31-free",
                 "glm-flash", "nemotron-35-free", "poolside-laguna-free", "openrouter-code-free", "groq-qwen38-free")

_MEDIA_EXT = (r"(?:jpe?g|png|gif|webp|bmp|heic|heif|tiff?|dng|cr2|nef|arw|mp4|m4v|mov|mkv|avi|webm|wmv|flv|3gp|mts)")
_MEDIA_TOKEN = re.compile(r"(?i)[^\s\"'<>|]+\." + _MEDIA_EXT + r"\b")
_MEDIA_DATA_URI = re.compile(r"(?i)data:(?:image|video)/")
_LOCAL_DATA_PATH = re.compile(r"(?i)(?:chat[\\/]+attachments|terminal[\\/]+sessions)[\\/]")
_ATTACHMENT_BLOCK = re.compile(r"Вложение «[^»\n]{1,200}»")
_CHAT_PAIR = re.compile(r"(?m)^Владелец: .*\nBossman: ")
_PRICE_RE = re.compile(r"\$\s*([0-9]*\.?[0-9]+)\s*/\s*\$?\s*([0-9]*\.?[0-9]+)")


def _markers() -> tuple[str, ...]:
    """Метки контекста чата: заголовок беседы, «Новое сообщение», резюме /compact, память задачи."""
    from ..provider_governance import MEMORY_CONTEXT_MARKER
    from ..terminal_cli.chat import SUMMARY_LABEL
    return tuple(m.strip() for m in (conversation_context.HEADER, conversation_context.MARKER, SUMMARY_LABEL,
                                     MEMORY_CONTEXT_MARKER) if m.strip())


def _est_tokens(text: str) -> int:
    from .agent_extras import estimate_tokens           # лениво: agent_extras сам импортирует этот модуль
    return estimate_tokens(text)


class _State:
    """Services для `bind_args` (у хука нет ctx) и замок аудита. Выставляется в setup(svc)."""

    def __init__(self) -> None:
        self.svc: Any = None
        self.audit_lock = asyncio.Lock()


_STATE = _State()


def set_services(svc: Any) -> None:
    _STATE.svc = svc


#: Транспорт облачного вызова. Тесты подменяют его, продакшен - `default_transport`.
transport: Any = None


# ----------------------------------------------------------------------------------------------------------------- текст

def outgoing_text(args: dict) -> str:
    """Ровно то, что уйдёт из ПК: явный `prompt` после удаления секретов тем же редактором, что и события агента."""
    from ..plugin_security import redact_text as _scrub
    return _scrub(redact_text(str((args or {}).get("prompt") or ""))).strip()


@functools.lru_cache(maxsize=1)
def _header_reserve() -> int:
    """Сколько знаков окна предпросмотра занимают поля вокруг текста (худший случай по длине значений)."""
    worst = {"model": "x" * 90, "provider": "x" * 40, "paid": False, "chars_out": 6000, "est_cost_usd": 0.123456,
             "budget_remaining_usd": 100.123456, "reason": "x" * MAX_REASON_CHARS, "prompt": ""}
    return len(json.dumps(worst, ensure_ascii=False, indent=1))


def static_denial(args: dict) -> str | None:
    """Отказ по одним лишь аргументам (без сервисов): пустой текст, утечка контекста, классифицированные документы/Telegram-экспорты, фото/видео, RunPod, не влезает в окно."""
    from ..security_data_governance import DataClassification, classify_text

    if not isinstance(args, dict):
        return "CLOUD_ASK_EMPTY: аргументы должны быть объектом"
    raw = args.get("prompt")
    if not isinstance(raw, str) or not raw.strip():
        return "CLOUD_ASK_EMPTY: пустой текст - нечего отправлять"
    if len(raw) > MAX_PROMPT_CHARS:
        return f"CLOUD_ASK_TOO_LONG: текст длиннее {MAX_PROMPT_CHARS} знаков"
    if "runpod" in str(args.get("model") or "").lower():
        return "CLOUD_ASK_RUNPOD: RunPod и платные GPU-инстансы запрещены без явного подтверждения владельца"
    cls = classify_text(raw)
    if cls in (DataClassification.RESTRICTED_TELEGRAM_EXPORT, DataClassification.RESTRICTED_DOCUMENT):
        if not bool(args.get("owner_cloud_export_approved")):
            return (
                f"CLOUD_ASK_CLASSIFIED_EXPORT ({cls.value}): отправка Telegram-экспортов и классифицированных "
                "документов в облако запрещена без отдельного подтверждения владельца (owner_cloud_export_approved)"
            )
    if (any(m in raw for m in _markers()) or _ATTACHMENT_BLOCK.search(raw) or _CHAT_PAIR.search(raw)
            or _LOCAL_DATA_PATH.search(raw)):
        return ("CLOUD_ASK_CONTEXT_LEAK: в тексте контекст чата, вложение или путь к данным Bossman - историю беседы в облако "
                "не отправляем; задайте вопрос своими словами, без вставки переписки")
    if _MEDIA_TOKEN.search(raw) or _MEDIA_DATA_URI.search(raw):
        return "CLOUD_ASK_MEDIA_PATH: в тексте путь или данные фото/видео - личные фото и видео в облако не уходят"
    shown = len(json.dumps(outgoing_text(args), ensure_ascii=False))
    room = PREVIEW_WINDOW - _header_reserve()
    if shown > room:
        return (f"CLOUD_ASK_PREVIEW_TOO_LONG: текст не помещается в окно одобрения целиком ({shown} > {room} знаков с "
                "экранированием); владелец должен видеть весь уходящий текст - сократите вопрос")
    return None



def _effect(args: dict):
    reason = static_denial(args)
    if reason:
        return ("deny", reason)
    return ("ask", "текст уйдёт в облако: владелец одобряет каждый вызов")


# ------------------------------------------------------------------------------------------- STOP, бюджет, журнал

def stop_reason(svc: Any) -> str:
    """'' или причина: глобальный STOP владельца (живое состояние Computer Use + STOP-файл) или STOP автономии."""
    from ..autonomy import stop as autonomy_stop
    from .telegram_calls import global_stop_active
    data_dir = Path(svc.settings.data_dir)
    why = autonomy_stop.stop_reason(data_dir / "autonomy", data_dir)
    if why:
        return why
    return "owner STOP: глобальный STOP владельца активен" if global_stop_active(svc, fail_closed=True) else ""


def _audit_path(svc: Any) -> Path:
    return Path(svc.settings.data_dir) / "cloud_ask" / "audit.jsonl"


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _read_audit(svc: Any) -> list[dict]:
    """Read the complete audit; corrupt or unreadable data must not reset spend."""
    out = []
    try:
        with _audit_path(svc).open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict) or not isinstance(row.get("ts"), str):
                    raise ValueError("invalid audit row")
                raw_cost = row.get("cost_usd")
                if isinstance(raw_cost, bool) or raw_cost is None:
                    raise ValueError("missing audit cost")
                cost = float(raw_cost)
                if not math.isfinite(cost) or cost < 0:
                    raise ValueError("invalid audit cost")
                out.append(row)
    except FileNotFoundError:
        return []
    return out


def _append_audit(svc: Any, row: dict) -> None:
    path = _audit_path(svc)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


async def audit(svc: Any, **row: Any) -> dict:
    """Одна строка журнала. Намеренно без полей текста: только метаданные вызова."""
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **row}
    async with _STATE.audit_lock:
        await asyncio.to_thread(_append_audit, svc, record)
    return record


async def _session_of_task(svc: Any, task_id: int | None) -> str | None:
    if not isinstance(task_id, int) or isinstance(task_id, bool):
        return None
    known = _orch(svc).task_to_session.get(task_id)
    if known:
        return known
    return await asyncio.to_thread(_thread_of_task, svc, task_id, exclude="")


async def budget_state(svc: Any, task_id: int | None) -> dict:
    """Бюджет, из которого платит вызов: сессия агента (учёт роутера) или, вне сессии, сутки по журналу cloud.ask."""
    try:
        rows = await asyncio.to_thread(_read_audit, svc)
    except (OSError, UnicodeError, ValueError, TypeError, OverflowError):
        return {"scope": "day", "session_id": None, "budget_usd": DEFAULT_SESSION_BUDGET_USD,
                "spent_usd": DEFAULT_SESSION_BUDGET_USD, "remaining_usd": 0.0,
                "billing_unknown": True, "audit_unavailable": True}
    today_rows = [r for r in rows if str(r.get("ts", ""))[:10] == _today()]
    if any(r.get("status") == "billing_unknown" for r in today_rows):
        return {"scope": "day", "session_id": None, "budget_usd": DEFAULT_SESSION_BUDGET_USD,
                "spent_usd": DEFAULT_SESSION_BUDGET_USD, "remaining_usd": 0.0,
                "billing_unknown": True}
    session_id = await _session_of_task(svc, task_id)
    if session_id:
        try:
            detail = await _session_detail(svc, session_id)
            return {"scope": "session", "session_id": session_id, "budget_usd": float(detail["budget_usd"]),
                    "spent_usd": float(detail["spent_usd"]), "remaining_usd": float(detail["budget_remaining_usd"])}
        except Exception:  # noqa: BLE001 - сессия пропала или не читается: считаем по суткам, не по воздуху
            pass
    spent = sum(float(r.get("cost_usd") or 0.0) for r in today_rows)
    return {"scope": "day", "session_id": None, "budget_usd": DEFAULT_SESSION_BUDGET_USD, "spent_usd": round(spent, 6),
            "remaining_usd": round(max(0.0, DEFAULT_SESSION_BUDGET_USD - spent), 6)}


async def _task_id_from_trace(svc: Any) -> int | None:
    """Задача текущего run'а: движок ставит trace `run-<id>` на время исполнения (bcc.trace); у bind_args нет ctx."""
    trace = current_trace_id.get() or ""
    match = re.fullmatch(r"run-(\d+)", trace)
    if not match:
        return None
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(runs_t.c.task_id).where(runs_t.c.id == int(match.group(1))))).first()
    return int(row[0]) if row else None


# ----------------------------------------------------------------------------------------------------------- каталог

def _worker_prices(worker_id: str, spec: dict, tier_row: dict | None) -> tuple[float, float] | None:
    if tier_row is not None:
        return float(tier_row["price_in_1m"]), float(tier_row["price_out_1m"])
    if spec.get("tier") == "free":
        return 0.0, 0.0
    match = _PRICE_RE.search(str(spec.get("price_display") or ""))
    return (float(match.group(1)), float(match.group(2))) if match else None


async def cloud_catalog(svc: Any) -> list[dict]:
    """Облачные исполнители каталога роутера, сильные первыми: ярус роутера, затем STRENGTH_HINT."""
    catalog = await build_model_router_catalog(svc)
    tiers = {t["worker"]: t for t in catalog.get("tiers", []) if t.get("worker")}
    out: list[dict] = []
    for item in catalog.get("workers", []):
        wid = str(item["id"])
        spec = WORKERS.get(wid) or {}
        prices = _worker_prices(wid, spec, tiers.get(wid))
        paid = bool(spec.get("tier") == "paid" or (prices and max(prices) > 0))
        tier = int(tiers[wid]["tier"]) if wid in tiers else (4 if paid else 3)
        out.append({"id": wid, "tier_id": tiers[wid]["id"] if wid in tiers else f"worker:{wid}", "tier": tier,
                    "model": str(item["model"]), "label": str(item["label"]), "provider": str(item["provider"]),
                    "endpoint": str(spec.get("endpoint") or ""), "paid": paid, "ready": bool(item["ready"]),
                    "price_in_1m": prices[0] if prices else None, "price_out_1m": prices[1] if prices else None})
    hint = {wid: i for i, wid in enumerate(STRENGTH_HINT)}
    out.sort(key=lambda c: (-c["tier"], hint.get(c["id"], len(hint)), c["id"]))
    return out


def _governance_rows(cand: dict) -> tuple[dict, dict]:
    """Строки provider/model для политик governance - как у Registry, но из таблицы WORKERS."""
    from urllib.parse import urlsplit
    host = (urlsplit(cand["endpoint"]).hostname or "").lower()
    caps = {"free_tier": True} if (not cand["paid"] and not host.endswith("openrouter.ai")) else {}
    provider = {"id": 0, "name": cand["provider"], "kind": "openai_compat", "base_url": cand["endpoint"]}
    model = {"id": 0, "name": cand["model"], "alias": cand["id"], "kind": "cloud", "caps": caps,
             "price_in": cand["price_in_1m"], "price_out": cand["price_out_1m"],
             "pricing_known": cand["price_in_1m"] is not None and cand["price_out_1m"] is not None}
    return provider, model


def _governance_refusal(cand: dict) -> str:
    from ..provider_governance import free_only_refusal
    provider, model = _governance_rows(cand)
    return free_only_refusal(provider, model)


def estimate_cost(cand: dict, tokens_in: int, tokens_out: int = MAX_ANSWER_TOKENS) -> float | None:
    if cand["price_in_1m"] is None or cand["price_out_1m"] is None:
        return None
    return round((tokens_in * cand["price_in_1m"] + tokens_out * cand["price_out_1m"]) / 1e6, 6)


def _match(cands: list[dict], wanted: str) -> dict | None:
    key = wanted.strip().lower()
    for c in cands:
        if key in (c["id"].lower(), c["tier_id"].lower(), c["model"].lower(), c["label"].lower()):
            return c
    return None


def choose(cands: list[dict], *, requested: str | None, tokens_in: int, remaining_usd: float) -> tuple[dict | None, str | None]:
    """(кандидат, отказ). Явная модель - только она; иначе самая сильная ГОТОВАЯ, разрешённая политикой и по бюджету."""
    def verdict(c: dict) -> tuple[str, str]:
        """(код отказа или '', подробность)."""
        if "runpod" in (c["id"] + c["model"] + c["endpoint"]).lower():
            return "CLOUD_ASK_RUNPOD", "RunPod запрещён без явного подтверждения владельца"
        if not c["ready"]:
            return "CLOUD_ASK_NO_MODEL", "нет ключа исполнителя"
        cost = estimate_cost(c, tokens_in)
        if cost is None:
            return "CLOUD_ASK_NO_MODEL", "цена модели неизвестна"          # как governance: без цены не отправляем
        refusal = _governance_refusal(c)
        if refusal:
            return "CLOUD_ASK_NO_MODEL", refusal
        if c["paid"] and (remaining_usd <= 0 or cost > remaining_usd):
            return "CLOUD_ASK_NO_BUDGET", f"оценка {cost} USD при остатке бюджета {remaining_usd} USD"
        return "", ""

    if requested:
        if "runpod" in requested.lower():
            return None, "CLOUD_ASK_RUNPOD: RunPod и платные GPU-инстансы запрещены без явного подтверждения владельца"
        c = _match(cands, requested)
        if c is None:
            names = ", ".join(x["id"] for x in cands if x["ready"]) or "нет готовых"
            return None, f"CLOUD_ASK_UNKNOWN_MODEL: модели «{requested[:60]}» нет в облачном каталоге; готовы: {names}"
        code, why = verdict(c)
        return (None, f"{code}: {c['id']}: {why}") if code else (c, None)
    refused: dict[str, int] = {}
    for c in cands:
        code, _why = verdict(c)
        if not code:
            return c, None
        refused[code] = refused.get(code, 0) + 1
    if refused.get("CLOUD_ASK_NO_BUDGET"):
        return None, ("CLOUD_ASK_NO_BUDGET: бюджет исчерпан или не покрывает платные модели, а бесплатной готовой "
                      f"облачной модели нет (остаток {remaining_usd} USD)")
    return None, "CLOUD_ASK_NO_MODEL: нет готовой облачной модели (проверьте ключи облачных исполнителей)"


async def resolve_route(svc: Any, args: dict, *, task_id: int | None, bound: dict | None = None
                        ) -> tuple[dict | None, str | None]:
    """(маршрут для привязки, отказ). `bound` - уже одобренный маршрут: проверяется, а не выбирается заново."""
    stopped = stop_reason(svc)
    if stopped:
        return None, f"CLOUD_ASK_STOP: {stopped}"
    text = outgoing_text(args)
    tokens_in = _est_tokens(text) + 16
    budget = await budget_state(svc, task_id)
    cands = await cloud_catalog(svc)
    wanted = str(bound.get("worker")) if bound else (str(args.get("model") or "").strip() or None)
    cand, denied = choose(cands, requested=wanted, tokens_in=tokens_in, remaining_usd=budget["remaining_usd"])
    if cand is None:
        return None, denied
    if bound and bound.get("model") != cand["model"]:
        return None, "CLOUD_ASK_CHANGED: модель маршрута изменилась после одобрения - запросите одобрение заново"
    return {"v": 1, "worker": cand["id"], "model": cand["model"], "label": cand["label"], "provider": cand["provider"],
            "paid": cand["paid"], "price_in_1m": cand["price_in_1m"], "price_out_1m": cand["price_out_1m"],
            "est_cost_usd": estimate_cost(cand, tokens_in), "budget_scope": budget["scope"],
            "budget_remaining_usd": budget["remaining_usd"], "chars_out": len(text),
            "prompt_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}, None


# --------------------------------------------------------------------------------------- хуки движка (до и после ASK)

def normalize_args(args: dict) -> dict:
    """Канонические аргументы одобрения: что видит владелец и что входит в digest. Поля маршрута - первыми, текст - последним."""
    cloud = args.get("_cloud") if isinstance(args.get("_cloud"), dict) else None
    out: dict[str, Any] = {}
    if cloud:
        out.update(model=cloud.get("model"), provider=cloud.get("provider"), paid=bool(cloud.get("paid")),
                   chars_out=cloud.get("chars_out"), est_cost_usd=cloud.get("est_cost_usd"),
                   budget_remaining_usd=cloud.get("budget_remaining_usd"))
    else:
        out["route"] = "не привязан"
    reason = " ".join(str(args.get("reason") or "").split())[:MAX_REASON_CHARS]
    if reason:
        out["reason"] = reason
    out["prompt"] = outgoing_text(args)
    return out


async def bind_route(args: dict) -> str | None:
    """Выбрать маршрут ДО одобрения и записать его в настоящие аргументы; None - привязано, строка - отказ."""
    svc = _STATE.svc
    denied = static_denial(args)
    if denied:
        return denied
    args.pop("_cloud", None)                 # маршрут пишет только эта функция, не модель
    if svc is None:
        return "CLOUD_ASK_NO_MODEL: сервисы Bossman недоступны - маршрут не выбран"
    route, denied = await resolve_route(svc, args, task_id=await _task_id_from_trace(svc))
    if route is None:
        return denied
    args["_cloud"] = route
    if len(json.dumps(normalize_args(args), ensure_ascii=False, indent=1)) > PREVIEW_WINDOW:
        args.pop("_cloud", None)
        return ("CLOUD_ASK_PREVIEW_TOO_LONG: описание вызова вместе с текстом не помещается в окно одобрения - "
                "сократите вопрос")
    return None


async def _context_deny(args: dict, ctx: ToolContext) -> str | None:
    """Читающая проверка до ASK и перед исполнением: STOP, бюджет, готовность модели. Права не расширяет."""
    denied = static_denial(args)
    if denied:
        return denied
    bound = args.get("_cloud") if isinstance(args.get("_cloud"), dict) else None
    task_id = (ctx.task or {}).get("id") if isinstance(ctx.task, dict) else None
    _route, denied = await resolve_route(ctx.svc, args, task_id=task_id, bound=bound)
    return denied


# --------------------------------------------------------------------------------------------------------- транспорт

async def default_transport(svc: Any, route: dict, messages: list[dict], *, max_tokens: int, timeout: float) -> Any:
    """Реальный вызов: адаптер OpenAI-совместимого эндпоинта исполнителя под управляемой обёрткой Registry (потолки и политики)."""
    from ..pit.jeff_kb_cloud import governed_adapter
    from ..providers import build_adapter
    cand = next((c for c in await cloud_catalog(svc) if c["id"] == route["worker"]), None)
    if cand is None:
        raise RuntimeError("исполнитель пропал из каталога")
    key = await _worker_key(WORKERS[cand["id"]]["key"], svc)
    if not key:
        raise RuntimeError("нет ключа исполнителя")
    provider, model = _governance_rows(cand)
    adapter = governed_adapter(provider, model, build_adapter("openai_compat", cand["endpoint"], key))
    try:
        return await adapter.chat(cand["model"], messages, max_tokens=max_tokens, temperature=0.2, timeout=timeout)
    finally:
        close = getattr(adapter, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                await close()


def _actual_cost(route: dict, result: Any) -> float | None:
    """Known actual cost or None; an estimate is not evidence of a provider charge."""
    if not route.get("paid"):
        return 0.0
    from .. import long_run
    priced = {"price_in": route.get("price_in_1m"), "price_out": route.get("price_out_1m")}
    spent = long_run.actual_eur(priced, result)
    return round(spent, 6) if spent is not None and math.isfinite(spent) and spent >= 0 else None


# ------------------------------------------------------------------------------------------------------- обработчик

def _fail(code: str, text: str) -> ToolResult:
    return ToolResult(content=f"{code}: {text}", one_line=f"{TOOL_NAME}: {code}", error=True)


async def _t_ask(args: dict, ctx: ToolContext) -> ToolResult:
    svc = ctx.svc
    # Одобрение владельца - условие исполнения: вызов по аренде или без строки approvals сюда не допускается.
    if ctx.approval_id is None:
        return _fail("CLOUD_ASK_NO_APPROVAL", "облачный запрос выполняется только по одобрению владельца на этот вызов")
    denied = static_denial(args)
    if denied:
        return _fail(denied.split(":", 1)[0], denied.split(":", 1)[-1].strip())
    bound = args.get("_cloud") if isinstance(args.get("_cloud"), dict) else None
    if bound is None:
        return _fail("CLOUD_ASK_UNBOUND", "маршрут не был привязан к одобрению - запросите одобрение заново")
    text = outgoing_text(args)
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != bound.get("prompt_sha256"):
        return _fail("CLOUD_ASK_CHANGED", "текст изменился после одобрения - запросите одобрение заново")
    task_id = (ctx.task or {}).get("id") if isinstance(ctx.task, dict) else None
    route, denied = await resolve_route(svc, args, task_id=task_id, bound=bound)
    if route is None:
        return _fail(denied.split(":", 1)[0], denied.split(":", 1)[-1].strip())
    budget = await budget_state(svc, task_id)
    messages = [{"role": "user", "content": text}]
    call = transport or default_transport
    started = time.monotonic()
    base = {"model": bound["model"], "provider": bound["provider"], "worker": bound["worker"], "chars_out": len(text),
            "approval_id": ctx.approval_id, "task_id": task_id, "run_id": ctx.run_id, "scope": budget["scope"]}
    try:
        result = await asyncio.wait_for(call(svc, bound, messages, max_tokens=MAX_ANSWER_TOKENS, timeout=CALL_TIMEOUT_S),
                                        timeout=CALL_TIMEOUT_S + 5.0)
    except asyncio.CancelledError:
        if bound.get("paid"):
            await asyncio.shield(audit(svc, **base, chars_in=0, cost_usd=0.0, status="billing_unknown", error="CancelledError",
                                       ms=int((time.monotonic() - started) * 1000)))
        raise
    except Exception as exc:  # noqa: BLE001 - не включать текст исключения, он может содержать запрос
        await audit(svc, **base, chars_in=0, cost_usd=0.0, status="billing_unknown" if bound.get("paid") else "error", error=type(exc).__name__,
                    ms=int((time.monotonic() - started) * 1000))
        return _fail("CLOUD_ASK_FAILED", f"облачная модель не ответила ({type(exc).__name__}); повторите позже или "
                                          "сообщите владельцу")
    answer = str(getattr(result, "text", "") or "").strip()
    cost = _actual_cost(bound, result)
    await audit(svc, **base, chars_in=len(answer), cost_usd=cost if cost is not None else 0.0,
                status="billing_unknown" if cost is None else ("ok" if answer else "empty"),
                ms=int((time.monotonic() - started) * 1000))
    if cost and budget["scope"] == "session" and budget["session_id"]:
        rt = _orch(svc).runtime(budget["session_id"])
        rt.spent_usd = round(budget["spent_usd"] + cost, 6)           # учёт роутера: spent_usd сессии
        rt.emit("session.updated", spent_usd=rt.spent_usd, cloud_ask_cost_usd=cost)
    if not answer:
        return _fail("CLOUD_ASK_EMPTY_ANSWER", "облачная модель вернула пустой ответ")
    cut = len(answer) > MAX_ANSWER_CHARS
    head = PROVENANCE.format(model=bound["model"])
    return ToolResult(
        content=head + "\n" + answer[:MAX_ANSWER_CHARS], one_line=f"{TOOL_NAME}: {bound['model']}, {len(text)} знаков",
        truncated=cut, more="спросите короче или попросите краткий ответ" if cut else "", external=True,
        data={"model": bound["model"], "provider": bound["provider"], "chars_out": len(text), "chars_in": len(answer),
              "cost_usd": cost})


SPECS = [
    ToolSpec(
        name=TOOL_NAME,
        description=("Посоветоваться с СИЛЬНОЙ облачной моделью, но только когда владелец сам попросил (например: «спроси у "
                     "облака», «спроси сильную модель»). prompt - готовый самодостаточный вопрос своими словами: в облако "
                     "уходит ТОЛЬКО этот текст, без истории чата, файлов и фото; не вставляй в него переписку, пути к "
                     "фото/видео и секреты. Каждый вызов - отдельное одобрение владельца, он видит модель, текст и цену. "
                     "Ответ приходит с пометкой «☁️ модель (облако)» - передай её владельцу как ответ облака, не как свой."),
        handler=_t_ask,
        input_schema={
            "prompt": {"type": "string", "description": f"вопрос для облачной модели, до {MAX_PROMPT_CHARS} знаков"},
            "model": {"type": "string", "description": "необязательно: исполнитель из облачного каталога; по умолчанию "
                                                       "самый сильный готовый в пределах бюджета"},
            "reason": {"type": "string", "description": "почему нужен облачный совет (видит владелец; в облако не уходит)"},
        },
        required=["prompt"], category="send", permission="", source="cloud", default_effect="ask",
        timeout_seconds=CALL_TIMEOUT_S + 40.0, idempotent=False, external_output=True,
        effect_hook=_effect, normalize_args=normalize_args, context_deny=_context_deny, bind_args=bind_route),
]


async def _setup(svc) -> None:
    set_services(svc)
    for spec in SPECS:
        REGISTRY.register(spec)


FEATURE = Feature(name="tools_cloud", router=None, setup=_setup)
