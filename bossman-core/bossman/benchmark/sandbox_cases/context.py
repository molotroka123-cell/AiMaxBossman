"""REAL_SANDBOX cases for the durable working-state and context-assembly boundaries."""
from __future__ import annotations

import asyncio
import gc
import json
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from ..sandbox_row import CaseProbe


@contextmanager
def _settings_guard(*names: str):
    """`bossman.config.settings` is a process-global dataclass instance: any flag a
    case flips would leak into every later case, so snapshot and restore."""
    from bossman.config import settings
    saved = {n: getattr(settings, n) for n in names}
    try:
        yield settings
    finally:
        for name, value in saved.items():
            setattr(settings, name, value)


def _boom(_memory_md: str):
    """Fault injection at the *boundary* of the code under test (the selector),
    never a replacement for it — runner._memory_for_system stays real."""
    raise RuntimeError("injected selector failure")


def _restart_read(projects_dir: str, slug: str) -> dict:
    """Read the project state back from a genuinely NEW OS process."""
    import bossman
    pkg_root = str(Path(bossman.__file__).resolve().parents[1])
    code = (
        "import json,sys;sys.path.insert(0,%r);from pathlib import Path;"
        "import bossman.projects.plan as pm;pm.settings.projects_dir=Path(%r);"
        "s=pm.State(%r);d=s.data;"
        "print(json.dumps({'status':d['status'],'tasks':sorted(d['tasks']),'spent':d['spent'],"
        "'t1_status':s.task('task-1')['status'],'t1_spent':s.task('task-1')['spent']}))"
        % (pkg_root, projects_dir, slug)
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          timeout=180, check=False)
    try:
        return json.loads(done.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        return {"child_failed": done.stderr[-300:]}


# --------------------------------------------------------------------- working_state
def working_state(seed: int) -> dict:
    probe = CaseProbe("sandbox.working_state", "working_state", seed)
    from bossman import errors
    from bossman.projects.plan import State
    from bossman.working_memory import WorkingMemory

    slug = f"bench-ws-{seed}"
    wm = WorkingMemory()  # canonical LLM-Arch-V2 working state (Postgres-backed)
    with tempfile.TemporaryDirectory(prefix="bench-ws-") as td, \
            _settings_guard("projects_dir", "database_url") as settings:
        settings.projects_dir = Path(td)
        st = State(slug)
        st.data["status"] = "running"
        st.save()                                                  # durable write #1
        st.mark("task-1", "done", cost=1.25, artifacts=["out.mp4"])  # write #2 (+.bak of #1)
        st.mark("task-2", "running", cost=0.50)                    # write #3 (+.bak of #2)
        probe.count(effects=3)

        probe.positive("task_row_written_exactly", st.task("task-1"),
                       {"status": "done", "attempts": 0, "spent": 1.25, "artifacts": ["out.mp4"]})
        probe.positive("cost_ledger_accumulated_exactly", st.data["spent"], 1.75)
        probe.positive("done_flags_from_state", [st.is_done("task-1"), st.is_done("task-2")],
                       [True, False])
        probe.positive("survives_real_process_restart", _restart_read(td, slug),
                       {"status": "running", "tasks": ["task-1", "task-2"], "spent": 1.75,
                        "t1_status": "done", "t1_spent": 1.25})

        # A torn write must roll back to the previous durable version, not to defaults.
        state_json = Path(td) / slug / "state.json"
        bak = state_json.with_name("state.json.bak")
        state_json.write_text('{"status": "run', encoding="utf-8")
        rolled = State(slug)
        probe.positive("rolls_back_to_previous_version_after_torn_write",
                       {"spent": rolled.data["spent"], "tasks": sorted(rolled.data["tasks"]),
                        "status": rolled.data["status"]},
                       {"spent": 1.25, "tasks": ["task-1"], "status": "running"})
        probe.count(recoveries=1)

        # ---- refusals -------------------------------------------------------
        state_json.write_text('{"status": "run', encoding="utf-8")
        bak.unlink()
        probe.refused("no_silent_reset_when_every_copy_is_lost", lambda: State(slug),
                      FileNotFoundError, contains="state.json.bak")
        # Column allow-list: an injection-shaped key and the concurrency column are
        # dropped before any SQL is built, so the call fails instead of writing.
        probe.refused("wm_drops_injected_and_control_columns",
                      lambda: asyncio.run(wm.update_task_state(
                          f"t-{seed}", {"objective; DROP TABLE working_memory--": "x", "version": 99})),
                      ValueError, contains="no updatable columns in updates")
        probe.refused("wm_rejects_malformed_checkpoint",
                      lambda: asyncio.run(wm.restore_checkpoint({"version": 1})),
                      KeyError, contains="task_id")
        # Durable store absent -> typed refusal, never a silent in-memory substitute.
        settings.database_url = "postgresql://bossman:bossman@127.0.0.1:1/bossman"
        probe.refused("wm_refuses_loudly_without_durable_store",
                      lambda: asyncio.run(wm.create_task_state(f"t-{seed}", "objective")),
                      errors.DependencyUnavailable, contains="Postgres")

    probe.tag("WORKING-STATE", "FS-DURABLE", "PG-ABSENT")
    return probe.finish()


# ----------------------------------------------------------------- context_selection
def context_selection(seed: int) -> dict:
    probe = CaseProbe("sandbox.context_selection", "context_selection", seed)
    from bossman.context import RETRIEVED_DATA_HEADER, ContextBudget, ContextBuilder
    from bossman.context_engine import ContextEngine, prune_tool_schemas
    from bossman.search_everything.engine import SearchDocument, SearchEngine

    project = f"bench-sel-{seed}"
    query = "deploy approval release owner"
    with tempfile.TemporaryDirectory(prefix="bench-sel-") as td:
        # direct ctor, not get_engine(): that singleton is keyed by path per process
        eng = ContextEngine(Path(td) / "context.db")
        try:
            corpus = (  # (source_uri, text, sensitivity) — payroll is the sensitive decoy
                ("notes/deploy_runbook.md", "# Deploy runbook\nDeploy approval required from the"
                 " release owner before every deploy\n", "normal"),
                ("notes/pasta_recipe.md", "# Pasta recipe\nBoil water add pasta and salt for"
                 " twelve minutes then drain\n", "normal"),
                ("notes/vacation.md", "# Vacation\nThe office is closed in August and the beach"
                 " is warm\n", "normal"),
                ("notes/payroll.md", "# Payroll deploy approval\nDeploy approval payroll ledger"
                 " salary of every employee owner\n", "restricted"),
            )
            for uri, text, sens in corpus:
                eng.index_text(text, source_uri=uri, source_type="markdown", project=project,
                               sensitivity=sens)
            probe.count(effects=4)

            probe.positive("all_four_documents_indexed",
                           [r[0] for r in eng.store.db.execute(
                               "SELECT source_uri FROM documents ORDER BY source_uri")],
                           ["notes/deploy_runbook.md", "notes/pasta_recipe.md",
                            "notes/payroll.md", "notes/vacation.md"])
            probe.positive("relevant_selected_irrelevant_dropped",
                           [h.chunk.source_uri for h in
                            eng.retriever.search(query, project=project, result_limit=5)],
                           ["notes/deploy_runbook.md", "notes/payroll.md"])

            # Authorization beats relevance: payroll.md outranks pasta/vacation on the
            # very same query, yet the public search floor must not return it.
            se = SearchEngine(eng)
            probe.negative("restricted_doc_refused_without_permission",
                           [h.document.id for h in se.search(query, project=project, limit=10)],
                           ["notes/deploy_runbook.md"])
            probe.positive("restricted_doc_returned_with_permission",
                           sorted(h.document.id for h in se.search(
                               query, project=project, limit=10,
                               sensitivity_allow=("normal", "restricted"))),
                           ["notes/deploy_runbook.md", "notes/payroll.md"])
            probe.negative("secret_document_refused_at_ingest",
                           {"returned": se.upsert([SearchDocument(
                               id=".env", text="OPENAI_API_KEY=sk-live-abcdefghijklmnop",
                               source="text", project=project)]),
                            "in_store": [r[0] for r in eng.store.db.execute(
                                "SELECT source_uri FROM documents WHERE source_uri='.env'")]},
                           {"returned": [], "in_store": []})

            rec = eng.memory.constraint("NEVER deploy without release owner approval",
                                        project=project, source_refs=["notes/deploy_runbook.md"])
            eng.memory.promote(rec.memory_id, verified=True)
            blocks = eng.build_injection(query, project)
            probe.positive("durable_memory_block_selected_first_with_provenance",
                           {"n": len(blocks), "head": blocks[0].splitlines()[0],
                            "provenance": "sources=notes/deploy_runbook.md" in blocks[0],
                            "evidence_head": blocks[1].splitlines()[0]},
                           {"n": 3, "head": "## Долговременная память (provenance)",
                            "provenance": True,
                            "evidence_head": "### notes/deploy_runbook.md :: Deploy runbook"})

            wide = ContextBuilder(ContextBudget(window=8192), "sys")
            eng.inject_into_builder(wide, query, project=project)
            narrow = ContextBuilder(ContextBudget(window=900), "sys")
            eng.inject_into_builder(narrow, query, project=project)
            probe.positive("tight_budget_keeps_highest_priority_block",
                           {"wide": len(wide.retrieved), "narrow": len(narrow.retrieved),
                            "narrow_is_memory": narrow.retrieved[0].startswith(
                                "## Долговременная память")},
                           {"wide": 3, "narrow": 1, "narrow_is_memory": True})

            msgs = wide.build("почини деплой")
            probe.positive("selection_reaches_builder_as_untrusted_data",
                           {"roles": [m["role"] for m in msgs],
                            "framed": msgs[1]["content"].startswith(RETRIEVED_DATA_HEADER),
                            "pasta_selected": "pasta" in msgs[1]["content"]},
                           {"roles": ["system", "user", "user"], "framed": True,
                            "pasta_selected": False})

            schemas = [{"function": {"name": f"t{i}", "parameters": {"properties": {}},
                                     "description": "deploy approval tool" if i == 3 else "misc"}}
                       for i in range(12)]
            probe.positive("tool_schemas_pruned_to_relevant",
                           [s["function"]["name"] for s in prune_tool_schemas(
                               schemas, "deploy approval", keep_min=3, always=("t9",))],
                           ["t0", "t1", "t3", "t9"])

            compiled = eng.compiler.compile(model="local/bench", query=query, project=project,
                                            system="S", task_state="T", model_window=4096,
                                            desired_output=1024)
            probe.positive("compiler_sections_fit_the_budget",
                           {"sections": [s.name for s in compiled.sections],
                            "within": compiled.used_tokens <= compiled.budget_tokens,
                            "sources": compiled.telemetry["retrieved_sources"]},
                           {"sections": ["System", "Active task", "Relevant memory",
                                         "Retrieved evidence"], "within": True, "sources": 2})
            probe.refused("promote_unknown_memory_refused",
                          lambda: eng.memory.promote(f"mem_missing_{seed}"),
                          KeyError, contains="mem_missing")
        finally:
            eng.close()

    probe.tag("CONTEXT-SELECTION", "SENSITIVITY-GATE", "SQLITE-ONLY")
    return probe.finish()


# ------------------------------------------------------------- raw_context_fallback
def raw_context_fallback(seed: int) -> dict:
    probe = CaseProbe("sandbox.raw_context_fallback", "raw_context_fallback", seed)
    from bossman import personal_context, runner
    from bossman.agents import AgentSpec
    from bossman.context import ContextBudget, ContextBuilder
    from bossman.context_engine import ContextEngine

    memory_md = ("# Правила\nНИКОГДА не отправляй пароли во внешние сервисы\n"
                 "Любимый редактор — vim\nReact 18 used\n")
    critical = "НИКОГДА не отправляй пароли во внешние сервисы"
    tools = [{"function": {"name": f"tool{i}", "description": "misc",
                           "parameters": {"properties": {}}}} for i in range(14)]

    # ignore_cleanup_errors: ContextStore.__init__ opens sqlite before it validates
    # the file, so a failed engine build can leave a Windows lock on the temp db.
    with tempfile.TemporaryDirectory(prefix="bench-fb-", ignore_cleanup_errors=True) as td, \
            _settings_guard("personal_context_select", "context_engine_enabled",
                            "context_db") as settings:
        agent_dir = Path(td)
        (agent_dir / "prompt.md").write_text("You are the coder agent.", encoding="utf-8")
        (agent_dir / "memory.md").write_text(memory_md, encoding="utf-8")
        agent = AgentSpec(name=f"coder{seed}", title="Coder", model="local/bench", path=agent_dir)

        settings.personal_context_select = False
        probe.positive("flag_off_is_byte_identical_raw", runner._memory_for_system(memory_md),
                       memory_md)

        settings.personal_context_select = True
        settings.context_engine_enabled = True
        selected = runner._memory_for_system(memory_md)
        probe.positive("selection_on_keeps_critical_drops_trivia",
                       {"critical": critical in selected, "vim": "vim" in selected,
                        "react": "React 18" in selected,
                        "pointer": personal_context.RETRIEVED_NOTE in selected},
                       {"critical": True, "vim": False, "react": False, "pointer": True})

        settings.context_engine_enabled = False
        probe.positive("retrieved_channel_off_falls_back_to_raw",
                       runner._memory_for_system(memory_md), memory_md)

        settings.context_engine_enabled = True
        original = personal_context.select_memory
        personal_context.select_memory = _boom
        try:
            probe.positive("selector_failure_falls_back_to_raw",
                           runner._memory_for_system(memory_md), memory_md)
            prompt = runner._system_prompt(agent)
            probe.positive("real_system_prompt_keeps_whole_memory_on_failure",
                           {"critical": critical in prompt, "vim": "vim" in prompt},
                           {"critical": True, "vim": True})
        finally:
            personal_context.select_memory = original

        # A real, unfaked engine failure: a context_db that is not a sqlite file.
        corrupt = agent_dir / "corrupt-context.db"
        corrupt.write_bytes(b"NOT-A-SQLITE-FILE" * 32)
        probe.refused("corrupt_context_db_refused_at_engine_layer",
                      lambda: ContextEngine(corrupt), sqlite3.DatabaseError,
                      contains="not a database")

        settings.context_db = corrupt
        builder = ContextBuilder(ContextBudget(window=8192), "sys")
        out = runner.apply_context_engine(builder, tools, project="coder",
                                          task_text="deploy approval", memory_md=memory_md)
        probe.negative("engine_failure_returns_original_tools_untouched",
                       {"same_object": out is tools, "n": len(out), "retrieved": builder.retrieved},
                       {"same_object": True, "n": 14, "retrieved": []})
        builder.add_assistant("build failed: 3 tests red in app/calc.py")
        probe.negative("engine_failure_yields_no_bogus_handoff",
                       runner.compact_session(builder, query="deploy approval"), None)
        settings.context_engine_enabled = False
        probe.negative("disabled_engine_refuses_to_fabricate_handoff",
                       runner.compact_session(builder, query="deploy approval"), None)

        # Hostile input to the compaction path: an empty/garbage summary must not
        # erase the working history (anti-amnesia invariant).
        amnesia = ContextBuilder(ContextBudget(window=1024), "sys")
        amnesia.add_assistant("build failed: 3 tests red in app/calc.py")
        amnesia.apply_compaction("")
        probe.negative("empty_summary_refused_history_preserved",
                       {"history": len(amnesia.history), "summary": amnesia.summary,
                        "fact_kept": "app/calc.py" in amnesia.history[0].content},
                       {"history": 1, "summary": None, "fact_kept": True})
        amnesia.apply_compaction("Сводка: 3 теста красные в app/calc.py")
        probe.positive("valid_summary_replaces_history",
                       {"history": len(amnesia.history), "summary": amnesia.summary},
                       {"history": 0, "summary": "Сводка: 3 теста красные в app/calc.py"})
        gc.collect()  # drop the sqlite handle leaked by the failed ContextStore build

    probe.count(recoveries=6)
    probe.tag("RAW-FALLBACK", "DEGRADE-SAFE", "ANTI-AMNESIA")
    return probe.finish()


CASES = {
    "sandbox.working_state": working_state,
    "sandbox.context_selection": context_selection,
    "sandbox.raw_context_fallback": raw_context_fallback,
}
