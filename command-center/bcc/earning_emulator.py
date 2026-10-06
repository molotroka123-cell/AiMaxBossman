"""Earning EMULATOR: reads a public remote-job listing (text), drafts the deliverable, and writes a SIMULATED invoice to an append-only ledger.

What it does NOT do, by construction (each is pinned by a test): it never applies, never messages anyone, never logs in, never pays or receives money,
never touches an account. `real_money` is always 0 and every record says SIMULATED. This module performs no network I/O at all — fetching a page
is the caller's separate, read-only step — and listings with scam signals (upfront fees, gift cards, ID documents, wallet seeds, ...) are refused.

The deliverable comes from an injectable worker. The only workers named here are free or local ($0 rule); a paid or unknown worker is refused.
The built-in `template` worker uses no model and says so in the deliverable.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

SIMULATED = True
FREE_WORKERS = ("template", "local", "nemotron-ultra-free", "openrouter-free", "openrouter-code-free")
NOT_DONE = ("не откликались на объявление", "не писали заказчику", "не входили в аккаунты", "не платили и не получали деньги")
MAX_DELIVERABLE = 4000

SCAM_SIGNS = {
    "upfront_fee": r"(pay|deposit|send)\b.{0,40}\b(fee|training|equipment|starter kit|registration)",
    "gift_cards": r"gift\s*cards?",
    "money_transfer": r"(western union|moneygram|wire transfer|cashier'?s? check|send (us )?(money|payment))",
    "identity_documents": r"(passport|id card|driver'?s? licen[cs]e|social security|\bssn\b)",
    "wallet_secrets": r"(seed phrase|private key|wallet password)",
    "recruitment_fee": r"recruitment fee",
}
KINDS = {
    "writing": r"\b(writ(er|ing)|copywrit\w*|content|article|blog|editor|proofread\w*)\b",
    "translation": r"\b(translat\w*|localiz\w*|interpreter)\b",
    "data_entry": r"\b(data entry|spreadsheet|transcri\w*|annotat\w*|labell?ing)\b",
    "testing": r"\b(qa|tester|testing|test cases|bug report\w*)\b",
    "research": r"\b(research\w*|fact[- ]check\w*|market analysis|survey)\b",
    "code": r"\b(developer|python|javascript|typescript|api|script|automation)\b",
    "design": r"\b(design\w*|figma|logo|illustrat\w*|ui/ux)\b",
}


@dataclass
class Listing:
    title: str
    remote: bool
    kind: str
    pay_amount: float
    pay_unit: str          # hour | fixed | unknown
    currency: str
    pay_text: str
    hours_hint: float | None
    text: str = field(repr=False, default="")


def parse_listing(text: str) -> Listing:
    t = " ".join(text.split())
    title = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")[:160]
    remote = bool(re.search(r"\b(remote|work from home|anywhere|distributed)\b", t, re.I))
    kind = next((k for k, rx in KINDS.items() if re.search(rx, t, re.I)), "other")
    m = re.search(r"([$€£])\s?(\d+(?:[.,]\d+)?)(?:\s?[-–]\s?(?:[$€£]\s?)?(\d+(?:[.,]\d+)?))?\s*(?:/|per\s*)?\s*(hour|hr|h\b|fixed|total|project)?", t, re.I)
    amount, unit, cur, pay_text = 0.0, "unknown", "", ""
    if m:
        lo = float(m.group(2).replace(",", "."))
        hi = float(m.group(3).replace(",", ".")) if m.group(3) else lo
        amount, cur, pay_text = round((lo + hi) / 2, 2), m.group(1), m.group(0).strip()
        unit = "hour" if (m.group(4) or "").lower() in ("hour", "hr", "h") else "fixed"
    h = re.search(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\b(?!\s*(?:/|per))", t, re.I)
    return Listing(title, remote, kind, amount, unit, cur, pay_text, float(h.group(1)) if h else None, t)


def eligibility(l: Listing) -> tuple[bool, list[str]]:
    reasons = [f"признак мошенничества: {k}" for k, rx in SCAM_SIGNS.items() if re.search(rx, l.text, re.I)]
    if not l.remote:
        reasons.append("не сказано, что работа удалённая")
    return (not reasons), reasons


def template_worker(prompt: str) -> str:
    return ("ШАБЛОН БЕЗ МОДЕЛИ (черновик плана, не готовая работа)\n" + "\n".join(f"- {line}" for line in prompt.splitlines() if line.strip())[:MAX_DELIVERABLE])


def simulated_invoice(l: Listing) -> dict:
    if l.pay_unit == "unknown" or l.pay_amount <= 0:
        return {"simulated": True, "amount": 0.0, "currency": l.currency or "", "basis": "оплата в объявлении не указана"}
    if l.pay_unit == "hour":
        hours = l.hours_hint or 2.0
        return {"simulated": True, "amount": round(l.pay_amount * hours, 2), "currency": l.currency,
                "basis": f"ставка {l.pay_amount:g}/час × {hours:g} ч ({'из объявления' if l.hours_hint else 'допущение эмулятора'})"}
    return {"simulated": True, "amount": l.pay_amount, "currency": l.currency, "basis": "фиксированная сумма из объявления"}


def run(text: str, source: str, worker: Callable[[str], str] = template_worker, worker_name: str = "template",
        ledger: Path | None = None, now: float | None = None) -> dict:
    if worker_name not in FREE_WORKERS:
        raise ValueError(f"исполнитель {worker_name!r} не входит в $0-список {list(FREE_WORKERS)}: платные и неизвестные запрещены")
    l = parse_listing(text)
    ok, reasons = eligibility(l)
    rec: dict = {"id": hashlib.sha256((source + text).encode("utf-8")).hexdigest()[:12], "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if now is None else now)),
                 "source": source, "title": l.title, "kind": l.kind, "remote": l.remote, "pay_text": l.pay_text,
                 "SIMULATED": True, "real_money": 0, "not_done": list(NOT_DONE), "worker": worker_name}
    if not ok:
        rec.update(verdict="REFUSED", reasons=reasons, deliverable_sha256=None, invoice=None)
    else:
        prompt = f"Задача из объявления ({l.kind}): {l.title}\n" + "\n".join(f"- {s.strip()}" for s in re.split(r"[.\n]", text) if len(s.strip()) > 20)[:1500]
        deliverable = (worker(prompt) or "")[:MAX_DELIVERABLE]
        rec.update(verdict="SIMULATED_DONE", reasons=[], deliverable_sha256=hashlib.sha256(deliverable.encode("utf-8")).hexdigest(),
                   deliverable_chars=len(deliverable), deliverable_preview=deliverable[:300], invoice=simulated_invoice(l))
    if ledger is not None:
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a", encoding="utf-8") as f:                      # append-only: records are never rewritten
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec
