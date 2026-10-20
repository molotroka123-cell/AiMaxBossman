"""Long-run budget profile: bounded, owner-enabled, per-provider daily caps.

Owner 2026-10-11: "lift the limits for long runs" -- but not to infinity.  The
default profile is unchanged (``mission_budget.DEFAULT_*``, free-only policy in
``provider_governance``).  This module adds an OPT-IN profile that:

  * is switched on only by an owner-written file ``<data>/autonomy/long_run.json``
    (``python -m bcc.long_run enable``); nothing in the product creates it;
  * admits exactly four paid model families through the free-only gate:
    Mistral Large 4 (api.mistral.ai), GLM 5.3 Flash, Haiku 5.5 and
    DeepSeek V4.1 Flash (OpenRouter);
  * puts a per-provider DAILY cap on each (default and HARD maximum EUR 10 --
    the file can lower it, never raise it above ``HARD_MAX_EUR_PER_PROVIDER``);
  * reserves the worst case BEFORE the network call and settles after it, in
    an append-only ledger (``<data>/autonomy/long_run/ledger-YYYY-MM-DD.jsonl``);
    an unsettled reservation counts as spent (the provider may have billed);
  * refuses LOUDLY when a cap would be crossed (ProviderError kind="budget",
    a ``refusal`` ledger row, an ERROR log line);
  * honours the global kill file ``<data>/autonomy/STOP`` (and the owner's
    ``<data>/computer/STOP``) on every paid call;
  * lifts the per-mission token / money ceilings to larger but FINITE values
    (``mission_budget.LONG_RUN_*``) only for tasks that ask for it.

Currency: provider prices are USD; the cap is EUR.  1 USD is booked as 1 EUR,
which over-counts (EUR > USD) and so errs on the safe side.

This module imports nothing from the rest of ``bcc`` except the tiny autonomy
file-lock / STOP helpers, so governance, the budget guard and the CLI can all
use it without cycles.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from .autonomy.journal import atomic_write_bytes, file_lock
from .autonomy import stop as _stop

log = logging.getLogger("bcc.long_run")

PROFILE_FILE = "long_run.json"
HARD_MAX_EUR_PER_PROVIDER = 10.0
DEFAULT_CAP_EUR = 10.0
USD_PER_EUR_BOOKED = 1.0           # conservative: 1 USD is booked as 1 EUR

#: family -> (display name, hosts the model may be called on, name test)
FAMILIES: dict[str, dict[str, Any]] = {
    "mistral": {"label": "Mistral Large 4", "hosts": ("api.mistral.ai",),
                "tokens": ("mistral-large-4",)},
    "glm": {"label": "GLM 5.3 Flash", "hosts": ("openrouter.ai",),
            "tokens": ("glm-5.3-flash",)},
    "haiku": {"label": "Haiku 5.5", "hosts": ("openrouter.ai",),
              "tokens": ("claude-haiku-5.5", "claude-5.5-haiku", "claude-haiku-5-5")},
    # Owner 2026-10-17: DeepSeek V4.1 Flash, its OWN line, default EUR 2/day (not 10).
    "deepseek": {"label": "DeepSeek V4.1 Flash", "hosts": ("openrouter.ai",),
                 "tokens": ("deepseek/deepseek-v4.1-flash",), "default_cap_eur": 2.0},
}
PROVIDERS = tuple(FAMILIES)


def default_cap(provider: str) -> float:
    """Per-family default cap; a family may default LOWER than DEFAULT_CAP_EUR."""
    return min(float(FAMILIES[provider].get("default_cap_eur", DEFAULT_CAP_EUR)),
               HARD_MAX_EUR_PER_PROVIDER)


class LongRunRefused(Exception):
    """Raised by reserve(); governance converts it to a ProviderError(kind=budget)."""


# ------------------------------------------------------------------ locations

def data_dir() -> Path:
    from .config import _data_dir
    return Path(_data_dir())


def autonomy_dir(root: str | os.PathLike | None = None) -> Path:
    if root is not None:
        return Path(root)
    explicit = os.environ.get("BOSSMAN_AUTONOMY_ROOT", "").strip()
    return Path(explicit).expanduser() if explicit else data_dir() / "autonomy"


def profile_path(root: str | os.PathLike | None = None) -> Path:
    return autonomy_dir(root) / PROFILE_FILE


def _ledger_dir(root) -> Path:
    return autonomy_dir(root) / "long_run"


def _day(clock: Callable[[], float] = time.time) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(clock()))


# ------------------------------------------------------------------ profile

def load_profile(root: str | os.PathLike | None = None) -> dict:
    """The owner's profile with caps clamped; {"enabled": False} when absent or broken.

    Fail closed: a malformed file, a non-true ``enabled`` or a bad cap never
    widens anything; a bad cap falls back to the default (not to "unlimited")
    and every cap is clamped to ``HARD_MAX_EUR_PER_PROVIDER``."""
    out = {"enabled": False, "caps_eur_per_day": {p: default_cap(p) for p in PROVIDERS}}
    try:
        raw = json.loads(profile_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    if not isinstance(raw, dict) or raw.get("enabled") is not True:
        return out
    out["enabled"] = True
    caps = raw.get("caps_eur_per_day")
    if isinstance(caps, dict):
        for p in PROVIDERS:
            v = caps.get(p)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v < 0:
                continue
            out["caps_eur_per_day"][p] = min(float(v), HARD_MAX_EUR_PER_PROVIDER)
    return out


def enabled(root: str | os.PathLike | None = None) -> bool:
    return load_profile(root)["enabled"]


def write_profile(*, enable: bool, caps: dict | None = None,
                  root: str | os.PathLike | None = None) -> dict:
    """Owner action (CLI). Caps above the hard maximum are clamped, not stored."""
    body = {"enabled": bool(enable),
            "caps_eur_per_day": {p: min(float((caps or {}).get(p, default_cap(p))),
                                        HARD_MAX_EUR_PER_PROVIDER) for p in PROVIDERS},
            "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "note": "owner-written; delete the file or set enabled=false to return to the default profile"}
    atomic_write_bytes(profile_path(root), json.dumps(body, indent=1, sort_keys=True).encode("utf-8"))
    return body


# ------------------------------------------------------------------ classification

def classify(model_name: str, base_url: str = "") -> str | None:
    """Family key for a PAID approved model, else None.

    Both the model name and the host must match: a lookalike name on another
    host is not approved, and a ``:free`` variant is not a paid call (free
    models never touch this ledger)."""
    name = str(model_name or "").strip().lower()
    if not name or name.endswith(":free"):
        return None
    host = (urlsplit(base_url or "").hostname or "").lower()
    for key, fam in FAMILIES.items():
        if host in fam["hosts"] and any(tok in name for tok in fam["tokens"]):
            return key
    return None


def allows_paid(model_name: str, base_url: str, root=None) -> bool:
    """Does the active profile admit this model through the free-only gate?"""
    return classify(model_name, base_url) is not None and enabled(root)


# ------------------------------------------------------------------ STOP

def stop_reason(root: str | os.PathLike | None = None) -> str:
    """'' when paid long-run work may continue, else why not.

    The autonomy STOP file sits at ``<autonomy root>/STOP`` (= ``<data>/autonomy/STOP``
    by default); the owner's global STOP at ``<data>/computer/STOP``."""
    ad = autonomy_dir(root)
    data = data_dir() if root is None else ad.parent
    return _stop.stop_reason(ad, data)


def request_stop(reason: str = "long-run kill", by: str = "owner",
                 root: str | os.PathLike | None = None) -> dict:
    return _stop.request_stop(autonomy_dir(root), by=by, reason=reason)


def clear_stop(root: str | os.PathLike | None = None) -> bool:
    return _stop.clear_stop(autonomy_dir(root))


# ------------------------------------------------------------------ ledger

class LongRunLedger:
    """Append-only per-day JSONL ledger with cross-process locking."""

    def __init__(self, root: str | os.PathLike | None = None, *,
                 clock: Callable[[], float] = time.time):
        self.root = root
        self.clock = clock

    # -- io
    def _path(self, day: str | None = None) -> Path:
        return _ledger_dir(self.root) / f"ledger-{day or _day(self.clock)}.jsonl"

    def _lock(self) -> Path:
        return _ledger_dir(self.root) / "ledger.lock"

    def _rows(self, day: str | None = None) -> list[dict]:
        out: list[dict] = []
        try:
            for line in self._path(day).read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    out.append(row)
        except OSError:
            pass
        return out

    def _append(self, row: dict) -> None:
        p = self._path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    # -- accounting
    @staticmethod
    def _spent_by_provider(rows: list[dict]) -> dict[str, float]:
        reserved: dict[str, dict] = {}
        spent = {p: 0.0 for p in PROVIDERS}
        for r in rows:
            if r.get("kind") == "reserve":
                reserved[r["id"]] = r
            elif r.get("kind") == "commit" and r.get("id") in reserved:
                reserved[r["id"]]["_settled"] = float(r.get("eur") or 0.0)
            elif r.get("kind") == "release" and r.get("id") in reserved:
                reserved[r["id"]]["_settled"] = 0.0     # proven not billed (refused before send)
        for r in reserved.values():
            amount = r.get("_settled", float(r.get("eur") or 0.0))   # unsettled = worst case
            if r.get("provider") in spent:
                spent[r["provider"]] += amount
        return spent

    def spent_today(self) -> dict[str, float]:
        return self._spent_by_provider(self._rows())

    def caps(self) -> dict[str, float]:
        return load_profile(self.root)["caps_eur_per_day"]

    # -- API
    def reserve(self, provider: str, worst_case_eur: float, *, model: str = "",
                purpose: str = "") -> str:
        if provider not in PROVIDERS:
            raise LongRunRefused(f"LONG-RUN REFUSED: unknown provider family {provider!r}")
        if not enabled(self.root):
            raise LongRunRefused("LONG-RUN REFUSED: the long_run profile is not enabled "
                                 "(owner file autonomy/long_run.json)")
        why = stop_reason(self.root)
        if why:
            self._refuse(provider, model, 0.0, f"STOP active: {why}")
            raise LongRunRefused(f"LONG-RUN REFUSED: {why}; paid calls are blocked until the STOP "
                                 "file is removed")
        try:
            worst = float(worst_case_eur)
        except (TypeError, ValueError):
            worst = float("nan")
        if not math.isfinite(worst):
            # max(0.0, nan) is 0.0: an unknown worst case used to be booked as a FREE call
            # and passed every cap. Unknown cost is refused, never assumed to be zero.
            msg = (f"LONG-RUN REFUSED: the worst-case cost of this {FAMILIES[provider]['label']} call "
                   f"is unknown ({worst_case_eur!r}); the call was NOT made.")
            self._refuse(provider, model, 0.0, msg)
            raise LongRunRefused(msg)
        worst = max(0.0, worst)
        with file_lock(self._lock()):
            cap = self.caps()[provider]
            spent = self._spent_by_provider(self._rows())[provider]
            if spent + worst > cap + 1e-9:
                msg = (f"LONG-RUN CAP HIT: {FAMILIES[provider]['label']} would reach "
                       f"EUR {spent + worst:.4f} today (spent EUR {spent:.4f} + this call's worst case "
                       f"EUR {worst:.4f}) against the daily cap EUR {cap:.2f}. The call was NOT made.")
                self._refuse(provider, model, worst, msg)
                raise LongRunRefused(msg)
            rid = uuid.uuid4().hex
            self._append({"id": rid, "kind": "reserve", "provider": provider, "model": model,
                          "eur": round(worst, 6), "purpose": purpose[:120],
                          "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.clock()))})
            return rid

    def commit(self, rid: str, actual_eur: float, *, tokens_in: int = 0, tokens_out: int = 0) -> None:
        with file_lock(self._lock()):
            self._append({"id": rid, "kind": "commit", "eur": round(max(0.0, float(actual_eur)), 6),
                          "tokens_in": int(tokens_in), "tokens_out": int(tokens_out),
                          "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.clock()))})

    def release(self, rid: str, why: str = "") -> None:
        """Only for a call proven NOT to have left the machine."""
        with file_lock(self._lock()):
            self._append({"id": rid, "kind": "release", "why": why[:200],
                          "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.clock()))})

    def _refuse(self, provider: str, model: str, eur: float, why: str) -> None:
        log.error("%s", why)
        try:
            self._append({"id": uuid.uuid4().hex, "kind": "refusal", "provider": provider,
                          "model": model, "eur": round(eur, 6), "why": why[:400],
                          "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.clock()))})
        except OSError:      # pragma: no cover - logging only
            pass

    # -- report
    def report(self, days: int = 7) -> dict:
        prof = load_profile(self.root)
        today = _day(self.clock)
        spent = self.spent_today()
        rows = self._rows()
        refusals = [r for r in rows if r.get("kind") == "refusal"]
        history: dict[str, dict[str, float]] = {}
        d = Path(_ledger_dir(self.root))
        for path in sorted(d.glob("ledger-*.jsonl"))[-max(1, days):]:
            day = path.stem.removeprefix("ledger-")
            history[day] = {k: round(v, 6) for k, v in self._spent_by_provider(self._rows(day)).items()}
        return {
            "profile_enabled": prof["enabled"], "day": today,
            "stop": stop_reason(self.root) or None,
            "hard_max_eur_per_provider": HARD_MAX_EUR_PER_PROVIDER,
            "providers": {p: {"label": FAMILIES[p]["label"],
                              "cap_eur": prof["caps_eur_per_day"][p],
                              "spent_eur": round(spent[p], 6),
                              "remaining_eur": round(max(0.0, prof["caps_eur_per_day"][p] - spent[p]), 6),
                              "calls": sum(1 for r in rows if r.get("kind") == "reserve"
                                           and r.get("provider") == p)}
                          for p in PROVIDERS},
            "refusals_today": len(refusals),
            "last_refusal": refusals[-1] if refusals else None,
            "history_eur": history,
        }


def _usd_per_token(model: dict) -> tuple[float, float]:
    """Registry rows hold USD per 1M tokens; the economy orchestrator's synthetic
    spec holds USD per token and says so with price_unit="per_token"."""
    scale = 1.0 if model.get("price_unit") == "per_token" else 1e-6
    return (float(model.get("price_in") or 0.0) * scale, float(model.get("price_out") or 0.0) * scale)


def estimate_worst_case_eur(model: dict, messages: list, max_tokens: int, tools: Any = None) -> float:
    """Upper bound: one token per character sent, every output token at the output price."""
    payload: list[Any] = [messages]
    if tools:
        payload.append(tools)
    chars = len(json.dumps(payload, ensure_ascii=False, default=str))
    pin, pout = _usd_per_token(model)
    return (chars * pin + int(max_tokens) * pout) / USD_PER_EUR_BOOKED


def actual_eur(model: dict, result: Any) -> float | None:
    """Provider-reported cost when present, else tokens x configured price; None = no evidence."""
    meta = getattr(result, "provider_meta", None) or {}
    usage = meta.get("usage") if isinstance(meta, dict) else None
    reported = usage.get("cost") if isinstance(usage, dict) else None
    if isinstance(reported, (int, float)) and not isinstance(reported, bool) and reported >= 0:
        return float(reported) / USD_PER_EUR_BOOKED
    tin, tout = int(getattr(result, "tokens_in", 0) or 0), int(getattr(result, "tokens_out", 0) or 0)
    if not (tin or tout):
        return None
    # Same units as the reservation (registry prices are USD per 1M tokens). The raw price was
    # multiplied by tokens here, booking 10^6x the real cost: one Mistral call without a reported
    # cost "spent" thousands of EUR and closed its line for the rest of the day.
    pin, pout = _usd_per_token(model)
    return (tin * pin + tout * pout) / USD_PER_EUR_BOOKED


# ------------------------------------------------------------------ CLI

def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m bcc.long_run",
                                 description="Long-run budget profile (owner-enabled, capped).")
    ap.add_argument("--root", default=None, help="autonomy dir (default: <data>/autonomy)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    en = sub.add_parser("enable", help="write the owner profile")
    for p in PROVIDERS:
        en.add_argument(f"--{p}", type=float, default=default_cap(p),
                        help=f"EUR/day cap for {FAMILIES[p]['label']} (max {HARD_MAX_EUR_PER_PROVIDER})")
    sub.add_parser("disable", help="return to the default profile")
    sub.add_parser("status", help="profile, STOP and today's ledger as JSON")
    sub.add_parser("report", help="same as status plus 7-day history")
    st = sub.add_parser("stop", help="write the global kill file")
    st.add_argument("--reason", default="owner long-run kill")
    sub.add_parser("resume", help="remove the autonomy STOP file")
    args = ap.parse_args(argv)
    root = args.root
    if args.cmd == "enable":
        body = write_profile(enable=True, caps={p: getattr(args, p) for p in PROVIDERS}, root=root)
        print(json.dumps(body, indent=1))
    elif args.cmd == "disable":
        body = write_profile(enable=False, root=root)
        print(json.dumps(body, indent=1))
    elif args.cmd in ("status", "report"):
        print(json.dumps(LongRunLedger(root).report(days=7 if args.cmd == "report" else 1),
                         indent=1, ensure_ascii=False))
    elif args.cmd == "stop":
        print(json.dumps(request_stop(args.reason, root=root)))
    elif args.cmd == "resume":
        print(json.dumps({"cleared": clear_stop(root)}))
    return 0


if __name__ == "__main__":      # pragma: no cover
    raise SystemExit(main())
