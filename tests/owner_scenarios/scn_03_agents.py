"""Владельческие сценарии 9–11: браузер, кодовый агент, цепочка из агентов.

Все три требуют способностей, которых в постном CI нет: настоящего браузера и
ключа ИИ владельца. Код здесь написан для НАСТОЯЩЕГО прогона — заглушек,
превращающих отсутствие способности в PASS, нет ни одной.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scenario_runner import INSTALLED_PRODUCT, PRODUCT_CONTRACTS, scenario  # noqa: E402

PAGE = """<!doctype html><meta charset="utf-8"><title>Проверка</title>
<button id="go" onclick="document.getElementById('out').textContent='ГОТОВО'">Нажми</button>
<div id="out">ПУСТО</div>"""

PATCH_TOOL = {
    "type": "function",
    "function": {
        "name": "apply_patch",
        "description": "Заменить содержимое файла проекта целиком.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"},
                           "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
}


@scenario(id="OS-09", depth=INSTALLED_PRODUCT)
def os09_browser_chain_completes_and_verifies(ctx) -> None:
    """Браузерная цепочка доходит до конца, и исход проверяется пост-состоянием DOM."""
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    page_path = ctx.path("web", "страница.html")
    page_path.write_text(PAGE, encoding="utf-8")
    ctx.reached_installed_product("движок браузера playwright установленного продукта")
    with sync_playwright() as p:
        # Путь берётся у пробы среды: в образе может лежать рабочая сборка
        # другой версии, чем просит движок, и тогда launch() без пути падает.
        # None здесь — «штатный путь годен», launch() находит браузер сам.
        from capabilities import browser_executable  # noqa: PLC0415
        browser = p.chromium.launch(executable_path=browser_executable())
        try:
            page = browser.new_page()
            page.goto(page_path.as_uri())
            before = page.text_content("#out")
            ctx.negative("до действия цель ещё не достигнута",
                         before.strip() == "ПУСТО", f"было={before!r}")
            page.click("#go")
            after = page.text_content("#out")
            ctx.positive("после действия пост-состояние страницы подтверждает исход",
                         after.strip() == "ГОТОВО", f"стало={after!r}")
            missing = page.query_selector("#нет-такого")
            ctx.negative("несуществующий элемент не выдаётся за успех", missing is None)
        finally:
            browser.close()


@scenario(id="OS-10", depth=PRODUCT_CONTRACTS)
def os10_code_agent_fixes_a_small_project(ctx) -> None:
    """Модель правит маленький проект, правка применяется, тесты проекта гоняются по-настоящему."""
    project = ctx.path("проект", "pkg")
    project.mkdir(parents=True, exist_ok=True)
    (project / "калькулятор.py").write_text("def сложить(a, b):\n    return a - b\n",
                                            encoding="utf-8")
    (project / "test_калькулятор.py").write_text(
        "from калькулятор import сложить\n\n\ndef test_сложить():\n    assert сложить(2, 2) == 4\n",
        encoding="utf-8")

    before = _pytest(project)
    ctx.negative("до правки тесты проекта по-настоящему падают",
                 before.returncode != 0, f"код={before.returncode}")

    answer = ctx.require_model(ctx.ai.chat(
        [{"role": "system", "content": "Ты чинишь код. Вызови apply_patch с исправленным файлом."},
         {"role": "user", "content": "Файл калькулятор.py содержит:\n"
                                     "def сложить(a, b):\n    return a - b\n\n"
                                     "Тест требует сложить(2, 2) == 4. Вызови apply_patch "
                                     "с path='калькулятор.py' и исправленным содержимым."}],
        tools=[PATCH_TOOL], max_tokens=300))

    calls = answer.tool_calls
    ctx.positive("модель выбрала инструмент, а не ответила прозой",
                 bool(calls) and calls[0]["function"]["name"] == "apply_patch",
                 f"вызовов инструмента={len(calls)}")
    args = json.loads(calls[0]["function"]["arguments"])
    ctx.positive("аргументы инструмента разбираются и называют нужный файл",
                 args.get("path", "").endswith("калькулятор.py") and "content" in args,
                 f"path={args.get('path')}")

    (project / "калькулятор.py").write_text(args["content"], encoding="utf-8")
    after = _pytest(project)
    ctx.positive("после правки модели тесты проекта проходят по-настоящему",
                 after.returncode == 0, after.stdout[-200:] or after.stderr[-200:])


def _pytest(project: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           str(project)], cwd=project, capture_output=True, text=True,
                          timeout=120, env={"PYTHONPATH": str(project), "PATH": "/usr/bin:/bin"})


@scenario(id="OS-11", depth=PRODUCT_CONTRACTS)
def os11_planner_executor_verifier(ctx) -> None:
    """Планировщик → исполнитель → проверяющий: три роли, и проверяющий независим."""
    target = ctx.path("работа", "результат.txt")
    plan = ctx.require_model(ctx.ai.chat(
        [{"role": "system", "content": "Ты планировщик. Ответь ТОЛЬКО JSON."},
         {"role": "user", "content": "Задача: записать в файл слово ГОТОВО. Верни JSON вида "
                                     '{"шаги": ["..."], "проверка": "..."} и ничего больше.'}],
        max_tokens=200))
    parsed = _json_or_none(plan.text)
    ctx.positive("планировщик вернул структурированный план",
                 isinstance(parsed, dict) and "шаги" in parsed,
                 f"ключи={list(parsed) if isinstance(parsed, dict) else plan.text[:60]}")

    execution = ctx.require_model(ctx.ai.chat(
        [{"role": "system", "content": "Ты исполнитель. Вызови инструмент, не отвечай прозой."},
         {"role": "user", "content": f"План: {json.dumps(parsed, ensure_ascii=False)}. "
                                     f"Вызови apply_patch с path='{target.name}' "
                                     "и content='ГОТОВО'."}],
        tools=[PATCH_TOOL], max_tokens=200))
    calls = execution.tool_calls
    ctx.positive("исполнитель выбрал инструмент и передал аргументы",
                 bool(calls) and "content" in json.loads(calls[0]["function"]["arguments"]))
    target.write_text(json.loads(calls[0]["function"]["arguments"])["content"], encoding="utf-8")

    # Проверяющий — отдельный вызов, и его вердикт сверяется с настоящим файлом.
    verdict = ctx.require_model(ctx.ai.chat(
        [{"role": "system", "content": "Ты проверяющий. Ответь одним словом: ДА или НЕТ."},
         {"role": "user", "content": f"В файле написано: {target.read_text(encoding='utf-8')!r}. "
                                     "Содержит ли он слово ГОТОВО?"}],
        max_tokens=8))
    real = "ГОТОВО" in target.read_text(encoding="utf-8")
    ctx.positive("вердикт проверяющего совпал с настоящим состоянием файла",
                 real and "да" in verdict.text.lower(),
                 f"файл={real} вердикт={verdict.text[:20]}")
    ctx.negative("вердикт проверяющего не заменяет наблюдение файла",
                 real is True, "зелёным считается наблюдение, а не слово модели")


def _json_or_none(text: str):
    body = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(body)
    except (TypeError, ValueError):
        return None
