"""Неизменяемая провенанс-запись прогона: КТО и ЧЕМ его выполнял.

Проблема, которую это закрывает. `task_runs` хранил из личности исполнителя
ровно одну строку — `model_alias`. Всё остальное (системный промпт, набор
инструментов, права, потолки, модель и её запасной вариант) жило в таблице
`agents`, которая МЕНЯЕТСЯ, а внешний ключ задачи объявлен `ON DELETE SET NULL`.
Практический смысл: отредактировал владелец агента — и прошлый прогон начинал
рассказывать про новую конфигурацию; удалил агента — и переставал рассказывать
вообще. Историю прогонов нельзя было использовать ни как улику, ни как ответ на
вопрос «а с какими правами эта задача вообще ходила».

Что здесь делается. В момент СТАРТА прогона личность исполнителя снимается
целиком и кладётся в сам прогон. Дальше она не меняется никогда: запрет стоит
триггером в базе (`_install_provenance_guard` в db.py), а не в вызовах, потому
что `task_runs` пишется из двух десятков мест и караулить каждое означало бы
держать инвариант ровно до следующего нового места записи.

Чего здесь НЕТ и почему:

* скрытых рассуждений модели — их не хранят, и провенанс не повод начать;
* секретов в открытом виде — ключи провайдера сюда не попадают ни в каком виде,
  включая маску: провенанс отвечает на вопрос «какой провайдер», а не «каким
  ключом»;
* полного текста промпта — там, где достаточно дайджеста, лежит дайджест.
  sha256 доказывает «промпт был ИМЕННО такой» и при этом не превращает таблицу
  прогонов во вторую копию всех системных промптов;
* выдуманного дозаполнения. У прогонов, которые прошли ДО этой правки,
  провенанса не было и появиться ему неоткуда. Они честно отвечают
  NOT_CAPTURED. Подставить туда сегодняшнюю конфигурацию агента значило бы
  соврать ровно в том месте, ради которого всё это писалось.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Версия формата. Читатель, встретивший больший номер, обязан сказать, что не
#: понимает запись, а не разобрать её наполовину.
SCHEMA_VERSION = 1

#: Что отвечает историческому прогону, снятому до появления провенанса.
NOT_CAPTURED = "NOT_CAPTURED"

#: Ключи `agents.permissions`, которые НЕ идут в дайджест прав: это не права, а
#: оформление. Иначе переименование агента «меняло» бы его полномочия.
_PERMISSION_NOISE = frozenset({"note", "notes", "comment", "description", "label"})


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> str:
    """Стабильная форма для дайджеста: порядок ключей и разделители фиксированы.

    Без этого один и тот же набор прав давал бы разный sha256 в зависимости от
    того, в каком порядке словарь оказался в памяти, и «права не менялись»
    было бы невозможно доказать.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str)


def _digest(value: Any) -> str:
    return _sha256(_canonical(value))


def _model_identity(row: dict | None) -> dict[str, Any]:
    """Личность модели без единого секрета.

    Провайдер назван (`provider_id`), ключ провайдера — нет. Вопрос, на который
    отвечает провенанс, — «через кого ходили», а не «чем расплачивались».
    """
    if not row:
        return {"present": False}
    return {
        "present": True,
        "id": row.get("id"),
        "alias": row.get("alias") or "",
        "name": row.get("name") or "",
        "kind": row.get("kind") or "",
        "provider_id": row.get("provider_id"),
        "context_window": row.get("context_window"),
    }


def _permissions_for_digest(agent: dict) -> Any:
    perms = agent.get("permissions")
    if not isinstance(perms, dict):
        return perms
    return {k: v for k, v in perms.items() if k not in _PERMISSION_NOISE}


def _policy_rules(agent: dict) -> list[dict]:
    perms = agent.get("permissions") or {}
    if isinstance(perms, dict):
        rules = perms.get("tool_rules")
        if isinstance(rules, list):
            return [r for r in rules if isinstance(r, dict)]
    return []


def _privacy_mode() -> str:
    """Режим приватности на момент старта.

    Читается из того же флага, что и сам offline-режим; импорт локальный,
    потому что провенанс не должен тянуть за собой фичу целиком.
    """
    try:
        from .features import offline_mode
        return "OFFLINE" if offline_mode.enabled() else "ONLINE"
    except Exception:                                   # pragma: no cover - защитный
        return "UNKNOWN"


_repo_sha_cache: str | None = None


def repository_sha(repo: Path | None = None) -> str:
    """SHA рабочего дерева, если оно есть. Иначе — честное NOT_CAPTURED.

    Считается один раз на процесс: это свойство сборки, а не прогона, и
    вызывать git на каждый старт задачи незачем.
    """
    global _repo_sha_cache
    if _repo_sha_cache is not None:
        return _repo_sha_cache
    root = repo or Path(__file__).resolve().parents[2]
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(root),
                             capture_output=True, text=True, timeout=5)
        sha = out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        sha = ""
    if not sha:
        # Установка из пакета/образа: репозитория рядом нет. Это не ошибка и не
        # повод выдумать значение.
        sha = os.environ.get("BCC_BUILD_SHA", "").strip() or NOT_CAPTURED
    _repo_sha_cache = sha
    return sha


def build(
    *,
    task: dict,
    run: dict,
    agent: dict | None,
    model: dict | None = None,
    fallback_model: dict | None = None,
    allowed_tools: list[str] | None = None,
    repo_sha: str | None = None,
) -> dict[str, Any]:
    """Собрать провенанс-запись. Чистая функция: ничего не читает и не пишет.

    `agent` может быть None — задача бывает без агента (детерминированный
    исполнитель). Это тоже факт исполнения, и он записывается как факт, а не
    подменяется пустым словарём, притворяющимся агентом.
    """
    agent = agent or {}
    tools = list(allowed_tools if allowed_tools is not None else _allowed_tools(task, agent))
    system_prompt = agent.get("system_prompt") or ""
    permissions = _permissions_for_digest(agent)
    rules = _policy_rules(agent)

    # «Ревизии» у агента в схеме нет. Вместо того чтобы объявить её отсутствие
    # неважной, берём content-addressed ревизию: дайджест тех полей агента,
    # которые определяют поведение. Две записи с одним agent_revision — это
    # буквально одна и та же конфигурация исполнителя.
    agent_revision = _digest({
        "name": agent.get("name") or "",
        "role": agent.get("role") or "",
        "system_prompt": system_prompt,
        "model_id": agent.get("model_id"),
        "fallback_model_id": agent.get("fallback_model_id"),
        "tools": agent.get("tools"),
        "permissions": permissions,
        "max_steps": agent.get("max_steps"),
        "max_tokens": agent.get("max_tokens"),
        "budget_usd": agent.get("budget_usd"),
    }) if agent else NOT_CAPTURED

    budget = {
        "max_steps": agent.get("max_steps"),
        "max_tokens": agent.get("max_tokens"),
        "budget_usd": agent.get("budget_usd"),
        # Потолки прогона владелец задаёт на ЗАДАЧУ (§8), поэтому личность
        # бюджета без них неполна.
        "task_limits": (task.get("meta") or {}).get("limits")
        if isinstance(task.get("meta"), dict) else None,
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "captured_at_run_start": True,
        # --- кто исполнял
        "agent_id": agent.get("id") if agent else None,
        "agent_name": agent.get("name") or (NOT_CAPTURED if agent else ""),
        "agent_role": agent.get("role") or "",
        "agent_revision": agent_revision,
        "agent_present": bool(agent),
        # --- чем именно
        "system_prompt_sha256": _sha256(system_prompt) if agent else NOT_CAPTURED,
        "system_prompt_bytes": len(system_prompt.encode("utf-8")) if agent else 0,
        "model": _model_identity(model),
        "fallback_model": _model_identity(fallback_model),
        # --- с какими полномочиями
        "allowed_tools": sorted(tools),
        "allowed_tools_sha256": _digest(sorted(tools)),
        "permissions_sha256": _digest(permissions),
        "policy_rules_sha256": _digest(rules),
        "policy_rules_count": len(rules),
        # --- в каком режиме и в каких пределах
        "privacy_mode": _privacy_mode(),
        "budget": budget,
        "budget_sha256": _digest(budget),
        # --- на каком коде
        "repository_sha": repo_sha if repo_sha is not None else repository_sha(),
        "task_kind": task.get("kind") or "generic",
        "attempt": run.get("attempt"),
    }


def _allowed_tools(task: dict, agent: dict) -> list[str]:
    try:
        from .tools import allowed_tools_for
        return allowed_tools_for(task, agent)
    except Exception:                                   # pragma: no cover - защитный
        return []


def describe(stored: Any) -> dict[str, Any]:
    """Прочитать сохранённый провенанс для показа.

    Историческому прогону возвращается явный NOT_CAPTURED, а не пустая
    структура, которую читатель примет за «прав не было».
    """
    if not stored:
        return {"status": NOT_CAPTURED,
                "reason": "run started before provenance capture existed"}
    if isinstance(stored, str):
        try:
            stored = json.loads(stored)
        except (TypeError, ValueError):
            return {"status": "UNREADABLE", "reason": "stored provenance is not JSON"}
    if not isinstance(stored, dict):
        return {"status": "UNREADABLE", "reason": "stored provenance is not an object"}
    version = stored.get("schema_version")
    if not isinstance(version, int) or version > SCHEMA_VERSION:
        return {"status": "UNSUPPORTED_SCHEMA", "schema_version": version}
    return {"status": "CAPTURED", **stored}
