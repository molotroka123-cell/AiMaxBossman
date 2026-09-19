#!/usr/bin/env python3
"""Владельческий прогон на УСТАНОВЛЕННОМ продукте: слабая, средняя и длинная задача.

Запускается на чистой машине (в CI — windows-latest), ставит продукт так же, как
его поставит владелец, и гоняет три задачи по нарастающей. Репозитория на пути
импорта нет: всё идёт через HTTP к установленному продукту.

ЧЕСТНО О ГРАНИЦАХ, до единого результата:

  * локальной модели в этом окружении НЕТ. Всё, что требует LLM — планирование
    миссии, кодовый бэкенд, предложение имён файлов — здесь НЕ ПРОВЕРЯЕТСЯ и
    помечается NOT_RUN. Выдавать отсутствие модели за успех нельзя;
  * измеряется то, что продукт делает САМ: жизненный цикл задач, исполнение
    команд с подтверждением владельца, правка и сохранение сайта, переживание
    перезапуска, восстановление после ЖЁСТКОГО убийства процесса под нагрузкой.

Правило раздела 0 соблюдается буквально: ответ движка — НАБЛЮДЕНИЕ, а не факт.
Каждый шаг завершается НЕЗАВИСИМЫМ перечитыванием — отдельным GET или чтением
файловой системы, — и только оно считается уликой.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_clean_install import (  # noqa: E402
    Client, _console_utf8, install, login, pristine_export, start, stop)

PASS, FAIL, NOT_RUN = "PASS", "FAIL", "NOT_RUN"

# Команда пишет маркер СТРОКОЙ В КОНЕЦ файла, а не перезаписывает его. Разница
# принципиальная: при перезаписи повторное исполнение той же команды неотличимо
# от единственного, и проверка «ничего не исполнено дважды» была бы пустой —
# она не смогла бы провалиться. С дозаписью дубль виден как вторая строка.
_APPENDER = """import sys
with open(sys.argv[1], "a", encoding="utf-8") as stream:
    stream.write(sys.argv[2] + "\\n")
"""


#: Подсистемы, за которые продукт отвечает САМ и обязан держать `ok`. Остальные
#: ключи здоровья описывают внешний мир — `browser: unknown`, пока живой Chromium
#: не наблюдали, `models`/`providers: empty`, пока владелец не подключил модель.
#: Требовать от них `ok` на раннере без модели и браузера значит изобрести свой
#: критерий строже контракта и покрасить в красный честный ответ продукта.
CONTRACTED = ("db", "queue_worker", "scheduler", "metrics")


def _unhealthy(health: dict) -> dict:
    need = [*CONTRACTED, *(k for k in health if k.startswith("tick:"))]
    statuses = {k: health[k].get("status", "missing")
                if isinstance(health.get(k), dict) else "missing" for k in need}
    return {k: status for k, status in statuses.items() if status != "ok"}


def _wait_healthy(c: Client, timeout: float = 40.0) -> dict:
    """HTTP readiness precedes the first background tick, including on restart.

    Poll for actual ok statuses; starting, missing and error never count as ok.
    The same bounded readiness contract applies before and after owner tasks.
    """
    deadline = time.monotonic() + timeout
    while True:
        health = c("/api/system")["health"]
        bad = _unhealthy(health)
        if not bad:
            return health
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"обещанные подсистемы не поднялись за {timeout:g} с: {bad}")
        time.sleep(min(0.5, remaining))


class _Tee:
    """Живой вывод в CI и одновременно файл-журнал: одно и то же, без расхождений."""

    def __init__(self, stream, path: Path) -> None:
        self.stream = stream
        self.file = path.open("w", encoding="utf-8", newline="\n")

    def write(self, text: str) -> int:
        self.file.write(text)
        self.file.flush()
        return self.stream.write(text)

    def flush(self) -> None:
        self.file.flush()
        self.stream.flush()

    def close(self) -> None:
        self.file.close()


class NotRunError(RuntimeError):
    """Шаг не проверен и это сказано прямо — не выдаётся за успех."""


class Run:
    """Журнал прогона: каждый шаг называет, что просили и чем это ПРОВЕРЕНО."""

    def __init__(self) -> None:
        self.steps: list[dict] = []
        self.started = time.monotonic()

    def step(self, task: str, name: str, fn) -> dict:
        t0 = time.monotonic()
        try:
            detail = fn() or ""
            row = {"задача": task, "шаг": name, "итог": PASS, "улика": detail}
        except NotRunError as exc:
            row = {"задача": task, "шаг": name, "итог": NOT_RUN, "улика": str(exc)}
        except Exception as exc:
            row = {"задача": task, "шаг": name, "итог": FAIL,
                   "улика": f"{type(exc).__name__}: {exc}"[:800]}
        row["секунд"] = round(time.monotonic() - t0, 1)
        self.steps.append(row)
        print(f"[{row['итог']:<7}] {task:<9} {name:<50} {row['улика']}", flush=True)
        return row

    def fail(self, task: str, name: str, exc: BaseException) -> None:
        self.step(task, name, lambda: (_ for _ in ()).throw(exc))

    @property
    def failed(self) -> int:
        return sum(1 for s in self.steps if s["итог"] == FAIL)


def _appender(work: Path) -> Path:
    """Маленький скрипт-дозаписчик рядом с данными владельца.

    Команда собирается из четырёх раздельно закавыченных путей и НЕ содержит
    вложенных кавычек, `$`, обратных кавычек и цепочек: одинаково понимается
    и `sh -lc` (он есть на Windows-раннере вместе с git), и `cmd /c`.
    """
    path = work / "data" / "append_marker.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_APPENDER, encoding="utf-8")
    return path


def _marker_command(python: Path, helper: Path, out: Path, marker: str) -> str:
    return f'"{python}" "{helper}" "{out}" "{marker}"'


def _approved_run(c: Client, payload: dict) -> str:
    """Запуск команды по полному owner-циклу: запрос → подтверждение → запуск."""
    first = json.loads(c("/api/terminal/run", method="POST", payload=payload,
                         expected=None, raw=True))
    if "session_id" in first:
        return first["session_id"]
    approval = first["error"]["approval_id"]
    c(f"/api/approvals/{approval}", method="POST", payload={"approve": True, "by": "owner"})
    return c("/api/terminal/run", method="POST",
             payload=dict(payload, approval_id=approval))["session_id"]


def _wait_finished(c: Client, session: str, timeout: float = 120.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = c(f"/api/terminal/sessions/{session}")
        if status.get("finished"):
            return status
        time.sleep(0.2)
    raise AssertionError(f"сессия {session} не завершилась за {timeout:.0f} с")


def audit_markers(done: Path, issued: list[str], confirmed: list[str]) -> dict:
    """Что на самом деле лежит на диске после длинного прогона.

    Отдельная чистая функция, потому что именно она выносит приговор, и её
    саму надо уметь провалить: `tests/test_windows_owner_tasks_audit.py`
    подсовывает ей потерю, дубль, лишнее исполнение и подменённый маркер и
    требует, чтобы она их назвала. Проверка, которая не может провалиться,
    ничего не проверяет.

    Возвращает ФАКТЫ, а не вердикт: решение принимает вызывающий шаг.
    """
    content: dict[str, list[str]] = {}
    for path in sorted(done.glob("HARD-*.txt")):
        content[path.stem] = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln]
    known = set(issued)
    return {
        "файлов": len(content),
        "потеряно": [m for m in confirmed if m not in content or m not in content[m]],
        "дважды": {m: lines for m, lines in content.items() if len(lines) > 1},
        "лишние": sorted(m for m in content if m not in known),
        "подменено": {m: lines for m, lines in content.items()
                      if len(lines) == 1 and lines[0] != m},
    }


#: Что именно прочитал замер памяти в первый раз — сырая строка, как её отдала
#: система. Без неё число в отчёте нечем перепроверить, а перепроверять пришлось:
#: прогон на Windows отдал 3 936 КБ там, где на Linux тот же продукт показывает
#: около 116 МБ. Такое значение неправдоподобно, и плоская линия, снятая неверным
#: прибором, — это не доказательство отсутствия роста, а отсутствие доказательства.
_RSS_RAW: dict[str, str] = {}


def _rss_kb(pid: int) -> int | None:
    """Резидентная память процесса продукта. НАБЛЮДЕНИЕ, не приговор.

    Возвращает None, когда прочитать не удалось, и НИКОГДА не возвращает
    правдоподобное число вместо признания неудачи: число без происхождения
    успокаивает ровно так же, как верное, и в этом вся опасность.
    """
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                 capture_output=True, timeout=20).stdout.decode("utf-8", "replace")
            row = out.strip().splitlines()[-1] if out.strip() else ""
            _RSS_RAW.setdefault("сырая_строка", row[:200])
            # `tasklist` при отсутствии процесса печатает не строку CSV, а фразу
            # INFO:. Разбираем настоящим CSV, а не split по кавычкам, и требуем,
            # чтобы последняя колонка выглядела как «123 456 K».
            fields = next(csv.reader([row]), [])
            if len(fields) < 5:
                return None
            cell = fields[-1].strip()
            _RSS_RAW.setdefault("колонка_памяти", cell[:60])
            if not cell.upper().endswith("K"):
                return None
            digits = re.sub(r"[^0-9]", "", cell)
            return int(digits) if digits else None
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)],
                             capture_output=True, timeout=20).stdout.decode()
        _RSS_RAW.setdefault("сырая_строка", out.strip()[:200])
        return int(out.strip())
    except Exception as exc:
        _RSS_RAW.setdefault("ошибка", f"{type(exc).__name__}: {exc}"[:200])
        return None


# --------------------------------------------------------------------------
# СЛАБАЯ задача: короткий владельческий цикл
# --------------------------------------------------------------------------
def task_light(run: Run, c: Client, state: dict) -> None:
    def identity() -> str:
        ident = c("/api/identity")
        return f"продукт назвал себя: {json.dumps(ident, ensure_ascii=False)[:120]}"
    run.step("слабая", "продукт называет свою личность", identity)

    def agent() -> str:
        made = c("/api/agents", method="POST", payload={"name": "прогон-слабая", "enabled": False})
        state["agent"] = made["id"]
        # независимое перечитывание ОТДЕЛЬНЫМ запросом, а не по ответу на создание
        assert any(a["id"] == made["id"] for a in c("/api/agents")), "агента нет в списке"
        return f"агент {made['id']} создан и перечитан отдельным запросом"
    run.step("слабая", "агент создаётся и читается независимо", agent)

    def health() -> str:
        observed = _wait_healthy(c)
        other = {k: v.get("status") for k, v in observed.items()
                 if isinstance(v, dict) and k not in CONTRACTED
                 and not k.startswith("tick:")}
        return (f"обещанные подсистемы ok; остальное — наблюдение: "
                f"{json.dumps(other, ensure_ascii=False)}")
    run.step("слабая", "фоновые циклы здоровы", health)


# --------------------------------------------------------------------------
# СРЕДНЯЯ задача: реальный эффект, отказ без подтверждения, версии сайта
# --------------------------------------------------------------------------
def task_medium(run: Run, c: Client, work: Path, python: Path, state: dict) -> None:
    helper = _appender(work)

    def refuse_self_approval() -> str:
        body = c("/api/terminal/run", method="POST", expected=None, raw=True, payload={
            "mode": "project_host", "command": "echo self-approved",
            "cwd": str(work / "data"), "approved": True}).decode("utf-8", "replace")
        assert "session_id" not in body, f"исполнено по самоутверждению: {body[:200]}"
        return "самоутверждённый флаг отклонён (обратный контроль)"
    run.step("средняя", "без подтверждения владельца действия нет", refuse_self_approval)

    def real_command() -> str:
        marker = "BOSSMAN-MEDIUM-7741"
        out = work / "data" / "medium.txt"
        session = _approved_run(c, {"mode": "project_host", "cwd": str(work / "data"),
                                    "command": _marker_command(python, helper, out, marker)})
        status = _wait_finished(c, session)
        assert status.get("exit_code") == 0, f"ненулевой код: {json.dumps(status)[:300]}"
        # УЛИКА — файл на диске, а не отчёт движка
        assert out.exists(), "движок отчитался об успехе, а файла-улики нет"
        lines = [ln for ln in out.read_text(encoding="utf-8").splitlines() if ln]
        assert lines == [marker], f"на диске не то, что просили: {lines}"
        return "команда исполнена, exit=0, файл на диске содержит ровно один маркер"
    run.step("средняя", "команда выполняется и оставляет улику на диске", real_command)

    def designer() -> str:
        project = c("/api/web-designer/projects", method="POST",
                    payload={"name": "прогон-средняя", "template": "blank"})
        state["pid"] = int(project["meta"]["id"])
        state["version"] = project["meta"]["version"]
        html = ('<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">'
                '<title>прогон</title></head><body><h1 id="marker">MEDIUM-OK</h1></body></html>')
        out = c(f"/api/web-designer/projects/{state['pid']}/code", method="PUT",
                payload={"html": html, "note": "прогон", "base_version": state["version"]})
        state["version"] = out["meta"]["version"]
        again = c(f"/api/web-designer/projects/{state['pid']}")["code"]
        assert 'id="marker"' in again, "правка не перечитывается"
        return f"проект {state['pid']}, версия {state['version']}, код перечитан независимо"
    run.step("средняя", "сайт правится и правка перечитывается", designer)

    def refuse_stale() -> str:
        c(f"/api/web-designer/projects/{state['pid']}/code", method="PUT", expected=None,
          raw=True, payload={"html": "<!DOCTYPE html><html><body><h1>stale</h1></body></html>",
                             "note": "устаревшая", "base_version": 0})
        assert 'id="marker"' in c(f"/api/web-designer/projects/{state['pid']}")["code"], (
            "устаревшая правка затёрла актуальный код")
        return "правка поверх устаревшей версии отклонена (обратный контроль)"
    run.step("средняя", "рассинхрон версий отклоняется", refuse_stale)


# --------------------------------------------------------------------------
# СЛОЖНАЯ супер-длинная: нагрузка, ЖЁСТКОЕ убийство в полёте, восстановление
# --------------------------------------------------------------------------
def task_hard(run: Run, python: Path, work: Path, state: dict, minutes: float) -> None:
    """Длинный прогон под нагрузкой с убийством процесса в середине.

    Смысл не в «поработать подольше», а в том, что проверяется ПОСЛЕ убийства.
    Процесс убивается ровно один раз и НЕ между командами, а пока команда ещё
    в полёте — иначе убивать нечему и проверка была бы декоративной. Дальше:

      * ни одна ПОДТВЕРЖДЁННАЯ движком команда не должна пропасть;
      * ни одна команда не должна исполниться дважды — включая ту, что была в
        полёте в момент убийства. Потерять её допустимо и это фиксируется
        отдельно; исполнить её повторно после перезапуска — нет.

    Считается по файлам на диске, мимо бухгалтерии движка: в момент падения
    именно она и есть подозреваемый.
    """
    done = work / "data" / "hard"
    done.mkdir(parents=True, exist_ok=True)
    helper = _appender(work)
    confirmed: list[str] = []
    issued: list[str] = []
    latency: list[float] = []
    samples: list[dict] = []
    inflight: str | None = None
    deadline = time.monotonic() + minutes * 60
    half = time.monotonic() + minutes * 30

    process, c = start(python, work)
    try:
        login(c, work)
        i = 0
        while time.monotonic() < deadline:
            i += 1
            marker = f"HARD-{i:04d}"
            out = done / f"{marker}.txt"
            payload = {"mode": "project_host", "cwd": str(work / "data"),
                       "command": _marker_command(python, helper, out, marker)}
            t0 = time.monotonic()
            try:
                kill_now = inflight is None and time.monotonic() >= half
                session = _approved_run(c, payload)
                issued.append(marker)
                if kill_now:
                    inflight = marker
                    time.sleep(0.2)            # команда уже стартовала и ещё идёт
                    before = len(list(done.glob("HARD-*.txt")))
                    process.kill()
                    process.wait(timeout=30)
                    process, c = start(python, work)
                    login(c, work)
                    state["kill"] = {"на_шаге": marker, "файлов_до": before}
                    run.step("сложная", "продукт поднялся после ЖЁСТКОГО убийства",
                             lambda m=marker, n=before:
                             f"убит во время команды {m} (не между командами), "
                             f"поднялся заново, файлов до падения {n}")
                    continue
                status = _wait_finished(c, session, timeout=90)
                assert status.get("exit_code") == 0, f"{marker}: {json.dumps(status)[:200]}"
                confirmed.append(marker)
            except Exception as exc:
                run.fail("сложная", f"нагрузка оборвалась на шаге {i}", exc)
                break
            latency.append(time.monotonic() - t0)
            if i % 25 == 0:
                samples.append({"шагов": i, "секунд": round(time.monotonic() - run.started, 1),
                                "файлов_на_диске": len(list(done.glob("HARD-*.txt"))),
                                "память_кб": _rss_kb(process.pid)})
    finally:
        try:
            stop(process)
        except Exception:
            pass

    time.sleep(2.0)   # дать осиротевшему потомку убитого сервера дописать своё
    state.update({"выдано": len(issued), "подтверждено": len(confirmed),
                  "в_полёте_при_убийстве": inflight, "замеры": samples})

    facts = audit_markers(done, issued, confirmed)
    state["аудит"] = {k: (v if not isinstance(v, dict) else {m: l for m, l in list(v.items())[:5]})
                      for k, v in facts.items()}

    def no_loss() -> str:
        assert confirmed, "ни одной подтверждённой команды — нагрузки не было"
        assert not facts["потеряно"], (
            f"потеряно {len(facts['потеряно'])} подтверждённых команд: {facts['потеряно'][:5]}")
        assert not facts["подменено"], f"на диске не тот маркер: {facts['подменено']}"
        return (f"подтверждено {len(confirmed)} команд (выдано {len(issued)}), все "
                f"нашлись ФАЙЛАМИ на диске — проверено мимо отчётов движка")
    run.step("сложная", "ни одна подтверждённая команда не потеряна", no_loss)

    def no_duplicates() -> str:
        assert not facts["дважды"], f"исполнено повторно: {list(facts['дважды'].items())[:5]}"
        assert not facts["лишние"], (
            f"исполнено то, чего владелец не просил: {facts['лишние'][:5]}")
        note = ""
        if inflight is not None:
            ran = (done / f"{inflight}.txt").is_file()
            note = (f"; команда {inflight} была в полёте при убийстве — "
                    f"{'исполнилась ровно один раз' if ran else 'потеряна, и это честный исход'}"
                    ", но НЕ дважды")
        return (f"{facts['файлов']} файлов, в каждом ровно один свой маркер, "
                f"лишних исполнений нет{note}")
    run.step("сложная", "ничего не исполнено дважды и лишнего", no_duplicates)

    def survives() -> str:
        process2, c2 = start(python, work)
        try:
            login(c2, work)
            assert any(a["id"] == state["agent"] for a in c2("/api/agents")), "агент пропал"
            assert 'id="marker"' in c2(f"/api/web-designer/projects/{state['pid']}")["code"], (
                "правка сайта потеряна")
            _wait_healthy(c2)
            return (f"агент и правка сайта на месте, обещанные подсистемы ok после "
                    f"{len(confirmed)} команд и жёсткого убийства")
        finally:
            stop(process2)
    run.step("сложная", "состояние владельца пережило всё это", survives)

    if len(latency) >= 8:
        quarter = max(1, len(latency) // 4)
        first, last = statistics.median(latency[:quarter]), statistics.median(latency[-quarter:])
        state["задержка"] = {"первая_четверть_с": round(first, 3),
                             "последняя_четверть_с": round(last, 3),
                             "рост": round(last / first, 2) if first else None}
        print(f"           наблюдение: медиана цикла {first:.3f} с в начале → "
              f"{last:.3f} с в конце ({len(latency)} команд). Это ЗАМЕР на шумной "
              f"машине CI, а не критерий приёмки.", flush=True)

    # Рост памяти на длинной дистанции — то, чего короткие тесты не видят в
    # принципе. Числа печатаются как НАБЛЮДЕНИЕ: на шумной машине CI порог
    # был бы выдуманным, а выдуманный порог — это либо ложная тревога, либо
    # зелёный, купленный за счёт правды.
    rss = [(r["шагов"], r["память_кб"]) for r in samples
           if isinstance(r.get("память_кб"), int)]
    if len(rss) >= 8:
        # Наклон считается ОТДЕЛЬНО по началу и по концу прогона. Один общий
        # наклон уже однажды ввёл в заблуждение здесь же: первые сотни команд
        # продукт НАПОЛНЯЕТ ограниченный кэш сессий, и это законный разовый
        # расход, а не утечка. Смешанные в одно число, два режима выглядят как
        # безостановочный рост, и починка кажется бесполезной.
        q = max(2, len(rss) // 4)
        def slope(rows):
            (a, x), (b, y) = rows[0], rows[-1]
            return (y - x) / max(1, b - a)
        first, last = slope(rss[:q]), slope(rss[-q:])
        state["память"] = {"первый_замер_кб": rss[0][1], "последний_замер_кб": rss[-1][1],
                           "кб_на_команду_в_начале": round(first, 2),
                           "кб_на_команду_в_конце": round(last, 2),
                           "шагов": rss[-1][0] - rss[0][0]}
        print(f"           наблюдение: память {rss[0][1]} КБ → {rss[-1][1]} КБ за "
              f"{rss[-1][0] - rss[0][0]} команд; наклон {first:+.2f} КБ/команду в "
              f"начале и {last:+.2f} КБ/команду в конце. Выход на полку — это "
              f"наполненный кэш, а не остановленная утечка.", flush=True)


def task_model_dependent(run: Run) -> None:
    """То, что без локальной модели проверить нельзя — и это сказано, а не скрыто."""
    def missing() -> str:
        raise NotRunError(
            "локальной модели в окружении нет: планирование миссии, кодовый бэкенд и "
            "предложение имён файлов НЕ ПРОВЕРЕНЫ. Отсутствие модели не является успехом")
    run.step("модельные", "сценарии, требующие локальной LLM", missing)


def main(argv: list[str] | None = None) -> int:
    _console_utf8()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--minutes", type=float, default=18.0,
                        help="длительность СЛОЖНОЙ задачи в минутах")
    parser.add_argument("--json-out", type=Path, default=Path("windows-owner-tasks.json"))
    parser.add_argument("--log", type=Path, default=None,
                        help="дублировать весь вывод в файл-журнал")
    args = parser.parse_args(argv)
    tee = _Tee(sys.stdout, args.log) if args.log else None
    if tee is not None:
        sys.stdout = tee
    try:
        return _run_everything(args)
    finally:
        if tee is not None:
            sys.stdout = tee.stream
            tee.close()


def _run_everything(args) -> int:

    scratch = Path(tempfile.mkdtemp(prefix="bossman-owner-tasks-"))
    run = Run()
    state: dict = {}
    print(f"хост: {platform.platform()} | python {sys.version.split()[0]}", flush=True)
    print("чистый экспорт коммита...", flush=True)
    source = pristine_export(scratch / "source")
    print("установка продукта, как его поставит владелец...", flush=True)
    python = install(source, scratch / "venv")
    work = scratch / "run"
    work.mkdir()

    print("\n--- СЛАБАЯ и СРЕДНЯЯ задачи ---", flush=True)
    process, c = start(python, work)
    try:
        login(c, work)
        task_light(run, c, state)
        task_medium(run, c, work, python, state)
    finally:
        stop(process)

    print(f"\n--- СЛОЖНАЯ задача: {args.minutes:g} минут под нагрузкой ---", flush=True)
    task_hard(run, python, work, state, args.minutes)
    task_model_dependent(run)

    total = len(run.steps)
    not_run = sum(1 for s in run.steps if s["итог"] == NOT_RUN)
    report = {
        "type": "bossman.windows_owner_tasks",
        "когда": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "хост": platform.platform(),
        "python": sys.version.split()[0],
        "sha": os.environ.get("BOSSMAN_ACCEPTANCE_SHA", ""),
        "минут_на_сложную": args.minutes,
        "всего_секунд": round(time.monotonic() - run.started, 1),
        "шаги": run.steps,
        "команд_выдано": state.get("выдано", 0),
        "команд_подтверждено": state.get("подтверждено", 0),
        "убийство": state.get("kill"),
        "в_полёте_при_убийстве": state.get("в_полёте_при_убийстве"),
        "задержка": state.get("задержка"),
        "память": state.get("память"),
        "чем_измерена_память": dict(_RSS_RAW),
        "замеры": state.get("замеры", []),
        "итог": PASS if not run.failed else FAIL,
    }
    args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    print(f"\nWINDOWS_OWNER_TASKS={report['итог']} "
          f"({total - run.failed - not_run} PASS, {run.failed} FAIL, {not_run} NOT_RUN); "
          f"подтверждённых команд в длинной задаче: {report['команд_подтверждено']}; "
          f"всего {report['всего_секунд']} с")
    return 1 if run.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
