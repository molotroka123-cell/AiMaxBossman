"""AGENT-4 — tool overload: the capability path must expose the SMALLEST
SUFFICIENT tool set, and must not lose the tools the mission needs.

Two failure modes, both measured here against the real 46-tool registry:

  * OVERLOAD — a mission that needs ~4 tools is handed dozens. Every extra
    schema is prompt budget and one more thing to pick wrong.
  * CAPABILITY LOSS — the pruner drops the tool the mission is about. This is
    the worse one, and it was real: `prune_tool_schemas` matched whole words
    only, so the Russian mission «Прочитай ФАЙЛ» shared no token with fs_read's
    description «Диапазон строк ФАЙЛА», scored 0 for every fs_* tool, and the
    keep_min floor then filled the slots in INDEX (alphabetical) order —
    the agent asked to read a file got ten browser_* tools and no fs_read.

RESIDUAL (not fixed here, deliberately): matching is still lexical. A stem of
length 4 bridges word FORMS («файл»/«файла»), it cannot bridge cross-script
SYNONYMS («коммит» -> git). Such a mission falls back to the capability-diverse
floor asserted below — it is no longer handed an alphabetical slice, but it is
not guaranteed its tool either. Closing that needs embeddings over the tool
descriptions, which is a model-backed change and out of this lane.
"""
from __future__ import annotations

import json
import re

import pytest

from bossman import runner
from bossman.context import estimate_tokens
from bossman.context_engine import prune_tool_schemas

ALL = sorted(runner.REGISTRY)
SCHEMAS = [runner.REGISTRY[n].schema() for n in ALL]
KEEP_MIN = 10                       # what runner.apply_context_engine uses


def _names(schemas: list[dict]) -> list[str]:
    return [s["function"]["name"] for s in schemas]


def _prune(query: str, keep_min: int = KEEP_MIN) -> list[str]:
    return _names(prune_tool_schemas(SCHEMAS, query, keep_min=keep_min,
                                     always=runner._ALWAYS_TOOLS))


def _family(name: str) -> str:
    return re.split(r"[._-]", name, maxsplit=1)[0]


# ------------------------------------------------------------- smallest set

FOUR_TOOL_MISSION = ("Прочитай файл reports/q3.md, найди строку с выручкой, "
                     "перезапиши файл итогов и запусти тесты.")
FOUR_TOOL_REQUIRED = {"fs_read", "fs_search", "fs_write", "tests"}


def test_a_four_tool_mission_does_not_get_the_whole_registry():
    kept = _prune(FOUR_TOOL_MISSION)
    assert len(kept) < len(ALL) / 2, (
        f"mission needing ~4 tools was handed {len(kept)}/{len(ALL)} schemas: {kept}")


def test_a_four_tool_mission_keeps_the_four_tools_it_needs():
    """Capability loss regression. A required tool pruned is a defect, and it is
    strictly worse than an extra tool kept."""
    kept = set(_prune(FOUR_TOOL_MISSION))
    missing = sorted(FOUR_TOOL_REQUIRED - kept)
    assert not missing, f"the mission's own tools were pruned away: {missing}"


def test_safety_critical_confirmed_tools_are_never_pruned():
    """`_ALWAYS_TOOLS` must survive any query, including one crafted to bury
    them under unrelated matches."""
    hostile = ("gmail gmail gmail http http crm crm ffmpeg ffmpeg vision vision "
               "artifact artifact docs docs analysis analysis media media")
    for query in (FOUR_TOOL_MISSION, hostile, "", "zzz"):
        kept = _prune(query)
        for guarded in runner._ALWAYS_TOOLS:
            assert guarded in kept, f"{guarded} pruned for query {query[:30]!r}"


def test_pruning_saves_prompt_budget():
    full = estimate_tokens(json.dumps(SCHEMAS, ensure_ascii=False))
    kept = prune_tool_schemas(SCHEMAS, FOUR_TOOL_MISSION, keep_min=KEEP_MIN,
                              always=runner._ALWAYS_TOOLS)
    pruned = estimate_tokens(json.dumps(kept, ensure_ascii=False))
    assert pruned < full / 2, f"pruning saved nothing: {pruned} of {full} tokens"


# --------------------------------------------- hostile retest #1: word forms

@pytest.mark.parametrize("query,required", [
    ("Прочитай файл README.md", "fs_read"),
    ("прочитать файлы проекта", "fs_read"),
    ("запусти тесты и покажи упавшие", "tests"),
    ("прочитай письма в почте", "gmail_read"),
    ("git commit изменений в репозитории", "git"),
])
def test_inflected_missions_still_reach_their_tool(query, required):
    """Second, different attack on the same defect: the same capability asked
    for in several word forms. Whole-word matching failed all of these."""
    assert required in _prune(query), f"{required} missing for {query!r}"


# ----------------------------------- hostile retest #2: zero-evidence queries

@pytest.mark.parametrize("query", ["", "zzz qqq", "..." , "проанализируй рынок кофеен"])
def test_zero_evidence_floor_is_capability_diverse_not_alphabetical(query):
    """When nothing matches, the floor used to be the alphabetically first
    keep_min names — for this registry that is ten browser_* tools and no file,
    git, mail or test capability at all. The floor must instead spread across
    capability families."""
    kept = _prune(query)
    families = {_family(n) for n in kept}
    assert len(families) >= 5, (
        f"zero-evidence floor covered only {sorted(families)} for query {query!r}")
    assert not all(_family(n) == "browser" for n in kept
                   if n not in runner._ALWAYS_TOOLS)


def test_floor_never_returns_fewer_than_keep_min():
    for query in ("", "zzz", FOUR_TOOL_MISSION):
        assert len(_prune(query)) >= KEEP_MIN


def test_small_registries_are_never_pruned():
    small = SCHEMAS[:KEEP_MIN]
    assert prune_tool_schemas(small, "zzz", keep_min=KEEP_MIN) == small


def test_missions_that_legitimately_need_many_tools_still_get_them():
    """The fix must not make broad missions narrow. A mission naming browser,
    file, git and mail work keeps tools from all of those families."""
    broad = ("открой браузер, извлеки данные со страницы, сохрани файл, "
             "сделай коммит в git и отправь письмо в gmail")
    kept = _prune(broad)
    families = {_family(n) for n in kept}
    for fam in ("browser", "fs", "git", "gmail"):
        assert fam in families, f"broad mission lost the {fam} family: {sorted(kept)}"


def test_pruning_is_deterministic_and_order_preserving():
    a = _prune(FOUR_TOOL_MISSION)
    b = _prune(FOUR_TOOL_MISSION)
    assert a == b
    order = [n for n in ALL if n.replace(".", "_") in a]
    assert [n.replace(".", "_") for n in order] == a, "pruning reordered the tools"
