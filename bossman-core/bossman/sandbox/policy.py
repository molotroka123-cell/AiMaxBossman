"""Stage 8 — движок политики и риска.

Детерминированно превращает заявку (SandboxSpec) в разрешённую конфигурацию
(SandboxPolicy). Ключевые инварианты:
- сеть по умолчанию OFFLINE; INTERNET не выводится из текста задачи;
- риск задаёт МИНИМАЛЬНУЮ изоляцию; политика — тоже; берётся максимум;
- fail closed: если рантайм не даёт нужный tier — IsolationUnavailable, а не
  тихий даунгрейд;
- host-секреты и продовый браузер-профиль в песочницу не попадают НИКОГДА.
"""
from __future__ import annotations

from .. import errors
from .models import (
    POLICY_MIN_ISOLATION,
    RISK_MIN_ISOLATION,
    IsolationTier,
    NetworkMode,
    PolicyMode,
    RiskAssessment,
    RiskLevel,
    RuntimeCapabilities,
    SandboxPolicy,
    SandboxSpec,
)


class RiskEngine:
    """Оценивает риск заявки. Подсказку пользователя нельзя опускать ниже
    того, что диктуют объективные признаки (untrusted-источник, egress и т.п.)."""

    def assess(self, spec: SandboxSpec) -> RiskAssessment:
        reasons: list[str] = []
        level = spec.risk_hint or RiskLevel.LOW
        if spec.risk_hint:
            reasons.append(f"risk_hint={spec.risk_hint.value}")

        # Объективные подъёмники риска (берём максимум с подсказкой).
        def bump(to: RiskLevel, why: str) -> None:
            nonlocal level
            reasons.append(why)
            if _risk_rank(to) > _risk_rank(level):
                level = to

        if spec.network_mode == NetworkMode.INTERNET:
            bump(RiskLevel.HIGH, "network=INTERNET")
        elif spec.network_mode == NetworkMode.ALLOWLIST:
            bump(RiskLevel.MEDIUM, "network=ALLOWLIST")
        if spec.workspace_source and not spec.trusted_source:
            bump(RiskLevel.MEDIUM, "untrusted workspace source")
        if spec.policy_mode == PolicyMode.HOSTILE:
            bump(RiskLevel.HOSTILE, "policy_mode=HOSTILE")

        min_iso = RISK_MIN_ISOLATION[level]
        return RiskAssessment(level=level, reasons=tuple(reasons), min_isolation=min_iso)


class PolicyEngine:
    """Резолвит SandboxSpec + RiskAssessment + возможности рантайма в SandboxPolicy.
    Бросает IsolationUnavailable/PolicyDenied вместо тихого ослабления."""

    def resolve(
        self,
        spec: SandboxSpec,
        risk: RiskAssessment,
        caps: RuntimeCapabilities | None,
    ) -> SandboxPolicy:
        mode = spec.policy_mode

        # 1) Минимальная изоляция = max(риск, режим политики).
        required = _max_tier(risk.min_isolation, POLICY_MIN_ISOLATION[mode])

        # 2) Fail closed: рантайм обязан ДАВАТЬ этот tier. Нет — отказ, не даунгрейд.
        if caps is None:
            raise errors.IsolationUnavailable(
                "no runtime available for sandbox", extra={"required_tier": required.value})
        if not caps.provides(required):
            raise errors.IsolationUnavailable(
                f"runtime '{caps.name}' cannot provide required isolation {required.value}",
                extra={"required_tier": required.value, "runtime": caps.name,
                       "runtime_tiers": [t.value for t in caps.tiers]})

        # 3) Сеть: OFFLINE по умолчанию. HOSTILE-режим не выпускаем в INTERNET.
        net = spec.network_mode
        if mode == PolicyMode.HOSTILE and net == NetworkMode.INTERNET:
            raise errors.PolicyDenied(
                "HOSTILE lab may not use INTERNET egress", extra={"network_mode": net.value})
        if net == NetworkMode.ALLOWLIST and not caps.supports_allowlist:
            raise errors.IsolationUnavailable(
                f"runtime '{caps.name}' cannot enforce an egress allowlist")
        if net == NetworkMode.ALLOWLIST and not spec.allowlist:
            raise errors.PolicyDenied("ALLOWLIST network mode requires a non-empty allowlist")

        return SandboxPolicy(
            mode=mode,
            network_mode=net,
            isolation_tier=required,
            allowlist=tuple(spec.allowlist),
            read_only_root=caps.supports_readonly_root,
            drop_caps=True,
            no_new_privs=True,
            mount_host_secrets=False,          # инвариант #4 — никогда
            reuse_prod_browser_profile=False,  # инвариант #9 — никогда
        )


def _risk_rank(r: RiskLevel) -> int:
    return {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "HOSTILE": 3}[r.value]


def _max_tier(a: IsolationTier, b: IsolationTier) -> IsolationTier:
    return a if a.rank >= b.rank else b
