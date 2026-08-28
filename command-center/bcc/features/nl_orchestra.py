"""Feature 11 — Natural Language Orchestration + Permissions compiler.

Детерминированный разбор русского/английского описания команды поверх пак-схемы
bcc/v2/orchestration_schema.OrchestraDraft: имена моделей/агентов сверяются с БД
(fuzzy подстрока), лимиты/бюджет/approval-политика распознаются. Ничего не
создаётся до confirm; невалидная модель → блокер (valid=false).
"""
from __future__ import annotations

import re

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from ..db import agents as agents_t, models as models_t, orchestras as orch_t
from ..db import orchestra_members as members_t, utcnow
from ..v2.orchestration_schema import OrchestraDraft
from . import Feature

router = APIRouter()


def _num_before(text: str, keywords: list[str]) -> int | None:
    for kw in keywords:
        m = re.search(rf"(\d+)\s*{kw}", text) or re.search(rf"{kw}\D{{0,12}}(\d+)", text)
        if m:
            return int(m.group(1))
    return None


def _budget(text: str) -> float | None:
    m = re.search(r"\$\s*(\d+(?:[.,]\d+)?)", text) or re.search(
        r"budget\D{0,12}(\d+(?:[.,]\d+)?)", text, re.I) or re.search(
        r"бюджет\D{0,12}(\d+(?:[.,]\d+)?)", text, re.I)
    return float(m.group(1).replace(",", ".")) if m else None


async def _known(svc) -> tuple[dict, dict]:
    """Словари alias/имя → id для моделей и агентов (для fuzzy-сверки)."""
    async with svc.db.session() as s:
        models = (await s.execute(sa.select(models_t.c.id, models_t.c.alias))).fetchall()
        agents = (await s.execute(sa.select(agents_t.c.id, agents_t.c.name))).fetchall()
    return ({m._mapping["alias"].lower(): m._mapping for m in models},
            {a._mapping["name"].lower(): a._mapping for a in agents})


def _unknown_names(text: str, known_names: list[str]) -> list[str]:
    """Токены вида «слово-с-дефисом» с ролевым словом рядом, которых нет в реестре
    (например ghost-model). Обычные слова без дефиса не считаем именами."""
    known_low = {n.lower() for n in known_names}
    out: list[str] = []
    for m in re.finditer(r"([a-zA-Z][a-zA-Z0-9]*-[a-zA-Z0-9\-]+)", text):
        tok = m.group(1)
        if tok.lower() in known_low:
            continue
        window = text[max(0, m.start() - 5):m.end() + 40]
        if any(k in window.lower() for k in MANAGER_KW + REVIEWER_KW + WORKER_KW):
            out.append(tok)
    return out


def _match(name: str, known: dict) -> dict | None:
    n = name.lower().strip()
    if n in known:
        return known[n]
    for key, val in known.items():          # fuzzy: подстрока
        if n and (n in key or key in n):
            return val
    return None


MANAGER_KW = ("главн", "manager", "менеджер", "руковод")
REVIEWER_KW = ("fallback", "reviewer", "ревьюер", "проверя", "запасн")
WORKER_KW = ("worker", "воркер", "исполнит")


def _role_for(context: str) -> str:
    """Роль по ключевым словам рядом с именем."""
    c = context.lower()
    if any(k in c for k in MANAGER_KW):
        return "manager"
    if any(k in c for k in REVIEWER_KW):
        return "reviewer"
    return "worker"


def _parse(text: str, names: list[str]) -> dict:
    """Режем текст на фрагменты (запятая/точка/;) и в каждом ищем ИЗВЕСТНОЕ имя +
    его роль ИЗ ЭТОГО ЖЕ фрагмента — так соседнее имя не влияет на роль. «Создай»/
    «Максимум» не принимаются за имена: сверка только с реестром."""
    roles: dict = {"manager": None, "reviewer": None, "workers": []}
    for frag in re.split(r"[,.\n;]", text):
        fl = frag.lower()
        for name in names:
            if name.lower() not in fl:
                continue
            role = _role_for(fl)
            if role == "manager":
                roles["manager"] = name
            elif role == "reviewer":
                roles["reviewer"] = name
            elif name not in roles["workers"]:
                roles["workers"].append(name)
    return roles


@router.post("/orchestras/parse")
async def parse(request: Request):
    """NL → structured preview. НИЧЕГО не создаёт. Невалидная модель → valid=false."""
    svc = request.app.state.svc
    body = await request.json()
    text = body.get("text", "")
    models, agents = await _known(svc)
    known_names = list(models.keys()) + list(agents.keys())
    roles = _parse(text, known_names)
    # выявим упомянутые, но неизвестные имена (напр. «ghost-model главный»)
    unknown = _unknown_names(text, known_names)

    warnings: list[str] = []
    members: list[dict] = []
    created_agents: list[str] = []

    def resolve(name: str, role: str):
        if name is None:
            return
        a = _match(name, agents)
        m = _match(name, models)
        if a:
            members.append({"agent_id": a["id"], "agent_name": a["name"], "role": role})
        elif m:
            # под модель нет агента → предложим создать агента-обёртку на confirm
            created_agents.append(m["alias"])
            members.append({"model_id": m["id"], "model_alias": m["alias"], "role": role,
                            "create_agent": True})
        else:
            warnings.append(f"не найдена модель/агент: {name}")

    resolve(roles["manager"], "manager")
    resolve(roles["reviewer"], "reviewer")
    for w in roles["workers"]:
        resolve(w, "worker")
    for u in unknown:
        warnings.append(f"не найдена модель/агент: {u}")

    max_workers = _num_before(text.lower(), ["workers?", "воркер", "исполнит"]) or 1
    hours = _num_before(text.lower(), ["hours?", "час"])
    budget = _budget(text)
    approval = "required" if re.search(r"approv|подтвержд|dangerous|опасн", text, re.I) else "auto"

    draft = OrchestraDraft(
        name=body.get("name", "NL-команда"),
        manager_agent=roles["manager"], reviewer_agent=roles["reviewer"],
        worker_agents=roles["workers"], max_workers=max_workers,
        max_runtime_minutes=(hours or 1) * 60, cloud_budget_usd=budget or 0.0,
        permissions={"dangerous": approval})
    valid = not warnings and 1 <= max_workers <= 32
    return {"orchestra": {"name": draft.name, "mode": "manager",
                          "config": {"max_workers": max_workers, "duration_hours": hours,
                                     "cloud_budget_usd": budget, "approval_policy": approval}},
            "members": members, "created_agents": created_agents,
            "warnings": warnings, "valid": valid}


@router.post("/orchestras/confirm")
async def confirm(request: Request):
    """Создать оркестр ТОЛЬКО из валидного preview (повторная валидация на сервере)."""
    svc = request.app.state.svc
    preview = await request.json()
    if not preview.get("valid"):
        raise HTTPException(422, {"message": "конфигурация невалидна",
                                  "hint": "; ".join(preview.get("warnings", []))})
    o = preview["orchestra"]
    cfg = o.get("config", {})
    async with svc.db.session() as s:
        oid = int((await s.execute(sa.insert(orch_t).values(
            name=o["name"], mode=o.get("mode", "manager"), config=cfg,
            created_at=utcnow()))).inserted_primary_key[0])
        pos = 0
        for member in preview.get("members", []):
            agent_id = member.get("agent_id")
            if agent_id is None and member.get("create_agent"):
                # агент-обёртка под модель
                agent_id = int((await s.execute(sa.insert(agents_t).values(
                    name=f"agent-{member['model_alias']}", model_id=member["model_id"],
                    created_at=utcnow()))).inserted_primary_key[0])
            if agent_id is None:
                continue
            await s.execute(sa.insert(members_t).values(
                orchestra_id=oid, agent_id=agent_id, role=member["role"], position=pos))
            pos += 1
        await s.commit()
    return {"orchestra_id": oid, "members": pos}


@router.post("/orchestras")
async def create_orchestra(request: Request):
    """Прямое создание оркестра (без NL) — для API/тестов."""
    svc = request.app.state.svc
    body = await request.json()
    async with svc.db.session() as s:
        oid = int((await s.execute(sa.insert(orch_t).values(
            name=body["name"], mode=body.get("mode", "manager"),
            config=body.get("config", {}), created_at=utcnow()))).inserted_primary_key[0])
        for pos, member in enumerate(body.get("members", [])):
            await s.execute(sa.insert(members_t).values(
                orchestra_id=oid, agent_id=member["agent_id"],
                role=member.get("role", "worker"), position=pos))
        await s.commit()
    return {"orchestra_id": oid}


FEATURE = Feature(name="nl_orchestra", router=router)
