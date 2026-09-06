#!/usr/bin/env python3
"""Снять РЕАЛЬНОЕ парное измерение удержания интеллекта на локальной модели.

Гейт `tools/intelligence_preservation_gate.py` существовал без измерителя:
канонический `docs/benchmark/intelligence-preservation-current.json` никто не
производил, поэтому проверка Intelligence Preservation в CI падала не из-за
модели и не из-за кода, а из-за отсутствия файла. Это он и производит.

Что здесь измеряется: ОДНА модель на ОДНОМ наборе задач в четырёх режимах —

    RAW      только задача, без системного промпта;
    SYSTEM   + системный промпт оператора;
    CONTEXT  + срез контекста (память/факты, доступные модели);
    FULL     + инструменты и полная обвязка.

Смысл сравнения: обвязка не должна ОТНИМАТЬ у модели способности. Поэтому
каждая задача прогоняется во всех четырёх режимах, и по каждой метрике
считается ПАРНОЕ расхождение с базовой полосой (`lost`/`gained`) — сколько
пунктов полоса потеряла и сколько приобрела на тех же самых задачах. Именно
парность позволяет гейту сделать вывод на десятках задач, а не на тысячах.

Честность встроена, а не декларируется:

  * ничего не выдумывается. Модель вызывается по-настоящему; при отсутствии
    ответа задача считается проваленной, а не пропускается;
  * `evaluated_sha` берётся из git и обязан совпасть с проверяемым коммитом —
    вердикт по старому SHA не является вердиктом по новому;
  * если задач меньше порога, файл всё равно пишется, но гейт ответит
    INSUFFICIENT_EVIDENCE. Это правильный ответ, а не дефект;
  * оценка каждого пункта — детерминированная проверка (точное совпадение,
    разбор JSON, имя инструмента), а не мнение второй модели.

Запуск на машине владельца (модель уже поднята локально):

    python tools/intelligence_preservation_run.py \\
        --model qwen2.5-coder:14b \\
        --endpoint http://127.0.0.1:11435 \\
        --out docs/benchmark/intelligence-preservation-current.json

Затем:

    python tools/intelligence_preservation_gate.py \\
        docs/benchmark/intelligence-preservation-current.json \\
        --expect-sha $(git rev-parse HEAD)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

MODES = ("raw", "system", "context", "full")
BASELINE = "raw"

# Метрики, которых требует гейт. Каждая задача набора объявляет, какую из них
# она измеряет; метрика без задач честно получает 0 задач, и гейт это увидит.
REQUIRED_METRICS = (
    "reasoning_accuracy", "coding_correctness", "structured_output_accuracy",
    "unknown_task_adaptation", "tool_selection_accuracy", "schema_argument_accuracy",
    "long_context_accuracy", "memory_retrieval_accuracy",
    "computer_use_planning_accuracy", "task_completion_rate", "hallucination_rate",
)
# Для этой метрики единица — ПЛОХО (доля выдумок), гейт её так и трактует.
LOWER_IS_BETTER = ("hallucination_rate",)

# Порог `--min-samples 20` в CI — это НИЖНЯЯ ГРАНИЦА ДОПУСТИМОСТИ, а не
# достаточность. Утверждение «удержано не меньше 98%» — это доверительная
# нижняя граница, и она зависит от размера выборки. Измерено прямым прогоном
# гейта: даже БЕЗУПРЕЧНЫЙ парный прогон (ни одной потерянной задачи) получает
# PASS только начиная со 189 задач на метрику; при 20 задачах ответ будет
# INSUFFICIENT_EVIDENCE, каким бы идеальным ни был результат. Число названо
# здесь, чтобы владелец узнавал это ДО многочасового локального прогона, а не
# после него.
SUFFICIENT_SAMPLES_PER_METRIC = 189

SYSTEM_PROMPT = (
    "Ты — BOSSMAN, оператор компьютера владельца. Отвечай кратко и по существу. "
    "Если просят JSON — верни только JSON, без пояснений и без markdown-ограждений."
)
TOOLS_PROMPT = (
    "Доступные инструменты: fs.read(path), fs.write(path, text), browser.open(url), "
    "terminal.run(command), computer.click(x, y), computer.type(text). "
    "Когда нужен инструмент — назови ровно один и только его имя."
)


class RunnerError(RuntimeError):
    """Измерение не состоялось. Пустой результат — не результат."""


@dataclass(frozen=True)
class Task:
    task_id: str
    metric: str
    prompt: str
    expect: dict[str, Any]
    context: str = ""

    def rendered(self, mode: str) -> list[dict[str, str]]:
        """Промпт для полосы. Полосы отличаются ТОЛЬКО обвязкой, не задачей."""
        messages: list[dict[str, str]] = []
        if mode != "raw":
            system = SYSTEM_PROMPT
            if mode == "full":
                system = f"{SYSTEM_PROMPT}\n{TOOLS_PROMPT}"
            messages.append({"role": "system", "content": system})
        user = self.prompt
        if mode in ("context", "full") and self.context:
            user = f"Известное о проекте:\n{self.context}\n\nЗадача: {self.prompt}"
        messages.append({"role": "user", "content": user})
        return messages


@dataclass
class LaneMetric:
    passed: float = 0.0
    samples: int = 0
    lost: int = 0
    gained: int = 0
    outcomes: dict[str, bool] = field(default_factory=dict)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _first_json(text: str) -> Any:
    """Достать первый JSON-объект из ответа, не притворяясь, что его нет."""
    raw = str(text or "")
    fence = re.search(r"```(?:json)?\s*(.+?)```", raw, re.S)
    if fence:
        raw = fence.group(1)
    start = raw.find("{")
    if start < 0:
        raise ValueError("no JSON object in the answer")
    depth, quote = 0, ""
    for i in range(start, len(raw)):
        ch = raw[i]
        if quote:
            if ch == quote and raw[i - 1] != "\\":
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(raw[start:i + 1])
    raise ValueError("unterminated JSON object")


def score(task: Task, answer: str) -> bool:
    """Детерминированная проверка одного ответа. Никакой модели-судьи.

    Мнение второй модели — не измерение: оно само зависит от обвязки, которую
    мы и проверяем.
    """
    expect = task.expect
    kind = expect.get("kind")
    text = str(answer or "")
    if kind == "equals":
        return _norm(text) == _norm(expect["value"])
    if kind == "contains":
        return all(_norm(v) in _norm(text) for v in expect["all_of"])
    if kind == "absent":
        # Проверка выдумок: правильный ответ — признать незнание, а не назвать
        # правдоподобное имя. Совпадение с приманкой считается провалом.
        return not any(_norm(v) in _norm(text) for v in expect["none_of"])
    if kind == "json_fields":
        try:
            obj = _first_json(text)
        except (ValueError, json.JSONDecodeError):
            return False
        if not isinstance(obj, dict):
            return False
        for key, value in expect["fields"].items():
            if key not in obj:
                return False
            if value is not None and _norm(obj[key]) != _norm(value):
                return False
        return True
    if kind == "tool":
        # Инструмент назван верно, если его имя есть, а имена соседей — нет.
        wanted = _norm(expect["name"])
        if wanted not in _norm(text):
            return False
        return not any(_norm(other) in _norm(text) for other in expect.get("not", []))
    raise RunnerError(f"unknown expectation kind: {kind!r}")


def ollama_client(endpoint: str, model: str, *, timeout: float = 120.0,
                  temperature: float = 0.0) -> Callable[[list[dict[str, str]]], str]:
    """Клиент к локальной модели. Температура 0: измерение обязано повторяться."""
    url = endpoint.rstrip("/") + "/api/chat"

    def call(messages: list[dict[str, str]]) -> str:
        body = json.dumps({"model": model, "messages": messages, "stream": False,
                           "options": {"temperature": temperature, "seed": 7}}).encode()
        request = urllib.request.Request(url, data=body,
                                         headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise RunnerError(f"local model call failed: {type(exc).__name__}: {exc}") from exc
        return str((payload.get("message") or {}).get("content") or "")

    return call


def load_tasks(path: Path) -> list[Task]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    tasks = [Task(task_id=t["task_id"], metric=t["metric"], prompt=t["prompt"],
                  expect=t["expect"], context=t.get("context", "")) for t in raw["tasks"]]
    seen = {t.task_id for t in tasks}
    if len(seen) != len(tasks):
        raise RunnerError("duplicate task_id in the task set")
    unknown = sorted({t.metric for t in tasks} - set(REQUIRED_METRICS))
    if unknown:
        raise RunnerError(f"task set names metrics the gate does not know: {unknown}")
    return tasks


def head_sha() -> str:
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                         text=True, check=False)
    sha = (out.stdout or "").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RunnerError("cannot resolve HEAD: retention is measured on a commit")
    return sha


def measure(tasks: list[Task], call: Callable[[list[dict[str, str]]], str], *,
            on_progress: Callable[[str, Task, bool], None] | None = None) -> dict[str, dict[str, LaneMetric]]:
    """Прогнать каждую задачу во ВСЕХ полосах и посчитать парное расхождение."""
    lanes: dict[str, dict[str, LaneMetric]] = {m: {} for m in MODES}
    for mode in MODES:
        for task in tasks:
            metric = lanes[mode].setdefault(task.metric, LaneMetric())
            try:
                answer = call(task.rendered(mode))
                ok = score(task, answer)
            except RunnerError:
                raise
            except Exception:
                # Модель не ответила или ответила негодным — это провал задачи,
                # а не повод её выбросить: выброшенная задача ломает парность.
                ok = False
            metric.samples += 1
            metric.passed += 1.0 if ok else 0.0
            metric.outcomes[task.task_id] = ok
            if on_progress is not None:
                on_progress(mode, task, ok)
    for mode in MODES:
        if mode == BASELINE:
            continue
        for name, metric in lanes[mode].items():
            base = lanes[BASELINE].get(name)
            if base is None:
                continue
            for task_id, ok in metric.outcomes.items():
                was = base.outcomes.get(task_id)
                if was is True and ok is False:
                    metric.lost += 1
                elif was is False and ok is True:
                    metric.gained += 1
    return lanes


def build_payload(lanes: dict[str, dict[str, LaneMetric]], *, model: str, dataset_id: str,
                  evaluated_sha: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"model": model, "dataset_id": dataset_id,
                               "evaluated_sha": evaluated_sha, "modes": {}}
    payload.update(extra or {})
    for mode in MODES:
        block: dict[str, Any] = {}
        for name in REQUIRED_METRICS:
            metric = lanes[mode].get(name, LaneMetric())
            if metric.samples == 0:
                # Метрика без задач — это дефект НАБОРА, а не результат модели.
                # Написать сюда ноль значило бы отдать гейту payload, который он
                # честно назовёт негодным («samples must be positive»), и
                # владелец пошёл бы чинить гейт вместо своего набора задач.
                raise RunnerError(
                    f"the task set covers no items for {name!r}, which the gate requires; "
                    "add tasks for it instead of publishing an unmeasured metric")
            rate = metric.passed / metric.samples
            if name in LOWER_IS_BETTER:
                rate = 1.0 - rate          # доля выдумок, а не доля успехов
            item: dict[str, Any] = {"score": round(rate, 6), "samples": metric.samples}
            if mode != BASELINE:
                item["paired"] = {"lost": metric.lost, "gained": metric.gained}
            block[name] = item
        payload["modes"][mode] = block
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", required=True, help="точный id локальной модели")
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434",
                        help="локальный сервер модели (Ollama)")
    parser.add_argument("--tasks", type=Path,
                        default=ROOT / "docs" / "benchmark" / "intelligence_tasks.json")
    parser.add_argument("--out", type=Path,
                        default=ROOT / "docs" / "benchmark" / "intelligence-preservation-current.json")
    parser.add_argument("--dataset-id", default=None,
                        help="по умолчанию берётся из набора задач")
    parser.add_argument("--sha", default=None, help="по умолчанию — текущий HEAD")
    parser.add_argument("--quantization", default="", help="фактическая квантизация модели")
    parser.add_argument("--hardware", default="", help="на чём измерено")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    tasks = load_tasks(args.tasks)
    per_metric: dict[str, int] = {}
    for task in tasks:
        per_metric[task.metric] = per_metric.get(task.metric, 0) + 1
    smallest = min(per_metric.values()) if per_metric else 0
    if smallest < SUFFICIENT_SAMPLES_PER_METRIC:
        # Предупреждение ДО прогона, а не разочарование после: прогон всё равно
        # полезен (это smoke по всем полосам), но вердиктом «PASS» он не станет.
        print(
            f"ВНИМАНИЕ: самая бедная метрика даёт {smallest} задач; для вердикта PASS "
            f"гейту нужно не меньше {SUFFICIENT_SAMPLES_PER_METRIC} на метрику даже при "
            "безупречном прогоне. Этот прогон измерит полосы честно, но ответом гейта "
            "будет INSUFFICIENT_EVIDENCE, а не PASS.", file=sys.stderr)
    dataset_id = args.dataset_id or json.loads(
        args.tasks.read_text(encoding="utf-8")).get("dataset_id") or args.tasks.stem
    sha = args.sha or head_sha()
    call = ollama_client(args.endpoint, args.model)

    done = {"n": 0}
    total = len(tasks) * len(MODES)

    def progress(mode: str, task: Task, ok: bool) -> None:
        done["n"] += 1
        if not args.quiet:
            print(f"[{done['n']:>4}/{total}] {mode:<7} {task.task_id:<28} "
                  f"{'ok' if ok else 'FAIL'}", file=sys.stderr)

    lanes = measure(tasks, call, on_progress=progress)
    extra = {"decoding": "greedy(temperature=0,seed=7)"}
    if args.quantization:
        extra["quantization"] = args.quantization
    if args.hardware:
        extra["hardware"] = args.hardware
    payload = build_payload(lanes, model=args.model, dataset_id=dataset_id,
                            evaluated_sha=sha, extra=extra)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(f"written: {args.out}", file=sys.stderr)
    print(f"tasks={len(tasks)} per lane x {len(MODES)} lanes; smallest metric has "
          f"{smallest} items (PASS needs {SUFFICIENT_SAMPLES_PER_METRIC})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
