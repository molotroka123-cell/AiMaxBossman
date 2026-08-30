"""Phase 1 — Context OS: hierarchical, decision/failure stores, compiler, state machine."""
import pytest

from bcc.context_os import (
    ContextCompiler,
    DecisionStore,
    FailureStore,
    HierarchicalContextManager,
    StateMachine,
    TokenBudgeter,
)


def test_token_budgeter_hard_cut():
    from bcc.context_os.hierarchical import ContextLayer
    layers = [
        ContextLayer("global", "G" * 400, 100, "h1", True),
        ContextLayer("task", "T" * 400, 100, "h2", False),
    ]
    out = TokenBudgeter.budget(layers, max_tokens=150)
    assert sum(l.tokens_est for l in out) <= 150
    assert out[0].name == "global"
    assert "truncated" in out[-1].text


async def test_hierarchical_assemble_and_cache(tmp_path):
    async def task_loader(tid):
        return f"task {tid} objective"

    hcm = HierarchicalContextManager(
        global_text="global invariants",
        task_loader=task_loader,
    )
    layers = await hcm.assemble(task_id=1, max_tokens=8000)
    assert any(l.name == "global" and "global invariants" in l.text for l in layers)
    assert any(l.name == "task" and "task 1" in l.text for l in layers)
    # cache hit — same hash object
    l2 = hcm.get_global()
    l3 = hcm.get_global()
    assert l2.hash == l3.hash


async def test_decision_store_crud(tmp_path):
    from bcc.config import Settings
    from bcc.db import Database

    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    settings = Settings(data_dir=tmp_path / "data", database_url=f"sqlite+aiosqlite:///{tmp_path/'data/bcc.db'}", ui_dir=tmp_path/"ui")
    db = Database(settings.database_url)
    await db.create_all()
    store = DecisionStore(db)
    d = await store.add(key="use-existing-stage13@v1", decision="use existing Stage13",
                        reason="avoid second PC engine", alternatives_rejected=["custom pyautogui"], scope="Bossman V1")
    assert d["key"] == "use-existing-stage13@v1"
    got = await store.get("use-existing-stage13@v1")
    assert got["decision"] == "use existing Stage13"
    lst = await store.list(scope="Bossman V1")
    assert len(lst) == 1
    await db.close()


async def test_failure_store_search(tmp_path):
    from bcc.config import Settings
    from bcc.db import Database
    (tmp_path / "data2").mkdir(parents=True, exist_ok=True)
    settings = Settings(data_dir=tmp_path / "data2", database_url=f"sqlite+aiosqlite:///{tmp_path/'data2/bcc.db'}", ui_dir=tmp_path/"ui")
    db = Database(settings.database_url)
    await db.create_all()
    store = FailureStore(db)
    await store.add(symptom="git merge orphan", root_cause="detached worktree", attempted_fix="update-ref", result="fixed", files=["coding_session.py"])
    res = await store.search("merge orphan")
    assert len(res) == 1 and res[0]["symptom"] == "git merge orphan"
    await db.close()


async def test_compiler_assembles_with_include(tmp_path):
    from bcc.config import Settings
    from bcc.db import Database
    (tmp_path / "data3").mkdir(parents=True, exist_ok=True)
    settings = Settings(data_dir=tmp_path / "data3", database_url=f"sqlite+aiosqlite:///{tmp_path/'data3/bcc.db'}", ui_dir=tmp_path/"ui")
    db = Database(settings.database_url)
    await db.create_all()
    hcm = HierarchicalContextManager(global_text="inv", task_loader=lambda tid: "task objective")
    dec = DecisionStore(db)
    fail = FailureStore(db)
    await dec.add(key="d1", decision="use Stage13", reason="avoid dup")
    await fail.add(symptom="timeout", root_cause="slow model")
    compiler = ContextCompiler(hcm, dec, fail)
    ctx = await compiler.request(task_id=1, objective="fix bug", max_tokens=8000,
                                 include=["decisions", "recent_failures", "next_action"],
                                 available_tools=["code.diff"])
    assert "INVARIANTS" in ctx.prompt
    assert "DECISIONS" in ctx.prompt and "use Stage13" in ctx.prompt
    assert "RECENT_FAILURES" in ctx.prompt
    assert "NEXT_ACTION" in ctx.prompt
    assert ctx.tokens_est <= 8000
    # include whitelist — no leakage
    ctx2 = await compiler.request(task_id=1, objective="x", max_tokens=8000, include=[])
    assert "DECISIONS" not in ctx2.prompt
    await db.close()


def test_state_machine_transitions_and_checkpoint():
    sm = StateMachine("PLAN")
    sm.transition("EXECUTE")
    sm.transition("OBSERVE")
    sm.transition("VERIFY")
    assert sm.state == "VERIFY"
    cp = sm.checkpoint()
    sm2 = StateMachine.from_checkpoint(cp)
    assert sm2.state == "VERIFY" and sm2.history == sm.history
    # illegal
    with pytest.raises(ValueError):
        sm.transition("EXECUTE")  # VERIFY -> EXECUTE not allowed
    # recover path
    sm.transition("RECOVER")
    sm.transition("PLAN")
    assert sm.state == "PLAN"
