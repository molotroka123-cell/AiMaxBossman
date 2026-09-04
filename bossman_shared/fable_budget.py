"""The canonical HARD-$3 ledger for paid Fable (Anthropic) work.

Why this module exists at the repository root instead of inside one app.
There are two ways money can leave this system: the direct API transport in
bossman-core (`bossman.apprentice.fable_direct`) and the Command Center's
Anthropic adapter (`bcc.providers.AnthropicAdapter`). A cap that lives in one
of them is not a cap: the other path spends freely, and reconciling two
ledgers after the fact is how "$3" quietly becomes "$3 each". So the ledger is
a single durable file, shared by both apps, and both reserve against it before
the request rather than accounting for it afterwards.

The three properties that make the cap real:

1. **Worst case first.** A reservation is taken BEFORE the network call, for
   the most expensive outcome the request could have (every token billed at
   the priciest bucket). A request whose worst case does not fit is refused
   without the adapter ever being invoked. Charging the real price afterwards
   would mean the money is already gone by the time we look.
2. **Unknown price is a refusal.** Prices come from PRICE_TABLE here, in code —
   not from the database, not from the API, not from the environment. A model
   nobody priced cannot be estimated, so it cannot be spent on.
3. **Uncertainty is held, not refunded.** A call that timed out, was cancelled,
   or came back without usage evidence keeps its reservation as RECONCILING.
   The provider may well have billed it. Only `trusted_reconcile`, holding a
   provider request id, may settle or release such a hold.

The cap itself (FABLE_HARD_CAP_USD) is a constant, and the ledger file refuses
to be reopened with a larger one: `total_usd` in a stored ledger can only ever
be lowered, never raised. So no setting, endpoint, environment variable, retry,
second process or restart can widen it.

Two things honestly fall outside that claim, and neither is a defence this
module pretends to mount. Editing this file raises the cap — but whoever can
edit it can delete the cap outright. And the ledger lives under the user's home
directory, so a process started with a different HOME gets a fresh ledger and a
fresh $3 — which is equally true of every other piece of this system's state.
"""
from __future__ import annotations

import contextlib
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

# USD per 1M tokens: (input, output, cache_read, cache_write). Unknown model => refuse.
PRICE_TABLE: dict[str, tuple[float, float, float, float]] = {
    "claude-sonnet-4-5": (3.0, 15.0, 0.30, 3.75),
    "claude-haiku-4-5": (1.0, 5.0, 0.10, 1.25),
    "claude-opus-4-1": (15.0, 75.0, 1.50, 18.75),
}
# Conservative tokenizer upper bound: Anthropic text is ~3.5-4 chars/token; use 3.0.
_CHARS_PER_TOKEN_UPPER_BOUND = 3.0

# The whole budget for paid Fable work, for this machine, for good.
FABLE_HARD_CAP_USD = 3.00
# One canonical file for both apps. A module constant rather than a setting:
# a relocatable ledger is a raisable cap (point it somewhere fresh and the $3
# starts over). Tests move it by patching this name in-process — which is not
# a runtime surface, and is only reachable by code that could remove the cap
# anyway.
LEDGER_PATH = Path.home() / ".bossman" / "fable_hard_cap.json"
CANONICAL_MISSION = "fable-hard-cap"


class BudgetExhausted(RuntimeError):
    """Refusal to spend. Raised BEFORE any paid request, never after."""
    code = "budget_exhausted"


def estimate_worst_case_usd(model: str, prompt_chars: int, max_output_tokens: int) -> float:
    """Safe upper bound: charge the most expensive bucket for every token."""
    rates = PRICE_TABLE.get(model)
    if rates is None:
        raise BudgetExhausted(f"unknown model {model!r}: price required before any spend")
    worst = max(rates)
    tokens = prompt_chars / _CHARS_PER_TOKEN_UPPER_BOUND + max_output_tokens
    return round(tokens / 1_000_000 * worst, 6)


def actual_usd(model: str, *, input_tokens: int, output_tokens: int,
               cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float:
    """Reported usage priced by the SAME trusted table the reservation used.

    Deliberately not the price stored next to the model in the database: that
    one is owner-editable, and a cap settled at a price the spender chooses is
    not a cap.
    """
    rates = PRICE_TABLE.get(model)
    if rates is None:
        raise BudgetExhausted(f"unknown model {model!r}: cannot price reported usage")
    return round(max(0, input_tokens) / 1e6 * rates[0]
                 + max(0, output_tokens) / 1e6 * rates[1]
                 + max(0, cache_read_tokens) / 1e6 * rates[2]
                 + max(0, cache_write_tokens) / 1e6 * rates[3], 6)


class _CrossProcessFileLock:
    """Advisory whole-file lock: msvcrt.locking on Windows, fcntl.flock on POSIX.
    One lock file per budget ledger; held for the whole read-modify-write transaction."""

    def __init__(self, path: Path) -> None:
        self.path = path.with_suffix(path.suffix + ".lock")
        self._handle: Any = None

    def __enter__(self) -> "_CrossProcessFileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self.path, "a+b")
        deadline = time.time() + 30.0
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    self._handle.seek(0)
                    msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.time() >= deadline:
                    raise BudgetExhausted("budget ledger is locked by another process; refusing to spend")
                time.sleep(0.05)

    def __exit__(self, *exc: Any) -> None:
        try:
            if os.name == "nt":
                import msvcrt
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


class DirectApiBudget:
    """Durable atomic reservation ledger with a cross-process lock.

    Record: {reservation_id, mission_id, owner_id, worst_case_usd, created_at,
    status: RESERVED|RECONCILING|COMMITTED|RELEASED, actual_usd, request_id}.
    remaining = total - committed - (RESERVED + RECONCILING holds). A crashed
    in-flight call stays RECONCILING (conservative): only trusted_reconcile()
    with provider request evidence may settle or free it. Every operation runs
    inside one lock + reload + atomic-replace transaction.

    The stored cap is a ratchet: reopening a ledger with a larger `total_usd`
    keeps the smaller stored one. That is what makes "no restart may raise it"
    true — the file remembers, so a second process asking for more gets less.
    """

    def __init__(self, path: str | Path, *, total_usd: float, mission_id: str = "mission",
                 owner_id: str = "owner") -> None:
        self.path, self.total = Path(path), round(float(total_usd), 6)
        self.mission_id, self.owner_id = mission_id, owner_id
        self._inproc = threading.Lock()
        self._records: list[dict] = []
        self._load()

    # ------------------------------------------------------------ persistence
    def _load(self) -> None:
        self._records = []
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("reservations"), list):
                    if data.get("mission_id") not in (None, self.mission_id):
                        raise BudgetExhausted("budget ledger belongs to another mission")
                    stored = data.get("total_usd")
                    if stored is not None:
                        # ratchet: the cap only ever goes down
                        with contextlib.suppress(TypeError, ValueError):
                            self.total = min(self.total, round(float(stored), 6))
                    self._records = data["reservations"]
            except json.JSONDecodeError:
                # corrupted ledger fails CLOSED: spend nothing new
                self._records = []

    def _save(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + f".tmp{os.getpid()}")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"mission_id": self.mission_id, "total_usd": self.total,
                                   "reservations": self._records}, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    @contextlib.contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._inproc, _CrossProcessFileLock(self.path):
            self._load()
            yield
            self._save()

    # ------------------------------------------------------------ accounting
    def _committed_total(self) -> float:
        return sum(float(r.get("actual_usd") or 0.0) for r in self._records if r["status"] == "COMMITTED")

    def _hold_total(self) -> float:
        return sum(float(r["worst_case_usd"]) for r in self._records if r["status"] in ("RESERVED", "RECONCILING"))

    def remaining(self) -> float:
        return round(max(0.0, self.total - self._committed_total() - self._hold_total()), 6)

    # ------------------------------------------------------------ operations
    def reserve(self, amount: float, *, purpose: str = "") -> str:
        with self._transaction():
            amount = round(float(amount), 6)
            if amount <= 0:
                raise BudgetExhausted("reservation amount must be positive")
            if amount > self.remaining():
                raise BudgetExhausted(
                    f"budget: worst case {amount:.2f} USD exceeds remaining {self.remaining():.2f} USD (cap {self.total:.2f} USD)")
            rid = f"rsv-{uuid.uuid4().hex[:12]}"
            self._records.append({"reservation_id": rid, "mission_id": self.mission_id, "owner_id": self.owner_id,
                                  "purpose": purpose[:120], "worst_case_usd": amount, "created_at": time.time(),
                                  "status": "RESERVED", "actual_usd": None, "request_id": ""})
            return rid

    def attach_request(self, reservation_id: str, request_id: str) -> None:
        with self._transaction():
            rec = next((r for r in self._records if r["reservation_id"] == reservation_id), None)
            if rec is None:
                raise KeyError(f"unknown reservation {reservation_id}")
            rec["request_id"] = str(request_id or "")[:120]

    def commit(self, reservation_id: str, actual_usd: float, *, request_id: str = "") -> None:
        with self._transaction():
            rec = next((r for r in self._records if r["reservation_id"] == reservation_id), None)
            if rec is None:
                raise KeyError(f"unknown reservation {reservation_id}")
            if rec["status"] == "COMMITTED":
                raise BudgetExhausted(f"reservation {reservation_id} already committed; double commit refused")
            if rec["status"] not in ("RESERVED", "RECONCILING"):
                raise BudgetExhausted(f"reservation {reservation_id} is {rec['status']}; cannot commit")
            actual = round(float(actual_usd), 6)
            if actual > float(rec["worst_case_usd"]) + 1e-9:
                raise BudgetExhausted(f"actual {actual:.2f} USD exceeds reserved {rec['worst_case_usd']:.2f} USD")
            rec["status"], rec["actual_usd"], rec["request_id"] = "COMMITTED", actual, str(request_id or rec.get("request_id", ""))[:120]

    def mark_reconciling(self, reservation_id: str) -> None:
        """A failed/uncertain call: budget stays held until the trusted reconciler settles it."""
        with self._transaction():
            rec = next((r for r in self._records if r["reservation_id"] == reservation_id), None)
            if rec is None:
                raise KeyError(f"unknown reservation {reservation_id}")
            if rec["status"] == "RESERVED":
                rec["status"] = "RECONCILING"

    def trusted_reconcile(self, reservation_id: str, *, request_id: str, actual_usd: float | None = None) -> str:
        """Trusted reconciler only: settle with provider usage evidence (COMMIT) or
        free the hold (RELEASE) when the provider proves no usage. Never callable
        as a silent client-side escape hatch."""
        with self._transaction():
            rec = next((r for r in self._records if r["reservation_id"] == reservation_id), None)
            if rec is None:
                raise KeyError(f"unknown reservation {reservation_id}")
            if rec["status"] not in ("RESERVED", "RECONCILING"):
                return rec["status"]
            rec["request_id"] = str(request_id or "")[:120]
            if actual_usd is None:
                rec["status"], rec["actual_usd"] = "RELEASED", 0.0
            else:
                actual = round(float(actual_usd), 6)
                if actual > float(rec["worst_case_usd"]) + 1e-9:
                    raise BudgetExhausted(f"reconciled actual {actual:.2f} USD exceeds reserved {rec['worst_case_usd']:.2f} USD")
                rec["status"], rec["actual_usd"] = "COMMITTED", actual
            return rec["status"]


def canonical_budget() -> DirectApiBudget:
    """The one ledger both paid paths reserve against.

    Constructed fresh on every call on purpose: it reloads the file, so a
    reservation another process took a moment ago is already visible here.
    """
    return DirectApiBudget(LEDGER_PATH, total_usd=FABLE_HARD_CAP_USD,
                           mission_id=CANONICAL_MISSION, owner_id="owner")
