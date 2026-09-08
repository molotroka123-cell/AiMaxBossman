"""Creation-time executor selection; runtime admission and effect policy still apply.

An explicit draft may intentionally have no executor. An execution request may
not silently become such a draft or create a predictably blocked queue entry.
Selection grants no permissions and never edits the chosen agent.
"""
from __future__ import annotations

import sqlalchemy as sa

from . import db as dbm, model_health
from .features.action_contract import classify_all
from .features.action_router import CAPABILITY_BROWSER, classify as browser_classify
from .providers import ADAPTERS
from .tools import REGISTRY, agent_policy_rules, decide_effect


class ExecutorUnavailable(ValueError):
    def __init__(self, message: str):
        super().__init__(message)
        self.hint = ("Откройте «Агенты»: выберите включённого агента с настроенной моделью "
                     "и нужными инструментами. Проверьте модель на странице «Модели». "
                     "Задача не создана; текст можно отправить снова после исправления.")


def _required_families(prompt: str) -> list[tuple[str, list]]:
    tools = REGISTRY.all()
    result = []
    for cap in classify_all(prompt):
        matches = [tool for tool in tools if tool.source in cap.tool_sources]
        # Status/kill/stdin cannot start the command requested by these intents.
        if cap.name in {"TERMINAL_FILE_ACTION", "GITHUB_ACTION"}:
            matches = [tool for tool in matches if tool.name == "terminal.run"]
        elif cap.name == "CODE_ACTION":
            matches = [tool for tool in matches
                       if tool.name == "terminal.run" or tool.source == "opencode"]
        elif cap.name == "MEMORY_ACTION":
            matches = [tool for tool in matches if tool.category != "read"]
        result.append((cap.name, matches))
    if browser_classify(prompt) == CAPABILITY_BROWSER:
        result.append((CAPABILITY_BROWSER, [tool for tool in tools if tool.name == "browser.open"]))
    return result


async def select_executor(session, *, prompt: str, agent_id: int | None) -> dict:
    """Use the same registered families and DENY algebra as runtime contracts.

Explicit selection remains an owner choice: capability execution is checked by
the existing before_run/gate hooks. Automatic selection additionally refuses
intents for which this process has no registered tool family. Model health is
used to rank candidates, never fabricated or probed as a paid side effect here.
"""
    query = sa.select(dbm.agents).order_by(dbm.agents.c.id)
    if agent_id is not None:
        query = query.where(dbm.agents.c.id == agent_id)
    agents = dbm.rows_dicts((await session.execute(query)).fetchall())
    if not agents:
        raise ExecutorUnavailable("Выбранный агент удалён или недоступен." if agent_id is not None
                                  else "Нет настроенного исполнителя для задачи.")
    models = {row["id"]: row for row in dbm.rows_dicts(
        (await session.execute(sa.select(dbm.models))).fetchall())}
    providers = {row["id"]: row for row in dbm.rows_dicts(
        (await session.execute(sa.select(dbm.providers))).fetchall())}
    families = _required_families(prompt)
    candidates = []
    reasons = []
    for agent in agents:
        reason = None
        model = models.get(agent.get("model_id"))
        provider = providers.get((model or {}).get("provider_id"))
        if not agent.get("enabled"):
            reason = "Агент выключен. Включите его или выберите другого."
        elif model is None or provider is None:
            reason = "У агента нет доступной модели/провайдера: настройка отсутствует или удалена."
        elif provider.get("kind") not in ADAPTERS:
            reason = "Провайдер агента не поддерживается установленным приложением."
        if reason is not None:
            reasons.append(reason)
            continue
        health = model_health.HealthRecord.from_dict(model.get("health"))
        if agent_id is None and (health.status not in {model_health.HEALTHY, model_health.UNMEASURED}
                                 or model.get("status") in {"offline", "error"}):
            reasons.append("У доступных агентов модель не отвечает. Проверьте модель или выберите её явно для повтора.")
            continue
        if agent_id is None and families and (model.get("caps") or {}).get("tools") is False:
            reasons.append("Модель агента не поддерживает вызов инструментов для этой задачи.")
            continue
        for name, tools in families:
            if not tools:
                if agent_id is None:
                    reason = f"В установленном приложении нет исполнителя для {name}."
                    break
                continue
            if all(decide_effect(tool, {}, agent, agent_policy_rules(agent))[0] == "deny"
                   for tool in tools):
                reason = f"Политика выбранного агента запрещает {name}. Выберите другого агента или измените политику."
                break
        if reason is not None:
            reasons.append(reason)
            continue
        candidates.append((health.rank_key(), int(agent["id"]), agent))
    if not candidates:
        raise ExecutorUnavailable(reasons[0] if reasons else "Нет подходящего исполнителя для задачи.")
    return min(candidates, key=lambda item: item[:2])[2]
