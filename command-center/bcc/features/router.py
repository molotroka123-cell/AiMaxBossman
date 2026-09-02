"""Feature 02 — Smart Model Router.

Обвязка готовой чистой логики bcc/v2/model_router.route над реестром моделей:
регистрирует хук engine.pick_model, кладёт объяснение маршрута в task_runs.route
и шлёт router.route_selected. Логику скоринга не переписываем (пак — owner).

V2.6 (модули B/G, за правилом rules["adaptive"]=true, OFF по умолчанию):
- classify_reasoning (L0–L4, bcc/v2/model_intelligence) наконец подключён к
  реальному pick_model — уровень выводится детерминированно из промпта/меты и
  ложится в route.reasoning (виден в /router/explain);
- success_rate становится per-(model, task.kind), консервативно: класс-метрика
  используется только при n >= CLASS_MIN_EPISODES, иначе fallback на глобальную
  (никаких выводов из одного эпизода).
"""
from __future__ import annotations

import json
import re

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from ..db import (models as models_t, providers as providers_t, settings_kv,
                  task_runs as runs_t, tasks as tasks_t)
from ..v2.model_intelligence import TaskComplexityFeatures, classify_reasoning
from ..v2.model_router import (MAX_CANDIDATES, ModelCandidate, RouteRequest,
                               candidate_digest, derive_local, disqualify, route,
                               shortlist)
from ..v2.tables import model_capability_checks as caps_t
from . import Feature

RULES_KEY = "router.rules"
DEFAULT_RULES = {
    # какие способности требует тип задачи (task.kind → verified/advertised caps)
    "requires": {"coding": ["coding"], "vision": ["vision"], "review": ["coding"]},
    # роль-скоры моделей по типу задачи можно доуточнять из UI; дефолт — из caps
    "prefer_local": True,
}

# V2.6: класс-специфичная метрика допускается только при достаточной выборке.
CLASS_MIN_EPISODES = 5

_MULTI_STEP_RE = re.compile(
    r"(затем|потом|после (этого|чего)|шаг|этап|and then|after that|step \d|"
    r"сравн|мигрир|migrate|refactor)", re.I)
_MUTATION_RE = re.compile(
    r"(удали|delete|drop |перепиши|rewrite|deploy|деплой|push|commit|измени|"
    r"замени|write|запиши)", re.I)
_SECURITY_RE = re.compile(
    r"(secret|секрет|парол|password|token|ключ|креденш|credential|прав[а ]|"
    r"доступ|policy|полит)", re.I)


def complexity_features(prompt: str, meta: dict, *,
                        previous_failures: int = 0) -> TaskComplexityFeatures:
    """Детерминированный вывод фич сложности из текста/меты (без LLM/ML)."""
    text = prompt or ""
    steps = len(_MULTI_STEP_RE.findall(text))
    allowed = meta.get("allowed_tools")
    tool_count = len(allowed) if isinstance(allowed, list) else 0
    return TaskComplexityFeatures(
        dependent_steps=min(steps, 8),
        security_impact=0.8 if _SECURITY_RE.search(text) else 0.0,
        mutation_impact=0.8 if _MUTATION_RE.search(text) else 0.0,
        previous_failures=previous_failures,
        ambiguity=float(meta.get("ambiguity") or 0.0),
        tool_count=tool_count,
        requires_verification=bool(meta.get("review")),
        code_change_scope=0.7 if _MUTATION_RE.search(text) and "код" in text.lower()
        else 0.0)

router = APIRouter()


def _requires(kind: str, rules: dict) -> set[str]:
    return set((rules.get("requires") or {}).get(kind, []))


async def _rules(svc) -> dict:
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(settings_kv.c.value_enc)
                               .where(settings_kv.c.key == RULES_KEY))).first()
    if row and row[0]:
        try:
            return json.loads(svc.vault.decrypt(row[0]))
        except Exception:
            pass
    return dict(DEFAULT_RULES)


async def _save_rules(svc, rules: dict) -> None:
    enc = svc.vault.encrypt(json.dumps(rules))
    async with svc.db.session() as s:
        await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == RULES_KEY))
        await s.execute(sa.insert(settings_kv).values(key=RULES_KEY, value_enc=enc))
        await s.commit()


async def _verified_caps(svc) -> dict[int, tuple[set[str], set[str]]]:
    """model_id → (verified=True, verified=False) по ПОСЛЕДНЕЙ пробе каждой способности.

    Источник — model_capability_checks (пишет feature openrouter). Строки только
    добавляются, поэтому «последняя» = максимальный id.
    """
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(
            caps_t.c.id, caps_t.c.model_id, caps_t.c.capability, caps_t.c.verified)
            .order_by(caps_t.c.id.desc()))).fetchall()
    out: dict[int, tuple[set[str], set[str]]] = {}
    seen: set[tuple[int, str]] = set()
    for r in rows:
        c = r._mapping
        key = (c["model_id"], c["capability"])
        if key in seen:
            continue                      # более старая проба той же способности
        seen.add(key)
        if c["verified"] is None:
            continue                      # проба не дала ответа → «неизвестно»
        ok, fail = out.setdefault(c["model_id"], (set(), set()))
        (ok if c["verified"] else fail).add(c["capability"])
    return out


async def _candidates(svc, rules: dict, *,
                      kind: str | None = None) -> list[ModelCandidate]:
    """Кандидаты из реестра + живой сигнал (health, bench, доля успехов, пробы).

    V2.6 модуль G: при rules["adaptive"] и заданном kind доля успехов берётся
    per-(alias, task.kind), но ТОЛЬКО при n >= CLASS_MIN_EPISODES — консервативно,
    без выводов из единичных эпизодов; иначе глобальная per-alias.
    """
    adaptive = bool(rules.get("adaptive"))
    async with svc.db.session() as s:
        # F-016: «местность» выводится из модели И провайдера (kind + base_url),
        # а не из одной строки kind=local, которую легко проставить неверно.
        models = (await s.execute(
            sa.select(models_t,
                      providers_t.c.kind.label("provider_kind"),
                      providers_t.c.base_url.label("provider_base_url"))
            .select_from(models_t.outerjoin(
                providers_t, models_t.c.provider_id == providers_t.c.id)))).fetchall()
        # доля успешных run'ов по alias (historical performance)
        stats = (await s.execute(sa.select(
            runs_t.c.model_alias,
            sa.func.count().label("n"),
            sa.func.sum(sa.case((runs_t.c.status == "completed", 1), else_=0)).label("ok"))
            .where(runs_t.c.model_alias.isnot(None)).group_by(runs_t.c.model_alias))).fetchall()
        class_stats = []
        if adaptive and kind:
            class_stats = (await s.execute(sa.select(
                runs_t.c.model_alias,
                sa.func.count().label("n"),
                sa.func.sum(sa.case((runs_t.c.status == "completed", 1), else_=0)).label("ok"))
                .select_from(runs_t.join(tasks_t, runs_t.c.task_id == tasks_t.c.id))
                .where(sa.and_(runs_t.c.model_alias.isnot(None),
                               tasks_t.c.kind == kind))
                .group_by(runs_t.c.model_alias))).fetchall()
    success = {r._mapping["model_alias"]: (r._mapping["ok"] or 0) / r._mapping["n"]
               for r in stats if r._mapping["n"]}
    for r in class_stats:
        m = r._mapping
        if m["n"] >= CLASS_MIN_EPISODES:      # достаточная выборка по классу
            success[m["model_alias"]] = (m["ok"] or 0) / m["n"]
    probes = await _verified_caps(svc)
    role_scores = rules.get("role_scores") or {}
    out: list[ModelCandidate] = []
    for r in models:
        m = r._mapping
        raw_caps = m["caps"] if isinstance(m["caps"], dict) else {}
        # реестр хранит ЗАЯВЛЕННЫЕ способности; verified/falsified — только из проб
        advertised = {k for k, v in raw_caps.items() if v}
        verified, unsupported = probes.get(m["id"], (set(), set()))
        bench = m["bench"] if isinstance(m["bench"], dict) else {}
        local, _why = derive_local(m["kind"], m["provider_kind"], m["provider_base_url"])
        out.append(ModelCandidate(
            id=m["id"], alias=m["alias"],
            online=m["status"] == "online",
            local=local,
            context_window=m["context_window"] or 8192,
            capabilities=advertised,
            verified_capabilities=set(verified),
            unsupported_capabilities=set(unsupported),
            price_in=m["price_in"] or 0.0, price_out=m["price_out"] or 0.0,
            latency_ms=bench.get("latency_ms"), gen_tps=bench.get("gen_tps"),
            success_rate=success.get(m["alias"]),
            role_scores=role_scores.get(m["alias"], {})))
    return out


def cloud_policy(meta: dict, agent: dict | None, rules: dict) -> tuple[bool, str]:
    """F-016: облако fail-closed. (разрешено?, объяснение).

    Облако открывает ТОЛЬКО строгий bool True в одном из мест:
      - task.meta.cloud_allowed;
      - agent.permissions.cloud_allowed;
      - rules.cloud_default_allow (глобальный дефолт роутера).
    Любое явное значение, отличное от True (False, "true", 1, "yes"), — отказ:
    оно перевешивает и глобальный дефолт. Явный cloud_budget_usd <= 0 (или не
    число) — лимит, закрывающий облако даже при разрешении. Отсутствие меты
    и бюджета — НЕ разрешение.
    """
    meta = meta if isinstance(meta, dict) else {}
    perms = (agent or {}).get("permissions") if isinstance(agent, dict) else None
    perms = perms if isinstance(perms, dict) else {}
    grants: list[str] = []
    for label, value in (("task.meta.cloud_allowed", meta.get("cloud_allowed")),
                         ("agent.permissions.cloud_allowed", perms.get("cloud_allowed"))):
        if value is None:
            continue
        if value is not True:
            return False, f"{label}={value!r}: облако открывает только строгое true"
        grants.append(label)
    if not grants and rules.get("cloud_default_allow") is True:
        grants.append("router.rules.cloud_default_allow")
    if not grants:
        return False, ("облако не разрешено явно (нужен true в task.meta.cloud_allowed, "
                       "agent.permissions.cloud_allowed или rules.cloud_default_allow)")
    budget = meta.get("cloud_budget_usd")
    if budget is not None:
        if (isinstance(budget, bool) or not isinstance(budget, (int, float))
                or budget != budget or budget <= 0):
            return False, f"cloud_budget_usd={budget!r}: облачный бюджет закрыт"
    return True, "; ".join(grants)


async def check_forced_model(svc, model_id, *, meta: dict | None, agent: dict | None,
                             kind: str | None) -> list[str]:
    """F-016: принудительная модель (meta.force_model_id, форк с model_id)
    проходит ТУ ЖЕ жёсткую политику, что и авто-выбор: облако fail-closed,
    цена, способности, здоровье. Пустой список = модель допущена; иначе —
    причины отказа (строки disqualify + пояснение облачной политики).
    """
    rules = await _rules(svc)
    meta = meta if isinstance(meta, dict) else {}
    kind = kind or "generic"
    try:
        mid = int(model_id)
    except (TypeError, ValueError):
        return [f"unknown model id {model_id!r}"]
    allowed, why = cloud_policy(meta, agent, rules)
    req = RouteRequest(
        task_type=kind, requires=_requires(kind, rules),
        min_context=int(meta.get("min_context") or 0),
        cloud_allowed=allowed,
        max_price_out=meta.get("max_price_out"),
        available_memory_mb=meta.get("available_memory_mb"),
        prefer_local=bool(rules.get("prefer_local", True)),
        require_verified=bool(rules.get("require_verified", False)))
    cand = next((c for c in await _candidates(svc, rules, kind=kind)
                 if int(c.id) == mid), None)
    if cand is None:
        return [f"unknown model id {mid}"]
    bad = disqualify(req, cand)
    if "cloud disabled" in bad:
        bad.append(f"cloud policy: {why}")
    return bad


async def _make_pick_hook(svc):
    async def pick_model(task, agent):
        rules = await _rules(svc)
        meta = task.get("meta") if isinstance(task.get("meta"), dict) else {}
        # роутер включается флагом задачи (meta.route=true) или типом kind≠generic;
        # иначе оставляем модель агента (None) — не ломаем существующее поведение
        kind = task.get("kind") or "generic"
        if not (meta.get("route") or kind not in ("generic", None)):
            return None
        # F-016: облако fail-closed — без явного разрешения кандидаты только местные
        cloud_allowed, cloud_why = cloud_policy(meta, agent, rules)
        prefer_local = bool(rules.get("prefer_local", True))
        require_verified = bool(rules.get("require_verified", False))
        reasoning_info = None
        if rules.get("adaptive"):
            # V2.6 модуль B: L0–L4 из детерминированных фич; прошлые провалы —
            # из task_runs (та же БД, один индексный запрос на РОУТИРУЕМУЮ задачу).
            async with svc.db.session() as s:
                failed = (await s.execute(sa.select(sa.func.count()).where(sa.and_(
                    runs_t.c.task_id == task["id"],
                    runs_t.c.status == "failed")))).scalar() or 0
            feats = complexity_features(task.get("prompt") or "", meta,
                                        previous_failures=int(failed))
            level, level_reasons = classify_reasoning(feats)
            reasoning_info = {"level": level, "reasons": level_reasons}
            if level in ("L3", "L4"):
                # сильный уровень: локальность не приоритет, непроверенные
                # способности отсеиваются (verified-only)
                prefer_local = False
                require_verified = True
            # L0/L1 оставляют prefer_local как есть (обычно True) — дёшево/локально
        req = RouteRequest(
            task_type=kind, requires=_requires(kind, rules),
            min_context=int(meta.get("min_context") or 0),
            cloud_allowed=cloud_allowed,
            max_price_out=meta.get("max_price_out"),
            available_memory_mb=meta.get("available_memory_mb"),
            prefer_local=prefer_local,
            max_candidates=int(rules.get("max_candidates") or MAX_CANDIDATES),
            require_verified=require_verified)
        decision = route(req, await _candidates(svc, rules, kind=kind))
        if decision.model is None:
            return None            # никого не выбрали → модель агента
        route_info = {"alias": decision.model.alias, "score": decision.score,
                      "reasons": decision.reasons, "rejected": decision.rejected,
                      "task_type": kind, "considered": decision.considered,
                      "total_candidates": decision.total,
                      "cloud_allowed": cloud_allowed, "cloud_policy": cloud_why}
        if reasoning_info is not None:
            route_info["reasoning"] = reasoning_info
        await svc.bus.emit("router.route_selected", task_id=task["id"],
                           model_id=decision.model.id, alias=decision.model.alias,
                           reason="; ".join(decision.reasons)[:300])
        return {"model_id": int(decision.model.id), "route": route_info}
    return pick_model


async def _setup(svc):
    svc.engine.add_hook("pick_model", await _make_pick_hook(svc))


@router.get("/router/rules")
async def get_rules(request: Request):
    return await _rules(request.app.state.svc)


@router.patch("/router/rules")
async def patch_rules(request: Request):
    svc = request.app.state.svc
    body = await request.json()
    rules = await _rules(svc)
    rules.update(body or {})
    await _save_rules(svc, rules)
    return rules


@router.get("/router/explain")
async def explain(request: Request, task_id: int):
    """Объяснение последнего маршрута задачи — из task_runs.route."""
    svc = request.app.state.svc
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(runs_t.c.route, runs_t.c.model_alias)
                               .where(runs_t.c.task_id == task_id)
                               .order_by(runs_t.c.id.desc()).limit(1))).first()
    if row is None:
        raise HTTPException(404, {"message": "у задачи нет прогонов"})
    return {"route": row._mapping["route"], "model_alias": row._mapping["model_alias"]}


@router.post("/router/preview")
async def preview(request: Request):
    """Показать выбор роутера для гипотетической задачи, ничего не выполняя."""
    svc = request.app.state.svc
    body = await request.json()
    rules = await _rules(svc)
    req = await _request_from(svc, body, rules)
    d = route(req, await _candidates(svc, rules))
    # score=-inf (никого не выбрали) не сериализуется в JSON → отдаём null
    return {"selected": d.model.alias if d.model else None,
            "score": d.score if d.model else None,
            "reasons": d.reasons, "rejected": d.rejected,
            "considered": d.considered, "total_candidates": d.total}


async def _request_from(svc, body: dict, rules: dict) -> RouteRequest:
    kind = body.get("task_type") or "generic"
    requires = set(body.get("requires") or []) | _requires(kind, rules)
    return RouteRequest(
        task_type=kind, requires=requires,
        min_context=int(body.get("min_context") or 0),
        cloud_allowed=bool(body.get("cloud_allowed", True)),
        max_price_out=body.get("max_price_out"),
        available_memory_mb=body.get("available_memory_mb"),
        prefer_local=bool(rules.get("prefer_local", True)),
        max_candidates=int(body.get("max_candidates")
                           or rules.get("max_candidates") or MAX_CANDIDATES),
        require_verified=bool(body.get("require_verified",
                                       rules.get("require_verified", False))))


@router.post("/router/candidates")
async def candidates(request: Request):
    """Ограниченный shortlist кандидатов (compact digest).

    Наружу и в промпт уезжает ТОЛЬКО этот список, а не весь реестр/каталог:
    длина гарантированно ≤ max_candidates.
    """
    svc = request.app.state.svc
    body = await request.json() if await request.body() else {}
    rules = await _rules(svc)
    req = await _request_from(svc, body, rules)
    all_models = await _candidates(svc, rules)
    picked, rejected = shortlist(req, all_models)
    return {"limit": req.max_candidates, "total": len(all_models),
            "candidates": [candidate_digest(m) for m in picked],
            "rejected": rejected}


FEATURE = Feature(name="router", router=router, setup=_setup)
