"""Мост к `bossman_shared.evidence` (подписанные улики, EH-01).

Без общего пакета подписать нельзя и проверить нельзя — fail-closed (INV-6)."""
from __future__ import annotations

from typing import Any, Mapping

import bossman._shared  # noqa: F401  — bootstrap пути к repo-root bossman_shared

try:
    from bossman_shared import evidence as _ev
    AVAILABLE = True
except Exception:  # noqa: BLE001
    _ev = None  # type: ignore[assignment]
    AVAILABLE = False

TRUSTED_SIGNERS = frozenset(_ev.TRUSTED_SIGNERS) if _ev else frozenset()
JOURNAL_SIGNER = "bossman_v3.memory.journal"
VERIFIER_SIGNER = "bossman_v3.verifier"


def sign_fields(payload: Mapping[str, Any], *, signer: str) -> dict[str, str]:
    if _ev is None:
        raise RuntimeError("bossman_shared.evidence недоступен — подписать улику нельзя")
    return _ev.sign_fields(payload, signer=signer)


def verify_signed(record: Mapping[str, Any]) -> bool:
    return bool(_ev and _ev.verify_signed(record))
