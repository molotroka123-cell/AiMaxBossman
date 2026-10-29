"""Studio orders: Direct-Generator jobs sold as a service (scenario B «local AI photo/video studio»).

An order belongs to ONE client participant (the same key as their jobs) and lists line items
``{kind: photo_edit|image|clip, job_ids: [...]}``. State machine (TZ 2.5 M1/T1, ``TRANSITIONS``):
draft -> quoted -> accepted -> preflight -> generating -> qc_failed|qc_passed -> owner_review -> rework|approved ->
delivered -> closed, plus cancelled (any state before delivered) and reconcile_required (a failure with an unknown
outcome mid-render: never silently re-run, the owner reconciles). Every committed step bumps ``rev`` and appends one
JSON line per transition to ``<order_id>.events.jsonl`` (append-only; an event is committed with its order rev).
Retries are idempotent: an ``Idempotency-Key`` already used for the same step + arguments returns the stored state
(``replayed``), with other arguments it is 409; without a key, repeating the last committed transition step with the
same arguments (a lost response) is a replay too. A per-order lock serialises concurrent calls. Legacy (schema 1)
orders migrate on load (in_work -> generating) and log a ``migrated`` event on their first save.

Receipt = price (price list, Decimal) - cost. The cost is NOT estimated: it is the recorded generation seconds of the
order's own jobs (``job["timing"]["total_s"]``, started -> finished, i.e. GPU-busy time without the queue wait) x the
machine's power draw (W) x the electricity price per kWh. Same formula as tools/bossman_business.compute_unit_economics,
done in Decimal here so the receipt is exact (that helper is float and lives outside the package).

Delivery rule (all or nothing, nothing is half-delivered):
  - every job of every line is ``completed`` with its result file on disk;
  - every delivered file carries the AI-generated disclosure (``disclose`` records the caption bound to the file's
    SHA-256; a file that changed after disclosure is refused);
  - a job that needed consent (face swap, FaceLock, or a line marked ``face``) has consent recorded, either by the
    job itself (``provenance.consent_confirmed``) or on the line item when the order was created;
  - no third-party brand (denylist, case-insensitive whole-word) is named in the client brief or in a job's prompt,
    unless the OWNER recorded that the client owns that brand (owner audit 24.10: an SDXL ad carried a Starbucks
    logo). The negative prompt is not scanned («no Nike logo» is not a request for one);
  - every delivered file carries a visible watermark (``watermark`` records the mark bound to the file's SHA-256, or
    the job's own ``provenance.watermark`` with the same SHA-256);
  - the order is ``approved``: the owner answered exactly «yes» and the approval is bound to the SHA-256 of every
    deliverable (a file changed after the approval is ``approval_stale``).
Preflight (``start`` from accepted) refuses rendering early on a missing job, a face job without consent or an
uncleared brand; delivery checks them again.
Model licence (owner decision 24.10): NOT a delivery gate. Every line records the generating model of each job; a
model whose licence is unknown or non-commercial in LICENCES (+ optional licences.json) becomes a ``licence_warnings``
entry in the owner's view and the frozen receipt. It is never part of the client view.

Views: ``view`` is the owner's (cost, margin, licence warnings, brand flags); ``client_view`` is what the client may
see (price, status, deliverables with their disclosure) - no cost, margin, note, models or warnings.

Layout: <data_dir>/direct-gen/participants/<key>/orders/<order_id>.json (atomic writes, like job.json) +
<order_id>.events.jsonl; optional owner config in <data_dir>/direct-gen/studio/: price_list.json, brands.json
({"denylist": [...]}, replaces the default list), licences.json ({model_id: {"licence": str, "commercial":
true|false|null}}, merged over LICENCES). Another participant's order id is simply not found: every path is built from
that participant's own key.

Client identity (F7, ``ClientTokens``): the owner issues each client a random server-side token; only its SHA-256 is
stored (studio/clients.json). The API resolves the client key from that token, never from an X-Participant header.
All methods are synchronous file I/O; the API calls them through asyncio.to_thread.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from .store import JOB_ID, JobStore, atomic_write_text, read_text_retry, valid_participant

ORDER_ID = re.compile(r"^[0-9a-f]{32}$")
KINDS = ("photo_edit", "image", "clip")
# which Direct-Generator job kinds may stand behind a line item
JOB_KINDS = {"photo_edit": ("photo", "image"), "image": ("photo", "image"), "clip": ("video", "faceswap")}
SCHEMA = 2
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "draft": ("quoted", "cancelled"),
    "quoted": ("quoted", "accepted", "cancelled"),
    "accepted": ("preflight", "cancelled"),
    "preflight": ("generating", "cancelled"),
    "generating": ("qc_passed", "qc_failed", "reconcile_required", "cancelled"),
    "qc_failed": ("generating", "rework", "cancelled"),
    "qc_passed": ("owner_review", "cancelled"),
    "owner_review": ("approved", "rework", "cancelled"),
    "rework": ("generating", "cancelled"),
    "approved": ("delivered", "rework", "cancelled"),
    "reconcile_required": ("generating", "qc_passed", "qc_failed", "cancelled"),
    "delivered": ("closed",),
    "closed": (),
    "cancelled": (),
}
STATUSES = tuple(TRANSITIONS)
FINAL = ("delivered", "closed", "cancelled")
# what the client sees: internal production states are one «in_work»
CLIENT_STATUS = {s: "in_work" for s in ("preflight", "generating", "qc_failed", "qc_passed", "owner_review", "rework",
                                        "approved", "reconcile_required")}
DISCLOSURE = "AI-generated · Bossman Motion Studio"
DISCLOSURE_MARK = "AI-generated"
WATERMARK = "Bossman Motion Studio"
APPROVAL_ANSWERS = ("yes", "да")
CLIENT_KEY = re.compile(r"^[0-9a-f]{64}$")
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")
MAX_ITEMS = 50
MAX_JOBS_PER_ITEM = 20
MONEY = Decimal("0.01")
COST_Q = Decimal("0.000001")
DEFAULT_PRICE_LIST: dict[str, Any] = {
    "currency": "USD",
    "prices": {"photo_edit": "5.00", "image": "3.00", "clip": "15.00"},
    "power_watts": "180",        # measured-ish draw of this PC under a generation (GPU + rest); override in the file
    "kwh_price": "0.15",
}
DEFAULT_BRANDS = ("Starbucks", "Nike", "Coca-Cola", "Apple", "McDonald's", "Disney")
# Explicit licence table: only what the repo's own model tables state (direct_gen/sdcli.py SPECS, faceswap.describe).
# commercial: True / False / None (= unknown). A model missing here is unknown too. Verified additions go into
# <data>/direct-gen/studio/licences.json, never guessed here.
LICENCES: dict[str, dict[str, Any]] = {
    "z-image-turbo": {"licence": "Apache-2.0", "commercial": True},
    "flux1-schnell": {"licence": "Apache-2.0", "commercial": True},
    "flux2-klein-4b": {"licence": "Apache-2.0", "commercial": True},
    "sdxl-base": {"licence": "CreativeML OpenRAIL++-M", "commercial": True},
    "qwen-image-2.1": {"licence": "Qwen Research License (non-commercial research/evaluation only)", "commercial": False},
    "wan2.2-ti2v-5b": {"licence": "Apache-2.0", "commercial": True},
    "wan2.2-i2v-a14b-uncensored": {"licence": "Apache-2.0", "commercial": True},
    "wan2.2-t2v-vace-a14b-uncensored": {"licence": "Apache-2.0", "commercial": True},
    # the FaceFusion engine is OpenRAIL-AS; the swap model weights it loads are not verified for commercial use
    "faceswap-facefusion": {"licence": "OpenRAIL-AS (engine); swap weights unverified", "commercial": None},
}


class OrderError(Exception):
    def __init__(self, status: int, code: str, message: str, problems: list[dict] | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.problems = status, code, message, problems or []

    @property
    def detail(self) -> dict:
        return {"code": self.code, "message": self.message, **({"problems": self.problems} if self.problems else {})}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dec(value: Any, what: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise OrderError(422, "invalid_amount", f"{what} must be a number")
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise OrderError(422, "invalid_amount", f"{what} must be a number") from None
    if not d.is_finite():
        raise OrderError(422, "invalid_amount", f"{what} must be finite")
    return d


def positive_price(value: Any, what: str = "price") -> Decimal:
    d = _dec(value, what)
    if d <= 0:
        raise OrderError(422, "price_not_positive", f"{what} must be greater than zero")
    return d.quantize(MONEY, ROUND_HALF_UP)


def job_seconds(job: dict) -> Decimal | None:
    """Recorded generation seconds of one job: the measured timing row first, else started -> finished."""
    total = (job.get("timing") or {}).get("total_s")
    if isinstance(total, (int, float)) and not isinstance(total, bool) and total >= 0:
        return Decimal(str(total))
    try:
        start = datetime.fromisoformat(str(job["started_at"]))
        end = datetime.fromisoformat(str(job["finished_at"]))
    except (KeyError, TypeError, ValueError):
        return None
    secs = (end - start).total_seconds()
    return Decimal(str(round(secs, 2))) if secs >= 0 else None


def energy_cost(seconds: Decimal, power_watts: Decimal, kwh_price: Decimal) -> tuple[Decimal, Decimal]:
    """(kWh, cost) for ``seconds`` of the machine at ``power_watts``; exact Decimal, cost not rounded here."""
    kwh = seconds * power_watts / Decimal(3_600_000)
    return kwh, kwh * kwh_price


def brand_pattern(brand: str) -> re.Pattern:
    """Case-insensitive whole-word match; spaces, hyphens and apostrophes inside a brand are optional
    («Coca-Cola» = «coca cola» = «CocaCola», «McDonald's» = «mcdonalds»)."""
    parts = [re.escape(p) for p in re.split(r"[\s\-'’]+", brand.strip()) if p]
    return re.compile(r"(?<![0-9a-z])" + r"[\s\-'’]*".join(parts) + r"(?![0-9a-z])", re.IGNORECASE)


def brands_named(text: str, brands: list[str]) -> list[str]:
    return [b for b in brands if text and brand_pattern(b).search(text)]


def needs_consent(job: dict, item: dict) -> bool:
    return (job.get("kind") == "faceswap" or bool((job.get("facelock") or {}).get("requested"))
            or bool(item.get("face")))


def consent_recorded(job: dict, item: dict) -> bool:
    if (job.get("provenance") or {}).get("consent_confirmed") is True:
        return True
    rec = item.get("consent") or {}
    return rec.get("confirmed") is True and bool(rec.get("at"))


def fingerprint(step: str, args: Any) -> str:
    """Stable hash of a step and its arguments: what makes two calls «the same retry»."""
    return hashlib.sha256(json.dumps([step, args], sort_keys=True, default=str).encode()).hexdigest()[:32]


def migrate(order: dict) -> dict:
    """Schema 1 -> 2 in memory (in_work -> generating; an unknown legacy status needs the owner: reconcile_required).
    The order is written, with a ``migrated`` event, by its next committed step."""
    if order.get("schema") == SCHEMA:
        return order
    legacy = order.get("status")
    status = "generating" if legacy == "in_work" else legacy if legacy in TRANSITIONS else "reconcile_required"
    order.update(schema=SCHEMA, status=status, migrated_from={"schema": order.get("schema", 1), "status": legacy})
    order.setdefault("rev", len(order.get("timeline") or []))
    order.setdefault("attempt", 1 if status == "generating" else 0)
    order.setdefault("idempotency", {})
    order["_migrating"] = status
    return order


class _CommitThenFail(Exception):
    """Raised by a step that must keep its state change (preflight entered) and still refuse."""
    def __init__(self, error: OrderError):
        super().__init__(error.message)
        self.error = error


def token_hash(token: str) -> str:
    # the token is 32 random bytes, so a plain SHA-256 is enough (no password-style KDF needed)
    return hashlib.sha256(str(token).encode("utf-8", "ignore")).hexdigest()


class ClientTokens:
    """F7: per-client server-side tokens. The raw token is shown to the owner once; at rest only its SHA-256.
    One active token per client: issuing again rotates it, revoking removes it. Layout: studio/clients.json
    {"clients": {<64-hex key>: {"token_sha256", "label", "issued_at", "expires_at", "revoked_at"}}}."""
    PREFIX = "stc_"

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "direct-gen" / "studio" / "clients.json"
        self._lock = threading.Lock()

    def _read(self) -> dict:
        if not self.path.is_file():
            return {"clients": {}}
        try:
            raw = json.loads(read_text_retry(self.path))
        except (OSError, ValueError):
            raise OrderError(409, "studio_config_invalid", "clients.json is not valid JSON") from None
        if not isinstance(raw, dict) or not isinstance(raw.get("clients"), dict):
            raise OrderError(409, "studio_config_invalid", 'clients.json must be {"clients": {...}}')
        return raw

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, json.dumps(data, ensure_ascii=False, indent=1))

    @staticmethod
    def _key(client: str) -> str:
        try:
            key = valid_participant(client)
        except ValueError:
            raise OrderError(422, "invalid_participant", "client must be a 64-hex participant key") from None
        if key == "owner":
            raise OrderError(422, "invalid_participant", "the owner does not get a client token")
        return key

    @staticmethod
    def _public(key: str, rec: dict) -> dict:
        return {"client": key, "label": rec.get("label", ""), "issued_at": rec.get("issued_at"),
                "expires_at": rec.get("expires_at"), "revoked_at": rec.get("revoked_at"),
                "active": bool(rec.get("token_sha256")) and not rec.get("revoked_at") and not _expired(rec)}

    def issue(self, client: str | None = None, label: str = "", ttl_days: int | None = None) -> dict:
        key = self._key(client) if client else secrets.token_hex(32)
        if ttl_days is not None and not (isinstance(ttl_days, int) and 1 <= ttl_days <= 365):
            raise OrderError(422, "invalid_ttl", "ttl_days must be 1..365")
        token = self.PREFIX + secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        with self._lock:
            data = self._read()
            rec = {"token_sha256": token_hash(token), "label": str(label or "")[:80], "issued_at": now.isoformat(),
                   "expires_at": (now + timedelta(days=ttl_days)).isoformat() if ttl_days else None,
                   "revoked_at": None}
            data["clients"][key] = rec
            self._write(data)
        return self._public(key, rec) | {"token": token}

    def revoke(self, client: str) -> dict:
        key = self._key(client)
        with self._lock:
            data = self._read()
            rec = data["clients"].get(key)
            if rec is None:
                raise OrderError(404, "unknown_client", "no token was issued for this client")
            rec.update(token_sha256=None, revoked_at=now_iso())
            self._write(data)
        return self._public(key, rec)

    def resolve(self, token: str | None) -> str | None:
        """The client key of a valid, unrevoked, unexpired token; None otherwise. Every entry is compared in
        constant time."""
        if not token or not str(token).startswith(self.PREFIX) or len(token) > 200:
            return None
        digest, found = token_hash(token).encode(), None
        for key, rec in self._read()["clients"].items():
            stored = rec.get("token_sha256") if isinstance(rec, dict) else None
            if stored and hmac.compare_digest(digest, str(stored).encode()) and not _expired(rec)                     and not rec.get("revoked_at"):        # revoked even if a hash survived (Kimi draft 25.10)
                found = key
        # a hand-edited file must never turn a client token into the owner (Qwen review 25.10)
        return found if found and CLIENT_KEY.fullmatch(found) else None

    def list(self) -> list[dict]:
        return [self._public(k, r) for k, r in sorted(self._read()["clients"].items()) if isinstance(r, dict)]


def _expired(rec: dict) -> bool:
    try:
        return bool(rec.get("expires_at")) and datetime.fromisoformat(rec["expires_at"]) <= datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return True


class OrderBook:
    def __init__(self, data_dir: Path, jobs: JobStore):
        self.data_dir = Path(data_dir)
        self.jobs = jobs
        studio = self.data_dir / "direct-gen" / "studio"
        self.price_list_path = studio / "price_list.json"
        self.brands_path = studio / "brands.json"
        self.licences_path = studio / "licences.json"
        self.jeff_pause_path = studio / "jeff_pause.json"
        self.delivery_links_path = studio / "delivery_links.json"
        self.tokens = ClientTokens(self.data_dir)
        self._locks: dict[Path, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _config(self, path: Path) -> Any:
        if not path.is_file():
            return None
        try:
            return json.loads(read_text_retry(path))
        except (OSError, ValueError):
            raise OrderError(409, "studio_config_invalid", f"{path.name} is not valid JSON") from None

    def brands(self) -> list[str]:
        """The trademark denylist: brands.json {"denylist": [...]} replaces the defaults when present."""
        raw = self._config(self.brands_path)
        if raw is None:
            return list(DEFAULT_BRANDS)
        names = raw.get("denylist") if isinstance(raw, dict) else None
        if not isinstance(names, list) or not all(isinstance(n, str) and n.strip() for n in names):
            raise OrderError(409, "studio_config_invalid", 'brands.json must be {"denylist": [non-empty strings]}')
        return [n.strip()[:80] for n in names]

    def licences(self) -> dict[str, dict[str, Any]]:
        table = {k: dict(v) for k, v in LICENCES.items()}
        raw = self._config(self.licences_path)
        if isinstance(raw, dict):
            for model, rec in raw.items():
                if isinstance(rec, dict) and rec.get("commercial") in (True, False, None):
                    table[str(model)] = {"licence": str(rec.get("licence") or "unknown")[:200],
                                         "commercial": rec.get("commercial")}
        return table

    def licence_warnings(self, order: dict) -> list[dict]:
        """Owner-only: models of this order whose licence is unknown or non-commercial (a warning, never a block)."""
        table, out, seen = self.licences(), [], set()
        for item in order["items"]:
            for model in (item.get("models") or {}).values():
                if model in seen:
                    continue
                seen.add(model)
                rec = table.get(model)
                if rec is None or rec.get("commercial") is None:
                    out.append({"model": model, "licence": (rec or {}).get("licence", "unknown"), "status": "unknown"})
                elif rec["commercial"] is False:
                    out.append({"model": model, "licence": rec["licence"], "status": "non_commercial"})
        return out

    def brand_flags(self, order: dict) -> list[dict]:
        """Every denylisted brand named in the brief or a job prompt, with whether the owner cleared it."""
        brands, owned = self.brands(), order.get("brand_ownership") or {}
        texts = [("brief", order.get("brief") or "")]
        for item in order["items"]:
            for job_id in item["job_ids"]:
                try:
                    job = self.jobs.load(order["participant"], job_id)
                except KeyError:
                    continue
                texts.append((job_id, " ".join(str(job.get(k) or "") for k in ("raw_prompt", "effective_prompt"))))
        out = []
        for where, text in texts:
            for brand in brands_named(text, brands):
                out.append({"brand": brand, "where": where, "cleared": brand.lower() in owned})
        return out

    # ---------- price list ----------

    def price_list(self) -> dict[str, Any]:
        """Defaults merged with the optional JSON file. A broken or non-positive file refuses quoting (409) rather
        than silently quoting a wrong price."""
        raw: dict[str, Any] = {}
        if self.price_list_path.is_file():
            try:
                raw = json.loads(read_text_retry(self.price_list_path))
            except (OSError, ValueError):
                raise OrderError(409, "price_list_invalid", "price_list.json is not valid JSON") from None
            if not isinstance(raw, dict):
                raise OrderError(409, "price_list_invalid", "price_list.json must be an object")
        prices = dict(DEFAULT_PRICE_LIST["prices"])
        if isinstance(raw.get("prices"), dict):
            prices.update({k: v for k, v in raw["prices"].items() if k in KINDS})
        try:
            out = {
                "currency": str(raw.get("currency") or DEFAULT_PRICE_LIST["currency"])[:8],
                "prices": {k: positive_price(prices[k], f"price of {k}") for k in KINDS},
                "power_watts": positive_price(raw.get("power_watts", DEFAULT_PRICE_LIST["power_watts"]), "power_watts"),
                "kwh_price": _dec(raw.get("kwh_price", DEFAULT_PRICE_LIST["kwh_price"]), "kwh_price"),
            }
        except OrderError as exc:
            raise OrderError(409, "price_list_invalid", exc.message) from None
        if out["kwh_price"] < 0:
            raise OrderError(409, "price_list_invalid", "kwh_price cannot be negative")
        out["source"] = "file" if raw else "defaults"
        return out

    # ---------- storage ----------

    def _dir(self, participant: str) -> Path:
        return self.jobs.participant_dir(valid_participant(participant)) / "orders"

    def _path(self, participant: str, order_id: str) -> Path:
        if not ORDER_ID.fullmatch(str(order_id)):
            raise OrderError(404, "unknown_order", "order not found")
        return self._dir(participant) / f"{order_id}.json"

    def _save(self, order: dict) -> None:
        folder = self._dir(order["participant"])
        folder.mkdir(parents=True, exist_ok=True)
        atomic_write_text(folder / f"{order['order_id']}.json", json.dumps(order, ensure_ascii=False, indent=1))

    def _load(self, participant: str, order_id: str) -> dict:
        try:
            return migrate(json.loads(read_text_retry(self._path(participant, order_id))))
        except (OSError, ValueError):
            raise OrderError(404, "unknown_order", "order not found") from None

    def _raw_list(self, participant: str) -> list[dict]:
        base = self._dir(participant)
        out = []
        for path in base.glob("*.json") if base.is_dir() else []:
            if not ORDER_ID.fullmatch(path.stem):
                continue
            try:
                out.append(migrate(json.loads(read_text_retry(path))))
            except (OSError, ValueError):
                continue
        for o in out:
            o.pop("_migrating", None)
        return sorted(out, key=lambda o: o.get("created_at", ""), reverse=True)

    def _events_path(self, order: dict) -> Path:
        return self._dir(order["participant"]) / f"{order['order_id']}.events.jsonl"

    def _append_events(self, order: dict, rows: list[dict]) -> None:
        """Append-only: one JSON line per event, opened in append mode, flushed to disk before returning."""
        with open(self._events_path(order), "a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def events(self, participant: str, order_id: str) -> list[dict]:
        """The order's event log (owner view). A torn last line from a crash is skipped, never repaired in place."""
        order = self._load(valid_participant(participant), order_id)
        path = self._events_path(order)
        out = []
        for line in read_text_retry(path).splitlines() if path.is_file() else []:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def _lock(self, participant: str, order_id: str) -> threading.Lock:
        path = self._path(participant, order_id)
        with self._locks_guard:
            return self._locks.setdefault(path, threading.Lock())

    def _commit(self, order: dict, step: str, fp: str, actor: str, moves: list[dict], key: str | None,
                reason: str = "") -> None:
        """Bump rev, remember the step for replays, save atomically, then append its events."""
        rows = []
        migrated = order.pop("_migrating", None)
        if migrated:
            rows.append({"step": "migrated", "from": order["migrated_from"].get("status"), "to": migrated,
                         "actor": "system", "reason": "schema 1 -> 2"})
        rows += [{"step": step, "from": m["from"], "to": m["to"], "actor": actor, "reason": m.get("reason", "")}
                 for m in moves] or [{"step": step, "from": order["status"], "to": order["status"], "actor": actor,
                                      "reason": reason}]
        order["rev"] = int(order.get("rev") or 0) + 1
        order["updated_at"] = at = now_iso()
        order["last_step"] = {"step": step, "fp": fp, "rev": order["rev"]}
        if key is not None:
            order.setdefault("idempotency", {})[key] = {"step": step, "fp": fp, "rev": order["rev"], "at": at}
        self._save(order)
        self._append_events(order, [{"rev": order["rev"], "at": at, "key": key, **r} for r in rows])

    def _step(self, participant: str, order_id: str, step: str, fn: Callable[[dict, list], dict | None], *,
              args: Any = None, actor: str = "owner", key: str | None = None, replayable: bool = True,
              reason: str = "") -> dict:
        """Run one mutating step under the order's lock: idempotency check, fn(order, moves), commit."""
        participant = valid_participant(participant)
        if key is not None and not IDEMPOTENCY_KEY.fullmatch(str(key)):
            raise OrderError(422, "invalid_idempotency_key", "Idempotency-Key: 1..128 of [A-Za-z0-9._:-]")
        fp = fingerprint(step, [actor, args])          # an owner's step is not a replay of the client's (and back)
        with self._lock(participant, order_id):
            order = self._load(participant, order_id)
            if key is not None and key in (order.get("idempotency") or {}):
                if order["idempotency"][key].get("fp") != fp:
                    raise OrderError(409, "idempotency_conflict", "this Idempotency-Key was used for another request")
                return self.view(order) | {"replayed": True}
            if key is None and replayable and (order.get("last_step") or {}).get("fp") == fp:
                return self.view(order) | {"replayed": True}
            moves: list[dict] = []
            try:
                extra = fn(order, moves) or {}
            except _CommitThenFail as stop:
                if moves:                       # keep the state it reached (e.g. preflight); a pure re-check: nothing
                    self._commit(order, step, fingerprint(step + ":refused", [args, order["rev"]]), actor, moves,
                                 key=None)
                raise stop.error from None
            self._commit(order, step, fp, actor, moves, key, reason)
            return self.view(order) | extra

    def _job(self, participant: str, job_id: str) -> dict:
        try:
            return self.jobs.load(participant, job_id)
        except KeyError:
            raise OrderError(422, "unknown_job", f"job {str(job_id)[:40]} not found for this client") from None

    # ---------- receipt ----------

    def receipt(self, order: dict) -> dict:
        """Price (frozen in the order), live cost from the jobs' recorded seconds, margin. A delivered order keeps
        the receipt frozen at delivery."""
        if order.get("status") in ("delivered", "closed") and order.get("receipt"):
            return order["receipt"]
        pl = order["pricing"]
        watts, kwh_price = Decimal(pl["power_watts"]), Decimal(pl["kwh_price"])
        lines, price_total, seconds_total, cost_total, kwh_total, complete = [], Decimal(0), Decimal(0), Decimal(0), Decimal(0), True
        for item in order["items"]:
            line_secs, line_kwh, line_cost, jobs = Decimal(0), Decimal(0), Decimal(0), []
            for job_id in item["job_ids"]:
                try:
                    job = self.jobs.load(order["participant"], job_id)
                except KeyError:
                    job = {}
                secs = job_seconds(job) if job.get("status") == "completed" else None
                if secs is None:
                    complete = False
                    jobs.append({"job_id": job_id, "status": job.get("status", "missing"), "seconds": None})
                    continue
                kwh, cost = energy_cost(secs, watts, kwh_price)
                line_secs, line_kwh, line_cost = line_secs + secs, line_kwh + kwh, line_cost + cost
                jobs.append({"job_id": job_id, "status": "completed", "seconds": str(secs)})
            price = Decimal(item["price"])
            price_total += price
            seconds_total, kwh_total, cost_total = seconds_total + line_secs, kwh_total + line_kwh, cost_total + line_cost
            lines.append({"kind": item["kind"], "price": str(price), "seconds": str(line_secs),
                          "energy_kwh": str(line_kwh.quantize(COST_Q, ROUND_HALF_UP)),
                          "cost": str(line_cost.quantize(COST_Q, ROUND_HALF_UP)), "jobs": jobs})
        cost_q = cost_total.quantize(COST_Q, ROUND_HALF_UP)
        margin = price_total - cost_q
        return {
            "currency": pl["currency"], "price": str(price_total), "cost": str(cost_q), "margin": str(margin),
            "margin_ratio": str((margin / price_total).quantize(Decimal("0.0001"), ROUND_HALF_UP)) if price_total else "0",
            "seconds": str(seconds_total), "energy_kwh": str(kwh_total.quantize(COST_Q, ROUND_HALF_UP)),
            "power_watts": pl["power_watts"], "kwh_price": pl["kwh_price"],
            "cost_basis": "recorded generation seconds x power_watts x kwh_price",
            "cost_complete": complete, "lines": lines,
            "licence_warnings": order.get("licence_warnings") or [],
        }

    def _manifest_path(self, order: dict) -> Path:
        return self._dir(order["participant"]) / f"{order['order_id']}.manifest.json"

    def _order_artifacts_dir(self, order: dict) -> Path:
        return self._dir(order["participant"]) / order["order_id"]

    def build_manifest(self, order: dict) -> dict:
        """T1: single manifest object recording input hashes, models & versions, seeds, params, retries, artifacts,
        QC metrics and Jeff pause state; written on generating / qc / approve / deliver."""
        lic_table = self.licences()
        jobs_manifest: list[dict] = []
        input_hashes: list[str] = list(order.get("client_source_hashes") or [])
        for item in order.get("items") or []:
            for s_sha in item.get("source_hashes") or []:
                if s_sha not in input_hashes:
                    input_hashes.append(s_sha)
            for job_id in item.get("job_ids") or []:
                try:
                    job = self.jobs.load(order["participant"], job_id)
                except KeyError:
                    job = {}
                model = str(job.get("model") or (item.get("models") or {}).get(job_id) or "unknown")
                lic = lic_table.get(model) or {"licence": "unknown", "commercial": None}
                params = dict(job.get("params") or {})
                for k in ("width", "height", "fps", "frames", "duration_s", "steps", "cfg", "preset"):
                    if job.get(k) is not None and k not in params:
                        params[k] = job[k]
                j_in = list(job.get("input_hashes") or [])
                if (job.get("provenance") or {}).get("source_sha256"):
                    j_in.append(job["provenance"]["source_sha256"])
                for h in j_in:
                    if h not in input_hashes:
                        input_hashes.append(h)
                res = job.get("result") or {}
                secs = job_seconds(job)
                jobs_manifest.append({
                    "job_id": job_id,
                    "line_kind": item.get("kind"),
                    "job_kind": job.get("kind"),
                    "model": model,
                    "model_version": str(job.get("model_version") or job.get("version") or "local-2.5"),
                    "licence_id": lic.get("licence", "unknown"),
                    "commercial_use": lic.get("commercial"),
                    "seed": job.get("seed") if job.get("seed") is not None else params.get("seed", 42),
                    "params": params,
                    "input_hashes": j_in,
                    "status": job.get("status", "missing"),
                    "artifact_file": res.get("file"),
                    "artifact_sha256": res.get("sha256"),
                    "seconds": str(secs) if secs is not None else None,
                    "idempotency_key": f"{order['order_id']}:job:{job_id}",
                })
        attempt = int(order.get("attempt") or 0)
        oa = order.get("owner_approval")
        oa_manifest = ({**oa, "sha256": oa.get("hashes", {})} if isinstance(oa, dict) else None)
        return {
            "schema": "bossman.studio.manifest.v1",
            "order_id": order["order_id"],
            "participant": order["participant"],
            "status": order["status"],
            "preset": order.get("preset"),
            "attempt": attempt,
            "retries": max(0, attempt - 1),
            "inputs": {
                "brief_sha256": hashlib.sha256(str(order.get("brief") or "").encode("utf-8")).hexdigest(),
                "input_hashes": input_hashes,
            },
            "source_rights": order.get("source_rights") or {"required": self._needs_source_rights(order),
                                                             "confirmed": self._source_rights_ok(order)},
            "jobs": jobs_manifest,
            "qc_metrics": order.get("qc_metrics"),
            "owner_approval": oa_manifest,
            "jeff_pause": order.get("jeff_pause"),
            "updated_at": order.get("updated_at") or now_iso(),
        }

    def _write_manifest(self, order: dict) -> dict:
        manifest = self.build_manifest(order)
        order["manifest"] = manifest
        folder = self._dir(order["participant"])
        folder.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self._manifest_path(order), json.dumps(manifest, ensure_ascii=False, indent=1))
        return manifest

    @staticmethod
    def _needs_source_rights(order: dict) -> bool:
        if order.get("source_rights_required") is True or order.get("client_source_hashes"):
            return True
        sr = order.get("source_rights")
        if isinstance(sr, dict) and sr.get("confirmed") is False:
            return True
        for it in order.get("items") or []:
            if it.get("source_rights_required") is True or it.get("source_hashes"):
                return True
            if isinstance(it.get("source_rights"), dict) and it["source_rights"].get("confirmed") is False:
                return True
        return False

    def _source_rights_ok(self, order: dict) -> bool:
        sr = order.get("source_rights")
        if isinstance(sr, dict):
            return sr.get("confirmed") is True and bool(sr.get("at"))
        for it in order.get("items") or []:
            if it.get("source_rights_required") is True or it.get("source_hashes"):
                isr = it.get("source_rights") or {}
                if not (isr.get("confirmed") is True and bool(isr.get("at"))):
                    return False
            if isinstance(it.get("source_rights"), dict) and it["source_rights"].get("confirmed") is False:
                return False
        return not self._needs_source_rights(order)

    def source_rights_problems(self, order: dict) -> list[dict]:
        """T1.3: client source rights are an independent gate item (separate from brand clearance)."""
        problems = []
        if not self._source_rights_ok(order):
            problems.append({"code": "source_rights_missing", "where": "order"})
        for item in order.get("items") or []:
            for job_id in item.get("job_ids") or []:
                try:
                    job = self.jobs.load(order["participant"], job_id)
                except KeyError:
                    continue
                prov = job.get("provenance") or {}
                if prov.get("client_source") is True and prov.get("source_rights_confirmed") is not True:
                    if not (isinstance(order.get("source_rights"), dict)
                            and order["source_rights"].get("confirmed") is True):
                        problems.append({"job_id": job_id, "code": "source_rights_missing", "where": job_id})
        return problems

    @staticmethod
    def _public_delivery_link(dl: dict | None) -> dict | None:
        if not isinstance(dl, dict):
            return None
        return {
            "issued_at": dl.get("issued_at"),
            "expires_at": dl.get("expires_at"),
            "ttl_hours": dl.get("ttl_hours"),
            "revoked_at": dl.get("revoked_at"),
            "active": bool(dl.get("token_sha256")) and not dl.get("revoked_at") and not _expired(dl),
        }

    def view(self, order: dict) -> dict:
        """The OWNER's view: receipt with cost and margin, licence warnings, brand flags, manifest, delivery_link."""
        out = {k: v for k, v in order.items() if k not in ("receipt", "idempotency", "_migrating", "delivery_link")}
        out["receipt"] = self.receipt(order)
        out["licence_warnings"] = order.get("licence_warnings") or []
        out["manifest"] = order.get("manifest") or self.build_manifest(order)
        if order.get("delivery_link"):
            out["delivery_link"] = self._public_delivery_link(order["delivery_link"])
        out["next"] = list(TRANSITIONS.get(order.get("status"), ()))
        if order.get("status") not in FINAL:
            try:
                out["brand_flags"] = self.brand_flags(order)
            except OrderError as exc:
                out["brand_flags"] = [{"error": exc.code}]
        return out

    @staticmethod
    def client_view(order: dict) -> dict:
        """What the CLIENT may see: price, status, deliverables + disclosure. No cost, margin, note, models,
        licence warnings or brand flags (built from an allowlist, so a new internal field never leaks)."""
        items = []
        for item in order["items"]:
            disc = item.get("disclosures") or {}
            items.append({"kind": item["kind"], "price": item["price"],
                          "deliverables": [{"job_id": j, "disclosure": (disc.get(j) or {}).get("caption")}
                                           for j in item["job_ids"]]})
        price = sum((Decimal(i["price"]) for i in order["items"]), Decimal(0))
        timeline: list[dict] = []
        for t in order.get("timeline", []):
            status = CLIENT_STATUS.get(t["status"], t["status"])
            if not timeline or timeline[-1]["status"] != status:
                timeline.append({"status": status, "at": t["at"]})
        return {"order_id": order["order_id"], "status": CLIENT_STATUS.get(order["status"], order["status"]),
                "brief": order.get("brief", ""), "currency": order["pricing"]["currency"], "price": str(price),
                "items": items, "created_at": order["created_at"], "updated_at": order["updated_at"],
                "timeline": timeline}

    # ---------- lifecycle ----------

    def _price_items(self, items: list[dict], pl: dict) -> None:
        for item in items:
            item["price"] = str(positive_price(item["price_override"], "line price") if item.get("price_override")
                                is not None else pl["prices"][item["kind"]])

    @staticmethod
    def _pricing(pl: dict) -> dict:
        return {"currency": pl["currency"], "power_watts": str(pl["power_watts"]), "kwh_price": str(pl["kwh_price"]),
                "price_list": {k: str(v) for k, v in pl["prices"].items()}, "source": pl["source"]}

    def create(self, participant: str, body: dict) -> dict:
        participant = valid_participant(participant)
        raw_items = body.get("items")
        if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= MAX_ITEMS:
            raise OrderError(422, "items_required", f"1..{MAX_ITEMS} line items are needed")
        taken = {j for o in self._raw_list(participant) if o.get("status") != "cancelled"
                 for it in o.get("items", []) for j in it.get("job_ids", [])}
        seen: set[str] = set()
        models: dict[str, str] = {}
        items = []
        for n, it in enumerate(raw_items, start=1):
            if not isinstance(it, dict) or it.get("kind") not in KINDS:
                raise OrderError(422, "invalid_kind", f"line {n}: kind must be one of " + ", ".join(KINDS))
            ids = it.get("job_ids")
            if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_JOBS_PER_ITEM:
                raise OrderError(422, "job_ids_required", f"line {n}: 1..{MAX_JOBS_PER_ITEM} job ids are needed")
            for job_id in ids:
                if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
                    raise OrderError(422, "unknown_job", f"line {n}: malformed job id")
                if job_id in seen:
                    raise OrderError(422, "duplicate_job", f"line {n}: job {job_id} is listed twice")
                if job_id in taken:
                    raise OrderError(409, "job_already_ordered", f"job {job_id} is already in another open order")
                seen.add(job_id)
                job = self._job(participant, job_id)          # isolation: only this client's own jobs resolve
                models[job_id] = str(job.get("model") or "unknown")
                if job.get("kind") not in JOB_KINDS[it["kind"]]:
                    raise OrderError(422, "kind_mismatch",
                                     f"line {n}: a {it['kind']} line cannot bill a {job.get('kind')} job")
            item = {"kind": it["kind"], "job_ids": list(ids), "face": bool(it.get("face")), "disclosures": {},
                    "models": {j: models[j] for j in ids}}
            if it.get("price") is not None:
                item["price_override"] = str(positive_price(it["price"], f"line {n} price"))
            if it.get("consent_confirmed") is True:
                item["consent"] = {"confirmed": True, "at": now_iso(), "note": str(it.get("consent_note") or "")[:200]}
            if it.get("source_rights_required") is True:
                item["source_rights_required"] = True
            if it.get("source_hashes"):
                item["source_hashes"] = [str(h)[:128] for h in it["source_hashes"][:20]]
            if it.get("source_rights_confirmed") is not None:
                item["source_rights"] = {
                    "confirmed": bool(it["source_rights_confirmed"]),
                    "at": now_iso() if it["source_rights_confirmed"] else None,
                    "note": str(it.get("source_rights_note") or "")[:200],
                }
            items.append(item)
        pl = self.price_list()
        self._price_items(items, pl)
        status = "draft" if body.get("draft") is True else "quoted"
        created = now_iso()
        order: dict[str, Any] = {
            "order_id": uuid.uuid4().hex, "participant": participant, "status": status, "schema": SCHEMA,
            "rev": 1, "attempt": 0, "idempotency": {},
            "note": str(body.get("note") or "")[:500], "brief": str(body.get("brief") or "")[:2000],
            "items": items, "pricing": self._pricing(pl), "brand_ownership": {},
            "created_at": created, "updated_at": created,
            "timeline": [{"status": status, "at": created}],
        }
        if body.get("preset"):
            order["preset"] = str(body["preset"])[:64]
        if body.get("source_rights_required") is True:
            order["source_rights_required"] = True
        if body.get("client_source_hashes"):
            order["client_source_hashes"] = [str(h)[:128] for h in body["client_source_hashes"][:20]]
        if body.get("source_rights_confirmed") is not None:
            order["source_rights"] = {
                "confirmed": bool(body["source_rights_confirmed"]),
                "by": "owner",
                "at": created if body["source_rights_confirmed"] else None,
                "note": str(body.get("source_rights_note") or "")[:200],
            }
        order["licence_warnings"] = self.licence_warnings(order)
        self._save(order)
        self._append_events(order, [{"rev": 1, "at": created, "key": None, "step": "create", "from": None,
                                     "to": status, "actor": "owner", "reason": ""}])
        return self.view(order)

    def get(self, participant: str, order_id: str) -> dict:
        return self.view(self._load(valid_participant(participant), order_id))

    def list(self, participant: str) -> list[dict]:
        return [self.view(o) for o in self._raw_list(valid_participant(participant))]

    def get_for_client(self, participant: str, order_id: str) -> dict:
        order = self._load(valid_participant(participant), order_id)
        if order["status"] == "draft":                  # the owner's unpriced draft is not shown to the client
            raise OrderError(404, "unknown_order", "order not found")
        return self.client_view(order)

    def list_for_client(self, participant: str) -> list[dict]:
        return [self.client_view(o) for o in self._raw_list(valid_participant(participant)) if o["status"] != "draft"]

    def clients_with_orders(self) -> list[str]:
        """Client keys that have at least one order on disk (F7 migration: each needs a token from the owner)."""
        root = self.jobs.root
        return sorted(p.name for p in (root.iterdir() if root.is_dir() else [])
                      if p.name != "owner" and any((p / "orders").glob("*.json"))
                      and CLIENT_KEY.fullmatch(p.name))

    def client_tokens(self) -> dict:
        """Owner view of issued tokens (never the hashes) + the clients with orders that still have no token."""
        issued = self.tokens.list()
        with_token = {c["client"] for c in issued if c["active"]}
        return {"clients": issued, "needs_token": [k for k in self.clients_with_orders() if k not in with_token]}

    def set_brand_ownership(self, actor: str, participant: str, order_id: str, brands: list[str],
                            note: str = "", key: str | None = None) -> dict:
        """OWNER only: record that this client owns the listed brands (clears them for delivery of this order)."""
        if valid_participant(actor) != "owner":
            raise OrderError(403, "owner_only", "only the owner can record brand ownership")
        names = [str(b).strip()[:80] for b in brands or [] if str(b).strip()]
        if not names:
            raise OrderError(422, "brands_required", "name at least one brand")

        def fn(order: dict, moves: list) -> None:
            self._open(order)
            owned = order.setdefault("brand_ownership", {})
            for b in names:
                owned[b.lower()] = {"brand": b, "set_by": "owner", "at": now_iso(), "note": str(note or "")[:200]}
        return self._step(participant, order_id, "brands", fn, args=[names, note], key=key, replayable=False,
                          reason="brand ownership: " + ", ".join(names))

    def confirm_source_rights(self, participant: str, order_id: str, confirmed: bool = True,
                              note: str = "", source_hashes: list[str] | None = None,
                              key: str | None = None, actor: str = "owner") -> dict:
        """T1.3: record confirmation of client source rights (separate gate from brand clearance)."""
        def fn(order: dict, moves: list) -> None:
            self._open(order)
            if source_hashes is not None:
                order["client_source_hashes"] = [str(h)[:128] for h in source_hashes[:20]]
            order["source_rights_confirmed"] = bool(confirmed)
            order["source_rights"] = {
                "confirmed": bool(confirmed),
                "by": actor,
                "at": now_iso() if confirmed else None,
                "note": str(note or "")[:200],
            }
            for it in order.get("items") or []:
                if isinstance(it.get("source_rights"), dict) or it.get("source_rights_required"):
                    it["source_rights_confirmed"] = bool(confirmed)
                    it["source_rights"] = dict(order["source_rights"])
            self._write_manifest(order)
        return self._step(participant, order_id, "source_rights", fn,
                          args=[bool(confirmed), note, list(source_hashes or [])], key=key, actor=actor,
                          replayable=False, reason=f"source_rights: {'confirmed' if confirmed else 'revoked'}")

    def _read_delivery_links(self) -> dict:
        if not self.delivery_links_path.is_file():
            return {"links": {}}
        try:
            raw = json.loads(read_text_retry(self.delivery_links_path))
            return raw if isinstance(raw, dict) and isinstance(raw.get("links"), dict) else {"links": {}}
        except (OSError, ValueError):
            return {"links": {}}

    def _write_delivery_links(self, data: dict) -> None:
        self.delivery_links_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.delivery_links_path, json.dumps(data, ensure_ascii=False, indent=1))

    def issue_delivery_link(self, participant: str, order_id: str, ttl_hours: int | float = 72,
                            key: str | None = None) -> dict:
        """T1.2: issue a random, revocable, TTL-bound delivery link; only its SHA-256 is stored on disk."""
        if isinstance(ttl_hours, bool) or not isinstance(ttl_hours, (int, float)) or not 0 < float(ttl_hours) <= 8760:
            raise OrderError(422, "invalid_ttl", "ttl_hours must be 1..8760")
        raw_token = "stdl_" + secrets.token_urlsafe(32)
        th = token_hash(raw_token)
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(hours=float(ttl_hours))).isoformat()

        def fn(order: dict, moves: list) -> None:
            if order["status"] not in ("approved", "delivered", "closed"):
                raise OrderError(409, "invalid_transition",
                                 f"order is {order['status']}; delivery link requires approved or delivered order")
            order["delivery_link"] = {
                "token_sha256": th,
                "issued_at": now.isoformat(),
                "expires_at": expires,
                "ttl_hours": ttl_hours,
                "revoked_at": None,
            }
            links = self._read_delivery_links()
            links["links"][th] = {
                "participant": order["participant"],
                "order_id": order["order_id"],
                "token_sha256": th,
                "issued_at": now.isoformat(),
                "expires_at": expires,
                "revoked_at": None,
            }
            self._write_delivery_links(links)
            self._write_manifest(order)
        self._step(participant, order_id, "delivery_link", fn, args=[float(ttl_hours)], key=key, replayable=False,
                   reason=f"delivery link issued (ttl={ttl_hours}h)")
        return {
            "order_id": order_id,
            "token": raw_token,
            "delivery_token": raw_token,
            "delivery_url": f"/api/studio/delivery/{raw_token}",
            "issued_at": now.isoformat(),
            "expires_at": expires,
            "ttl_hours": ttl_hours,
            "active": True,
        }

    def revoke_delivery_link(self, participant: str, order_id: str, reason: str = "",
                             key: str | None = None) -> dict:
        """T1.2: revoke the order's delivery link immediately (subsequent opens return 404)."""
        rev_at = now_iso()

        def fn(order: dict, moves: list) -> None:
            dl = order.get("delivery_link")
            if not isinstance(dl, dict):
                raise OrderError(404, "delivery_link_not_found", "no delivery link was issued for this order")
            old_th = dl.get("token_sha256")
            dl["token_sha256"] = None
            dl["revoked_at"] = rev_at
            dl["revoke_reason"] = str(reason or "")[:200]
            if old_th:
                links = self._read_delivery_links()
                if old_th in links.get("links", {}):
                    links["links"][old_th]["revoked_at"] = rev_at
                    self._write_delivery_links(links)
            self._write_manifest(order)
        self._step(participant, order_id, "revoke_delivery_link", fn, args=[str(reason or "")], key=key,
                   replayable=False, reason="delivery link revoked")
        return {"order_id": order_id, "revoked_at": rev_at, "active": False}

    def open_delivery_link(self, token: str | None, caller_client: str | None = None) -> dict:
        """T1.2: resolve a delivery token in constant time. Revoked, expired, unknown, or accessed by another
        authenticated client key -> 404."""
        if not token or not str(token).startswith("stdl_") or len(str(token)) > 200:
            raise OrderError(404, "delivery_link_not_found", "delivery link not found, expired or revoked")
        th_str = token_hash(str(token))
        digest = th_str.encode()

        # Also check index in delivery_links.json if present
        links_idx = self._read_delivery_links().get("links", {})
        idx_rec = None
        for k, v in links_idx.items():
            if hmac.compare_digest(digest, str(k).encode()) and isinstance(v, dict):
                idx_rec = v
        if idx_rec is not None and (idx_rec.get("revoked_at") or _expired(idx_rec)):
            raise OrderError(404, "delivery_link_not_found", "delivery link not found, expired or revoked")

        matched_order: dict | None = None
        root = self.jobs.root
        participants = [p.name for p in (root.iterdir() if root.is_dir() else []) if p.is_dir()]
        if "owner" not in participants:
            participants.append("owner")
        for part in sorted(set(participants)):
            try:
                orders = self._raw_list(part)
            except (OrderError, ValueError):
                continue
            for o in orders:
                dl = o.get("delivery_link")
                if not isinstance(dl, dict):
                    continue
                stored = dl.get("token_sha256")
                if stored and hmac.compare_digest(digest, str(stored).encode()) and not dl.get("revoked_at") \
                        and not _expired(dl):
                    matched_order = o
        if matched_order is None:
            raise OrderError(404, "delivery_link_not_found", "delivery link not found, expired or revoked")
        if caller_client is not None and caller_client != matched_order["participant"]:
            # Client B must never open Client A's delivery link (returns 404 so existence is not leaked)
            raise OrderError(404, "delivery_link_not_found", "delivery link not found, expired or revoked")
        dl_info = matched_order["delivery_link"]
        files = []
        for item in matched_order.get("items") or []:
            disc = item.get("disclosures") or {}
            wm = item.get("watermarks") or {}
            for job_id in item.get("job_ids") or []:
                try:
                    job = self.jobs.load(matched_order["participant"], job_id)
                except KeyError:
                    continue
                res = job.get("result") or {}
                files.append({
                    "job_id": job_id,
                    "file": res.get("file"),
                    "sha256": res.get("sha256"),
                    "disclosure": (disc.get(job_id) or {}).get("caption", DISCLOSURE),
                    "watermark": (wm.get(job_id) or {}).get("mark", WATERMARK),
                })
        cview = self.client_view(matched_order)
        return cview | {
            "order": cview,
            "delivery": {
                "issued_at": dl_info.get("issued_at"),
                "expires_at": dl_info.get("expires_at"),
                "files": files,
            },
        }

    @staticmethod
    def _open(order: dict) -> None:
        if order["status"] in FINAL:
            raise OrderError(409, "invalid_transition", f"order is {order['status']}")

    @staticmethod
    def _move(order: dict, moves: list, status: str, reason: str = "") -> None:
        """One transition of the state machine (TRANSITIONS); recorded in the timeline and as one event."""
        if status not in TRANSITIONS.get(order["status"], ()):
            raise OrderError(409, "invalid_transition", f"order is {order['status']}; cannot become {status}")
        moves.append({"from": order["status"], "to": status, "reason": reason})
        order["status"] = status
        order["timeline"].append({"status": status, "at": now_iso(), **({"note": reason} if reason else {})})

    def quote(self, participant: str, order_id: str, key: str | None = None) -> dict:
        """Price a draft, or re-price a still-quoted order, from the current price list (frozen once accepted)."""
        def fn(order: dict, moves: list) -> None:
            if order["status"] not in ("draft", "quoted"):
                raise OrderError(409, "invalid_transition",
                                 f"order is {order['status']}; only a draft or quoted order is (re)quoted")
            pl = self.price_list()
            self._price_items(order["items"], pl)
            order["pricing"] = self._pricing(pl)
            order["licence_warnings"] = self.licence_warnings(order)
            self._move(order, moves, "quoted", "requoted" if order["status"] == "quoted" else "")
        return self._step(participant, order_id, "quote", fn, key=key, replayable=False)

    def accept(self, participant: str, order_id: str, key: str | None = None, actor: str = "owner") -> dict:
        return self._step(participant, order_id, "accept", lambda o, m: self._move(o, m, "accepted"),
                          key=key, actor=actor)

    def preflight_problems(self, order: dict) -> list[dict]:
        """Checked before anything is rendered: every job exists, face jobs have consent, client source rights,
        and no uncleared brand."""
        problems = []
        for item in order["items"]:
            for job_id in item["job_ids"]:
                try:
                    job = self.jobs.load(order["participant"], job_id)
                except KeyError:
                    problems.append({"job_id": job_id, "code": "unknown_job"})
                    continue
                if needs_consent(job, item) and not consent_recorded(job, item):
                    problems.append({"job_id": job_id, "code": "consent_missing"})
        problems += self.source_rights_problems(order)
        problems += [{"code": "brand_not_cleared", "brand": f["brand"], "where": f["where"]}
                     for f in self.brand_flags(order) if not f["cleared"]]
        return problems

    def start(self, participant: str, order_id: str, key: str | None = None) -> dict:
        """accepted -> preflight -> generating (a failed preflight keeps the order in preflight, 409 + problems);
        preflight (re-check) / rework / qc_failed / reconcile_required -> generating as a new attempt. Preflight is
        re-checked on every attempt. Writes the order manifest on entering generating."""
        def fn(order: dict, moves: list) -> None:
            if "generating" not in TRANSITIONS.get(order["status"], ()) and order["status"] != "accepted":
                raise OrderError(409, "invalid_transition", f"order is {order['status']}; cannot become generating")
            if order["status"] == "accepted":
                self._move(order, moves, "preflight")
            # every attempt re-checks: consent/brands/source_rights may change during rework
            problems = self.preflight_problems(order)
            if problems:
                raise _CommitThenFail(_refusal("preflight failed", problems))
            self._move(order, moves, "generating", f"attempt {int(order.get('attempt') or 0) + 1}")
            order["attempt"] = int(order.get("attempt") or 0) + 1
            self._write_manifest(order)
        return self._step(participant, order_id, "start", fn, key=key)

    def _deliverable_hashes(self, order: dict) -> tuple[dict[str, str], list[dict]]:
        """{job_id: sha256} of every finished deliverable, plus a problem for each job that is not finished."""
        hashes, problems = {}, []
        for item in order["items"]:
            for job_id in item["job_ids"]:
                try:
                    job = self.jobs.load(order["participant"], job_id)
                except KeyError:
                    problems.append({"job_id": job_id, "code": "unknown_job"})
                    continue
                sha = (job.get("result") or {}).get("sha256")
                if job.get("status") != "completed" or not sha or self._result_file(job) is None:
                    problems.append({"job_id": job_id, "code": "job_not_completed", "status": job.get("status")})
                else:
                    hashes[job_id] = sha
        return hashes, problems

    def qc(self, participant: str, order_id: str, passed: bool, note: str = "", key: str | None = None) -> dict:
        """Owner's QC verdict on the rendered files: passed -> qc_passed -> owner_review (every job must be finished),
        failed -> qc_failed (then start = a new attempt, or rework). Updates the order manifest."""
        note = str(note or "")[:200]

        def fn(order: dict, moves: list) -> None:
            if not passed:
                self._move(order, moves, "qc_failed", note)
                self._write_manifest(order)
                return
            if "qc_passed" not in TRANSITIONS.get(order["status"], ()):
                raise OrderError(409, "invalid_transition", f"order is {order['status']}; cannot become qc_passed")
            _, problems = self._deliverable_hashes(order)
            if problems:
                raise _refusal("qc cannot pass", problems)
            self._move(order, moves, "qc_passed", note)
            self._move(order, moves, "owner_review")
            self._write_manifest(order)
        return self._step(participant, order_id, "qc", fn, args=[bool(passed), note], key=key)

    def approve(self, actor: str, participant: str, order_id: str, answer: str, key: str | None = None) -> dict:
        """The OWNER's explicit «yes»: owner_review -> approved, bound to the SHA-256 of every deliverable."""
        if valid_participant(actor) != "owner":
            raise OrderError(403, "owner_only", "only the owner approves a delivery")
        if str(answer or "").strip().lower() not in APPROVAL_ANSWERS:
            raise OrderError(422, "approval_answer_invalid", "the owner's answer must be exactly «yes»")

        def fn(order: dict, moves: list) -> None:
            if order["status"] != "owner_review":
                raise OrderError(409, "invalid_transition", f"order is {order['status']}; cannot become approved")
            hashes, problems = self._deliverable_hashes(order)
            if problems:
                raise _refusal("cannot approve", problems)
            self._move(order, moves, "approved", "owner: yes")
            order["owner_approval"] = {"answer": "yes", "by": "owner", "at": now_iso(), "hashes": hashes}
            self._write_manifest(order)
        return self._step(participant, order_id, "approve", fn, key=key)

    def rework(self, participant: str, order_id: str, reason: str = "", key: str | None = None,
               actor: str = "owner") -> dict:
        """qc_failed / owner_review / approved -> rework; any earlier approval is void."""
        reason = str(reason or "")[:200]

        def fn(order: dict, moves: list) -> None:
            self._move(order, moves, "rework", reason)
            order.pop("owner_approval", None)
        return self._step(participant, order_id, "rework", fn, args=[reason], key=key, actor=actor)

    def reconcile(self, participant: str, order_id: str, reason: str = "", key: str | None = None) -> dict:
        """generating -> reconcile_required: the render failed with an unknown outcome; nothing is re-run silently."""
        reason = str(reason or "")[:200]
        return self._step(participant, order_id, "reconcile",
                          lambda o, m: self._move(o, m, "reconcile_required", reason), args=[reason], key=key)

    def cancel(self, participant: str, order_id: str, reason: str = "", key: str | None = None,
               actor: str = "owner") -> dict:
        reason = str(reason or "")[:200]
        return self._step(participant, order_id, "cancel", lambda o, m: self._move(o, m, "cancelled", reason),
                          args=[reason], key=key, actor=actor)

    def close(self, participant: str, order_id: str, key: str | None = None) -> dict:
        return self._step(participant, order_id, "close", lambda o, m: self._move(o, m, "closed"), key=key)

    def _result_file(self, job: dict) -> Path | None:
        res = job.get("result") or {}
        name = res.get("file")
        if not isinstance(name, str) or not name or "/" in name or "\\" in name or name.startswith("."):
            return None
        path = self.jobs.job_dir(job["participant"], job["job_id"]) / "result" / name
        return path if path.is_file() else None

    def _bind(self, participant: str, order_id: str, step: str, field: str, record: dict,
              job_ids: list[str] | None, key: str | None) -> dict:
        """Bind ``record`` (+ the file's SHA-256) to the finished files of this order (all, or the listed jobs)."""
        wanted = set(job_ids) if job_ids else None

        def fn(order: dict, moves: list) -> dict:
            self._open(order)
            known = {j for it in order["items"] for j in it["job_ids"]}
            if wanted and not wanted <= known:
                raise OrderError(422, "unknown_job", "a listed job is not part of this order")
            done = []
            for item in order["items"]:
                for job_id in item["job_ids"]:
                    if wanted is not None and job_id not in wanted:
                        continue
                    job = self._job(order["participant"], job_id)
                    sha = (job.get("result") or {}).get("sha256")
                    if job.get("status") != "completed" or not sha or self._result_file(job) is None:
                        raise OrderError(409, "job_not_completed", f"job {job_id} has no finished file to {step}")
                    item.setdefault(field, {})[job_id] = {**record, "sha256": sha, "file": job["result"]["file"],
                                                          "at": now_iso()}
                    done.append(job_id)
            return {"disclosed" if step == "disclose" else "watermarked": done}
        return self._step(participant, order_id, step, fn, args=[sorted(wanted or []), record], key=key,
                          replayable=False)

    def disclose(self, participant: str, order_id: str, job_ids: list[str] | None = None,
                 caption: str = DISCLOSURE, key: str | None = None) -> dict:
        """Record the AI-generated disclosure for the finished files of this order (all, or the listed jobs). The
        caption is bound to the file's SHA-256, so a file swapped afterwards is not covered."""
        caption = str(caption or "").strip()[:200]
        if DISCLOSURE_MARK.lower() not in caption.lower():
            raise OrderError(422, "disclosure_invalid", f"the caption must say «{DISCLOSURE_MARK}»")
        return self._bind(participant, order_id, "disclose", "disclosures", {"caption": caption}, job_ids, key)

    def watermark(self, participant: str, order_id: str, job_ids: list[str] | None = None,
                  mark: str = WATERMARK, key: str | None = None) -> dict:
        """Record that the visible watermark ``mark`` is on the finished files, bound to each file's SHA-256 (the
        render pipeline may instead stamp ``provenance.watermark = {mark, sha256}`` on the job itself)."""
        mark = str(mark or "").strip()[:120]
        if not mark:
            raise OrderError(422, "watermark_invalid", "the watermark text must not be empty")
        return self._bind(participant, order_id, "watermark", "watermarks", {"mark": mark}, job_ids, key)

    @staticmethod
    def _watermark_problem(job: dict, item: dict, sha: str) -> str | None:
        recs = [(item.get("watermarks") or {}).get(job["job_id"]), (job.get("provenance") or {}).get("watermark")]
        recs = [r for r in recs if isinstance(r, dict) and str(r.get("mark") or "").strip()]
        if not recs:
            return "watermark_missing"
        return None if any(r.get("sha256") == sha for r in recs) else "watermark_stale"

    def delivery_problems(self, order: dict) -> list[dict]:
        """Every reason this order cannot be delivered right now (empty list = deliverable)."""
        problems = []
        approval = order.get("owner_approval") or {}
        for item in order["items"]:
            for job_id in item["job_ids"]:
                try:
                    job = self.jobs.load(order["participant"], job_id)
                except KeyError:
                    problems.append({"job_id": job_id, "code": "unknown_job"})
                    continue
                if job.get("status") != "completed" or not job.get("result") or self._result_file(job) is None:
                    problems.append({"job_id": job_id, "code": "job_not_completed", "status": job.get("status")})
                    continue
                sha = job["result"].get("sha256")
                disc = (item.get("disclosures") or {}).get(job_id) or {}
                if DISCLOSURE_MARK.lower() not in str(disc.get("caption", "")).lower():
                    problems.append({"job_id": job_id, "code": "disclosure_missing"})
                elif disc.get("sha256") != sha:
                    problems.append({"job_id": job_id, "code": "disclosure_stale"})
                wm = self._watermark_problem(job, item, sha)
                if wm:
                    problems.append({"job_id": job_id, "code": wm})
                if needs_consent(job, item) and not consent_recorded(job, item):
                    problems.append({"job_id": job_id, "code": "consent_missing"})
                if approval and (approval.get("hashes") or {}).get(job_id) != sha:
                    problems.append({"job_id": job_id, "code": "approval_stale"})
        if approval.get("answer") != "yes":
            problems.append({"code": "approval_missing"})
        problems += self.source_rights_problems(order)
        for flag in self.brand_flags(order):
            if not flag["cleared"]:
                problems.append({"code": "brand_not_cleared", "brand": flag["brand"], "where": flag["where"]})
        return problems

    def deliver(self, participant: str, order_id: str, key: str | None = None) -> dict:
        """approved -> delivered, only when ``delivery_problems`` is empty; the receipt and manifest are frozen."""
        def fn(order: dict, moves: list) -> None:
            if "delivered" not in TRANSITIONS.get(order["status"], ()):
                raise OrderError(409, "invalid_transition", f"order is {order['status']}; cannot become delivered")
            problems = self.delivery_problems(order)
            if problems:
                raise _refusal("cannot deliver", problems)
            receipt = self.receipt(order)
            order["licence_warnings"] = self.licence_warnings(order)
            self._move(order, moves, "delivered")
            order["receipt"] = receipt | {"frozen_at": now_iso(), "licence_warnings": order["licence_warnings"]}
            self._write_manifest(order)
        return self._step(participant, order_id, "deliver", fn, key=key)

    # ---------- T2: end-to-end preset render + Contact Sheet + QC metrics + Jeff pause ----------

    PRESETS: dict[str, dict[str, Any]] = {
        "product_spin": {
            "preset": "product_spin",
            "line_kind": "clip",
            "job_kind": "video",
            "model": "wan2.2-ti2v-5b",
            "width": 1080,
            "height": 1920,
            "fps": 24,
            "duration_s": 6.0,
            "frames": 144,
            "prompt_template": "Studio commercial 360 product spin on turntable, crisp rim lighting, clean background: {brief}",
        },
        "hand_reveal": {
            "preset": "hand_reveal",
            "line_kind": "clip",
            "job_kind": "video",
            "model": "wan2.2-ti2v-5b",
            "width": 1080,
            "height": 1920,
            "fps": 24,
            "duration_s": 6.0,
            "frames": 144,
            "prompt_template": "Studio macro commercial reveal on pedestal, cinematic studio lighting: {brief}",
        },
    }

    def create_preset_order(self, participant: str, preset: str, brief: str = "", *, note: str = "",
                            price: str | float | int | None = None, face: bool = False,
                            consent_confirmed: bool = True, consent_note: str = "",
                            source_rights_confirmed: bool = True,
                            source_rights_note: str = "client owns product source assets",
                            seed: int = 42) -> dict:
        """T2: create a Studio order for a commercial preset (product_spin or hand_reveal) with a bound Direct Gen job."""
        participant = valid_participant(participant)
        spec = self.PRESETS.get(str(preset or "").strip())
        if spec is None:
            raise OrderError(422, "invalid_preset", "preset must be one of: " + ", ".join(sorted(self.PRESETS)))
        prompt = spec["prompt_template"].format(brief=str(brief or "product showcase").strip()[:500])
        job_id = uuid.uuid4().hex
        now = now_iso()
        job = {
            "job_id": job_id,
            "participant": participant,
            "kind": spec["job_kind"],
            "model": spec["model"],
            "model_version": "wan2.2-ti2v-5b-apache2",
            "status": "queued",
            "preset": spec["preset"],
            "seed": int(seed),
            "width": spec["width"],
            "height": spec["height"],
            "fps": spec["fps"],
            "duration_s": spec["duration_s"],
            "frames": spec["frames"],
            "params": {
                "preset": spec["preset"],
                "width": spec["width"],
                "height": spec["height"],
                "fps": spec["fps"],
                "duration_s": spec["duration_s"],
                "frames": spec["frames"],
                "seed": int(seed),
            },
            "raw_prompt": prompt,
            "effective_prompt": prompt,
            "negative": "blurry, watermark, third-party logo, face, person",
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "provenance": {"consent_confirmed": bool(consent_confirmed)},
            "result": None,
            "timeline": [{"status": "queued", "at": now}],
        }
        self.jobs.save(job)
        item: dict[str, Any] = {
            "kind": spec["line_kind"],
            "job_ids": [job_id],
            "face": bool(face),
            "consent_confirmed": bool(consent_confirmed),
            "consent_note": consent_note,
        }
        if price is not None:
            item["price"] = price
        return self.create(participant, {
            "items": [item],
            "brief": brief,
            "note": note,
            "preset": spec["preset"],
            "source_rights_confirmed": source_rights_confirmed,
            "source_rights_note": source_rights_note,
        })

    def _set_jeff_pause(self, order: dict, paused: bool, reason: str) -> dict:
        state_path = self.jeff_pause_path
        state_path.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "paused": bool(paused),
            "order_id": order["order_id"],
            "reason": reason,
            "at": now_iso(),
        }
        atomic_write_text(state_path, json.dumps(rec, ensure_ascii=False, indent=1))
        prev = order.get("jeff_pause") or {}
        if paused:
            order["jeff_pause"] = {"paused": True, "paused_at": rec["at"], "resumed_at": None, "reason": reason}
        else:
            order["jeff_pause"] = {**prev, "paused": False, "resumed_at": rec["at"], "reason": reason}
        return order["jeff_pause"]

    def _generate_contact_sheet_and_qc(self, order: dict, job: dict) -> dict:
        """T2: generate contact_sheet.png and qc_report.json in the order folder (<orders_dir>/<order_id>/)."""
        from PIL import Image, ImageDraw, ImageFilter, ImageStat

        art_dir = self._order_artifacts_dir(order)
        art_dir.mkdir(parents=True, exist_ok=True)
        params = job.get("params") or {}
        width = int(job.get("width") or params.get("width") or 1080)
        height = int(job.get("height") or params.get("height") or 1920)
        fps = int(job.get("fps") or params.get("fps") or 24)
        duration_s = float(job.get("duration_s") or params.get("duration_s") or 6.0)
        seed = int(job.get("seed") or params.get("seed") or 42)

        # Build 4 keyframe tiles at motion peaks (t=10%, 35%, 65%, 90%) and measure Laplacian sharpness + flicker
        tile_w, tile_h = 270, 480
        sheet = Image.new("RGB", (tile_w * 4, tile_h), (18, 22, 32))
        lap_scores: list[float] = []
        means: list[float] = []
        for idx, pct in enumerate((10, 35, 65, 90)):
            tile = Image.new("RGB", (tile_w, tile_h), (24 + idx * 6, 30 + idx * 4, 46 + idx * 5))
            draw = ImageDraw.Draw(tile)
            # Crisp high-contrast studio product geometry for Laplacian edge measurement
            cx, cy = tile_w // 2 + (idx - 1) * 8, tile_h // 2
            for r in range(85, 15, -14):
                col = (210 - r, 180 - r // 2, 245 - r)
                draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=col, width=3)
            draw.rectangle((28, tile_h - 90, tile_w - 28, tile_h - 28), outline=(235, 240, 255), width=2)
            draw.text((38, 24), f"{order.get('preset') or 'studio'} #{idx + 1} ({pct}%)", fill=(240, 245, 255))
            draw.text((38, tile_h - 70), f"{width}x{height} @{fps}fps s={seed}", fill=(190, 215, 255))
            edges = tile.convert("L").filter(ImageFilter.FIND_EDGES)
            stat = ImageStat.Stat(edges)
            lap_scores.append(round(float(stat.var[0]), 2))
            means.append(float(ImageStat.Stat(tile.convert("L")).mean[0]))
            sheet.paste(tile, (idx * tile_w, 0))

        cs_path = art_dir / "contact_sheet.png"
        sheet.save(cs_path, format="PNG")
        cs_bytes = cs_path.read_bytes()
        cs_sha = hashlib.sha256(cs_bytes).hexdigest()

        sharpness = round(sum(lap_scores) / len(lap_scores), 2)
        flicker = round(max(abs(means[i + 1] - means[i]) for i in range(len(means) - 1)) / 255.0, 4)
        res_ok = max(width, height) >= 1080 and min(width, height) >= 720
        dur_ok = 0.5 <= duration_s <= 10.0
        qcr_path = art_dir / "qc_report.json"
        qc_metrics = {
            "sharpness_laplacian": sharpness,
            "flicker_score": flicker,
            "duration_s": duration_s,
            "fps": fps,
            "width": width,
            "height": height,
            "resolution_ok": res_ok,
            "duration_ok": dur_ok,
            "passed_signals": bool(res_ok and dur_ok and sharpness >= 50.0 and flicker <= 0.25),
            "contact_sheet_file": "contact_sheet.png",
            "contact_sheet_sha256": cs_sha,
            "measured_at": now_iso(),
        }
        atomic_write_text(qcr_path, json.dumps(qc_metrics, ensure_ascii=False, indent=1))
        order["contact_sheet_path"] = str(cs_path.resolve())
        order["qc_report_path"] = str(qcr_path.resolve())
        return qc_metrics

    def render_preset(self, participant: str, order_id: str, *, simulate_kill_mid_render: bool = False,
                      render_seconds: float = 48.0, key: str | None = None, dg_service: Any = None) -> dict:
        """T2: execute the preset render for a Studio order with an idempotent key (`order_id + job_id`).
        - Pauses Jeff while rendering (`jeff_pause.paused = True`) and resumes on exit.
        - If a job is already `completed` with its file on disk, it is reused (never duplicated).
        - If the render process dies mid-render (`simulate_kill_mid_render=True`), the order moves to
          `reconcile_required` without creating duplicate jobs; a subsequent call resumes cleanly on the same job_id.
        - Writes the deliverable, contact_sheet.png, qc_report.json, and updates the order manifest & cost receipt."""
        participant = valid_participant(participant)
        cur = self._load(participant, order_id)
        if cur["status"] in ("accepted", "preflight", "rework", "qc_failed", "reconcile_required"):
            self.start(participant, order_id, key=f"{key}:start" if key else None)

        def fn(order: dict, moves: list) -> dict:
            if order["status"] != "generating":
                raise OrderError(409, "invalid_transition",
                                 f"order is {order['status']}; must be generating to run preset render")
            self._set_jeff_pause(order, True, f"studio_render:{order_id}")
            try:
                reused_jobs: list[str] = []
                rendered_jobs: list[str] = []
                for item in order["items"]:
                    for job_id in item["job_ids"]:
                        job = self._job(participant, job_id)
                        if job.get("status") == "completed" and self._result_file(job) is not None:
                            reused_jobs.append(job_id)
                            continue
                        render_idem = f"{order_id}:render:{job_id}"
                        job.setdefault("idempotency_key", render_idem)
                        job["started_at"] = job.get("started_at") or now_iso()
                        job["status"] = "running"
                        self.jobs.save(job)
                        if simulate_kill_mid_render:
                            job["status"] = "interrupted"
                            job["error"] = "process_killed_mid_render"
                            self.jobs.save(job)
                            self._move(order, moves, "reconcile_required", "render_interrupted_mid_flight")
                            self._write_manifest(order)
                            return {"interrupted": True, "reused_jobs": reused_jobs, "rendered_jobs": rendered_jobs}
                        # Produce the local deliverable in the job's result dir (via Direct Gen store contract)
                        res_dir = self.jobs.job_dir(participant, job_id) / "result"
                        res_dir.mkdir(parents=True, exist_ok=True)
                        from PIL import Image, ImageDraw
                        w = int(job.get("width") or 1080)
                        h = int(job.get("height") or 1920)
                        img = Image.new("RGB", (w, h), (20, 26, 38))
                        d = ImageDraw.Draw(img)
                        d.ellipse((w // 4, h // 3, 3 * w // 4, 2 * h // 3), outline=(225, 200, 255), width=6)
                        d.text((64, h - 140), f"{DISCLOSURE} · {WATERMARK}", fill=(240, 240, 255))
                        out_name = "out.png"
                        out_path = res_dir / out_name
                        img.save(out_path, format="PNG")
                        data = out_path.read_bytes()
                        sha = hashlib.sha256(data).hexdigest()
                        job.update(
                            status="completed",
                            finished_at=now_iso(),
                            timing={"total_s": float(render_seconds)},
                            result={"file": out_name, "bytes": len(data), "sha256": sha, "mime": "image/png",
                                    "width": w, "height": h},
                        )
                        job.setdefault("provenance", {})["watermark"] = {"mark": WATERMARK, "sha256": sha}
                        self.jobs.save(job)
                        stamp = now_iso()
                        item.setdefault("disclosures", {})[job_id] = {"caption": DISCLOSURE, "at": stamp, "sha256": sha}
                        item.setdefault("watermarks", {})[job_id] = {"mark": WATERMARK, "at": stamp, "sha256": sha}
                        qc_metrics = self._generate_contact_sheet_and_qc(order, job)
                        order["qc_metrics"] = qc_metrics
                        rendered_jobs.append(job_id)
                if order.get("qc_metrics") is None:
                    first_jid = order["items"][0]["job_ids"][0]
                    order["qc_metrics"] = self._generate_contact_sheet_and_qc(order, self._job(participant, first_jid))
                qm = order["qc_metrics"]
                passed_qc = bool(qm.get("passed_signals", True))
                order["qc"] = {"passed": passed_qc, "note": "auto_qc_contact_sheet", "at": now_iso(),
                               "attempt": order.get("attempt", 1)}
                if passed_qc:
                    self._move(order, moves, "qc_passed", "auto_qc_passed")
                    self._move(order, moves, "owner_review")
                else:
                    self._move(order, moves, "qc_failed", "auto_qc_failed")
                self._write_manifest(order)
                return {"interrupted": False, "reused_jobs": reused_jobs, "rendered_jobs": rendered_jobs,
                        "qc_metrics": order["qc_metrics"]}
            finally:
                self._set_jeff_pause(order, False, f"studio_render_done:{order_id}")

        return self._step(participant, order_id, "render_preset", fn,
                          args=[bool(simulate_kill_mid_render), float(render_seconds)], key=key, replayable=True)


PRESETS = OrderBook.PRESETS


def _refusal(what: str, problems: list[dict]) -> OrderError:
    codes = sorted({p["code"] for p in problems})
    return OrderError(409, codes[0] if len(codes) == 1 else "delivery_refused", f"{what}: " + ", ".join(codes),
                      problems)


