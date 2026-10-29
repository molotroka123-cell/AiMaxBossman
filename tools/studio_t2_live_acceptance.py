"""T2 Studio Live Acceptance (TZ 2.5 §5 T2 & Argon Pair Prompt §3.4):
1. Run 1 (synthetic client, product without faces): quote -> accept -> render_preset (product_spin) ->
   contact_sheet.png + qc_report.json -> owner_review -> approve ('yes') -> deliver -> delivery_link (200 for client A,
   404 for client B, 404 after revoke).
2. Run 2 (killed mid-render recovery): quote -> accept -> render_preset(simulate_kill_mid_render=True) ->
   reconcile_required (Jeff pause cleared, 0 duplicate jobs) -> render_preset(simulate_kill_mid_render=False) ->
   resumes on the exact same job_id -> owner_review -> approve -> deliver.
3. Run 3 (live owner pult card): creates/updates a ready-to-review Studio preset order in the live CommandCenter data
   dir under participant 'owner' in status 'owner_review' with contact_sheet.png and qc_report.json so the owner's
   Mobile Pult (:8840) displays the card with «Принять» and «На доработку» buttons.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "command-center"))
sys.path.insert(0, str(ROOT / "apps" / "bossman-mobile"))

from bcc.direct_gen.orders import ClientTokens, OrderBook, OrderError  # noqa: E402
from bcc.direct_gen.store import JobStore  # noqa: E402


CLIENT_A = "c1" * 32
CLIENT_B = "c2" * 32


def run_acceptance(live_data_dir: Path | None = None) -> dict:
    # Run 1 & Run 2 execute in an isolated temporary Studio store so synthetic test orders do not pollute client history
    with tempfile.TemporaryDirectory(prefix="bossman-studio-t2-acc-") as tmp:
        tmp_dir = Path(tmp)
        store = JobStore(tmp_dir)
        book = OrderBook(tmp_dir, store)
        tokens = ClientTokens(tmp_dir)

        tok_a = tokens.issue(CLIENT_A, label="synthetic-client-a")
        tok_b = tokens.issue(CLIENT_B, label="synthetic-client-b")
        assert tokens.resolve(tok_a["token"]) == CLIENT_A
        assert tokens.resolve(tok_b["token"]) == CLIENT_B

        # --- Run 1: full path quote -> accept -> render_preset -> QC -> approve -> deliver + delivery_link ---
        o1 = book.create_preset_order(
            CLIENT_A,
            "product_spin",
            "Synthetic matte-black espresso grinder on studio turntable, owned product photo, no faces",
            face=False,
            source_rights_confirmed=True,
            seed=20261029,
        )
        oid1 = o1["order_id"]
        book.accept(CLIENT_A, oid1, actor="client")
        r1 = book.render_preset(CLIENT_A, oid1, simulate_kill_mid_render=False)
        assert r1["status"] == "owner_review"
        assert Path(r1["contact_sheet_path"]).is_file()
        assert Path(r1["qc_report_path"]).is_file()
        ap1 = book.approve("owner", CLIENT_A, oid1, "yes")
        assert ap1["status"] == "approved"
        deliv1 = book.deliver(CLIENT_A, oid1)
        assert deliv1["status"] == "delivered"
        dlink = book.issue_delivery_link(CLIENT_A, oid1, ttl_hours=48)
        opened_a = book.open_delivery_link(dlink["token"], caller_client=CLIENT_A)
        assert opened_a["order"]["order_id"] == oid1

        cross_client_status = 200
        try:
            book.open_delivery_link(dlink["token"], caller_client=CLIENT_B)
        except OrderError as exc:
            cross_client_status = exc.status
        assert cross_client_status == 404

        book.revoke_delivery_link(CLIENT_A, oid1, reason="acceptance test revoke check")
        revoked_status = 200
        try:
            book.open_delivery_link(dlink["token"], caller_client=CLIENT_A)
        except OrderError as exc:
            revoked_status = exc.status
        assert revoked_status == 404

        run1_summary = {
            "order_id": oid1,
            "preset": "product_spin",
            "model": r1["manifest"]["jobs"][0]["model"],
            "licence_id": r1["manifest"]["jobs"][0]["licence_id"],
            "status_flow": [t["status"] for t in deliv1["timeline"]],
            "cost": deliv1["receipt"]["cost"],
            "price": deliv1["receipt"]["price"],
            "margin": deliv1["receipt"]["margin"],
            "qc_metrics": r1["qc_metrics"],
            "manifest_sha256": hashlib.sha256(
                json.dumps(deliv1["manifest"], sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "delivery_link_checks": {
                "client_a_open": 200,
                "client_b_cross_open": cross_client_status,
                "after_revoke_open": revoked_status,
            },
        }

        # --- Run 2: killed process mid-render -> reconcile_required -> resume without duplicate jobs ---
        o2 = book.create_preset_order(
            CLIENT_A,
            "hand_reveal",
            "Synthetic anodized aluminum mechanical keyboard reveal on studio desk, owned product, no faces",
            face=False,
            source_rights_confirmed=True,
            seed=20261030,
        )
        oid2 = o2["order_id"]
        jobs_initial = list(o2["items"][0]["job_ids"])
        book.accept(CLIENT_A, oid2, actor="client")

        killed = book.render_preset(CLIENT_A, oid2, simulate_kill_mid_render=True)
        assert killed["status"] == "reconcile_required"
        assert killed["items"][0]["job_ids"] == jobs_initial
        jeff_after_kill = json.loads(book.jeff_pause_path.read_text("utf-8"))
        assert jeff_after_kill["paused"] is False

        resumed = book.render_preset(CLIENT_A, oid2, simulate_kill_mid_render=False)
        assert resumed["status"] == "owner_review"
        assert resumed["items"][0]["job_ids"] == jobs_initial
        assert len(resumed["items"][0]["job_ids"]) == 1
        book.approve("owner", CLIENT_A, oid2, "yes")
        deliv2 = book.deliver(CLIENT_A, oid2)
        assert deliv2["status"] == "delivered"

        run2_summary = {
            "order_id": oid2,
            "preset": "hand_reveal",
            "job_ids_before_kill": jobs_initial,
            "job_ids_after_resume": resumed["items"][0]["job_ids"],
            "duplicate_jobs_created": 0,
            "status_after_kill": killed["status"],
            "jeff_paused_after_kill": jeff_after_kill["paused"],
            "final_status": deliv2["status"],
            "status_flow": [t["status"] for t in deliv2["timeline"]],
            "qc_metrics": resumed["qc_metrics"],
        }

    # --- Run 3: ensure live owner pult (:8840) has an active owner_review card for «Принять / На доработку» ---
    if live_data_dir is None:
        live_data_dir = Path(os.environ.get("LOCALAPPDATA", r"C:\Users\asd\AppData\Local")) / "Bossman" / "CommandCenter"

    pult_card = None
    if live_data_dir.is_dir():
        live_store = JobStore(live_data_dir)
        live_book = OrderBook(live_data_dir, live_store)
        existing_review = [
            o for o in live_book.list("owner")
            if o.get("status") == "owner_review" and o.get("qc_metrics")
        ]
        if existing_review:
            card_order = existing_review[0]
        else:
            created = live_book.create_preset_order(
                "owner",
                "product_spin",
                "Commercial demo preset: Ceramic Studio Pour-Over Kettle 360 spin (synthetic product, no faces)",
                note="T2 live acceptance card for owner pult decision («Принять» / «На доработку»)",
                face=False,
                source_rights_confirmed=True,
                seed=777,
            )
            live_book.accept("owner", created["order_id"], actor="owner")
            card_order = live_book.render_preset("owner", created["order_id"], simulate_kill_mid_render=False)

        pult_card = {
            "order_id": card_order["order_id"],
            "participant": "owner",
            "status": card_order["status"],
            "preset": card_order.get("preset"),
            "price": card_order["receipt"]["price"],
            "cost": card_order["receipt"]["cost"],
            "margin": card_order["receipt"]["margin"],
            "qc_metrics": card_order.get("qc_metrics"),
            "contact_sheet_exists": bool(
                card_order.get("contact_sheet_path") and Path(card_order["contact_sheet_path"]).is_file()
            ),
            "qc_report_exists": bool(
                card_order.get("qc_report_path") and Path(card_order["qc_report_path"]).is_file()
            ),
            "mobile_pult_endpoints": {
                "list": "/m/api/studio/orders",
                "preview": f"/m/api/studio/orders/{card_order['order_id']}/preview",
                "approve": f"POST /m/api/studio/orders/{card_order['order_id']}/approve",
                "rework": f"POST /m/api/studio/orders/{card_order['order_id']}/rework",
            },
        }

    return {
        "schema": "bossman.studio.t2-live-acceptance.v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "status": "PASS",
        "run1_full_e2e_delivery": run1_summary,
        "run2_kill_mid_render_recovery": run2_summary,
        "run3_owner_pult_card": pult_card,
        "cloud_cost_usd": 0.0,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run T2 Studio live acceptance (e2e + kill recovery + owner pult card).")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    receipt = run_acceptance()
    (out_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps({
        "status": receipt["status"],
        "run1_order_id": receipt["run1_full_e2e_delivery"]["order_id"],
        "run2_order_id": receipt["run2_kill_mid_render_recovery"]["order_id"],
        "run2_duplicates": receipt["run2_kill_mid_render_recovery"]["duplicate_jobs_created"],
        "pult_card_order_id": (receipt["run3_owner_pult_card"] or {}).get("order_id"),
        "pult_card_status": (receipt["run3_owner_pult_card"] or {}).get("status"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
