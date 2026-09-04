"""Хук `gate_completion`: две беды маленькой модели, по одной попытке на каждую.

Зачем хук вообще нужен именно здесь. Локальная модель на 7-14B ошибается двумя
устойчивыми способами, и оба заканчиваются молча:

  * она печатает вызов инструмента ТЕКСТОМ вместо штатного `tool_calls`.
    `OpenAICompatAdapter` читает только `choices[0].message.tool_calls`, а текст
    с `<tool_call>` движок принимает за финальный ответ — прогон «успешно»
    завершается вызовом вместо результата, без ошибки и без повтора. Владелец
    получает ответ, в котором вместо фактов лежит просьба их поискать;
  * она читает страницы и отвечает БЕЗ единой ссылки. Такой ответ владелец не
    может отличить от припомненного, то есть весь смысл веб-поиска пропадает.

Обе беды видны только на финальном ответе — раньше их поймать нечем. Поэтому
хук, а не проверка внутри инструмента.

Чего этот файл НЕ делает и делать не будет:

  * НЕ исполняет вызов, разобранный из текста. Такой вызов миновал бы
    `decide_effect`, парковку на одобрение, `approval_digest` и anti-replay —
    модель, печатающая вызов текстом, получила бы БОЛЬШЕ прав, чем вызывающая
    штатно. Здесь он только опознаётся, и модели говорят: «повтори штатно»;
  * НЕ даёт больше одной корректирующей попытки на правило. Счётчик живёт в
    реестре прогона (он переживает парковку и перезапуск), а не в памяти
    процесса. Без потолка слабая модель крутится до `max_steps` на процессоре
    владельца, и лечение оказывается тяжелее болезни;
  * НЕ трогает прогоны, в которых веб-инструментов не было: там вердикт
    `NOT_APPLICABLE`, и чужой прогон хук не касается вовсе.

Отдельно про fail-open, единственный в этой фиче. `gate_completion` входит в
`CRITICAL_HOOK_NAMES`: исключение внутри хука — это `CriticalHookFailure`, то
есть эскалация человеку по ЧУЖОМУ прогону и незавершённая задача. Цена ошибки
здесь несимметрична: пропущенная ссылка стоит владельцу неудобства, а упавший
хук — сорванной работы. Поэтому ВЕСЬ хук обёрнут в `try/except` с возвратом
`PASS`. На всём пути наружу (egress) правило обратное — fail-closed.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config, ledger as ledger_mod, render

# Формы, которыми модель печатает вызов вместо штатного механизма. Список
# закрытый и намеренно узкий: ловим то, что реально выдают раннеры локальных
# моделей (llama.cpp, Ollama, LM Studio) и их шаблоны, а не всё, где встретилось
# слово web. Ложное срабатывание тут дороже пропуска: оно заставит модель
# переписывать правильный ответ.
_TEXT_CALL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"<\s*tool_call\s*>", re.I),
    re.compile(r"<\|\s*python_tag\s*\|>", re.I),
    re.compile(r"\[TOOL_CALLS?\]", re.I),
    re.compile(r'"name"\s*:\s*"web[._][a-z_]+"', re.I),
    re.compile(r"^\s*Action\s*:\s*web[._][a-z_]+", re.I | re.M),
    re.compile(r"^\s*(?:Tool|Функция|Инструмент)\s*:\s*web[._][a-z_]+", re.I | re.M),
    re.compile(r"```[a-z]*\s*\{\s*[^}]{0,200}web[._](?:search|open|find|cite)", re.I),
)

# Маркер ссылки в ответе: [w1] или [l3@host/path]. Достаточно ЛЮБОГО — гейт
# проверяет наличие ссылок, а не их правильность: за правильность отвечает
# web.cite, который сверяет цитату с текстом дословно.
_MARKER = re.compile(r"\[[wl]\d{1,3}(?:@[^\]]{0,200})?\]")

RULE_TEXT_CALL = "text_call"
RULE_UNCITED = "uncited"


def looks_like_text_call(answer: str) -> bool:
    """Ответ содержит напечатанный вызов web-инструмента.

    Проверяется именно web.*: чужие инструменты не наша забота, и лезть в них
    значило бы чинить не свою беду на чужом прогоне.
    """
    text = str(answer or "")
    if not text:
        return False
    if "web" not in text.lower():
        # Дешёвая отсечка: ни один из шаблонов без слова web не сработает,
        # а регулярки по длинному ответу стоят заметно дороже.
        return False
    return any(p.search(text) for p in _TEXT_CALL_PATTERNS)


def has_citation_marker(answer: str) -> bool:
    return bool(_MARKER.search(str(answer or "")))


def _verdict(verdict: str, *, rule: str = "", feedback: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {"verdict": verdict}
    if rule:
        out["reasons"] = f"web_research/{rule}"
    if feedback:
        out["feedback"] = feedback
        out["requeue"] = True
    return out


def make_gate(svc: Any):
    """Собрать хук для конкретного сервиса.

    Возвращается функция ровно той формы, которую зовёт движок:
    `await fn(task, run_id, answer)`. Ничего, кроме реестра прогона, хук не
    читает — ни базы, ни сети, ни диска сверх одного json.
    """

    async def gate(task: Any, run_id: Any, answer: Any) -> dict[str, Any]:
        try:
            return await _decide(svc, run_id, answer)
        except Exception:  # noqa: BLE001 — см. docstring модуля: fail-open осознанный
            # Молчать нельзя, но и ронять чужой прогон нельзя тем более:
            # событие уходит, вердикт остаётся проходным.
            bus = getattr(svc, "bus", None)
            if bus is not None:
                try:
                    await bus.emit("web.gate_error", run_id=str(run_id))
                except Exception:  # noqa: BLE001
                    pass
            return _verdict("PASS")

    return gate


async def _decide(svc: Any, run_id: Any, answer: Any) -> dict[str, Any]:
    if not config.enabled():
        return _verdict("NOT_APPLICABLE")

    book = ledger_mod.Ledger.load(svc, run_id)
    opened = book.opened_refs()
    text = str(answer or "")

    # Прогон, в котором веб-инструментов не было вовсе, — не наш. Реестр без
    # единой ссылки означает именно это: mint() зовут только инструменты.
    if not book.refs() and not opened:
        return _verdict("NOT_APPLICABLE")

    if looks_like_text_call(text):
        if book.gate_attempts(RULE_TEXT_CALL) == 0:
            book.bump_gate(RULE_TEXT_CALL)
            book.save()
            return _verdict("FAIL", rule=RULE_TEXT_CALL,
                            feedback=render.render_gate_feedback(RULE_TEXT_CALL))
        # Попытка уже была: настаивать бессмысленно, а крутить прогон до
        # max_steps — вредно. Пропускаем, но говорим об этом вслух.
        await _emit(svc, "web.text_call_detected", run_id=str(run_id))
        return _verdict("PASS", rule=RULE_TEXT_CALL)

    if opened and not has_citation_marker(text):
        if book.gate_attempts(RULE_UNCITED) == 0:
            book.bump_gate(RULE_UNCITED)
            book.save()
            return _verdict("FAIL", rule=RULE_UNCITED,
                            feedback=render.render_gate_feedback(RULE_UNCITED, opened))
        await _emit(svc, "web.uncited_answer", run_id=str(run_id),
                    pages=len(opened))
        return _verdict("PASS", rule=RULE_UNCITED)

    return _verdict("PASS")


async def _emit(svc: Any, kind: str, **data: Any) -> None:
    bus = getattr(svc, "bus", None)
    if bus is None:
        return
    try:
        await bus.emit(kind, **data)
    except Exception:  # noqa: BLE001 — телеметрия не имеет права мешать вердикту
        pass


# --------------------------------------------------------------- преполёт

# Файл преполёта лежит рядом с реестрами прогонов: это тоже состояние фичи, а
# не общее состояние приложения. Своей строки в `model_capability_checks` мы не
# пишем СПЕЦИАЛЬНО — та таблица принадлежит фиче openrouter, и заводить в чужой
# таблице свои записи значит однажды разойтись с её владельцем.
PREFLIGHT_FILE = "preflight.json"

# Проба намеренно тривиальна: если раннер не смог вернуть tool_calls на вызов
# из одного целого числа, он не сможет и на web.search с четырьмя полями.
_PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "bossman_probe",
        "description": "Вернуть целое число.",
        "parameters": {"type": "object",
                       "properties": {"value": {"type": "integer"}},
                       "required": ["value"]},
    },
}
_PROBE_PROMPT = "Вызови bossman_probe со значением 7."


def _preflight_path(svc: Any) -> Path:
    return config.runs_dir(svc) / PREFLIGHT_FILE


def last_preflight(svc: Any) -> dict[str, Any]:
    """Прошлый вердикт преполёта или честное «не проверялось».

    «Не проверялось» и «проверено, не умеет» — РАЗНЫЕ ответы, и путать их
    нельзя: первый означает, что владелец ещё не нажимал кнопку, второй — что
    его раннер и правда не отдаёт нативные вызовы. Отсутствие файла это первое.
    """
    path = _preflight_path(svc)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"checked": False,
                "text": "раннер не проверялся: нажмите «проверить», и я скажу, "
                        "отдаёт ли ваша модель нативные вызовы инструментов"}
    if not isinstance(raw, dict):
        return {"checked": False, "text": "прошлая проверка не читается — проверьте заново"}
    raw.setdefault("checked", True)
    return raw


def _save_preflight(svc: Any, payload: dict[str, Any]) -> None:
    """Запись атомарная: половина файла читалась бы как «не проверялось»."""
    path = _preflight_path(svc)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".preflight-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except OSError:
        # Не сохранили — не беда: вердикт всё равно вернётся вызывающему.
        pass


async def preflight(svc: Any, model_id: Any = None) -> dict[str, Any]:
    """Отдаёт ли раннер владельца нативные `tool_calls`.

    Проба идёт ТЕМ ЖЕ путём, каким пойдёт настоящий вызов: адаптер из реестра,
    а не отдельный HTTP-клиент. Второй клиент проверял бы не то: вопрос ведь
    именно в том, доедет ли вызов через боевую цепочку раннер → адаптер →
    разбор ответа.

    Ответ «нет» — не приговор: хук `gate_completion` ловит напечатанный текстом
    вызов и просит повторить штатно. Но владелец должен знать это заранее, а не
    выяснять по пустым ответам.
    """
    registry = getattr(svc, "registry", None)
    if registry is None:
        return {"checked": False, "ok": False, "code": "no_registry",
                "text": "в сервисах нет реестра моделей — проверять нечем"}

    target = model_id
    if target is None:
        try:
            models = await registry.list_models()
        except Exception as exc:  # noqa: BLE001
            return {"checked": False, "ok": False, "code": "no_models",
                    "text": f"список моделей не читается: {type(exc).__name__}"}
        enabled = [m for m in models if m.get("enabled", True)]
        if not enabled:
            return {"checked": False, "ok": False, "code": "no_models",
                    "text": "ни одной включённой модели: сначала добавьте модель"}
        target = enabled[0]["id"]

    started = datetime.now(timezone.utc)
    try:
        adapter, model = await registry.adapter_for(int(target))
        result = await adapter.chat(model["name"], [{"role": "user", "content": _PROBE_PROMPT}],
                                    tools=[_PROBE_TOOL], tool_choice="auto",
                                    max_tokens=64, temperature=0)
        calls = list(getattr(result, "tool_calls", ()) or ())
        ok = bool(calls) and str(calls[0].name) == "bossman_probe"
        payload = {
            "checked": True, "ok": ok, "code": "native" if ok else "no_native_tool_calls",
            "model_id": int(target), "model": str(model.get("alias") or model.get("name") or ""),
            "at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "text": ("ваш раннер отдаёт нативные вызовы инструментов: да"
                     if ok else
                     "ваш раннер НЕ отдаёт нативные вызовы инструментов. Модель будет "
                     "печатать вызов текстом; я это замечу и попрошу повторить штатно, "
                     "но исполнять напечатанное текстом нельзя — такой вызов миновал бы "
                     "и права, и одобрение владельца"),
        }
    except Exception as exc:  # noqa: BLE001 — раннер бывает любым, включая мёртвый
        payload = {"checked": True, "ok": False, "code": "probe_failed",
                   "model_id": int(target) if str(target).isdigit() else None,
                   "at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "text": f"проба не удалась: {type(exc).__name__}. Это НЕ значит "
                           f"«не умеет» — это значит «спросить не вышло»"}
    _save_preflight(svc, payload)
    return payload
