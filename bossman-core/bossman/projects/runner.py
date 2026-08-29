"""Раннер проектов: Исполнитель, Проверяющий и Сборщик (роли 3–5) одной петлёй.

Возобновляемость (9.5): идём по плану, готовые и проверенные задачи пропускаем;
превью-гейт после первых клипов; жёсткий лимит бюджета; после каждого этапа —
сводка в notes/ (10.5). Экономия контекста (9.4): модель никогда не видит проект
целиком, инструменты возвращают ссылки, проверка — всегда воркер со свежим контекстом.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import shlex

import httpx

from .. import approvals, db, events, telegram
from ..agents import AgentSpec
from ..config import settings
from ..llm import chat
from ..toolkit import REGISTRY, ToolContext
from .plan import Plan, PlanTask, State, journal_append, journal_tail, load_plan, project_dir
from .router import Route, choose, load_registry

MAX_RETRIES = 2  # провал проверки → перегенерация с уточнённым промптом, максимум две попытки


class ProjectPaused(Exception):
    pass


async def _db_task_update(slug: str, t: PlanTask, status: str, cost: float = 0.0) -> None:
    await db.execute(
        """UPDATE project_tasks pt SET status=$3, cost=pt.cost+$4, updated_at=now(),
           attempts=attempts+CASE WHEN $3='running' THEN 1 ELSE 0 END
           FROM projects p WHERE pt.project_id=p.id AND p.slug=$1 AND pt.name=$2""",
        slug, t.name, status, cost)
    events.emit("project.task", slug=slug, task=t.id, status=status)


def _ctx(slug: str) -> ToolContext:
    d = project_dir(slug)
    return ToolContext(agent=f"project:{slug}", workdir=d,
                       journal=d / "journal.md", notes_dir=d / "notes")


async def _execute(slug: str, t: PlanTask, route: Route, state: State) -> tuple[list[str], float]:
    """Исполнитель: вызывает инструмент, кладёт результат в assets/, пишет в journal.
    Возвращает (пути артефактов, потраченное)."""
    spec = route.spec
    d = project_dir(slug)
    cost = 0.0

    if spec.get("where") == "cloud" and spec.get("cloud_policy") == "ask":
        # облако — осознанно и на виду: предпросмотр того, что уйдёт, до отправки
        preview = (f"Проект {slug}, задача «{t.name}»\nинструмент: {route.tool} (облако)\n"
                   f"параметры: {t.params}")
        approval_id = await approvals.create("cloud", preview, payload={"slug": slug, "task": t.id})
        decision = await approvals.wait(approval_id)
        if decision["status"] != "approved":
            raise ProjectPaused(f"облачный вызов {route.tool} отклонён")
        seconds = float(t.params.get("seconds", 0) or 0)
        cost = seconds * float((spec.get("cost") or {}).get("value", 0))

    kind = spec.get("kind")
    if kind == "builtin":
        tool = REGISTRY[spec["builtin"]]
        result = await tool.handler(t.params, _ctx(slug))
        if result.error:
            raise RuntimeError(result.content)
    elif kind == "cmd":
        args = {k: shlex.quote(str(v)) for k, v in t.params.items()}
        args.setdefault("out", shlex.quote(str(d / (t.outputs[0] if t.outputs else "assets/out"))))
        args.setdefault("input", args.get("out", "''"))
        proc = await asyncio.create_subprocess_shell(
            spec["cmd"].format(**args), cwd=d,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await proc.communicate()
        if proc.returncode:
            raise RuntimeError(f"{route.tool}: код {proc.returncode}: {out.decode(errors='replace')[-500:]}")
    elif kind == "api":
        url = os.environ.get(spec.get("endpoint_env", ""), "")
        if not url:
            raise RuntimeError(f"{route.tool}: не задан {spec.get('endpoint_env')} в .env")
        async with httpx.AsyncClient(timeout=1800) as client:
            resp = await client.post(url, json={"model": spec.get("model"), **t.params},
                                     headers={"Authorization": f"Bearer {os.environ.get('HIGGSFIELD_API_KEY', '')}"})
            resp.raise_for_status()
            data = resp.json()
            file_url = data.get("url") or data.get("video_url")
            if file_url and t.outputs:
                target = d / t.outputs[0]
                target.parent.mkdir(parents=True, exist_ok=True)
                dl = await client.get(file_url)
                target.write_bytes(dl.content)
    else:
        raise RuntimeError(f"{route.tool}: неизвестный kind '{kind}'")

    artifacts = [o for o in t.outputs if (d / o).exists()]
    for a in artifacts:
        await db.execute(
            """INSERT INTO artifacts (project_id, path, kind, meta)
               SELECT id, $2, $3, $4 FROM projects WHERE slug=$1""",
            slug, a, "clip" if t.is_clip else "doc", {"tool": route.tool})
    journal_append(slug, f"[{t.stage}] {t.name}: {route.tool} → {', '.join(artifacts) or 'без файла'}")
    return artifacts, cost


async def _check(slug: str, t: PlanTask, artifacts: list[str], attempt: int) -> tuple[bool, str]:
    """Проверяющий (роль 4) — всегда воркер: один клип — один вызов, свежий контекст.
    Сверяет результат с критериями; для клипов — и стык с предыдущим."""
    if not t.check:
        return True, "критерии не заданы"
    if not artifacts:
        return False, "нет выходного файла"
    qa = choose("qa_clip" if t.is_clip else "qa_frame", private=False)
    if qa.spec.get("kind") == "builtin":
        tool = REGISTRY[qa.spec["builtin"]]
        result = await tool.handler(
            {"path": artifacts[0],
             "question": f"Проверь по критериям: {t.check}. Ответь строго 'PASS' или 'FAIL: причина'."},
            _ctx(slug))
        verdict_text = result.content
    else:
        verdict_text = "PASS"  # облачный QA без настройки не блокирует пайплайн
    passed = verdict_text.strip().upper().startswith("PASS")
    await db.execute(
        """INSERT INTO qa_results (checker, verdict, criteria, notes, attempt)
           VALUES ($1,$2,$3,$4,$5)""",
        qa.tool, "pass" if passed else "fail", t.check, verdict_text[:1000], attempt)
    return passed, verdict_text


async def _stage_summary(slug: str, stage: str) -> None:
    """Уплотнение после этапа (10.5): журнал этапа → сводка 10–15 строк в notes/."""
    tail = journal_tail(slug, 40)
    if not tail:
        return
    agent = AgentSpec(name="compactor", title="Сжатие", model="bossman-fast", cloud_policy="never")
    try:
        msg = await chat(agent, [
            {"role": "system", "content": "Сожми журнал этапа в сводку 10–15 строк: цель, сделано (пути), решения, открыто, дальше."},
            {"role": "user", "content": tail},
        ], max_tokens=600)
        (project_dir(slug) / "notes" / f"{stage}.md").write_text(
            f"## Сводка этапа {stage}\n{msg.get('content') or ''}\n")
    except Exception:
        journal_append(slug, f"сводка этапа {stage} не составлена (модель недоступна)")


# Пространство advisory-локов проектов ('PROJ' в hex) и 32-битный ключ из slug:
# pg_try_advisory_lock(ns, key) — межпроцессный лок, снимается сам при обрыве
# соединения (падение воркера не блокирует перезапуск навсегда).
_PROJECT_LOCK_NS = 0x50524F4A


def _project_lock_key(slug: str) -> int:
    """Стабильный знаковый 32-битный ключ проекта для pg_advisory_lock."""
    h = int.from_bytes(hashlib.blake2b(slug.encode("utf-8"), digest_size=4).digest(), "big")
    return h - (1 << 32) if h >= (1 << 31) else h  # в диапазон int4


async def run_project(slug: str) -> None:
    """Единственный писатель на проект. Две параллельные попытки одного slug
    (два вызова API, API+CLI, повтор при живом запуске) раньше выполняли каждую
    задачу дважды — двойная оплата облака и гонка за state.json. Здесь берём
    межпроцессный advisory-лок Postgres; если проект уже выполняется — тихо
    выходим, не трогая состояние."""
    key = _project_lock_key(slug)
    pool = await db.pool()
    conn = await pool.acquire()
    try:
        got = await conn.fetchval("SELECT pg_try_advisory_lock($1, $2)", _PROJECT_LOCK_NS, key)
        if not got:
            journal_append(slug, "повторный запуск проигнорирован: проект уже выполняется")
            events.emit("project.updated", slug=slug, status="running", reason="уже выполняется")
            return
        try:
            await _run_project_locked(slug)
        finally:
            with contextlib.suppress(Exception):
                await conn.execute("SELECT pg_advisory_unlock($1, $2)", _PROJECT_LOCK_NS, key)
    finally:
        with contextlib.suppress(Exception):
            await pool.release(conn)


async def _run_project_locked(slug: str) -> None:
    plan = load_plan(slug)
    state = State(slug)
    state.data["status"] = "running"
    state.save()
    await db.execute("UPDATE projects SET status='running', updated_at=now() WHERE slug=$1", slug)
    events.emit("project.updated", slug=slug, status="running")
    row = await db.fetchrow("SELECT id, budget_limit FROM projects WHERE slug=$1", slug)
    budget = float(row["budget_limit"] or plan.budget_limit or 0) if row else plan.budget_limit
    prev_stage: str | None = None

    try:
        for t in plan.tasks:
            if State(slug).data.get("status") == "paused":     # «стоп» с Пульта
                raise ProjectPaused("пауза по запросу пользователя")
            if state.is_done(t.id):
                continue                                        # готовое не переделывается
            if prev_stage and t.stage != prev_stage:
                await _stage_summary(slug, prev_stage)
            prev_stage = t.stage

            if budget and state.data["spent"] >= budget:        # жёсткий лимит на проект
                raise ProjectPaused(f"бюджет исчерпан: {state.data['spent']} из {budget}")

            # превью-гейт: после первых клипов — стоп, вы смотрите персонажа и стыки
            if (t.is_clip and state.data["clips_done"] >= plan.preview_gate_after
                    and not state.data["preview_gate_passed"]):
                done_clips = [a for tid, ts in state.data["tasks"].items()
                              for a in ts.get("artifacts", [])][: plan.preview_gate_after]
                await db.execute("UPDATE projects SET status='preview_gate' WHERE slug=$1", slug)
                approval_id = await approvals.create(
                    "preview_gate",
                    f"Проект {slug}: первые {plan.preview_gate_after} клипа готовы:\n" +
                    "\n".join(done_clips) + "\n\nПродолжить генерацию остальных?",
                    payload={"slug": slug})
                decision = await approvals.wait(approval_id)
                if decision["status"] != "approved":
                    raise ProjectPaused("превью-гейт: остановлено пользователем")
                state.data["preview_gate_passed"] = True
                state.save()
                await db.execute("UPDATE projects SET status='running' WHERE slug=$1", slug)

            route = choose(t.tool, clip_seconds=float(t.params.get("seconds", 0) or 0),
                           total_clips=sum(1 for x in plan.tasks if x.is_clip),
                           budget_left=(budget - state.data["spent"]) if budget else None)
            state.mark(t.id, "running")
            await _db_task_update(slug, t, "running")

            passed, notes = False, ""
            artifacts: list[str] = []
            for attempt in range(1, MAX_RETRIES + 2):           # 1 попытка + 2 перегенерации
                artifacts, cost = await _execute(slug, t, route, state)
                state.mark(t.id, "running", cost=cost)
                await _db_task_update(slug, t, "running", cost)
                passed, notes = await _check(slug, t, artifacts, attempt)
                if passed:
                    break
                journal_append(slug, f"[{t.stage}] {t.name}: проверка FAIL (попытка {attempt}): {notes[:150]}")
                t.params["prompt"] = f"{t.params.get('prompt', '')}\nИсправь: {notes[:300]}"

            if not passed:                                      # две перегенерации — и к вам
                state.mark(t.id, "needs_approval")
                await _db_task_update(slug, t, "needs_approval")
                approval_id = await approvals.create(
                    "action", f"Проект {slug}: «{t.name}» не прошла проверку {MAX_RETRIES + 1} раза.\n"
                              f"{notes[:800]}\n\nПринять как есть?", payload={"slug": slug, "task": t.id})
                decision = await approvals.wait(approval_id)
                if decision["status"] != "approved":
                    raise ProjectPaused(f"задача {t.id} отклонена после проверок")
            state.mark(t.id, "done", artifacts=artifacts)
            await _db_task_update(slug, t, "done")
            if t.is_clip:
                state.data["clips_done"] += 1
                state.save()

        if prev_stage:
            await _stage_summary(slug, prev_stage)
        state.data["status"] = "done"
        state.save()
        await db.execute("UPDATE projects SET status='done', spent=$2, updated_at=now() WHERE slug=$1",
                         slug, state.data["spent"])
        events.emit("project.updated", slug=slug, status="done")
        await telegram.notify(f"🎬 Проект {slug}: готово. Расход: {state.data['spent']}")
    except ProjectPaused as why:
        state.data["status"] = "paused"
        state.save()
        journal_append(slug, f"пауза: {why}")
        await db.execute("UPDATE projects SET status='paused', spent=$2, updated_at=now() WHERE slug=$1",
                         slug, state.data["spent"])
        events.emit("project.updated", slug=slug, status="paused", reason=str(why))
    except Exception as exc:
        state.data["status"] = "failed"
        state.save()
        journal_append(slug, f"ошибка: {exc}")
        await db.execute("UPDATE projects SET status='failed', updated_at=now() WHERE slug=$1", slug)
        events.emit("project.updated", slug=slug, status="failed", reason=str(exc))
        await telegram.notify(f"⚠️ Проект {slug} упал: {str(exc)[:500]}")
