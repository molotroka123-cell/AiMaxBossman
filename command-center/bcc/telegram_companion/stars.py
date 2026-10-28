"""Telegram Stars two-phase protocol and downtime reconciliation (BL-121).

Implements:
1. Two-phase quota reservation (RESERVE_QUOTA -> SEND_DELIVERABLE -> COMMIT_OR_REFUND).
2. Auto-refund of quota on deliverable delivery failure (`refund_quota_on_delivery_failure`),
   fail-safe without depleting user's bounded daily void allowance.
3. Background transaction history reconciliation after bot downtime > 24 hours
   (`reconcile_star_transactions_after_downtime`) using Telegram Bot API `getStarTransactions`.
"""
from __future__ import annotations

import inspect
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from bcc.pit.stars_ledger import (
    DAILY_CAP_PAID,
    DAY,
    DOCS30,
    MAX_VOIDS_PER_DAY,
    ORDER_TTL_SECONDS,
    TRIAL_DOCS,
    TRIAL_REQUESTS,
    Consumed,
    Package,
    PaymentOutcome,
    RefundOutcome,
    StarsLedger,
)

logger = logging.getLogger(__name__)

DOWNTIME_RECONCILE_THRESHOLD_SECONDS = 86400.0  # 24 hours


class StarReservationError(Exception):
    """Raised when star reservation cannot be completed or is rejected."""


@dataclass
class TwoPhaseStarReservation:
    """Two-phase quota reservation object."""

    ledger: StarsLedger
    usage_id: int
    pool: str
    ok: bool
    reason: str = ""
    committed: bool = False
    refunded: bool = False

    def commit(self) -> None:
        """Phase 2a: Deliverable successfully sent to client, finalize quota."""
        if self.refunded:
            raise StarReservationError("Cannot commit a refunded reservation")
        if not self.committed and self.usage_id > 0:
            self.ledger.finalize(self.usage_id)
            self.committed = True

    def refund(self, *, reason: str = "delivery_failure", now: float | None = None) -> bool:
        """Phase 2b: Deliverable failed to reach client, return reserved quota."""
        if self.committed:
            raise StarReservationError("Cannot refund an already committed reservation")
        if not self.refunded and self.usage_id > 0:
            res = refund_quota_on_delivery_failure(self.ledger, self.usage_id, now=now, reason=reason)
            self.refunded = True
            return res
        return False

    def __enter__(self) -> "TwoPhaseStarReservation":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is not None:
            # An unhandled error occurred during turn execution or delivery; auto-refund
            if not self.committed and not self.refunded:
                self.refund(reason=f"exception:{exc_type.__name__}")
        else:
            # Successful turn execution; auto-commit if not explicitly refunded or committed
            if not self.committed and not self.refunded:
                self.commit()

    async def __aenter__(self) -> "TwoPhaseStarReservation":
        return self.__enter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        return self.__exit__(exc_type, exc_val, exc_tb)


def reserve_quota_two_phase(
    ledger: StarsLedger,
    person_key: str,
    kind: str = "query",
    *,
    now: float | None = None,
    owner: bool = False,
) -> TwoPhaseStarReservation:
    """Phase 1: Atomically reserve one quota slot before processing model response."""
    now = time.time() if now is None else now
    consumed: Consumed = ledger.consume(person_key, kind, now=now, owner=owner)
    if not consumed.ok:
        return TwoPhaseStarReservation(
            ledger=ledger,
            usage_id=0,
            pool="",
            ok=False,
            reason=consumed.reason or "no_quota",
        )
    return TwoPhaseStarReservation(
        ledger=ledger,
        usage_id=consumed.usage_id,
        pool=consumed.pool,
        ok=True,
    )


def refund_quota_on_delivery_failure(
    ledger: StarsLedger,
    usage_id: int,
    *,
    now: float | None = None,
    reason: str = "delivery_failure",
) -> bool:
    """Auto-refund reserved quota if response failed to deliver (network error, Telegram outage).

    Uses bounded=False so legitimate network and Telegram delivery drops do not count
    against the user's daily void limit (MAX_VOIDS_PER_DAY).
    """
    if usage_id <= 0:
        return False
    now = time.time() if now is None else now
    # bounded=False gives back the request unconstrained by daily bounds
    voided = ledger.void(usage_id, now=now, bounded=False)
    logger.info("refund_quota_on_delivery_failure usage_id=%s reason=%s voided=%s", usage_id, reason, voided)
    return voided


async def reconcile_star_transactions_after_downtime(
    adapter: Any,
    ledger: StarsLedger,
    *,
    last_online_ts: float | None = None,
    downtime_threshold_s: float = DOWNTIME_RECONCILE_THRESHOLD_SECONDS,
    now: float | None = None,
) -> dict[str, Any]:
    """Background reconciliation after bot downtime > 24 hours (closing BL-121).

    Queries Telegram Stars ledger via `getStarTransactions`, checks for missed
    payments or refunds during downtime, and reconciles state idempotently.
    """
    now = time.time() if now is None else now
    downtime = 0.0
    if last_online_ts is not None and last_online_ts > 0:
        downtime = max(0.0, now - last_online_ts)
        if downtime < downtime_threshold_s:
            return {
                "reconciled": False,
                "downtime_s": round(downtime, 2),
                "reason": "downtime_below_threshold",
                "status": "SKIPPED",
            }

    # Fetch star transactions from Telegram Bot API via adapter
    resp: dict[str, Any] = {}
    if hasattr(adapter, "payment_call"):
        call_res = adapter.payment_call("getStarTransactions", {"offset": 0, "limit": 100})
        if inspect.isawaitable(call_res):
            resp = await call_res
        else:
            resp = call_res
    elif hasattr(adapter, "get_star_transactions"):
        call_res = adapter.get_star_transactions(offset=0, limit=100)
        if inspect.isawaitable(call_res):
            resp = await call_res
        else:
            resp = call_res
    elif callable(adapter):
        call_res = adapter("getStarTransactions", {"offset": 0, "limit": 100})
        if inspect.isawaitable(call_res):
            resp = await call_res
        else:
            resp = call_res
    else:
        return {
            "reconciled": False,
            "error": "adapter_lacks_payment_call",
            "status": "FAILED",
        }

    if not isinstance(resp, dict) or not resp.get("ok"):
        desc = resp.get("description", "TELEGRAM_API_ERROR") if isinstance(resp, dict) else "MALFORMED_RESPONSE"
        logger.warning("reconcile_star_transactions failed to fetch: %s", desc)
        return {
            "reconciled": False,
            "error": desc,
            "status": "FAILED",
        }

    raw_txs = resp.get("result") or resp.get("transactions") or []
    if isinstance(raw_txs, dict) and "transactions" in raw_txs:
        raw_txs = raw_txs["transactions"]
    if not isinstance(raw_txs, list):
        raw_txs = []

    recovered_payments = 0
    recovered_refunds = 0
    discrepancies: list[dict[str, Any]] = []

    for tx in raw_txs:
        if not isinstance(tx, dict):
            continue
        charge_id = str(tx.get("id") or tx.get("telegram_payment_charge_id") or "")
        if not charge_id:
            continue

        amount = int(tx.get("amount") or 0)
        source = tx.get("source") or {}
        user_id = 0
        if isinstance(source, dict):
            user_id = int(source.get("id") or 0)
        elif isinstance(source, int):
            user_id = source

        existing = ledger.payment(charge_id)

        # Negative amount or refund flag indicates a refund
        is_refund = amount < 0 or tx.get("is_refund") is True or "refund" in str(tx.get("source", "")).lower()

        if is_refund:
            if existing is None:
                # Refund of a payment not yet recorded locally or recorded before
                ledger.record_refund(charge_id, now=now)
                recovered_refunds += 1
            elif existing.get("status") != "refunded":
                ledger.record_refund(charge_id, now=now)
                recovered_refunds += 1
        else:
            if existing is None:
                # Missed payment during downtime! Reconcile it
                payload = str(tx.get("invoice_payload") or "reconciled_after_downtime")
                # We record with person_key mapped from user_id or transaction metadata
                person_key = str(tx.get("person_key") or f"tg_{user_id}" if user_id else f"charge_{charge_id}")
                outcome = ledger.record_payment(
                    person_key=person_key,
                    user_id=user_id,
                    payload=payload,
                    charge_id=charge_id,
                    currency="XTR",
                    total_amount=amount if amount > 0 else DOCS30.stars,
                    now=now,
                )
                if outcome.status in ("granted", "unmatched"):
                    recovered_payments += 1
                    discrepancies.append({
                        "charge_id": charge_id,
                        "action": "recovered_payment",
                        "status": outcome.status,
                    })

    return {
        "reconciled": True,
        "downtime_s": round(downtime, 2),
        "transactions_checked": len(raw_txs),
        "recovered_payments": recovered_payments,
        "recovered_refunds": recovered_refunds,
        "discrepancies": discrepancies,
        "status": "PASS",
    }
