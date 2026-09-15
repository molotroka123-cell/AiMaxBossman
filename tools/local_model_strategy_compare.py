#!/usr/bin/env python3
"""Сравнить СТРАТЕГИИ локальных моделей на числах, а не на убеждениях.

Раздел 26 задания просит подготовить Bossman к двум стратегиям сразу:

    A — одна сильная большая локальная модель как основной мозг;
    B — несколько маленьких специализированных: маршрутизация, код, зрение,
        сжатие, исполнение инструментов, проверка.

и прямо запрещает зашивать философский ответ: «Do not hard-code the
philosophical answer. Measure it on the actual machine.»

ЧТО ЭТОТ ИНСТРУМЕНТ ДЕЛАЕТ. Он ничего не измеряет сам — измеряет
`tools/intelligence_preservation_run.py`, по одному прогону на конфигурацию.
Здесь происходит то, что нельзя доверить глазам: проверка, что два прогона
ВООБЩЕ СРАВНИМЫ, и честный вывод о том, различимы ли они.

ПОЧЕМУ ЭТО ОТДЕЛЬНЫЙ ИНСТРУМЕНТ, А НЕ ТАБЛИЦА В ОТЧЁТЕ. Две точечные оценки
на двадцати предметах отличаются почти всегда, и почти всегда — шумом.
Человек, глядя на «0.85 против 0.80», говорит «A лучше». Доверительный
интервал говорит «при таком числе предметов эти два числа неразличимы».
Первое решение купит владельцу неверную архитектуру; второе скажет, что надо
собрать больше предметов.

ЧЕГО ОН НЕ ДЕЛАЕТ:

  * не сравнивает прогоны на РАЗНЫХ наборах задач и на РАЗНЫХ SHA. Это не
    сравнение стратегий, а сравнение двух разных экспериментов, и оно
    отвергается, а не помечается звёздочкой;
  * не принимает полосу FULL, которая не исполняла инструменты — те же
    правила, что у релизного гейта (AF-04). Стратегию оценивают по обвязке,
    работающей по-настоящему;
  * не выдумывает пропущенных величин. Скорость и память печатаются, только
    если прогон их принёс; иначе в клетке стоит «не измерено», а не ноль;
  * не объявляет победителя, когда интервалы пересекаются.

Запуск:

    python tools/local_model_strategy_compare.py \\
        A=artifacts/one-large-model.json \\
        B=artifacts/small-model-team.json \\
        --out artifacts/strategy-comparison.json

Коды выхода: 0 — сравнение выполнено, 2 — сравнивать нельзя (названа причина).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.intelligence_preservation_gate import (  # noqa: E402
    CORE_METRICS,
    LOWER_IS_BETTER,
    MODES,
    REQUIRED_METRICS,
    TOOL_METRICS,
    wilson,
)

#: Доверие по умолчанию — то же, что у релизного гейта. Одно число на весь
#: проект: две разные уверенности в двух местах означают, что «значимо» имеет
#: два смысла, и никто не помнит какой где.
Z_95 = 1.959963984540054

#: Поля, которые прогон МОЖЕТ принести, и которые нужны разделу 26. Их
#: отсутствие — не ошибка: не всякий стенд умеет их снять. Их ВЫДУМЫВАНИЕ —
#: ошибка, поэтому список закрыт и читается только отсюда.
THROUGHPUT_FIELDS = (
    ("decode_tokens_per_second", "декодирование, токенов/с"),
    ("time_to_first_token_ms", "время до первого токена, мс"),
    ("wall_seconds_total", "полное время прогона, с"),
    ("peak_resident_memory_mib", "пиковая занятая память, МиБ"),
    ("unified_memory_mib", "занятая единая память, МиБ"),
    ("concurrent_agents", "параллельных агентов"),
    ("energy_wh", "энергия, Вт·ч"),
)

NOT_MEASURED = "не измерено"


class Incomparable(ValueError):
    """Прогоны нельзя поставить рядом. Причина всегда называется."""


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Incomparable(f"{path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise Incomparable(f"{path}: полезная нагрузка должна быть объектом")
    return dict(payload)


def _metric(payload: Mapping[str, Any], lane: str, name: str) -> tuple[float, int]:
    block = payload["modes"][lane][name]
    return float(block["score"]), int(block["samples"])


def require_comparable(runs: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Отвергнуть всё, что сравнением не является, и назвать причину.

    Порядок проверок не случаен: сначала то, что делает эксперименты разными
    (набор задач, коммит), потом то, что делает полосу ненастоящей, и только
    потом арифметика. Так сообщение об ошибке называет НАСТОЯЩУЮ причину, а не
    её следствие.
    """
    if len(runs) < 2:
        raise Incomparable("для сравнения нужно не меньше двух прогонов")

    datasets = {name: str(run.get("dataset_id") or "") for name, run in runs.items()}
    if "" in datasets.values():
        missing = [n for n, d in datasets.items() if not d]
        raise Incomparable(f"нет dataset_id у прогонов: {', '.join(missing)}")
    if len(set(datasets.values())) != 1:
        raise Incomparable(
            "наборы задач различаются — это сравнение двух разных экспериментов, "
            f"а не двух стратегий: {datasets}")

    shas = {name: str(run.get("evaluated_sha") or "") for name, run in runs.items()}
    if len(set(shas.values())) != 1:
        raise Incomparable(
            "прогоны сняты на РАЗНЫХ коммитах: обвязка у них разная, и разница "
            f"в числах не принадлежит стратегии: {shas}")

    models = {name: str(run.get("model") or "") for name, run in runs.items()}
    if len(set(models.values())) == 1:
        raise Incomparable(
            "во всех прогонах одна и та же модель: сравнивать стратегии нечем. "
            "Раздел 26 просит сравнить ОДНУ большую против КОМАНДЫ маленьких")

    for name, run in runs.items():
        modes = run.get("modes")
        if not isinstance(modes, Mapping) or any(m not in modes for m in MODES):
            raise Incomparable(f"{name}: нет всех четырёх полос {MODES}")
        lanes = run.get("lanes")
        full = lanes.get("full") if isinstance(lanes, Mapping) else None
        if not isinstance(full, Mapping) or full.get("executes_tools") is not True:
            raise Incomparable(
                f"{name}: полоса FULL не исполняла инструменты. Стратегию оценивают "
                "по обвязке, работающей по-настоящему, а не по дописанному к "
                "промпту списку имён инструментов (AF-04)")
        for metric in REQUIRED_METRICS:
            for lane in MODES:
                block = modes[lane].get(metric)
                if not isinstance(block, Mapping) or not isinstance(block.get("samples"), int):
                    raise Incomparable(f"{name}: полоса {lane} не несёт метрику {metric}")

    for metric in REQUIRED_METRICS:
        counts = {name: _metric(run, "full", metric)[1] for name, run in runs.items()}
        if len(set(counts.values())) != 1:
            raise Incomparable(
                f"у метрики {metric} разное число предметов между прогонами {counts}: "
                "сравнивать долю от разного знаменателя нельзя")

    return {"dataset_id": next(iter(datasets.values())),
            "evaluated_sha": next(iter(shas.values())),
            "models": models}


def _interval(score: float, samples: int, *, lower_is_better: bool) -> tuple[float, float]:
    lo, hi = wilson(score * samples, samples, Z_95)
    return (1.0 - hi, 1.0 - lo) if lower_is_better else (lo, hi)


def compare_metric(runs: Mapping[str, Mapping[str, Any]], metric: str,
                   *, lane: str = "full") -> dict[str, Any]:
    """Сравнить одну метрику и честно сказать, различимы ли стратегии."""
    lower_is_better = metric in LOWER_IS_BETTER
    rows: dict[str, dict[str, Any]] = {}
    for name, run in runs.items():
        score, samples = _metric(run, lane, metric)
        lo, hi = _interval(score, samples, lower_is_better=lower_is_better)
        # Для метрики «чем меньше, тем лучше» сравнивается доля УСПЕХА,
        # то есть 1 - score. Иначе «лучше» означало бы разное в разных строках
        # одной таблицы.
        rows[name] = {"score": round(score, 6), "samples": samples,
                      "effective": round(1.0 - score if lower_is_better else score, 6),
                      "ci95": [round(lo, 6), round(hi, 6)]}

    ordered = sorted(rows.items(), key=lambda kv: -kv[1]["effective"])
    best_name, best = ordered[0]
    runner_name, runner = ordered[1]
    # Пересекающиеся интервалы — это «столько предметов не хватает, чтобы
    # различить», а не «одинаково». Разница названа по имени.
    distinguishable = best["ci95"][0] > runner["ci95"][1]
    return {
        "metric": metric,
        "lane": lane,
        "lower_is_better": lower_is_better,
        "runs": rows,
        "leader": best_name if distinguishable else None,
        "verdict": ("DISTINGUISHABLE" if distinguishable else "INDISTINGUISHABLE"),
        "note": (f"{best_name} выше {runner_name} за пределами доверительных интервалов"
                 if distinguishable else
                 f"интервалы {best_name} и {runner_name} пересекаются: при {best['samples']} "
                 "предметах эти числа неразличимы — нужны предметы, а не вывод"),
    }


def throughput_table(runs: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Скорость, память и стоимость — только то, что прогоны реально принесли."""
    table: dict[str, dict[str, Any]] = {}
    for field, title in THROUGHPUT_FIELDS:
        row: dict[str, Any] = {}
        for name, run in runs.items():
            value = run.get(field)
            if value is None and isinstance(run.get("hardware"), Mapping):
                value = run["hardware"].get(field)
            row[name] = value if isinstance(value, (int, float)) else NOT_MEASURED
        table[field] = {"title": title, "values": row}
    return table


def build_report(runs: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    identity = require_comparable(runs)
    quality = [compare_metric(runs, m) for m in CORE_METRICS]
    tools = [compare_metric(runs, m) for m in TOOL_METRICS]
    honesty = [compare_metric(runs, "hallucination_rate")]
    others = [compare_metric(runs, m) for m in REQUIRED_METRICS
              if m not in CORE_METRICS + TOOL_METRICS + LOWER_IS_BETTER]

    decided = [c for c in quality + tools + honesty if c["verdict"] == "DISTINGUISHABLE"]
    leaders = {c["leader"] for c in decided}
    if not decided:
        overall, why = "INDISTINGUISHABLE", (
            "ни одна метрика качества, инструментов или честности не различила стратегии "
            "на этом числе предметов. Это результат, а не отсутствие результата: он "
            "говорит, что выбирать надо по стоимости и памяти, а не по качеству")
    elif len(leaders) == 1:
        winner = next(iter(leaders))
        overall, why = f"LEADER:{winner}", (
            f"{winner} ведёт по {len(decided)} различимым метрикам и не проигрывает "
            "ни одной")
    else:
        overall, why = "SPLIT", (
            "стратегии ведут по РАЗНЫМ метрикам: " +
            "; ".join(f"{c['metric']} → {c['leader']}" for c in decided) +
            ". Общего победителя нет, и объявлять его значило бы выбрать метрику "
            "задним числом")

    return {
        "tool": "bossman.local_model_strategy_compare",
        "schema": 1,
        "identity": identity,
        "verdict": overall,
        "why": why,
        "quality": quality,
        "tool_calling": tools,
        "honesty": honesty,
        "secondary": others,
        "throughput_and_cost": throughput_table(runs),
        "caveat": ("Сравнение действительно только для этой машины, этого набора задач и "
                   "этого коммита. Перенос вывода на другое железо требует нового прогона: "
                   "распределение памяти и скорость декодирования — свойства машины."),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="+", metavar="ИМЯ=ФАЙЛ",
                        help="прогон на стратегию, например A=one-large.json")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    loaded: dict[str, Any] = {}
    try:
        for item in args.runs:
            if "=" not in item:
                raise Incomparable(f"ожидалось ИМЯ=ФАЙЛ, получено {item!r}")
            name, _, path = item.partition("=")
            if name in loaded:
                raise Incomparable(f"имя стратегии {name!r} встречается дважды")
            loaded[name] = _load(Path(path))
        report = build_report(loaded)
    except Incomparable as exc:
        out = {"tool": "bossman.local_model_strategy_compare",
               "verdict": "INCOMPARABLE", "reason": str(exc)}
        text = json.dumps(out, ensure_ascii=False, indent=2)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text + "\n", encoding="utf-8")
        print(text)
        print("\nSTRATEGY_COMPARISON=INCOMPARABLE")
        return 2

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    print(f"\nSTRATEGY_COMPARISON={report['verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
