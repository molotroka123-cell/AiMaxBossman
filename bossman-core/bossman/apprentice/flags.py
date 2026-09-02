"""Feature flags for the Universal Computer Apprentice. All default OFF.

Convention matches learning_guard.autonomy_trainer.enabled(): env var set to
1/true/yes enables. Flags are narrow: one capability each."""
from __future__ import annotations

import os

MASTER = "BOSSMAN_UNIVERSAL_COMPUTER_APPRENTICE"
SKILL_RECORDING = "BOSSMAN_SKILL_RECORDING"
SKILL_SHADOW_REPLAY = "BOSSMAN_SKILL_SHADOW_REPLAY"
SKILL_PROMOTION = "BOSSMAN_SKILL_PROMOTION"
CLAUDE_CODE_FALLBACK = "BOSSMAN_CLAUDE_CODE_FALLBACK"
EXTERNAL_OUTREACH = "BOSSMAN_EXTERNAL_OUTREACH"
# own proposals (doc section 4)
DRY_RUN_PREVIEW = "BOSSMAN_APPRENTICE_DRY_RUN_PREVIEW"
CHECKPOINT_RESUME = "BOSSMAN_APPRENTICE_CHECKPOINT_RESUME"
ANCHOR_REDUNDANCY = "BOSSMAN_APPRENTICE_ANCHOR_REDUNDANCY"
LESSON_PRECHECK = "BOSSMAN_APPRENTICE_LESSON_PRECHECK"
EVIDENCE_EXPORT = "BOSSMAN_APPRENTICE_EVIDENCE_EXPORT"

ALL_FLAGS: tuple[str, ...] = (
    MASTER, SKILL_RECORDING, SKILL_SHADOW_REPLAY, SKILL_PROMOTION, CLAUDE_CODE_FALLBACK,
    EXTERNAL_OUTREACH, DRY_RUN_PREVIEW, CHECKPOINT_RESUME, ANCHOR_REDUNDANCY, LESSON_PRECHECK,
    EVIDENCE_EXPORT,
)


def enabled(flag: str) -> bool:
    return os.environ.get(flag, "").strip().lower() in ("1", "true", "yes")


def master_enabled() -> bool:
    return enabled(MASTER)


def snapshot() -> dict[str, bool]:
    return {f: enabled(f) for f in ALL_FLAGS}
