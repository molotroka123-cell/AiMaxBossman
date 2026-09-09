#!/usr/bin/env python3
"""Вечерняя приёмка владельца — 12 реальных сценариев, а не юнит-тесты.

Зачем инструмент, а не список в документе: список нельзя проверить. Здесь
длительность ИЗМЕРЯЕТСЯ, а не вписывается; вердикт хранится вместе с уликой;
а после прогона харнесс сам сверяет заявленные задачи с корпусом реальных
нагрузок — если владелец говорит «задача прошла», а телеметрия её не видела,
это расхождение будет названо, а не сглажено.

Чего инструмент НЕ делает и делать не должен: он не выносит вердикт за
владельца и не «проходит» ни один тест сам. Живой браузер, реальный экспорт
видео и восстановление после убийства процесса проверяются руками — иначе это
снова тест о тестах.

    python scripts/evening_acceptance.py run       провести приёмку
    python scripts/evening_acceptance.py status    что уже сделано
    python scripts/evening_acceptance.py report    собрать отчёт
    python scripts/evening_acceptance.py verify    самопроверка харнесса (CI)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
PASS, FAIL, BLOCKED, SKIPPED = "PASS", "FAIL", "BLOCKED", "SKIPPED"
VERDICTS = (PASS, FAIL, BLOCKED, SKIPPED)
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Scenario:
    ident: str
    title: str
    goal: str
    steps: tuple[str, ...]
    proof: str
    blocks_release: bool = True
    needs: tuple[str, ...] = ()


CORPUS: tuple[Scenario, ...] = (
    Scenario(
        "T01", "Чат и рассуждение",
        "Обычная многошаговая задача целиком внутри продукта.",
        ("Откройте Command Center и начните новую задачу.",
         "Дайте задачу из НЕСКОЛЬКИХ шагов, где второй зависит от первого "
         "(например: собери факты, потом сравни их и сделай вывод).",
         "Смотрите на потоковый вывод: текст идёт по мере генерации, а не разом.",
         "Убедитесь, что состояние задачи меняется видимо: выполняется → завершена.",
         "Проверьте финальный ответ на связность со ВСЕМИ шагами."),
        "id задачи + скриншот финального состояния"),
    Scenario(
        "T02", "Веб-исследование",
        "Настоящий веб через настоящий браузерный путь, с ссылками.",
        ("Попросите исследовать тему, которая изменилась недавно "
         "(так ответ по памяти модели не пройдёт).",
         "Требуйте ссылки на источники.",
         "Откройте 2 ссылки руками: они существуют и содержат заявленное.",
         "Убедитесь, что взаимодействие было РЕАЛЬНЫМ: в логе/трассе видны запросы, "
         "а не только текст модели."),
        "ссылки + трасса браузера", needs=("browser",)),
    Scenario(
        "T03", "Управление компьютером",
        "Открыть приложение/страницу, нажать, напечатать, прочитать состояние.",
        ("Дайте безопасную локальную задачу: открыть страницу, ввести текст, "
         "проверить результат на экране.",
         "Наблюдайте запрос разрешения — он обязан появиться перед действием, "
         "меняющим состояние.",
         "ПРЕРВИТЕ выполнение один раз (Pause/Stop) и возобновите.",
         "Убедитесь, что после возобновления сделанное НЕ переигрывается."),
        "id задачи + скриншот до/после прерывания", needs=("browser",)),
    Scenario(
        "T04", "Задача по коду",
        "Воспроизвести баг → починить → доказать тестом.",
        ("Дайте небольшой контролируемый баг в репозитории.",
         "Требуйте: сначала воспроизведение, потом патч, потом тест.",
         "Проверьте, что тест ПАДАЕТ на старом коде и проходит на новом.",
         "Проверьте, что патч минимальный, а не переписывание модуля."),
        "diff + вывод теста до и после"),
    Scenario(
        "T05", "Web Studio",
        "Создать, отредактировать и выгрузить небольшую адаптивную страницу.",
        ("Создайте страницу, отредактируйте текст и стиль.",
         "Проверьте предпросмотр на узком и широком вьюпорте.",
         "Сохраните, закройте и откройте заново: правки на месте.",
         "Выгрузите (export) и откройте результат в браузере.",
         "Убедитесь, что ни одна кнопка не обещает ПУБЛИКАЦИЮ, если есть только "
         "предпросмотр и экспорт."),
        "экспортированный файл + два скриншота вьюпортов"),
    Scenario(
        "T06", "Video Studio",
        "Импорт реального клипа, обрезка, изменение таймлайна, экспорт.",
        ("Импортируйте короткий настоящий клип.",
         "Обрежьте его и измените таймлайн (сдвиг/разрез/удаление).",
         "Экспортируйте.",
         "Откройте результат в НЕЗАВИСИМОМ плеере (не внутри продукта).",
         "Проверьте ffprobe: длительность, разрешение, наличие звука.",
         "Отмените один экспорт на середине и убедитесь, что состояние осталось честным."),
        "путь к файлу + вывод ffprobe", needs=("ffmpeg",)),
    Scenario(
        "T07", "Файл или артефакт",
        "Создать настоящий документ и убедиться, что он существует и открывается.",
        ("Попросите создать документ/файл с конкретным содержимым.",
         "Найдите файл на диске по указанному пути.",
         "Откройте его сторонним приложением.",
         "Сверьте содержимое с тем, что было обещано в ответе."),
        "путь к файлу + скриншот открытого файла"),
    Scenario(
        "T08", "Восстановление",
        "Убить и перезапустить во время БЕЗОПАСНОЙ идемпотентной задачи.",
        ("Запустите безопасную (только чтение или идемпотентную) задачу.",
         "Убейте процесс на середине.",
         "Запустите заново и возобновите ту же задачу.",
         "Проверьте, что дублирующего эффекта НЕ произошло.",
         "Проверьте, что закрытые шаги не переигрались."),
        "журнал задачи до и после + доказательство отсутствия дубля"),
    Scenario(
        "T09", "Запрещённый эффект",
        "Отказать в записи/действии и проверить финальную истину задачи.",
        ("Дайте задачу, требующую записи или внешнего действия.",
         "На запросе разрешения ОТКАЖИТЕ.",
         "Прочитайте финальное состояние задачи.",
         "Оно НЕ должно быть «завершено»: отказ — это failed или blocked.",
         "Текст ответа при этом может сохраниться — это нормально; истина задачи важнее."),
        "скриншот финального состояния задачи"),
    Scenario(
        "T10", "Телеметрия",
        "Сверить корпус реальных нагрузок с тем, что на самом деле происходило.",
        ("Запустите: python scripts/evening_acceptance.py report",
         "Он сам вызовет real_workload_audit по накопленному корпусу.",
         "Сверьте число записей с числом задач, которые вы реально дали.",
         "Убедитесь, что ПРОВАЛЫ и ОТКАЗЫ тоже в корпусе, а не отфильтрованы.",
         "Убедитесь, что вердикт про железо честный "
         "(при малом числе задач это INSUFFICIENT_EVIDENCE — так и должно быть)."),
        "real_workload_audit.md"),
    Scenario(
        "T11", "Параллельность",
        "Несколько безопасных задач одновременно: смотреть на поведение, не на факт запуска.",
        ("Запустите 3–5 безопасных задач одновременно.",
         "Наблюдайте задержку: она растёт плавно или система встаёт?",
         "Наблюдайте память (диспетчер задач / htop).",
         "Проверьте, что admission ограничивает нагрузку, а не принимает всё подряд.",
         "Убедитесь, что все задачи дошли до ТЕРМИНАЛЬНОГО состояния."),
        "скриншот памяти + id всех задач", blocks_release=False),
    Scenario(
        "T12", "Обход интерфейса",
        "Открыть каждое основное приложение и найти неправду.",
        ("Откройте по очереди все приложения из меню.",
         "Video Studio должен быть ОДИН, Web Studio — ОДИН.",
         "Проверьте: нет мёртвых ссылок и кнопок без действия.",
         "Проверьте: нет кнопок, которые показывают успех, ничего не сделав.",
         "Проверьте вёрстку на длинном сообщении и на большом куске кода.",
         "Введите текст по-русски и проверьте, что он не искажён.",
         "Fleet обязан быть помечен как ЭКСПЕРИМЕНТАЛЬНЫЙ."),
        "скриншоты каждого приложения"),
)


def state_path() -> Path:
    root = Path(os.environ.get("BOSSMAN_ACCEPTANCE_ROOT")
                or REPO / ".bossman-state" / "acceptance")
    root.mkdir(parents=True, exist_ok=True)
    return root / "evening_acceptance.json"


def load_state() -> dict[str, Any]:
    path = state_path()
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "started_at": None, "results": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"schema_version": SCHEMA_VERSION, "started_at": None, "results": {},
                "corrupt_previous_state": True}
    if not isinstance(data, dict):
        return {"schema_version": SCHEMA_VERSION, "started_at": None, "results": {}}
    data.setdefault("results", {})
    return data


def save_state(state: dict[str, Any]) -> Path:
    path = state_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------- interaction


def _ask(prompt: str, allowed: tuple[str, ...] | None = None, default: str = "") -> str:
    while True:
        raw = input(prompt).strip()
        if not raw and default:
            return default
        if allowed is None:
            return raw
        upper = raw.upper()
        if upper in allowed:
            return upper
        print(f"    ожидается одно из: {', '.join(allowed)}")


def _preflight() -> dict[str, Any]:
    """Доктор запускается САМ: приёмка на сломанной машине — потерянный вечер."""
    out = subprocess.run([sys.executable, str(REPO / "scripts" / "bossman_doctor.py"), "--json"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return {"blocked": -1, "checks": [], "error": (out.stderr or out.stdout)[:500]}


def _unavailable(report: dict[str, Any]) -> set[str]:
    """Какие возможности доктор не подтвердил — чтобы честно предлагать BLOCKED."""
    bad = set()
    for check in report.get("checks", []):
        if check.get("status") in ("WARN", "BLOCKED"):
            bad.add(check.get("name", ""))
    return bad


def cmd_run(args: argparse.Namespace) -> int:
    state = load_state()
    state.setdefault("started_at", time.time())

    print(__doc__.split("\n\n")[0])
    print("\nСначала предполётная проверка.\n")
    doctor = _preflight()
    state["doctor"] = doctor
    if doctor.get("blocked"):
        print(f"BLOCKED-проверок: {doctor['blocked']}. Приёмка остановлена.")
        print("Запустите `python scripts/bossman_doctor.py` и закройте их.")
        save_state(state)
        return 1
    missing = _unavailable(doctor)
    print(f"Доктор: PASS {doctor.get('pass')}, WARN {doctor.get('warn')}, BLOCKED 0.\n")

    only = {x.upper() for x in (args.only or [])}
    for scenario in CORPUS:
        if only and scenario.ident not in only:
            continue
        if not args.redo and scenario.ident in state["results"]:
            print(f"{scenario.ident} уже пройден ({state['results'][scenario.ident]['verdict']}); "
                  f"--redo чтобы перепройти.")
            continue

        print("=" * 70)
        print(f"{scenario.ident} — {scenario.title}")
        print(f"Цель: {scenario.goal}")
        blocked_by = [n for n in scenario.needs if n in missing]
        if blocked_by:
            print(f"ВНИМАНИЕ: доктор не подтвердил {', '.join(blocked_by)}. "
                  f"Честный вердикт здесь — BLOCKED, а не FAIL.")
        print()
        for i, step in enumerate(scenario.steps, 1):
            print(f"  {i}. {step}")
        print(f"\n  Улика: {scenario.proof}")
        print(f"  Блокирует релиз: {'да' if scenario.blocks_release else 'нет'}")
        print()

        started = time.monotonic()
        _ask("  Нажмите Enter, когда НАЧНЁТЕ этот тест… ", allowed=None, default=" ")
        started = time.monotonic()
        verdict = _ask(f"  Вердикт [{'/'.join(VERDICTS)}]: ", VERDICTS)
        duration = round(time.monotonic() - started, 1)

        interventions = _ask("  Сколько раз пришлось вмешаться руками? [0]: ", None, "0")
        proof = _ask("  Улика (путь к файлу/скриншоту, id задачи): ")
        task_id = _ask("  id задачи/прогона (если виден): ")
        notes = _ask("  Заметки (что именно сломалось / что удивило): ")

        state["results"][scenario.ident] = {
            "ident": scenario.ident, "title": scenario.title, "verdict": verdict,
            "duration_s": duration, "measured_duration": True,
            "human_interventions": int(interventions) if interventions.isdigit() else 0,
            "proof": proof, "task_id": task_id, "notes": notes,
            "blocks_release": scenario.blocks_release,
            "doctor_gaps": blocked_by, "at": time.time(),
        }
        save_state(state)
        print(f"  → записано: {verdict} за {duration:.0f} с\n")

    save_state(state)
    print(f"Состояние приёмки: {state_path()}")
    return cmd_report(args)


# -------------------------------------------------------------------- report


def _telemetry_audit() -> dict[str, Any]:
    """Свести отчёт по РЕАЛЬНОМУ корпусу нагрузок, накопленному за вечер."""
    sys.path[:0] = [str(REPO / "bossman-core"), str(REPO)]
    try:
        from bossman_v3.execution import telemetry as tm
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
    path = tm.corpus_root() / tm.CORPUS_NAME
    if not path.exists():
        return {"available": False, "reason": f"корпус пуст: {path} не существует",
                "path": str(path)}
    import importlib.util
    spec = importlib.util.spec_from_file_location("rwa", REPO / "scripts" / "real_workload_audit.py")
    rwa = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("rwa", rwa)   # иначе dataclass/typing внутри не найдут модуль
    spec.loader.exec_module(rwa)
    try:
        records = rwa.load_records(path)
        report = rwa.build_report(records)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"available": False, "reason": f"корпус нечитаем: {exc}", "path": str(path)}
    out = REPO / "real_workload_audit.md"
    out.write_text(rwa.markdown(report), encoding="utf-8")
    (REPO / "real_workload_audit.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"available": True, "path": str(path), "records": len(records),
            "summary": report["summary"], "decision": report["decision"],
            "markdown": str(out),
            "task_ids": sorted({str(r.get("task_id", "")) for r in records if r.get("task_id")}),
            "statuses": {s: sum(1 for r in records if r.get("status") == s)
                         for s in ("passed", "failed", "blocked")}}


def _cross_check(state: dict[str, Any], telemetry: dict[str, Any]) -> list[str]:
    """Расхождения между словами и корпусом. Ни одно из них не сглаживается."""
    notes: list[str] = []
    if not telemetry.get("available"):
        notes.append(f"Корпус реальных нагрузок недоступен: {telemetry.get('reason')}. "
                     "Пункт TEST 10 нельзя считать пройденным.")
        return notes
    claimed = {r.get("task_id", "") for r in state.get("results", {}).values() if r.get("task_id")}
    recorded = set(telemetry.get("task_ids") or [])
    missing = sorted(claimed - recorded)
    if missing:
        notes.append("Владелец назвал задачи, которых НЕТ в корпусе телеметрии: "
                     + ", ".join(missing)
                     + ". Либо это не задачи исполнителя, либо телеметрия их не увидела.")
    failed_claimed = sum(1 for r in state.get("results", {}).values() if r.get("verdict") == FAIL)
    statuses = telemetry.get("statuses") or {}
    if failed_claimed and not (statuses.get("failed", 0) + statuses.get("blocked", 0)):
        notes.append(f"Владелец отметил {failed_claimed} FAIL, но в корпусе нет ни одной "
                     "записи failed/blocked. Провалы обязаны попадать в корпус.")
    return notes


def _gates(state: dict[str, Any]) -> dict[str, Any]:
    results = state.get("results", {})
    done = len(results)
    verdicts = {v: sum(1 for r in results.values() if r.get("verdict") == v) for v in VERDICTS}
    blocking_fail = [r["ident"] for r in results.values()
                     if r.get("verdict") == FAIL and r.get("blocks_release")]
    not_run = [s.ident for s in CORPUS if s.ident not in results]
    ready = not blocking_fail and not not_run and verdicts[PASS] > 0
    return {"scenarios_total": len(CORPUS), "scenarios_recorded": done, "verdicts": verdicts,
            "blocking_failures": blocking_fail, "not_run": not_run,
            "acceptance_verdict": "PASS" if ready else
            ("FAIL" if blocking_fail else "INCOMPLETE")}


def cmd_report(args: argparse.Namespace) -> int:
    state = load_state()
    telemetry = _telemetry_audit()
    gates = _gates(state)
    discrepancies = _cross_check(state, telemetry)

    lines = ["# Вечерняя приёмка владельца — отчёт", "",
             f"Сценариев записано: {gates['scenarios_recorded']} из {gates['scenarios_total']}", "",
             "| # | Сценарий | Вердикт | Длит., с | Вмешательств | Улика | Заметки |",
             "|---|---|---|---:|---:|---|---|"]
    for scenario in CORPUS:
        r = state.get("results", {}).get(scenario.ident)
        if not r:
            lines.append(f"| {scenario.ident} | {scenario.title} | НЕ ЗАПУСКАЛСЯ | — | — | — | — |")
            continue
        lines.append("| {} | {} | {} | {:.0f} | {} | {} | {} |".format(
            r["ident"], r["title"], r["verdict"], r.get("duration_s", 0),
            r.get("human_interventions", 0), (r.get("proof") or "—").replace("|", "/"),
            (r.get("notes") or "—").replace("|", "/")))

    lines += ["", "## Телеметрия реальных нагрузок", ""]
    if telemetry.get("available"):
        s = telemetry["summary"]
        lines += [f"- записей в корпусе: **{telemetry['records']}** "
                  f"(passed {telemetry['statuses']['passed']}, "
                  f"failed {telemetry['statuses']['failed']}, "
                  f"blocked {telemetry['statuses']['blocked']})",
                  f"- проверенный успех: {s['verified_passed']}/{s['tasks']}",
                  f"- вердикт про железо: **{telemetry['decision']['verdict']}**",
                  f"- отчёт: `{telemetry['markdown']}`"]
    else:
        lines.append(f"- НЕДОСТУПНА: {telemetry.get('reason')}")

    lines += ["", "## Расхождения", ""]
    lines += [f"- {n}" for n in discrepancies] or ["- не обнаружено"]
    lines += ["", "## Итог", "",
              f"- ACCEPTANCE_VERDICT = **{gates['acceptance_verdict']}**",
              f"- блокирующие провалы: {', '.join(gates['blocking_failures']) or 'нет'}",
              f"- не запускались: {', '.join(gates['not_run']) or 'нет'}", ""]

    text = "\n".join(lines)
    out = Path(getattr(args, "md_out", None) or REPO / "EVENING_ACCEPTANCE_RESULT.md")
    out.write_text(text, encoding="utf-8")
    state["gates"], state["telemetry"], state["discrepancies"] = gates, telemetry, discrepancies
    save_state(state)
    print(text)
    print(f"\nОтчёт: {out}")
    return 0 if gates["acceptance_verdict"] == "PASS" else 1


def cmd_status(args: argparse.Namespace) -> int:
    state = load_state()
    gates = _gates(state)
    for scenario in CORPUS:
        r = state.get("results", {}).get(scenario.ident)
        mark = r["verdict"] if r else "—"
        print(f"  {scenario.ident}  {mark:<8} {scenario.title}")
    print(f"\n  записано {gates['scenarios_recorded']}/{gates['scenarios_total']}, "
          f"вердикт {gates['acceptance_verdict']}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Самопроверка харнесса: он обязан быть исправен ДО вечера, а не во время."""
    problems: list[str] = []
    idents = [s.ident for s in CORPUS]
    if len(set(idents)) != len(idents):
        problems.append("дублирующиеся идентификаторы сценариев")
    if len(CORPUS) != 12:
        problems.append(f"ожидалось 12 сценариев, найдено {len(CORPUS)}")
    for s in CORPUS:
        if not s.steps or not s.proof or not s.goal:
            problems.append(f"{s.ident}: неполное описание")
    doc = REPO / "docs" / "release" / "EVENING_ACCEPTANCE_2026-09-06.md"
    if not doc.exists():
        problems.append(f"нет документа приёмки {doc}")
    else:
        body = doc.read_text(encoding="utf-8")
        for s in CORPUS:
            if s.ident not in body:
                problems.append(f"{s.ident} отсутствует в {doc.name} — документ и харнесс разошлись")
    doctor = REPO / "scripts" / "bossman_doctor.py"
    if not doctor.exists():
        problems.append("нет scripts/bossman_doctor.py")
    for line in problems:
        print(f"  ПРОБЛЕМА: {line}")
    if not problems:
        print(f"  харнесс исправен: {len(CORPUS)} сценариев, документ на месте, доктор на месте")
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
    parser = argparse.ArgumentParser(prog="evening_acceptance", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="провести приёмку")
    run.add_argument("--only", nargs="*", help="только эти сценарии, например T06 T09")
    run.add_argument("--redo", action="store_true", help="перепройти уже записанные")
    run.add_argument("--md-out", type=Path, default=None)
    run.set_defaults(func=cmd_run)
    rep = sub.add_parser("report", help="собрать отчёт из записанного")
    rep.add_argument("--md-out", type=Path, default=None)
    rep.set_defaults(func=cmd_report)
    sub.add_parser("status", help="что уже сделано").set_defaults(func=cmd_status)
    sub.add_parser("verify", help="самопроверка харнесса").set_defaults(func=cmd_verify)
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
