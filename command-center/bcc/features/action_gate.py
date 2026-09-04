"""Feature — Action Completion Gate.

Testing-period corrective patch BCC-V2-SESSION-20783913FA36-P1-FIX-001.

Наблюдённый дефект (docs/testing/sessions/*20783913fa36*): владелец попросил
«Открой на моём компьютере в браузере... YouTube... Never Gonna Give You Up».
У выбранного агента не было привязано ни одного инструмента (agents.tools
пуст — так и задумано: «в MVP пусто, опасных инструментов нет», см. bcc/db.py).
Модель честно ответила текстом: «я не могу напрямую управлять вашим
компьютером, сделайте это вручную» — и прогон всё равно завершился completed,
потому что review_gate (единственный на тот момент гейт gate_completion)
настраивается ОПЦИОНАЛЬНО через /review/enable, для этой задачи настроен не
был и потому честно вернул NOT_APPLICABLE. Движок трактует «ни одного FAIL» как
«можно завершать» — то есть NOT_APPLICABLE одного гейта молча читался как
«действие подтверждено», хотя гейт всего лишь промолчал.

Это отдельный, независимый от review_gate гейт `gate_completion`. Он никогда
не выставляет PASS — только VETO (FAIL) явно опознанного случая: ответ
модели — текстовый отказ выполнить действие самой («сделайте это вручную»),
а в этом прогоне НЕТ НИ ОДНОГО вызова инструмента вообще (bcc.db.tool_calls;
тот же «чек изменённого состояния», что использует review_gate для
verified evidence). Наличие хотя бы одного вызова инструмента — не важно,
исполнен он, отклонён политикой или запрошено подтверждение, — означает, что
модель ДЕЙСТВИТЕЛЬНО пыталась пройти инструментальный путь, и здесь мнения
нет: NOT_APPLICABLE.

Что этот файл НЕ делает:
  * не читает task.title/task.prompt — классификация «это ли action-задача»
    строится не по формулировке задачи, а по СОБСТВЕННОМУ ответу модели
    (отказ от прямого исполнения) и по факту отсутствия инструментальных
    вызовов — это применимо к любой модели и любой формулировке задачи;
  * не завязан на конкретную модель (qwen2.5 и т.п.) — фразы отказа общие,
    на двух языках интерфейса (RU/EN), и относятся к КЛАССУ «не могу
    действовать напрямую», а не к конкретной формулировке одного раннера;
  * не исполняет инструменты сам и не подделывает их исполнение;
  * не изобретает новых терминальных статусов — использует существующий
    словарь tasks.status (failed), как и остальные гейты движка.
"""
from __future__ import annotations

import re

import sqlalchemy as sa

from ..db import agents as agents_t, tasks as tasks_t, tool_calls as tool_calls_t, utcnow
from ..tools import allowed_tools_for
from . import Feature

# Первое лицо: модель говорит о СЕБЕ, что не может действовать («I cannot»,
# «я не могу»), а не общее слово «нельзя»/«cannot» из объяснения кода —
# первое лицо отсекает «This function cannot return None» и подобные.
#
# После кириллических фраз — НЕ `\b`, а свой негативный просмотр вперёд
# (не кириллица/латиница). `\b` в Python Unicode-aware: иероглиф CJK — тоже
# `\w`, поэтому «могу» вплотную к «直接» (реальный ответ сессии 20783913fa36:
# «Я не могу直接操作您的计算机», модель без пробела переключилась на
# китайский) НЕ образует границу слова, и `\b` там молча не совпадает.
_NOT_LETTER_AHEAD = r"(?![a-zа-яё])"
_SELF_DENIAL = (rf"(\bi\s*can(?:no|')t\b|\bi\s+am\s+unable\s+to\b|\bi'?m\s+unable\s+to\b|"
               rf"\bi\s+do\s?n['o]?t\s+have\s+(the\s+)?(ability|access)\b|"
               rf"я\s+не\s+могу{_NOT_LETTER_AHEAD}|"
               rf"я\s+не\s+имею\s+(прямого\s+)?доступа{_NOT_LETTER_AHEAD})")
# Рядом обязана быть тема «управление устройством», иначе «я не могу» ловило
# бы и безобидные оговорки («не могу гарантировать», «cannot guarantee»).
# EN/RU/CJK: локальные модели иногда переключаются на китайский посреди
# ответа — это наблюдалось в реальной сессии 20783913fa36 («Я не могу直接
# 操作您的计算机») — общее свойство многоязычных весов, а не код под одну
# модель: проверки на имя/семейство модели в этом файле нет.
_DEVICE_TOPIC = (r"(control|operate|access|click|open|interact|computer|browser|device|"
                r"управля|контролир|открыт|нажат|кликн|взаимодейств|доступ|компьютер|браузер|"
                r"直接|操作|控制|访问|计算机|浏览器|电脑)")
# Общие, не завязанные на модель или формулировку задачи фразы: первое лицо +
# тема управления устройством в одном предложении (окно ~80 знаков, любой
# порядок). Ложное срабатывание здесь стоит ОДНОГО дополнительного прогона
# с фидбеком (см. ниже) — несимметрично дешевле, чем молчаливый false-success.
_REFUSAL_RE = re.compile(
    rf"{_SELF_DENIAL}[^.!?\n]{{0,80}}{_DEVICE_TOPIC}|{_DEVICE_TOPIC}[^.!?\n]{{0,80}}{_SELF_DENIAL}",
    re.I)

# Инструменты, способные произвести проверяемое ВНЕШНЕЕ действие (не просто
# прочитать состояние). Список закрытый и совпадает с category="write"/"send"
# у зарегистрированных ToolSpec browser.*; используется только чтобы решить,
# стоит ли дать модели ОДНУ повторную попытку (инструмент реально доступен —
# возможно, модель просто не воспользовалась им), или отказ окончателен сразу
# (инструментов для действия нет вовсе — повтор гарантированно даст тот же
# текст и просто сожжёт токены владельца).
_ACTION_TOOLS = frozenset({
    "browser.open", "browser.click", "browser.type", "browser.select",
    "browser.submit", "browser.back", "browser.reload", "browser.login",
    "terminal.run",
})

META_KEY = "action_gate_attempts"
RULE = "no_verified_action"


def looks_like_action_refusal(answer: str) -> bool:
    """Модель сама сказала, что не может выполнить действие, и переадресовала
    владельца делать это вручную. Проверяется ОТВЕТ модели, а не задача."""
    return bool(_REFUSAL_RE.search(str(answer or "")))


async def _has_any_tool_call(svc, run_id) -> bool:
    """Хотя бы один вызов инструмента в этом прогоне — любого исхода.

    Исполнен, отклонён политикой или ждал подтверждения — не важно: любой из
    трёх означает, что модель ДЕЙСТВИТЕЛЬНО пыталась пройти инструментальный
    путь, а не просто описала его текстом. Здесь гейту сказать нечего."""
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(tool_calls_t.c.id)
                               .where(tool_calls_t.c.run_id == run_id).limit(1))).first()
    return row is not None


async def _agent_has_action_tools(svc, task: dict) -> bool:
    async with svc.db.session() as s:
        agent_id = task.get("agent_id")
        if agent_id is None:
            return False
        row = (await s.execute(sa.select(agents_t).where(agents_t.c.id == agent_id))).first()
    if row is None:
        return False
    agent = dict(row._mapping)
    names = set(allowed_tools_for(task, agent))
    return bool(names & _ACTION_TOOLS)


async def _attempts(svc, task_id: int) -> tuple[int, dict]:
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(tasks_t.c.meta).where(tasks_t.c.id == task_id))).first()
    meta = dict(row._mapping["meta"]) if row and isinstance(row._mapping["meta"], dict) else {}
    return int(meta.get(META_KEY, 0)), meta


async def _bump_attempts(svc, task_id: int, meta: dict) -> None:
    meta = dict(meta)
    meta[META_KEY] = int(meta.get(META_KEY, 0)) + 1
    async with svc.db.session() as s:
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(
            meta=meta, updated_at=utcnow()))
        await s.commit()


def _verdict(verdict: str, *, rule: str = "", feedback: str = "", requeue: bool = True,
            status: str = "") -> dict:
    out: dict = {"verdict": verdict}
    if rule:
        out["reasons"] = f"action_gate/{rule}"
    if verdict == "FAIL":
        # requeue обязан присутствовать в словаре ВСЕГДА при FAIL: движок
        # читает `res.get("requeue", True)` — отсутствующий ключ читается как
        # «повторить», и terminal-отказ (requeue=False) без явного ключа
        # превратился бы в бесконечный повтор одного и того же текста.
        out["requeue"] = requeue
        if feedback:
            out["feedback"] = feedback
    if status:
        out["status"] = status
    return out


async def _gate(svc):
    async def gate_completion(task, run_id, answer):
        if not looks_like_action_refusal(answer):
            return _verdict("NOT_APPLICABLE")
        if await _has_any_tool_call(svc, run_id):
            # Модель пыталась пройти инструментальный путь (исполнен, отклонён
            # или ждал подтверждения) — отказ в тексте относится к чему-то
            # другому, не к задаче в целом; здесь это не наш случай.
            return _verdict("NOT_APPLICABLE")

        has_tools = await _agent_has_action_tools(svc, task)
        attempts, meta = await _attempts(svc, task["id"])

        if has_tools and attempts == 0:
            # Инструмент действия был доступен, но не вызван ни разу — даём
            # ОДНУ попытку самокоррекции с прямой инструкцией, как review_gate.
            await _bump_attempts(svc, task["id"], meta)
            return _verdict(
                "FAIL", rule=RULE, requeue=True,
                feedback=(
                    "Ответ описывает, что сделать ВРУЧНУЮ, вместо того чтобы выполнить "
                    "действие инструментом. У тебя есть инструмент для этого — вызови "
                    "его штатным механизмом (например browser.open), а не пересказывай "
                    "шаги владельцу. Текстовая инструкция не завершает задачу."))

        # Инструментов для действия нет вовсе, ИЛИ уже была одна попытка и
        # отказ повторился: дальше требовать бессмысленно — задача не может
        # быть выполнена в её нынешнем виде. Честный терминал, а не completed.
        await svc.bus.emit("action_gate.blocked", task_id=task["id"], run_id=run_id,
                           has_action_tools=has_tools)
        return _verdict("FAIL", rule=RULE, requeue=False, status="failed")

    return gate_completion


async def _setup(svc):
    svc.engine.add_hook("gate_completion", await _gate(svc))


FEATURE = Feature(name="action_gate", setup=_setup)
