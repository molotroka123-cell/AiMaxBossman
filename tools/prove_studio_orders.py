"""Proof (in-process, no GPU): a studio order goes quote -> accept -> disclose -> deliver with an exact receipt.

    python tools/prove_studio_orders.py [--out docs/audits/2026-10-24-earn-studio-orders]

Scenario B «earn money with local compute»: Direct-Generator jobs sold as a studio service. Everything happens in a
fresh TEMP data dir (BCC_DATA_DIR is pointed at it before bcc is imported, so Settings() can never fall back to the
owner's real data). Jobs are synthetic job.json records with recorded seconds and a tiny result file; nothing is
generated, no model, ComfyUI, sd-cli or GPU is touched, no network call is made, no service is started or stopped.

The order is driven through the real /api/studio routes (the studio_orders router on a bare FastAPI app over ASGI,
no auth layer: auth is the app's shared token middleware, covered by tests/test_studio_orders.py). The cost is
cross-checked against tools/bossman_business.compute_unit_economics (float) on the same seconds/W/kWh price.
Negative controls must all be REFUSED (incl. a third-party brand in a prompt without the owner's ownership record).
Model licence is an owner-only WARNING (owner decision 24.10): the proof checks it is recorded for the owner and absent
from the client's view. Receipt: receipt.json with price/cost/margin, licence warnings, negatives, revenue NOT_RUN
(no real client, no payment: this shows capability, not earned money), verdict PASS/FAIL. Exit 0 only on PASS.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "docs" / "audits" / "2026-10-24-earn-studio-orders"
CLIENT = "c" * 64          # synthetic client participant key
OTHER = "d" * 64           # another client


def _job(store, who: str, *, kind: str, seconds: float | None, status: str = "completed", consent=None,
         facelock=False, model: str = "z-image-turbo", prompt: str = "ceramic mug on a linen cloth, soft daylight") -> str:
    job_id = uuid.uuid4().hex
    job = {"job_id": job_id, "participant": who, "kind": kind, "model": model, "status": status,
           "raw_prompt": prompt, "effective_prompt": prompt, "negative": "",
           "created_at": "2026-10-24T12:00:00+00:00", "started_at": "2026-10-24T12:00:02+00:00",
           "finished_at": "2026-10-24T12:10:00+00:00" if status == "completed" else None,
           "provenance": {} if consent is None else {"consent_confirmed": consent}, "result": None, "timeline": []}
    if facelock:
        job["facelock"] = {"requested": True, "status": "applied"}
    if status == "completed":
        job["timing"] = {"total_s": seconds}
        res = store.job_dir(who, job_id) / "result"
        res.mkdir(parents=True, exist_ok=True)
        data = f"synthetic proof file {job_id}".encode()
        name = "out.mp4" if kind in ("video", "faceswap") else "out.png"
        (res / name).write_bytes(data)
        job["result"] = {"file": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    store.save(job)
    return job_id


async def run(data_dir: Path) -> dict:
    import httpx
    from fastapi import FastAPI

    from bcc.direct_gen.store import JobStore
    from bcc.features import studio_orders

    store = JobStore(data_dir)
    app = FastAPI()
    app.state.svc = SimpleNamespace(settings=SimpleNamespace(data_dir=data_dir), direct_gen=SimpleNamespace(store=store))
    app.include_router(studio_orders.router, prefix="/api")
    owner = {"X-Participant": "owner"}
    out: dict = {"negatives": {}}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proof") as c:
        async def post(path, body=None, who=owner):
            # the owner runs the studio and addresses the client's orders with ?client=<key>
            q = f"?client={CLIENT}" if who is owner else ""
            r = await c.post("/api/studio" + path + q, json=body, headers=who)
            return r.status_code, r.json()

        # a small shop's order: 3 product photo edits + 1 short 9:16 clip (measured-looking seconds)
        # photo edits on the model the owner's chat photo.edit really uses (licence not in the repo table -> warning)
        photos = [_job(store, CLIENT, kind="photo", seconds=s, model="qwen-image-2.1-uc-edit") for s in (41.37, 38.9, 44.05)]
        clip = _job(store, CLIENT, kind="video", seconds=912.6, model="wan2.2-ti2v-5b")
        code, order = await post("/orders", {"items": [{"kind": "photo_edit", "job_ids": [p]} for p in photos]
                                             + [{"kind": "clip", "job_ids": [clip]}], "note": "synthetic proof",
                                             "brief": "3 product photos + one 9:16 clip for a ceramics shop"})
        assert code == 201, order
        oid = order["order_id"]
        out["quote"] = order["receipt"]
        out["steps"] = [("create", code, order["status"])]
        for step in ("accept", "start", "disclose", "deliver"):
            code, body = await post(f"/orders/{oid}/{step}")
            out["steps"].append((step, code, body.get("status") if code == 200 else body))
        out["delivered"] = body
        r = await c.get(f"/api/studio/orders/{oid}", headers={"X-Participant": OTHER})
        out["negatives"]["cross_participant_read"] = {"status": r.status_code, "refused": r.status_code == 404}
        r = await c.get(f"/api/studio/orders/{oid}", headers={"X-Participant": CLIENT})
        leaks = [w for w in ("licence", "qwen-image", "cost", "margin", "synthetic proof") if w in r.text]
        out["client_view"] = {"status": r.status_code, "leaks": leaks, "body": r.json()}

        async def refused_delivery(name, items, expect, disclose=True):
            code, o = await post("/orders", {"items": items})
            assert code == 201, o
            await post(f"/orders/{o['order_id']}/accept")
            if disclose:
                await post(f"/orders/{o['order_id']}/disclose")
            code, body = await post(f"/orders/{o['order_id']}/deliver")
            err = body.get("error") or body.get("detail") or {}
            out["negatives"][name] = {"status": code, "code": err.get("code"), "refused": code == 409 and err.get("code") == expect}

        await refused_delivery("unfinished_job", [{"kind": "image", "job_ids": [
            _job(store, CLIENT, kind="photo", seconds=None, status="generating")]}], "job_not_completed", disclose=False)
        await refused_delivery("no_disclosure", [{"kind": "image", "job_ids": [
            _job(store, CLIENT, kind="photo", seconds=30)]}], "disclosure_missing", disclose=False)
        await refused_delivery("faceswap_without_consent", [{"kind": "clip", "job_ids": [
            _job(store, CLIENT, kind="faceswap", seconds=300, consent=False, model="faceswap-facefusion")]}], "consent_missing")
        await refused_delivery("facelock_without_consent", [{"kind": "photo_edit", "job_ids": [
            _job(store, CLIENT, kind="photo", seconds=50, facelock=True)]}], "consent_missing")

        await refused_delivery("brand_in_prompt_without_ownership", [{"kind": "image", "job_ids": [
            _job(store, CLIENT, kind="photo", seconds=40, prompt="running shoe with the Nike swoosh")]}], "brand_not_cleared")
        code, o = await post("/orders", {"brief": "Starbucks-style latte ad", "items": [{"kind": "image", "job_ids": [
            _job(store, CLIENT, kind="photo", seconds=40)]}]})
        code, body = await post(f"/orders/{o['order_id']}/brands", {"brands": ["Starbucks"]}, who={"X-Participant": CLIENT})
        out["negatives"]["client_clears_brand_itself"] = {"status": code, "refused": code == 403}

        for name, price in (("zero_price", "0"), ("negative_price", "-4.99")):
            code, body = await post("/orders", {"items": [{"kind": "image", "price": price,
                                                           "job_ids": [_job(store, CLIENT, kind="photo", seconds=10)]}]})
            err = body.get("error") or body.get("detail") or {}
            out["negatives"][name] = {"status": code, "code": err.get("code"), "refused": code == 422}

        code, o = await post("/orders", {"items": [{"kind": "image", "job_ids": [_job(store, CLIENT, kind="photo", seconds=20)]}]})
        await post(f"/orders/{o['order_id']}/accept")
        await post(f"/orders/{o['order_id']}/disclose")
        await post(f"/orders/{o['order_id']}/cancel", {"reason": "proof"})
        code, body = await post(f"/orders/{o['order_id']}/deliver")
        err = body.get("error") or body.get("detail") or {}
        out["negatives"]["cancelled_then_deliver"] = {"status": code, "code": err.get("code"),
                                                      "refused": code == 409 and err.get("code") == "invalid_transition"}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    with tempfile.TemporaryDirectory(prefix="bossman-studio-proof-") as tmp:
        data_dir = Path(tmp) / "data"
        os.environ["BCC_DATA_DIR"] = str(data_dir)          # before bcc is imported: never the owner's real data
        sys.path.insert(0, str(ROOT / "command-center"))
        sys.path.insert(0, str(ROOT / "tools"))
        from bossman_business import compute_unit_economics

        result = asyncio.run(run(data_dir))
        orders_in_temp = len(list(data_dir.glob("direct-gen/participants/*/orders/*.json")))

    d = result["delivered"]
    receipt = d.get("receipt") or {}
    secs, watts, kwh = Decimal(receipt.get("seconds", "0")), Decimal(receipt.get("power_watts", "0")), Decimal(receipt.get("kwh_price", "0"))
    expected_cost = (secs * watts / Decimal(3_600_000) * kwh).quantize(Decimal("0.000001"))
    ue = compute_unit_economics(runtime_seconds=float(secs), pc_power_watts=float(watts), kwh_price_usd=float(kwh),
                                report_price_usd=float(receipt.get("price", "0")))
    checks = {
        "delivered": d.get("status") == "delivered",
        "steps_all_200": all(code in (200, 201) for _, code, _ in result["steps"]),
        "cost_exact_decimal": receipt.get("cost") == str(expected_cost),
        "margin_is_price_minus_cost": Decimal(receipt.get("margin", "0")) == Decimal(receipt.get("price", "0")) - Decimal(receipt.get("cost", "0")),
        "cost_matches_compute_unit_economics": abs(ue["energy_cost_usd"] - float(expected_cost)) < 1e-6,
        "every_file_disclosed": all(v.get("caption") == "AI-generated · Bossman Motion Studio"
                                    for it in d.get("items", []) for v in it.get("disclosures", {}).values())
                                and sum(len(it.get("disclosures", {})) for it in d.get("items", [])) == 4,
        "all_negatives_refused": all(n["refused"] for n in result["negatives"].values()),
        "licence_warning_recorded_for_owner": [w["model"] for w in receipt.get("licence_warnings", [])] == ["qwen-image-2.1-uc-edit"],
        "client_view_has_no_cost_margin_or_licence": result["client_view"]["status"] == 200 and not result["client_view"]["leaks"],
        # main + 5 refused deliveries + brand-ownership attempt + cancelled; bad prices never stored
        "orders_written_to_temp_data_dir": orders_in_temp == 8,
    }
    verdict = "PASS" if all(checks.values()) else "FAIL"
    doc = {
        "proof": "studio orders (scenario B: local AI photo/video studio as a service)",
        "date": "2026-10-24", "verdict": verdict, "checks": checks,
        "order": {"lines": "3 x photo_edit + 1 x clip (synthetic jobs, recorded seconds)",
                  "currency": receipt.get("currency"), "price": receipt.get("price"), "cost": receipt.get("cost"),
                  "margin": receipt.get("margin"), "margin_ratio": receipt.get("margin_ratio"),
                  "seconds": receipt.get("seconds"), "energy_kwh": receipt.get("energy_kwh"),
                  "power_watts": receipt.get("power_watts"), "kwh_price": receipt.get("kwh_price"),
                  "cost_basis": receipt.get("cost_basis"), "lines_detail": receipt.get("lines")},
        "revenue": {"status": "NOT_RUN", "reason": "no real client and no payment; this receipt proves the order/"
                    "receipt capability on synthetic jobs, not earned money"},
        "licence_warnings_owner_only": receipt.get("licence_warnings"),
        "client_view": result["client_view"]["body"],
        "quote_at_create": {k: result["quote"].get(k) for k in ("price", "cost", "margin", "cost_complete")},
        "cross_check_compute_unit_economics": {"energy_cost_usd_float": ue["energy_cost_usd"],
                                               "decimal_cost": str(expected_cost)},
        "steps": [{"step": s, "http": c, "status": st} for s, c, st in result["steps"]],
        "negatives": result["negatives"],
        "not_proven": ["live run on :8801 through the pult/chat path (owner-gated; backend not restarted by this tool)",
                       "licence table covers only models whose licence the repo states; the rest are warnings, unverified",
                       "brand gate is a word denylist on brief + prompts: it does not see logos in the pixels",
                       "real client payment (no payment flow exists; price/margin are a receipt, not revenue)",
                       "power_watts 180 is a configured default, not a wattmeter measurement of this PC"],
        "safety": "temp BCC_DATA_DIR, synthetic jobs, no generation, no model/GPU/network call",
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "receipt.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"verdict": verdict, "checks": checks, "price": receipt.get("price"), "cost": receipt.get("cost"),
                      "margin": receipt.get("margin"), "revenue": "NOT_RUN"}, ensure_ascii=False, indent=1))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
