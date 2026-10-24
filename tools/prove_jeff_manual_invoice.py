"""In-process proof: Jeff's paid documents over a MANUAL bank-transfer invoice (Telegram Stars paused).

    python tools/prove_jeff_manual_invoice.py [--out docs/audits/2026-10-24-earn-paid-docs]

Runs the whole owner flow against a TEMPORARY data dir (BCC_DATA_DIR is pointed at it before anything is imported;
the owner's real data is never opened): owner issues an invoice -> participant uploads a synthetic contract and uses
the free trial -> trial exhausted -> owner marks the invoice paid -> a paid /ask is answered -> refund revokes. The
/ask answer is a STUB string: no Ollama, no model, no GPU. Every socket connect and httpx send is refused and counted
during the run, so "nothing was sent" is measured, not assumed. Negative controls are run too.

The verdict is about the CAPABILITY only. No real client and no real payment took part: revenue is NOT_RUN and this
receipt never claims earned money. Exit 0 only when every step and every negative control passes.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "docs" / "audits" / "2026-10-24-earn-paid-docs"
STUB_ANSWER = "Срок оплаты — 10 рабочих дней с момента подписания акта [1]."
CONTRACT = ("Срок оплаты — 10 рабочих дней с момента подписания акта.\n\n"
            "Штраф за просрочку составляет 0.1% в день от суммы.\n\n"
            "Гарантия на выполненные работы — 12 месяцев.").encode()
GUEST = 900000202          # synthetic Telegram id, not a real participant


def _sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def run(out_dir: Path) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="jeff-manual-invoice-proof-"))
    os.environ["BCC_DATA_DIR"] = str(tmp)
    sys.path.insert(0, str(ROOT / "command-center"))

    import httpx

    from bcc.pit import manual_invoice as mi
    from bcc.pit import paid_docs as pd_mod
    from bcc.pit.manual_invoice import InvoiceRejected, NotOwner
    from bcc.pit.stars_ledger import DAY, DOCS30, TRIAL_REQUESTS
    from bcc.pit.vault import PersonaVault

    attempts: list[str] = []

    def refuse(*args, **kwargs):
        attempts.append(type(args[0]).__name__ if args else "?")
        raise OSError("network is forbidden in this proof")

    saved = (socket.socket.connect, httpx.Client.send, httpx.AsyncClient.send)
    socket.socket.connect, httpx.Client.send, httpx.AsyncClient.send = refuse, refuse, refuse

    steps: list[dict] = []
    negatives: list[dict] = []

    def step(name: str, ok: bool, **detail) -> None:
        steps.append({"step": name, "pass": bool(ok), **detail})

    def neg(name: str, ok: bool, **detail) -> None:
        negatives.append({"control": name, "pass": bool(ok), **detail})

    started = time.time()
    try:
        docs = pd_mod.PaidDocs(tmp, PersonaVault(tmp, os.urandom(32)))
        key = docs.vault.key_for_telegram(GUEST)
        ledger_path = Path(docs.ledger.path)
        step("ledger_in_temp_dir", ledger_path.is_relative_to(tmp), ledger=str(ledger_path.relative_to(tmp)))

        text = docs.manual_command("/invoice", f"{GUEST} docs30 9.90 EUR", owner=True)
        row = docs.invoices.listing()[0]
        ref = row["ref"]
        step("owner_issues_invoice", row["status"] == "issued" and "Участнику ничего не отправлено" in text,
             ref=ref, amount=row["amount"], currency=row["currency"], owner_reply=text)
        step("invoice_alone_grants_nothing", not docs.ledger.entitlement(key)["has_paid"])

        reply, stored, _ = docs.add_document(key, "договор.txt", CONTRACT, owner=False)
        step("participant_uploads_synthetic_document", stored, reply=reply)
        trial = []
        for _ in range(TRIAL_REQUESTS):
            prepared = docs.prepare(key, "ask", "какой срок оплаты?", owner=False)
            trial.append(getattr(prepared, "pool", "refused"))
            if isinstance(prepared, pd_mod.Prepared):
                docs.finish(prepared, STUB_ANSWER)
        step("free_trial_used", trial == ["trial"] * TRIAL_REQUESTS, pools=trial)
        refused = docs.prepare(key, "ask", "какой срок оплаты?", owner=False)
        step("trial_exhausted_refuses", isinstance(refused, pd_mod.Refusal), text=getattr(refused, "text", ""))

        paid_reply = docs.manual_command("/paid", ref, owner=True)
        ent = docs.ledger.entitlement(key)
        step("owner_marks_paid_grants_docs30", ent["paid_requests_left"] == DOCS30.requests
             and ent["doc_limit"] == DOCS30.doc_slots and abs(ent["valid_until"] - (time.time() + DOCS30.days * DAY)) < 120,
             owner_reply=paid_reply, entitlement=ent)

        prepared = docs.prepare(key, "ask", "какой срок оплаты?", owner=False)
        answer = docs.finish(prepared, STUB_ANSWER) if isinstance(prepared, pd_mod.Prepared) else ""
        step("paid_ask_answered_with_stub_model", getattr(prepared, "pool", "") == "paid" and "Источники:" in answer
             and docs.ledger.entitlement(key)["paid_requests_left"] == DOCS30.requests - 1,
             model="STUB (no model loaded)", answer=answer)

        again = docs.invoices.mark_paid(ref, owner=True)
        step("double_mark_paid_is_idempotent", again.status == "duplicate"
             and docs.ledger.entitlement(key)["paid_requests_left"] == DOCS30.requests - 1
             and len(docs.ledger.person_payments(key)) == 1, status=again.status)

        report = docs.payments_text()
        step("owner_report_shows_money_apart_from_stars", "EUR: оплачено 9.90" in report
             and docs.ledger.report()["stars_gross_received"] == 0, report=report)

        refund_reply = docs.manual_command("/refund_invoice", ref, owner=True)
        ent = docs.ledger.entitlement(key)
        step("owner_refund_revokes_entitlement", not ent["has_paid"] and ent["paid_requests_left"] == 0
             and "снято запросов: %d" % (DOCS30.requests - 1) in refund_reply, owner_reply=refund_reply)

        # -- negative controls ------------------------------------------------------------------
        ref2 = docs.invoices.issue(key, GUEST, "docs30", "250", "CZK", owner=True)["ref"]
        try:
            docs.invoices.mark_paid(ref2, owner=False)
            raised = False
        except NotOwner:
            raised = True
        guest_cmd = docs.manual_command("/paid", ref2, owner=False)
        neg("non_owner_cannot_mark_paid", raised and guest_cmd is None and not docs.ledger.entitlement(key)["has_paid"],
            api="NotOwner raised" if raised else "NOT raised", guest_command_result=guest_cmd)

        unknown = docs.invoices.mark_paid("JD-00000000-000000", owner=True)
        neg("unknown_ref", unknown.status == "unknown" and not docs.ledger.entitlement(key)["has_paid"],
            status=unknown.status)

        late = time.time() + (mi.INVOICE_TTL_DAYS + 1) * DAY
        expired = docs.invoices.mark_paid(ref2, owner=True, now=late)
        neg("expired_invoice", expired.status == "expired" and not docs.ledger.entitlement(key)["has_paid"],
            status=expired.status)

        ref3 = docs.invoices.issue(key, GUEST, "docs30", "900", "RUB", owner=True)["ref"]
        cancelled = docs.invoices.cancel(ref3, owner=True)
        after = docs.invoices.mark_paid(ref3, owner=True)
        neg("cancelled_then_paid", cancelled.status == "cancelled" and after.status == "cancelled"
            and not docs.ledger.entitlement(key)["has_paid"], status=after.status)

        refunded_again = docs.invoices.mark_paid(ref, owner=True)
        neg("refunded_invoice_never_regrants", refunded_again.status == "refunded"
            and not docs.ledger.entitlement(key)["has_paid"], status=refunded_again.status)

        bad = {}
        for amount in ("0", "-5", "abc", "nan", "1.001"):
            try:
                docs.invoices.issue(key, GUEST, "docs30", amount, "EUR", owner=True)
                bad[amount] = "ACCEPTED"
            except InvoiceRejected as exc:
                bad[amount] = exc.text
        neg("amount_le_zero_or_malformed_refused", "ACCEPTED" not in bad.values(), results=bad)

        cur = {}
        for currency in ("USD", "XTR", ""):
            try:
                docs.invoices.issue(key, GUEST, "docs30", "5", currency, owner=True)
                cur[currency or "<empty>"] = "ACCEPTED"
            except InvoiceRejected as exc:
                cur[currency or "<empty>"] = exc.text
        neg("bad_currency_refused", "ACCEPTED" not in cur.values(), results=cur)

        neg("no_outbound_send", attempts == [], network_attempts=len(attempts),
            how="socket.connect, httpx.Client.send and httpx.AsyncClient.send replaced by a refusing recorder")
        docs.ledger.close()
    finally:
        socket.socket.connect, httpx.Client.send, httpx.AsyncClient.send = saved
        shutil.rmtree(tmp, ignore_errors=True)

    verdict = "PASS" if all(s["pass"] for s in steps) and all(n["pass"] for n in negatives) else "FAIL"
    receipt = {
        "schema": "bossman.proof.jeff-manual-invoice/1",
        "date": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "git_head": _sha(),
        "worktree_note": "proved on the working tree; the module, ledger/paid_docs edits and tests are uncommitted",
        "scenario": "A: paid document assistant (Jeff), manual bank-transfer invoice while Telegram Stars are paused",
        "verdict": verdict,
        "verdict_scope": "capability only (in-process, synthetic participant, stub model answer)",
        "revenue": "NOT_RUN",
        "revenue_note": "no real client and no real payment took part; this receipt is not evidence of earned money",
        "data_dir": "temporary directory, deleted after the run (owner data never opened)",
        "model": "STUB answer string; no Ollama, no model, no GPU",
        "not_covered": ["runtime.py does not yet route /invoice /paid /cancel_invoice /refund_invoice /invoices to "
                        "PaidDocs.manual_command (owner-chat wiring is a separate change)",
                        "no live proof through the real pult / Telegram chat path"],
        "seconds": round(time.time() - started, 2),
        "steps": steps,
        "negative_controls": negatives,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    receipt = run(args.out)
    for row in receipt["steps"]:
        print(("PASS " if row["pass"] else "FAIL ") + row["step"])
    for row in receipt["negative_controls"]:
        print(("PASS " if row["pass"] else "FAIL ") + "neg:" + row["control"])
    print(f"verdict={receipt['verdict']} revenue={receipt['revenue']} -> {args.out / 'receipt.json'}")
    return 0 if receipt["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
