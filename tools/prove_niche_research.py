"""Proof for niche_research (owner spec 24.10: 28-day commercial niche research run).

    python tools/prove_niche_research.py [--out docs/audits/2026-10-24-niche-research] [--skip-real]

Part 1 — ONE simulated day end-to-end: temp data dir, a local HTTP site on 127.0.0.1 with synthetic business
pages, fake search, fake model. `start` + one `tick` must produce reports/day-01.md (with «платёжный спрос не
проверен»), samples/day-01-sample.md, proposals/day-01-proposals.md, learning records, $0 spend.

Part 2 — REAL tiny smoke: one call through the production router (local Ollama only if a model is ALREADY loaded,
else the free cloud: OpenRouter :free / Gemini Flash preview; paid limit stays 0) asking for a one-word answer, and
one polite fetch of https://example.com (robots.txt checked, BossmanNicheResearch UA, >= 5 s per host).

Receipt JSON with verdict and spend (must be $0). No key is printed or written; nothing is sent or published.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from niche_research import PAYMENT_DISCLAIMER  # noqa: E402
from niche_research.config import load_config  # noqa: E402
from niche_research.models import FakeModel, ModelError, ModelRouter, build_default_clients  # noqa: E402
from niche_research.orchestrator import Orchestrator  # noqa: E402
from niche_research.store import Store  # noqa: E402
from niche_research.web import PoliteFetcher, ResearchLog, SearchHit, SearchResult  # noqa: E402

SITE = {
    "/robots.txt": ("text/plain", "User-agent: *\nDisallow: /admin\n"),
    "/a": ("text/html; charset=utf-8", "<title>Клининг А</title><p>Генеральная уборка квартиры от 4500 рублей</p>"
           "<p>Клиенты спрашивают про уборку после ремонта и сроки выезда</p>"),
    "/b": ("text/html; charset=utf-8", "<title>Клининг Б</title><p>Поддерживающая уборка от 2900 рублей</p>"
           "<p>Отзыв: долго отвечали на вопрос о стоимости уборки</p>"),
    "/c": ("text/html; charset=utf-8", "<title>Клининг В</title><p>Мойка окон и уборка офисов по договору</p>"
           "<p>Запись на уборку через форму, ответ в течение часа</p>"),
}


class _H(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        ctype, body = SITE.get(self.path, ("text/plain", "not found"))
        data = body.encode("utf-8")
        self.send_response(200 if self.path in SITE else 404)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def _fake_responder(system: str, prompt: str) -> str:
    import re
    role = re.search(r"РОЛЬ: (\w+)", prompt).group(1)
    src = [(int(m.group(1)), m.group(2).strip()) for m in re.finditer(r"^\[(\d+)\] \S+ .*?\n(.+)$", prompt, re.M)]
    if role == "researcher":
        return json.dumps({"observations": [{"source_id": i, "quote": q[:70], "fact": "наблюдение"} for i, q in src],
                           "conclusions": ["владельцы тратят время на ответы о цене"],
                           "assumptions": ["часть заявок теряется"], "unknowns": ["объём рынка"]}, ensure_ascii=False)
    if role == "competitor":
        return json.dumps({"offers": [{"source_id": i, "name": f"фирма {i}", "offer": "уборка", "price_text":
                                       "неизвестно", "quote": q[:50]} for i, q in src]}, ensure_ascii=False)
    if role == "packager":
        return json.dumps({"offer": {"for_whom": "малые клининговые фирмы", "routine_task": "ответы о цене",
                                     "deliverable": "шаблоны ответов", "timeframe": "3 дня",
                                     "price_hypothesis": "неизвестно"},
                           "scores": {"problem_visibility": 3, "buyer_accessibility": 3, "competition": 2,
                                      "output_quality": 4, "expected_margin": 3}}, ensure_ascii=False)
    if role == "critic":
        return json.dumps({"unsupported_claims": [], "weak_points": ["синтетические источники"],
                           "reasons_wont_pay": ["делают сами", "нет бюджета", "не доверяют"],
                           "adjusted_scores": {"problem_visibility": 3, "buyer_accessibility": 2, "competition": 2,
                                               "output_quality": 4, "expected_margin": 2}}, ensure_ascii=False)
    return "# FAQ\n\nСколько стоит уборка? От 2900 рублей. Срок выезда: неизвестно."


class _Search:
    def __init__(self, base):
        self.base = base

    def search(self, query, task_id=None):
        return SearchResult(query, [SearchHit(p, self.base + p) for p in ("/a", "/b", "/c")])


def simulated_day() -> dict:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    clock = {"t": 1793005200.0}  # Monday 2026-10-26 09:00 UTC -> day 1 = «уборка»

    def now():
        return clock["t"]

    def sleep(s):
        clock["t"] += s

    try:
        with tempfile.TemporaryDirectory(prefix="niche-proof-") as tmp:
            Path(tmp, "config.json").write_text(json.dumps({"tz_offset_hours": 0, "allow_private_hosts": True,
                                                            "max_tasks_per_tick": 10}), encoding="utf-8")
            cfg = load_config(Path(tmp))
            store = Store(cfg.db_path)
            router = ModelRouter(store, cfg, [FakeModel(_fake_responder)], now)
            orch = Orchestrator(cfg, store=store, router=router, search=_Search(base), clock=now, sleeper=sleep)
            started = orch.start()
            clock["t"] += 3600
            tick = orch.tick()
            run = cfg.run_dir
            day = (run / "reports" / "day-01.md").read_text(encoding="utf-8")
            sample = run / "samples" / "day-01-sample.md"
            prop = run / "proposals" / "day-01-proposals.md"
            learning = (run / "research" / "learning.jsonl").read_text(encoding="utf-8").splitlines()
            checks = {
                "started": bool(started.get("ok")),
                "all_5_roles_done": [p["outcome"] for p in tick["processed"]] == ["done"] * 5,
                "day_report_exists": True,
                "day_report_has_disclaimer": PAYMENT_DISCLAIMER in day,
                "sample_exists": sample.exists(),
                "proposal_exists": prop.exists(),
                "learning_records": len(learning),
                "spend_usd": store.total_spend(),
                "paid_spend_usd": store.paid_spend(),
            }
            ok = (checks["started"] and checks["all_5_roles_done"] and checks["day_report_has_disclaimer"]
                  and checks["sample_exists"] and checks["proposal_exists"] and checks["learning_records"] > 0
                  and checks["spend_usd"] == 0)
            store.close()
            return {"ok": ok, "checks": checks, "processed": tick["processed"],
                    "day_report_head": day.splitlines()[:6]}
    finally:
        srv.shutdown()
        srv.server_close()


def real_smoke() -> dict:
    out: dict = {}
    with tempfile.TemporaryDirectory(prefix="niche-smoke-") as tmp:
        cfg = load_config(Path(tmp))  # defaults: paid_usd_limit = 0
        store = Store(cfg.db_path)
        clients = build_default_clients(cfg)
        out["paid_clients_configured"] = sum(1 for c in clients if c.paid)
        router = ModelRouter(store, cfg, clients)
        t0 = time.time()
        try:
            r = router.complete("Отвечай одним словом.", "Столица Франции? Одно слово.", role="smoke")
            out["model"] = {"ok": True, "provider": r.provider, "model": r.model, "answer": r.text.strip()[:80],
                            "tokens_in": r.tokens_in, "tokens_out": r.tokens_out, "cost_usd": r.cost_usd,
                            "seconds": round(time.time() - t0, 1)}
        except ModelError as exc:
            out["model"] = {"ok": False, "error": str(exc)[:300], "seconds": round(time.time() - t0, 1)}
        out["model_attempts"] = [{"provider": x["provider"], "model": x["model"], "ok": bool(x["ok"]),
                                  "note": (x["note"] or "")[:120]} for x in store.spend_rows()]
        fetcher = PoliteFetcher(store, ResearchLog(store, cfg.run_dir))
        t0 = time.time()
        page = fetcher.fetch("https://example.com/", task_id=None)
        out["fetch"] = {"ok": page.ok, "status": page.status, "title": page.title, "error": page.error,
                        "requests": fetcher.requests, "seconds": round(time.time() - t0, 1),
                        "rate_limited_ge_5s": (time.time() - t0) >= 5.0 if len(fetcher.requests) >= 2 else None}
        out["spend_usd"] = store.total_spend()
        out["paid_spend_usd"] = store.paid_spend()
        store.close()
    out["ok"] = bool(out["model"].get("ok") and out["fetch"]["ok"] and out["spend_usd"] == 0)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docs" / "audits" / "2026-10-24-niche-research"))
    ap.add_argument("--skip-real", action="store_true")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except OSError:
        sha = ""
    sim = simulated_day()
    real = {"skipped": True, "ok": False} if args.skip_real else real_smoke()
    spend = sim["checks"]["spend_usd"] + real.get("spend_usd", 0.0)
    verdict = "PASS" if sim["ok"] and real.get("ok") and spend == 0 else ("PARTIAL" if sim["ok"] else "FAIL")
    receipt = {
        "proof": "niche_research 28-day run infrastructure",
        "date": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "base_sha": sha,
        "note": "working tree uncommitted at proof time; scheduled task NOT registered; nothing sent/published",
        "simulated_day": sim,
        "real_smoke": real,
        "spend_usd_total": spend,
        "verdict": verdict,
        "payment_status": PAYMENT_DISCLAIMER,
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"verdict": verdict, "spend_usd_total": spend, "receipt": str(out / "receipt.json"),
                      "real_model": real.get("model"), "real_fetch": real.get("fetch")}, ensure_ascii=False, indent=1))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
