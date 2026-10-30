"""Runner for AiMaxBossman Weekly Research Digest (2026-10-30).

Executes all 4 benchmarks in priority order and writes a signed receipt to:
  docs/audits/2026-10-30-weekly-research-benchmarks/receipt.json
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CC_DIR = ROOT / "command-center"
if str(CC_DIR) not in sys.path:
    sys.path.insert(0, str(CC_DIR))

from bcc.config import Settings
from bcc.db import Database
from bcc.features.research_weekly_20261030 import run_all_weekly_research_checks


class _MiniServices:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db


async def _main() -> int:
    with tempfile.TemporaryDirectory(prefix="bossman_weekly_20261030_") as tmp:
        tmp_path = Path(tmp)
        settings = Settings(data_dir=tmp_path / "data")
        settings.ensure_dirs()
        db = Database(settings.database_url)
        await db.create_all()
        try:
            svc = _MiniServices(settings=settings, db=db)
            report = await run_all_weekly_research_checks(svc, work_dir=tmp_path / "work")
        finally:
            await db.close()

    out_dir = ROOT / "docs" / "audits" / "2026-10-30-weekly-research-benchmarks"
    out_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = out_dir / "receipt.json"
    receipt_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"Receipt saved to: {receipt_path}")
    print(f"all_targets_met = {report['all_targets_met']}")
    print(f"receipt_sha256  = {report['receipt_sha256']}")
    for key, val in report["results"].items():
        print(f"  [{key}] passed_target={val['passed_target']} elapsed_ms={val['elapsed_ms']}")
    return 0 if report["all_targets_met"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
