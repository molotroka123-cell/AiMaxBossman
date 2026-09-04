"""BCC-V2-UNIVERSAL-ACTION-EXECUTION-P1-001 — Universal Action-Execution Contract.

Continuation of MODULE 1 (bcc/features/action_router.py, browser — NOT touched
or re-tested here) and BCC-V2-SESSION-20783913FA36-P1-FIX-001. Same invariant,
extended to the remaining EXISTING action-capable V2 executors via ONE shared
layer (bcc/features/action_contract.py) instead of one bespoke router per
module:

    SIDE_EFFECT_REQUIRED && VERIFIED_SIDE_EFFECT == FALSE -> TASK_SUCCESS == FALSE

Sections:
  CLASSIFY        — deterministic capability classifier, unit-level.
  TERMINAL_FILE    — real subprocess (project_host mode), real filesystem
                     state, matrix cases C/E from the mission's test matrix.
  MEMORY           — real durable memory write (bcc/v2 memory service),
                     matrix case I.
  FAMILY_MATRIX    — table-driven: for every OTHER wired capability
                     (apps/openclaw/opencode/github via terminal/mcp/plugin),
                     a manufactured tool_calls row of the right `source` +
                     status="executed" makes the gate NOT_APPLICABLE, and its
                     absence makes it FAIL — the same harness style
                     tests/test_action_gate.py already uses for browser
                     (TEST3), applied generically instead of once per module.
  CAPABILITY_UNAVAILABLE — images/workflow/schedules: classified, but no
                     ToolSpec of that family exists anywhere in this codebase
                     yet -> immediate honest `failed`, no pointless retry.
  COMPOUND          — a task whose review evidence has two required child
                     expectations, one satisfied and one not, cannot complete
                     (reuses bcc/v2/verification.verify_all, unchanged).
  CACHE_REPLAY      — a model's answer that merely echoes a PRIOR, unrelated
                     run's success text cannot complete a NEW run that has no
                     tool_calls row of its own.
  IDEMPOTENCY       — the new hooks are pure functions of (prompt, DB state):
                     calling them twice does not create duplicate tool_calls
                     or duplicate meta entries (restart/replay safety of this
                     layer itself; the engine's own checkpoint/resume and
                     OpenClaw dedup machinery is pre-existing and untouched).
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from bcc import db as dbm
from bcc.features import action_contract as ac

from .conftest import FakeAdapter
from .helpers import make_stack
from .test_v21_tool_loop import FINISHED, ToolAdapter, _run_task, _stack_with_tools

TERMINAL = ("completed", "failed", "stopped", "waiting_approval")


# --------------------------------------------------------------------- CLASSIFY

def test_classify_terminal_and_file():
    assert ac.classify("Создай файл hello.txt").name == "TERMINAL_FILE_ACTION"
    assert ac.classify("Запусти команду в терминале").name == "TERMINAL_FILE_ACTION"


def test_classify_apps():
    assert ac.classify("Открой приложение Калькулятор").name == "APPS_ACTION"


def test_classify_code_and_github_are_distinct():
    assert ac.classify("Исправь баг в коде").name == "CODE_ACTION"
    assert ac.classify("Запушь изменения в git").name == "GITHUB_ACTION"


def test_classify_mcp_and_plugin():
    assert ac.classify("Вызови MCP инструмент").name == "MCP_ACTION"
    assert ac.classify("Используй плагин погоды").name == "PLUGIN_ACTION"


def test_classify_openclaw_memory_images_workflow_schedules():
    assert ac.classify("Отправь сообщение в чат").name == "OPENCLAW_ACTION"
    assert ac.classify("Запомни это на будущее").name == "MEMORY_ACTION"
    assert ac.classify("Remember this fact").name == "MEMORY_ACTION"
    assert ac.classify("Сгенерируй картинку заката").name == "IMAGES_ACTION"
    assert ac.classify("Запусти workflow развёртывания").name == "WORKFLOW_ACTION"
    assert ac.classify("Напомни мне завтра").name == "SCHEDULES_ACTION"


def test_classify_leaves_informational_and_browser_prompts_alone():
    """A. informational text can complete (matrix item 1) — not this file's
    concern to enforce, but it must not misclassify and grab tools it
    shouldn't. Browser prompts are MODULE 1's exclusive territory
    (action_router.py) — this layer must stay silent on them."""
    assert ac.classify("Сделай краткое содержание статьи") is None
    assert ac.classify("Explain what this function does") is None
    assert ac.classify(
        "Открой на моём компьютере в браузере YouTube и включи Never Gonna Give You Up"
    ) is None


# ------------------------------------------------------------------ TERMINAL_FILE

async def _allow_root(env, path) -> None:
    import json
    async with env.svc.db.session() as s:
        await s.execute(sa.delete(dbm.settings_kv).where(dbm.settings_kv.c.key == "terminal.roots"))
        await s.execute(sa.insert(dbm.settings_kv).values(
            key="terminal.roots", value_enc=env.svc.vault.encrypt(json.dumps([str(path)]))))
        await s.commit()


async def test_terminal_text_only_claim_does_not_complete(env, tmp_path):
    """C/E from the mission's matrix: 'create hello.txt', model answers text
    only ('done'/'created' — never execution evidence). Must NOT complete."""
    work = tmp_path / "proj"
    work.mkdir()
    await _allow_root(env, work)
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter(
        "Готово, я создал файл hello.txt с нужным содержимым.")
    stack = await make_stack(env.client, prompt="Создай файл hello.txt через терминал", max_steps=4)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}",
                           json={"permissions": {"terminal.run": True}})

    status = await _run_task(env, stack["task"]["id"], timeout=15, until=FINISHED)
    assert status == "failed"
    assert not (work / "hello.txt").exists()


async def test_terminal_real_execution_with_matching_file_completes(env, tmp_path):
    """Real subprocess (project_host — no docker needed), real filesystem
    state, evidence auto-derived from the task text (bcc/features/
    action_contract._terminal_evidence: 'hello.txt' literally in the prompt)
    and verified fresh by the existing review_gate/verification.py pipeline —
    no second verifier built."""
    work = tmp_path / "proj"
    work.mkdir()
    await _allow_root(env, work)

    create_cmd = "python -c \"open('hello.txt','w').write('hi')\""
    adapter = ToolAdapter([
        ("tool", "terminal_run", {"command": create_cmd, "mode": "project_host", "cwd": str(work)}),
        ("text", "Готово, файл создан."),
    ])
    stack = await _stack_with_tools(env, ["terminal.run"], adapter=adapter,
                                    prompt="Создай файл hello.txt через терминал", max_steps=6)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}",
                           json={"permissions": {"terminal.run": True}})

    # project_host is always an ASK boundary regardless of permission (see
    # tests/test_v21_tools_terminal_browser.py) — approve, then let it finish.
    assert await _run_task(env, stack["task"]["id"], timeout=15) == "waiting_approval"
    approval = (await env.client.get("/api/approvals")).json()[0]
    await env.client.post(f"/api/approvals/{approval['id']}", json={"approve": True, "by": "test"})

    status = await _run_task(env, stack["task"]["id"], timeout=15, until=FINISHED)
    assert (work / "hello.txt").read_text(encoding="utf-8") == "hi"
    assert status == "completed"


# ---------------------------------------------------------------------- MEMORY

async def test_memory_text_only_claim_does_not_complete(env, tmp_path):
    """I from the mission's matrix: model says 'запомнил' with no durable
    write. Must NOT complete."""
    vault = tmp_path / "vault"
    vault.mkdir()
    await env.client.post("/api/memory/config", json={"root": str(vault)})
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("Запомнил, учту в следующий раз.")
    stack = await make_stack(env.client, prompt="Запомни это на будущее: релиз по пятницам",
                             max_steps=4)

    status = await _run_task(env, stack["task"]["id"], timeout=15, until=FINISHED)
    assert status == "failed"


async def test_memory_real_write_completes(env, tmp_path):
    """Real durable write via the existing memory service — no new storage
    built. Text explaining 'I remembered' is irrelevant; the tool_calls row
    (source=memory, status=executed) is what the gate looks at."""
    vault = tmp_path / "vault"
    vault.mkdir()
    await env.client.post("/api/memory/config", json={"root": str(vault)})

    adapter = ToolAdapter([
        ("tool", "memory_write", {"title": "Релизы", "content": "Релиз по пятницам.",
                                  "kind": "fact"}),
        ("text", "Запомнил."),
    ])
    stack = await _stack_with_tools(env, ["memory.write"], adapter=adapter,
                                    prompt="Запомни это на будущее: релиз по пятницам",
                                    max_steps=6)
    # filesystem.write is DANGEROUS (bcc/permissions.py) — granting it to the
    # agent moves memory.write's default_effect ("ask") to "auto" (bcc.tools.
    # decide_effect: an explicitly granted permission wins over the tool's
    # own ask default), same as the owner pre-approving this agent for it.
    await env.client.patch(f"/api/agents/{stack['agent']['id']}",
                           json={"permissions": {"filesystem.write": True}})

    status = await _run_task(env, stack["task"]["id"], timeout=15, until=FINISHED)
    assert status == "completed"
    async with env.svc.db.session() as s:
        rows = (await s.execute(sa.select(dbm.tool_calls).where(
            dbm.tool_calls.c.tool == "memory.write"))).fetchall()
    assert len(rows) == 1 and rows[0]._mapping["status"] == "executed"


# ------------------------------------------------------------------ FAMILY_MATRIX

async def _run_once(env):
    for _ in range(10):
        run_id = await env.svc.engine.claim()
        if run_id is None:
            return
        await env.svc.engine.execute(run_id)


async def _drive_with_approvals(env, task_id, *, ticks: int = 30) -> str | None:
    """Крутит настоящий воркер и одобряет всё, что просит подтверждения.

    Нужен там, где путь задачи содержит ASK-границу (project_host у
    terminal.run — всегда ASK) И последующий повтор после вето гейта:
    одного `_run_task` мало, потому что после одобрения задача уходит на
    второй круг, где гейт выносит окончательный вердикт."""
    import asyncio
    env.svc.engine.poll_interval = 0.02
    worker = asyncio.create_task(env.svc.engine.worker_loop())
    watcher = asyncio.create_task(env.svc.engine.approval_watcher())
    status = None
    try:
        for _ in range(ticks):
            await asyncio.sleep(0.2)
            task = (await env.client.get(f"/api/tasks/{task_id}")).json()["task"]
            status = task["status"]
            if status in ("completed", "failed", "stopped"):
                break
            for a in (await env.client.get("/api/approvals?status=pending")).json():
                await env.client.post(f"/api/approvals/{a['id']}",
                                      json={"approve": True, "by": "test"})
    finally:
        worker.cancel()
        watcher.cancel()
        await asyncio.gather(worker, watcher, return_exceptions=True)
    return status


# Имена — НАСТОЯЩИЕ неЧИТАЮЩИЕ инструменты каждого семейства: с тех пор как
# read-инструменты семейства перестали засчитываться как доказательство
# (memory.search != memory.write, перечисление инструментов MCP != выполненное
# им действие), подделать доказательство несуществующим именем `.probe` уже
# нельзя — и это ровно то поведение, которое здесь и проверяется.
FAMILY_CASES = [
    ("apps", "apps.start", "Открой приложение Калькулятор"),
    ("openclaw", "openclaw.send", "Отправь сообщение в чат"),
    ("opencode", "opencode.send", "Исправь баг в коде"),
    ("plugin", "plugin:telegram.send", "Используй плагин погоды"),
]


async def _family_case(env, source: str, prompt: str, *, tool: str | None,
                       status_value: str = "executed") -> str:
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("Готово, сделано.")
    stack = await make_stack(env.client, prompt=prompt, max_steps=4)
    if tool is not None:
        run_id = await env.svc.engine.claim()
        assert run_id is not None
        async with env.svc.db.session() as s:
            await s.execute(sa.insert(dbm.tool_calls).values(
                run_id=run_id, task_id=stack["task"]["id"], tool=tool,
                source=source, status=status_value))
            await s.commit()
        await env.svc.engine.execute(run_id)
        # После вето гейта с requeue задача возвращается в очередь; догоняем её
        # до терминального состояния, иначе тест увидит промежуточный "queued".
        await _run_once(env)
    else:
        await _run_once(env)
    task = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()["task"]
    return task["status"]


@pytest.mark.parametrize("source,tool,prompt", FAMILY_CASES)
async def test_family_matrix_no_call_does_not_complete(env, source, tool, prompt):
    """B/D/G/H from the mission's matrix, generically: text claiming success
    for a wired capability's tool family, with zero real tool_calls of that
    family in this run, must not complete — regardless of which family."""
    status = await _family_case(env, source, prompt, tool=None)
    assert status == "failed", f"{source}: {status}"


@pytest.mark.parametrize("source,tool,prompt", FAMILY_CASES)
async def test_family_matrix_executed_call_is_not_applicable(env, source, tool, prompt):
    """A genuinely executed tool_calls row of the right family clears the
    veto (deeper verification, where wired, is review_gate's job — this gate
    only rules out the zero-attempt case, exactly like action_gate.py)."""
    status = await _family_case(env, source, prompt, tool=tool)
    assert status == "completed", f"{source}: {status}"


@pytest.mark.parametrize("source,tool,prompt", FAMILY_CASES)
async def test_family_matrix_unknown_tool_name_is_not_proof(env, source, tool, prompt):
    """Строка tool_calls с именем, которого нет в реестре как неЧИТАЮЩЕГО
    инструмента семейства, доказательством не является: иначе достаточно было
    бы записать любое имя с нужным `source`."""
    status = await _family_case(env, source, prompt, tool=f"{source}.probe")
    assert status == "failed", f"{source}: {status}"


async def test_family_matrix_rejected_or_errored_call_is_not_evidence(env):
    """A tool call that was attempted but DENIED or FAILED is not execution
    evidence either (the critical universal rule: an unsuccessful attempt is
    not proof the side effect happened) — status must be 'executed', not
    merely present."""
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("Готово.")
    stack = await make_stack(env.client, prompt="Открой приложение Калькулятор", max_steps=4)
    run_id = await env.svc.engine.claim()
    assert run_id is not None
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(dbm.tool_calls).values(
            run_id=run_id, task_id=stack["task"]["id"], tool="apps.start",
            source="apps", status="error"))
        await s.commit()
    await env.svc.engine.execute(run_id)
    task = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()["task"]
    assert task["status"] != "completed"


# --------------------------------------------- READ-ONLY / DENIED / PENDING

async def test_readonly_family_call_is_not_execution_proof(env, tmp_path):
    """TEST I класса «discovery != action»: инструмент того же семейства, но
    ЧИТАЮЩИЙ (memory.search вместо memory.write; перечисление инструментов
    MCP-сервера вместо выполненного им действия), доказательством быть не
    может. До этой правки `_has_family_tool_call` смотрел только на `source`,
    и любой read-вызов семейства снимал вето."""
    vault = tmp_path / "vault"
    vault.mkdir()
    await env.client.post("/api/memory/config", json={"root": str(vault)})
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("Запомнил ALPHA-742.")
    stack = await make_stack(env.client, prompt="Запомни: ALPHA-742", max_steps=4)

    run_id = await env.svc.engine.claim()
    assert run_id is not None
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(dbm.tool_calls).values(
            run_id=run_id, task_id=stack["task"]["id"], tool="memory.search",
            source="memory", status="executed"))
        await s.commit()
    await env.svc.engine.execute(run_id)
    await _run_once(env)

    task = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()["task"]
    assert task["status"] == "failed"


async def test_denied_action_is_blocked_not_completed(env, tmp_path):
    """TEST M: политика запретила действие. Итог обязан быть правдивым —
    строка tool_calls со status="rejected" не является исполнением."""
    work = tmp_path / "proj"
    work.mkdir()
    await _allow_root(env, work)
    status = await _family_case(env, "terminal", "Через терминал создай папку bossman_test",
                                tool="terminal.run", status_value="rejected")
    assert status == "failed"


async def test_pending_approval_never_reports_completed(env, tmp_path):
    """TEST O: инструмент ждёт подтверждения владельца и ещё не исполнялся.
    Задача обязана стоять в waiting_approval, а не завершиться."""
    work = tmp_path / "proj"
    work.mkdir()
    await _allow_root(env, work)
    adapter = ToolAdapter([
        ("tool", "terminal_run", {"command": "python -c \"open('x.txt','w').write('1')\"",
                                  "mode": "project_host", "cwd": str(work)}),
        ("text", "Готово."),
    ])
    stack = await _stack_with_tools(env, ["terminal.run"], adapter=adapter,
                                    prompt="Создай файл x.txt через терминал", max_steps=6)
    # Право НЕ выдаётся: project_host — ASK-граница, подтверждения не даём.
    status = await _run_task(env, stack["task"]["id"], timeout=15)
    assert status == "waiting_approval"
    assert not (work / "x.txt").exists()


# ------------------------------------------------------------ CAPABILITY_UNAVAILABLE

UNAVAILABLE_CASES = [
    "Сгенерируй картинку заката",
    "Запусти workflow развёртывания",
    "Напомни мне завтра",
    # Ни одного MCP-сервера в тестовом окружении не подключено, значит ни одного
    # инструмента source="mcp" в реестре нет — capability честно недоступна.
    # Подключённый сервер регистрирует свои инструменты, и MCP_ACTION начинает
    # работать как остальные семейства, без изменений в коде.
    "Вызови MCP инструмент",
    # coding_sessions.py — только HTTP-ручки, ни одного ToolSpec: модель не может
    # создать сессию в принципе. До этой правки фраза вообще не классифицировалась
    # и завершалась текстом — последний известный путь «текст => успех».
    "Создай отдельную coding session",
]


@pytest.mark.parametrize("prompt", UNAVAILABLE_CASES)
async def test_capability_unavailable_fails_immediately_without_retry(env, prompt):
    """images/workflow/schedules(missions): classified, but no ToolSpec of
    that family is registered anywhere in this codebase yet. Must reach an
    honest terminal `failed` on the FIRST attempt — not an endless retry
    loop asking the model to call a tool that structurally does not exist."""
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("Готово, сделано.")
    stack = await make_stack(env.client, prompt=prompt, max_steps=4)
    await _run_once(env)
    task = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()["task"]
    assert task["status"] == "failed", f"{prompt!r}: {task['status']}"

    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(dbm.tasks.c.meta).where(
            dbm.tasks.c.id == stack["task"]["id"]))).first()
    # no pointless self-correction attempt was recorded — a single pass, not a loop
    assert int((row._mapping["meta"] or {}).get(ac.META_KEY, 0)) == 0


# ---------------------------------------------------------------------- COMPOUND

async def test_compound_action_fails_if_one_required_child_fails(env, tmp_path):
    """5 from the mission's regression matrix: two required child
    expectations (kind=file, kind=app), one genuinely satisfied and one not
    — reuses bcc/v2/verification.verify_all (unchanged; 'any FAILED ->
    FAILED') instead of inventing new compound-tracking logic."""
    work = tmp_path / "proj"
    work.mkdir()
    (work / "part1.txt").write_text("done", encoding="utf-8")
    await _allow_root(env, work)

    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("Готово: часть 1 и часть 2 выполнены.")
    stack = await make_stack(env.client, prompt="составной план", max_steps=1)
    await env.client.post("/api/review/enable", json={
        "task_id": stack["task"]["id"],
        "evidence": [
            {"kind": "file", "target": str(work / "part1.txt"), "expect": {"exists": True}},
            {"kind": "app", "target": "nonexistent-app-id", "expect": {"running": True}},
        ],
    })
    await _run_once(env)
    task = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()["task"]
    assert task["status"] != "completed"


async def test_classify_all_covers_every_capability_a_compound_prompt_touches():
    """Regression for the owner's live-probe finding: 'Измени тестовый файл в
    репозитории, запусти тест и закоммить' touches BOTH TERMINAL_FILE_ACTION
    (edit + test) and GITHUB_ACTION (commit) — classify() (first match only)
    silently dropped the GITHUB half; classify_all must return both."""
    caps = [c.name for c in ac.classify_all(
        "Измени тестовый файл в репозитории, запусти тест и закоммить")]
    assert caps == ["TERMINAL_FILE_ACTION", "GITHUB_ACTION"]


async def test_compound_terminal_and_github_needs_a_real_git_mutation(env, tmp_path):
    """Owner's live-probe finding, reproduced end-to-end: TERMINAL_FILE_ACTION
    and GITHUB_ACTION share tool_sources={"terminal"} (this codebase has no
    dedicated git tool — git runs through terminal.run). A genuinely executed
    but UNRELATED terminal.run (e.g. `echo hi`) together with a text claim of
    'file edited, tests passed, pushed to git' must NOT complete: it clears
    TERMINAL_FILE_ACTION's veto but not GITHUB_ACTION's — Capability.call_filter
    (_is_git_mutation_call) inspects the *args* of the executed call, not just
    its tool family, specifically to catch this."""
    work = tmp_path / "proj"
    work.mkdir()
    await _allow_root(env, work)

    adapter = ToolAdapter([
        ("tool", "terminal_run", {"command": "echo hi", "mode": "project_host", "cwd": str(work)}),
        ("text", "Файл изменён, тесты прошли, изменения запушены в git."),
    ])
    stack = await _stack_with_tools(env, ["terminal.run"], adapter=adapter,
                                    prompt="Измени тестовый файл в репозитории, запусти тест и закоммить",
                                    max_steps=10)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}",
                           json={"permissions": {"terminal.run": True}})

    status = await _drive_with_approvals(env, stack["task"]["id"])
    assert status == "failed"
    async with env.svc.db.session() as s:
        events = (await s.execute(sa.select(dbm.events).where(
            dbm.events.c.kind == "action_contract.blocked"))).fetchall()
    assert any(e._mapping["data"].get("capabilities") == ["GITHUB_ACTION"] for e in events)


async def test_schedules_classifier_catches_automation_phrasing():
    """Regression for the owner's live-probe finding: 'Создай тестовую
    automation на безопасное время' was not classified at all (bare
    'automation' wasn't in the SCHEDULES pattern), so a text-only 'готово'
    silently completed it. Also verified end-to-end below."""
    assert ac.classify("Создай тестовую automation на безопасное время").name == "SCHEDULES_ACTION"


async def test_schedules_automation_phrasing_does_not_falsely_complete(env):
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("Готово, автоматизация настроена.")
    stack = await make_stack(env.client, prompt="Создай тестовую automation на безопасное время",
                             max_steps=4)
    await _run_once(env)
    task = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()["task"]
    assert task["status"] == "failed"


# ------------------------------------------------------------------- CODE_ACTION

@pytest.mark.parametrize("command", [
    "echo hi", "ls -la", "git status", "git log --oneline -5", "pytest -q",
    "cat calc.py", "grep -rn bug .", "true", "ruff check .",
])
def test_readonly_commands_are_not_code_mutations(command):
    """Читающие команды и прогон тестов — не починка кода. Именно этим
    классом команд обходился CODE_ACTION до появления call_filter."""
    assert ac._is_code_mutation_call(
        {"tool": "terminal.run", "source": "terminal", "args": {"command": command}}) is False


@pytest.mark.parametrize("command", [
    "python edit_calc.py",          # канонический путь правки в тестах этой кодовой базы
    "sed -i s/a/b/ calc.py", "echo fixed > calc.py", "git apply fix.patch",
    "make build", "cp new.py calc.py",
])
def test_real_edit_commands_count_as_code_mutations(command):
    """Обратная сторона того же выбора: настоящая правка незнакомой формы не
    должна ошибочно блокироваться (см. _looks_like_mutation)."""
    assert ac._is_code_mutation_call(
        {"tool": "terminal.run", "source": "terminal", "args": {"command": command}}) is True


def test_opencode_call_is_code_proof_by_itself():
    """opencode.* — исполнитель, созданный ровно для правки кода: отдельной
    проверки команды ему не нужно."""
    assert ac._is_code_mutation_call(
        {"tool": "opencode.send", "source": "opencode", "args": {"text": "почини"}}) is True


async def test_code_action_not_satisfied_by_an_unrelated_terminal_call(env, tmp_path):
    """Тот же класс обхода, что владелец нашёл живым пробоем для GITHUB, но
    для CODE_ACTION: модель реально дёрнула terminal.run (`git status`),
    ничего не починила и заявила текстом «баг исправлен». Завершаться не
    должно."""
    work = tmp_path / "repo"
    work.mkdir()
    await _allow_root(env, work)

    adapter = ToolAdapter([
        ("tool", "terminal_run", {"command": "git status", "mode": "project_host",
                                  "cwd": str(work)}),
        ("text", "Баг в коде исправлен, всё готово."),
    ])
    stack = await _stack_with_tools(env, ["terminal.run"], adapter=adapter,
                                    prompt="Исправь баг в коде", max_steps=10)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}",
                           json={"permissions": {"terminal.run": True}})
    status = await _drive_with_approvals(env, stack["task"]["id"])
    assert status == "failed"

    async with env.svc.db.session() as s:
        events = (await s.execute(sa.select(dbm.events).where(
            dbm.events.c.kind == "action_contract.blocked"))).fetchall()
    assert any(e._mapping["data"].get("capabilities") == ["CODE_ACTION"] for e in events)


async def test_code_action_satisfied_by_a_real_edit(env, tmp_path):
    """Позитивная сторона: настоящая правка файла через terminal.run
    закрывает CODE_ACTION — фильтр не блокирует реальную работу."""
    work = tmp_path / "repo"
    work.mkdir()
    (work / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    await _allow_root(env, work)

    fix = "python -c \"open('calc.py','w').write('def add(a, b):\\n    return a + b\\n')\""
    adapter = ToolAdapter([
        ("tool", "terminal_run", {"command": fix, "mode": "project_host", "cwd": str(work)}),
        ("text", "Баг исправлен."),
    ])
    stack = await _stack_with_tools(env, ["terminal.run"], adapter=adapter,
                                    prompt="Исправь баг в коде", max_steps=10)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}",
                           json={"permissions": {"terminal.run": True}})
    status = await _drive_with_approvals(env, stack["task"]["id"])
    assert status == "completed"
    assert "a + b" in (work / "calc.py").read_text(encoding="utf-8")


# -------------------------------------------------------------------- CACHE_REPLAY

async def test_cached_success_text_cannot_complete_an_unrelated_run(env):
    """6 from the mission's regression matrix: a model answer that echoes a
    PRIOR run's real success ('Готово, файл создан, как и в прошлый раз')
    carries no evidence for THIS run — _has_family_tool_call is scoped to
    run_id, so a cached/replayed claim about a different run cannot forge
    this one's completion."""
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter(
        "Готово, файл создан — как и в прошлый раз, всё сделано успешно.")
    stack = await make_stack(env.client, prompt="Создай файл again.txt через терминал", max_steps=4)
    await _run_once(env)
    task = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()["task"]
    assert task["status"] == "failed"


# ------------------------------------------------------- RETRY BOUND / RESTART

async def test_gate_failure_terminates_and_does_not_loop_forever(env, tmp_path):
    """PHASE 8: путь FAIL гейта обязан сходиться. Модель бесконечно отвечает
    текстом «готово» — задача обязана прийти в терминальное состояние за
    ограниченное число прогонов (одна попытка самокоррекции, затем честный
    отказ), а не крутить бесконечный requeue и жечь вызовы модели."""
    work = tmp_path / "proj"
    work.mkdir()
    await _allow_root(env, work)
    adapter = FakeAdapter("Готово, файл создан.")
    env.svc.registry.adapter_factory = lambda m, p: adapter
    stack = await make_stack(env.client, prompt="Создай файл loop.txt через терминал",
                             max_steps=4)

    for _ in range(30):                     # заведомо больше любого разумного лимита
        run_id = await env.svc.engine.claim()
        if run_id is None:
            break
        await env.svc.engine.execute(run_id)

    task = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()["task"]
    assert task["status"] == "failed"
    async with env.svc.db.session() as s:
        runs = (await s.execute(sa.select(dbm.task_runs).where(
            dbm.task_runs.c.task_id == stack["task"]["id"]))).fetchall()
    assert len(runs) <= 3, f"слишком много прогонов: {len(runs)}"
    assert adapter.calls <= 4, f"слишком много вызовов модели: {adapter.calls}"


async def test_restart_does_not_repeat_a_completed_side_effect(tmp_path):
    """PHASE 9: DUPLICATE_SIDE_EFFECT_COUNT=0 после изменений контракта.

    Реальный внешний эффект (дозапись в файл — повтор был бы виден как второй
    символ), затем остановка Services и запуск заново ПРОТИВ ТОЙ ЖЕ durable
    базы, затем попытка возобновить работу. Уже выполненное действие не должно
    выполниться второй раз."""
    from .conftest import make_settings, start_app, client_for

    settings = make_settings(tmp_path)
    work = tmp_path / "proj"
    work.mkdir()
    log = work / "log.txt"
    append = "python -c \"open('log.txt','a').write('X')\""

    app, svc = await start_app(settings, start_workers=False)
    async with client_for(app, svc) as client:
        class _Env:
            pass
        env = _Env()
        env.client, env.svc = client, svc
        await _allow_root(env, work)
        adapter = ToolAdapter([
            ("tool", "terminal_run", {"command": append, "mode": "project_host",
                                      "cwd": str(work)}),
            ("text", "Готово."),
        ])
        stack = await _stack_with_tools(env, ["terminal.run"], adapter=adapter,
                                        prompt="Создай файл log.txt через терминал",
                                        max_steps=8)
        await client.patch(f"/api/agents/{stack['agent']['id']}",
                           json={"permissions": {"terminal.run": True}})
        assert await _drive_with_approvals(env, stack["task"]["id"]) == "completed"
        assert log.read_text(encoding="utf-8") == "X"
        task_id = stack["task"]["id"]
    await svc.stop()

    # Перезапуск против той же durable базы.
    app2, svc2 = await start_app(settings, start_workers=False)
    async with client_for(app2, svc2) as client2:
        class _Env2:
            pass
        env2 = _Env2()
        env2.client, env2.svc = client2, svc2
        svc2.registry.adapter_factory = lambda m, p: FakeAdapter("Готово.")
        await _run_once(env2)               # ничего исполнять не должно
        task = (await client2.get(f"/api/tasks/{task_id}")).json()["task"]
        assert task["status"] == "completed"
        async with svc2.db.session() as s:
            calls = (await s.execute(sa.select(dbm.tool_calls).where(
                dbm.tool_calls.c.task_id == task_id))).fetchall()
    await svc2.stop()

    assert log.read_text(encoding="utf-8") == "X", "side effect повторился после рестарта"
    assert len(calls) == 1, f"дубликат чека исполнения: {len(calls)}"


# --------------------------------------------------------------------- IDEMPOTENCY

async def test_hooks_are_idempotent_no_duplicate_tool_calls_or_meta(env):
    """7 from the mission's regression matrix, scoped to what this layer
    owns: before_run/gate_completion are pure functions of (prompt, DB
    state) — running them again (as a restart/resume would) must not insert
    a second tool_calls row or grow meta.allowed_tools. The engine's own
    checkpoint/resume and OpenClaw restart/dedup machinery is pre-existing
    and unmodified by this patch; not re-verified here."""
    stack = await make_stack(env.client, prompt="Создай файл dup.txt через терминал", max_steps=4)
    async with env.svc.db.session() as s:
        task = dict((await s.execute(sa.select(dbm.tasks).where(
            dbm.tasks.c.id == stack["task"]["id"]))).first()._mapping)
        run = dict((await s.execute(sa.select(dbm.task_runs).where(
            dbm.task_runs.c.task_id == stack["task"]["id"]))).first()._mapping)

    hook = await ac._before_run(env.svc)
    await hook(task, run)
    await hook(task, run)  # simulate a second before_run pass (retry/resume)

    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(dbm.tasks.c.meta).where(
            dbm.tasks.c.id == stack["task"]["id"]))).first()
    meta = row._mapping["meta"]
    expected = ac._family_tool_names(frozenset({"terminal"}))
    assert sorted(meta["allowed_tools"]) == sorted(expected)  # not duplicated by the 2nd pass

    # gate_completion has a stateful one-retry counter (meta[ac.META_KEY]),
    # same shape as action_gate.py: the two calls are legitimately NOT
    # identical (1st -> retry, 2nd -> terminal) — that is the intended
    # single-retry-then-honest-failure behavior, not a duplicate side
    # effect. What must hold is: neither call ever inserts a tool_calls row
    # itself (the gate only reads evidence, it is never the executor), and
    # the attempt counter advances by exactly one per call, not more.
    gate = await ac._gate(env.svc)
    v1 = await gate(task, run["id"], "готово")
    assert v1["verdict"] == "FAIL" and v1.get("requeue") is True
    task["meta"] = await ac._meta(env.svc, task["id"])
    v2 = await gate(task, run["id"], "готово")
    assert v2["verdict"] == "FAIL" and v2.get("requeue") is False
    async with env.svc.db.session() as s:
        calls = (await s.execute(sa.select(dbm.tool_calls).where(
            dbm.tool_calls.c.run_id == run["id"]))).fetchall()
        attempts = (await s.execute(sa.select(dbm.tasks.c.meta).where(
            dbm.tasks.c.id == task["id"]))).first()._mapping["meta"].get(ac.META_KEY)
    assert calls == []  # the gate itself never inserts a tool_calls row
    assert attempts == 1  # bumped exactly once, by the 1st (retryable) call only
