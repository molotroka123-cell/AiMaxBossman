"""Feature-гейты CyberSec V1 и тройной гейт тренировочной лаборатории.

Всё ВЫКЛЮЧЕНО по умолчанию. Защитные модули (firewall/IDS/guardian) можно
включить отдельно от лаборатории; сама лаборатория (red-vs-blue) требует
ЯВНОГО подтверждения и одноразовой песочницы.

Гейты — единственная точка активации. Никакой модуль не «включает себя сам».
"""
from __future__ import annotations

import os
from dataclasses import dataclass

CYBERSEC_ENABLED_ENV = "BOSSMAN_CYBERSEC_V1_ENABLED"
LAB_ENABLED_ENV = "BOSSMAN_CYBER_LAB_ENABLED"
LAB_ACK_ENV = "BOSSMAN_CYBER_LAB_ACK"
LAB_ACK_VALUE = "I_UNDERSTAND_THIS_IS_A_SANDBOX"


def _on(name: str) -> bool:
    return (os.getenv(name) or "").strip() == "1"


def cybersec_enabled() -> bool:
    """Защитный слой CyberSec V1 (firewall/IDS/guardian). OFF by default."""
    return _on(CYBERSEC_ENABLED_ENV)


@dataclass(frozen=True)
class SandboxFacts:
    """Факты о среде, которые ОБЯЗАН предоставить вызывающий (не угадываем сами)."""
    is_disposable: bool = False
    production_secrets_mounted: bool = True
    production_network_allowed: bool = True


class LabFrozen(RuntimeError):
    """Лаборатория заморожена: гейт не пройден."""


def lab_enabled(facts: SandboxFacts | None = None) -> bool:
    try:
        assert_lab_enabled(facts or SandboxFacts())
        return True
    except LabFrozen:
        return False


def assert_lab_enabled(facts: SandboxFacts) -> None:
    """Все условия обязаны совпасть; иначе — LabFrozen (fail-closed).

    Порядок: сначала env-гейты (дёшево), затем факты среды. Сообщение НЕ
    раскрывает, какие именно секреты/сеть присутствуют — только сам факт.
    """
    if not cybersec_enabled():
        raise LabFrozen(f"cybersec frozen: {CYBERSEC_ENABLED_ENV} != 1")
    if not _on(LAB_ENABLED_ENV):
        raise LabFrozen(f"training lab frozen: {LAB_ENABLED_ENV} != 1")
    if (os.getenv(LAB_ACK_ENV) or "") != LAB_ACK_VALUE:
        raise LabFrozen(f"training lab frozen: {LAB_ACK_ENV} acknowledgement missing")
    if not facts.is_disposable:
        raise LabFrozen("training lab requires a disposable sandbox")
    if facts.production_secrets_mounted:
        raise LabFrozen("production secrets must not be mounted for the lab")
    if facts.production_network_allowed:
        raise LabFrozen("production network scope must be disabled for the lab")
