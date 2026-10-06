"""tools/leaf_usefulness_queue.py: the queue is a transparent hypothesis; it never claims benefit."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("leaf_usefulness_queue", ROOT / "tools" / "leaf_usefulness_queue.py")
luq = importlib.util.module_from_spec(spec)
sys.modules["leaf_usefulness_queue"] = luq
spec.loader.exec_module(luq)

LEAF = {"id": "x", "label": "X", "zone": "jeff", "source": "a.py", "tests_state": "PASSED_RECORDED_RUN", "proven_through": "tests"}


def test_other_modules_importing_a_leaf_raise_its_priority_up_to_a_cap():
    assert luq.score(LEAF, 0, 100, set())["score"] == 3.0
    assert luq.score(LEAF, 3, 100, set())["score"] == 4.0
    assert luq.score(LEAF, 300, 100, set())["score"] == 6.0          # the «needed by others» bonus is capped at 3


def test_size_and_heavy_dependencies_cost_priority():
    assert luq.score(LEAF, 0, 700, set())["score"] == 2.0
    assert luq.score(LEAF, 0, 700, {"torch"})["score"] == 1.0
    assert luq.score(LEAF, 0, 2000, {"cv2"})["cost_penalty"] == 2.5


def test_a_leaf_without_a_test_is_told_to_get_one_first_whatever_its_score():
    s = luq.score({**LEAF, "tests_state": "NO_TEST", "proven_through": "code"}, 10, 100, set())
    assert s["first_step"] == "сначала тест" and s["regression_risk"] == "высокий"
    assert luq.score(LEAF, 0, 100, set())["first_step"] == "измерить до/после"


def test_dependants_counts_distinct_importing_files_not_the_file_itself():
    graph = {"command-center/bcc/x.py": {"bcc.x"}, "command-center/bcc/a.py": {"bcc.x", "bcc.x.sub"}, "command-center/bcc/b.py": {"os"}, "command-center/bcc/d.py": {"bcc.xray"}}
    assert luq.dependants("command-center/bcc/x.py", graph) == 1      # only a.py: not itself, not the look-alike bcc.xray
    assert luq.dependants("docs/readme.md", graph) == 0


def test_the_markdown_states_it_is_a_hypothesis_and_names_no_benefit():
    q = {"leaves_ranked": 1, "top": [{"id": "x", "label": "X", "zone": "jeff", "score": 3.0, "needed_by_modules": 0, "loc": 10, "heavy_deps": [],
                                       "regression_risk": "низкий", "first_step": "измерить до/после"}]}
    md = luq.to_markdown(q)
    assert "ГИПОТЕЗА" in md and "Польза подтверждается только сравнением до/после" in md
    assert "| 1 | `x`" in md
