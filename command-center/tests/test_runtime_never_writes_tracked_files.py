"""Runtime writers must not touch tracked repo files: a dirty checkout makes /health lose build identity (02.11.2026)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WRITERS = {
    "command-center/bcc/features/web_trend_teacher.py": "latest_dir = r_dir",
    "command-center/bcc/features/antigravity_skills_audit.py": "default_receipt_path(db_or_svc)",
}


def test_known_runtime_writers_use_the_data_dir():
    for rel, marker in WRITERS.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert marker in text, rel
        assert not re.search(r'_repo_root\(\)\s*/\s*"(data|docs)"', text), f"{rel} writes into the repo again"
