"""Динамическая политика: AUTO / ASK / DENY на аккаунт и на действие.

Политика — это данные времени выполнения, а не константы в коде. Но у неё есть
две границы, которые данными не двигаются:

1. **Возможность проверяется раньше политики.** Разрешение не создаёт
   возможности. Если аккаунт физически не умеет действие, никакое `AUTO` этого
   не изменит — и результат оценки будет `NOT_AVAILABLE`, а не `DENY`. Разница
   важна: «нельзя» и «нечем» требуют от владельца разного.
2. **Класс SECURITY политикой не открывается.** Пароль, второй фактор и
   владение аккаунтом выведены из автоматизации совсем.

Спека задаёт три механизма разрешения конфликтов сразу — специфичность,
приоритет и порядок объявления — и не говорит, как они соотносятся
(`DIGEST_CORE` G11). Здесь принят явный порядок; он записан в
`PRE_IMPLEMENTATION_AUDIT.md` §5 и закреплён тестами.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .capability import CapabilitySnapshot, CapabilityStatus, is_actionable
from .safety import default_decision, is_unoverridable, safety_of


class Decision(str, Enum):
    AUTO = "AUTO"
    ASK = "ASK"
    DENY = "DENY"


class Scope(str, Enum):
    SYSTEM = "SYSTEM"
    PROVIDER = "PROVIDER"
    ACCOUNT = "ACCOUNT"
    ACTION = "ACTION"


# Чем больше число, тем уже область действия правила и тем оно сильнее.
_SPECIFICITY = {Scope.SYSTEM: 0, Scope.PROVIDER: 1, Scope.ACCOUNT: 2, Scope.ACTION: 3}

# Особый исход: возможности нет. Это не решение политики, и он не смешивается
# с DENY ни в интерфейсе, ни в аудите.
NOT_AVAILABLE = "NOT_AVAILABLE"


class PolicyError(ValueError):
    """Ошибка конфигурации политики. Не путать с отказом по политике."""


# ------------------------------------------------------------------ условия

_OPS = ("_in", "_not_in", "_gte", "_lte", "_gt", "_lt", "_eq", "_ne")


def _compare(op: str, actual: Any, expected: Any) -> bool:
    try:
        if op == "in":
            return actual in expected
        if op == "not_in":
            return actual not in expected
        if op == "eq":
            return actual == expected
        if op == "ne":
            return actual != expected
        if op == "gte":
            return float(actual) >= float(expected)
        if op == "lte":
            return float(actual) <= float(expected)
        if op == "gt":
            return float(actual) > float(expected)
        if op == "lt":
            return float(actual) < float(expected)
    except (TypeError, ValueError):
        # Несравнимые типы — условие НЕ выполнено. Ошибку сравнения нельзя
        # трактовать как совпадение: так `AUTO` получалось бы из мусора.
        return False
    raise PolicyError(f"неизвестная операция условия: {op}")


def conditions_hold(conditions: dict[str, Any] | None, context: dict[str, Any]) -> bool:
    """Выполнены ли условия правила на данном контексте.

    Отсутствующий в контексте ключ означает «не выполнено». Это главное решение
    всей функции: неизвестное не должно открывать действие. Иначе любой сбой
    сбора контекста превращался бы в разрешение.
    """
    if not conditions:
        return True
    for key, expected in conditions.items():
        if isinstance(expected, dict):
            nested = context.get(key)
            for raw_op, value in expected.items():
                for suffix in _OPS:
                    if raw_op.endswith(suffix):
                        field_name, op = raw_op[: -len(suffix)], suffix[1:]
                        break
                else:
                    raise PolicyError(
                        f"условие {key}.{raw_op} не содержит операции; "
                        f"допустимы окончания: {', '.join(_OPS)}")
                if not isinstance(nested, dict) or field_name not in nested:
                    return False
                if not _compare(op, nested[field_name], value):
                    return False
        else:
            if key not in context or context[key] != expected:
                return False
    return True


# ------------------------------------------------------------------ правила

@dataclass(frozen=True, slots=True)
class PolicyRule:
    """Правило политики. Поля — из `policy_rule.schema.json`."""

    id: str
    scope: Scope
    capability: str
    decision: Decision
    provider: str | None = None
    account_id: str | None = None
    conditions: dict[str, Any] = field(default_factory=dict)
    version: int = 1
    enabled: bool = True
    priority: int = 0
    hard_deny: bool = False
    order: int = 0            # порядок объявления в профиле; заполняется профилем

    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, order: int = 0) -> "PolicyRule":
        for required in ("capability", "decision"):
            if not raw.get(required):
                raise PolicyError(f"в правиле политики нет обязательного поля {required}")
        try:
            decision = Decision(str(raw["decision"]))
        except ValueError as exc:
            raise PolicyError(
                f"неизвестное решение {raw['decision']!r}; допустимы "
                f"{[d.value for d in Decision]}") from exc
        try:
            scope = Scope(str(raw.get("scope") or "ACCOUNT"))
        except ValueError as exc:
            raise PolicyError(f"неизвестная область правила {raw.get('scope')!r}") from exc

        capability = str(raw["capability"])
        hard_deny = bool(raw.get("hard_deny"))
        # Правило, открывающее класс SECURITY, — ошибка конфигурации, а не
        # разрешение. Молча проигнорировать его нельзя: владелец будет считать,
        # что автоматизация настроена, а она не настроена.
        if is_unoverridable(capability) and decision is not Decision.DENY:
            raise PolicyError(
                f"возможность {capability} относится к классу "
                f"{safety_of(capability).value} и политикой не открывается; "
                f"правило {raw.get('id') or '(без id)'} с решением "
                f"{decision.value} недопустимо")
        return cls(
            id=str(raw.get("id") or f"rule_{capability}_{order}"),
            scope=scope, capability=capability, decision=decision,
            provider=(str(raw["provider"]) if raw.get("provider") else None),
            account_id=(str(raw["account_id"]) if raw.get("account_id") else None),
            conditions=dict(raw.get("conditions") or {}),
            version=int(raw.get("version") or 1),
            enabled=bool(raw.get("enabled", True)),
            priority=int(raw.get("priority") or 0),
            hard_deny=hard_deny, order=order)

    def matches(self, *, capability: str, provider: str, account_id: str) -> bool:
        if not self.enabled:
            return False
        if self.capability not in ("*", capability):
            return False
        if self.provider and self.provider != provider:
            return False
        if self.account_id and self.account_id != account_id:
            return False
        return True

    @property
    def sort_key(self) -> tuple:
        """Порядок отбора: специфичнее → приоритетнее → условное → раньше объявлено."""
        return (-_SPECIFICITY[self.scope], -self.priority,
                0 if self.conditions else 1, self.order)


@dataclass(frozen=True, slots=True)
class PolicyEvaluation:
    """Результат оценки. Поля — из `policy_evaluation.schema.json`.

    Хранится целиком в аудите: без него нельзя ответить на вопрос «почему
    система решила именно так», а он задаётся ровно тогда, когда что-то пошло
    не так.
    """

    decision: str
    profile_id: str
    profile_version: int
    matched_rule_ids: list[str]
    reason: str
    conditions_snapshot: dict[str, Any]
    evaluated_at: str
    capability: str = ""
    capability_status: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"decision": self.decision, "profile_id": self.profile_id,
                "profile_version": self.profile_version,
                "matched_rule_ids": list(self.matched_rule_ids), "reason": self.reason,
                "conditions_snapshot": dict(self.conditions_snapshot),
                "evaluated_at": self.evaluated_at, "capability": self.capability,
                "capability_status": self.capability_status}

    @property
    def available(self) -> bool:
        return self.decision != NOT_AVAILABLE

    @property
    def allowed_without_human(self) -> bool:
        return self.decision == Decision.AUTO.value


@dataclass(frozen=True, slots=True)
class PolicyProfile:
    """Именованный набор правил с версией. Версия хранится в каждой работе."""

    profile_id: str
    version: int
    rules: tuple[PolicyRule, ...] = ()

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PolicyProfile":
        rules = tuple(PolicyRule.from_dict(r, order=i)
                      for i, r in enumerate(raw.get("rules") or []))
        return cls(profile_id=str(raw.get("profile_id") or "default"),
                   version=int(raw.get("version") or 1), rules=rules)

    def evaluate(self, *, capability: str, provider: str, account_id: str,
                 snapshot: CapabilitySnapshot | None = None,
                 context: dict[str, Any] | None = None,
                 now: datetime | None = None) -> PolicyEvaluation:
        """Оценка одного действия. Возможность проверяется первой."""
        ctx = dict(context or {})
        stamp = (now or datetime.now(timezone.utc)).isoformat()
        status = snapshot.status_of(capability) if snapshot is not None else None

        if status is not None and not is_actionable(status):
            # Не «запрещено», а «нечем». Смешивать нельзя: владельцу нужны
            # разные действия — поменять политику или получить возможность.
            cap = snapshot.get(capability) if snapshot else None
            return PolicyEvaluation(
                decision=NOT_AVAILABLE, profile_id=self.profile_id,
                profile_version=self.version, matched_rule_ids=[],
                reason=(f"возможность {capability} не подтверждена аккаунтом: "
                        f"{cap.why_not() if cap else 'её нет в снимке возможностей'}"),
                conditions_snapshot=ctx, evaluated_at=stamp, capability=capability,
                capability_status=(status.value if status else
                                   CapabilityStatus.NOT_SUPPORTED.value))

        candidates = [r for r in self.rules
                      if r.matches(capability=capability, provider=provider,
                                   account_id=account_id)]

        # Жёсткий запрет обходит всё остальное, включая более узкие правила.
        for rule in candidates:
            if rule.hard_deny and conditions_hold(rule.conditions, ctx):
                return self._result(Decision.DENY.value, [rule.id],
                                    f"жёсткий запрет: правило {rule.id}", ctx, stamp,
                                    capability, status)

        for rule in sorted(candidates, key=lambda r: r.sort_key):
            if conditions_hold(rule.conditions, ctx):
                return self._result(rule.decision.value, [rule.id],
                                    f"правило {rule.id} ({rule.scope.value})",
                                    ctx, stamp, capability, status)

        fallback = default_decision(capability)
        return self._result(
            fallback, [],
            f"подходящего правила нет; дефолт класса {safety_of(capability).value}",
            ctx, stamp, capability, status)

    def _result(self, decision: str, rules: list[str], reason: str,
                ctx: dict[str, Any], stamp: str, capability: str,
                status: CapabilityStatus | None) -> PolicyEvaluation:
        return PolicyEvaluation(
            decision=decision, profile_id=self.profile_id, profile_version=self.version,
            matched_rule_ids=rules, reason=reason, conditions_snapshot=ctx,
            evaluated_at=stamp, capability=capability,
            capability_status=(status.value if status else ""))


__all__ = ["NOT_AVAILABLE", "Decision", "PolicyError", "PolicyEvaluation", "PolicyProfile",
           "PolicyRule", "Scope", "conditions_hold"]
