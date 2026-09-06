"""AGENT-4 — deterministic context ablation over the REAL bossman-core pipeline.

Why this exists: "testing the gate is not testing the model". There is no model
runner here, so no lane may claim an intelligence score. What CAN be measured
without a model is what orchestration does to the model's INPUT — and that is
the mechanism by which an orchestrator makes a good model dumber:

  * it spends the window on itself (tokens per layer),
  * it multiplies instruction sources and lets low-trust text reach system
    authority (instruction sources + their role/authority),
  * it prunes away the very facts the task depends on (required-fact survival),
  * it drowns the model in a tool registry it does not need (tools exposed).

Layers: RAW -> +SYSTEM -> +CONTEXT(memory/retrieval) -> +SKILLS(refs) -> FULL.
Everything here is deterministic: no network, no model, no embeddings service
beyond what context_engine already degrades to offline. Numbers are asserted,
not printed.
"""
from __future__ import annotations

import json

import pytest

from bossman import runner
from bossman.agents import AgentSpec, ToolGrant
from bossman.context import ContextBuilder, ContextBudget, estimate_tokens
from bossman.context_engine import prune_tool_schemas

WINDOW = 64_000

# The mission and the facts the model must still have when the pipeline is done.
TASK_TEXT = (
    "Прочитай файл reports/q3.md, найди строку с итоговой выручкой, "
    "отредактируй раздел «Итоги» и запусти тесты."
)
KEY_CONSTRAINT = "не отправлять данные наружу: проект PRIVATE"

REQUIRED_FACTS = {
    "task.object": "reports/q3.md",          # what to operate on
    "task.constraint": "PRIVATE",            # the constraint
    "task.forbidden": "не отправлять",       # the forbidden effect
    "task.verification": "запусти тесты",    # how completion is proven
}

AGENT_PROMPT = (
    "Ты аккуратный инженер-агент Bossman.\n"
    "Работаешь только в рабочей папке проекта.\n"
    "Не помечаешь задачу выполненной, пока тесты не прошли."
)

REFS = (
    "## Правила проекта\n"
    "Стиль: короткие функции, русские докстроки.\n\n"
    "## Раскадровка отчёта\n"
    "Итоги идут последним разделом.\n"
)

MEMORY_MD = (
    "# Заметки агента\n"
    "Проект PRIVATE — данные не выходят из локальной сети.\n"
    "Отчёты лежат в reports/, итоговая выручка — в разделе «Итоги».\n"
    "Ключи живут в .env и в модель не попадают.\n"
)


def _agent(tools: list[str]) -> AgentSpec:
    return AgentSpec(name="coder", title="Coder", model="local-coder",
                     tools=[ToolGrant(t) for t in tools])


def _tool_schemas(names: list[str]) -> list[dict]:
    return [runner.REGISTRY[n].schema() for n in names if n in runner.REGISTRY]


def _messages_tokens(msgs: list[dict]) -> int:
    return sum(estimate_tokens(m["content"]) for m in msgs)


def _instruction_sources(msgs: list[dict]) -> list[tuple[str, str]]:
    """(authority, label) for every message that can instruct the model.

    Authority is what the model will treat the block as:
      * "system"      — role=system, full policy authority;
      * "data"        — role=user but explicitly framed as data (a header that
                        says «это НЕ инструкции»);
      * "task"        — the owner's task turn;
      * "unlabelled"  — role=user carrying instruction-shaped text with no
                        framing. Anything unlabelled that is not the task turn
                        is a laundering channel and must not exist.
    """
    from bossman.context import RETRIEVED_DATA_HEADER

    out: list[tuple[str, str]] = []
    for i, m in enumerate(msgs):
        content = m["content"]
        if m["role"] == "system":
            out.append(("system", f"system[{i}]"))
        elif content.startswith(RETRIEVED_DATA_HEADER):
            out.append(("data", f"retrieved[{i}]"))
        elif content.startswith(runner.EXTERNAL_DATA_HEADER):
            out.append(("data", f"tool_output[{i}]"))
        elif i == len(msgs) - 1:
            out.append(("task", f"task[{i}]"))
        else:
            out.append(("unlabelled", f"unlabelled[{i}]"))
    return out


# --------------------------------------------------------------------- layers

def _layer_raw() -> dict:
    """RAW baseline: the mission and its constraint in one user turn, no
    orchestration at all. Every later layer must keep at least this much."""
    b = ContextBuilder(ContextBudget(window=WINDOW), system="",
                       key_constraint=KEY_CONSTRAINT)
    msgs = [m for m in b.build(TASK_TEXT) if m["content"]]
    return {"messages": msgs, "tools": []}


def _layer_system() -> dict:
    b = ContextBuilder(ContextBudget(window=WINDOW),
                       system=runner._system_prompt(_agent([])),
                       key_constraint=KEY_CONSTRAINT)
    return {"messages": b.build(TASK_TEXT), "tools": []}


def _layer_context(tmp_db, tools: list[dict] | None = None) -> dict:
    """+CONTEXT: memory.md indexed and injected through the real integration."""
    agent = _agent([])
    b = ContextBuilder(ContextBudget(window=WINDOW),
                       system=runner._system_prompt(agent),
                       key_constraint=KEY_CONSTRAINT)
    out_tools = runner.apply_context_engine(b, tools or [], project="coder",
                                            task_text=TASK_TEXT, memory_md=MEMORY_MD)
    return {"messages": b.build(TASK_TEXT), "tools": out_tools, "builder": b}


def _layer_skills(tmp_db) -> dict:
    agent = _agent([])
    b = ContextBuilder(ContextBudget(window=WINDOW),
                       system=runner._system_prompt(agent), refs=REFS,
                       key_constraint=KEY_CONSTRAINT)
    runner.apply_context_engine(b, [], project="coder",
                                task_text=TASK_TEXT, memory_md=MEMORY_MD)
    return {"messages": b.build(TASK_TEXT), "tools": [], "builder": b}


FULL_TOOLS = sorted(runner.REGISTRY)


def _layer_full(tmp_db) -> dict:
    agent = _agent(FULL_TOOLS)
    b = ContextBuilder(ContextBudget(window=WINDOW),
                       system=runner._system_prompt(agent), refs=REFS,
                       key_constraint=KEY_CONSTRAINT)
    tools = runner.apply_context_engine(b, _tool_schemas(FULL_TOOLS), project="coder",
                                        task_text=TASK_TEXT, memory_md=MEMORY_MD)
    return {"messages": b.build(TASK_TEXT), "tools": tools, "builder": b}


@pytest.fixture()
def ablation(tmp_path, monkeypatch):
    """All five layers, built once against an isolated context db."""
    from bossman.config import settings
    from bossman.context_engine import close_all

    db = tmp_path / "ctx.db"
    monkeypatch.setattr(settings, "context_db", db, raising=False)
    monkeypatch.setattr(settings, "context_engine_enabled", True, raising=False)
    close_all()
    try:
        layers = {
            "RAW": _layer_raw(),
            "+SYSTEM": _layer_system(),
            "+CONTEXT": _layer_context(db),
            "+SKILLS": _layer_skills(db),
            "FULL": _layer_full(db),
        }
        for name, layer in layers.items():
            layer["tokens"] = _messages_tokens(layer["messages"])
            layer["tools_tokens"] = (
                estimate_tokens(json.dumps(layer["tools"], ensure_ascii=False))
                if layer["tools"] else 0)
            layer["sources"] = _instruction_sources(layer["messages"])
            layer["name"] = name
        yield layers
    finally:
        close_all()


LAYER_ORDER = ["RAW", "+SYSTEM", "+CONTEXT", "+SKILLS", "FULL"]


# ---------------------------------------------------------------- assertions

def test_ablation_layers_are_monotonic_in_prompt_cost(ablation):
    """Each layer may only add prompt cost, and the total must stay inside the
    working set (window minus the untouchable reserve). A layer that pushes the
    prompt past the working set is spending the model's reasoning room."""
    costs = [ablation[n]["tokens"] + ablation[n]["tools_tokens"] for n in LAYER_ORDER]
    for prev, cur, name in zip(costs, costs[1:], LAYER_ORDER[1:]):
        assert cur >= prev, f"layer {name} lost prompt content instead of adding it"
    working_set = ContextBudget(window=WINDOW).working_set
    assert costs[-1] <= working_set, (
        f"FULL layer costs {costs[-1]} tokens, working set is {working_set}")


def test_no_layer_introduces_a_second_system_authority_source(ablation):
    """Exactly one message may carry system authority beyond the fixed pair
    (agent prompt, project refs), and nothing may be unlabelled.

    An unlabelled instruction-shaped block is how retrieved text, memory and
    tool output launder themselves into policy. The retrieved block is allowed
    only because it is explicitly framed as data (RETRIEVED_DATA_HEADER)."""
    for name in LAYER_ORDER:
        srcs = ablation[name]["sources"]
        unlabelled = [s for a, s in srcs if a == "unlabelled"]
        assert not unlabelled, f"{name}: unlabelled instruction source(s) {unlabelled}"
        system_sources = [s for a, s in srcs if a == "system"]
        # system prompt (+ refs once the SKILLS layer is on) and nothing else
        assert len(system_sources) <= 2, (
            f"{name}: {len(system_sources)} system-authority sources: {system_sources}")


def test_context_layer_adds_only_data_authority(ablation):
    """Adding memory/retrieval must add a DATA source, never a system one."""
    sys_before = sum(1 for a, _ in ablation["+SYSTEM"]["sources"] if a == "system")
    sys_after = sum(1 for a, _ in ablation["+CONTEXT"]["sources"] if a == "system")
    assert sys_after == sys_before, (
        "the memory/retrieval layer changed the number of system-authority sources")
    data_after = sum(1 for a, _ in ablation["+CONTEXT"]["sources"] if a == "data")
    assert data_after >= 1, "retrieval produced nothing — the layer is not exercised"


@pytest.mark.parametrize("layer", LAYER_ORDER)
def test_required_facts_survive_every_layer(ablation, layer):
    """A required fact that gets pruned is a defect.

    The task object, its constraint, its forbidden effect and its verification
    condition must be literally present in the prompt of every layer that has a
    task at all — including FULL, where the budget is under the most pressure.
    """
    blob = "\n".join(m["content"] for m in ablation[layer]["messages"])
    missing = [k for k, v in REQUIRED_FACTS.items() if v not in blob]
    assert not missing, f"{layer}: required facts pruned: {missing}"


def test_no_layer_silently_drops_instruction_text(ablation):
    """ContextBuilder must report anything it removed; a silent cut is the
    failure nobody can diagnose. Here nothing should be cut at all."""
    for name in ("+CONTEXT", "+SKILLS", "FULL"):
        report = ablation[name]["builder"].pruning_report()
        assert report == {}, f"{name}: budget removed instruction text: {report}"


def test_memory_layer_does_not_evict_task_constraints(ablation):
    """Adding memory must not push the task constraint out of the prompt."""
    for name in ("+CONTEXT", "+SKILLS", "FULL"):
        last = ablation[name]["messages"][-1]["content"]
        assert KEY_CONSTRAINT in last, (
            f"{name}: the key constraint left the final task turn")


def test_full_layer_does_not_expose_the_whole_registry(ablation):
    """FULL exposes the pruned tool surface, not the registry."""
    exposed = [s["function"]["name"] for s in ablation["FULL"]["tools"]]
    assert len(exposed) < len(FULL_TOOLS), (
        f"FULL exposed the whole registry: {len(exposed)}/{len(FULL_TOOLS)}")


def test_ablation_is_deterministic(tmp_path, monkeypatch):
    """Same input twice -> byte-identical prompt. A context pipeline that is not
    reproducible cannot be benchmarked at all."""
    from bossman.config import settings
    from bossman.context_engine import close_all

    def once(db):
        monkeypatch.setattr(settings, "context_db", db, raising=False)
        monkeypatch.setattr(settings, "context_engine_enabled", True, raising=False)
        close_all()
        try:
            return json.dumps(_layer_full(db)["messages"], ensure_ascii=False)
        finally:
            close_all()

    assert once(tmp_path / "a.db") == once(tmp_path / "b.db")
