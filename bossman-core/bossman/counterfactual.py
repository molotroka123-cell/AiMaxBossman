"""V2.6 — Counterfactual Verifier (модуль D): «какие минимальные допущения
сделали бы это решение неверным?»

НЕ бесконечная LLM-критика: детерминированная генерация МАКСИМУМ
MAX_ASSUMPTIONS критических допущений по типу действия. Активация — только для
необратимых/чувствительных действий и высокой неопределённости (should_verify);
дёшево (микросекунды). Допущения попадают в approval-preview: владелец видит,
ЧТО должно быть правдой, чтобы действие было безопасным. Проверка допущений —
задача существующих verifier/observation-контуров, не этого модуля.
"""
from __future__ import annotations

from dataclasses import dataclass

from .signals import DecisionSignals

MAX_ASSUMPTIONS = 3


@dataclass(frozen=True, slots=True)
class Assumption:
    text: str          # что предполагается верным
    check_kind: str    # observation | endpoint | state | fact
    ref: str = ""      # что перепроверить (инструмент/наблюдение)


# Детерминированные допущения по префиксу/имени инструмента. Первые
# MAX_ASSUMPTIONS выигрывают — bounded по построению.
_BY_TOOL: dict[str, tuple[Assumption, ...]] = {
    "gmail.send": (
        Assumption("адресат и содержимое актуальны — письмо нельзя отозвать",
                   "fact", "перечитать черновик"),
        Assumption("в тексте нет секретов/внутренних данных", "state", "egress_guard"),
    ),
    "crm.write": (
        Assumption("запись CRM не изменилась с момента чтения", "observation", "crm.read"),
    ),
    "run": (
        Assumption("команда выполняется в песочнице/на согласованном хосте",
                   "state", "sandbox_mode"),
        Assumption("команда не удаляет/не перезаписывает невосстановимое",
                   "fact", "предпросмотр команды"),
    ),
    "tests": (
        Assumption("команда выполняется в песочнице/на согласованном хосте",
                   "state", "sandbox_mode"),
    ),
    "http": (
        Assumption("endpoint существует и это ПРАВИЛЬНЫЙ endpoint", "endpoint", "url"),
        Assumption("в заголовках/теле нет секретов сверх необходимых", "state", "egress_guard"),
    ),
}

_BY_PREFIX: tuple[tuple[str, tuple[Assumption, ...]], ...] = (
    ("browser.confirmed_", (
        Assumption("состояние страницы совпадает со СВЕЖИМ наблюдением "
                   "(DOM мог обновиться)", "observation", "browser.observe"),
        Assumption("клик/ввод затрагивает именно тот элемент, который виден в "
                   "предпросмотре", "observation", "browser.observe"),
        Assumption("действие не платёж/перевод вне согласованного", "fact", "policy"),
    )),
    ("fs.", (
        Assumption("файл не изменился с момента последнего чтения", "observation", "fs.read"),
    )),
    ("sandbox.", (
        Assumption("политика песочницы соответствует риску задачи", "state", "sandbox.status"),
    )),
)


def critical_assumptions(tool_name: str, args: dict | None = None) -> tuple[Assumption, ...]:
    """<= MAX_ASSUMPTIONS допущений для действия; пусто — если ничего
    специфичного (дефолтное «наблюдение свежо» добавляется для write/exec
    вызывающим по надобности)."""
    out: list[Assumption] = []
    exact = _BY_TOOL.get(tool_name)
    if exact:
        out.extend(exact)
    else:
        for prefix, assumptions in _BY_PREFIX:
            if tool_name.startswith(prefix):
                out.extend(assumptions)
                break
    return tuple(out[:MAX_ASSUMPTIONS])


def should_verify(signals: DecisionSignals) -> bool:
    """Активация: необратимость/чувствительность ИЛИ высокая неопределённость
    при слабом evidence. Дешёвые обычные действия не платят."""
    return (signals.risk >= 0.5
            or signals.uncertainty >= 0.7
            or (signals.uncertainty >= 0.5 and signals.evidence_confidence < 0.5))


def render_for_preview(assumptions: tuple[Assumption, ...]) -> str:
    """Блок для approval-preview: владелец видит, что должно быть правдой."""
    if not assumptions:
        return ""
    lines = ["", "Что должно быть правдой (counterfactual check):"]
    lines += [f"• {a.text} [{a.check_kind}]" for a in assumptions]
    return "\n".join(lines)
