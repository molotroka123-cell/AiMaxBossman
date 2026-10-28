"""CLD-UP-1 (audit 28.10): the linter that keeps cloud LLM calls inside the governed perimeter must really fail.

The real repository passes; a planted direct call, a missing critical module and a marker that exists only in a comment
all fail; an allow-listed module is not flagged. Synthetic temp trees only.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINTER = ROOT / "tools" / "check_no_direct_llm_http.py"
CRITICAL = ("command-center/bcc/economy_orchestrator.py", "command-center/bcc/fable_cap.py", "command-center/bcc/features/studio.py")
GOVERNED = "from bcc.provider_governance import GovernedAdapter\n"


def run(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(LINTER), "--root", str(root)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)


def tree(tmp: Path, *, critical_body: str = GOVERNED, skip: str | None = None, extra: dict[str, str] | None = None) -> Path:
    for rel in CRITICAL:
        if rel == skip:
            continue
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(critical_body, encoding="utf-8")
    for rel, body in (extra or {}).items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return tmp


def test_the_real_repository_passes():
    r = run(ROOT)
    assert r.returncode == 0, r.stdout[-800:]


def test_a_planted_direct_call_fails(tmp_path):
    t = tree(tmp_path, extra={"command-center/bcc/features/sneaky.py":
                              'import httpx\nURL = "https://api.openai.com/v1/chat/completions"\n'})
    r = run(t)
    assert r.returncode == 1 and "sneaky.py" in r.stdout


def test_an_allow_listed_module_is_not_flagged(tmp_path):
    t = tree(tmp_path, extra={"command-center/bcc/providers.py": 'URL = "https://api.anthropic.com/v1/messages"\n'})
    assert run(t).returncode == 0


def test_a_missing_critical_module_fails_instead_of_passing_vacuously(tmp_path):
    t = tree(tmp_path, skip="command-center/bcc/features/studio.py")
    r = run(t)
    assert r.returncode == 1 and "not found" in r.stdout


def test_a_marker_that_lives_only_in_a_comment_does_not_count(tmp_path):
    t = tree(tmp_path, critical_body="# TODO: wrap with GovernedAdapter someday\nimport httpx\n")
    r = run(t)
    assert r.returncode == 1 and "missing SpendFirewall" in r.stdout
