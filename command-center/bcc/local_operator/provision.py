"""Pure planning: which installed Ollama models become registry models and which local agents the chat offers.

No I/O here — `bcc.features.local_operator` applies the plan. Rules:

* Only local chat models are provisioned: `:cloud` tags (prompt leaves the PC) and embedding-only models are
  skipped with a reason.
* Nothing the owner already has is changed. A model already registered on the same Ollama endpoint, or an agent
  with the same name, is reported (`exists`, or `drift` when the agent points at another model) and left alone.
* Per model two agents may exist, both local-only (no fallback model, no free pool):
    «Локально · <model>»       — plain chat, no tools;
    «Локально · <model> · ПК» — computer use (computer.observe / computer.act / computer.processes), only when
                                 Ollama reports `tools` for that model. Observation is granted
                                 (`computer.observe`), every desktop action stays an owner approval: the
                                 computer.act policy hook is a floor no grant lowers.
* «Bossman Harness» is created only when no agent of that name exists, on the largest tool-capable local model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import urlsplit

from .inventory import LocalModel

PROVIDER_NAME = "Ollama (локально)"
PROVIDER_KIND = "openai_compat"
CHAT_PREFIX = "Локально · "
PC_SUFFIX = " · ПК"
HARNESS_NAME = "Bossman Harness"
OPERATOR_TOOLS = ["computer.observe", "computer.act", "computer.processes"]
HARNESS_TOOLS = ["code.*", "guard.*", "computer.*", "terminal.*"]

CHAT_PROMPT = ("Ты — Bossman на локальной модели владельца. Отвечай по-русски, коротко и по делу. "
               "Всё работает на этом компьютере, данные никуда не уходят.")

OPERATOR_PROMPT = (
    "Ты — локальный оператор компьютера владельца (Bossman). Работаешь только через инструменты computer.*.\n"
    "Цикл: 1) computer.observe — посмотреть экран, запомнить generation, hwnd и pid окна; "
    "2) computer.act — ОДНО действие по свежему наблюдению, с полем expect (что должно стать видно); "
    "3) computer.observe — проверить результат. Не повторяй действие вслепую.\n"
    "Каждое действие на рабочем столе — отдельное одобрение владельца: жди решения, не обходи отказ.\n"
    "Никогда не вводи пароли, ключи, платёжные данные; не отправляй, не публикуй, не удаляй без явной просьбы.\n"
    "Говори «готово» только если последнее наблюдение подтвердило expect. Иначе честно скажи, что не подтверждено "
    "и почему. При STOP владельца — немедленно прекрати.")

HARNESS_PROMPT = (
    "Ты — Bossman Harness: локальный агент-исполнитель задач владельца (код, игры, рабочий стол).\n"
    "Порядок: план из 3–7 шагов → выполнение по шагу → проверка каждого шага инструментом (тест, чтение файла, "
    "наблюдение экрана) → итог.\n"
    "Правила: не утверждай успех без проверки; пиши PASS / FAIL / NOT_RUN по каждому пункту; пустой результат — "
    "не успех; команды с последствиями (запись, запуск, действия на экране) идут через одобрение владельца; "
    "при STOP — остановись. В конце дай короткий отчёт: что сделано, чем проверено, что осталось.")


def _norm(url: str) -> tuple[str, str, int | None, str]:
    p = urlsplit(str(url or "").strip())
    host = (p.hostname or "").lower()
    if host == "localhost":
        host = "127.0.0.1"
    return (p.scheme.lower(), host, p.port, (p.path or "").rstrip("/") or "/v1")


def same_endpoint(a: str, b: str) -> bool:
    return bool(a and b) and _norm(a) == _norm(b)


def find_provider(providers: Iterable[dict], v1_url: str) -> dict | None:
    for p in providers:
        if same_endpoint(p.get("base_url") or "", v1_url):
            return p
    return None


@dataclass
class Plan:
    models_to_create: list[dict] = field(default_factory=list)
    model_report: list[dict] = field(default_factory=list)


def skip_reason(m: LocalModel) -> str:
    if m.cloud:
        return "облачный тег (:cloud) — запрос уходит с компьютера, в локальный чат не добавляется"
    if not m.chat:
        return "модель не умеет чат (Ollama: только embedding)"
    return ""


def unique_alias(wanted: str, taken: set[str]) -> str:
    alias, n = wanted, 2
    while alias.lower() in taken:
        alias = f"{wanted}-{n}"
        n += 1
    taken.add(alias.lower())
    return alias


def plan_models(local_models: Iterable[LocalModel], registered: Iterable[dict], provider_id: int | None) -> Plan:
    """Rows to insert into `models` (provider_id filled by the caller when the provider is new)."""
    registered = list(registered)
    taken = {str(r.get("alias") or "").lower() for r in registered}
    on_provider = {str(r.get("name")): r for r in registered
                   if provider_id is not None and r.get("provider_id") == provider_id}
    plan = Plan()
    for m in local_models:
        why = skip_reason(m)
        if why:
            plan.model_report.append({"name": m.name, "status": "skipped", "reason": why})
            continue
        row = on_provider.get(m.name)
        if row is not None:
            plan.model_report.append({"name": m.name, "status": "exists", "model_id": row.get("id")})
            continue
        plan.models_to_create.append({
            "name": m.name, "alias": unique_alias(f"ollama/{m.name}", taken), "kind": "local",
            "context_window": m.context_length or 8192, "caps": m.caps(),
            # local inference costs nothing per token: a known zero price, so budget gates do not treat it as unknown
            "price_in": 0.0, "price_out": 0.0})
        plan.model_report.append({"name": m.name, "status": "create"})
    return plan


def chat_agent(model: LocalModel, model_id: int) -> dict[str, Any]:
    return {"name": CHAT_PREFIX + model.name, "role": "Локальный чат", "system_prompt": CHAT_PROMPT,
            "model_id": model_id, "fallback_model_id": None, "tools": [], "max_steps": 4, "max_tokens": 4096,
            "budget_usd": 0.0, "permissions": {}, "enabled": True}


def computer_agent(model: LocalModel, model_id: int) -> dict[str, Any]:
    return {"name": CHAT_PREFIX + model.name + PC_SUFFIX, "role": "Локальный оператор ПК",
            "system_prompt": OPERATOR_PROMPT, "model_id": model_id, "fallback_model_id": None,
            "tools": list(OPERATOR_TOOLS), "max_steps": 30, "max_tokens": 4096, "budget_usd": 0.0,
            "permissions": {"computer.observe": True}, "enabled": True}


def harness_agent(model: LocalModel, model_id: int) -> dict[str, Any]:
    return {"name": HARNESS_NAME, "role": "Исполнитель задач: план → действие → проверка → отчёт",
            "system_prompt": HARNESS_PROMPT, "model_id": model_id, "fallback_model_id": None,
            "tools": list(HARNESS_TOOLS), "max_steps": 40, "max_tokens": 8192, "budget_usd": 0.0,
            "permissions": {"computer.observe": True, "filesystem.read": True}, "enabled": True}


def pick_harness_model(local_models: Iterable[LocalModel]) -> tuple[LocalModel | None, str]:
    """Largest tool-capable local chat model by Ollama's parameter count (unknown size ranks last)."""
    cands = [m for m in local_models if not skip_reason(m) and m.tools]
    if not cands:
        return None, "нет локальной модели с поддержкой инструментов (Ollama не сообщает tools ни для одной)"
    best = max(cands, key=lambda m: (m.params or 0.0, m.context_length or 0, m.name))
    size = best.parameter_size or "размер неизвестен"
    return best, f"самая крупная локальная модель с инструментами: {best.name} ({size})"


def plan_agents(local_models: Iterable[LocalModel], model_ids: dict[str, int],
                existing_agents: Iterable[dict], *, harness: bool = True) -> tuple[list[dict], list[dict]]:
    """(agents to create, report). `model_ids` maps a local model name to its registry id."""
    local_models = [m for m in local_models if not skip_reason(m) and m.name in model_ids]
    by_name = {str(a.get("name")): a for a in existing_agents}
    create: list[dict] = []
    report: list[dict] = []

    def want(spec: dict) -> None:
        cur = by_name.get(spec["name"])
        if cur is None:
            create.append(spec)
            by_name[spec["name"]] = spec
            report.append({"agent": spec["name"], "status": "create", "model_id": spec["model_id"]})
        elif cur.get("model_id") != spec["model_id"]:
            report.append({"agent": spec["name"], "status": "drift", "agent_id": cur.get("id"),
                           "model_id": cur.get("model_id"), "expected_model_id": spec["model_id"],
                           "note": "агент с этим именем уже есть и смотрит на другую модель — не меняю"})
        else:
            report.append({"agent": spec["name"], "status": "exists", "agent_id": cur.get("id")})

    for m in local_models:
        mid = model_ids[m.name]
        want(chat_agent(m, mid))
        if m.tools:
            want(computer_agent(m, mid))
        else:
            report.append({"agent": CHAT_PREFIX + m.name + PC_SUFFIX, "status": "skipped",
                           "reason": "Ollama не сообщает поддержку инструментов — управление ПК недоступно"})
    if harness:
        if HARNESS_NAME in by_name:
            cur = by_name[HARNESS_NAME]
            report.append({"agent": HARNESS_NAME, "status": "exists", "agent_id": cur.get("id"),
                           "model_id": cur.get("model_id")})
        else:
            best, why = pick_harness_model(local_models)
            if best is None:
                report.append({"agent": HARNESS_NAME, "status": "skipped", "reason": why})
            else:
                spec = harness_agent(best, model_ids[best.name])
                create.append(spec)
                report.append({"agent": HARNESS_NAME, "status": "create", "model_id": spec["model_id"],
                               "reason": why})
    return create, report


__all__ = ["CHAT_PREFIX", "HARNESS_NAME", "HARNESS_TOOLS", "OPERATOR_TOOLS", "PC_SUFFIX", "PROVIDER_KIND",
           "PROVIDER_NAME", "Plan", "chat_agent", "computer_agent", "find_provider", "harness_agent",
           "pick_harness_model", "plan_agents", "plan_models", "same_endpoint", "skip_reason", "unique_alias"]
