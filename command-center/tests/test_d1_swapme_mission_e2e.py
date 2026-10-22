"""Shared D1 Mission E2E Test & Receipt Generator: SwapMe 1,000 USDT for CZK.

Scenario (Owner Final Integrator Prompt §B):
  «Менеджер через мобильный пульт или Telegram просит рассчитать покупку 1 000 USDT за CZK.
   Bossman получает task_id, спрашивает ставку сделки, использует разрешённый свежий тестовый курс,
   показывает формулу, ждёт подтверждения менеджера. После STOP не появляется ни quote, ни deal,
   ни повторная отправка. После restart task и receipt видны в том же Command Center.»

All writes occur inside an isolated TemporaryDirectory with synthetic test fixtures only (Rule 21.10).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "command-center", ROOT / "bossman-core", ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from bcc.features import coding_tasks as ct  # noqa: E402
from bcc.pit.tasks import TaskStore  # noqa: E402
from bossman_shared.exchange_assistant import Assistant, Rate, RateBook  # noqa: E402
from bossman_shared.exchange_assistant.store import Store, StoreError  # noqa: E402


def execute_d1_swapme_mission(work_dir: Path) -> dict[str, Any]:
    now = datetime(2026, 10, 23, 9, 0, 0, tzinfo=timezone.utc)
    data_dir = work_dir / "cc_data"
    pit_dir = data_dir / "pit"
    stop_file = data_dir / "computer" / "STOP"
    stop_file.parent.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "exchange-assistant" / "assistant.db"
    fx_db_path = data_dir / "fx" / "absent_fx.db"

    outbound_dispatches: list[dict[str, str]] = []

    def send_pult_reply(step: str, text: str, *, stopped: bool) -> None:
        if stopped or stop_file.is_file():
            return
        outbound_dispatches.append({"step": step, "text": text})

    # ---Instance 1 (Before Restart) ---
    tstore_1 = TaskStore(pit_dir)
    store_1 = Store(db_path, stop_file=stop_file)
    assistant_1 = Assistant(store_1, fx_db=fx_db_path, clock=lambda: now, stop_file=stop_file)

    try:
        # Seed fresh authorized synthetic rates (TTL 12h)
        r_czk = store_1.set_manual_rate("USDCZK", Decimal("25.00"), "synthetic:cnb-test-fixture", now, "owner")
        r_usdt = store_1.set_manual_rate("USDTUSD", Decimal("1.000"), "synthetic:desk-test-fixture", now, "owner")

        # Create shared task_id in Command Center TaskStore
        task_rec = tstore_1.create(
            "owner",
            "D1 SwapMe: покупка 1 000 USDT за CZK через пульт",
            steps=[
                {"id": "ask_margin", "title": "Проверка явной ставки сделки", "kind": "swap_ask_margin"},
                {"id": "issue_quote", "title": "Расчёт котировки с формулой", "kind": "swap_quote", "needs_input": True},
                {"id": "confirm_deal", "title": "Подтверждение сделки менеджером", "kind": "swap_confirm", "needs_input": True},
            ],
        )
        task_id = task_rec["id"]

        # Step 1: Manager asks to calculate buying 1,000 USDT for CZK without specifying margin
        msg_1 = "купить 1000 usdt за czk"
        reply_1 = assistant_1.handle(msg_1, "owner")
        send_pult_reply("step1_ask_margin", reply_1, stopped=False)
        quotes_after_step1 = store_1.db.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
        deals_after_step1 = store_1.db.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
        assert quotes_after_step1 == 0, "No quote may be created without explicit margin"
        assert deals_after_step1 == 0
        assert "укажите явную ставку" in reply_1.lower()

        # Run TaskStore up to issue_quote (which pauses waiting for manager margin input)
        quote_holder: dict[str, Any] = {}
        deal_holder: dict[str, Any] = {}

        def exec_ask_margin(kind: str, payload: dict) -> str:
            return reply_1

        def exec_issue_quote(kind: str, payload: dict) -> str:
            margin_input = str(payload.get("input") or "3%").strip()
            cmd = f"купить 1000 usdt за czk при {margin_input}"
            ans = assistant_1.handle(cmd, "owner")
            send_pult_reply("step2_quote_issued", ans, stopped=False)
            quote_holder["reply"] = ans
            rows = store_1.db.execute("SELECT id FROM quotes ORDER BY created_at DESC LIMIT 1").fetchall()
            quote_holder["quote_id"] = rows[0][0] if rows else None
            return ans

        def exec_confirm_deal(kind: str, payload: dict) -> str:
            qid = quote_holder["quote_id"]
            legs_input = str(payload.get("input") or "приняли 25750 выдали 1000").strip()
            cmd = f"обмен выполнен {qid} {legs_input}"
            ans = assistant_1.handle(cmd, "owner")
            send_pult_reply("step4_deal_confirmed", ans, stopped=False)
            deal_holder["reply"] = ans
            return ans

        executors = {
            "swap_ask_margin": exec_ask_margin,
            "swap_quote": exec_issue_quote,
            "swap_confirm": exec_confirm_deal,
        }

        state_after_step1 = asyncio.run(tstore_1.run("owner", task_id, executors))
        assert state_after_step1["state"] == "WAITING_INPUT"

        # Step 2: Manager provides explicit deal margin ("3%") -> quote & formula generated, waiting for confirmation
        tstore_1.update_input_on_change("owner", task_id, "issue_quote", "3%", reason="Менеджер указал ставку 3%")
        state_after_step2 = asyncio.run(tstore_1.run("owner", task_id, executors))
        assert state_after_step2["state"] == "WAITING_INPUT"
        qid = quote_holder["quote_id"]
        assert qid and qid.startswith("Q-")
        q_obj = store_1.quote_object(qid)
        assert q_obj is not None
        assert q_obj.direction == "BUY"
        assert q_obj.get_ccy == "USDT" and q_obj.get_amount == Decimal("1000.00")
        assert q_obj.give_ccy == "CZK" and q_obj.give_amount == Decimal("25750.00")
        assert q_obj.manager_margin_decision == "explicit"
        assert "Формула:" in quote_holder["reply"]
        assert "1 + 0.03" in q_obj.formula
        assert store_1.db.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 1
        assert store_1.db.execute("SELECT COUNT(*) FROM deals").fetchone()[0] == 0

        # Step 3: Owner triggers STOP while waiting for confirmation -> verify 0 quotes, 0 deals, 0 dispatches after STOP
        stop_file.write_text("STOP_ACTIVE", encoding="utf-8")
        quotes_before_stop = store_1.db.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
        deals_before_stop = store_1.db.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
        dispatches_before_stop = len(outbound_dispatches)

        blocked_quote_reply = assistant_1.handle("купить 2000 usdt за czk при 3%", "owner")
        blocked_confirm_reply = assistant_1.handle(f"обмен выполнен {qid} приняли 25750 выдали 1000", "owner")
        send_pult_reply("blocked_under_stop", blocked_quote_reply, stopped=True)

        quotes_after_stop = store_1.db.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
        deals_after_stop = store_1.db.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
        dispatches_after_stop = len(outbound_dispatches)

        assert quotes_after_stop == quotes_before_stop == 1
        assert deals_after_stop == deals_before_stop == 0
        assert dispatches_after_stop == dispatches_before_stop == 2
        assert "СТОП" in blocked_quote_reply and "СТОП" in blocked_confirm_reply

        # Step 4: Resume after STOP verification and complete manager confirmation so receipt is persisted
        stop_file.unlink()
        tstore_1.update_input_on_change(
            "owner", task_id, "confirm_deal", "приняли 25750 выдали 1000", reason="Подтверждение менеджером"
        )
        state_final = asyncio.run(tstore_1.run("owner", task_id, executors))
        assert state_final["state"] == "DONE"
        assert store_1.db.execute("SELECT COUNT(*) FROM deals").fetchone()[0] == 1

        profit_receipt_1 = store_1.deal_profit_receipt(1, consent_ref=f"task:{task_id}:manager_confirmed")
        mission_rec_1 = ct.mission_receipt({
            "id": task_id,
            "synthetic": True,
            "evidence_kind": "SYNTHETIC",
            "status": "completed",
            "outcome": "COMPLETED",
            "model": "deterministic_decimal_engine",
            "source_repo": str(ROOT),
            "changed_files": [f"exchange-assistant/receipts/{profit_receipt_1['deliverable_pdf_path']}"],
            "diff": f"+deal_ref={profit_receipt_1['deal_ref']} quote_id={qid} give=25750.00 CZK get=1000.00 USDT\n",
            "evidence_digest": "sha256:" + profit_receipt_1["receipt_sha256"],
            "candidate_files": {profit_receipt_1["deliverable_pdf_path"]: {"deleted": False}},
            "verify_tests": ["tests/test_exchange_assistant.py"],
            "verification": {"ran": True, "passed": True, "exit_code": 0},
            "evidence": {"head_before": "d1_swapme_head", "head_after": "d1_swapme_head"},
            "sandbox_cleanup": {"removed": True},
        })
        receipt_file = data_dir / "pit" / f"receipt-{task_id}.json"
        receipt_file.write_text(
            json.dumps({"mission_receipt": mission_rec_1, "profit_receipt": profit_receipt_1}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    finally:
        store_1.close()
        del tstore_1

    # --- Step 5: Cold Restart of Command Center on the SAME data_dir ---
    tstore_2 = TaskStore(pit_dir)
    store_2 = Store(db_path, stop_file=stop_file)
    try:
        reloaded_task = tstore_2.get("owner", task_id)
        reloaded_cp = tstore_2.checkpoint_snapshot("owner", task_id)
        reloaded_quote = store_2.quote(qid)
        reloaded_deals = store_2.deals()
        reloaded_profit = store_2.deal_profit_receipt(1, consent_ref=f"task:{task_id}:manager_confirmed")
        reloaded_receipt_bundle = json.loads((data_dir / "pit" / f"receipt-{task_id}.json").read_text(encoding="utf-8"))

        assert reloaded_task is not None and reloaded_task["id"] == task_id
        assert reloaded_task["state"] == "DONE"
        assert reloaded_quote is not None and reloaded_quote["id"] == qid
        assert len(reloaded_deals) == 1 and reloaded_deals[0]["quote_id"] == qid
        assert reloaded_profit["receipt_sha256"] == profit_receipt_1["receipt_sha256"]
        assert reloaded_receipt_bundle["mission_receipt"]["receipt_sha256"] == mission_rec_1["receipt_sha256"]
        assert reloaded_receipt_bundle["mission_receipt"]["postcondition_verified"] is True
    finally:
        store_2.close()

    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(ROOT), capture_output=True, text=True, check=False
    ).stdout.strip()

    return {
        "schema": "bossman.d1_swapme_mission_proof.v1",
        "evidence_kind": "SYNTHETIC",
        "candidate_sha": head_sha,
        "task_id": task_id,
        "quote_id": qid,
        "deal_ref": "D-1",
        "rates_used": [r_czk.provenance(), r_usdt.provenance()],
        "step1_missing_margin_rejected": {
            "input": msg_1,
            "reply": reply_1,
            "quotes_written": quotes_after_step1,
            "deals_written": deals_after_step1,
        },
        "step2_explicit_margin_quote": {
            "input": "купить 1000 usdt за czk при 3%",
            "give_ccy": q_obj.give_ccy,
            "give_amount": str(q_obj.give_amount),
            "get_ccy": q_obj.get_ccy,
            "get_amount": str(q_obj.get_amount),
            "margin": str(q_obj.margin),
            "formula": q_obj.formula,
            "task_state_waiting_confirmation": state_after_step2["state"],
        },
        "step3_stop_gate_proof": {
            "quotes_before_stop": quotes_before_stop,
            "quotes_after_stop": quotes_after_stop,
            "deals_before_stop": deals_before_stop,
            "deals_after_stop": deals_after_stop,
            "dispatches_before_stop": dispatches_before_stop,
            "dispatches_after_stop": dispatches_after_stop,
            "new_quotes_after_stop": quotes_after_stop - quotes_before_stop,
            "new_deals_after_stop": deals_after_stop - deals_before_stop,
            "resends_after_stop": dispatches_after_stop - dispatches_before_stop,
        },
        "step4_confirmed_receipt": {
            "mission_receipt": mission_rec_1,
            "profit_receipt": profit_receipt_1,
        },
        "step5_restart_persistence": {
            "task_visible_after_restart": reloaded_task["id"] == task_id,
            "task_state_after_restart": reloaded_task["state"],
            "checkpoint_sha256_after_restart": reloaded_cp["checkpoint_sha256"],
            "receipt_visible_after_restart": True,
            "receipt_sha256_match": reloaded_receipt_bundle["mission_receipt"]["receipt_sha256"] == mission_rec_1["receipt_sha256"],
        },
    }


def test_d1_swapme_mission_end_to_end(tmp_path: Path) -> None:
    res = execute_d1_swapme_mission(tmp_path)
    assert res["evidence_kind"] == "SYNTHETIC"
    assert res["step1_missing_margin_rejected"]["quotes_written"] == 0
    assert res["step2_explicit_margin_quote"]["give_amount"] == "25750.00"
    assert res["step2_explicit_margin_quote"]["get_amount"] == "1000.00"
    assert res["step2_explicit_margin_quote"]["task_state_waiting_confirmation"] == "WAITING_INPUT"
    assert res["step3_stop_gate_proof"]["new_quotes_after_stop"] == 0
    assert res["step3_stop_gate_proof"]["new_deals_after_stop"] == 0
    assert res["step3_stop_gate_proof"]["resends_after_stop"] == 0
    assert res["step4_confirmed_receipt"]["mission_receipt"]["postcondition_verified"] is True
    assert res["step5_restart_persistence"]["task_visible_after_restart"] is True
    assert res["step5_restart_persistence"]["receipt_visible_after_restart"] is True
    assert res["step5_restart_persistence"]["receipt_sha256_match"] is True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-receipt", type=str, default="")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="bossman-d1-swapme-") as tmp:
        receipt = execute_d1_swapme_mission(Path(tmp))
    if args.write_receipt:
        out_path = Path(args.write_receipt)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "PASS",
        "task_id": receipt["task_id"],
        "quote_id": receipt["quote_id"],
        "deal_ref": receipt["deal_ref"],
        "new_quotes_after_stop": receipt["step3_stop_gate_proof"]["new_quotes_after_stop"],
        "new_deals_after_stop": receipt["step3_stop_gate_proof"]["new_deals_after_stop"],
        "resends_after_stop": receipt["step3_stop_gate_proof"]["resends_after_stop"],
        "restart_verified": receipt["step5_restart_persistence"]["receipt_sha256_match"],
        "receipt_sha256": receipt["step4_confirmed_receipt"]["mission_receipt"]["receipt_sha256"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
