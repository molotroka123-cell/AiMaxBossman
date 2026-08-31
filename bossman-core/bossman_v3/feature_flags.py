from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class V3Flags:
    master: bool
    computer_agent: bool
    visual_state: bool
    self_healing: bool
    skill_factory: bool
    recovery_kernel: bool
    self_improvement: bool
    data_guardian: bool
    low_memory: bool

    @classmethod
    def from_env(cls) -> "V3Flags":
        master = _env_bool("BOSSMAN_V3_ENABLED", False)
        def enabled(name: str) -> bool:
            return master and _env_bool(name, False)
        return cls(
            master=master,
            computer_agent=enabled("BOSSMAN_V3_COMPUTER_AGENT"),
            visual_state=enabled("BOSSMAN_V3_VISUAL_STATE"),
            self_healing=enabled("BOSSMAN_V3_SELF_HEALING"),
            skill_factory=enabled("BOSSMAN_V3_SKILL_FACTORY"),
            recovery_kernel=enabled("BOSSMAN_V3_RECOVERY_KERNEL"),
            self_improvement=enabled("BOSSMAN_V3_SELF_IMPROVEMENT"),
            data_guardian=enabled("BOSSMAN_V3_DATA_GUARDIAN"),
            low_memory=_env_bool("BOSSMAN_LOW_MEMORY", False),
        )
