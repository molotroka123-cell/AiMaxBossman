"""Feature 10 — Skill Library + Skill Forge + MCP Hub.

Skills — переиспользуемые проверенные процессы в .agents/skills/<id>/SKILL.md
(discovery поверх готовой bcc/v2/skill_library, БЕЗ рекурсии по всей машине).

V2.1 фаза K: запуск скилла идёт через КАНОНИЧЕСКИЙ рантайм (обычный движок
задач), второго пути исполнения нет. Порядок:
  1. вход валидируется по input_schema скилла;
  2. агент выбирается и проверяется (существует, включён, есть модель);
  3. `tasks.meta.allowed_tools` = РОВНО required_tools скилла — `allowed_tools_for`
     не даёт модели ничего сверх этого (скилл без инструментов не наследует
     инструменты агента: подставляется NO_TOOLS_SENTINEL);
  4. процесс скилла + вход + ожидаемый выход → `tasks.prompt`;
  5. задача уходит в движок (enqueue), tool-loop делает всё остальное;
  6. после run'а хук `after_run` кладёт результат и версию/отпечаток скилла
     в `tasks.meta`, а `tasks.skill_version_id` проставлен при создании.

Skill Forge: предлагает новый скилл из ПОВТОРЯЮЩЕГОСЯ процесса и правит
существующий, но любое расширение прав/инструментов — только через каноническую
очередь approvals (см. FORGE_* ниже).

V2.2 §7 — петля самообучения перестала быть инструкцией и стала механизмом,
но ОГРАНИЧЕННЫМ. Тот же хук `after_run` теперь:
  * пересчитывает сравнения версий скилла (`skill_evaluations`), и рантайм сам
    выносит PROMOTE/REJECT только по жёстким порогам — всё спорное и всё, что
    расширяет права, уходит человеку как HUMAN_REVIEW с approval;
  * при статусе `failed` ПРЕДЛАГАЕТ разбор провала (`failure-retrospective`) —
    предлагает, а не запускает: иначе падающий скилл порождал бы лавину задач.

MCP Hub — канонический реестр MCP-серверов/инструментов с AUTO/ASK/DENY;
только назначенные инструменты попадают в контекст модели.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from ..db import (agents as agents_t, approvals as approvals_t, settings_kv,
                  skill_evaluations as skill_evals_t, skills as skills_t,
                  skill_versions as skill_versions_t,
                  task_runs as runs_t, tasks as tasks_t, utcnow)
from ..permissions import agent_allowed
from ..tools import REGISTRY as TOOLS
from ..v2 import skill_evaluation as evaluation
from ..v2.mcp_hub import MCPServerSpec, namespaced_tool
from ..v2.skill_library import (SkillLibrary, build_skill_prompt, default_skill_roots,
                                skill_contract)
from ..v2.tables import mcp_servers as mcp_servers_t, mcp_tools as mcp_tools_t
from . import Feature

ASSIGN_KEY = "skills.assignments"
MCP_POLICY_KEY = "mcp.policy"          # {"mcp:server:tool": "auto|ask|deny"}
FORGE_KEY = "skills.forge"             # {"signatures": {...}, "pending": {...}}

# Правило Skill Forge (чтобы скилл НЕ рождался из каждого чата):
#   * предложение делается только явным вызовом /skills/forge/propose;
#   * один и тот же процесс должен быть замечен не менее FORGE_MIN_RUNS раз
#     (успешных, с одинаковой сигнатурой) — «повторяющийся», а не разовый;
#   * повторное предложение по той же сигнатуре — не чаще раза в
#     FORGE_COOLDOWN_HOURS часов.
FORGE_MIN_RUNS = 3
FORGE_COOLDOWN_HOURS = 24

# V2.2 §7 — переход «провал → разбор провала». Автоматически он только
# ПРЕДЛАГАЕТСЯ: движок не запускает разборы сам, иначе один падающий скилл
# порождал бы лавину задач. Кулдаун — чтобы очередь предложений не забивалась
# одним и тем же скиллом.
RETRO_KEY = "skills.retrospectives"
RETRO_SKILL = "failure-retrospective"
RETRO_COOLDOWN_HOURS = 6
RETRO_MAX_PENDING = 20

router = APIRouter()


def _lib(svc) -> SkillLibrary:
    if getattr(svc, "skills", None) is not None:
        return svc.skills
    # запасной путь, если менеджер не поднялся
    repo = svc.settings.ui_dir.parent.parent
    return SkillLibrary(default_skill_roots(repo), repo / ".agents" / "skills")


def _skill_dict(sk) -> dict:
    con = skill_contract(sk)
    return {"id": sk.id, "name": sk.name, "description": sk.description,
            "source_root": str(sk.source_root), "fingerprint": sk.fingerprint[:16],
            "version": con.version,
            "permissions": con.permissions,
            "required_tools": con.required_tools}


# ---------- Skills API ----------

@router.get("/skills")
async def list_skills(request: Request):
    svc = request.app.state.svc
    lib = _lib(svc)
    assigns = await _assignments(svc)
    out = []
    for sk in lib.discover():
        d = _skill_dict(sk)
        d["agents"] = assigns.get(sk.id, [])
        out.append(d)
    return out


@router.get("/skills/{skill_id}")
async def get_skill(skill_id: str, request: Request):
    svc = request.app.state.svc
    sk = _lib(svc).by_id().get(skill_id)
    if sk is None:
        raise HTTPException(404, {"message": "скилл не найден"})
    d = _skill_dict(sk)
    d["process"] = sk.body
    d["frontmatter"] = sk.frontmatter
    return d


@router.post("/skills")
async def create_skill(request: Request):
    svc = request.app.state.svc
    body = await request.json()
    sid = body.get("id")
    content = body.get("content")
    if not sid or not content:
        raise HTTPException(422, {"message": "нужны id и content"})
    try:
        sk = _lib(svc).create(sid, content, overwrite=bool(body.get("overwrite")))
    except (ValueError, FileExistsError) as exc:
        raise HTTPException(409, {"message": str(exc)})
    await svc.bus.emit("skill.created", slug=sk.id)
    return _skill_dict(sk)


@router.post("/skills/{skill_id}/clone")
async def clone_skill(skill_id: str, request: Request):
    svc = request.app.state.svc
    body = await request.json()
    lib = _lib(svc)
    sk = lib.by_id().get(skill_id)
    if sk is None:
        raise HTTPException(404, {"message": "скилл не найден"})
    new_id = body.get("new_id") or f"{skill_id}-copy"
    content = sk.path.read_text(encoding="utf-8")
    try:
        clone = lib.create(new_id, content)
    except (ValueError, FileExistsError) as exc:
        raise HTTPException(409, {"message": str(exc)})
    return _skill_dict(clone)


@router.get("/skills/{skill_id}/export")
async def export_skill(skill_id: str, request: Request):
    svc = request.app.state.svc
    sk = _lib(svc).by_id().get(skill_id)
    if sk is None:
        raise HTTPException(404, {"message": "скилл не найден"})
    return {"id": sk.id, "content": sk.path.read_text(encoding="utf-8"),
            "fingerprint": sk.fingerprint}


@router.post("/skills/import")
async def import_skill(request: Request):
    svc = request.app.state.svc
    body = await request.json()
    if not body.get("id") or not body.get("content"):
        raise HTTPException(422, {"message": "нужны id и content"})
    try:
        sk = _lib(svc).create(body["id"], body["content"], overwrite=bool(body.get("overwrite")))
    except (ValueError, FileExistsError) as exc:
        raise HTTPException(409, {"message": str(exc)})
    return _skill_dict(sk)


@router.post("/skills/{skill_id}/assign")
async def assign_skill(skill_id: str, request: Request):
    svc = request.app.state.svc
    body = await request.json()
    agent_id = body.get("agent_id")
    if agent_id is None:
        raise HTTPException(422, {"message": "нужен agent_id"})
    assigns = await _assignments(svc)
    lst = assigns.setdefault(skill_id, [])
    if agent_id not in lst:
        lst.append(agent_id)
    await _save_assignments(svc, assigns)
    await svc.bus.emit("skill.assigned", slug=skill_id, agent_id=agent_id)
    return {"skill_id": skill_id, "agents": lst}


@router.post("/skills/{skill_id}/run")
async def run_skill(skill_id: str, request: Request):
    """Запуск скилла через канонический рантайм (V2.1 фаза K).

    Второго пути исполнения нет: собирается обычная задача, ей выдаются РОВНО
    объявленные скиллом инструменты, дальше работает движок с tool-loop.
    """
    svc = request.app.state.svc
    body = await request.json()
    sk = _lib(svc).by_id().get(skill_id)
    if sk is None:
        raise HTTPException(404, {"message": "скилл не найден"})
    con = skill_contract(sk)

    # 1. вход по схеме скилла
    inputs = body.get("input") or {}
    if not isinstance(inputs, dict):
        raise HTTPException(422, {"message": "input должен быть объектом"})
    errors = _validate_input(con.input_schema, inputs)
    if errors:
        raise HTTPException(422, {"message": "вход не по схеме", "hint": "; ".join(errors)})

    # 2. агент: существует, включён, с моделью
    agent_id = body.get("agent_id")
    agent: dict | None = None
    if agent_id is not None:
        try:
            agent_id = int(agent_id)
        except (TypeError, ValueError):
            raise HTTPException(422, {"message": "agent_id должен быть числом"})
        async with svc.db.session() as s:
            row = (await s.execute(sa.select(agents_t)
                                   .where(agents_t.c.id == int(agent_id)))).first()
        if row is None:
            raise HTTPException(404, {"message": "агент не найден"})
        agent = dict(row._mapping)
        if not agent.get("enabled", True):
            raise HTTPException(409, {"message": "агент выключен"})
        if agent.get("model_id") is None:
            raise HTTPException(409, {"message": "у агента не задана модель"})

    # честность: объявленные, но не зарегистрированные инструменты — предупреждение,
    # а не тихое «всё хорошо». Модель их всё равно не получит.
    unknown_tools = [t for t in con.required_tools if not TOOLS.resolve([t])]
    missing_perms = [p for p in con.permissions
                     if agent is not None and not agent_allowed(agent, p)]

    # 3–4. процесс + вход → prompt; инструменты — ровно объявленные
    prompt = build_skill_prompt(con, inputs)
    version_id = await _persist_skill_version(svc, con, sk.description)
    meta = {"skill": con.id, "skill_fp": con.fingerprint, "skill_version": con.version,
            "skill_version_id": version_id,
            "allowed_tools": con.allowed_tools(),
            "skill_required_tools": con.required_tools,
            "skill_output_schema": con.output_schema,
            "skill_input": inputs,
            "skill_missing_permissions": missing_perms,
            "skill_unknown_tools": unknown_tools}
    async with svc.db.session() as s:
        res = await s.execute(sa.insert(tasks_t).values(
            title=f"Скилл: {sk.name}", prompt=prompt, agent_id=agent_id,
            status="draft", kind="research", meta=meta, skill_version_id=version_id,
            created_at=utcnow(), updated_at=utcnow()))
        task_id = int(res.inserted_primary_key[0])
        await s.commit()
    # 5. канонический рантайм
    if agent_id is not None:
        await svc.engine.enqueue(task_id)
    await svc.bus.emit("skill.run", slug=con.id, task_id=task_id, version=con.version)
    return {"task_id": task_id, "skill": con.id, "version": con.version,
            "skill_version_id": version_id, "fingerprint": con.fingerprint[:16],
            "allowed_tools": con.required_tools, "unknown_tools": unknown_tools,
            "missing_permissions": missing_perms}


async def _persist_skill_version(svc, con, description: str = "") -> int:
    """Строка `skill_versions` для этого отпечатка (идемпотентно по fingerprint).
    Возвращает id — он ложится в `tasks.skill_version_id`."""
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(skills_t).where(skills_t.c.slug == con.id))).first()
        if row is None:
            sid = int((await s.execute(sa.insert(skills_t).values(
                name=con.name, slug=con.id, description=description,
                created_at=utcnow()))).inserted_primary_key[0])
        else:
            sid = int(row._mapping["id"])
        versions = (await s.execute(sa.select(skill_versions_t)
                                    .where(skill_versions_t.c.skill_id == sid))).fetchall()
        for v in versions:
            m = v._mapping
            if (m["permissions"] or {}) and isinstance(m["permissions"], dict) \
                    and m["permissions"].get("fingerprint") == con.fingerprint:
                return int(m["id"])
        vid = int((await s.execute(sa.insert(skill_versions_t).values(
            skill_id=sid, version=len(versions) + 1,
            input_schema=con.input_schema, output_schema=con.output_schema,
            required_tools=con.required_tools, process=con.process,
            permissions={"declared": con.permissions, "fingerprint": con.fingerprint,
                         "label": con.version},
            created_at=utcnow()))).inserted_primary_key[0])
        await s.execute(sa.update(skills_t).where(skills_t.c.id == sid).values(
            current_version_id=vid))
        await s.commit()
    return vid


_TYPES = {"string": str, "number": (int, float), "integer": int, "boolean": bool,
          "array": list, "object": dict}


def _validate_input(schema: dict, data: dict) -> list[str]:
    """Минимальный валидатор required/type (jsonschema не тянем)."""
    errors: list[str] = []
    for field in schema.get("required", []):
        if field not in data:
            errors.append(f"нет обязательного поля {field}")
    for name, spec in (schema.get("properties") or {}).items():
        if name not in data or not isinstance(spec, dict):
            continue
        expected = _TYPES.get(str(spec.get("type") or ""))
        if expected is None:
            continue
        value = data[name]
        if expected is not bool and isinstance(value, bool):
            errors.append(f"{name} должно быть типа {spec.get('type')}")
        elif not isinstance(value, expected):
            errors.append(f"{name} должно быть типа {spec.get('type')}")
    return errors


async def _assignments(svc) -> dict:
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(settings_kv.c.value_enc)
                               .where(settings_kv.c.key == ASSIGN_KEY))).first()
    if row and row[0]:
        try:
            return json.loads(svc.vault.decrypt(row[0]))
        except Exception:
            pass
    return {}


async def _save_assignments(svc, data: dict) -> None:
    enc = svc.vault.encrypt(json.dumps(data))
    async with svc.db.session() as s:
        await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == ASSIGN_KEY))
        await s.execute(sa.insert(settings_kv).values(key=ASSIGN_KEY, value_enc=enc))
        await s.commit()


# ---------- результат скилла на задаче ----------

async def _record_skill_result(svc, task_id: int, run_id: int, status: str) -> None:
    """Хук after_run: результат + версия/отпечаток скилла остаются на задаче.

    Пишем только для задач, рождённых скиллом (`meta.skill`), и не трогаем
    остальные — фича не владеет чужими задачами.
    """
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(tasks_t.c.meta)
                               .where(tasks_t.c.id == task_id))).first()
        meta = dict(row._mapping["meta"] or {}) if row else {}
        if not meta.get("skill"):
            return
        run = (await s.execute(sa.select(runs_t.c.result, runs_t.c.error)
                               .where(runs_t.c.id == run_id))).first()
        result = (run._mapping["result"] if run else None) or ""
        meta["skill_status"] = status
        meta["skill_result"] = result[:4000]
        meta["skill_error"] = (run._mapping["error"] if run else None) or ""
        meta["skill_finished_at"] = utcnow().isoformat()
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(meta=meta))
        await s.commit()
    await svc.bus.emit("skill.finished", slug=meta["skill"], task_id=task_id,
                       version=meta.get("skill_version"), status=status)


# ---------- V2.2 §7: переходы самообучения ----------

async def _after_skill_run(svc, task_id: int, run_id: int, status: str) -> None:
    """Два перехода, которые раньше существовали только как инструкция в тексте.

    Оба ОГРАНИЧЕНЫ: сравнение версий решает сам рантайм (и то лишь по жёстким
    порогам, см. `bcc.v2.skill_evaluation`), а разбор провала он только
    предлагает — запускает человек.
    """
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(tasks_t.c.meta, tasks_t.c.skill_version_id,
                                         tasks_t.c.title)
                               .where(tasks_t.c.id == task_id))).first()
    if row is None:
        return
    m = row._mapping
    meta = dict(m["meta"] or {})
    slug = meta.get("skill")
    if not slug:
        return                                   # чужая задача — фича её не трогает

    if m["skill_version_id"]:
        try:
            await evaluation.refresh_for_version(svc, int(m["skill_version_id"]))
        except Exception as exc:                 # сравнение версий не имеет права
            await svc.bus.emit("skill.evaluation.error",  # уронить сам прогон
                               task_id=task_id, error=str(exc)[:300])

    if status == "failed":
        await _propose_retrospective(svc, task_id=task_id, run_id=run_id, slug=str(slug),
                                     title=str(m["title"] or ""))


async def _propose_retrospective(svc, *, task_id: int, run_id: int, slug: str,
                                 title: str) -> dict | None:
    """Предложить разбор провала. Ничего не запускает и не решает за человека."""
    if slug == RETRO_SKILL:
        return None                              # разбор разбора — петля, не польза
    state = await _kv_get(svc, RETRO_KEY) or {}
    pending = state.setdefault("pending", {})
    if str(task_id) in pending:
        return None
    now = utcnow()
    last_raw = (state.setdefault("last_proposed", {})).get(slug)
    if last_raw:
        try:
            if now - datetime.fromisoformat(last_raw) < timedelta(hours=RETRO_COOLDOWN_HOURS):
                return None
        except ValueError:
            pass

    async with svc.db.session() as s:
        run = (await s.execute(sa.select(runs_t.c.error)
                               .where(runs_t.c.id == run_id))).first()
    entry = {"task_id": task_id, "run_id": run_id, "skill": slug, "title": title[:200],
             "error": ((run._mapping["error"] if run else "") or "")[:1000],
             "proposed_at": now.isoformat(), "status": "pending"}
    pending[str(task_id)] = entry
    state["last_proposed"][slug] = now.isoformat()
    # очередь предложений не растёт бесконечно: старые вытесняются новыми
    if len(pending) > RETRO_MAX_PENDING:
        for key in sorted(pending, key=lambda k: pending[k]["proposed_at"])[
                :len(pending) - RETRO_MAX_PENDING]:
            pending.pop(key, None)
    await _kv_set(svc, RETRO_KEY, state)
    await svc.bus.emit("skill.retrospective.proposed", task_id=task_id, run_id=run_id,
                       skill=slug, retro_skill=RETRO_SKILL)
    return entry


@router.get("/skill-retrospectives")
async def list_retrospectives(request: Request):
    """Предложенные разборы провалов. Ни один из них сам не запустится."""
    state = await _kv_get(request.app.state.svc, RETRO_KEY) or {}
    items = sorted((state.get("pending") or {}).values(),
                   key=lambda e: e.get("proposed_at") or "", reverse=True)
    return {"retro_skill": RETRO_SKILL, "cooldown_hours": RETRO_COOLDOWN_HOURS,
            "pending": items}


@router.post("/skill-retrospectives/{task_id}/dismiss")
async def dismiss_retrospective(task_id: int, request: Request):
    svc = request.app.state.svc
    state = await _kv_get(svc, RETRO_KEY) or {}
    if str(task_id) not in (state.get("pending") or {}):
        raise HTTPException(404, {"message": "предложение не найдено"})
    state["pending"].pop(str(task_id))
    await _kv_set(svc, RETRO_KEY, state)
    return {"ok": True, "task_id": task_id}


# ---------- V2.2 §7: сравнение версий скилла ----------

@router.get("/skill-evaluations")
async def list_evaluations(request: Request):
    svc = request.app.state.svc
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(skill_evals_t)
                                .order_by(skill_evals_t.c.id.desc()).limit(100))).fetchall()
    return {"evaluations": [dict(r._mapping) for r in rows],
            "rules": {"min_runs": evaluation.MIN_RUNS,
                      "improve_delta": evaluation.IMPROVE_DELTA,
                      "regress_delta": evaluation.REGRESS_DELTA,
                      "note": "PROMOTE меняет только текущую версию скилла; кандидат "
                              "с расширенными правами автоматически не применяется"}}


@router.post("/skill-evaluations")
async def create_evaluation(request: Request):
    """Завести сравнение baseline↔кандидат и сразу пересчитать его."""
    svc = request.app.state.svc
    body = await request.json()
    try:
        skill_id = int(body["skill_id"])
        baseline = int(body["baseline_version_id"])
        candidate = int(body["candidate_version_id"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, {"message": "нужны skill_id, baseline_version_id, "
                                             "candidate_version_id"})
    try:
        row = await evaluation.open_evaluation(svc, skill_id=skill_id,
                                               baseline_version_id=baseline,
                                               candidate_version_id=candidate)
        return {"evaluation": await evaluation.refresh(svc, int(row["id"]))}
    except ValueError as exc:
        raise HTTPException(400, {"message": str(exc)})
    except KeyError as exc:
        raise HTTPException(404, {"message": f"версия не найдена: {exc}"})


@router.post("/skill-evaluations/{evaluation_id}/refresh")
async def refresh_evaluation(evaluation_id: int, request: Request):
    try:
        return {"evaluation": await evaluation.refresh(request.app.state.svc, evaluation_id)}
    except KeyError:
        raise HTTPException(404, {"message": "сравнение не найдено"})


@router.post("/skill-evaluations/{evaluation_id}/decide")
async def decide_evaluation(evaluation_id: int, request: Request):
    """Решение человека по HUMAN_REVIEW — единственный путь для спорного кандидата."""
    svc = request.app.state.svc
    body = await request.json()
    try:
        return {"evaluation": await evaluation.apply_human_decision(
            svc, evaluation_id, approve=bool(body.get("approve")),
            by=str(body.get("by") or "owner"))}
    except KeyError:
        raise HTTPException(404, {"message": "сравнение не найдено"})
    except ValueError as exc:
        raise HTTPException(409, {"message": str(exc)})


# ---------- Skill Forge ----------

def _signature(text: str) -> str:
    """Нормализованная сигнатура процесса: регистр и пробелы не считаются."""
    import hashlib
    import re as _re
    norm = _re.sub(r"\s+", " ", str(text or "").strip().lower())
    return hashlib.sha256(norm.encode()).hexdigest()[:24]


@router.post("/skills/forge/observe")
async def forge_observe(request: Request):
    """Зафиксировать очередное появление процесса. Ничего не создаёт."""
    svc = request.app.state.svc
    body = await request.json()
    workflow = body.get("workflow") or ""
    if not workflow:
        raise HTTPException(422, {"message": "нужен workflow"})
    sig = _signature(workflow)
    state = await _forge_state(svc)
    entry = state["signatures"].setdefault(sig, {"count": 0, "workflow": workflow[:2000],
                                                 "last_proposed": None})
    entry["count"] += 1
    entry["workflow"] = workflow[:2000]
    await _save_forge_state(svc, state)
    return {"signature": sig, "count": entry["count"],
            "ready": entry["count"] >= FORGE_MIN_RUNS}


@router.post("/skills/forge/propose")
async def forge_propose(request: Request):
    """Предложение нового скилла — ТОЛЬКО по явному запросу и только для
    процесса, повторившегося не менее FORGE_MIN_RUNS раз (см. шапку модуля)."""
    svc = request.app.state.svc
    body = await request.json()
    workflow = body.get("workflow") or ""
    if not workflow:
        raise HTTPException(422, {"message": "нужен workflow"})
    sig = _signature(workflow)
    state = await _forge_state(svc)
    entry = state["signatures"].get(sig)
    count = int((entry or {}).get("count") or 0)
    if count < FORGE_MIN_RUNS:
        return {"proposed": False, "signature": sig, "count": count,
                "reason": f"процесс повторился {count} раз(а) из {FORGE_MIN_RUNS} — "
                          f"скилл из разового чата не делаем"}
    last = (entry or {}).get("last_proposed")
    if last:
        from datetime import datetime
        try:
            prev = datetime.fromisoformat(last)
        except ValueError:
            prev = None
        if prev is not None and utcnow() - prev < timedelta(hours=FORGE_COOLDOWN_HOURS):
            return {"proposed": False, "signature": sig, "count": count,
                    "reason": f"предложение по этому процессу уже было менее "
                              f"{FORGE_COOLDOWN_HOURS} ч назад"}
    sid = str(body.get("id") or f"forged-{sig[:8]}")
    tools = [str(t) for t in (body.get("required_tools") or [])]
    perms = [str(p) for p in (body.get("permissions") or [])]
    content = _draft_skill(sid, body.get("name") or sid,
                           body.get("description") or "Собран Skill Forge из повторяющегося процесса",
                           workflow, tools, perms)
    entry["last_proposed"] = utcnow().isoformat()
    await _save_forge_state(svc, state)
    await svc.bus.emit("skill.proposed", slug=sid, signature=sig, count=count)
    return {"proposed": True, "signature": sig, "count": count,
            "skill": {"id": sid, "content": content,
                      "required_tools": tools, "permissions": perms}}


def _draft_skill(sid: str, name: str, description: str, process: str,
                 tools: list[str], perms: list[str]) -> str:
    fm = [f"name: {name}", f"description: {description}",
          "metadata:", "  owner: skill-forge", '  version: "1.0"']
    if tools:
        fm.append("required_tools: [" + ", ".join(tools) + "]")
    if perms:
        fm.append("permissions: [" + ", ".join(perms) + "]")
    return "---\n" + "\n".join(fm) + "\n---\n\n# " + name + "\n\n" + process.strip() + "\n"


@router.post("/skills/forge/apply")
async def forge_apply(request: Request):
    """Записать предложенный/обновлённый скилл.

    Расширение прав или набора инструментов (по сравнению с текущей версией
    скилла, а для нового скилла — с пустым набором) НЕ применяется сразу:
    заводится строка в канонической очереди approvals, и только её одобрение
    даёт право записать файл.
    """
    svc = request.app.state.svc
    body = await request.json()
    approval_id = body.get("approval_id")
    state = await _forge_state(svc)

    if approval_id is not None:
        pending = state["pending"].get(str(approval_id))
        if pending is None:
            raise HTTPException(404, {"message": "нет отложенного изменения для этого approval"})
        async with svc.db.session() as s:
            row = (await s.execute(sa.select(approvals_t)
                                   .where(approvals_t.c.id == int(approval_id)))).first()
        status = row._mapping["status"] if row else "missing"
        if status != "approved":
            raise HTTPException(409, {"message": "расширение прав не одобрено",
                                      "hint": f"статус подтверждения: {status}"})
        try:
            sk = _lib(svc).create(pending["id"], pending["content"], overwrite=True)
        except (ValueError, OSError) as exc:
            raise HTTPException(409, {"message": str(exc)})
        state["pending"].pop(str(approval_id), None)
        await _save_forge_state(svc, state)
        await svc.bus.emit("skill.updated", slug=sk.id, approval_id=int(approval_id))
        return {"applied": True, "skill": _skill_dict(sk), "approval_id": int(approval_id)}

    sid, content = body.get("id"), body.get("content")
    if not sid or not content:
        raise HTTPException(422, {"message": "нужны id и content"})
    lib = _lib(svc)
    current = lib.by_id().get(sid)
    old_tools = set(skill_contract(current).required_tools) if current else set()
    old_perms = set(skill_contract(current).permissions) if current else set()
    new = _parse_draft(content)
    added_tools = sorted(set(new["required_tools"]) - old_tools)
    added_perms = sorted(set(new["permissions"]) - old_perms)

    if not (added_tools or added_perms):
        try:
            sk = lib.create(sid, content, overwrite=True)
        except (ValueError, OSError) as exc:
            raise HTTPException(409, {"message": str(exc)})
        await svc.bus.emit("skill.updated", slug=sk.id)
        return {"applied": True, "skill": _skill_dict(sk), "expansion": False,
                "removed_tools": sorted(old_tools - set(new["required_tools"])),
                "removed_permissions": sorted(old_perms - set(new["permissions"]))}

    preview = json.dumps({"skill": sid, "added_tools": added_tools,
                          "added_permissions": added_perms,
                          "was_tools": sorted(old_tools), "was_permissions": sorted(old_perms)},
                         ensure_ascii=False)
    appr = await svc.approvals.create("skill_permissions", preview=preview)
    state["pending"][str(appr["id"])] = {"id": sid, "content": content,
                                         "added_tools": added_tools,
                                         "added_permissions": added_perms}
    await _save_forge_state(svc, state)
    return {"applied": False, "expansion": True, "approval_id": int(appr["id"]),
            "added_tools": added_tools, "added_permissions": added_perms,
            "hint": "одобрите подтверждение и повторите вызов с approval_id"}


def _parse_draft(content: str) -> dict:
    """Разбор фронтматтера черновика БЕЗ записи на диск."""
    import tempfile
    from pathlib import Path

    from ..v2.skill_library import parse_skill
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "draft"
        d.mkdir()
        p = d / "SKILL.md"
        p.write_text(content, encoding="utf-8")
        con = skill_contract(parse_skill(p, Path(tmp)))
    return {"required_tools": con.required_tools, "permissions": con.permissions}


async def _forge_state(svc) -> dict:
    raw = await _kv_get(svc, FORGE_KEY) or {}
    raw.setdefault("signatures", {})
    raw.setdefault("pending", {})
    return raw


async def _save_forge_state(svc, data: dict) -> None:
    await _kv_set(svc, FORGE_KEY, data)


async def _kv_get(svc, key: str) -> dict:
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(settings_kv.c.value_enc)
                               .where(settings_kv.c.key == key))).first()
    if row and row[0]:
        try:
            return json.loads(svc.vault.decrypt(row[0]))
        except Exception:
            pass
    return {}


async def _kv_set(svc, key: str, data: dict) -> None:
    enc = svc.vault.encrypt(json.dumps(data, ensure_ascii=False))
    async with svc.db.session() as s:
        await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == key))
        await s.execute(sa.insert(settings_kv).values(key=key, value_enc=enc))
        await s.commit()


# ---------- MCP Hub API ----------

@router.get("/mcp/servers")
async def list_mcp(request: Request):
    svc = request.app.state.svc
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(mcp_servers_t))).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/mcp/servers")
async def add_mcp(request: Request):
    svc = request.app.state.svc
    body = await request.json()
    spec = MCPServerSpec(id=body.get("name", ""), name=body.get("name", ""),
                         transport=body.get("transport", "stdio"),
                         command=body.get("command", []), url=body.get("url", ""))
    errs = spec.validate()
    if errs:
        raise HTTPException(422, {"message": "; ".join(errs)})
    async with svc.db.session() as s:
        res = await s.execute(sa.insert(mcp_servers_t).values(
            name=spec.name, transport=spec.transport, command=spec.command,
            url=spec.url, enabled=True, status="unknown", created_at=utcnow()))
        sid = int(res.inserted_primary_key[0])
        await s.commit()
    return {"id": sid, "name": spec.name}


@router.delete("/mcp/servers/{server_id}")
async def del_mcp(server_id: int, request: Request):
    svc = request.app.state.svc
    async with svc.db.session() as s:
        await s.execute(sa.delete(mcp_servers_t).where(mcp_servers_t.c.id == server_id))
        await s.commit()
    return {"ok": True}


@router.get("/mcp/tools")
async def list_mcp_tools(request: Request):
    """Инструменты в каноническом виде mcp:<server>:<tool> + политика AUTO/ASK/DENY."""
    svc = request.app.state.svc
    policy = await _mcp_policy(svc)
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(mcp_tools_t, mcp_servers_t.c.name)
                                .join(mcp_servers_t, mcp_servers_t.c.id == mcp_tools_t.c.server_id))
                ).fetchall()
    out = []
    for r in rows:
        m = r._mapping
        canonical = namespaced_tool(m["name_1"], m["name"])
        out.append({"server": m["name_1"], "tool": m["name"], "canonical": canonical,
                    "description": m["description"], "policy": policy.get(canonical, "ask")})
    return out


@router.post("/mcp/policy")
async def set_mcp_policy(request: Request):
    svc = request.app.state.svc
    body = await request.json()
    canonical = body.get("canonical")
    decision = body.get("policy")
    if decision not in ("auto", "ask", "deny"):
        raise HTTPException(422, {"message": "policy должна быть auto|ask|deny"})
    policy = await _mcp_policy(svc)
    policy[canonical] = decision
    enc = svc.vault.encrypt(json.dumps(policy))
    async with svc.db.session() as s:
        await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == MCP_POLICY_KEY))
        await s.execute(sa.insert(settings_kv).values(key=MCP_POLICY_KEY, value_enc=enc))
        await s.commit()
    return {"canonical": canonical, "policy": decision}


async def _mcp_policy(svc) -> dict:
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(settings_kv.c.value_enc)
                               .where(settings_kv.c.key == MCP_POLICY_KEY))).first()
    if row and row[0]:
        try:
            return json.loads(svc.vault.decrypt(row[0]))
        except Exception:
            pass
    return {}


async def _setup(svc) -> None:
    """Результат скилла попадает на задачу через канонический хук движка."""
    async def after_run(task_id: int, run_id: int, status: str) -> None:
        await _record_skill_result(svc, int(task_id), int(run_id), str(status))
        await _after_skill_run(svc, int(task_id), int(run_id), str(status))

    svc.engine.add_hook("after_run", after_run)


FEATURE = Feature(name="skills", router=router, setup=_setup)
