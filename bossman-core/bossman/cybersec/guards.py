"""Канонические ДВЕ точки CyberSec-периметра: ingest_guard / egress_guard.

Владелец правильно указал: не размазывать `assert_no_secret_egress()` по десяти
outbound-точкам (через полгода появится один новый tool без проверки), а иметь
ДВЕ канонические функции, через которые проходит весь недоверенный вход и весь
исходящий трафик.

```
UNTRUSTED INPUT → ingest_guard()  → prompt-injection / provenance / trust → Agent
Agent → typed outbound action → egress_guard() → secret-scan + policy → отправка
```

Инварианты (Security Hardening V1.1):
* ingest_guard — fail-OPEN по детекции безопасно: даже без срабатывания текст
  уже помечен как данные (шаг 7). Здесь добавляется активный детект инъекций.
* egress_guard — **fail-CLOSED** для чувствительных каналов: если проверить на
  секреты не удалось, для sensitive-канала возвращаем HOLD (задержать/спросить
  approval), а НЕ отправляем «на авось».
* IDS сам НЕ меняет permissions. Он выдаёт `RiskSignal`, который потребляет
  каноничная Policy (approval / deny / continue) — CyberSec не становится вторым
  Policy-движком, главный инвариант сохраняется.

Всё под общим флагом `BOSSMAN_CYBERSEC_V1_ENABLED` (OFF by default): пока слой
выключен, ingest_guard возвращает вход как есть, а egress_guard — ALLOW.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from . import gates, ids, injection, secret_guardian
from .trust import TrustLevel


# --------------------------------------------------------------- ingest_guard

@dataclass(frozen=True)
class IngestVerdict:
    safe: bool
    text: str                      # обезвреженный (или исходный) текст-данные
    findings: tuple[str, ...] = ()
    effective_trust: TrustLevel = TrustLevel.UNTRUSTED


def ingest_guard(text: str, *, source_trust: TrustLevel = TrustLevel.UNTRUSTED) -> IngestVerdict:
    """Единственная точка входа недоверенного текста в контекст.

    Слой выключен → текст возвращается как есть (шаг 7 уже пометил его данными).
    Включён → прогоняем Prompt Injection Firewall; при находке high/critical
    возвращаем обезвреженный текст. Trust никогда не поднимается выше источника.
    """
    if not gates.cybersec_enabled():
        return IngestVerdict(True, text, (), source_trust)
    v = injection.inspect(text, source_trust=source_trust)
    return IngestVerdict(
        safe=v.safe,
        text=(text if v.safe else v.sanitized),
        findings=tuple(f.pattern_id for f in v.findings),
        effective_trust=v.effective_trust,
    )


# --------------------------------------------------------------- egress_guard

class EgressDecision(str, Enum):
    ALLOW = "allow"
    HOLD = "hold"        # требуется approval (fail-closed на sensitive-канале)
    DENY = "deny"        # найден секрет / запрос эксфильтрации


@dataclass(frozen=True)
class EgressVerdict:
    decision: EgressDecision
    reason: str = ""
    channel: str = ""


#: Каналы, для которых egress fail-CLOSED (личное/наружу). Прочие — best-effort.
SENSITIVE_CHANNELS = frozenset({"telegram", "email", "webhook", "http", "sms", "external"})


def egress_guard(payload: object, *, channel: str, sensitive: bool | None = None) -> EgressVerdict:
    """Единственная точка исходящего трафика.

    Слой выключен → ALLOW (поведение ядра не меняется).
    Включён:
      * найден секрет в payload или это запрос на эксфильтрацию → DENY;
      * канал чувствительный, а проверка упала с исключением → HOLD (fail-closed:
        лучше задержать и спросить, чем отправить непроверенным);
      * иначе → ALLOW.
    Значение секрета в вердикт не попадает (reason не содержит payload).
    """
    if not gates.cybersec_enabled():
        return EgressVerdict(EgressDecision.ALLOW, "cybersec off", channel)

    is_sensitive = sensitive if sensitive is not None else (channel in SENSITIVE_CHANNELS)
    try:
        secret_guardian.assert_no_secret_egress(payload, destination=channel)
        text = payload if isinstance(payload, str) else str(payload)
        if secret_guardian.detect_exfil_request(text).is_request:
            return EgressVerdict(EgressDecision.DENY, "exfiltration request detected", channel)
        return EgressVerdict(EgressDecision.ALLOW, "no secret detected", channel)
    except secret_guardian.SecretEgressBlocked:
        return EgressVerdict(EgressDecision.DENY, "secret detected in outbound payload", channel)
    except Exception:  # noqa: BLE001 — проверка ошиблась
        if is_sensitive:
            # fail-CLOSED: не смогли проверить чувствительный канал → задержать.
            return EgressVerdict(EgressDecision.HOLD, "egress check failed; hold for approval", channel)
        return EgressVerdict(EgressDecision.ALLOW, "egress check failed; non-sensitive channel", channel)


# --------------------------------------------------- IDS → RiskSignal → Policy

@dataclass(frozen=True)
class RiskSignal:
    """Структурированный сигнал риска для КАНОНИЧНОЙ Policy.

    IDS НЕ меняет permissions сам. Он отдаёт этот сигнал, а Policy решает:
    continue / approval / deny. Так CyberSec не становится вторым Policy-движком.
    """
    score: float
    reason: str
    evidence: tuple[str, ...] = ()
    recommend_containment: bool = False

    @property
    def severity(self) -> str:
        s = self.score
        return "critical" if s >= 0.8 else "high" if s >= 0.6 else "medium" if s >= 0.3 else "low"


def ids_risk_signal(signal: ids.BehaviorSignal) -> RiskSignal:
    """Отобразить поведенческий сигнал IDS в RiskSignal (advisory для Policy)."""
    r = ids.score_behavior(signal)
    return RiskSignal(score=r.score, reason=r.severity, evidence=tuple(r.reasons),
                      recommend_containment=r.recommend_containment)


#: Рекомендация для Policy — НЕ решение. Policy может ужесточить, но не обязана.
def policy_recommendation(risk: RiskSignal) -> str:
    """continue | require_approval | deny — совет канонической Policy."""
    if risk.recommend_containment or risk.score >= 0.8:
        return "deny"
    if risk.score >= 0.5:
        return "require_approval"
    return "continue"
