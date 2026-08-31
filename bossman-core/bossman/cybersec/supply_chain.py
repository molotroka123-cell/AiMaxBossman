"""Supply Chain Guardian — происхождение того, что попадает в систему.

Проверяет предложения (навык, инструмент, MCP-сервер, зависимость) ДО их
регистрации: откуда пришло, кто подтвердил, нет ли исполняемых/shell-элементов.
Не заменяет Tool Registry — стоит ПЕРЕД ним как допуск.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .trust import TrustLevel


@dataclass(frozen=True)
class SupplyVerdict:
    accepted: bool
    reasons: tuple[str, ...] = ()

    def __bool__(self) -> bool:  # удобство в условиях
        return self.accepted


#: Признаки, которых не должно быть в предложении навыка/инструмента.
_SHELLY = re.compile(
    r"\b(subprocess|os\.system|popen|eval\(|exec\(|/bin/(?:sh|bash)|powershell|cmd\.exe)\b"
    r"|\bcurl\b[^\n]{0,40}\|\s*(?:sh|bash)\b")


def review_proposal(proposal: dict[str, Any], *,
                    source_trust: TrustLevel = TrustLevel.UNTRUSTED,
                    verified_runs: int = 0,
                    min_verified_runs: int = 3) -> SupplyVerdict:
    """Допуск предложения в реестр. Deny-by-default.

    Отклоняем, если: источник недоверен; в теле есть shell/eval; заявлен
    произвольный запуск команд; недостаточно верифицированных прогонов.
    """
    reasons: list[str] = []
    body = " ".join(str(v) for v in proposal.values())

    if int(source_trust) < int(TrustLevel.TRUSTED_REPO):
        reasons.append("source is not a trusted repository or better")
    if _SHELLY.search(body):
        reasons.append("proposal contains shell/eval execution primitives")
    if str(proposal.get("kind", "")).lower() in {"shell", "exec", "command"}:
        reasons.append("raw command execution is never an acceptable skill kind")
    if proposal.get("requests_secret") or "secret" in str(proposal.get("scopes", "")).lower():
        reasons.append("proposal requests secret access")
    if verified_runs < min_verified_runs:
        reasons.append(f"insufficient verified runs ({verified_runs} < {min_verified_runs})")

    return SupplyVerdict(not reasons, tuple(reasons))
