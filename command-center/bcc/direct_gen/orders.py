"""Studio orders: Direct-Generator jobs sold as a service (scenario B «local AI photo/video studio»).

An order belongs to ONE client participant (the same key as their jobs) and lists line items
``{kind: photo_edit|image|clip, job_ids: [...]}``. Status: quoted -> accepted -> in_work -> delivered, or cancelled
from any state before delivered.

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
    logo). The negative prompt is not scanned («no Nike logo» is not a request for one).
Model licence (owner decision 24.10): NOT a delivery gate. Every line records the generating model of each job; a
model whose licence is unknown or non-commercial in LICENCES (+ optional licences.json) becomes a ``licence_warnings``
entry in the owner's view and the frozen receipt. It is never part of the client view.

Views: ``view`` is the owner's (cost, margin, licence warnings, brand flags); ``client_view`` is what the client may
see (price, status, deliverables with their disclosure) - no cost, margin, note, models or warnings.

Layout: <data_dir>/direct-gen/participants/<key>/orders/<order_id>.json (atomic writes, like job.json); optional
owner config in <data_dir>/direct-gen/studio/: price_list.json, brands.json ({"denylist": [...]}, replaces the
default list), licences.json ({model_id: {"licence": str, "commercial": true|false|null}}, merged over LICENCES).
Another participant's order id is simply not found: every path is built from that participant's own key.
All methods are synchronous file I/O; the API calls them through asyncio.to_thread.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .store import JOB_ID, JobStore, atomic_write_text, read_text_retry, valid_participant

ORDER_ID = re.compile(r"^[0-9a-f]{32}$")
KINDS = ("photo_edit", "image", "clip")
# which Direct-Generator job kinds may stand behind a line item
JOB_KINDS = {"photo_edit": ("photo", "image"), "image": ("photo", "image"), "clip": ("video", "faceswap")}
STATUSES = ("quoted", "accepted", "in_work", "delivered", "cancelled")
DISCLOSURE = "AI-generated · Bossman Motion Studio"
DISCLOSURE_MARK = "AI-generated"
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


class OrderBook:
    def __init__(self, data_dir: Path, jobs: JobStore):
        self.data_dir = Path(data_dir)
        self.jobs = jobs
        studio = self.data_dir / "direct-gen" / "studio"
        self.price_list_path = studio / "price_list.json"
        self.brands_path = studio / "brands.json"
        self.licences_path = studio / "licences.json"

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
            return json.loads(read_text_retry(self._path(participant, order_id)))
        except (OSError, ValueError):
            raise OrderError(404, "unknown_order", "order not found") from None

    def _raw_list(self, participant: str) -> list[dict]:
        base = self._dir(participant)
        out = []
        for path in base.glob("*.json") if base.is_dir() else []:
            try:
                out.append(json.loads(read_text_retry(path)))
            except (OSError, ValueError):
                continue
        return sorted(out, key=lambda o: o.get("created_at", ""), reverse=True)

    def _job(self, participant: str, job_id: str) -> dict:
        try:
            return self.jobs.load(participant, job_id)
        except KeyError:
            raise OrderError(422, "unknown_job", f"job {str(job_id)[:40]} not found for this client") from None

    # ---------- receipt ----------

    def receipt(self, order: dict) -> dict:
        """Price (frozen in the order), live cost from the jobs' recorded seconds, margin. A delivered order keeps
        the receipt frozen at delivery."""
        if order.get("status") == "delivered" and order.get("receipt"):
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

    def view(self, order: dict) -> dict:
        """The OWNER's view: receipt with cost and margin, licence warnings, brand flags."""
        out = {k: v for k, v in order.items() if k != "receipt"}
        out["receipt"] = self.receipt(order)
        out["licence_warnings"] = order.get("licence_warnings") or []
        if order.get("status") not in ("delivered", "cancelled"):
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
        return {"order_id": order["order_id"], "status": order["status"], "brief": order.get("brief", ""),
                "currency": order["pricing"]["currency"], "price": str(price), "items": items,
                "created_at": order["created_at"], "updated_at": order["updated_at"],
                "timeline": [{"status": t["status"], "at": t["at"]} for t in order.get("timeline", [])]}

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
            items.append(item)
        pl = self.price_list()
        self._price_items(items, pl)
        order = {
            "order_id": uuid.uuid4().hex, "participant": participant, "status": "quoted",
            "note": str(body.get("note") or "")[:500], "brief": str(body.get("brief") or "")[:2000],
            "items": items, "pricing": self._pricing(pl), "brand_ownership": {},
            "created_at": now_iso(), "updated_at": now_iso(),
            "timeline": [{"status": "quoted", "at": now_iso()}],
        }
        order["licence_warnings"] = self.licence_warnings(order)
        self._save(order)
        return self.view(order)

    def get(self, participant: str, order_id: str) -> dict:
        return self.view(self._load(valid_participant(participant), order_id))

    def list(self, participant: str) -> list[dict]:
        return [self.view(o) for o in self._raw_list(valid_participant(participant))]

    def get_for_client(self, participant: str, order_id: str) -> dict:
        return self.client_view(self._load(valid_participant(participant), order_id))

    def list_for_client(self, participant: str) -> list[dict]:
        return [self.client_view(o) for o in self._raw_list(valid_participant(participant))]

    def set_brand_ownership(self, actor: str, participant: str, order_id: str, brands: list[str],
                            note: str = "") -> dict:
        """OWNER only: record that this client owns the listed brands (clears them for delivery of this order)."""
        if valid_participant(actor) != "owner":
            raise OrderError(403, "owner_only", "only the owner can record brand ownership")
        order = self._load(valid_participant(participant), order_id)
        if order["status"] in ("delivered", "cancelled"):
            raise OrderError(409, "invalid_transition", f"order is {order['status']}")
        names = [str(b).strip()[:80] for b in brands or [] if str(b).strip()]
        if not names:
            raise OrderError(422, "brands_required", "name at least one brand")
        owned = order.setdefault("brand_ownership", {})
        for b in names:
            owned[b.lower()] = {"brand": b, "set_by": "owner", "at": now_iso(), "note": str(note or "")[:200]}
        order["updated_at"] = now_iso()
        self._save(order)
        return self.view(order)

    def _move(self, order: dict, status: str, allowed: tuple[str, ...], note: str = "") -> None:
        if order["status"] not in allowed:
            raise OrderError(409, "invalid_transition", f"order is {order['status']}; cannot become {status}")
        order["status"], order["updated_at"] = status, now_iso()
        order["timeline"].append({"status": status, "at": order["updated_at"], **({"note": note} if note else {})})

    def quote(self, participant: str, order_id: str) -> dict:
        """Re-price a still-quoted order from the current price list (after acceptance the price is frozen)."""
        order = self._load(valid_participant(participant), order_id)
        if order["status"] != "quoted":
            raise OrderError(409, "invalid_transition", f"order is {order['status']}; only a quoted order is re-quoted")
        pl = self.price_list()
        self._price_items(order["items"], pl)
        order["pricing"], order["updated_at"] = self._pricing(pl), now_iso()
        order["licence_warnings"] = self.licence_warnings(order)
        order["timeline"].append({"status": "quoted", "at": order["updated_at"], "note": "requoted"})
        self._save(order)
        return self.view(order)

    def accept(self, participant: str, order_id: str) -> dict:
        order = self._load(valid_participant(participant), order_id)
        self._move(order, "accepted", ("quoted",))
        self._save(order)
        return self.view(order)

    def start(self, participant: str, order_id: str) -> dict:
        order = self._load(valid_participant(participant), order_id)
        self._move(order, "in_work", ("accepted",))
        self._save(order)
        return self.view(order)

    def cancel(self, participant: str, order_id: str, reason: str = "") -> dict:
        order = self._load(valid_participant(participant), order_id)
        self._move(order, "cancelled", ("quoted", "accepted", "in_work"), str(reason or "")[:200])
        self._save(order)
        return self.view(order)

    def _result_file(self, job: dict) -> Path | None:
        res = job.get("result") or {}
        name = res.get("file")
        if not isinstance(name, str) or not name or "/" in name or "\\" in name or name.startswith("."):
            return None
        path = self.jobs.job_dir(job["participant"], job["job_id"]) / "result" / name
        return path if path.is_file() else None

    def disclose(self, participant: str, order_id: str, job_ids: list[str] | None = None,
                 caption: str = DISCLOSURE) -> dict:
        """Record the AI-generated disclosure for the finished files of this order (all, or the listed jobs). The
        caption is bound to the file's SHA-256, so a file swapped afterwards is not covered."""
        order = self._load(valid_participant(participant), order_id)
        if order["status"] in ("delivered", "cancelled"):
            raise OrderError(409, "invalid_transition", f"order is {order['status']}")
        caption = str(caption or "").strip()[:200]
        if DISCLOSURE_MARK.lower() not in caption.lower():
            raise OrderError(422, "disclosure_invalid", f"the caption must say «{DISCLOSURE_MARK}»")
        wanted = set(job_ids) if job_ids else None
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
                    raise OrderError(409, "job_not_completed", f"job {job_id} has no finished file to disclose")
                item.setdefault("disclosures", {})[job_id] = {"caption": caption, "sha256": sha,
                                                               "file": job["result"]["file"], "at": now_iso()}
                done.append(job_id)
        order["updated_at"] = now_iso()
        self._save(order)
        return self.view(order) | {"disclosed": done}

    def delivery_problems(self, order: dict) -> list[dict]:
        """Every reason this order cannot be delivered right now (empty list = deliverable)."""
        problems = []
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
                disc = (item.get("disclosures") or {}).get(job_id) or {}
                if DISCLOSURE_MARK.lower() not in str(disc.get("caption", "")).lower():
                    problems.append({"job_id": job_id, "code": "disclosure_missing"})
                elif disc.get("sha256") != job["result"].get("sha256"):
                    problems.append({"job_id": job_id, "code": "disclosure_stale"})
                if needs_consent(job, item) and not consent_recorded(job, item):
                    problems.append({"job_id": job_id, "code": "consent_missing"})
        for flag in self.brand_flags(order):
            if not flag["cleared"]:
                problems.append({"code": "brand_not_cleared", "brand": flag["brand"], "where": flag["where"]})
        return problems

    def deliver(self, participant: str, order_id: str) -> dict:
        order = self._load(valid_participant(participant), order_id)
        if order["status"] not in ("accepted", "in_work"):
            raise OrderError(409, "invalid_transition", f"order is {order['status']}; cannot become delivered")
        problems = self.delivery_problems(order)
        if problems:
            codes = sorted({p["code"] for p in problems})
            raise OrderError(409, codes[0] if len(codes) == 1 else "delivery_refused",
                             "cannot deliver: " + ", ".join(codes), problems)
        receipt = self.receipt(order)
        order["licence_warnings"] = self.licence_warnings(order)
        self._move(order, "delivered", ("accepted", "in_work"))
        order["receipt"] = receipt | {"frozen_at": order["updated_at"], "licence_warnings": order["licence_warnings"]}
        self._save(order)
        return self.view(order)
