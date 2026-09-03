"""Режим безопасности модуля обучения на трейдинге. Не обсуждается.

Почему отдельный модуль и почему он первый в цепочке импортов: любая ошибка
здесь стоит денег владельца, поэтому запрет на исполнение не размазан по коду,
а собран в одном месте, которое обязаны спросить все остальные модули.

Ключевое отличие от «флага в конфиге»: переменные окружения могут только
УЖЕСТОЧИТЬ режим. Попытка включить исполнение через окружение не открывает
путь, потому что live-пути в модуле не существует физически — нет ни клиента
биржи, ни подписи ордера, ни ключа на запись. Это единственная защита, которую
нельзя обойти опечаткой в .env.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

# Умолчания владельца. Читаются как константы, а не как настройки: их нельзя
# ослабить из окружения (см. execution_mode()).
TRADING_EXECUTION = "OFF"
PAPER_TRADING_ONLY = True
OWNER_APPROVAL_REQUIRED = True
EXTERNAL_WRITE_ACTIONS = "DENY"

# Разрешённый путь работы. Всё, чего нет в списке, отклоняется по умолчанию,
# а не «разрешается, раз не запрещено».
ALLOWED_STAGES = ("historical_analysis", "replay", "simulation", "paper_trading", "report")

# Действия, которые модуль не выполняет никогда — ни с одобрением, ни без.
FORBIDDEN_ACTIONS = frozenset({
    "place_order", "cancel_order_live", "transfer", "deposit", "withdraw",
    "api_key_write", "margin_change", "leverage_change", "send_external_message",
})


class LiveExecutionForbidden(RuntimeError):
    """Попытка выйти за пределы исторического анализа / симуляции / paper."""


class OwnerApprovalRequired(RuntimeError):
    """Действие требует подтверждения владельца, которого нет или оно поддельное."""


class UnknownProviderPrice(RuntimeError):
    """Цена платного вызова неизвестна. Выдумывать стоимость запрещено — отказ."""


def execution_mode() -> str:
    """Всегда OFF.

    Окружение читается только чтобы ЗАФИКСИРОВАТЬ попытку включения в логе
    вызывающего кода — вернуть что-то кроме OFF функция не может. Возврат
    значения из окружения означал бы, что режим безопасности настраиваемый.
    """
    return "OFF"


def env_requested_live() -> bool:
    """Признак того, что кто-то пытался включить live через окружение."""
    raw = os.environ.get("TRADING_EXECUTION", "").strip().upper()
    return raw not in ("", "OFF", "0", "FALSE", "NO")


def assert_no_live_execution(action: str, *, stage: str = "") -> None:
    """Единственная точка, через которую проходят любые «торговые» действия."""
    normalized = (action or "").strip().lower()
    if normalized in FORBIDDEN_ACTIONS:
        raise LiveExecutionForbidden(
            f"action {normalized!r} is permanently disabled: TRADING_EXECUTION=OFF, "
            f"PAPER_TRADING_ONLY={PAPER_TRADING_ONLY}")
    if stage and stage not in ALLOWED_STAGES:
        raise LiveExecutionForbidden(
            f"stage {stage!r} is not in the allowed path {ALLOWED_STAGES}")


def assert_read_only_integration(name: str, mode: str) -> None:
    """Существующие интеграции (биржа, Agent OS, MCP) — только на чтение."""
    if (mode or "").strip().lower() not in ("read", "read_only", "readonly"):
        raise LiveExecutionForbidden(
            f"integration {name!r} may only be used READ_ONLY, got mode={mode!r}")


# Кто НЕ может выдать одобрение. Модель, выдавшая одобрение сама себе, — это
# не одобрение, а самосертификация; та же логика, что в learning/trace.py.
_SELF_APPROVAL_MARKERS = ("self", "same-agent", "agent", "model", "assistant",
                          "bossman", "claude", "gpt", "llm", "auto")
_MODEL_LIKE = re.compile(r"(claude|gpt|gemini|llama|qwen|deepseek|opus|sonnet|haiku|o\d)", re.I)


@dataclass(frozen=True, slots=True)
class OwnerApproval:
    """Подтверждение владельца на конкретный источник и конкретную стадию."""

    subject: str          # что именно одобрено (например, source_id видео)
    stage: str            # стадия из ALLOWED_STAGES
    granted_by: str       # человек, не агент
    granted_at: datetime

    def __post_init__(self) -> None:
        who = (self.granted_by or "").strip().lower()
        if not who:
            raise OwnerApprovalRequired("granted_by is empty")
        # Подстрока, а не точное совпадение: «bossman-agent», «auto-approver»
        # и «self-service» — это те же самые агенты, только с суффиксом.
        if any(marker in who for marker in _SELF_APPROVAL_MARKERS) or _MODEL_LIKE.search(who):
            raise OwnerApprovalRequired(
                f"approval issued by a model/agent identity {self.granted_by!r} is not an approval")
        if self.stage not in ALLOWED_STAGES:
            raise LiveExecutionForbidden(f"stage {self.stage!r} not allowed")
        if self.granted_at.tzinfo is None:
            raise OwnerApprovalRequired("granted_at must be timezone-aware")


def require_owner_approval(approval: OwnerApproval | None, *, subject: str, stage: str) -> OwnerApproval:
    """Нет одобрения ровно на этот предмет и стадию — нет работы."""
    if not OWNER_APPROVAL_REQUIRED:  # pragma: no cover — константа, ветка для читателя
        raise LiveExecutionForbidden("OWNER_APPROVAL_REQUIRED must stay True")
    if approval is None:
        raise OwnerApprovalRequired(f"owner approval required for {subject!r} / {stage!r}")
    if approval.subject != subject or approval.stage != stage:
        raise OwnerApprovalRequired(
            f"approval mismatch: have ({approval.subject!r},{approval.stage!r}), "
            f"need ({subject!r},{stage!r})")
    return approval


class EvidenceClass(str, Enum):
    """Класс доказательности. Честность важнее красоты отчёта."""

    MOCK = "MOCK"
    SIMULATED = "SIMULATED"
    HISTORICAL_REPLAY = "HISTORICAL_REPLAY"
    REAL_SANDBOX = "REAL_SANDBOX"
    LIVE_PROVEN = "LIVE_PROVEN"
    BLOCKED = "BLOCKED"
    DEAD_OR_UNWIRED = "DEAD_OR_UNWIRED"


# Поля, без которых LIVE_PROVEN невозможен физически.
LIVE_PROOF_FIELDS = ("venue", "size", "leverage", "entry_fills", "exit_fills",
                     "fees_paid", "funding_paid", "realized_pnl", "raw_market_data_ref")


def assert_live_proof(payload: dict) -> None:
    """LIVE_PROVEN без данных исполнения ставить запрещено."""
    missing = [f for f in LIVE_PROOF_FIELDS if not payload.get(f)]
    if missing:
        raise LiveExecutionForbidden(
            "LIVE_PROVEN requires real execution data; missing: " + ",".join(missing))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
