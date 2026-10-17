"""Live proof run: the whole owner workflow on REAL public data against a running terminal, with an evidence file.

Run against a SEPARATE instance (own data dir), never the owner's: approvals here are made by this script and are labelled
"proof-script" in the evidence; they are not the owner's decisions.

    python -m warterminal --port 8831 --live --data <tmpdir>     # in another shell
    python tools/live_proof.py --base http://127.0.0.1:8831 --data <tmpdir> --out proof.json
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8831")
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    tok = json.loads((Path(args.data) / "tokens.json").read_text(encoding="utf-8"))
    steps: list[dict] = []

    def call(method, path, body=None, role="analyst", expect=200):
        req = urllib.request.Request(args.base + "/api/v1" + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {tok[role]}", "Content-Type": "application/json"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                code, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            code, raw = e.code, e.read()
        try:
            data = json.loads(raw)
        except ValueError:
            data = raw.decode("utf-8", "replace")[:500]
        return code, data, round(time.time() - t0, 2), code == expect

    def step(name, method, path, body=None, role="analyst", expect=200, keep=None):
        code, data, dt, ok = call(method, path, body, role, expect)
        ev = keep(data) if (keep and isinstance(data, (dict, list)) and ok) else (data if not ok else None)
        steps.append({"step": name, "ok": ok, "http": code, "expected": expect, "seconds": dt, "role": role, "evidence": ev,
                      "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        print(("PASS " if ok else "FAIL ") + f"{name} [{code}] {dt}s", flush=True)
        return data

    step("health", "GET", "/health", role="viewer", keep=lambda d: d)
    step("TED Чехия (топливо/еда/логистика, 30 дней)", "POST", "/ingest", {"source": "ted", "country": "CZE", "days": 30, "limit": 200},
         keep=lambda d: {k: d[k] for k in ("fetched", "new", "total_available", "snapshot_sha256")})
    step("TED весь мир (14 дней)", "POST", "/ingest", {"source": "ted", "days": 14, "limit": 200}, keep=lambda d: {k: d[k] for k in ("fetched", "new", "total_available")})
    step("World Bank весь мир", "POST", "/ingest", {"source": "worldbank", "limit": 100}, keep=lambda d: {k: d[k] for k in ("fetched", "new")})
    step("Санкции OFAC + ООН", "POST", "/sanctions/fetch", {"sources": ["ofac", "un"]}, role="compliance",
         keep=lambda d: {k: d[k] for k in ("stored", "by_source", "list_version", "as_of")})
    step("Курсы ЧНБ", "POST", "/fx/fetch", {}, keep=lambda d: d)
    step("Конфликты UCDP", "POST", "/conflicts/fetch", {}, role="compliance", keep=lambda d: d)
    step("Профиль компании -> запрос одобрения", "PATCH", "/company/profile",
         {"changes": {"name": "PROOF COMPANY s.r.o. (тестовый профиль)", "country": "CZ", "registered": True, "cpv_capabilities": ["09", "15", "60", "63"],
                      "capital_limit_usd": 50000, "min_prep_days": 7}}, keep=lambda d: d)
    pending = call("GET", "/approvals?status=pending", role="viewer")[1]["items"]
    step("Одобрение профиля (proof-script, не владелец)", "POST", f"/approvals/{pending[0]['id']}/decide", {"approve": True, "note": "proof-script"}, role="owner",
         keep=lambda d: {"applied": d["applied"]})
    step("Поставщик CZ (проверка по санкциям)", "POST", "/suppliers", {"name": "Proof Fuel Supplier s.r.o.", "country": "CZ", "cpv_prefixes": ["09", "15"], "confirmed": True},
         keep=lambda d: {"screening": d["screening"]})
    step("Поставщик из запрещённой юрисдикции -> отказ", "POST", "/suppliers", {"name": "Any Trader", "country": "IR"}, expect=422)
    lst = call("GET", "/tenders?include_demo=false&status=open&country=CZE", role="viewer")[1]
    top = lst["items"][0] if lst["items"] else None
    steps.append({"step": "Открытые тендеры Чехии", "ok": bool(top), "evidence": {"open_cz": lst["total"], "top": top and {k: top[k] for k in
                  ("key", "title", "estimated_value", "currency", "deadline", "source_url")}}})
    if top:
        a = step("Оценка тендера (сохранить)", "POST", "/assessments", {"tender_key": top["key"], "dry_run": False, "use_ai": True},
                 keep=lambda d: {"complexity": d["complexity"], "tier_if_cleared": d["tier_if_cleared"], "fit": d["fit"]["fit_score"],
                                 "confidence": d["fit"]["confidence"], "usd_value": d["usd_value"], "ai": d.get("ai"), "ai_disagreement": d.get("ai_disagreement"),
                                 "gates": {g["id"]: g["status"] for g in d["gates"]}})
        if isinstance(a, dict) and "id" in a:
            step("Подпись проверки документов (compliance)", "POST", f"/assessments/{a['id']}/review",
                 {"gate_id": "documents", "decision": "cleared", "note": "proof-script: документация прочитана (имитация)"}, role="compliance", keep=lambda d: d)
        step("Чек-лист заявки (черновик)", "GET", f"/tenders/{top['key']}/checklist", role="viewer", keep=lambda d: {"items": len(d["items"]), "draft": d["draft"]})
        step("Сделка Чехия <-> заказчик (состыковка)", "POST", "/deals",
             {"title": "Proof: заказ + поставщик", "tender_key": top["key"], "buyer_country": "DE", "goods": "diesel", "order_confirmed": True,
              "supplier": {"name": "Proof Fuel Supplier s.r.o.", "country": "CZ", "confirmed": True}, "supplier_contract_signed": True},
             keep=lambda d: {"matched": d["matched"], "screening": d["screening"]["screening"]})
    step("Финансы: landed cost, cashflow, стресс 30/60/90, лимит 50k", "POST", "/finance/estimate",
         {"currency": "CZK", "fx": {"USDCZK": 21.622}, "purchase": 700000, "freight": 40000, "duties_pct": 0, "bid_price": 820000,
          "supplier_prepay_pct": 30, "delivery_day": 20, "acceptance_day": 25, "customer_pay_delay_days": 30, "order_confirmed": True, "supplier_contract_signed": True},
         keep=lambda d: {"landed": d["landed"]["landed_cost"], "margin_pct": d["metrics"]["margin_pct"], "markup_pct": d["metrics"]["markup_pct"],
                         "peak_usd": d["peak_funding_need_usd"], "capital": d["capital"]["status"], "stress_profit": [s["profit"] for s in d["stress"]]})
    step("Подача заявки -> запрещено", "POST", "/actions/submit-bid", role="owner", expect=403)
    step("Аудит: хеш-цепочка цела", "GET", "/audit/verify", role="compliance", keep=lambda d: d)
    step("Источники: статусы", "GET", "/sources/status", role="viewer",
         keep=lambda d: {s["id"]: s["status"] for s in d["sources"]})
    out = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "base": args.base, "passed": sum(s["ok"] for s in steps), "total": len(steps),
           "note": "одобрения сделаны скриптом в отдельном экземпляре (proof-script), это не решения владельца", "steps": steps}
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"PASSED {out['passed']}/{out['total']} -> {args.out}")
    return 0 if out["passed"] == out["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
