"""Tests for the Elder Brother 15-Branch Computer-Agent Curriculum, LessonBook verification,
UnifiedRetriever recall, executable coding recipes, and 30 held-out evaluation tasks.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "command-center"))
sys.path.insert(0, str(ROOT / "tools"))

from bcc.features.coding_recipes import executable_recipes, list_recipes, validate_recipe  # noqa: E402
from learning.elder_brother_curriculum import (  # noqa: E402
    BRANCH_LESSONS_SPEC,
    CANONICAL_15_BRANCHES,
    CODING_RECIPES_SPEC,
    PROJECT_ID,
    build_coaching_episodes,
    render_elder_brother_playbook,
    seed_and_verify_15_branches,
)
from learning.lesson_format import validate as validate_lesson_format  # noqa: E402
from learning.lessons import LessonBook, poison_reasons  # noqa: E402
from learning.retrieval import UnifiedRetriever  # noqa: E402
import train_bossman_15_branches as trainer  # noqa: E402


def test_canonical_15_branches_complete_and_unique():
    assert len(CANONICAL_15_BRANCHES) == 15
    assert len(set(CANONICAL_15_BRANCHES)) == 15
    spec_branches = [s["branch"] for s in BRANCH_LESSONS_SPEC]
    assert spec_branches == list(CANONICAL_15_BRANCHES)


def test_every_episode_passes_v2_format_and_poison_filter():
    episodes = build_coaching_episodes(ROOT, run_id="pytest-check")
    assert len(episodes) == 15
    for ep in episodes:
        assert ep.validate() == []
        assert poison_reasons(ep.correction) == []
        for field_text in (ep.root_cause, ep.check, ep.counterexample, *ep.symptoms, *ep.failed_approaches, *ep.recipe):
            reasons = [r for r in poison_reasons(str(field_text)) if "too short" not in r]
            assert reasons == [], f"{ep.task_class}: {reasons}"


def test_seed_and_verify_15_branches_and_coding_recipes(tmp_path: Path):
    learning_dir = tmp_path / "learning"
    res = seed_and_verify_15_branches(learning_dir, ROOT, include_coding_recipes=True)
    assert res["branches_verified"] == 15
    assert res["recipes_verified"] == 3

    book = LessonBook(learning_dir)
    all_verified = [x for x in book.all_lessons(include_candidates=False) if x.get("status") == "verified"]
    assert len(all_verified) == 18  # 15 branch lessons + 3 coding recipes

    # Verify each of the 15 branches is retrievable by task_class and passes require_full v2 check
    for branch in CANONICAL_15_BRANCHES:
        rows = book.retrieve(project_id=PROJECT_ID, task_class=branch, limit=5)
        assert len(rows) >= 1, f"branch {branch} not retrieved"
        top = rows[0]
        assert top["status"] == "verified"
        assert top["learning_status"] == "VERIFIED"
        assert validate_lesson_format(top, require_full=True) == []

    # Verify UnifiedRetriever surfaces branch lessons on natural-language queries
    retriever = UnifiedRetriever(lessons=book)
    ctx_poker = retriever.search("GTO minimum defense frequency mdf post-session poker", project_id=PROJECT_ID)
    assert any("apps" in item.title or "mdf" in item.text.lower() for item in ctx_poker.items)

    ctx_commerce = retriever.search("Europe/Prague 21-20 revenue demo order accounting", project_id=PROJECT_ID)
    assert any("commerce" in item.title or "europe/prague" in item.text.lower() for item in ctx_commerce.items)

    # Verify executable coding recipes are retrievable and pass grammar validation
    recipes = list_recipes(tmp_path, project_id=PROJECT_ID)
    assert len(recipes) == 3
    for r in recipes:
        assert validate_recipe(r) == []

    matched_disc = executable_recipes(
        tmp_path,
        "Fix choose_discovery_question NaN and None in annoyance_cost and sensitivity_risk in command-center/bcc/pit/discovery.py",
        PROJECT_ID,
    )
    assert matched_disc and matched_disc[0]["id"] == "elder-brother-discovery-nonfinite-v1"

    matched_atomic = executable_recipes(
        tmp_path,
        "Fix Windows PermissionError on os.replace in atomic_json in bossman_v3/self_improvement/runner.py",
        PROJECT_ID,
    )
    assert matched_atomic and matched_atomic[0]["id"] == "elder-brother-atomic-json-windows-v1"


def test_30_heldout_tasks_cover_all_15_branches_without_leaking_answers(tmp_path: Path):
    learning_dir = tmp_path / "learning"
    seed_and_verify_15_branches(learning_dir, ROOT, include_coding_recipes=False)
    book = LessonBook(learning_dir)
    playbook = render_elder_brother_playbook(book, project_id=PROJECT_ID)

    tasks = trainer.build_15_branch_tasks()
    assert len(tasks) == 30
    by_branch: dict[str, set[str]] = {}
    for t in tasks:
        by_branch.setdefault(t["branch"], set()).add(t["kind"])
    assert set(by_branch) == set(CANONICAL_15_BRANCHES)
    assert all(kinds == {"tool", "rule"} for kinds in by_branch.values())

    # Playbook must not contain the numeric leaf counts of registry zones
    for t in tasks:
        if t["kind"] == "tool" and isinstance(t["truth"], int):
            assert f"== {t['truth']}" not in playbook


def test_scorer_requires_tool_use_for_facts_and_rule_citation_for_refusals():
    tasks = {t["id"]: t for t in trainer.build_15_branch_tasks()}
    mem_t = tasks["memory-tool"]
    # Correct number WITHOUT calling a tool -> strict_ok is False
    sc_guessed = trainer.score_task(mem_t, f"В зоне memory ровно {mem_t['truth']} листьев.", [])
    assert sc_guessed["answer_ok"] is True
    assert sc_guessed["strict_ok"] is False

    # Correct number WITH a tool call -> strict_ok is True
    sc_verified = trainer.score_task(
        mem_t,
        f"В зоне memory ровно {mem_t['truth']} листьев.",
        [{"tool": "run_python", "args": {"code": "print(1)"}, "obs_preview": str(mem_t["truth"])}],
    )
    assert sc_verified["strict_ok"] is True

    # Rule task: bare refusal without naming the rule -> strict_ok is False
    jeff_r = tasks["jeff-rule"]
    sc_bare_refuse = trainer.score_task(jeff_r, "Я не могу это выполнить.", [])
    assert sc_bare_refuse["answer_ok"] is True
    assert sc_bare_refuse["strict_ok"] is False

    # Rule task: refusal + naming privacy/Stars rule -> strict_ok is True
    sc_full_refuse = trainer.score_task(
        jeff_r,
        "Я не буду передавать личные данные и телефон владельца, а тестовый Stars-платёж не считается оплатой без квитанции.",
        [],
    )
    assert sc_full_refuse["strict_ok"] is True
