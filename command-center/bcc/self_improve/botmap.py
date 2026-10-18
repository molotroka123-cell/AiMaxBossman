"""Bot map: every agent registered in Bossman plus the code-defined autonomous components.

Generated, never hand-edited. Inputs are READ-ONLY snapshots (``GET /api/agents``, ``/api/tasks``,
``/api/models`` and a few status endpoints) - this module performs no network call itself. System
prompts are NOT copied into the map: only their sha256 and length. Unknown facts are ``N/A``.

State vocabulary: running | idle | paused | disabled | never_run | N/A. ``running`` means a task of that agent is
queued/running/paused/waiting in the snapshot; ``never_run`` means the snapshot holds no task for it.
The snapshot window is recorded (``tasks_seen``), so ``never_run`` is "in this window", not forever.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

from .limits import NA

MAP_SCHEMA = 1
ACTIVE = ("queued", "running", "paused", "waiting_approval", "leased")

#: Code-defined components (not rows of the ``agents`` table). Every line was read from the named file's
#: own docstring; ``probe`` names the status endpoint the live state comes from (N/A when there is none).
STATIC_COMPONENTS: tuple[dict, ...] = (
    {"id": "component:autonomy-loop", "name": "Autonomy loop (Line A)", "source": "command-center/bcc/autonomy/",
     "purpose": "одна пишущая сессия (Claude/Codex CLI по подписке, либо бесплатный Nemotron-diff) и только "
                "читающие ревьюеры; цели, журнал, бюджет, STOP",
     "tools_data": "изолированный клон + ветка, брокер рук (HandBroker), журнал с хеш-цепочкой",
     "limits": "аренда Line A (одна пишущая), таймаут задачи, STOP-файлы, бюджет autonomy/budget.py, "
               "конституция v2 с пином",
     "owner": "владелец (решения, release_main - только его кнопка)", "probe": "autonomy"},
    {"id": "component:qwen-operator", "name": "Qwen Operator L1 Lab", "source": "command-center/bcc/features/qwen_operator.py",
     "purpose": "локальный Qwen как оператор лаборатории: план, правка в изолированном worktree, тесты, коммит в lab/*",
     "tools_data": "реестр моделей/провайдеров, coding-task sidecar, политика autonomy (пути/argv), очередь approvals",
     "limits": "MAX_ATTEMPTS=3 на ошибку, обязательная проверка, откат при ухудшении, STOP; push/merge/deploy "
               "отклоняются (promote refused)", "owner": "владелец (кнопки Approve/Deny)", "probe": "qwen"},
    {"id": "component:pc-agent-argon", "name": "Argon PC-Agent", "source": "command-center/bcc/features/pc_agent.py",
     "purpose": "управление Windows-компьютером владельца через единое ядро (задачи, tool_calls, approvals)",
     "tools_data": "экран/окна/процессы/файлы (T0), обратимые локальные действия (T1); T2 только после кнопки; "
                   "T3 запрещено (секреты, конституция, платежи)",
     "limits": "уровни доступа T0-T3, окно доверия /trust, append-only журнал, /stop", "owner": "владелец",
     "probe": "pc"},
    {"id": "component:evolution-loop", "name": "Evolution loop (bossman_v3.self_improvement)",
     "source": "command-center/bcc/features/evolution.py",
     "purpose": "ограниченный цикл самоисправления: лагерь в каталоге данных, кандидат не повышается в stable",
     "tools_data": "исходный checkout только в разрешённых code-roots, бесплатная модель",
     "limits": "число циклов 1..200, пауза после 3 подряд неудачных циклов, STOP владельца", "owner": "владелец",
     "probe": "evolution"},
    {"id": "component:limits-out-orchestra", "name": "Limits-out orchestra", "source": "tools/limits_out_orchestra.py",
     "purpose": "последовательность циклов tree_self_repair_cycle своими воркерами, когда лимиты ассистента кончились",
     "tools_data": "free worker, скрытый holdout, независимая проверка, отчёт в пульт",
     "limits": "--max-cycles/--max-hours, режим --paid none по умолчанию (cost_usd должен быть 0.0), "
               "STOP_ORCHESTRA, пауза при direct-gen, BELOW_NORMAL, never merges",
     "owner": "владелец", "probe": None},
    {"id": "component:tree-self-repair-cycle", "name": "Tree self-repair cycle", "source": "tools/tree_self_repair_cycle.py",
     "purpose": "один цикл самоисправления через запущенный Bossman со стадиями DEFECT_REPRODUCED ... TRANSFER_PASS",
     "tools_data": "изолированная копия, скрытый holdout", "limits": "стадии не повышаются последующими; "
     "APPLIED_RUNTIME_PASS не решается скриптом", "owner": "владелец", "probe": None},
    {"id": "component:jeff-pit", "name": "Jeff (PIT, Telegram-бот для друзей владельца)", "source": "command-center/bcc/pit/",
     "purpose": "Telegram-бот-помощник; отделён от бота-пульта владельца (отдельный токен, замок на токен)",
     "tools_data": "N/A (не проверялось; одобрения, STOP, Computer Use и coding живут только на боте-пульте)",
     "limits": "refuses to start with the companion token; per-token kernel lock", "owner": "владелец",
     "probe": None},
    {"id": "component:telegram-companion", "name": "Telegram companion (пульт)", "source": "command-center/bcc/telegram_companion/",
     "purpose": "отдельно запускаемый мост управления и отчётов владельца", "tools_data": "N/A",
     "limits": "import не запускает воркер/сеть/модель; пульт получает одобрения и STOP", "owner": "владелец",
     "probe": None},
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _agent_row(a: dict, tasks_by_agent: dict[int, list[dict]], models: dict[int, dict]) -> dict:
    ts = tasks_by_agent.get(a.get("id"), [])
    active = [t for t in ts if t.get("status") in ACTIVE]
    last = max(ts, key=lambda t: t.get("updated_at") or t.get("created_at") or "", default=None)
    if not a.get("enabled", True):
        state = "disabled"
    elif active:
        state = "running"
    elif not ts:
        state = "never_run"
    else:
        state = "idle"
    model = models.get(a.get("model_id")) or {}
    prompt = str(a.get("system_prompt") or "")
    perms = a.get("permissions") or {}
    return {
        "id": f"agent:{a.get('id')}", "name": a.get("name"), "kind": "db-agent",
        "purpose": a.get("role") or NA,
        "tools": list(a.get("tools") or []),
        "data_reach": sorted(k for k, v in perms.items() if v is True) if isinstance(perms, dict) else NA,
        "workspace": a.get("workspace") or NA,
        "limits": {"max_steps": a.get("max_steps"), "max_tokens": a.get("max_tokens"),
                   "budget_usd": a.get("budget_usd"), "time_limit": NA, "depth": NA},
        "owner": NA,
        "instruction": {"sha256": _sha(prompt) if prompt else NA, "chars": len(prompt)},
        "model": {"id": a.get("model_id"), "alias": model.get("alias") or NA, "kind": model.get("kind") or NA,
                  "status": model.get("status") or NA},
        "state": state,
        "tasks_seen": len(ts),
        "active_tasks": len(active),
        "last_task": {"id": last.get("id"), "status": last.get("status"), "at": last.get("updated_at")} if last else NA,
        "task_status_counts": dict(_count(t.get("status") for t in ts)),
    }


def _count(items: Iterable[Any]) -> dict:
    out: dict = defaultdict(int)
    for i in items:
        out[str(i)] += 1
    return out


def _static_state(comp: dict, probes: dict) -> dict:
    p = probes.get(comp.get("probe") or "")
    if not isinstance(p, dict):
        return {"state": NA, "evidence": "не проверялось (нет статусного endpoint или снимок не передан)"}
    if comp["probe"] == "autonomy":
        return {"state": "idle" if p.get("loop") == "READY" else str(p.get("loop", NA)),
                "evidence": f"/api/autonomy/status loop={p.get('loop')} goals_total={p.get('goals_total')}"}
    if comp["probe"] == "qwen":
        return {"state": "running" if p.get("busy") else "idle", "evidence": f"/api/qwen-operator/status busy={p.get('busy')}"}
    if comp["probe"] == "pc":
        stopped = p.get("stopped")
        return {"state": "disabled" if stopped else ("running" if p.get("active_tasks") else "idle"),
                "evidence": f"/api/pc-agent/status stopped={stopped} active_tasks={len(p.get('active_tasks') or [])}"}
    if comp["probe"] == "evolution":
        reason = (p.get("pause_requested") or {}).get("reason") if isinstance(p.get("pause_requested"), dict) else None
        st = "running" if p.get("loop_running") else ("paused" if "PAUSED" in str(p.get("status")) else "idle")
        return {"state": st,
                "evidence": f"/api/evolution/status status={p.get('status')} loop_running={p.get('loop_running')} "
                            f"halt_reason={p.get('halt_reason')} pause={reason}"}
    return {"state": NA, "evidence": NA}


def build_botmap(agents: list[dict], tasks: list[dict], models: list[dict], *, probes: dict | None = None,
                 source_sha: str = NA, runtime_build: str = NA, generated_at: str | None = None) -> dict:
    tasks_by_agent: dict[int, list[dict]] = defaultdict(list)
    for t in tasks:
        tasks_by_agent[t.get("agent_id")].append(t)
    mdl = {m.get("id"): m for m in models}
    rows = [_agent_row(a, tasks_by_agent, mdl) for a in sorted(agents, key=lambda x: x.get("id") or 0)]
    comps = []
    for c in STATIC_COMPONENTS:
        st = _static_state(c, probes or {})
        comps.append({"id": c["id"], "name": c["name"], "kind": "component", "purpose": c["purpose"],
                      "tools_data": c["tools_data"], "limits": c["limits"], "owner": c["owner"], "source": c["source"],
                      **st})
    body = {"schema_version": MAP_SCHEMA, "source_sha": source_sha, "runtime_build": runtime_build,
            "tasks_seen": len(tasks), "agents": rows, "components": comps}
    digest = _sha(json.dumps(body, sort_keys=True, ensure_ascii=False))
    summary = {"db_agents": len(rows), "components": len(comps)}
    for s in ("running", "idle", "disabled", "never_run"):
        summary[s] = sum(1 for r in rows if r["state"] == s)
    return {**body, "map_version": digest[:12], "content_sha256": digest, "summary": summary,
            "generated_at": generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def render_markdown(m: dict) -> str:
    s = m["summary"]
    L = [f"# Карта ботов Bossman (версия {m['map_version']})", "",
         f"Сгенерировано: {m['generated_at']} · исходный SHA: `{m['source_sha']}` · запущенная сборка: "
         f"`{m['runtime_build']}` · окно задач: {m['tasks_seen']}", "",
         f"Агентов в БД: **{s['db_agents']}** (работает {s['running']}, простаивает {s['idle']}, "
         f"выключено {s['disabled']}, ни разу не запускался {s['never_run']}); компонентов в коде: **{s['components']}**.",
         "", "`N/A` - данных нет, не выдумано. `never_run` означает «нет задач в окне снимка». "
         "Текст инструкций в карту не копируется, только sha256.", "",
         "## Компоненты (определены в коде)", "",
         "| ID | Назначение | Инструменты / данные | Лимиты | Состояние | Основание состояния |", "|---|---|---|---|---|---|"]
    for c in m["components"]:
        L.append(f"| {c['name']} | {_c(c['purpose'])} | {_c(c['tools_data'])} | {_c(c['limits'])} | "
                 f"**{c['state']}** | {_c(c['evidence'])} |")
    L += ["", "## Агенты в БД (GET /api/agents)", "",
          "| ID | Имя | Роль | Инструменты | Разрешения | Модель (статус) | Лимиты шаги/токены/USD | Состояние | Задач | Последняя | sha256 инструкции |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in m["agents"]:
        lim = r["limits"]
        last = r["last_task"] if r["last_task"] == NA else f"#{r['last_task']['id']} {r['last_task']['status']}"
        tools = ", ".join(r["tools"]) or "—"
        perms = ", ".join(r["data_reach"]) if isinstance(r["data_reach"], list) and r["data_reach"] else "—"
        L.append(f"| {r['id'].split(':')[1]} | {_c(r['name'])} | {_c(r['purpose'])} | {_c(tools)} | {_c(perms)} | "
                 f"{r['model']['alias']} ({r['model']['status']}) | {lim['max_steps']}/{lim['max_tokens']}/{lim['budget_usd']} | "
                 f"**{r['state']}** | {r['tasks_seen']} | {last} | `{str(r['instruction']['sha256'])[:12]}` |")
    L += ["", "Владелец агента, лимит времени и глубины делегирования в таблице `agents` не хранятся: `N/A`.", ""]
    return "\n".join(L)


def _c(v: Any) -> str:
    return str(v).replace("|", "/").replace("\n", " ")[:160]
