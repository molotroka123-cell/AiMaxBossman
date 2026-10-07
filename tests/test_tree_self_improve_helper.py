"""tools/tree_self_improve.py: the morning one-command for the first proven self-improvement. Honest verdicts, $0 workers only."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("tree_self_improve", ROOT / "tools" / "tree_self_improve.py")
tsi = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(tsi)  # type: ignore[union-attr]


def test_only_a_verified_completed_candidate_counts():
    base = {"status": "completed", "changed_files": ["command-center/bcc/pit/discovery.py"]}
    assert tsi.verdict({**base, "earned": "verified"})[0] == "VERIFIED_CANDIDATE"
    # bad cases: each of these must NOT read as proof
    assert tsi.verdict({**base, "earned": "unverified"})[0] == "UNVERIFIED_CANDIDATE"
    assert tsi.verdict({**base, "earned": None})[0] == "CHECK_FAILED"
    assert tsi.verdict({"status": "completed", "changed_files": [], "earned": None})[0] == "NO_DEFECT_FOUND"
    assert tsi.verdict({"status": "failed", "changed_files": [], "earned": None})[0] == "FAILED"


def test_paid_or_unknown_workers_are_refused_and_free_ones_pass():
    assert tsi.refuse_worker("nemotron-ultra-free") is None and tsi.refuse_worker("openrouter-free") is None
    # владелец 07.10: NVIDIA NIM (build.nvidia.com, бесплатный доступ разработчика) допустим наравне с OpenRouter :free
    assert tsi.refuse_worker("nvidia-nim") is None
    for paid in ("glm-flash", "claude", ""):
        assert tsi.refuse_worker(paid), paid
    assert tsi.main(["--worker", "glm-flash"]) == 2          # refused before any network call


def test_free_worker_ids_exist_in_the_coding_allowlist_and_the_node_is_the_discovery_leaf():
    text = (ROOT / "command-center" / "bcc" / "features" / "coding_tasks.py").read_text(encoding="utf-8")
    for worker in tsi.FREE_WORKERS:
        assert worker == "local" or f'"{worker}"' in text, worker
    seed = json.loads((ROOT / "command-center" / "bcc" / "capability_tree_seed.json").read_text(encoding="utf-8"))
    node = next(n for n in seed["nodes"] if n["id"] == tsi.NODE_ID)
    assert any(s.get("path") == "command-center/bcc/pit/discovery.py" for s in node.get("sources") or [])


def test_the_wish_pins_the_scope_to_one_source_file_and_one_existing_test_file():
    """Owner-PC run 06.10: the free worker changed discovery.py but also created 4 extra test files and touched unrelated
    tests, so the independent check failed. The wish must now name the only two files and forbid new ones."""
    wish = tsi.WISH
    assert "command-center/bcc/pit/discovery.py" in wish and "command-center/tests/test_pit_foundation.py" in wish
    assert "новых файлов не создавай" in wish
    for rel in ("command-center/bcc/pit/discovery.py", "command-center/tests/test_pit_foundation.py"):
        assert (ROOT / rel).is_file(), rel                 # a wish naming a missing file would send the worker astray
    assert "choose_discovery_question" in (ROOT / "command-center/tests/test_pit_foundation.py").read_text(encoding="utf-8")
