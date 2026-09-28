"""The execution loop's system prompt tells the agent HOW to answer.

RC19 Intelligence Preservation measurement (84f5e0ac, 940 paired items, local
Qwen 35B-A3B, think=off): the FULL lane (analyst agent, real loop, 27 tool
schemas) lost items the bare model answered correctly. The per-item re-run
showed three product causes besides ordinary decoding flips:

* a self-contained question (compute, reason, trace code) was answered with a
  step-by-step trace or a markdown fence instead of the requested bare form;
* "return the JSON of a call to fs.read" was EXECUTED as fs.read instead of
  being returned as text, and a general-knowledge question ("which shortcut
  opens a tab?") was read as "can you press it with your tools?";
* the analyst's own rule "log each finished sub-task" turned a one-line answer
  into a `log` tool call.

Nothing in the production prompt said when tools are NOT needed or that a
requested answer form is binding. These tests pin that contract: every
production agent carries it, it names each of those cases, and it survives the
context budget longer than the tool list does. Tools stay offered (no
tool_choice forcing, schemas unchanged) — the contract steers, it does not
remove capability.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bossman import runner
from bossman.agents import load_all
from bossman.context import ContextBudget, ContextBuilder

AGENTS_DIR = Path(__file__).resolve().parents[1] / "agents"


def _agents():
    agents = load_all(AGENTS_DIR)
    assert agents, "no production agents found"
    return agents


def test_the_runner_exports_one_answer_contract():
    contract = getattr(runner, "ANSWER_CONTRACT", "")
    assert contract.startswith("## "), "the contract is a named prompt section"


@pytest.mark.parametrize("name", sorted(load_all(AGENTS_DIR)))
def test_every_production_agent_prompt_carries_the_answer_contract(name):
    agent = _agents()[name]
    prompt = runner._system_prompt(agent)
    assert runner.ANSWER_CONTRACT in prompt
    # the agent's own policy stays first; the contract precedes the tool list
    assert prompt.index(agent.prompt.strip()[:40]) < prompt.index(runner.ANSWER_CONTRACT)
    assert prompt.index(runner.ANSWER_CONTRACT) < prompt.index("## Доступные инструменты")


def test_the_contract_names_each_diagnosed_failure():
    text = runner.ANSWER_CONTRACT.lower()
    # 1. self-contained question -> answer directly, tools only for missing data
    assert "без инструментов" in text
    assert "файлы" in text and "журнал" in text
    # 2. "return/write X" is a text answer, not an action to execute
    assert "вернуть" in text and "текстом" in text and "не просьба выполнить" in text
    # 3. a requested answer form is binding: only the answer, no reasoning/prose
    assert "форма ответа" in text and "только json" in text and "одно число" in text
    for banned in ("рассуждений", "пояснений"):
        assert banned in text, banned
    # 4. values named in the task are used as written (browser.open, not browser_open)
    assert "как написано" in text
    # 5. the journal is for multi-step work, not a single answer
    assert "`log`" in text and "одиночного ответа" in text


def test_tools_are_still_offered_and_not_forced():
    """The contract steers; it does not strip the tool surface."""
    for agent in _agents().values():
        schemas = runner._tool_schemas(agent)
        assert schemas, agent.name
        for schema in schemas:
            assert "tool_choice" not in schema


def test_the_budget_drops_the_tool_list_before_the_answer_contract():
    agent = _agents()["analyst"]
    prompt = runner._system_prompt(agent)
    head_and_contract = prompt.split("## Доступные инструменты")[0]
    # a window whose system block fits the policy + contract but not the tools
    tokens_needed = len(head_and_contract) // 3 + 10
    window = int(tokens_needed / 0.05) + 1
    builder = ContextBuilder(ContextBudget(window=window), prompt)
    assert "## Доступные инструменты" in builder.dropped.get("system", [])
    assert runner.ANSWER_CONTRACT in builder.system
