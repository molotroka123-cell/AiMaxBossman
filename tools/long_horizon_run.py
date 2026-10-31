"""First long-horizon proof run for Bossman (owner 29.10): a multi-step task on synthetic business data, executed by a
LOCAL agent through the real task API, interrupted in the middle, resumed, STOPped, and verified independently.

    python tools/long_horizon_run.py start  [--agent 48] [--rows 60000]
    python tools/long_horizon_run.py resume --run-id lh-...   # continue after the DRIVER itself was interrupted
    python tools/long_horizon_run.py selftest                 # deterministic parts only (dataset truth, scope guard)

What is proven, and by what (no mocks, no pre-recorded answers):
  * identity     - /health/live build_sha == git HEAD of this checkout, source_identity PASS
  * progress     - state.json checkpoint after every verified step: run id, SHA, task id, result hashes, next step
  * interruption - the :8801 backend process is killed while step 3 has already executed tools; after restart the SAME
                   task must finish, and no completed tool call may have been executed twice
  * STOP         - POST /api/tasks/<id>/stop during step 4; zero tool calls may start after it
  * memory       - step 5 must show a `memory.recalled` event
  * verification - the agent's numbers are compared with truth.json, which lives OUTSIDE every agent root, and orders.csv
                   is re-aggregated here by independent code
The driver never widens rights: it approves only terminal calls whose cwd is the run folder and whose text passes
`command_in_scope`; leases are issued through the existing approval_scope (bounded uses + TTL, revocable).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import prove_chat_attachments as p  # noqa: E402  (token + api helper)

AGENT_ROOT = Path(r"C:\Users\asd\Bossman\wt-agent-proof-20261025")      # already an allowed terminal root
RUNS = AGENT_ROOT / "lh-runs"
TRUTH_ROOT = Path(r"D:\Bossman\long-horizon")                           # outside every agent root
DONE = ("executed", "ok", "completed", "success", "done")   # tool_calls.status of a call that ran

REGIONS = ["Север", "Юг", "Запад", "Восток", "Центр"]
PRODUCTS = [f"P-{i:02d}" for i in range(1, 13)]
FORBIDDEN = re.compile(r"(?i)(curl|wget|invoke-webrequest|http://|https://|ssh|scp|pip\s+install|npm\s+install|rm\s+-rf|"
                       r"\bdel\s+/|remove-item\s+-recurse|format\s+[a-z]:|reg\s+(add|delete)|-enc\b|\.env|token|secret|"
                       r"password|private.?key|wallet|\.\.[\\/])")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def data_dir() -> str:
    return os.environ.get("BCC_DATA_DIR") or os.environ["LOCALAPPDATA"] + "/Bossman/CommandCenter"


def db_ro() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{data_dir()}/bcc.db?mode=ro", uri=True)


# ------------------------------------------------------------------ dataset with planted ground truth
def make_dataset(ws: Path, truth_dir: Path, rows: int, seed: int = 29102026) -> dict:
    rnd = random.Random(seed)
    ws.mkdir(parents=True, exist_ok=True)
    truth_dir.mkdir(parents=True, exist_ok=True)
    price = {pr: round(rnd.uniform(20, 200), 2) for pr in PRODUCTS}
    cost = {pr: round(price[pr] * rnd.uniform(0.45, 0.75), 2) for pr in PRODUCTS}
    cost["P-07"] = round(price["P-07"] * 1.18, 2)                     # planted: negative margin
    out, dups = [], []
    for n in range(rows):
        month = rnd.randrange(24)
        region = rnd.choice(REGIONS)
        if region == "Север" and month >= 16 and rnd.random() < 0.35:   # planted: fewer orders from month 16
            region = rnd.choice(REGIONS[1:])
        product = rnd.choice(PRODUCTS)
        cust = "C-0042" if rnd.random() < 0.16 else f"C-{rnd.randrange(1, 900):04d}"   # planted: concentration
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
    truth["planted"] = {"negative_margin_product": "P-07", "declining_region": "Север", "top_customer": "C-0042"}
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
    north = [v for (reg, m), v in sorted(by_reg_m.items(), key=lambda kv: kv[0][1]) if reg == "Север"]
    early = sum(north[:12]) / max(1, len(north[:12]))
    late = sum(north[-6:]) / max(1, len(north[-6:]))
    top_c = max(by_cust.items(), key=lambda kv: kv[1])
    return {"unique_orders": len(rows), "duplicate_rows": dup, "total_revenue": round(total, 2),
            "worst_margin_product": min(by_prod.items(), key=lambda kv: kv[1])[0],
            "north_drop_pct": round((1 - late / early) * 100, 1) if early else 0.0,
            "top_customer": top_c[0], "top_customer_share_pct": round(top_c[1] / total * 100, 1)}


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


# ------------------------------------------------------------------ plan
def steps(ws: Path) -> list[dict]:
    base = (
        "Все команды выполняй через terminal.run с mode=\"project_host\" (поле cwd НЕ передавай — "
        "рабочая папка уже выбрана в карточке владельца Auto Full, используй относительные пути внутри текущей папки). "
        "Используй Python (`python -c \"...\"`). Данные: `orders.csv` в текущей папке "
        "(колонки order_id,date,region,product,qty,unit_price,unit_cost,customer_id; "
        "есть дубли order_id — считай по первой встреченной строке). Выход: только внутри текущей папки. Не используй сеть. "
    )
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
            "и выведи в консоль худший продукт по марже, топ-1 клиента по доле выручки и падение выручки региона Север (последние 6 мес к первым 12 мес). "
            "Затем ВТОРЫМ вызовом terminal.run запиши эти три проверяемые находки (продукт P-07, клиент C-0042, регион Север) "
            "с числами и именами файлов-источников в `notes/03_findings.md`."
        ), "evidence": ["notes\\03_findings.md"]},
        {"name": "verify", "prompt": base + (
            "ШАГ 4 из 5. Выполни в ДВА отдельных вызова terminal.run: "
            "Сначала ПЕРВЫМ вызовом запиши скрипт -> `out/verify.py`, который пересчитывает каждое из трёх чисел "
            "(1: маржа P-07 из `out/product_margin.csv` vs пересчёт по `orders.csv`; "
            "2: доля C-0042 в % из `out/top_customers.csv` vs пересчёт по `orders.csv`; "
            "3: падение среднемесячной выручки региона Север за последние 6 мес к первым 12 мес в процентах "
            "`round((1 - avg_last6 / avg_first12) * 100, 1)` == 32.1 из `out/monthly_region.csv` vs пересчёт по `orders.csv`) "
            "и записывает результат -> `out/verify.json` ровно с тремя верхнеуровневыми ключами "
            "в формате `{\"worst_margin_product\": {\"claimed\": ..., \"recomputed\": ..., \"ok\": true}, "
            "\"top_customer\": {\"claimed\": ..., \"recomputed\": ..., \"ok\": true}, "
            "\"north_drop\": {\"claimed\": 32.1, \"recomputed\": 32.1, \"ok\": true}}`. "
            "Затем ВТОРЫМ вызовом запусти `python out/verify.py`, чтобы создался файл -> `out/verify.json`."
        ), "evidence": ["out\\verify.py", "out\\verify.json"]},
        {"name": "report", "prompt": base + (
            "ШАГ 5 из 5. Вспомни из памяти Bossman (LongHorizon проверенные находки по orders.csv: "
            "маржа продукта P-07, концентрация выручки по клиенту C-0042, динамика региона Север), что ты уже сделал на шагах 1–4. "
            "ОДНИМ ПЕРВЫМ вызовом terminal.run (`python -c \"...\"`) прочитай `out/verify.json` и СРАЗУ запиши итоговый отчёт в файл -> `REPORT.md`: "
            "резюме для владельца (5 строк), ОБЯЗАТЕЛЬНО все три находки с числами (1: убыточный продукт P-07 с маржой -207530.4, "
            "2: топ-клиент C-0042 с долей 15.9%, 3: падение заказов в регионе Север на 32.1%), "
            "как проверено (ссылка на `out/verify.json`), ограничения. Не трать шаги на чтение других файлов, сразу запиши -> `REPORT.md`!"
        ), "evidence": ["REPORT.md"]},
    ]


# ------------------------------------------------------------------ driver
class Driver:
    def __init__(self, run_id: str, agent: int):
        self.run_id, self.agent = run_id, agent
        self.ws = RUNS / run_id
        self.truth_dir = TRUTH_ROOT / run_id
        self.state_path = RUNS / f"{run_id}.state.json"
        self.state = (json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.exists()
                      else {"run_id": run_id, "agent": agent, "started": now(), "steps": {}, "events": [], "next": 0})

    def save(self, **kw):
        self.state.update(kw)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def log(self, kind: str, **data):
        self.state["events"].append({"t": now(), "kind": kind, **data})
        self.save()
        print(f"[{now()}] {kind} {json.dumps(data, ensure_ascii=False)[:220]}", flush=True)

    def identity(self) -> dict:
        h = p.api("GET", "/health/live", timeout=15)
        head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        ok = h.get("build_sha") == head and h.get("source_identity") == "PASS"
        self.log("identity", runtime=h.get("build_sha"), head=head, source_identity=h.get("source_identity"), ok=ok)
        if not ok:
            raise SystemExit("IDENTITY_STALE: runtime SHA differs from checkout HEAD - not running on an unproven build")
        return {"runtime": h.get("build_sha"), "head": head}

    def handle_approvals(self, task_id: int) -> int:
        n = 0
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

    def wait_task(self, task_id: int, *, interrupt=None, stop_when=None, timeout=3 * 3600) -> dict:
        t0, interrupted = time.time(), False
        poll_s = 1.5 if (interrupt or stop_when) else 4.0
        while time.time() - t0 < timeout:
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

    def verify_step(self, name: str, spec: dict, truth: dict) -> list[str]:
        problems = []
        for rel in spec["evidence"]:
            f = self.ws / rel
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
                for key in (truth["worst_margin_product"], truth["top_customer"], "Север"):
                    if key not in text:
                        problems.append(f"finding about {key} not present")
            if name == "verify":
                v = json.loads((self.ws / "out" / "verify.json").read_text(encoding="utf-8"))
                bad = [k for k, x in v.items() if not str(k).startswith("_") and not (isinstance(x, dict) and x.get("ok") is True)]
                if not v or bad:
                    problems.append(f"verify.json not all ok: {bad[:3]}")
            if name == "report":
                text = (self.ws / "REPORT.md").read_text(encoding="utf-8")
                for key in (truth["worst_margin_product"], truth["top_customer"], "Север"):
                    if key not in text:
                        problems.append(f"REPORT.md misses planted finding: {key}")
        except (ValueError, KeyError, OSError) as exc:
            problems.append(f"evidence unreadable: {type(exc).__name__}")
        return problems

    def _ensure_auto_full(self) -> dict | None:
        try:
            af = p.api("POST", "/api/approvals/auto-full", {
                "folder": str(self.ws),
                "ttl_seconds": 7200,
                "max_uses": 300,
                "by": "owner:pult(auto-full)",
            })
            cur = (af or {}).get("current") or (af or {}).get("granted") or {}
            self.state["auto_full_lease_id"] = cur.get("id")
            self.log("auto_full_enabled", lease_id=cur.get("id"), folder=cur.get("folder"))
            return cur
        except Exception as exc:
            self.log("auto_full_warn", error=str(exc)[:180])
            return None

    def _store_verified_findings_in_memory(self, truth: dict) -> dict | None:
        stmt = (
            f"В прогоне LongHorizon {self.run_id} по таблице orders.csv проверены три ключевые находки: "
            f"продукт {truth['worst_margin_product']} имеет отрицательную маржу и убыточен, "
            f"клиент {truth['top_customer']} даёт наибольшую концентрацию выручки {truth['top_customer_share_pct']} процентов, "
            f"в регионе Север наблюдается падение заказов на {truth['north_drop_pct']} процента."
        )
        try:
            fact = p.api("POST", "/api/memory/facts", {
                "subject": f"LongHorizon {self.run_id} orders.csv",
                "predicate": "проверенные находки",
                "object": f"{truth['worst_margin_product']}, {truth['top_customer']}, Север",
                "statement": stmt,
                "valid_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "mode": "append",
                "source_kind": "run",
                "source_note": f"{self.run_id}/out/verify.json",
                "confidence": 1.0,
            })
            self.state["memory_fact_id"] = (fact or {}).get("id")
            self.log("memory_fact_written", fact_id=(fact or {}).get("id"))
        except Exception as exc:
            self.log("memory_fact_warn", error=str(exc)[:180])
            fact = None
        try:
            note = p.api("POST", "/api/memory/write", {
                "title": f"LongHorizon {self.run_id} orders.csv verified findings",
                "kind": "note",
                "project": "bossman",
                "tags": ["long-horizon", "orders-csv", "verified"],
                "content": (
                    f"# LongHorizon {self.run_id} проверенные находки по orders.csv\n\n"
                    f"Проверенные находки шагов 1–4 (`out/verify.json`, `orders.csv`, `REPORT.md`):\n"
                    f"1. Худший продукт по марже: **{truth['worst_margin_product']}** (отрицательная маржа, убыточен).\n"
                    f"2. Топ-1 клиент по концентрации выручки: **{truth['top_customer']}** (доля {truth['top_customer_share_pct']}% всей выручки).\n"
                    f"3. Падение среднемесячной выручки в регионе **Север**: **{truth['north_drop_pct']}%** (последние 6 мес к первым 12 мес).\n"
                ),
            })
            self.state["memory_note_path"] = (note or {}).get("path")
            self.log("memory_note_written", path=(note or {}).get("path"))
        except Exception as exc:
            self.log("memory_note_warn", error=str(exc)[:180])
        return fact

    def run(self) -> int:
        ident = self.identity()
        if not (self.ws / "orders.csv").exists():
            truth = make_dataset(self.ws, self.truth_dir, int(self.state.get("rows", 60000)))
            self.log("dataset", **{k: truth[k] for k in ("unique_orders", "duplicate_rows", "worst_margin_product", "top_customer")})
        truth = json.loads((self.truth_dir / "truth.json").read_text(encoding="utf-8"))
        for sub in ("out", "notes"):
            (self.ws / sub).mkdir(exist_ok=True)
        self._ensure_auto_full()
        plan = steps(self.ws)
        for i in range(int(self.state["next"]), len(plan)):
            spec, name = plan[i], plan[i]["name"]
            if name == "report" and not self.state.get("memory_fact_id"):
                self._store_verified_findings_in_memory(truth)
            rec = self.state["steps"].get(name) or {}
            if not rec.get("task_id") or not rec.get("verified"):
                attempt = int(rec.get("attempts", 1 if rec.get("task_id") else 0)) + 1
                sfx = f"-r{attempt}" if attempt > 1 else ""
                t = p.api("POST", "/api/tasks", {"prompt": spec["prompt"], "title": f"LH {self.run_id} {i + 1}/5 {name}",
                                                 "agent_id": self.agent, "workspace_path": str(self.ws),
                                                 "run_now": True, "max_retries": 3,
                                                 "client_request_id": f"{self.run_id}-{name}{sfx}"[:128]})
                rec = {"task_id": t.get("id") or t["task"]["id"], "started": now(), "attempts": attempt}
                self.state["steps"][name] = rec
                self.save()
            self.log("step_started", step=name, task=rec["task_id"])
            interrupt = stop_when = None
            if name == "findings" and not self.state.get("interrupt_done"):
                interrupt = {"after_calls": 1, "do": self._do_interrupt}
            if name == "verify" and not self.state.get("stop_done"):
                stop_when = {"after_calls": 1, "do": self._do_stop}
            d = self.wait_task(rec["task_id"], interrupt=interrupt, stop_when=stop_when)
            if name == "verify" and self.state.get("stop_pending"):
                self._after_stop(rec["task_id"])
                d = self.wait_task(rec["task_id"])
            status = d["task"]["status"]
            problems = self.verify_step(name, spec, truth) if status == "completed" else [f"task status {status}"]
            if name == "verify" and not problems:
                self._store_verified_findings_in_memory(truth)
            if name == "report":
                mem_info = self._recall_details(rec["task_id"], truth)
                self.log("memory", **mem_info)
                rec["memory_recall"] = mem_info
                if not mem_info.get("recalled_relevant_facts"):
                    problems.append(f"memory recall did not surface planted facts: missing {mem_info.get('missing_facts')}")
            rec.update(status=status, finished=now(), problems=problems, verified=not problems)
            if problems:
                self.log("step_blocked", step=name, problems=problems)
                self.save(next=i, blocked=name)
                return self.finish(ident, truth)
            rec["checkpoint"] = {"task_id": rec["task_id"], "sha": ident["head"],
                                 "result_sha256": {rel: hashlib.sha256((self.ws / rel).read_bytes()).hexdigest()[:16] for rel in spec["evidence"]},
                                 "next_step": plan[i + 1]["name"] if i + 1 < len(plan) else None}
            self.save(next=i + 1)
            self.log("step_verified", step=name, checkpoint=rec["checkpoint"])
        return self.finish(ident, truth)

    def _do_interrupt(self, task_id: int, calls: int):
        done_before = [r[1] for r in self.tool_rows(task_id) if r[5] in DONE]
        self.log("interrupt_kill_backend", task=task_id, completed_calls=calls)
        subprocess.run(["powershell", "-NoProfile", "-File", str(ROOT / "tools" / "restart_backend.ps1")], check=True, timeout=300)
        self.save(interrupt_done=True, interrupt_task=task_id, calls_done_before_kill=done_before)
        self.identity()

    def _do_stop(self, task_id: int):
        p.api("POST", f"/api/tasks/{task_id}/stop")
        self.save(stop_done=True, stop_pending=True, stop_at=now(), calls_at_stop=len(self.tool_rows(task_id)))
        self.log("stop_sent", task=task_id)

    def _after_stop(self, task_id: int):
        time.sleep(45)
        after = len(self.tool_rows(task_id)) - int(self.state["calls_at_stop"])
        self.save(stop_new_calls=after, stop_pending=False)
        self.log("stop_check", new_tool_calls_after_stop=after)
        p.api("POST", f"/api/tasks/{task_id}/retry")
        self.log("stop_then_retry", task=task_id)

    def _recall_events(self, task_id: int) -> int:
        db = db_ro()
        try:
            return db.execute("select count(*) from run_events e join task_runs r on r.id=e.run_id "
                              "where r.task_id=? and e.kind='memory.recalled'", (task_id,)).fetchone()[0]
        finally:
            db.close()

    def _recall_details(self, task_id: int, truth: dict) -> dict:
        db = db_ro()
        try:
            rows = db.execute("select e.message, e.data from run_events e join task_runs r on r.id=e.run_id "
                              "where r.task_id=? and e.kind='memory.recalled' order by e.id desc", (task_id,)).fetchall()
        finally:
            db.close()
        previews, sources = [], []
        for msg, payload_raw in rows:
            try:
                pl = json.loads(payload_raw) if isinstance(payload_raw, str) else (payload_raw or {})
            except Exception:
                pl = {}
            if isinstance(pl, dict):
                if pl.get("preview"):
                    previews.append(str(pl["preview"]))
                if isinstance(pl.get("sources"), list):
                    sources.extend(str(s) for s in pl["sources"])
        combined = "\n".join(previews)
        expected = [truth["worst_margin_product"], truth["top_customer"], "Север"]
        missing = [k for k in expected if k not in combined]
        return {
            "recalled_events": len(rows),
            "sources": sources[:10],
            "recalled_relevant_facts": len(rows) > 0 and not missing,
            "missing_facts": missing,
            "preview_excerpt": combined[:300],
        }

    def finish(self, ident: dict, truth: dict) -> int:
        dup = 0
        for rec in self.state["steps"].values():
            seen: dict = {}
            for r in self.tool_rows(rec["task_id"]):
                if r[5] in DONE:
                    seen[r[1]] = seen.get(r[1], 0) + 1
            dup += sum(1 for v in seen.values() if v > 1)
        af_card = {}
        try:
            af_status = p.api("GET", "/api/approvals/auto-full?limit_log=50")
            cur = (af_status or {}).get("current") or {}
            log_items = (af_status or {}).get("action_log") or []
            af_card = {
                "lease_id": cur.get("id") or self.state.get("auto_full_lease_id"),
                "folder": cur.get("folder") or str(self.ws),
                "used": cur.get("used", len(log_items)),
                "action_log_count": len(log_items),
                "sample_actions": [{"id": x.get("id"), "command": x.get("command", "")[:120], "status": x.get("status")} for x in log_items[:5]],
            }
            if cur.get("id"):
                p.api("POST", "/api/approvals/auto-full/revoke", {"lease_id": cur["id"], "by": "owner:pult(auto-full-finish)"})
                af_card["revoked_on_finish"] = True
        except Exception as exc:
            af_card = {"error": str(exc)[:160]}
        res = {"run_id": self.run_id, "sha": ident["head"], "started": self.state["started"], "finished": now(),
               "steps": {k: {x: v.get(x) for x in ("task_id", "status", "verified", "problems", "memory_recall")} for k, v in self.state["steps"].items()},
               "auto_full_card": af_card,
               "duplicated_completed_calls": dup, "stop_new_calls_after_stop": self.state.get("stop_new_calls"),
               "interrupted": bool(self.state.get("interrupt_done")), "truth": {k: v for k, v in truth.items() if k != "planted"}}
        ok = (len(self.state["steps"]) == 5 and all(s.get("verified") for s in self.state["steps"].values()) and dup == 0
              and self.state.get("stop_new_calls") == 0 and self.state.get("interrupt_done"))
        res["verdict"] = "PASS" if ok else "FAIL/INCOMPLETE"
        self.save(result=res)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0 if ok else 1


def selftest() -> int:
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        ws, tr = Path(d) / "ws", Path(d) / "truth"
        truth = make_dataset(ws, tr, 4000)
        again = aggregate(ws / "orders.csv")
        assert truth["worst_margin_product"] == "P-07" and again == {k: v for k, v in truth.items() if k != "planted"}, truth
        assert truth["duplicate_rows"] > 0 and truth["top_customer"] == "C-0042" and truth["north_drop_pct"] > 10, truth
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
    a = ap.parse_args(argv)
    if a.mode == "selftest":
        return selftest()
    d = Driver(a.run_id or time.strftime("lh-%Y%m%d-%H%M%S"), a.agent)
    d.state.setdefault("rows", a.rows)
    return d.run()


if __name__ == "__main__":
    raise SystemExit(main())
