#!/usr/bin/env python3
"""Owner breaker sequence — the checks hosted CI is structurally unable to make.

Why a second harness next to `evening_acceptance.py`: that one walks twelve
product scenarios. This one answers a narrower and more dangerous question —
*which claims about this build rest on the owner's machine rather than on a
runner*, and it refuses to let the two kinds of evidence be added together.

Every item carries both halves explicitly:

    hosted      what a hosted runner already proved, named by its suite/job
    owner_live  what only the owner's machine can prove, and why

A hosted PASS is never promoted. `B2` runs a real effect on a runner and that
proves the effect path; it does not prove the owner's GPU, the owner's
OpenHands provider, or the owner's AI File Sorter binary. Those are separate
verdicts here, and the report prints them in separate columns.

    python scripts/owner_breaker.py plan              что предстоит и чем уже закрыто
    python scripts/owner_breaker.py run               пройти последовательность
    python scripts/owner_breaker.py run --only B7 B9  только эти пункты
    python scripts/owner_breaker.py report            собрать отчёт
    python scripts/owner_breaker.py verify            самопроверка (CI)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1

#: Вердикты. HOSTED_PASS и OWNER_LIVE_PASS РАЗНЫЕ и не складываются: первый
#: говорит «путь работает на раннере», второй — «работает на машине владельца».
#: Смешать их значит выдать чужое железо за проверенное.
HOSTED_PASS = "HOSTED_PASS"
OWNER_LIVE_PASS = "OWNER_LIVE_PASS"
FAIL = "FAIL"
BLOCKED = "BLOCKED"
NOT_RUN = "NOT_RUN"
VERDICTS = (HOSTED_PASS, OWNER_LIVE_PASS, FAIL, BLOCKED)


#: Что записывается на КАЖДОМ шаге (§5). Порядок — порядок вопросов.
FIELDS: tuple[tuple[str, str], ...] = (
    ("task_id", "id задачи"),
    ("run_id", "id прогона"),
    ("model", "модель"),
    ("status", "финальный статус"),
    ("result", "результат (коротко)"),
    ("tool_calls", "вызовы инструментов"),
    ("effect_evidence", "улика ПОСТ-СОСТОЯНИЯ (файл/URL/diff/ffprobe)"),
    ("approvals", "подтверждения"),
    ("retries", "повторы"),
    ("http_errors", "ошибки HTTP"),
    ("dead_clicks", "клики без реакции"),
    ("console_errors", "ошибки консоли"),
    ("tokens_cost", "токены / стоимость"),
    ("resource_telemetry", "телеметрия ресурсов"),
)


def _build_sha() -> str:
    """SHA кода, на котором идёт приёмка. Улика без него не привязывается к
    коммиту, а значит не является уликой."""
    try:
        import subprocess
        out = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        dirty = subprocess.run(["git", "-C", str(REPO), "diff", "--quiet", "HEAD", "--"],
                               capture_output=True, timeout=5)
        sha = out.stdout.strip()
        if out.returncode == 0 and len(sha) == 40:
            return sha if dirty.returncode == 0 else f"{sha}-DIRTY"
    except Exception:
        pass
    return "SOURCE_IDENTITY_UNKNOWN"


BUILD_SHA = _build_sha()


@dataclass(frozen=True)
class Check:
    ident: str
    title: str
    steps: tuple[str, ...]
    proof: str
    #: Чем закрыто на раннере. Пусто — раннер этого не доказывает вообще.
    hosted: str = ""
    #: Что остаётся за машиной владельца. Пусто — хостинга достаточно.
    owner_live: str = ""
    blocks_release: bool = True


#: Двенадцать шагов владельческого прогона, в порядке из задания. Порядок не
#: случайный: сначала простой ответ, потом структурированный, потом эффекты, и
#: только в конце отказ провайдера и перезапуск. Ломать продукт раньше, чем
#: убедился, что он вообще отвечает, — потерять вечер на ложный след.
#:
#: Для КАЖДОГО шага записывается пост-состояние, а не рассказ модели:
#:     HTTP 200        != SUCCESS
#:     текст модели    != эффект
#:     клик по кнопке  != действие
#:     completed       != цель достигнута
BREAKER: tuple[Check, ...] = (
    Check(
        "B1", "Обычный ответ GLM",
        ("Задайте вопрос, на который отвечают текстом.",
         "Дождитесь терминального состояния задачи.",
         "Сверьте: модель в журнале — та, что выбрана."),
        "id задачи + id прогона + имя модели + текст ответа",
        hosted="command-center tests/test_p0_completion_truth.py",
        owner_live="живой провайдер и ключ владельца"),
    Check(
        "B2", "Структурированный JSON",
        ("Попросите ответ строго по схеме (объект с заданными полями).",
         "Проверьте, что ответ РАЗБИРАЕТСЯ как JSON и содержит поля схемы.",
         "Невалидный JSON — это провал шага, а не «почти получилось»."),
        "сам JSON + результат разбора",
        hosted="bossman-core tests/test_gateway_anthropic_contract.py",
        owner_live="структурированный вывод живой модели"),
    Check(
        "B3", "Навигация браузера",
        ("Дайте задачу открыть конкретную страницу.",
         "Дождитесь конечного состояния.",
         "Проверьте АДРЕС и содержимое итоговой страницы, а не рассказ о ней.",
         "Челлендж/логин обязан ПАУЗИТЬ задачу, а не завершать её."),
        "итоговый URL + скриншот + трасса браузера",
        hosted="command-center tests/test_secrem_browser_policy.py",
        owner_live="живой сайт и живая проверка «вы человек»"),
    Check(
        "B4", "Создание текстового файла",
        ("Поручите создать документ с конкретным содержимым.",
         "Найдите файл на диске по названному пути.",
         "Откройте его сторонним приложением и сверьте содержимое.",
         "Нет файла — задача не выполнена, каким бы уверенным ни был ответ."),
        "путь + содержимое файла + финальный статус задачи",
        hosted="command-center tests/test_p0_completion_truth.py"),
    Check(
        "B5", "Приложения: старт и стоп",
        ("Включите политику, запустите приложение, дождитесь health.",
         "Остановите его и убедитесь в системе, что процесса больше нет.",
         "Проверьте, что «running» не висит после остановки."),
        "вывод диспетчера задач до и после + статусы карточки",
        hosted="command-center tests/test_apps_*.py, tests/test_stop_grace_lifecycle.py"),
    Check(
        "B6", "Coding → OpenHands, одноразовая задача",
        ("Дайте задачу по коду в репозитории внутри разрешённых корней.",
         "Дождитесь diff и улик.",
         "Проверьте, что тесты ДЕЙСТВИТЕЛЬНО запускались, а не описаны словами.",
         "Проверьте, что исходный репозиторий не изменён, а песочница удалена."),
        "diff + вывод тестов + улика пост-состояния",
        hosted="bossman-core tests/apprentice/test_openhands_*.py (детерминированный сайдкар)",
        owner_live="настоящий провайдер OpenHands и настоящий ключ"),
    Check(
        "B7", "Web Designer: правка, Apply дважды, сохранение и перезагрузка",
        ("Откройте проект, выберите элемент, измените текст, нажмите Apply.",
         "Нажмите Apply ВТОРОЙ раз с другим изменением.",
         "Сохраните, перезагрузите страницу, откройте проект заново.",
         "Оба изменения обязаны быть на месте после перезагрузки."),
        "три скриншота (до, после первого, после второго) + превью после перезагрузки",
        hosted="command-center tests/test_web_designer_*.py (117 проверок в реальном Chromium)"),
    Check(
        "B8", "Video Studio: создать, импортировать, править, превью, экспорт",
        ("Создайте проект и импортируйте настоящий клип.",
         "Отредактируйте таймлайн и воспроизведите превью.",
         "Экспортируйте и ДЕКОДИРУЙТЕ результат (ffprobe): длительность, потоки, контейнер.",
         "Откройте проект заново и убедитесь, что состояние сохранилось."),
        "путь к экспорту + полный вывод ffprobe + скриншот открытого заново проекта",
        hosted="command-center tests/test_video_studio_*.py на настоящем ffmpeg"),
    Check(
        "B9", "Trading Lab: задача только на анализ",
        ("Поручите анализ без единого ордера.",
         "Проверьте, что НИ ОДНОГО торгового действия не выполнено.",
         "Сверьте числовые утверждения с источником данных."),
        "журнал задачи без торговых вызовов + разбор чисел",
        hosted="root tests/test_trader_apprentice.py"),
    Check(
        "B10", "Отказ провайдера → fallback",
        ("Сломайте основного провайдера (неверный ключ или недоступный адрес).",
         "Запустите задачу.",
         "Проверьте, что переход на запасного НАЗВАН в журнале.",
         "Молчаливая подмена модели — это провал шага."),
        "журнал с обоими провайдерами и причиной перехода",
        hosted="bossman-core tests/test_gateway_failover_4xx.py, test_gateway_circuit_breaker.py",
        owner_live="настоящий отказ живого провайдера"),
    Check(
        "B11", "Одно настоящее подтверждение",
        ("Дайте задачу, требующую разрешения владельца.",
         "Дождитесь запроса и ОДОБРЬТЕ его.",
         "Проверьте, что эффект произошёл ровно один раз.",
         "Повторите с ОТКАЗОМ: задача обязана стать failed/blocked, не completed."),
        "id одобрения + пост-состояние эффекта + финальный статус обеих задач",
        hosted="command-center tests/test_p0_completion_truth.py, tests/test_provenance.py"),
    Check(
        "B12", "Перезапуск и переподключение",
        ("Остановите Bossman во время безопасной задачи.",
         "Запустите заново и войдите.",
         "Проверьте, что SHA сборки в оболочке ТОТ ЖЕ.",
         "Проверьте, что закрытые шаги не переигрались и дубля эффекта нет."),
        "SHA до и после + журнал задачи до и после + доказательство отсутствия дубля",
        hosted="command-center tests/test_stop_grace_lifecycle.py, tests/test_runtime_source_identity.py"),
)

#: Живые расширения. Ни одно из них хостинг не доказывает — здесь `hosted`
#: пусто намеренно, и отчёт обязан показывать это как пробел, а не как норму.
LIVE: tuple[Check, ...] = (
    Check(
        "L1", "Настоящий Windows",
        ("Соберите и установите на настоящей Windows-машине.",
         "Пути с пробелами и с не-ASCII символами.",
         "Старт, работа, остановка, удаление.",
         "Проверьте, что в каталог установленного пакета ничего не пишется."),
        "лог установки + скриншот работающего продукта",
        hosted="hosted Windows runner: сборка, установка, пути, старт, уборка "
               "(command-center-ci «windows paths», local-bundle)",
        owner_live="браузер и UI на настоящей Windows — OWNER_WINDOWS_REQUIRED"),
    Check(
        "L2", "Локальная модель на железе владельца",
        ("Ryzen AI Max+ 395 / Radeon 8060S / 128 ГБ.",
         "Обнаружение, загрузка, выгрузка, отмена.",
         "Пределы контекста, вызов инструментов, структурированный вывод.",
         "Давление по памяти: graceful OOM/отказ, а не падение.",
         "Здоровье бэкенда и fallback."),
        "имена моделей + время загрузки + поведение под давлением",
        owner_live="OWNER_LOCAL_MODEL_REQUIRED — хостинг это железо не имеет"),
    Check(
        "L3", "Настоящий OpenHands",
        ("Реальный провайдер, реальный ключ, реальный репозиторий.",
         "Прогон задачи целиком.",
         "Улика пост-состояния из неизменяемого снимка хоста."),
        "id прогона + улика",
        owner_live="настоящий провайдер и ключ владельца"),
    Check(
        "L4", "OpenRouter / GLM",
        ("Прогон на реальных учётных данных.",
         "Проверьте потоковую выдачу, вызовы инструментов и завершение.",
         "Проверьте, что обрыв потока НЕ становится успешным завершением."),
        "id прогонов + журнал",
        owner_live="ключи владельца"),
    Check(
        "L5", "File Intelligence с настоящим бинарём AI File Sorter",
        ("Установите AI File Sorter ВНЕ дерева Bossman.",
         "Включите фичу явно (BCC_FILE_INTELLIGENCE=1) и задайте AIFS_ROOTS.",
         "Откройте страницу File Intelligence: доктор обязан назвать бинарь, "
         "его sha256 и статус VERSION_UNVERIFIED.",
         "Прогоните анализ на тестовой папке (review-only).",
         "Подмените бинарь между обзором и применением — применение обязано "
         "ОТКАЗАТЬ с названной причиной.",
         "Верните бинарь, примените, проверьте пост-состояние на диске.",
         "Проверьте, что .git и каталоги Bossman не тронуты."),
        "скриншот доктора + план обзора + отказ на подмене + пост-состояние",
        hosted="command-center tests/test_file_intelligence_*.py на "
               "детерминированном фальшивом сайдкаре",
        owner_live="настоящий бинарь hyperfield/ai-file-sorter"),
    Check(
        "L6", "File Intelligence с локальной моделью",
        ("Настройте сайдкар на локальный LLMChoice (Local_*).",
         "Убедитесь, что backend_mode = LOCAL, а не UNKNOWN.",
         "Прогоните анализ и проверьте, что наружу ничего не ушло."),
        "backend_mode + сетевой журнал",
        owner_live="настоящий бинарь и настоящая локальная модель"),
    Check(
        "L7", "N4 / N5 / N6 / N8",
        ("Пройдите каждый пункт по его собственному описанию.",
         "Для каждого назовите: доказано репозиторием или требует живой "
         "инфраструктуры.",
         "Не засчитывайте юнит-тест как приёмку."),
        "по одному вердикту на пункт",
        owner_live="живая инфраструктура там, где она требуется"),
    Check(
        "L8", "Canary / откат",
        ("Выкатите заведомо плохой релиз в тестовом окружении.",
         "Убедитесь, что health-провал обнаружен.",
         "Убедитесь, что откат сработал и вернул предыдущее состояние.",
         "Проверьте, что эффекты не переигрались."),
        "журнал выката и отката",
        owner_live="OWNER_CANARY_ROLLBACK_REQUIRED — нужна настоящая "
                   "инфраструктура развёртывания"),
    Check(
        "L9", "Сохранение интеллекта",
        ("Прогоните базовую и текущую линии на ОДНОЙ И ТОЙ ЖЕ модели.",
         "Сравните только сопоставимые прогоны.",
         "Если одинаковой модели нет — вердикт INSUFFICIENT_EVIDENCE, и это "
         "законный ответ, а не провал."),
        "две линии с именем модели или явный INSUFFICIENT_EVIDENCE",
        owner_live="доступ к той же модели на обеих линиях"),
    Check(
        "L10", "Soak",
        ("Оставьте продукт работать под нагрузкой на несколько часов.",
         "Следите за памятью, дескрипторами и зависшими задачами.",
         "Убедитесь, что после soak продукт по-прежнему стартует и отвечает."),
        "график памяти + состояние после",
        owner_live="длительный прогон на машине владельца", blocks_release=False),
)

ALL: tuple[Check, ...] = BREAKER + LIVE


def state_path() -> Path:
    root = Path(os.environ.get("BOSSMAN_BREAKER_ROOT")
                or REPO / ".bossman-state" / "acceptance")
    root.mkdir(parents=True, exist_ok=True)
    return root / "owner_breaker.json"


def load_state() -> dict[str, Any]:
    path = state_path()
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "results": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"schema_version": SCHEMA_VERSION, "results": {}, "corrupt_previous_state": True}
    if not isinstance(data, dict):
        return {"schema_version": SCHEMA_VERSION, "results": {}}
    data.setdefault("results", {})
    return data


def save_state(state: dict[str, Any]) -> Path:
    path = state_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)
    return path


def _ask(prompt: str, allowed: tuple[str, ...] | None = None) -> str:
    while True:
        raw = input(prompt).strip()
        if allowed is None:
            return raw
        upper = raw.upper()
        if upper in allowed:
            return upper
        print(f"    ожидается одно из: {', '.join(allowed)}")


def cmd_plan(args: argparse.Namespace) -> int:
    print("\nB1–B10 — последовательность брейкера\n")
    for check in BREAKER:
        print(f"  {check.ident}  {check.title}")
        print(f"        хостинг:  {check.hosted or 'НЕ ДОКАЗЫВАЕТ'}")
        if check.owner_live:
            print(f"        владелец: {check.owner_live}")
    print("\nЖивые расширения — хостинг их не закрывает\n")
    for check in LIVE:
        print(f"  {check.ident}  {check.title}")
        if check.hosted:
            print(f"        хостинг:  {check.hosted}")
        print(f"        владелец: {check.owner_live}")
    print()
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    state = load_state()
    results = state["results"]
    selected = [c for c in ALL if not args.only or c.ident in args.only]
    if not selected:
        print("нечего проходить: --only не совпал ни с одним пунктом")
        return 2
    for check in selected:
        if check.ident in results and not args.redo:
            print(f"\n  {check.ident} уже записан ({results[check.ident]['verdict']}); "
                  "--redo чтобы перепройти")
            continue
        print(f"\n=== {check.ident}. {check.title} ===")
        if check.hosted:
            print(f"  Хостинг уже проверил: {check.hosted}")
        else:
            print("  Хостинг это НЕ проверяет.")
        if check.owner_live:
            print(f"  Только машина владельца: {check.owner_live}")
        for number, step in enumerate(check.steps, 1):
            print(f"    {number}. {step}")
        print(f"  Улика: {check.proof}")
        started = time.time()
        allowed = (HOSTED_PASS, OWNER_LIVE_PASS, FAIL, BLOCKED)
        verdict = _ask(f"  вердикт [{'/'.join(allowed)}]: ", allowed)
        if verdict == HOSTED_PASS and check.owner_live:
            print("  ВНИМАНИЕ: у пункта есть часть, которую доказывает только машина")
            print("  владельца. HOSTED_PASS её НЕ закрывает и в отчёте останется пробелом.")
        proof = _ask("  улика (путь/id/скриншот): ")
        # §5 — измеряемый след прогона. Спрашивается по одному полю, потому что
        # «заполню потом» превращается в пустой отчёт, а пустой отчёт ничем не
        # отличается от непройденного шага.
        record = {field: _ask(f"  {prompt}: ") for field, prompt in FIELDS}
        notes = _ask("  заметки: ")
        results[check.ident] = {
            "ident": check.ident, "title": check.title, "verdict": verdict,
            "proof": proof, "notes": notes, "duration_s": round(time.time() - started, 1),
            "hosted": check.hosted, "owner_live": check.owner_live,
            "blocks_release": check.blocks_release,
            "build_sha": BUILD_SHA,
            **record,
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        save_state(state)
    print(f"\nсостояние: {state_path()}")
    return 0


def _gates(state: dict[str, Any]) -> dict[str, Any]:
    results = state.get("results", {})
    counts = {v: sum(1 for r in results.values() if r.get("verdict") == v) for v in VERDICTS}
    blocking = [r["ident"] for r in results.values()
                if r.get("verdict") == FAIL and r.get("blocks_release")]
    not_run = [c.ident for c in ALL if c.ident not in results]
    # Пункт с частью «только владелец» не закрывается хостингом. Это отдельный
    # список, а не «почти PASS»: иначе owner-live вечно остаётся на завтра.
    owner_gap = [c.ident for c in ALL if c.owner_live
                 and results.get(c.ident, {}).get("verdict") != OWNER_LIVE_PASS]
    verdict = ("FAIL" if blocking else
               "OWNER_LIVE_COMPLETE" if not not_run and not owner_gap else
               "INCOMPLETE")
    return {"total": len(ALL), "recorded": len(results), "verdicts": counts,
            "blocking_failures": blocking, "not_run": not_run,
            "owner_live_outstanding": owner_gap, "breaker_verdict": verdict}


def cmd_report(args: argparse.Namespace) -> int:
    state = load_state()
    gates = _gates(state)
    lines = ["# Owner breaker — отчёт", "",
             f"Записано {gates['recorded']} из {gates['total']}.", "",
             "| # | Пункт | Вердикт | Хостинг | Только владелец | Улика |",
             "|---|---|---|---|---|---|"]
    for check in ALL:
        r = state.get("results", {}).get(check.ident)
        lines.append("| {} | {} | {} | {} | {} | {} |".format(
            check.ident, check.title, (r or {}).get("verdict", NOT_RUN),
            (check.hosted or "—").replace("|", "/"),
            (check.owner_live or "—").replace("|", "/"),
            ((r or {}).get("proof") or "—").replace("|", "/")))
    lines += ["", "## Итог", "",
              f"- BREAKER_VERDICT = **{gates['breaker_verdict']}**",
              f"- блокирующие провалы: {', '.join(gates['blocking_failures']) or 'нет'}",
              f"- не запускались: {', '.join(gates['not_run']) or 'нет'}",
              "- ждут машины владельца: "
              f"{', '.join(gates['owner_live_outstanding']) or 'нет'}", ""]
    text = "\n".join(lines)
    out = Path(getattr(args, "md_out", None) or REPO / "OWNER_BREAKER_RESULT.md")
    out.write_text(text, encoding="utf-8")
    state["gates"] = gates
    save_state(state)
    print(text)
    print(f"\nОтчёт: {out}")
    return 0 if gates["breaker_verdict"] == "OWNER_LIVE_COMPLETE" else 1


def cmd_status(args: argparse.Namespace) -> int:
    state = load_state()
    gates = _gates(state)
    for check in ALL:
        r = state.get("results", {}).get(check.ident)
        print(f"  {check.ident:<4} {(r or {}).get('verdict', NOT_RUN):<16} {check.title}")
    print(f"\n  записано {gates['recorded']}/{gates['total']}, "
          f"вердикт {gates['breaker_verdict']}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Харнесс обязан быть исправен ДО приёмки, а не во время."""
    problems: list[str] = []
    idents = [c.ident for c in ALL]
    if len(set(idents)) != len(idents):
        problems.append("дублирующиеся идентификаторы")
    expected = [f"B{n}" for n in range(1, 13)]
    if [c.ident for c in BREAKER] != expected:
        problems.append(f"последовательность брейкера обязана быть {expected}")
    for check in ALL:
        if not check.steps or not check.proof or not check.title:
            problems.append(f"{check.ident}: неполное описание")
        if not check.hosted and not check.owner_live:
            problems.append(f"{check.ident}: не сказано ни что закрывает хостинг, "
                            "ни что остаётся владельцу")
    # Живые пункты обязаны называть владельческую часть — иначе они молча
    # переедут в хостинговую колонку и исчезнут из списка невыполненного.
    for check in LIVE:
        if not check.owner_live:
            problems.append(f"{check.ident}: живой пункт без owner_live")
    for line in problems:
        print(f"  ПРОБЛЕМА: {line}")
    if not problems:
        print(f"  харнесс исправен: {len(BREAKER)} пунктов брейкера, "
              f"{len(LIVE)} живых расширений")
    return 1 if problems else 0


def _console_utf8() -> None:
    """Windows consoles default to a legacy code page (cp1252 on the runners).

    Every line these harnesses print is Russian, so the first print crashed the
    owner-facing verify with UnicodeEncodeError before it could report anything.
    The launchers happened to export PYTHONUTF8, which hid it; a direct run and
    CI did not. Output encoding is not a verdict — make it robust here.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # already replaced or not a text stream
            pass


def main(argv: list[str] | None = None) -> int:
    _console_utf8()
    parser = argparse.ArgumentParser(prog="owner_breaker", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("plan", help="что предстоит и чем уже закрыто").set_defaults(func=cmd_plan)
    run = sub.add_parser("run", help="пройти последовательность")
    run.add_argument("--only", nargs="*", help="только эти пункты, например B7 B9")
    run.add_argument("--redo", action="store_true", help="перепройти уже записанные")
    run.set_defaults(func=cmd_run)
    report = sub.add_parser("report", help="собрать отчёт")
    report.add_argument("--md-out", type=Path, default=None)
    report.set_defaults(func=cmd_report)
    sub.add_parser("status", help="что уже сделано").set_defaults(func=cmd_status)
    sub.add_parser("verify", help="самопроверка харнесса").set_defaults(func=cmd_verify)
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
