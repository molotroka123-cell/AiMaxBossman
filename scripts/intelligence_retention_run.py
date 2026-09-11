#!/usr/bin/env python3
"""Замер удержания интеллекта: ОДНА модель, ОДИН набор задач, четыре слоя.

Гейт `tools/intelligence_preservation_gate.py` не пускает релиз без файла
`docs/benchmark/intelligence-preservation-current.json`. Инструмента, который
этот файл ПРОИЗВОДИТ, в проекте не было — гейт годами отвечал
INSUFFICIENT_EVIDENCE не потому, что интеллект деградировал, а потому, что
замер было нечем сделать. Этот раннер закрывает дыру.

Что он делает:

    задача → lane raw     (только модель)
           → lane system  (+ системная политика Bossman)
           → lane context (+ компилятор контекста/retrieval)
           → lane full    (+ полный инструментальный контур)

Каждый предмет проходит ВСЕ четыре лейна, поэтому сравнение парное, и
раннер считает расхождение с базовым лейном (`paired: lost/gained`) —
именно оно позволяет гейту сузить доверительный интервал.

Чего он НЕ делает и делать не должен:

* не выдумывает недостающие лейны. Если системная политика или контекст
  недоступны, лейны получились бы побайтово одинаковыми, а одинаковые лейны
  «доказали» бы 100% удержания ничего не измерив. Такой прогон прерывается
  с FAKE_LANES;
* не пишет полезную нагрузку, если хоть один предмет хоть в одном лейне не
  выполнился: частичный замер — это не замер;
* не судит ответы другой моделью. Оценка детерминированная и объявлена в
  самом предмете, иначе результат нельзя перепроверить;
* не считает, что достаточного числа предметов достаточно: порог решает гейт.

    python scripts/intelligence_retention_run.py \
        --items docs/benchmark/retention-items-v1.json \
        --model <имя модели> --base-url http://127.0.0.1:8080/v1 \
        --out docs/benchmark/intelligence-preservation-current.json

Коды выхода: 0 — нагрузка записана, 2 — замер невозможен (названа причина).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
LANES = ("raw", "system", "context", "full")
CORE = ("reasoning_accuracy", "coding_correctness", "structured_output_accuracy",
        "unknown_task_adaptation")
TOOL = ("tool_selection_accuracy", "schema_argument_accuracy")
SECONDARY = ("long_context_accuracy", "memory_retrieval_accuracy",
             "computer_use_planning_accuracy", "task_completion_rate")
LOWER_IS_BETTER = ("hallucination_rate",)
METRICS = CORE + TOOL + SECONDARY + LOWER_IS_BETTER


class Unmeasurable(RuntimeError):
    """Замер невозможен. Это честный ответ, а не повод что-нибудь придумать."""


def _console_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def source_sha() -> str:
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                          capture_output=True, text=True, timeout=60)
    dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"],
                           capture_output=True, text=True, timeout=60)
    if head.returncode or not re.fullmatch(r"[0-9a-f]{40}", head.stdout.strip()):
        raise Unmeasurable("не удалось определить коммит замера")
    if dirty.stdout.strip():
        raise Unmeasurable("дерево грязное: удержание меряется на коммите, а не на черновике")
    return head.stdout.strip()


# ------------------------------------------------------------------ слои

def lane_layers(lane: str) -> list[str]:
    """Какие слои Bossman включены в лейне. Порядок — как в документе гейта."""
    return {"raw": [], "system": ["system"], "context": ["system", "context"],
            "full": ["system", "context", "tools"]}[lane]


def build_envelope(item: dict, lane: str, layers: dict[str, str]) -> dict:
    """Запрос к модели для конкретного лейна.

    Слои берутся из продукта, а не сочиняются здесь: `layers` приходит из
    `--layer system=<файл>` и т.п., поэтому замер отражает то, что Bossman
    действительно кладёт в запрос.
    """
    messages: list[dict[str, str]] = []
    applied = lane_layers(lane)
    if "system" in applied:
        messages.append({"role": "system", "content": layers["system"]})
    if "context" in applied:
        messages.append({"role": "system", "content": layers["context"]})
    user = item["prompt"]
    if "tools" in applied:
        user = layers["tools"] + "\n\n" + user
    messages.append({"role": "user", "content": user})
    return {"messages": messages}


def envelope_digest(envelope: dict) -> str:
    return hashlib.sha256(json.dumps(envelope, sort_keys=True,
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def refuse_identical_lanes(items: list[dict], layers: dict[str, str]) -> None:
    """Одинаковые лейны — это не замер, а самообман."""
    texts = [layers["system"].strip(), layers["context"].strip(), layers["tools"].strip()]
    if any(not t for t in texts) or len(set(texts)) != len(texts):
        raise Unmeasurable(
            "FAKE_LANES: слои system/context/tools пусты или совпадают между собой. "
            "Лейны должны отличаться тем, что Bossman реально добавляет в запрос.")
    probe = items[0]
    digests = {lane: envelope_digest(build_envelope(probe, lane, layers)) for lane in LANES}
    if len(set(digests.values())) != len(LANES):
        same = [lane for lane in LANES if list(digests.values()).count(digests[lane]) > 1]
        raise Unmeasurable(
            "FAKE_LANES: конверты лейнов совпадают (" + ", ".join(same) + "). "
            "Одинаковые лейны показали бы 100% удержания, ничего не измерив. "
            "Передайте настоящие слои через --layer system=... --layer context=... "
            "--layer tools=...")


# ------------------------------------------------------------------ модель

def ask(base_url: str, model: str, api_key: str | None, envelope: dict,
        timeout: int) -> str:
    body = json.dumps({"model": model, "messages": envelope["messages"],
                       "temperature": 0, "stream": False}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(base_url.rstrip("/") + "/chat/completions",
                                     data=body, headers=headers, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise Unmeasurable(f"модель недоступна по {base_url}: {exc}") from exc
    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise Unmeasurable(f"ответ модели не разобран: {str(payload)[:300]}") from exc


# ------------------------------------------------------------------ оценка

def grade(item: dict, answer: str) -> bool:
    """Детерминированная проверка, объявленная в самом предмете."""
    check = item["check"]
    kind = check["kind"]
    text = answer.strip()
    if kind == "exact":
        return text == check["value"]
    if kind == "contains":
        return all(part.lower() in text.lower() for part in check["value"])
    if kind == "absent":
        return all(part.lower() not in text.lower() for part in check["value"])
    if kind == "regex":
        return re.search(check["value"], text, re.S) is not None
    if kind == "json_keys":
        try:
            parsed = json.loads(text[text.index("{"):text.rindex("}") + 1])
        except (ValueError, json.JSONDecodeError):
            return False
        return all(key in parsed for key in check["value"])
    raise Unmeasurable(f"неизвестный вид проверки: {kind}")


def load_items(path: Path) -> tuple[str, list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise Unmeasurable("набор предметов пуст")
    for item in items:
        if item.get("metric") not in METRICS:
            raise Unmeasurable(f"предмет {item.get('id')} объявляет неизвестную метрику")
        for field in ("id", "prompt", "check"):
            if not item.get(field):
                raise Unmeasurable(f"предмет без поля {field}")
    return data.get("dataset_id") or path.stem, items


def measure(items: list[dict], layers: dict[str, str], *, base_url: str, model: str,
            api_key: str | None, timeout: int) -> dict[str, dict[str, list[bool]]]:
    """Каждый предмет во всех четырёх лейнах. Любой сбой прерывает замер."""
    outcomes: dict[str, dict[str, list[bool]]] = {lane: {} for lane in LANES}
    for index, item in enumerate(items, 1):
        for lane in LANES:
            answer = ask(base_url, model, api_key,
                         build_envelope(item, lane, layers), timeout)
            correct = grade(item, answer)
            outcomes[lane].setdefault(item["metric"], []).append(correct)
        print(f"  {index}/{len(items)} {item['id']}", flush=True)
    return outcomes


def to_payload(outcomes, *, model: str, dataset_id: str, sha: str) -> dict:
    modes: dict[str, dict] = {}
    for lane in LANES:
        block: dict[str, Any] = {}
        for metric in METRICS:
            results = outcomes[lane].get(metric)
            if not results:
                raise Unmeasurable(
                    f"лейн {lane} не дал ни одного замера метрики {metric}: "
                    "частичный прогон не является доказательством")
            hits = sum(1 for ok in results if ok)
            score = hits / len(results)
            if metric in LOWER_IS_BETTER:
                score = 1.0 - score
            entry: dict[str, Any] = {"score": score, "samples": len(results)}
            if lane != "raw":
                baseline = outcomes["raw"].get(metric) or []
                if len(baseline) == len(results):
                    entry["paired"] = {
                        "lost": sum(1 for b, c in zip(baseline, results) if b and not c),
                        "gained": sum(1 for b, c in zip(baseline, results) if c and not b),
                    }
            block[metric] = entry
        modes[lane] = block
    return {"model": model, "dataset_id": dataset_id, "evaluated_sha": sha, "modes": modes}


def main(argv: list[str] | None = None) -> int:
    _console_utf8()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--items", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", required=True,
                        help="OpenAI-совместимый эндпоинт: LocalAI, llama.cpp, vLLM")
    parser.add_argument("--api-key-env", default="BOSSMAN_BENCH_API_KEY")
    parser.add_argument("--layer", action="append", default=[],
                        metavar="ИМЯ=ФАЙЛ",
                        help="слой лейна: system=..., context=..., tools=...")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--audit", type=Path, default=None,
                        help="куда положить пер-предметный след замера")
    args = parser.parse_args(argv)

    try:
        layers: dict[str, str] = {}
        for spec in args.layer:
            name, _, path = spec.partition("=")
            if name not in ("system", "context", "tools") or not path:
                raise Unmeasurable(f"слой объявлен неверно: {spec}")
            layers[name] = Path(path).read_text(encoding="utf-8")
        missing = [n for n in ("system", "context", "tools") if n not in layers]
        if missing:
            raise Unmeasurable(
                "нет слоёв " + ", ".join(missing) + ": без них лейны совпадут, "
                "а совпавшие лейны показали бы удержание, ничего не измерив")

        sha = source_sha()
        dataset_id, items = load_items(args.items)
        refuse_identical_lanes(items, layers)
        print(f"замер: модель {args.model}, предметов {len(items)}, коммит {sha[:12]}",
              flush=True)
        outcomes = measure(items, layers, base_url=args.base_url, model=args.model,
                           api_key=os.environ.get(args.api_key_env), timeout=args.timeout)
        payload = to_payload(outcomes, model=args.model, dataset_id=dataset_id, sha=sha)
    except Unmeasurable as exc:
        print(f"INTELLIGENCE_RETENTION=UNMEASURABLE: {exc}", file=sys.stderr)
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    if args.audit:
        args.audit.write_text(json.dumps(
            {lane: {m: outcomes[lane][m] for m in outcomes[lane]} for lane in LANES},
            indent=2) + "\n", encoding="utf-8")
    print(f"INTELLIGENCE_RETENTION=MEASURED → {args.out}")
    print("теперь прогоните гейт: python tools/intelligence_preservation_gate.py "
          f"{args.out} --expect-sha {payload['evaluated_sha']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
