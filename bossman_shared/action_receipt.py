"""Канонический ActionReceipt (BOSS-V3-TRUTH-CLOSURE-003 §2).

Один контракт для всех побочных действий: что исполнение ЗАЯВЛЯЕТ (executor_status,
времена, idempotency_key, fencing_token) и что независимо НАБЛЮДАЛ верификатор
(observation_*, verification_*). Подпись — HMAC ключом процесса
(`bossman_shared.evidence`), signer ∈ TRUSTED_SIGNERS.

Инварианты, которые кодирует тип:
  SIGNED_RECEIPT != VERIFIED_SIDE_EFFECT — `verification_status` ставит только
  наблюдение пост-состояния; подпись лишь доказывает, кто и когда записал.
  TOOL_CALLED != SIDE_EFFECT_VERIFIED — executor_status="executed" ≠ VERIFIED.
  Свежесть: observed_at >= finished_at и observed_at > started_at; иначе
  улика устарела (STALE_EVIDENCE_REJECTED) и verified() == False.
Адаптирует существующие типы (V3 ExecutionReceipt/Observation/VerificationResult,
строку V2 tool_calls), не заменяет их.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from . import evidence as _ev

VERIFICATION_STATUSES = ("VERIFIED", "FAILED", "UNVERIFIED")
EXECUTOR_STATUSES = ("executed", "error", "denied", "rejected", "replayed", "unknown")


def _iso(dt: datetime | str | None) -> str:
    if dt is None:
        return ""
    if isinstance(dt, str):
        return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _parse(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def request_digest(tool: str, args: Mapping[str, Any]) -> str:
    body = json.dumps({"tool": tool, "args": {k: v for k, v in dict(args).items() if k != "expect"}},
                      sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]


@dataclass
class ActionReceipt:
    task_id: str
    step_id: str
    capability: str
    tool: str
    effect_type: str                     # READ_ONLY | IDEMPOTENT_WRITE | REVERSIBLE_WRITE | IRREVERSIBLE
    started_at: str
    finished_at: str
    receipt_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str = ""
    idempotency_key: str = ""
    fencing_token: int | None = None
    request_digest: str = ""
    executor_status: str = "unknown"
    executor_metadata: dict[str, Any] = field(default_factory=dict)
    observation_type: str = "none"       # post_state | receipt_readback | tool_result_only | none
    observation_ref: str = ""
    verification_status: str = "UNVERIFIED"
    verification_reason: str = ""
    observed_at: str = ""
    signer: str = ""
    signature: str = ""
    nonce: str = ""
    issued_at: str = ""

    # ------------------------------------------------------------ rules

    def fresh(self) -> tuple[bool, str]:
        """observed_at >= finished_at и observed_at > started_at. Без наблюдения — не свежо."""
        s, f, o = _parse(self.started_at), _parse(self.finished_at), _parse(self.observed_at)
        if o is None:
            return False, "no observation"
        if f is not None and o < f:
            return False, "STALE_EVIDENCE_REJECTED: observed before execution finished"
        if s is not None and o <= s:
            return False, "STALE_EVIDENCE_REJECTED: observed before/at execution start"
        return True, "fresh"

    def verified(self) -> bool:
        """VERIFIED только при наблюдении пост-состояния, свежем и не по одному лишь результату инструмента."""
        return (self.verification_status == "VERIFIED" and self.observation_type == "post_state"
                and self.fresh()[0])

    # ------------------------------------------------------- signing

    def signing_body(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("signature", None)
        return d

    def sign(self, *, signer: str) -> "ActionReceipt":
        f = _ev.sign_fields(self.signing_body(), signer=signer)
        self.signer, self.nonce, self.issued_at, self.signature = f["signer"], f["nonce"], f["issued_at"], f["sig"]
        return self

    def signature_valid(self) -> bool:
        if not self.signature:
            return False
        rec = self.signing_body()
        rec["sig"] = self.signature
        return _ev.verify_signed(rec)

    # ---------------------------------------------------------- codec

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ActionReceipt":
        fields = {k: raw.get(k) for k in cls.__dataclass_fields__ if k in raw}  # type: ignore[attr-defined]
        fields.setdefault("task_id", ""); fields.setdefault("step_id", ""); fields.setdefault("capability", "")
        fields.setdefault("tool", ""); fields.setdefault("effect_type", "READ_ONLY")
        fields.setdefault("started_at", ""); fields.setdefault("finished_at", "")
        if fields.get("executor_metadata") is None:
            fields["executor_metadata"] = {}
        return cls(**fields)

    @classmethod
    def from_v3(cls, *, task_id: str, step_id: str, action_type: str, effect_type: str, args: Mapping[str, Any],
                started_at, finished_at, observed_at, executor_status: str, observation_type: str,
                observation_ref: str, verification_status: str, verification_reason: str,
                idempotency_key: str = "", fencing_token: int | None = None, run_id: str = "",
                executor_metadata: Mapping[str, Any] | None = None) -> "ActionReceipt":
        return cls(task_id=str(task_id), step_id=str(step_id), capability=action_type, tool=action_type,
                   effect_type=str(effect_type), started_at=_iso(started_at), finished_at=_iso(finished_at),
                   run_id=str(run_id), idempotency_key=idempotency_key or f"{task_id}/{step_id}",
                   fencing_token=fencing_token, request_digest=request_digest(action_type, args),
                   executor_status=executor_status, executor_metadata=dict(executor_metadata or {}),
                   observation_type=observation_type, observation_ref=observation_ref,
                   verification_status=verification_status, verification_reason=verification_reason[:500],
                   observed_at=_iso(observed_at))
