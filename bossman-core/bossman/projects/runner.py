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
import re
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

# F-005: шаблоны cmd в registry.yaml пишет владелец, но ПАРАМЕТРЫ в них подставляет
# план, сочинённый моделью. Поэтому: (1) плейсхолдеры — только из известного набора,
# (2) ключи params — только те, что есть в шаблоне (плюс метаданные, которые в
# команду не попадают), (3) шаблон режется на argv ДО подстановки — значение
# параметра целиком становится одним элементом argv, без оболочки.
KNOWN_PLACEHOLDERS = frozenset({"prompt", "out", "input", "text", "seconds", "model"})
# Параметры плана, которые НЕ подставляются в команду (используются раннером:
# длительность клипа — для лимитов/стоимости) и потому не обязаны быть в шаблоне.
PARAM_METADATA_KEYS = frozenset({"seconds"})
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_SHELLS = frozenset({"sh", "bash", "dash", "zsh"})


class ProjectPaused(Exception):
    pass


class ProjectToolDenied(RuntimeError):
    """Инструмент/шаблон/параметры не прошли проверку раннера проектов —
    ничего не исполнено."""


def _declared_builtins() -> set[str]:
    """Явный allowlist builtin-инструментов: только те, что registry.yaml
    объявляет с kind: builtin. Строка из конфига — не ключ в REGISTRY напрямую."""
    tools = (load_registry().get("tools") or {})
    return {str(spec.get("builtin")) for spec in tools.values()
            if isinstance(spec, dict) and spec.get("kind") == "builtin" and spec.get("builtin")}


async def _run_builtin(slug: str, name: str, params: dict, *, task_id: str | None = None):
    """F-005: builtin через семантику runner._call_tool, а не REGISTRY[...].handler
    напрямую: allowlist из registry.yaml + confirm_default/mandatory_confirm →
    approval владельца (отказ = пауза проекта, инструмент не исполняется)."""
    if name not in _declared_builtins():
        raise ProjectToolDenied(f"builtin '{name}' не объявлен в registry.yaml — отказ")
    tool = REGISTRY.get(name)
    if tool is None:
        raise ProjectToolDenied(f"builtin '{name}' не зарегистрирован в toolkit — отказ")
    needs_confirm = bool(tool.confirm_default)
    if tool.mandatory_confirm is not None:
        try:
            if tool.mandatory_confirm():
                needs_confirm = True
        except Exception:  # noqa: BLE001 — сбой предиката трактуем как «нужно спросить»
            needs_confirm = True
    if needs_confirm:
        preview = (f"Проект {slug}, задача {task_id or '-'}: инструмент {tool.name}\n"
                   f"аргументы: {compact_params(params)}")
        approval_id = await approvals.create("action", preview, tool=tool.name,
                                             payload={"slug": slug, "task": task_id, "args": params})
        decision = await approvals.wait(approval_id)
        if decision["status"] != "approved":
            raise ProjectPaused(f"инструмент {tool.name} отклонён владельцем")
    return await tool.handler(params, _ctx(slug))


def compact_params(params: dict, limit: int = 800) -> str:
    try:
        import json
        text = json.dumps(params, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        text = str(params)
    return text[:limit]


def build_cmd_argv(template: str, params: dict, defaults: dict) -> tuple[list[str], bool]:
    """Шаблон → argv без оболочки. Возвращает (argv, via_shell).

    - shlex.split ДО подстановки: границы аргументов задаёт владелец шаблоном,
      значение параметра модели их сдвинуть не может;
    - плейсхолдеры только из KNOWN_PLACEHOLDERS, ключи params — только из шаблона
      (+ PARAM_METADATA_KEYS);
    - единственное исключение: шаблон вида `sh -c '<script>'` (piper_local в
      registry.yaml) — это оболочка по замыслу владельца. Такой шаблон
      исполняется через create_subprocess_shell(<script>) с shlex.quote каждой
      подстановки; это оставшийся templated-shell, покрытый
      tests/test_stage13_hostexec_redteam.py KNOWN_SHELL_EXCEPTIONS."""
    try:
        argv = shlex.split(template)
    except ValueError as exc:
        raise ProjectToolDenied(f"шаблон cmd не разбирается: {exc}") from exc
    if not argv:
        raise ProjectToolDenied("пустой шаблон cmd")
    placeholders = set(_PLACEHOLDER_RE.findall(template))
    unknown = placeholders - KNOWN_PLACEHOLDERS
    if unknown:
        raise ProjectToolDenied(f"шаблон cmd содержит неизвестные плейсхолдеры: {sorted(unknown)}")
    extra_keys = set(params) - placeholders - PARAM_METADATA_KEYS
    if extra_keys:
        raise ProjectToolDenied(f"параметры плана не из шаблона cmd: {sorted(extra_keys)}")
    values = {k: str(v) for k, v in params.items() if k in placeholders}
    for k, v in defaults.items():
        if k in placeholders:
            values.setdefault(k, str(v))
    missing = placeholders - set(values)
    if missing:
        raise ProjectToolDenied(f"в плане нет значений для плейсхолдеров: {sorted(missing)}")
    via_shell = len(argv) >= 3 and argv[0] in _SHELLS and argv[1] == "-c"
    if via_shell and len(argv) != 3:
        raise ProjectToolDenied("shell-шаблон допустим только в форме `sh -c '<script>'`")

    def _sub(elem: str, quote: bool) -> str:
        return _PLACEHOLDER_RE.sub(
            lambda m: shlex.quote(values[m.group(1)]) if quote else values[m.group(1)], elem)

    if via_shell:
        return [argv[0], argv[1], _sub(argv[2], quote=True)], True
    return [_sub(a, quote=False) for a in argv], False


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
        # F-005: allowlist + подтверждение как в runner._call_tool (см. _run_builtin).
        result = await _run_builtin(slug, str(spec.get("builtin")), t.params, task_id=t.id)
        if result.error:
            raise RuntimeError(result.content)
    elif kind == "cmd":
        out_path = str(d / (t.outputs[0] if t.outputs else "assets/out"))
        argv, via_shell = build_cmd_argv(str(spec["cmd"]), t.params,
                                         {"out": out_path, "input": out_path})
        if via_shell:
            # Единственная оставшаяся оболочка (piper_local: `sh -c 'echo {text} | piper …'`).
            # Подстановки внутри уже shlex.quote'нуты в build_cmd_argv. Пиннится
            # tests/test_stage13_hostexec_redteam.py::KNOWN_SHELL_EXCEPTIONS.
            proc = await asyncio.create_subprocess_shell(
                argv[2], cwd=d,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        else:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=d,
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
        # F-005: тот же путь с allowlist/подтверждением, что и у Исполнителя.
        result = await _run_builtin(
            slug, str(qa.spec.get("builtin")),
            {"path": artifacts[0],
             "question": f"Проверь по критериям: {t.check}. Ответь строго 'PASS' или 'FAIL: причина'."},
            task_id=t.id)
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


def _reconcile_external_status(slug: str, state: State) -> None:
    """После долгого ожидания (генерация, проверка, аппрувал) перечитываем
    state.json с диска. «Стоп» с Пульта мог записать статус 'paused', пока
    раннер ждал: устаревший in-memory снимок со статусом 'running' не должен
    перезатереть его при следующем save() и не должен начинать следующую
    платную задачу."""
    disk = State(slug)
    if disk.data.get("status") == "paused":
        state.data = disk.data          # дисковая правда вместо нашего снимка
        raise ProjectPaused("пауза по запросу пользователя")


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
            _reconcile_external_status(slug, state)            # «стоп» с Пульта
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
                _reconcile_external_status(slug, state)
                state.data["preview_gate_passed"] = True
                state.save()
                await db.execute("UPDATE projects SET status='running' WHERE slug=$1", slug)

            route = choose(t.tool, clip_seconds=float(t.params.get("seconds", 0) or 0),
                           total_clips=sum(1 for x in plan.tasks if x.is_clip),
                           budget_left=(budget - state.data["spent"]) if budget else None)
            _reconcile_external_status(slug, state)             # пауза могла прийти во время сводки этапа
            state.mark(t.id, "running")
            await _db_task_update(slug, t, "running")

            passed, notes = False, ""
            artifacts: list[str] = []
            for attempt in range(1, MAX_RETRIES + 2):           # 1 попытка + 2 перегенерации
                artifacts, cost = await _execute(slug, t, route, state)
                _reconcile_external_status(slug, state)         # пауза во время долгой генерации
                state.mark(t.id, "running", cost=cost)
                await _db_task_update(slug, t, "running", cost)
                passed, notes = await _check(slug, t, artifacts, attempt)
                _reconcile_external_status(slug, state)         # пауза во время проверки — не перегенерировать платно
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
                _reconcile_external_status(slug, state)
            state.mark(t.id, "done", artifacts=artifacts)
            await _db_task_update(slug, t, "done")
            if t.is_clip:
                state.data["clips_done"] += 1
                state.save()

        if prev_stage:
            await _stage_summary(slug, prev_stage)
        _reconcile_external_status(slug, state)                 # пауза во время финальной сводки
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
