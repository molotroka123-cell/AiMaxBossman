"""Long-horizon proof run for Bossman (owner 29.10, gaps closed 01.11): a multi-step task on synthetic business data,
executed by a LOCAL agent through the real task API, interrupted in the middle, resumed, STOPped, and verified
independently - repeated for N rounds on FRESH datasets.

    python tools/long_horizon_run.py start  [--agent 52] [--rows 60000] [--rounds 1] [--max-hours H]
    python tools/long_horizon_run.py start  --soak-hours 24 ...            # separate 24 h stability mode
    python tools/long_horizon_run.py start  --no-memory-control ...        # negative control: no memory record
    python tools/long_horizon_run.py resume --run-id lh-...                # continue after the DRIVER was interrupted
    python tools/long_horizon_run.py selftest                              # deterministic parts only

What is proven, and by what (no mocks, no pre-recorded answers):
  * identity     - /health/live build_sha == git HEAD of this checkout, source_identity PASS; every round on one SHA
  * progress     - state.json checkpoint after every verified step: round, SHA, task id, result hashes, next step
  * rounds       - every round gets a new seed, new planted entities, new truth.json and a new run folder
  * interruption - in EVERY round the :8801 backend is killed while step 3 has already executed tools; after restart
                   the SAME task must finish, and no completed tool call may have been executed twice (all rounds)
  * STOP         - in EVERY round POST /api/tasks/<id>/stop during step 4; zero tool calls may start after it
  * report       - REPORT.md must state ALL verified findings with the right numbers (worst-margin product + margin,
                   top customer + share %, declining region + drop %), parsed tolerantly and compared with truth.json
  * memory       - the driver stores the verified findings with a per-round auditor code word that exists ONLY in
                   memory (never in a file the agent can read, never in the prompt). Step 5 must show (a) the recall
                   surfaced THAT record (fact id / note ref / code word) and (b) REPORT.md reproduces the code word.
                   `--no-memory-control` skips the memory record: the code word must then be ABSENT from the report.
  * verification - the agent's numbers are compared with truth.json, which lives OUTSIDE every agent root (with the
                   driver state, the driver log and the code word), and orders.csv is re-aggregated by independent code
  * duration     - `--rounds N` and `--max-hours H` (hard wall-clock cap -> PARTIAL with the rounds done);
                   24 h stability is claimed ONLY by `--soak-hours 24` after 24 h of passing rounds, never by a shorter run
The driver never widens rights: it approves only terminal calls whose cwd is the round folder and whose text passes
`command_in_scope`; leases are issued through the existing approval_scope (bounded uses + TTL, revocable).
Receipts: docs/audits/<date>-long-horizon-<run-id>/ (README.md table, state.json, driver.log, r<k>/truth.json ...).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

AGENT_ROOT = Path(r"C:\Users\asd\Bossman\wt-agent-proof-20261025")      # already an allowed terminal root
RUNS = AGENT_ROOT / "lh-runs"                                           # round folders only (agent-visible)


def evidence_path(ws: Path, rel: str) -> Path:
    """A step's evidence is written Windows-style (`out\\x.json`); on Linux CI that is ONE file name, not a path."""
    return ws.joinpath(*str(rel).replace("\\", "/").split("/"))
TRUTH_ROOT = Path(r"D:\Bossman\long-horizon")       # outside every agent root: truth, code word, state, driver log
DONE = ("executed", "ok", "completed", "success", "done")   # tool_calls.status of a call that ran
DRIVER_VERSION = 2

LEGACY_SEED = 29102026                     # run g dataset: P-07 / C-0042 / Север
REGIONS = ["Север", "Юг", "Запад", "Восток", "Центр"]
PRODUCTS = [f"P-{i:02d}" for i in range(1, 13)]
STEP_NAMES = ("profile", "metrics", "findings", "verify", "report")
FORBIDDEN = re.compile(r"(?i)(curl|wget|invoke-webrequest|http://|https://|ssh|scp|pip\s+install|npm\s+install|rm\s+-rf|"
                       r"\bdel\s+/|remove-item\s+-recurse|format\s+[a-z]:|reg\s+(add|delete)|-enc\b|\.env|token|secret|"
                       r"password|private.?key|wallet|\.\.[\\/])")
CODE_WORDS_A = ["AMBER", "COBALT", "CEDAR", "DELTA", "EMBER", "FROST", "GRANITE", "HARBOR", "IVORY", "JUNIPER",
                "KESTREL", "LINDEN", "MAPLE", "NOVA", "ONYX", "PEBBLE", "QUARTZ", "RAVEN", "SAFFRON", "TUNDRA"]
CODE_WORDS_B = ["FALCON", "OTTER", "BADGER", "HERON", "LYNX", "MARTEN", "OSPREY", "PANTHER", "QUAIL", "SALMON",
                "TAPIR", "VIPER", "WALRUS", "YAK", "ZEBRA", "BISON", "CONDOR", "DINGO", "EGRET", "FERRET"]


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def data_dir() -> str:
    return os.environ.get("BCC_DATA_DIR") or os.environ["LOCALAPPDATA"] + "/Bossman/CommandCenter"


def db_ro() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{data_dir()}/bcc.db?mode=ro", uri=True)


def _api():
    import prove_chat_attachments as p  # noqa: WPS433  (token + api helper; lazy so pure functions import without PIL)
    return p


# ------------------------------------------------------------------ dataset with planted ground truth
def round_seed(run_id: str, round_no: int) -> int:
    """A fresh, reproducible seed per round (never the legacy run-g seed)."""
    seed = zlib.crc32(f"{run_id}:r{round_no}".encode("utf-8")) & 0x7FFFFFFF
    return seed + 1 if seed == LEGACY_SEED else seed


def planted_for_seed(seed: int) -> dict:
    """Which entities carry the planted anomalies. The legacy seed keeps run g's P-07 / C-0042 / Север; every other
    seed draws new ones, so a report copied from an earlier round's memory is wrong."""
    if seed == LEGACY_SEED:
        return {"negative_margin_product": "P-07", "declining_region": "Север", "top_customer": "C-0042"}
    r = random.Random(seed * 7919 + 13)
    return {"negative_margin_product": r.choice(PRODUCTS), "declining_region": r.choice(REGIONS),
            "top_customer": f"C-{r.randrange(900, 1000):04d}"}        # regular customers are C-0001..C-0899


def make_dataset(ws: Path, truth_dir: Path, rows: int, seed: int = LEGACY_SEED) -> dict:
    planted = planted_for_seed(seed)
    bad_p, bad_r, top_c = planted["negative_margin_product"], planted["declining_region"], planted["top_customer"]
    others = [r for r in REGIONS if r != bad_r]
    rnd = random.Random(seed)
    ws.mkdir(parents=True, exist_ok=True)
    truth_dir.mkdir(parents=True, exist_ok=True)
    price = {pr: round(rnd.uniform(20, 200), 2) for pr in PRODUCTS}
    cost = {pr: round(price[pr] * rnd.uniform(0.45, 0.75), 2) for pr in PRODUCTS}
    cost[bad_p] = round(price[bad_p] * 1.18, 2)                      # planted: negative margin
    out, dups = [], []
    for n in range(rows):
        month = rnd.randrange(24)
        region = rnd.choice(REGIONS)
        if region == bad_r and month >= 16 and rnd.random() < 0.35:   # planted: fewer orders from month 16
            region = rnd.choice(others)
        product = rnd.choice(PRODUCTS)
        cust = top_c if rnd.random() < 0.16 else f"C-{rnd.randrange(1, 900):04d}"   # planted: concentration
        qty = rnd.randrange(1, 9)
        date = f"{2025 + month // 12}-{month % 12 + 1:02d}-{rnd.randrange(1, 28):02d}"
        row = [f"O-{n:06d}", date, region, product, qty, price[product], cost[product], cust]
        out.append(row)
        if rnd.random() < 0.02:                                     # planted: ~2 % duplicate order ids
            dups.append(list(row))
    out.extend(dups)
    rnd.shuffle(out)
    with (ws / "orders.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["order_id", "date", "region", "product", "qty", "unit_price", "unit_cost", "customer_id"])
        w.writerows(out)
    truth = aggregate(ws / "orders.csv")
    truth["planted"] = {**planted, "seed": seed}
    (truth_dir / "truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")
    return truth


def aggregate(csv_path: Path) -> dict:
    """Independent re-aggregation (deduplicated by order_id, first occurrence)."""
    seen, rows, dup = set(), [], 0
    with csv_path.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r["order_id"] in seen:
                dup += 1
                continue
            seen.add(r["order_id"])
            rows.append(r)
    total = sum(int(r["qty"]) * float(r["unit_price"]) for r in rows)
    by_prod, by_cust, by_reg_m = {}, {}, {}
    for r in rows:
        rev = int(r["qty"]) * float(r["unit_price"])
        by_prod[r["product"]] = by_prod.get(r["product"], 0.0) + int(r["qty"]) * (float(r["unit_price"]) - float(r["unit_cost"]))
        by_cust[r["customer_id"]] = by_cust.get(r["customer_id"], 0.0) + rev
        key = (r["region"], r["date"][:7])
        by_reg_m[key] = by_reg_m.get(key, 0.0) + rev
    drops = {}
    for region in sorted({reg for reg, _ in by_reg_m}):
        series = [v for (reg, m), v in sorted(by_reg_m.items(), key=lambda kv: kv[0][1]) if reg == region]
        early = sum(series[:12]) / max(1, len(series[:12]))
        late = sum(series[-6:]) / max(1, len(series[-6:]))
        drops[region] = round((1 - late / early) * 100, 1) if early else 0.0
    worst = min(by_prod.items(), key=lambda kv: kv[1])
    top_c = max(by_cust.items(), key=lambda kv: kv[1])
    declining = max(drops.items(), key=lambda kv: kv[1])
    return {"unique_orders": len(rows), "duplicate_rows": dup, "total_revenue": round(total, 2),
            "worst_margin_product": worst[0], "worst_margin_value": round(worst[1], 2),
            "north_drop_pct": drops.get("Север", 0.0),
            "declining_region": declining[0], "region_drop_pct": declining[1],
            "top_customer": top_c[0], "top_customer_share_pct": round(top_c[1] / total * 100, 1)}


# ------------------------------------------------------------------ tolerant report parsing (pure)
_DASHES = "-\u2212\u2013\u2012\u2014\u2010\u2011"
_NUM_RE = re.compile(
    r"(?<![\d.,])"
    r"(?P<sign>[" + re.escape(_DASHES) + r"])?"
    r"(?P<int>\d{1,3}(?:[ \u00a0\u202f\u2009',]\d{3})+(?!\d)|\d+)"
    r"(?:[.,](?P<frac>\d+))?"
    r"(?:\s?(?P<mult>тыс|млн|k|K|M)(?!\w))?")
_ID_RE = re.compile(r"\b[A-Z]{1,3}[" + re.escape(_DASHES) + r"]?\d+\b")
_DATE_RE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")
_LOSS_RE = re.compile(r"(?i)(убыт|отриц|минус|loss|negative|дефицит|deficit)")
_MULT = {"тыс": 1e3, "k": 1e3, "K": 1e3, "млн": 1e6, "M": 1e6}


def parse_numbers(text: str) -> list[float]:
    """Every number a human could have meant: «-207 530,4», «−207,530.4», «15,9 %», «−207,5 тыс.». IDs such as
    P-07/C-0042 and ISO dates are removed first, so they never count as numbers. An ambiguous «1,234» yields both
    1234 and 1.234."""
    text = _DATE_RE.sub(" ", _ID_RE.sub(" ", text or ""))
    out: list[float] = []
    for m in _NUM_RE.finditer(text):
        raw_int, frac, sign = m.group("int"), m.group("frac"), -1.0 if m.group("sign") else 1.0
        mult = _MULT.get(m.group("mult") or "", 1.0)
        digits = re.sub(r"[ \u00a0\u202f\u2009',]", "", raw_int)
        value = float(f"{digits}.{frac}" if frac else digits)
        out.append(sign * value * mult)
        seps = re.findall(r"[ \u00a0\u202f\u2009',]", raw_int)
        if not frac and seps == [","]:                               # «32,100»: thousands or a decimal comma
            out.append(sign * float(raw_int.replace(",", ".")) * mult)
    return out


def _entity_re(entity: str) -> re.Pattern:
    body = "".join(f"[{re.escape(_DASHES)}]?" if ch == "-" else re.escape(ch) for ch in entity)
    return re.compile(body, re.I)


def _close(found: float, expected: float, kind: str) -> bool:
    tol = 0.11 if kind == "pct" else max(1.0, abs(expected) * 0.0005)
    return abs(found - expected) <= tol


def report_findings(truth: dict) -> list[dict]:
    """The verified findings a complete REPORT.md must state, with the number that proves each one."""
    return [
        {"name": "worst_margin", "entity": truth["worst_margin_product"], "expected": truth["worst_margin_value"],
         "kind": "money", "label": "худший продукт по марже"},
        {"name": "top_customer", "entity": truth["top_customer"], "expected": truth["top_customer_share_pct"],
         "kind": "pct", "label": "топ-клиент, доля выручки %"},
        {"name": "region_drop", "entity": truth["declining_region"], "expected": truth["region_drop_pct"],
         "kind": "pct", "label": "падение выручки региона %"},
    ]


def _number_ok(item: dict, numbers: list[float], line: str) -> bool:
    exp = float(item["expected"])
    if item["kind"] == "pct":                                        # «падение на 32,1 %» == «−32.1 %»; «— 15,9 %»
        return any(_close(abs(x), abs(exp), "pct") for x in numbers)
    if item["name"] == "worst_margin" and exp < 0 and _LOSS_RE.search(line):
        return any(_close(abs(x), abs(exp), "money") for x in numbers)   # «убыток 207 530,4»
    return any(_close(x, exp, item["kind"]) for x in numbers)


def check_report(text: str, truth: dict) -> dict:
    """REPORT.md must state ALL verified findings: each entity together with its correct number on the same line
    (or the next line when that line names no other finding). Partial -> complete=False with the missing items."""
    findings = report_findings(truth)
    lines = (text or "").splitlines()
    patterns = {f["name"]: _entity_re(str(f["entity"])) for f in findings}
    items, missing = {}, []
    for f in findings:
        pat = patterns[f["name"]]
        hit_lines = [i for i, ln in enumerate(lines) if pat.search(ln)]
        near: list[float] = []
        ok = False
        for i in hit_lines:
            windows = [lines[i]]
            if i + 1 < len(lines) and not any(p.search(lines[i + 1]) for n, p in patterns.items() if n != f["name"]):
                windows.append(lines[i + 1])
            for w in windows:
                nums = parse_numbers(w)
                near.extend(nums)
                if _number_ok(f, nums, lines[i] + " " + w):
                    ok = True
        items[f["name"]] = {"entity": f["entity"], "expected": f["expected"], "entity_found": bool(hit_lines),
                            "number_found": ok, "numbers_near": [round(x, 2) for x in near[:8]]}
        if not hit_lines:
            missing.append(f"{f['name']}: «{f['entity']}» ({f['label']}) not mentioned")
        elif not ok:
            missing.append(f"{f['name']}: «{f['entity']}» stated without the verified number {f['expected']} "
                           f"(numbers near it: {items[f['name']]['numbers_near']})")
    return {"complete": not missing, "items": items, "missing": missing}


# ------------------------------------------------------------------ memory usefulness (pure)
def make_code_word(rng: random.Random | None = None) -> str:
    """The auditor code word: lives only in the memory record and in truth_dir (outside every agent root)."""
    pick = (rng.choice if rng else secrets.choice)
    num = rng.randrange(100, 1000) if rng else 100 + secrets.randbelow(900)
    return f"{pick(CODE_WORDS_A)}-{pick(CODE_WORDS_B)}-{num}"


def _norm(s: str) -> str:
    return re.sub(r"[^0-9A-Za-zА-Яа-яЁё]", "", s or "").upper()


def _ref_match(source: str, ref: str) -> bool:
    a, b = str(source).replace("\\", "/").lower().strip("/"), str(ref).replace("\\", "/").lower().strip("/")
    if not a or not b:
        return False
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)       # vault-relative ref vs absolute note path


def check_memory(recall_rows: list[dict], report_text: str, code_word: str, record_refs: list[str],
                 control: bool) -> dict:
    """recall_rows: payloads of `memory.recalled` events ({"sources": [...], "preview": "..."}).
    Normal run: ok only when the recall surfaced THIS round's record (its fact id / note ref, or the code word in the
    recalled text) AND the report reproduces the code word - the only way to know it is memory.
    Control (no record written): ok only when the code word is absent from the report and from the recall."""
    sources = [str(s) for r in recall_rows for s in (r.get("sources") or [])]
    preview = "\n".join(str(r.get("preview") or "") for r in recall_rows)
    cw = _norm(code_word)
    record_in_recall = any(_ref_match(s, ref) for s in sources for ref in record_refs if ref)
    token_in_recall = bool(cw) and cw in _norm(preview)
    token_in_report = bool(cw) and cw in _norm(report_text)
    res = {"mode": "control" if control else "memory", "recalled_events": len(recall_rows), "sources": sources[:10],
           "record_refs": [r for r in record_refs if r], "record_in_recall": record_in_recall,
           "code_word_in_recall": token_in_recall, "code_word_in_report": token_in_report}
    if control:
        res["ok"] = not token_in_report and not token_in_recall and not record_in_recall
        res["reason"] = ("control holds: the code word is unknowable without the memory record" if res["ok"]
                         else "CONTROL BROKEN: the code word reached the agent without a memory record (leak)")
        return res
    if not recall_rows:
        res["ok"], res["reason"] = False, "no memory.recalled event in step 5"
    elif not (record_in_recall or token_in_recall):
        res["ok"], res["reason"] = False, "recall did not surface this round's memory record"
    elif not token_in_report:
        res["ok"], res["reason"] = False, "REPORT.md does not reproduce the memory-only auditor code word"
    else:
        res["ok"], res["reason"] = True, "recall surfaced this round's record and the report used it"
    return res


# ------------------------------------------------------------------ rounds bookkeeping (pure)
def count_duplicated_completed_calls(rows_by_task: dict) -> int:
    """rows_by_task: {task_id: [(id, call_id, tool, args_hash, effect, status, ...), ...]} across ALL rounds and
    attempts. A call_id that completed more than once inside its task was executed twice."""
    dup = 0
    for rows in rows_by_task.values():
        seen: dict = {}
        for r in rows:
            if r[5] in DONE:
                seen[r[1]] = seen.get(r[1], 0) + 1
        dup += sum(1 for v in seen.values() if v > 1)
    return dup


def step_action(rec: dict, task_status: str | None, stop_pending: bool = False) -> str:
    """skip = verified, never redo; attach = the step's task still exists and may finish (resume must NOT start a
    second task for it); new = no task yet, or the previous attempt ended without a verified result."""
    if rec.get("verified"):
        return "skip"
    if not rec.get("task_id"):
        return "new"
    if rec.get("problems"):
        return "new"
    if stop_pending:
        return "attach"                                              # our own STOP: the same task gets the retry
    if task_status in ("failed", "blocked", "stopped"):
        return "new"
    return "attach"


def round_finished(r: dict) -> bool:
    return int(r.get("next", 0)) >= len(STEP_NAMES)


def resume_point(state: dict, now_ts: float | None = None) -> tuple[int, int]:
    """(round, step index) where a run continues: the first unverified step of the current round, or step 0 of the next
    round when the current one finished cleanly and more rounds are due."""
    k = int(state.get("current_round") or 1)
    r = (state.get("rounds") or {}).get(str(k))
    if r is None:
        return k, 0
    if round_finished(r) and not r.get("failed") and wants_more_rounds(state, k, time.time() if now_ts is None else now_ts):
        return k + 1, 0
    return k, min(int(r.get("next", 0)), len(STEP_NAMES))


def wants_more_rounds(state: dict, k: int, now_ts: float) -> bool:
    if state.get("soak_hours"):
        return now_ts - float(state["started_ts"]) < float(state["soak_hours"]) * 3600
    return k < int(state.get("rounds_planned") or 1)


def time_for_round(state: dict, now_ts: float) -> bool:
    """False when the hard cap would cut the next round: remaining time < mean duration of the finished rounds."""
    deadline = state.get("deadline_ts")
    if deadline is None:
        return True
    remaining = float(deadline) - now_ts
    durs = [float(r["finished_ts"]) - float(r["started_ts"]) for r in (state.get("rounds") or {}).values()
            if r.get("finished_ts") is not None and r.get("started_ts") is not None]
    need = sum(durs) / len(durs) if durs else 0.0
    return remaining > need


def round_verdict(r: dict, dup: int) -> list[str]:
    """Reasons a finished round fails; empty = every check passed."""
    reasons = []
    for n in STEP_NAMES:
        s = (r.get("steps") or {}).get(n) or {}
        if not s.get("verified"):
            reasons.append(f"step {n} not verified: {s.get('problems') or 'not run'}")
    if not r.get("interrupt_done"):
        reasons.append("backend was not killed during step 3")
    if r.get("stop_new_calls") != 0:
        n = r.get("stop_new_calls")
        reasons.append(f"STOP: {n} tool call(s) started after STOP" if isinstance(n, int) else "STOP not checked")
    if dup:
        reasons.append(f"{dup} completed tool call(s) executed twice")
    if r.get("sha_changed"):
        reasons.append(f"runtime SHA changed inside the round: {r.get('sha_changed')}")
    if ((r.get("steps") or {}).get("report") or {}).get("verified"):
        rc, mc = r.get("report_check") or {}, r.get("memory_check") or {}
        if not rc.get("complete"):
            reasons.append("REPORT.md incomplete: " + "; ".join(rc.get("missing") or ["not checked"]))
        if not mc.get("ok"):
            reasons.append("memory: " + str(mc.get("reason") or "not checked"))
    return reasons


def round_status(r: dict, dup: int) -> tuple[str, list[str]]:
    if round_finished(r):
        reasons = round_verdict(r, dup)
        return ("PASS" if not reasons else "FAIL"), reasons
    if r.get("blocked"):
        s = (r.get("steps") or {}).get(r["blocked"]) or {}
        return "FAIL", [f"step {r['blocked']} blocked: {s.get('problems')}"]
    where = STEP_NAMES[min(int(r.get("next", 0)), len(STEP_NAMES) - 1)]
    why = "stopped by --max-hours" if r.get("stopped_by_deadline") else "not finished"
    return "INCOMPLETE", [f"{why} at step {where}"]


def summarize(state: dict, dups: dict, now_ts: float, end_reason: str | None = None) -> dict:
    """Per-round and overall verdict. PASS only if every planned round finished and passed every check (and, for a
    soak, the soak duration was reached); FAIL if any round failed a check; otherwise PARTIAL."""
    rounds_out, failed, incomplete, passed = [], [], [], []
    for key in sorted(state.get("rounds") or {}, key=int):
        r = state["rounds"][key]
        dup = int(dups.get(int(key), dups.get(key, 0)) or 0)
        status, reasons = round_status(r, dup)
        mc, rc = r.get("memory_check") or {}, r.get("report_check") or {}
        dur = ((float(r["finished_ts"]) - float(r["started_ts"])) / 60
               if r.get("finished_ts") is not None and r.get("started_ts") is not None else None)
        rounds_out.append({
            "round": int(key), "round_id": r.get("round_id"), "seed": r.get("seed"), "planted": r.get("planted"),
            "sha": r.get("sha"), "status": status, "reasons": reasons,
            "steps": {n: {x: (r.get("steps") or {}).get(n, {}).get(x) for x in ("task_id", "verified", "attempts")}
                      for n in STEP_NAMES},
            "interrupted": bool(r.get("interrupt_done")), "stop_new_calls": r.get("stop_new_calls"),
            "duplicated_completed_calls": dup, "report_complete": rc.get("complete"),
            "report_missing": rc.get("missing"), "memory": {k: mc.get(k) for k in (
                "mode", "ok", "reason", "recalled_events", "record_in_recall", "code_word_in_recall",
                "code_word_in_report")},
            "duration_min": round(dur, 1) if dur is not None else None})
        {"PASS": passed, "FAIL": failed, "INCOMPLETE": incomplete}[status].append(rounds_out[-1])
    started_ts = state.get("started_ts")
    elapsed_h = (now_ts - float(now_ts if started_ts is None else started_ts)) / 3600
    shas = {r.get("sha") for r in rounds_out if r.get("sha")}
    soak_h = state.get("soak_hours")
    reasons = []
    if failed:
        verdict = "FAIL"
        reasons += [f"round {r['round']}: {x}" for r in failed for x in r["reasons"]]
    elif len(shas) > 1:
        verdict = "FAIL"
        reasons.append(f"rounds ran on different SHAs: {sorted(shas)}")
    else:
        planned = state.get("rounds_planned") or 1
        enough = (elapsed_h >= float(soak_h)) if soak_h else len(passed) >= int(planned)
        if incomplete or not enough:
            verdict = "PARTIAL"
            reasons += [f"round {r['round']}: {x}" for r in incomplete for x in r["reasons"]]
            if soak_h and elapsed_h < float(soak_h):
                reasons.append(f"soak ended after {elapsed_h:.2f} h of {soak_h} h")
            elif not soak_h and len(passed) < int(planned):
                reasons.append(f"{len(passed)} of {planned} planned rounds passed")
            if end_reason:
                reasons.append(f"run ended: {end_reason}")
        else:
            verdict = "PASS"
    if soak_h:
        soak = {"requested_hours": soak_h, "status": "SOAK_PASS" if verdict == "PASS" else "NOT_MET"}
    else:
        soak = {"requested_hours": None, "status": "NOT_CLAIMED",
                "note": f"a {elapsed_h:.2f} h run never claims 24H_SOAK; use --soak-hours 24"}
    return {"run_id": state.get("run_id"), "mode": "no-memory-control" if state.get("control") else "memory",
            "rounds_planned": None if soak_h else state.get("rounds_planned"), "rounds_passed": len(passed),
            "rounds": rounds_out, "elapsed_hours": round(elapsed_h, 2), "max_hours": state.get("max_hours"),
            "duplicated_completed_calls": sum(r["duplicated_completed_calls"] for r in rounds_out),
            "soak": soak, "verdict": verdict, "reasons": reasons}


def render_readme(res: dict, state: dict) -> str:
    def yn(v):
        return "—" if v is None else ("yes" if v else "NO")
    lines = [f"# Long-horizon run {res['run_id']} — receipt", "",
             f"Verdict: **{res['verdict']}** · mode `{res['mode']}` · rounds passed {res['rounds_passed']}"
             f"{' of ' + str(res['rounds_planned']) if res['rounds_planned'] else ''} · elapsed {res['elapsed_hours']} h"
             f" · cap {res['max_hours'] or 'none'} h · agent {state.get('agent')} · driver v{DRIVER_VERSION}", ""]
    if res["reasons"]:
        lines += ["Reasons:", *[f"- {x}" for x in res["reasons"]], ""]
    lines += ["| Round | Seed | Planted (product / customer / region) | Steps verified | Backend killed | STOP: calls after |"
              " Dup calls | Report complete | Memory: record recalled / code word in report | Minutes | SHA | Status |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in res["rounds"]:
        pl = r.get("planted") or {}
        ver = sum(1 for s in r["steps"].values() if s.get("verified"))
        mem = r["memory"]
        mem_cell = (f"{yn(mem.get('record_in_recall') or mem.get('code_word_in_recall'))} / {yn(mem.get('code_word_in_report'))}"
                    if mem.get("mode") else "—")
        lines.append(f"| {r['round']} | {r['seed']} | {pl.get('negative_margin_product')} / {pl.get('top_customer')} / "
                     f"{pl.get('declining_region')} | {ver}/5 | {yn(r['interrupted'])} | {r['stop_new_calls']} | "
                     f"{r['duplicated_completed_calls']} | {yn(r['report_complete'])} | {mem_cell} | "
                     f"{r['duration_min'] if r['duration_min'] is not None else '—'} | {str(r.get('sha') or '')[:8]} | "
                     f"{r['status']} |")
    soak = res["soak"]
    lines += ["", f"24 h soak: **{soak['status']}**" + (f" — {soak['note']}" if soak.get("note") else
                                                         f" (requested {soak['requested_hours']} h)"), "",
              "Files: `state.json` (checkpoints per round), `driver.log`, `r<k>/truth.json` (planted truth + seed + "
              "memory-only auditor code word), `r<k>/agent_REPORT.md`, `r<k>/agent_verify.json`.", "",
              "Memory check: the driver writes the verified findings plus «аудитор: <code word>» to Bossman memory; the code "
              "word is in no file the agent can read and not in any prompt. Negative control: "
              "`python tools/long_horizon_run.py start --rounds 1 --no-memory-control` — the code word must be ABSENT.", ""]
    return "\n".join(lines)


# ------------------------------------------------------------------ scope guard for the driver's approvals
def _inside(path: Path, ws: Path) -> bool:
    path, ws = path.resolve(), ws.resolve()
    return path == ws or ws in path.parents


def command_in_scope(command: str, cwd: str, ws: Path) -> str | None:
    """None = the driver may approve it; otherwise the refusal reason. Never widens rights: refuses anything that leaves
    the run folder, touches the network or secrets, or deletes recursively."""
    if not cwd or not _inside(Path(cwd), ws):
        return f"cwd outside the run folder: {cwd}"
    if FORBIDDEN.search(command or ""):
        return "command matches the forbidden list"
    for m in re.finditer(r"[A-Za-z]:[\\/][^\s\"']*", command or ""):
        if not _inside(Path(m.group(0)), ws):
            return f"path outside the run folder: {m.group(0)}"
    return None


# ------------------------------------------------------------------ plan (no answers in the prompts)
def steps(ws: Path, round_id: str = "") -> list[dict]:
    base = (
        "Все команды выполняй через terminal.run с mode=\"project_host\" (поле cwd НЕ передавай — "
        "рабочая папка уже выбрана в карточке владельца Auto Full, используй относительные пути внутри текущей папки). "
        "Используй Python (`python -c \"...\"`). Данные: `orders.csv` в текущей папке "
        "(колонки order_id,date,region,product,qty,unit_price,unit_cost,customer_id; "
        "есть дубли order_id — считай по первой встреченной строке). Выход: только внутри текущей папки. Не используй сеть. "
    )
    drop = ("падение среднемесячной выручки региона: последние 6 месяцев к первым 12 месяцам, "
            "`round((1 - avg_last6 / avg_first12) * 100, 1)` в процентах")
    return [
        {"name": "profile", "prompt": base + (
            "ШАГ 1 из 5. Профилирование одним вызовом terminal.run: посчитай общее число строк данных (rows), "
            "число дублей order_id (duplicate_rows) и число уникальных заказов (unique_orders). "
            "Запиши `out/profile.json` с ключами rows, duplicate_rows, unique_orders и `notes/01_profile.md` (3–6 строк выводов)."
        ), "evidence": ["out\\profile.json", "notes\\01_profile.md"]},
        {"name": "metrics", "prompt": base + (
            "ШАГ 2 из 5. Одним вызовом terminal.run по уникальным заказам из `orders.csv` посчитай и сохрани три CSV-файла: "
            "1) выручка по месяцам и регионам -> `out/monthly_region.csv` (колонки region,month,revenue); "
            "2) маржа по продуктам -> `out/product_margin.csv` (колонки product,revenue,margin, где margin = sum(qty*(unit_price-unit_cost))); "
            "3) выручка по клиентам топ-10 -> `out/top_customers.csv` (колонки customer_id,revenue,share_pct)."
        ), "evidence": ["out\\monthly_region.csv", "out\\product_margin.csv", "out\\top_customers.csv"]},
        {"name": "findings", "prompt": base + (
            "ШАГ 3 из 5. Выполни в ДВА отдельных шага (два отдельных вызова модели с terminal.run): "
            "Сначала ПЕРВЫМ вызовом terminal.run прочитай `out/product_margin.csv`, `out/top_customers.csv` и `out/monthly_region.csv` "
            f"и выведи в консоль: худший продукт по марже и его маржу; топ-1 клиента и его долю выручки в %; "
            f"регион с наибольшим падением ({drop}) и это падение. "
            "Затем ВТОРЫМ вызовом terminal.run запиши эти три проверяемые находки (код продукта, id клиента, название региона) "
            "с числами и именами файлов-источников в `notes/03_findings.md`."
        ), "evidence": ["notes\\03_findings.md"]},
        {"name": "verify", "prompt": base + (
            "ШАГ 4 из 5. Выполни в ДВА отдельных вызова terminal.run: "
            "Сначала ПЕРВЫМ вызовом запиши скрипт -> `out/verify.py`, который берёт три находки из `notes/03_findings.md` / `out/*.csv` "
            "и пересчитывает каждое число по `orders.csv` (уникальные заказы): "
            "1: маржа худшего продукта; 2: доля топ-1 клиента в %; "
            f"3: {drop} для региона с наибольшим падением; "
            "и записывает результат -> `out/verify.json` ровно с тремя верхнеуровневыми ключами в формате "
            "`{\"worst_margin_product\": {\"product\": \"<код>\", \"claimed\": <маржа>, \"recomputed\": <маржа>, \"ok\": true}, "
            "\"top_customer\": {\"customer_id\": \"<id>\", \"claimed\": <доля %>, \"recomputed\": <доля %>, \"ok\": true}, "
            "\"region_drop\": {\"region\": \"<регион>\", \"claimed\": <падение %>, \"recomputed\": <падение %>, \"ok\": true}}` "
            "(ok = claimed совпадает с recomputed). "
            "Затем ВТОРЫМ вызовом запусти `python out/verify.py`, чтобы создался файл -> `out/verify.json`."
        ), "evidence": ["out\\verify.py", "out\\verify.json"]},
        {"name": "report", "prompt": base + (
            f"ШАГ 5 из 5. В памяти Bossman есть проверенная запись «LongHorizon {round_id}» с находками этого прогона "
            "и строкой «аудитор: …» (кто подтвердил находки). Используй эту запись из памяти. "
            "ОДНИМ ПЕРВЫМ вызовом terminal.run (`python -c \"...\"`) прочитай `out/verify.json` и СРАЗУ запиши итоговый отчёт в файл -> `REPORT.md`: "
            "резюме для владельца (5 строк); ОБЯЗАТЕЛЬНО все три проверенные находки, каждая в ОДНОЙ строке вместе со своим числом: "
            "1) худший продукт по марже — код продукта и маржа; 2) топ-1 клиент — id и доля выручки в %; "
            "3) регион с падением — название региона и падение в %; "
            "отдельной строкой `Аудитор: <значение из памяти>` (если в памяти его нет — `Аудитор: не найден в памяти`); "
            "как проверено (ссылка на `out/verify.json`); ограничения. Не трать шаги на чтение других файлов, сразу запиши -> `REPORT.md`!"
        ), "evidence": ["REPORT.md"]},
    ]


# ------------------------------------------------------------------ driver
def new_state(run_id: str, agent: int, *, rows: int, rounds: int, max_hours: float | None, soak_hours: float | None,
              control: bool) -> dict:
    t = time.time()
    cap = max_hours if max_hours else ((soak_hours + 2) if soak_hours else None)
    return {"run_id": run_id, "agent": agent, "driver_version": DRIVER_VERSION, "started": now(), "started_ts": t,
            "rows": rows, "rounds_planned": None if soak_hours else rounds, "max_hours": cap,
            "deadline_ts": (t + cap * 3600) if cap else None, "soak_hours": soak_hours, "control": control,
            "current_round": 1, "rounds": {}, "events": []}


class Driver:
    def __init__(self, run_id: str, agent: int, *, new: dict | None = None):
        self.run_id, self.agent = run_id, agent
        self.run_dir = TRUTH_ROOT / run_id
        self.state_path = self.run_dir / "state.json"
        self.log_path = self.run_dir / "driver.log"
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if self.state.get("driver_version") != DRIVER_VERSION:
                raise SystemExit("state.json was written by another driver version - resume it with that revision")
        elif new is None:
            raise SystemExit(f"no state for {run_id} under {self.run_dir}")
        else:
            self.state = new
        self.R: dict = {}
        self.k = int(self.state.get("current_round") or 1)
        self.ws = RUNS / f"{run_id}-r{self.k}"
        self.truth_dir = self.run_dir / f"r{self.k}"

    # -------------------------------------------------------------- persistence
    def save(self, **kw):
        self.state.update(kw)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def rsave(self, **kw):
        self.R.update(kw)
        self.save()

    def log(self, kind: str, **data):
        ev = {"t": now(), "round": self.k, "kind": kind, **data}
        self.state["events"].append(ev)
        self.save()
        line = f"[{ev['t']}] r{self.k} {kind} {json.dumps(data, ensure_ascii=False)[:220]}"
        print(line, flush=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def enter_round(self, k: int):
        self.k = k
        key = str(k)
        if key not in self.state["rounds"]:
            self.state["rounds"][key] = {"round": k, "round_id": f"{self.run_id}-r{k}", "seed": round_seed(self.run_id, k),
                                         "steps": {}, "next": 0, "started": now(), "started_ts": time.time()}
        self.R = self.state["rounds"][key]
        self.state["current_round"] = k
        self.ws = RUNS / self.R["round_id"]
        self.truth_dir = self.run_dir / f"r{k}"
        self.save()

    def code_word(self) -> str:
        f = self.truth_dir / "memory_code_word.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))["code_word"]
        cw = make_code_word()
        self.truth_dir.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"round_id": self.R["round_id"], "code_word": cw}, ensure_ascii=False), encoding="utf-8")
        self.rsave(code_word_sha256=hashlib.sha256(cw.encode("utf-8")).hexdigest()[:16])
        return cw

    def deadline_passed(self) -> bool:
        d = self.state.get("deadline_ts")
        return d is not None and time.time() >= float(d)

    # -------------------------------------------------------------- backend
    def identity(self) -> dict:
        h = _api().api("GET", "/health/live", timeout=15)
        head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        ok = h.get("build_sha") == head and h.get("source_identity") == "PASS"
        self.log("identity", runtime=h.get("build_sha"), head=head, source_identity=h.get("source_identity"), ok=ok)
        if not ok:
            raise SystemExit("IDENTITY_STALE: runtime SHA differs from checkout HEAD - not running on an unproven build")
        return {"runtime": h.get("build_sha"), "head": head}

    def handle_approvals(self, task_id: int) -> int:
        p, n = _api(), 0
        pending = p.api("GET", "/api/approvals?status=pending")
        pending = pending if isinstance(pending, list) else pending.get("approvals", [])
        for a in pending:
            if a.get("task_id") != task_id:
                continue
            prev = a.get("preview") or ""
            m = re.search(r"cwd[\"':= ]+([^\s\"',}]+)", prev)
            cmd_m = re.search(r"command[\"':= ]+(.+)", prev, re.S)
            why = command_in_scope(cmd_m.group(1) if cmd_m else prev, m.group(1) if m else str(self.ws), self.ws)
            if why:
                p.api("POST", f"/api/approvals/{a['id']}", {"approve": False, "by": "harness:long_horizon"})
                self.log("approval_refused", approval=a["id"], why=why)
                continue
            by = "harness:long_horizon(test-folder)"
            try:
                p.api("POST", f"/api/approvals/{a['id']}", {"approve": True, "by": by, "lease": {"max_uses": 40, "ttl_seconds": 1800}})
            except Exception:                                       # a lease is optional; the single approval still stands
                p.api("POST", f"/api/approvals/{a['id']}", {"approve": True, "by": by})
            n += 1
        return n

    def tool_rows(self, task_id: int) -> list[tuple]:
        db = db_ro()
        try:
            return db.execute("select id,call_id,tool,args_hash,effect,status,created_at,finished_at from tool_calls "
                              "where task_id=? order by id", (task_id,)).fetchall()
        finally:
            db.close()

    def task_status(self, task_id: int) -> str | None:
        try:
            return _api().api("GET", f"/api/tasks/{task_id}", timeout=60)["task"]["status"]
        except Exception:
            return None

    def wait_task(self, task_id: int, *, interrupt=None, stop_when=None, timeout=3 * 3600) -> dict | None:
        """None = the --max-hours cap was reached while waiting."""
        p = _api()
        t0, interrupted = time.time(), False
        poll_s = 1.5 if (interrupt or stop_when) else 4.0
        while time.time() - t0 < timeout:
            if self.deadline_passed():
                return None
            d = p.api("GET", f"/api/tasks/{task_id}", timeout=60)
            st = d["task"]["status"]
            self.handle_approvals(task_id)
            rows = self.tool_rows(task_id)
            if interrupt and not interrupted and len([r for r in rows if r[5] in DONE]) >= interrupt["after_calls"]:
                interrupt["do"](task_id, len([r for r in rows if r[5] in DONE]))
                interrupted = True
            if stop_when and len(rows) >= stop_when["after_calls"]:
                stop_when["do"](task_id)
                return p.api("GET", f"/api/tasks/{task_id}", timeout=60)
            if st in ("completed", "failed", "stopped", "blocked"):
                return d
            time.sleep(poll_s)
        raise SystemExit(f"TIMEOUT waiting for task {task_id}")

    # -------------------------------------------------------------- verification of one step
    def verify_step(self, name: str, spec: dict, truth: dict) -> list[str]:
        problems = []
        for rel in spec["evidence"]:
            f = evidence_path(self.ws, rel)
            if not f.is_file() or f.stat().st_size == 0:
                problems.append(f"missing or empty: {rel}")
        if problems:
            return problems
        try:
            if name == "profile":
                prof = json.loads((self.ws / "out" / "profile.json").read_text(encoding="utf-8"))
                for k in ("duplicate_rows", "unique_orders"):
                    if int(prof.get(k, -1)) != truth[k]:
                        problems.append(f"{k} {prof.get(k)} != truth {truth[k]}")
            if name == "metrics":
                pm = list(csv.DictReader((self.ws / "out" / "product_margin.csv").open(encoding="utf-8")))
                worst = min(pm, key=lambda r: float(r["margin"]))["product"] if pm else ""
                if worst != truth["worst_margin_product"]:
                    problems.append(f"worst margin product {worst} != truth {truth['worst_margin_product']}")
            if name == "findings":
                text = (self.ws / "notes" / "03_findings.md").read_text(encoding="utf-8")
                for key in (truth["worst_margin_product"], truth["top_customer"], truth["declining_region"]):
                    if not _entity_re(key).search(text):
                        problems.append(f"finding about {key} not present")
            if name == "verify":
                v = json.loads((self.ws / "out" / "verify.json").read_text(encoding="utf-8"))
                bad = [k for k, x in v.items() if not str(k).startswith("_") and not (isinstance(x, dict) and x.get("ok") is True)]
                if not v or bad:
                    problems.append(f"verify.json not all ok: {bad[:3]}")
                for key, name_ in (("worst_margin_product", truth["worst_margin_product"]),
                                   ("top_customer", truth["top_customer"]), ("region_drop", truth["declining_region"])):
                    if key not in v or not _entity_re(name_).search(json.dumps(v.get(key), ensure_ascii=False)):
                        problems.append(f"verify.json {key} does not name the verified {name_}")
            if name == "report":
                text = (self.ws / "REPORT.md").read_text(encoding="utf-8")
                rc = check_report(text, truth)
                self.R["report_check"] = rc
                problems += [f"REPORT.md misses: {m}" for m in rc["missing"]]
        except (ValueError, KeyError, OSError) as exc:
            problems.append(f"evidence unreadable: {type(exc).__name__}")
        return problems

    # -------------------------------------------------------------- auto-full lease per round folder
    def _ensure_auto_full(self) -> None:
        try:
            af = _api().api("POST", "/api/approvals/auto-full", {
                "folder": str(self.ws), "ttl_seconds": 7200, "max_uses": 300, "by": "owner:pult(auto-full)"})
            cur = (af or {}).get("current") or (af or {}).get("granted") or {}
            self.rsave(auto_full_lease_id=cur.get("id"))
            self.log("auto_full_enabled", lease_id=cur.get("id"), folder=cur.get("folder"))
        except Exception as exc:
            self.log("auto_full_warn", error=str(exc)[:180])

    def _close_auto_full(self) -> dict:
        p = _api()
        try:
            st = p.api("GET", "/api/approvals/auto-full?limit_log=50")
            cur = (st or {}).get("current") or {}
            log_items = (st or {}).get("action_log") or []
            card = {"lease_id": cur.get("id") or self.R.get("auto_full_lease_id"), "folder": cur.get("folder") or str(self.ws),
                    "used": cur.get("used", len(log_items)), "action_log_count": len(log_items)}
            if cur.get("id"):
                p.api("POST", "/api/approvals/auto-full/revoke", {"lease_id": cur["id"], "by": "owner:pult(auto-full-finish)"})
                card["revoked_on_finish"] = True
        except Exception as exc:
            card = {"error": str(exc)[:160]}
        self.rsave(auto_full_card=card)
        return card

    # -------------------------------------------------------------- memory
    def _store_verified_findings_in_memory(self, truth: dict) -> None:
        p, rid, cw = _api(), self.R["round_id"], self.code_word()
        stmt = (f"В прогоне LongHorizon {rid} по таблице orders.csv проверены три ключевые находки: "
                f"продукт {truth['worst_margin_product']} убыточен, маржа {truth['worst_margin_value']}; "
                f"клиент {truth['top_customer']} даёт наибольшую долю выручки {truth['top_customer_share_pct']} процента; "
                f"в регионе {truth['declining_region']} падение выручки на {truth['region_drop_pct']} процента. "
                f"аудитор: {cw}")
        try:
            fact = p.api("POST", "/api/memory/facts", {
                "subject": f"LongHorizon {rid} orders.csv", "predicate": "проверенные находки",
                "object": f"{truth['worst_margin_product']}, {truth['top_customer']}, {truth['declining_region']}",
                "statement": stmt, "valid_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "mode": "append",
                "source_kind": "run", "source_note": f"{rid}/out/verify.json", "confidence": 1.0})
            self.rsave(memory_fact_id=(fact or {}).get("id"))
            self.log("memory_fact_written", fact_id=(fact or {}).get("id"))
        except Exception as exc:
            self.log("memory_fact_warn", error=str(exc)[:180])
        try:
            note = p.api("POST", "/api/memory/write", {
                "title": f"LongHorizon {rid} orders.csv verified findings", "kind": "note", "project": "bossman",
                "tags": ["long-horizon", "orders-csv", "verified"],
                "content": (f"# LongHorizon {rid} проверенные находки по orders.csv\n\n"
                            f"1. Худший продукт по марже: **{truth['worst_margin_product']}**, маржа {truth['worst_margin_value']}.\n"
                            f"2. Топ-1 клиент: **{truth['top_customer']}**, доля {truth['top_customer_share_pct']}% выручки.\n"
                            f"3. Падение среднемесячной выручки региона **{truth['declining_region']}**: "
                            f"**{truth['region_drop_pct']}%** (последние 6 мес к первым 12 мес).\n\n"
                            f"аудитор: {cw}\n")})
            self.rsave(memory_note_path=(note or {}).get("path"))
            self.log("memory_note_written", path=(note or {}).get("path"))
        except Exception as exc:
            self.log("memory_note_warn", error=str(exc)[:180])
        self.rsave(memory_stored=True)

    def _recall_rows(self, task_id: int) -> list[dict]:
        db = db_ro()
        try:
            rows = db.execute("select e.data from run_events e join task_runs r on r.id=e.run_id "
                              "where r.task_id=? and e.kind='memory.recalled' order by e.id", (task_id,)).fetchall()
        finally:
            db.close()
        out = []
        for (raw,) in rows:
            try:
                pl = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except Exception:
                pl = {}
            out.append(pl if isinstance(pl, dict) else {})
        return out

    def _memory_check(self, task_id: int) -> dict:
        f = self.ws / "REPORT.md"
        text = f.read_text(encoding="utf-8") if f.is_file() else ""
        refs = [f"fact:{self.R['memory_fact_id']}" if self.R.get("memory_fact_id") else "", self.R.get("memory_note_path") or ""]
        res = check_memory(self._recall_rows(task_id), text, self.code_word(), refs, bool(self.state.get("control")))
        self.rsave(memory_check=res)
        self.log("memory", **{k: v for k, v in res.items() if k != "sources"})
        return res

    # -------------------------------------------------------------- one round
    def run_round(self, k: int, ident: dict) -> str:
        """passed | failed | blocked | deadline"""
        self.enter_round(k)
        self.rsave(sha=self.R.get("sha") or ident["head"])
        if self.R["sha"] != ident["head"]:
            self.rsave(sha_changed=f"{self.R['sha'][:12]} -> {ident['head'][:12]}")
            self.log("sha_changed", round_sha=self.R["sha"], now=ident["head"])
        if not (self.ws / "orders.csv").exists():
            truth = make_dataset(self.ws, self.truth_dir, int(self.state.get("rows", 60000)), self.R["seed"])
            self.log("dataset", seed=self.R["seed"], **{x: truth[x] for x in ("unique_orders", "duplicate_rows")})
        truth = json.loads((self.truth_dir / "truth.json").read_text(encoding="utf-8"))
        self.rsave(planted={x: truth["planted"][x] for x in ("negative_margin_product", "top_customer", "declining_region")})
        self.code_word()
        for sub in ("out", "notes"):
            (self.ws / sub).mkdir(exist_ok=True)
        if not self.R.get("auto_full_lease_id"):
            self._ensure_auto_full()
        plan = steps(self.ws, self.R["round_id"])
        control = bool(self.state.get("control"))
        p = _api()
        for i in range(int(self.R["next"]), len(plan)):
            spec, name = plan[i], plan[i]["name"]
            if self.deadline_passed():
                self.rsave(stopped_by_deadline=True)
                self.log("max_hours_reached", step=name)
                return "deadline"
            if name == "report" and not control and not self.R.get("memory_stored"):
                self._store_verified_findings_in_memory(truth)
            rec = self.R["steps"].get(name) or {}
            status = self.task_status(rec["task_id"]) if rec.get("task_id") and not rec.get("verified") else None
            act = step_action(rec, status, bool(self.R.get("stop_pending")) and name == "verify")
            if act == "new":
                attempt = int(rec.get("attempts", 1 if rec.get("task_id") else 0)) + 1
                sfx = f"-a{attempt}" if attempt > 1 else ""
                t = p.api("POST", "/api/tasks", {"prompt": spec["prompt"], "title": f"LH {self.R['round_id']} {i + 1}/5 {name}",
                                                 "agent_id": self.agent, "workspace_path": str(self.ws),
                                                 "run_now": True, "max_retries": 3,
                                                 "client_request_id": f"{self.R['round_id']}-{name}{sfx}"[:128]})
                tid = t.get("id") or t["task"]["id"]
                rec = {"task_id": tid, "task_ids": [*rec.get("task_ids", [rec["task_id"]] if rec.get("task_id") else []), tid],
                       "started": now(), "attempts": attempt}
                self.R["steps"][name] = rec
                self.save()
            self.log("step_started" if act == "new" else "step_attached", step=name, task=rec["task_id"],
                     task_status=status)
            interrupt = stop_when = None
            if name == "findings" and not self.R.get("interrupt_done"):
                interrupt = {"after_calls": 1, "do": self._do_interrupt}
            if name == "verify" and not self.R.get("stop_done"):
                stop_when = {"after_calls": 1, "do": self._do_stop}
            d = self.wait_task(rec["task_id"], interrupt=interrupt, stop_when=stop_when)
            if d is not None and name == "verify" and self.R.get("stop_pending"):
                self._after_stop(rec["task_id"])
                d = self.wait_task(rec["task_id"])
            if d is None:
                try:
                    p.api("POST", f"/api/tasks/{rec['task_id']}/stop")          # the harness's own test task
                except Exception:
                    pass
                self.rsave(stopped_by_deadline=True)
                self.log("max_hours_reached", step=name, task=rec["task_id"])
                return "deadline"
            status = d["task"]["status"]
            problems = self.verify_step(name, spec, truth) if status == "completed" else [f"task status {status}"]
            if name == "verify" and not problems and not control:
                self._store_verified_findings_in_memory(truth)
            if name == "report" and status == "completed":
                mc = self._memory_check(rec["task_id"])
                if not mc["ok"]:
                    problems.append(f"memory: {mc['reason']}")
            rec.update(status=status, finished=now(), problems=problems, verified=not problems)
            if problems:
                self.log("step_blocked", step=name, problems=problems)
                self.rsave(blocked=name)
                return "blocked"
            rec["checkpoint"] = {"round": k, "task_id": rec["task_id"], "sha": ident["head"],
                                 "result_sha256": {rel: hashlib.sha256(evidence_path(self.ws, rel).read_bytes()).hexdigest()[:16] for rel in spec["evidence"]},
                                 "next_step": plan[i + 1]["name"] if i + 1 < len(plan) else None}
            self.R.pop("blocked", None)
            self.rsave(next=i + 1)
            self.log("step_verified", step=name, checkpoint=rec["checkpoint"])
        dup = self.round_dups(self.R)
        self.rsave(duplicated_completed_calls=dup, finished=now(), finished_ts=time.time())
        self._close_auto_full()
        reasons = round_verdict(self.R, dup)
        self.rsave(failed=bool(reasons))
        self.log("round_finished", status="PASS" if not reasons else "FAIL", reasons=reasons)
        return "passed" if not reasons else "failed"

    def round_dups(self, r: dict) -> int:
        rows = {}
        for rec in (r.get("steps") or {}).values():
            for tid in rec.get("task_ids") or ([rec["task_id"]] if rec.get("task_id") else []):
                rows[tid] = self.tool_rows(tid)
        return count_duplicated_completed_calls(rows)

    def _do_interrupt(self, task_id: int, calls: int):
        done_before = [r[1] for r in self.tool_rows(task_id) if r[5] in DONE]
        self.log("interrupt_kill_backend", task=task_id, completed_calls=calls)
        subprocess.run(["powershell", "-NoProfile", "-File", str(ROOT / "tools" / "restart_backend.ps1")], check=True, timeout=300)
        self.rsave(interrupt_done=True, interrupt_task=task_id, calls_done_before_kill=done_before)
        self.identity()

    def _do_stop(self, task_id: int):
        _api().api("POST", f"/api/tasks/{task_id}/stop")
        self.rsave(stop_done=True, stop_pending=True, stop_at=now(), calls_at_stop=len(self.tool_rows(task_id)))
        self.log("stop_sent", task=task_id)

    def _after_stop(self, task_id: int):
        time.sleep(45)
        after = len(self.tool_rows(task_id)) - int(self.R["calls_at_stop"])
        self.rsave(stop_new_calls=after, stop_pending=False)
        self.log("stop_check", new_tool_calls_after_stop=after)
        _api().api("POST", f"/api/tasks/{task_id}/retry")
        self.log("stop_then_retry", task=task_id)

    # -------------------------------------------------------------- whole run
    def run(self) -> int:
        ident = self.identity()
        k, step_i = resume_point(self.state)
        self.log("resume_point" if self.state["rounds"] else "run_started", round=k, step=STEP_NAMES[min(step_i, 4)],
                 rounds_planned=self.state.get("rounds_planned"), max_hours=self.state.get("max_hours"),
                 soak_hours=self.state.get("soak_hours"), control=self.state.get("control"))
        end_reason = None
        while True:
            if self.deadline_passed():
                end_reason = "max-hours cap reached"
                break
            if str(k) not in self.state["rounds"] and k > 1 and not time_for_round(self.state, time.time()):
                end_reason = f"max-hours cap would cut round {k} (remaining < mean round duration)"
                break
            outcome = self.run_round(k, ident)
            self.write_receipts(final=False)
            if outcome != "passed":
                end_reason = {"deadline": "max-hours cap reached mid-round", "blocked": f"round {k} blocked",
                              "failed": f"round {k} failed its checks"}[outcome]
                break
            if not wants_more_rounds(self.state, k, time.time()):
                break
            k += 1
        return self.finish(end_reason)

    def finish(self, end_reason: str | None) -> int:
        dups = {int(key): (r.get("duplicated_completed_calls") if r.get("finished") else self.round_dups(r)) or 0
                for key, r in self.state["rounds"].items()}
        if self.R and not self.R.get("auto_full_card"):
            self._close_auto_full()
        res = summarize(self.state, dups, time.time(), end_reason)
        res["finished"] = now()
        self.save(result=res)
        self.log("run_finished", verdict=res["verdict"], rounds_passed=res["rounds_passed"])
        self.write_receipts(final=True)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0 if res["verdict"] == "PASS" else 1

    def receipt_dir(self) -> Path:
        return ROOT / "docs" / "audits" / f"{self.state['started'][:10]}-long-horizon-{self.run_id}"

    def write_receipts(self, *, final: bool) -> Path:
        out = self.receipt_dir()
        out.mkdir(parents=True, exist_ok=True)
        res = (self.state.get("result") if final else None) or summarize(
            self.state, {int(k): r.get("duplicated_completed_calls") or 0 for k, r in self.state["rounds"].items()},
            time.time(), "run in progress")
        for key, r in self.state["rounds"].items():
            if not (final or r.get("finished")):
                continue                                             # never reveal a running round's truth/code word
            rd, td, ws = out / f"r{key}", self.run_dir / f"r{key}", RUNS / r["round_id"]
            rd.mkdir(exist_ok=True)
            truth = json.loads((td / "truth.json").read_text(encoding="utf-8")) if (td / "truth.json").exists() else {}
            cwf = td / "memory_code_word.json"
            if cwf.exists():
                truth["memory_code_word"] = json.loads(cwf.read_text(encoding="utf-8"))["code_word"]
            (rd / "truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")
            for src, dst in (("REPORT.md", "agent_REPORT.md"), ("out/verify.json", "agent_verify.json")):
                if (ws / src).is_file():
                    shutil.copyfile(ws / src, rd / dst)
        shutil.copyfile(self.state_path, out / "state.json")
        if self.log_path.exists():
            shutil.copyfile(self.log_path, out / "driver.log")
        (out / "README.md").write_text(render_readme(res, self.state), encoding="utf-8")
        return out


# ------------------------------------------------------------------ selftest (no backend)
def selftest() -> int:
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        ws, tr = Path(d) / "ws", Path(d) / "truth"
        truth = make_dataset(ws, tr, 4000)
        again = aggregate(ws / "orders.csv")
        assert truth["worst_margin_product"] == "P-07" and again == {k: v for k, v in truth.items() if k != "planted"}, truth
        assert truth["duplicate_rows"] > 0 and truth["top_customer"] == "C-0042" and truth["north_drop_pct"] > 10, truth
        assert truth["declining_region"] == "Север" and truth["worst_margin_value"] < 0, truth
        for k in (1, 2, 3):                                          # fresh rounds: planted entities are the truth
            seed = round_seed("lh-selftest", k)
            t = make_dataset(Path(d) / f"ws{k}", Path(d) / f"tr{k}", 6000, seed)
            pl = t["planted"]
            assert (t["worst_margin_product"], t["top_customer"], t["declining_region"]) == (
                pl["negative_margin_product"], pl["top_customer"], pl["declining_region"]), t
        report = (f"1. Худший продукт {truth['worst_margin_product']}: маржа {truth['worst_margin_value']}\n"
                  f"2. Клиент {truth['top_customer']} — {str(truth['top_customer_share_pct']).replace('.', ',')} %\n"
                  f"3. Регион {truth['declining_region']}: падение на {truth['region_drop_pct']}%\n")
        assert check_report(report, truth)["complete"], check_report(report, truth)
        assert not check_report("\n".join(report.splitlines()[:2]), truth)["complete"]
        assert check_memory([{"sources": ["fact:7"], "preview": "аудитор: AMBER-OTTER-123"}],
                            "Аудитор: AMBER-OTTER-123", "AMBER-OTTER-123", ["fact:7"], False)["ok"]
        assert check_memory([], "Аудитор: не найден в памяти", "AMBER-OTTER-123", [], True)["ok"]
        assert command_in_scope("python x.py", str(ws), ws) is None
        assert command_in_scope("python x.py", str(Path(d)), ws)
        assert command_in_scope("curl http://x", str(ws), ws)
        assert command_in_scope(f"type {Path(d).parent}\\a.txt", str(ws), ws)
    print("selftest PASS")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=["start", "resume", "selftest"])
    ap.add_argument("--agent", type=int, default=48)
    ap.add_argument("--rows", type=int, default=60000)
    ap.add_argument("--run-id", default="")
    ap.add_argument("--rounds", type=int, default=1, help="full 5-step cycles, each on a fresh dataset")
    ap.add_argument("--max-hours", type=float, default=None, help="hard wall-clock cap; ends cleanly as PARTIAL")
    ap.add_argument("--soak-hours", type=float, default=None,
                    help="separate stability mode: rounds until this many hours passed (24 = the 24H_SOAK rung)")
    ap.add_argument("--no-memory-control", action="store_true",
                    help="negative control: write no memory record; the code word must be ABSENT from REPORT.md")
    a = ap.parse_args(argv)
    try:                                   # a cp866/cp1251 console must never crash a multi-hour run on «» or −
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    if a.mode == "selftest":
        return selftest()
    if a.mode == "resume":
        if not a.run_id:
            ap.error("resume needs --run-id")
        d = Driver(a.run_id, a.agent)
        d.agent = int(d.state.get("agent") or a.agent)
        return d.run()
    if a.rounds < 1:
        ap.error("--rounds must be >= 1")
    if a.soak_hours is not None and a.max_hours is not None and a.max_hours < a.soak_hours:
        ap.error("--max-hours below --soak-hours can never reach the soak; omit it (default cap = soak + 2 h)")
    run_id = a.run_id or time.strftime("lh-%Y%m%d-%H%M%S")
    d = Driver(run_id, a.agent, new=new_state(run_id, a.agent, rows=a.rows, rounds=a.rounds, max_hours=a.max_hours,
                                               soak_hours=a.soak_hours, control=a.no_memory_control))
    return d.run()


if __name__ == "__main__":
    raise SystemExit(main())
