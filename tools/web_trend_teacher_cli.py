"""CLI для запуска аудита и боевого прогона «Учитель Интернет-Трендов Bossman» (`web_trend_teacher`).

Запускает полный цикл:
1. Аудит готовности веб-поиска, памяти И-1 и защитных контуров (`audit_web_learning_readiness`).
2. Боевой выход в интернет по HTTPS (`hn-algolia`, `openalex`, `pypi`) по 4 доменам студии.
3. Построение карточек уроков Учителя, проверка контрольного экзамена усвоения (`mastery_score >= 0.85`).
4. Проверка CUA-Recovery (`StepStateGuard`), AgentProofBench (`verify_criterion_with_proof`) и
   запись самодостаточных фактов в темпоральную память `FactStore` (`TimlMemBench`).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CC_ROOT = REPO_ROOT / "command-center"
for p in (str(REPO_ROOT), str(CC_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from bcc.api import create_app  # noqa: E402
from bcc.config import Settings  # noqa: E402
from bcc.features.web_trend_teacher import (  # noqa: E402
    RULE_VERSION,
    audit_web_learning_readiness,
    recall_learned_trends,
    run_web_trend_teacher_session,
)


async def run_full_audit_and_live_session(*, allow_live_network: bool = True) -> dict:
    with tempfile.TemporaryDirectory(prefix="bossman_trend_teacher_") as tmp:
        tmp_path = Path(tmp)
        data_dir = tmp_path / "data"
        settings = Settings(
            data_dir=data_dir,
            database_url=f"sqlite+aiosqlite:///{data_dir / 'bcc.db'}",
            ui_dir=tmp_path / "no-ui",
        )
        app = create_app(settings, announce_token=False, start_workers=False)
        svc = app.state.svc
        await svc.start()
        try:
            audit = audit_web_learning_readiness()
            session = await run_web_trend_teacher_session(
                svc,
                note="Боевой прогон Учителя интернет-трендов по таймеру (1ч)",
                allow_live_network=allow_live_network,
                scope="work",
            )
            recalled = await recall_learned_trends(svc, caller_scope="work")
        finally:
            await svc.stop()

        return {
            "schema": "bossman.web_trend_teacher.live_run_receipt.v1",
            "rule_version": RULE_VERSION,
            "verdict": "VERIFIED" if session["verifier_status"] == "VERIFIED" else "FAILED",
            "audit": audit,
            "session": {
                "session_id": session["session_id"],
                "session_date": session["session_date"],
                "status": session["status"],
                "verifier_status": session["verifier_status"],
                "proof_sha256": session["proof_sha256"],
                "average_mastery_score": session["average_mastery_score"],
                "live_signals_fetched": session["live_signals_fetched"],
                "lessons_count": session["lessons_count"],
                "facts_written_count": len(session["memory_facts_written"]),
                "cua_recovery": session["cua_recovery"],
                "lessons": session["lessons"],
            },
            "recalled_from_memory": {
                "active_trend_facts_count": recalled["active_trend_facts_count"],
                "sample_facts": recalled["active_trend_facts"][:4],
            },
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Bossman Web Trend Teacher CLI")
    parser.add_argument("--offline", action="store_true", help="Run without live internet requests")
    parser.add_argument("--write-receipt", type=str, default="", help="Path to write JSON receipt")
    args = parser.parse_args()

    res = asyncio.run(run_full_audit_and_live_session(allow_live_network=not args.offline))
    if args.write_receipt:
        out_p = Path(args.write_receipt).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(res, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
    return 0 if res["verdict"] == "VERIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
