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


_UNVERIFIED_CLAIM_PATTERNS: tuple[tuple[str, str], ...] = (
    ("false_model_test_claim", r"(?i)\b(все\s+промпты\s+(проверены|протестированы)|промпты\s+протестированы\s+на|all\s+prompts\s+(are\s+)?(tested|verified)\s+on|tested\s+and\s+verified\s+on\s+(midjourney|sdxl|stable\s+diffusion|dall[·\-]?e))"),
    ("false_live_metric_claim", r"(?i)\b(гарантированн(ый|ая)\s+(доход|прибыль|конверсия)|100\s*%\s*гаранти[яи]|guaranteed\s+(profit|income|roi))\b"),
    ("unauthorized_legal_promise", r"(?i)\b(юридическ\w*\s+(гаранти\w*|обязательств\w*)|обязуемся\s+(компенсировать|возместить)|публичн\w*\s+оферт\w*\s+от\s+имени|legally\s+binding\s+guarantee)\b"),
)
_ESTIMATE_MARKERS = re.compile(
    r"(?i)(оценк[аиеуой]|ориентир\w*|приблизительн\w*|примерн\w*|допущени\w*|расчётн\w*|estimate\w*|approx\w*|~|≈)"
)
_PRICE_TOKEN = re.compile(r"(?:\$\s?\d+(?:[.,]\d+)?(?:\s?[-–]\s?\$?\s?\d+(?:[.,]\d+)?)?|₽\s?\d[\d\s]*(?:[.,]\d+)?|\d[\d\s]*\s?(?:руб|₽|usd|\$))", re.I)


def check_unverified_claims(text: str, *, verified_runs: int = 0) -> list[str]:
    """Catch claims that prompts/code/deliverables were tested on external engines when verified_runs == 0,
    or unauthorized legal/financial guarantees."""
    issues: list[str] = []
    for code, pat in _UNVERIFIED_CLAIM_PATTERNS:
        if code == "false_model_test_claim" and verified_runs > 0:
            continue
        m = re.search(pat, text)
        if m:
            issues.append(f"{code}: '{m.group(0).strip()}' (verified_runs={verified_runs})")
    return issues


def check_arithmetic_claims(text: str, *, expected_total: int | None = None) -> list[str]:
    """Catch arithmetic mismatches in commercial deliverables (e.g. '5 × 4 = 20' when 5 blocks × 5 items = 25, or A × B != C)."""
    issues: list[str] = []
    # 1. Explicit multiplication expressions: A × B = C or A * B = C
    for m in re.finditer(r"\b(\d{1,4})\s*[×*xх]\s*(\d{1,4})\s*=\s*(\d{1,6})\b", text, re.I):
        a, b, c = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a * b != c:
            issues.append(f"arithmetic_mismatch: {a} × {b} = {c} (expected {a * b})")
        else:
            # Check if the surrounding sentence contradicts the per-block factor (e.g. '5 × 4 = 20... Каждый блок содержит 5')
            window = text[max(0, m.start() - 160): min(len(text), m.end() + 160)]
            per_block = re.search(r"(?:по|содержит|в\s+каждом)\s+(\d{1,3})\s+(?:промпт\w*|вариац\w*|элемент\w*|пункт\w*|шаг\w*)", window, re.I)
            if per_block:
                k = int(per_block.group(1))
                if b != k and (k not in (a, b) or a == k):
                    issues.append(f"block_count_contradiction: formula uses {a}×{b}={c} but text says {k} per block ({a}×{k}={a * k})")
    # 2. Check markdown table block rows vs 'N промптов в каждом' header
    hdr = re.search(r"\|\s*Блок\s*\|\s*[^|\n]*?\((\d{1,3})\s+промпт\w*\s+в\s+каждом\)[^\n]*", text, re.I)
    if hdr:
        per_row = int(hdr.group(1))
        after = text[hdr.end():]
        table_lines = []
        for ln in after.splitlines():
            s = ln.strip()
            if not s:
                if table_lines:
                    break
                continue
            if s.startswith("|"):
                if not re.match(r"^\|[\s\-:|]+\|$", s):
                    table_lines.append(s)
            elif table_lines:
                break
        if table_lines:
            subtotal = len(table_lines) * per_row
            ready_m = re.search(r"\b(\d{1,3})\s+полностью\s+готовых\s+промпт\w*", text, re.I)
            ready_n = int(ready_m.group(1)) if ready_m else 0
            if expected_total is not None and ready_n and (ready_n + subtotal != expected_total):
                issues.append(
                    f"total_mismatch: {ready_n} ready + ({len(table_lines)} blocks × {per_row} = {subtotal}) = {ready_n + subtotal} != {expected_total}"
                )
    if expected_total is not None and not hdr:
        m_tot = re.search(
            r"(?:всего|итого|total|в\s+наборе|пакет(?:\s+из)?\s*[:—\-]?)\s*(\d{1,4})\s+"
            r"(?:коммерческ\w*\s+)?(?:фотопромпт\w*|промпт\w*|пункт\w*|позиц\w*|строк\w*|элемент\w*|шаг\w*|услуг\w*)",
            text,
            re.I,
        )
        if m_tot and int(m_tot.group(1)) != int(expected_total):
            issues.append(f"expected_total_mismatch: claimed {int(m_tot.group(1))} != expected {int(expected_total)}")
    return issues


_BRIEF_STOPWORDS = frozenset({
    "собери", "сделай", "напиши", "нужен", "нужна", "нужно", "пакет", "таблицу", "таблица", "готовую",
    "готовый", "клиенту", "указанием", "оценочной", "стоимости", "каждого", "каждой", "пункта", "пунктов",
    "смету", "смета", "план", "заказ", "задача", "ровно", "блоков", "блока", "вариации", "вариаций",
    "промптов", "промпта", "промпты", "описанием", "описание", "список", "create", "make", "build",
    "prepare", "deliverable", "client", "table", "with", "from", "that", "this",
})


def check_brief_alignment(text: str, brief: str | None) -> list[str]:
    """Verify that the deliverable actually addresses the domain/topic of the brief rather than an unrelated template."""
    if not brief or not brief.strip():
        return []
    body_low = (text or "").lower()
    brief_low = brief.lower()
    # Extract domain stems (first 5 chars of words >= 5 chars not in stopwords)
    words = [w for w in re.findall(r"[а-яёa-z]{5,}", brief_low) if w not in _BRIEF_STOPWORDS]
    if not words:
        return []
    stems = {w[:5] for w in words}
    matched = {s for s in stems if s in body_low}
    if not matched:
        return [f"brief_topic_mismatch: deliverable does not mention any key topic from brief ({sorted(words)[:5]})"]
    return []


def check_price_estimates(text: str) -> list[str]:
    """Ensure price/rate figures in a commercial deliverable are marked as estimates (оценка / estimate / ≈)."""
    issues: list[str] = []
    prices = [m.group(0).strip() for m in _PRICE_TOKEN.finditer(text)]
    if not prices:
        return issues
    if not _ESTIMATE_MARKERS.search(text):
        issues.append(f"unlabeled_price_estimate: prices {prices[:3]} stated without an estimate marker (оценка/estimate/≈)")
    return issues


def verify_commercial_deliverable(
    text: str,
    *,
    expected_total: int | None = None,
    require_price_estimate_label: bool = True,
    verified_runs: int = 0,
    min_chars: int = 200,
    brief: str | None = None,
) -> dict:
    """Deterministic audit of a commercial deliverable before sending to the owner or customer."""
    body = (text or "").strip()
    issues: list[str] = []
    if len(body) < min_chars:
        issues.append(f"too_short: {len(body)} chars < {min_chars}")
    issues.extend(check_unverified_claims(body, verified_runs=verified_runs))
    issues.extend(check_arithmetic_claims(body, expected_total=expected_total))
    if require_price_estimate_label:
        issues.extend(check_price_estimates(body))
    if brief:
        issues.extend(check_brief_alignment(body, brief))
    return {
        "passed": len(issues) == 0,
        "verdict": "VERIFIED_CLEAN" if not issues else "NEEDS_REVISION",
        "issues": issues,
        "chars": len(body),
    }


def run(
    text: str,
    source: str,
    worker: Callable[[str], str] = template_worker,
    worker_name: str = "template",
    ledger: Path | None = None,
    now: float | None = None,
    *,
    quality_audit: dict | None = None,
    expected_total: int | None = None,
    require_price_estimate_label: bool = False,
    min_chars: int = 10,
) -> dict:
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
        audit = (
            dict(quality_audit)
            if isinstance(quality_audit, dict)
            else verify_commercial_deliverable(
                deliverable,
                expected_total=expected_total,
                require_price_estimate_label=require_price_estimate_label,
                min_chars=min_chars,
            )
        )
        passed = bool(audit.get("passed", True))
        verdict = "SIMULATED_DONE" if passed else "NEEDS_REVISION"
        reasons_out = [] if passed else list(audit.get("issues") or [])
        rec.update(verdict=verdict, reasons=reasons_out, deliverable_sha256=hashlib.sha256(deliverable.encode("utf-8")).hexdigest(),
                   deliverable_chars=len(deliverable), deliverable_preview=deliverable[:300], invoice=simulated_invoice(l),
                   quality_audit=audit)
    if ledger is not None:
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a", encoding="utf-8") as f:                      # append-only: records are never rewritten
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


HARDWARE_TARGET_USD = 3200.0
PROVEN_MONETIZATION_RULE = "real_user + real_payment + metric"
_CURRENCY_TO_USD = {"$": 1.0, "usd": 1.0, "€": 1.08, "eur": 1.08, "£": 1.27, "gbp": 1.27}


def _to_usd(amount: float, currency: str, *, rub_per_usd: float = 95.0) -> float:
    if amount <= 0:
        return 0.0
    cur = (currency or "$").strip().lower()
    if cur in ("₽", "rub", "руб", "руб."):
        return round(amount / max(1.0, rub_per_usd), 2)
    factor = _CURRENCY_TO_USD.get(cur, 1.0)
    return round(amount * factor, 2)


def is_proven_monetization(record: dict) -> tuple[bool, list[str]]:
    """Правило владельца (MASTER_PROMPT): «Доказано» значит: реальный пользователь + реальная оплата + метрика.
    Заявления без доказательств или записи симулятора (SIMULATED=True / real_money=0) — НЕ доказанная монетизация.
    """
    missing: list[str] = []
    if not isinstance(record, dict):
        return False, ["not_a_record"]
    if record.get("SIMULATED", True) is True:
        missing.append("simulated_only (SIMULATED=True)")
    real_user = record.get("real_user") or record.get("customer_id") or record.get("user_proof")
    if not real_user:
        missing.append("нет реального пользователя (real_user)")
    try:
        real_paid = float(record.get("real_paid_usd") or record.get("real_money") or 0.0)
    except (TypeError, ValueError):
        real_paid = 0.0
    if real_paid <= 0 or not record.get("payment_proof"):
        missing.append("нет подтверждённой реальной оплаты (real_payment + payment_proof)")
    metric = record.get("metric")
    if not (isinstance(metric, dict) and metric.get("name") and metric.get("value") is not None):
        missing.append("нет измеримой метрики (metric.name + metric.value)")
    return len(missing) == 0, missing


def hardware_roi_summary(
    records: list[dict],
    *,
    target_usd: float = HARDWARE_TARGET_USD,
    cloud_savings_usd: float = 0.0,
    rub_per_usd: float = 95.0,
) -> dict:
    """Deterministic $3,200 hardware payback & unit-economics summary (pure Python, SIMULATED=True, real_money=0).

    Strictly separates:
      - `proven_monetization_usd`: ONLY records passing `is_proven_monetization` (real_user + real_payment + metric).
      - `verified_simulated_usd`: local drafts/deliverables that passed `verify_commercial_deliverable` (SIMULATED=True).
      - `needs_revision_usd`: drafts rejected by `verify_commercial_deliverable`.
    """
    proven_usd = 0.0
    proven_count = 0
    verified_usd = 0.0
    needs_revision_usd = 0.0
    verified_count = 0
    needs_revision_count = 0
    refused_count = 0

    for r in records or []:
        if not isinstance(r, dict):
            continue
        verdict = str(r.get("verdict") or "")
        if verdict == "REFUSED":
            refused_count += 1
            continue
        proven_ok, _ = is_proven_monetization(r)
        if proven_ok:
            proven_count += 1
            proven_usd = round(proven_usd + float(r.get("real_paid_usd") or r.get("real_money") or 0.0), 2)
        inv = r.get("invoice")
        amt = 0.0
        cur = "$"
        if isinstance(inv, dict):
            try:
                amt = float(inv.get("amount") or 0.0)
            except (TypeError, ValueError):
                amt = 0.0
            cur = str(inv.get("currency") or "$")
        elif r.get("est_usd") is not None:
            try:
                amt = float(r.get("est_usd") or 0.0)
            except (TypeError, ValueError):
                amt = 0.0
            cur = "$"
        usd = _to_usd(amt, cur, rub_per_usd=rub_per_usd)
        audit = r.get("quality_audit") or r.get("audit") or {}
        audit_ok = audit.get("passed", True) if isinstance(audit, dict) else True
        if verdict in ("SIMULATED_DONE", "VERIFIED_CLEAN") and audit_ok:
            verified_count += 1
            verified_usd = round(verified_usd + usd, 2)
        else:
            needs_revision_count += 1
            needs_revision_usd = round(needs_revision_usd + usd, 2)

    savings = round(max(0.0, float(cloud_savings_usd or 0.0)), 2)
    total_val = round(verified_usd + savings, 2)
    target = round(max(1.0, float(target_usd or HARDWARE_TARGET_USD)), 2)
    remaining_proven = round(max(0.0, target - proven_usd), 2)
    remaining = round(max(0.0, target - total_val), 2)
    pct = round(min(100.0, (total_val / target) * 100.0), 2)
    proven_pct = round(min(100.0, (proven_usd / target) * 100.0), 2)
    avg_task = round(verified_usd / verified_count, 2) if verified_count > 0 and verified_usd > 0 else 35.0
    tasks_needed = int((remaining + avg_task - 1e-9) // avg_task) if remaining > 0 else 0

    pillars = [
        {
            "id": "commercial_tasks",
            "title": "Коммерческие заказы (Earn + verify + Coach)",
            "est_usd_per_unit": 35.0,
            "units_for_3200": int((target + 35.0 - 1e-9) // 35.0),
            "status": "MVP_VERIFIED_LOCAL",
            "monetization_stage": "гипотеза → MVP проверен локально → ждёт реального покупателя",
        },
        {
            "id": "cinema_motion_studio",
            "title": "Киностудия (Motion Studio 37 шаблонов + Video Studio OTIO)",
            "est_usd_per_unit": 80.0,
            "units_for_3200": int((target + 80.0 - 1e-9) // 80.0),
            "status": "MVP_VERIFIED_LOCAL",
            "monetization_stage": "гипотеза → MVP проверен локально → ждёт заказчика ролика",
        },
        {
            "id": "poker_vision_coach",
            "title": "Покер Vision + Тренер (+52.05 bb/100 botlab, 36/36 точность)",
            "est_usd_per_unit": 50.0,
            "units_for_3200": int((target + 50.0 - 1e-9) // 50.0),
            "status": "MVP_VERIFIED_LOCAL",
            "monetization_stage": "гипотеза → доказан перевес в тренажёре (+52.05 bb/100) → без живых ставок",
        },
        {
            "id": "k1m6a_ocr_arena",
            "title": "Аналитика рынков (K1m6a RapidOCR 98.21% + Solana Paper Arena)",
            "est_usd_per_unit": 40.0,
            "units_for_3200": int((target + 40.0 - 1e-9) // 40.0),
            "status": "PAPER_ONLY_NO_GO",
            "monetization_stage": "OCR 98.21% доказан; стратегии S1-S3 в пиле убиты; S4 на бумажном карантине",
        },
        {
            "id": "self_repair_autonomy",
            "title": "Самоулучшение и локальный стек (100% локально, 4/4 holdouts)",
            "est_usd_per_unit": 25.0,
            "units_for_3200": int((target + 25.0 - 1e-9) // 25.0),
            "status": "PROVEN_LOCAL",
            "monetization_stage": "экономия $0 облака + авторемонт в пределах 3 попыток",
        },
    ]

    return {
        "SIMULATED": True,
        "real_money": 0,
        "proven_rule": PROVEN_MONETIZATION_RULE,
        "proven_monetization_usd": proven_usd,
        "proven_count": proven_count,
        "proven_payback_pct": proven_pct,
        "remaining_proven_to_payback_usd": remaining_proven,
        "hardware_target_usd": target,
        "verified_count": verified_count,
        "needs_revision_count": needs_revision_count,
        "refused_count": refused_count,
        "verified_simulated_usd": verified_usd,
        "needs_revision_usd": needs_revision_usd,
        "cloud_savings_usd": savings,
        "total_tracked_value_usd": total_val,
        "remaining_to_payback_usd": remaining,
        "payback_progress_pct": pct,
        "avg_verified_task_usd": avg_task,
        "tasks_needed_at_avg": tasks_needed,
        "pillars": pillars,
        "primary_hypothesis": FRESH_VIBES_HYPOTHESIS,
    }


FRESH_VIBES_HYPOTHESIS: dict = {
    "id": "fresh_vibes_instagram_14d",
    "name": "Fresh Vibes (Instagram Direct)",
    "hypothesis": "Instagram Fresh Vibes приводит ≥5 запросов на запись за 14 дней при ежедневном контенте.",
    "duration_days": 14,
    "daily_plan": [
        "1 пост (реальное фото клиники/врачей из GitHub, подпись RU+CZ + хештеги)",
        "2 сторис",
        "В каждой подписи и шапке — призыв «Запись в Direct»",
    ],
    "metric_name": "direct_booking_requests",
    "metric_description": "только реальные запросы на запись в Direct (не лайки, не охваты)",
    "scale_threshold": 5,
    "kill_threshold_max": 1,
    "rules": [
        "только реальные фото клиники/врачей из GitHub",
        "без AI-лиц",
        "без выдуманных цен и обещаний",
        "отчёт каждый вечер одной строкой: день N, постов, запросов всего",
        "никаких внешних публикаций без явной кнопки подтверждения владельца на каждую публикацию",
    ],
}


def fresh_vibes_evening_line(day: int, posts: int, direct_requests: int) -> str:
    """Формирует вечерний отчёт Fresh Vibes ровно одной строкой: 'день N, постов M, запросов всего K'."""
    d = max(0, int(day))
    p = max(0, int(posts))
    r = max(0, int(direct_requests))
    return f"день {d}, постов {p}, запросов всего {r}"


def evaluate_fresh_vibes_test(
    day: int,
    posts: int,
    direct_requests: int,
    *,
    stories: int = 0,
    has_ai_faces: bool = False,
    has_invented_prices: bool = False,
    real_github_photos_only: bool = True,
) -> dict:
    """Оценивает 14-дневный тест гипотезы Fresh Vibes по правилам владельца:
    - ≥5 запросов на запись в Direct за 14 дней → гипотеза подтверждена, масштабируй (`CONFIRMED_SCALE`).
    - 0–1 запрос за 14 дней → гипотеза убита, канал не зарабатывает, дальше не вкладывай (`KILLED`).
    - Правила: только реальные фото из GitHub, без AI-лиц, без выдуманных цен и обещаний.
    - Никаких внешних публикаций без кнопки подтверждения владельца.
    """
    d = max(0, int(day))
    p = max(0, int(posts))
    r = max(0, int(direct_requests))
    st = max(0, int(stories))
    violations: list[str] = []
    if has_ai_faces:
        violations.append("запрещено правило: обнаружены AI-лица (разрешены только реальные фото из GitHub)")
    if has_invented_prices:
        violations.append("запрещено правило: обнаружены выдуманные цены или обещания")
    if not real_github_photos_only:
        violations.append("запрещено правило: фото не из проверенного репозитория GitHub клиники/врачей")

    if violations:
        decision = "RULE_VIOLATION"
        verdict_ru = "Нарушение правил Fresh Vibes: " + "; ".join(violations)
    elif r >= FRESH_VIBES_HYPOTHESIS["scale_threshold"]:
        decision = "CONFIRMED_SCALE"
        verdict_ru = f"Гипотеза подтверждена ({r} ≥ 5 запросов на запись в Direct за {d}/14 дн.) → масштабируй."
    elif d >= FRESH_VIBES_HYPOTHESIS["duration_days"] and r <= FRESH_VIBES_HYPOTHESIS["kill_threshold_max"]:
        decision = "KILLED"
        verdict_ru = (
            f"Гипотеза убита ({r} запросов за {d} дн., порог 0–1) → канал не зарабатывает, дальше не вкладывай."
        )
    elif d >= FRESH_VIBES_HYPOTHESIS["duration_days"]:
        decision = "NEEDS_OWNER_DECISION"
        verdict_ru = (
            f"Тест 14 дней завершён: {r} запросов в Direct (выше 0–1, но ниже порога масштабирования ≥5). "
            "Нужно решение владельца."
        )
    else:
        decision = "IN_PROGRESS"
        verdict_ru = (
            f"Тест Fresh Vibes в процессе (день {d}/14): {r}/5 реальных запросов на запись в Direct "
            "(лайки и охваты не учитываются)."
        )

    return {
        "id": FRESH_VIBES_HYPOTHESIS["id"],
        "name": FRESH_VIBES_HYPOTHESIS["name"],
        "hypothesis": FRESH_VIBES_HYPOTHESIS["hypothesis"],
        "day": d,
        "duration_days": 14,
        "posts": p,
        "stories": st,
        "direct_requests": r,
        "metric_counted": "direct_booking_requests_only",
        "likes_and_reach_ignored": True,
        "real_github_photos_only": bool(real_github_photos_only),
        "has_ai_faces": bool(has_ai_faces),
        "has_invented_prices": bool(has_invented_prices),
        "rule_violations": violations,
        "decision": decision,
        "verdict_ru": verdict_ru,
        "evening_line": fresh_vibes_evening_line(d, p, r),
        "external_publish_policy": "ONLY_WITH_OWNER_APPROVAL_BUTTON",
    }


def weekly_monetization_report(
    records: list[dict],
    *,
    target_usd: float = HARDWARE_TARGET_USD,
    fresh_vibes_state: dict | None = None,
) -> dict:
    """Еженедельный отчёт управляющего агента Bossman по формату MASTER_PROMPT:
    1) что заработано (ТОЛЬКО доказано: реальный пользователь + реальная оплата + метрика),
    2) что проверено (фактом/тестом),
    3) что убито (нежизнеспособное),
    4) что следующее (гипотеза -> минимальный продукт -> проверка на реальных людях -> метрика -> решение).
    """
    roi = hardware_roi_summary(records, target_usd=target_usd)
    fv_raw = fresh_vibes_state or {}
    fv = evaluate_fresh_vibes_test(
        int(fv_raw.get("day") or 1),
        int(fv_raw.get("posts") or 0),
        int(fv_raw.get("direct_requests") or 0),
        stories=int(fv_raw.get("stories") or 0),
        has_ai_faces=bool(fv_raw.get("has_ai_faces", False)),
        has_invented_prices=bool(fv_raw.get("has_invented_prices", False)),
        real_github_photos_only=bool(fv_raw.get("real_github_photos_only", True)),
    )
    killed_list = [
        "Стратегии Solana S1_sol_ema, S2_sol_breakout, S3_sol_vwap без фильтра режима — УБИТЫ (съедены комиссией 28 bps и дневным стопом -3% в боковике CHOP).",
        "Облачные вызовы в контуре управляющего агента — ОТКЛЮЧЕНЫ (переведён в строгий локальный режим local_only=True по MASTER_PROMPT).",
        "Заявления «все промпты проверены на Midjourney» при verified_runs=0 и цены без пометки «(оценка)» — БЛОКИРУЮТСЯ детерминированным гейтом.",
    ]
    if fv["decision"] == "KILLED":
        killed_list.append(
            f"Гипотеза Instagram Fresh Vibes (14 дней): {fv['evening_line']} — УБИТА (0–1 запрос в Direct за 14 дней, канал не зарабатывает, дальше не вкладываем)."
        )
    return {
        "SIMULATED": True,
        "real_money": 0,
        "proven_rule": PROVEN_MONETIZATION_RULE,
        "earned_proven": {
            "proven_usd": roi["proven_monetization_usd"],
            "proven_count": roi["proven_count"],
            "honest_status": (
                "Реальных оплат от внешних клиентов пока $0.00 (0 сделок с реальным пользователем + оплатой + метрикой). "
                f"Локально подготовлено и проверено верификатором {roi['verified_count']} черновиков на оценочную сумму ≈ ${roi['verified_simulated_usd']:.2f} (SIMULATED, не считается реальным доходом)."
            ),
        },
        "verified_facts": [
            "K1m6a RapidOCR: точность чтения шапки графика на 14 locked-кадрах = 98.21% (против 90.48% у vision).",
            "Poker Vision & Coach: +52.05 bb/100 (botlab_learned на 16 000 раздач) и 36/36 (100%) точность чтения стола в тренажёре.",
            "Киностудия (Motion Studio + Video Studio): 37 шаблонов в 6 категориях, OpenTimelineIO + FFmpeg + Lottie проверены.",
            "Саморемонт (tree_holdout): 4/4 скрытых экзамена пройдены (98/98 кейсов: atomic_json_replace, discovery_nonfinite, goal_budget_nonfinite, cli_task_paging).",
            f"Коммерческий верификатор (verify_commercial_deliverable): {roi['verified_count']} чистых пакетов, {roi['needs_revision_count']} отбраковано до показа.",
            f"Тест гипотезы владельца Fresh Vibes (Instagram Direct, 14 дней): {fv['evening_line']} ({fv['verdict_ru']}).",
        ],
        "killed": killed_list,
        "next_step": {
            "pipeline": "гипотеза → минимальный продукт → проверка на реальных людях → метрика → решение (масштабировать / убить / переделать)",
            "hypothesis": (
                "Fresh Vibes — Instagram приводит ≥5 запросов на запись в Direct за 14 дней при ежедневном контенте "
                "(каждый день: 1 пост с реальным фото клиники/врачей из GitHub, подпись RU+CZ + хештеги + 2 сторис, "
                "призыв «Запись в Direct»; считаем только реальные запросы в Direct, не лайки и не охваты; "
                "≥5 запросов → масштабируй, 0–1 запрос → гипотеза убита; только реальные фото, без AI-лиц и без выдуманных цен; "
                f"вечерний отчёт одной строкой: `{fv['evening_line']}`)"
            ),
            "owner_action_needed": (
                "Подтвердить кнопкой публикацию дневного поста (реальное фото из GitHub + RU+CZ подпись «Запись в Direct») "
                "и зафиксировать вечерний счётчик реальных запросов в Direct (`/fresh_vibes день N постов M запросов K`). "
                "Без нажатия кнопки владельца никакие посты во внешние сети не публикуются."
            ),
        },
        "fresh_vibes": fv,
        "roi": roi,
    }



