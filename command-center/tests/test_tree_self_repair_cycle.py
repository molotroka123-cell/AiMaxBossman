"""The self-repair cycle scores stages separately and never promotes a partial or failed fix (owner, 06.10.2026)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
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
    assert cyc.main(["--worker", "some-other-paid", "--allow-paid-worker", "glm-flash", "--evidence", "x"]) == 2
    # owner 07.10: Haiku 5.5 approved next to GLM Flash; anything else stays refused
    assert cyc.OWNER_APPROVED_PAID == {"glm-flash": "z-ai/glm-5.3-flash", "haiku-5.5": "anthropic/claude-haiku-5.5"}


# ---- case `atomic-json` (audit 07.10): wiring + holdout behave on a known-good and a known-bad implementation ----
import json  # noqa: E402
import os  # noqa: E402
import time  # noqa: E402
import types  # noqa: E402
import uuid  # noqa: E402

from tree_holdout import atomic_json_replace  # noqa: E402

ATOMIC = cyc.CASES["atomic-json"]
DEFECT_PREFIXES = ("reader holds", "8 concurrent", "4 writers")


def _impl(retry: bool):
    def atomic_json(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            for attempt in range(200 if retry else 1):
                try:
                    os.replace(tmp, path)
                    return
                except PermissionError:
                    if attempt == 199 or not retry:
                        raise
                    time.sleep(0.02)
        finally:
            tmp.unlink(missing_ok=True)
    return types.SimpleNamespace(atomic_json=atomic_json)


def test_atomic_case_is_wired_to_an_existing_holdout_and_a_scoped_task():
    assert (ROOT / ATOMIC["holdout"]).is_file() and ATOMIC["mode"] == "task"
    assert ATOMIC["target"] in ATOMIC["allowed"] and all(p in ATOMIC["allowed"] for p in ATOMIC["verify_tests"])
    assert "os.replace" not in ATOMIC["wish"].split("Сначала")[0].replace("PermissionError на os.replace", "")  # no patch in the wish
    assert (ROOT / ATOMIC["target"]).is_file() and (ROOT / ATOMIC["verify_tests"][0]).is_file()


def test_atomic_scope_is_the_case_allow_list_not_the_discovery_rule():
    ok = cyc.stages(rec(changed_files=ATOMIC["allowed"]), check(), ATOMIC, None, False)
    assert ok["SCOPE_RESPECTED"] is True
    bad = cyc.stages(rec(changed_files=[ATOMIC["target"], "command-center/tests/test_other.py"]), check(), ATOMIC, None, False)
    assert bad["SCOPE_RESPECTED"] is False


def test_atomic_recipe_passes_the_store_grammar_and_names_its_own_cause():
    recipe, _, _ = cyc.build_recipe(rec(changed_files=ATOMIC["allowed"]), ATOMIC, check())
    assert validate_recipe(recipe) == []
    assert "os.replace" in recipe["cause"] and "NaN" not in recipe["cause"]
    assert "permissionerror" in recipe["applies_when"]["keywords"]
    assert recipe["steps"][1]["args"]["pattern"] == "os.replace"


def test_discovery_and_goal_budget_recipes_keep_their_old_defaults():
    recipe, _, _ = cyc.build_recipe(rec(), CASE, check())
    assert "NaN" in recipe["cause"] and recipe["steps"][1]["args"]["pattern"] == "float("
    assert "nan" in recipe["applies_when"]["keywords"]


def test_holdout_passes_every_case_on_an_implementation_that_survives_the_race():
    rows = atomic_json_replace.cases(_impl(retry=True), lambda v: v)
    assert len(rows) == 10 and all(r["ok"] for r in rows), [r for r in rows if not r["ok"]]


def test_holdout_flags_exactly_the_defect_cases_on_an_implementation_that_fails_when_the_destination_is_open():
    rows = atomic_json_replace.cases(_impl(retry=False), lambda v: v)
    failed = [r["case"] for r in rows if not r["ok"]]
    if os.name == "nt":            # only Windows refuses to replace an open file; on POSIX the base passes too
        assert failed and all(c.startswith(DEFECT_PREFIXES) for c in failed), failed
        assert any(c.startswith("reader holds") for c in failed)
    assert not any(c.startswith("valid/") for c in failed), failed


def test_local_worker_is_sent_to_the_api_as_none_and_cloud_workers_unchanged():
    assert cyc.wire_worker("local") is None
    assert cyc.wire_worker("nvidia-nim") == "nvidia-nim" and cyc.wire_worker("openrouter-free") == "openrouter-free"
