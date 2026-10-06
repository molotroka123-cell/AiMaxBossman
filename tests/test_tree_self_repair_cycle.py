"""The self-repair cycle scores stages separately and never promotes a partial or failed fix (owner, 06.10.2026)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "command-center"))
sys.path.insert(0, str(ROOT / "bossman-core"))
sys.path.insert(0, str(ROOT))

import tree_self_repair_cycle as cyc  # noqa: E402
from bcc.features.coding_recipes import validate_recipe  # noqa: E402
from tree_holdout import discovery_nonfinite, goal_budget_nonfinite  # noqa: E402

CASE = cyc.CASES["discovery"]
DIFF = ("diff --git a/command-center/bcc/pit/discovery.py b/command-center/bcc/pit/discovery.py\n"
        "--- a/command-center/bcc/pit/discovery.py\n+++ b/command-center/bcc/pit/discovery.py\n"
        "@@ -1,1 +1,2 @@\n+import math\n")


def rec(**kw):
    base = {"id": "t1", "status": "completed", "worker": "openrouter-free", "model": "m:free", "diff": DIFF,
            "changed_files": [CASE["target"], "command-center/tests/test_pit_foundation.py"],
            "verification": {"ran": True, "passed": True}, "verify_tests": ["command-center/tests/test_discovery.py"],
            "sidecar": {"summary": "guarded every numeric field"}, "memory": {"recipe_ids": []}}
    base.update(kw)
    return base


def check(before_failed=10, after_failed=0):
    def res(failed):
        return {"total": 72, "failed": failed, "passed": failed == 0,
                "failures": [{"case": "relevance=nan/bad_first"}] if failed else [],
                "cases": [{"case": "valid/best_wins/abc", "ok": True}]}
    return {"base_commit": "a" * 40, "before": res(before_failed), "after": res(after_failed)}


def test_full_fix_is_independently_verified():
    st = cyc.stages(rec(), check(), CASE, None, transfer=False)
    assert st["DEFECT_REPRODUCED"] and st["MODEL_PATCH_CREATED"] and st["INDEPENDENT_VERIFICATION_PASS"]
    assert not st["EXPERIENCE_AUTO_SAVED"]


def test_partial_fix_stays_partial_even_if_bossmans_own_tests_pass():
    st = cyc.stages(rec(), check(after_failed=32), CASE, None, transfer=False)
    assert st["BOSSMAN_ZONE_CHECK_PASS"] and not st["HOLDOUT_PASS_ON_PATCH"]
    assert not st["INDEPENDENT_VERIFICATION_PASS"]


def test_no_reproduction_no_verification():
    st = cyc.stages(rec(), check(before_failed=0), CASE, None, transfer=False)
    assert not st["DEFECT_REPRODUCED"] and not st["INDEPENDENT_VERIFICATION_PASS"]


def test_a_failed_bossman_check_is_not_rescued_by_the_holdout():
    st = cyc.stages(rec(verification={"ran": True, "passed": False}), check(), CASE, None, transfer=False)
    assert not st["INDEPENDENT_VERIFICATION_PASS"]


def test_transfer_needs_a_recalled_recipe():
    st = cyc.stages(rec(), check(), CASE, {"lesson_id": "coach-lesson:x"}, transfer=True)
    assert st["INDEPENDENT_VERIFICATION_PASS"] and not st["RECIPE_RECALLED"] and not st["TRANSFER_PASS"]
    st = cyc.stages(rec(memory={"recipe_ids": ["tree-selfrepair-t0"]}), check(), CASE, {"lesson_id": "x"}, transfer=True)
    assert st["RECIPE_RECALLED"] and st["TRANSFER_PASS"]


def test_scope_violation_is_reported():
    st = cyc.stages(rec(changed_files=[CASE["target"], "command-center/tests/run_test.py"]), check(), CASE, None, False)
    assert st["SCOPE_RESPECTED"] is False


def test_the_auto_recipe_passes_the_store_grammar_and_cites_the_worker():
    recipe, evidence, verifier = cyc.build_recipe(rec(), CASE, check())
    assert validate_recipe(recipe) == []
    assert recipe["provenance"]["source"] == "student" and "openrouter-free" in recipe["provenance"]["who"]
    assert "guarded every numeric field" in recipe["action"] and "import math" in recipe["action"]
    assert evidence["actual"] == "PASS" and verifier["independence_class"] == "external_tool"


def test_a_failed_holdout_cannot_produce_passing_evidence():
    _, evidence, _ = cyc.build_recipe(rec(), CASE, check(after_failed=3))
    assert evidence["actual"] == "FAIL"           # the store refuses actual != expected


def test_holdouts_run_and_keep_valid_behaviour_on_this_checkout():
    disc = discovery_nonfinite.run(ROOT)
    goal = goal_budget_nonfinite.run(ROOT)
    assert disc["total"] >= 60 and goal["total"] >= 9
    assert all(c["ok"] for c in disc["cases"] if c["case"].startswith("valid/"))
    assert all(c["ok"] for c in goal["cases"] if c["case"].startswith("valid:"))


def test_paid_worker_needs_the_explicit_owner_flag(monkeypatch, capsys):
    # without the flag glm-flash is refused (the $0 rule); the flag lifts it for that exact worker only
    assert cyc.main(["--worker", "glm-flash", "--evidence", "x"]) == 2
    assert "SELF_REPAIR=REFUSED" in capsys.readouterr().out
    assert cyc.main(["--worker", "nvidia-nim", "--allow-paid-worker", "glm-flash", "--evidence", "x"]) == 2
    assert cyc.OWNER_APPROVED_PAID == {"glm-flash": "z-ai/glm-5.3-flash"}
