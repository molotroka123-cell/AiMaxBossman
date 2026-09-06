#!/usr/bin/env python3
"""Epoch 4: сбор ПОДЛИННЫХ выборок и парное сравнение baseline/candidate.

Чего здесь нет намеренно: генератора данных. `bossman_shared.epoch4_metrics`
умеет считать арифметику парного протокола, но до сих пор её было НЕЧЕМ кормить,
кроме синтетики из тестов. Синтетика, названная измерением, — это не быстрый
путь к цифре, это ложная цифра. Поэтому единственный источник здесь — корпус
реальных нагрузок, который пишется автоматически на терминальной границе
исполнения (`bossman_v3.execution.telemetry`).

    # на базовой версии
    python scripts/epoch4_performance.py collect --label baseline  --out perf/baseline.json
    # переключились на кандидата, прогнали ТУ ЖЕ работу
    python scripts/epoch4_performance.py collect --label candidate --out perf/candidate.json
    python scripts/epoch4_performance.py compare --baseline perf/baseline.json \
                                                 --candidate perf/candidate.json

Если подлинных выборок не хватает — а сегодня их почти наверняка не хватает —
ответ PERFORMANCE_VERDICT=INSUFFICIENT_EVIDENCE. Это правильный ответ, а не
недоработка инструмента: протокол требует >=100 пар и >=30 наблюдений в каждом
семействе, и обойти это можно только враньём.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "bossman-core")]

COLLECTION_SCHEMA = 1
COLLECTION_TYPE = "bossman.epoch4_collection"
MIN_PAIRS = 100
MIN_PER_FAMILY = 30


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True,
                              text=True, timeout=15).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _source_identity() -> tuple[str, bool]:
    """Точный SHA и честный признак грязного дерева. Оценщик отвергает и то,
    и другое, если соврать, — и правильно делает."""
    sha = _git("rev-parse", "HEAD")
    dirty = bool(_git("status", "--porcelain"))
    return sha, dirty


def _corpus_records(path: Path | None) -> tuple[list[dict[str, Any]], Path]:
    from bossman_v3.execution import telemetry as tm
    target = Path(path) if path else tm.corpus_root() / tm.CORPUS_NAME
    if not target.exists():
        return [], target
    rows: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("record_type") == tm.RECORD_TYPE:
            rows.append(item)
    return rows, target


def _usable(record: dict[str, Any]) -> tuple[bool, str]:
    """Пригодна ли выборка для ПАРНОГО протокола. Отбраковка объясняется, а не
    прячется: исключённая молча выборка — это подмена выборки."""
    if not str(record.get("pair_id") or "").strip():
        return False, "нет pair_id (план не был привязан) — пару не с чем сопоставить"
    if not isinstance(record.get("verified"), bool):
        return False, "нет явного verified"
    elapsed = record.get("duration_s")
    if not isinstance(elapsed, (int, float)) or not elapsed > 0:
        return False, "длительность не положительная — измерения не было"
    if not str(record.get("evidence_ref") or "").strip():
        return False, "нет ссылки на улику"
    if not str(record.get("workload_family") or "").strip():
        return False, "нет семейства нагрузки"
    return True, ""


def cmd_collect(args: argparse.Namespace) -> int:
    records, corpus = _corpus_records(args.corpus)
    sha, dirty = _source_identity()
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    seen: set[str] = set()
    for record in records:
        ok, why = _usable(record)
        if not ok:
            rejected.append({"task_id": record.get("task_id", ""), "reason": why})
            continue
        pair = str(record["pair_id"])
        if pair in seen:
            # Парный протокол требует РОВНО одно наблюдение на пару.
            rejected.append({"task_id": record.get("task_id", ""),
                             "reason": f"повторная выборка для пары {pair}"})
            continue
        seen.add(pair)
        kept.append(record)

    collection = {
        "schema_version": COLLECTION_SCHEMA, "record_type": COLLECTION_TYPE,
        "label": args.label, "collected_at": time.time(),
        "commit_sha": sha, "dirty_tree": dirty,
        "corpus": str(corpus), "configuration": _configuration(args),
        "records": kept, "rejected": rejected,
        "families": sorted({str(r["workload_family"]) for r in kept}),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(collection, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(f"label={args.label}  sha={sha or 'НЕИЗВЕСТЕН'}  dirty={dirty}")
    print(f"пригодных выборок: {len(kept)}   отброшено: {len(rejected)}")
    for family in collection["families"]:
        print(f"  {family}: {sum(1 for r in kept if r['workload_family'] == family)}")
    if rejected:
        print("\nотброшено (причины):")
        for item in rejected[:10]:
            print(f"  {item['task_id'] or '—'}: {item['reason']}")
        if len(rejected) > 10:
            print(f"  … ещё {len(rejected) - 10}")
    if dirty:
        print("\nДЕРЕВО ГРЯЗНОЕ: оценщик отвергнет такую коллекцию. Закоммитьте или "
              "уберите изменения и соберите заново.")
    print(f"\nзаписано: {args.out}")
    return 0


def _configuration(args: argparse.Namespace) -> dict[str, str]:
    """Замороженная конфигурация. Она обязана СОВПАДАТЬ у baseline и candidate —
    иначе сравниваются не версии, а машины."""
    import platform
    return {
        "hardware": args.hardware or f"{platform.machine()}/{__import__('os').cpu_count()}cpu",
        "platform": args.platform or platform.platform(),
        "models": args.models or "UNSPECIFIED",
        "permissions": args.permissions or "UNSPECIFIED",
        "resource_envelope": args.resource_envelope or "UNSPECIFIED",
        "workload": args.workload or "real-owner-tasks",
        "execution_mode": "serial",
    }


def _to_measurements(collection: dict[str, Any], manifest: dict[str, str]):
    from bossman_shared.epoch4_metrics import Dataset, Measurement
    rows = []
    for record in collection["records"]:
        pair = str(record["pair_id"])
        if pair not in manifest:
            continue
        cost = record.get("cost_usd")
        rows.append(Measurement(
            pair_id=pair, family=str(record["workload_family"]),
            elapsed_seconds=float(record["duration_s"]),
            # None означает «стоимость не измерена». Оценщик на этом остановится,
            # и это лучше, чем подставить ноль и получить «бесплатно».
            cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
            verified_result=bool(record["verified"]),
            avoidable_interventions=int(record.get("human_interventions", 0)),
            mandatory_approvals=int(record.get("mandatory_approvals", 0)),
            unsafe_events=int(record.get("unsafe_events", 0)),
            evidence_ref=str(record["evidence_ref"])))
    return Dataset(commit_sha=str(collection.get("commit_sha") or ""),
                   dirty_tree=bool(collection.get("dirty_tree", True)),
                   configuration=tuple(sorted(dict(collection["configuration"]).items())),
                   records=tuple(rows))


def build_manifest(baseline: dict[str, Any], candidate: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    """Предрегистрированный манифест пар: только те нагрузки, что наблюдались в
    ОБЕИХ версиях, и только с одинаковым семейством."""
    notes: list[str] = []
    base = {str(r["pair_id"]): str(r["workload_family"]) for r in baseline["records"]}
    cand = {str(r["pair_id"]): str(r["workload_family"]) for r in candidate["records"]}
    manifest: dict[str, str] = {}
    for pair, family in base.items():
        if pair not in cand:
            continue
        if cand[pair] != family:
            notes.append(f"пара {pair}: семейство различается ({family} / {cand[pair]}) — исключена")
            continue
        manifest[pair] = family
    only_base = sorted(set(base) - set(cand))
    only_cand = sorted(set(cand) - set(base))
    if only_base:
        notes.append(f"{len(only_base)} нагрузок есть только в baseline — пары нет")
    if only_cand:
        notes.append(f"{len(only_cand)} нагрузок есть только в candidate — пары нет")
    return manifest, notes


def cmd_compare(args: argparse.Namespace) -> int:
    from bossman_shared.epoch4_metrics import evaluate
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    manifest, notes = build_manifest(baseline, candidate)
    from collections import Counter
    counts = Counter(manifest.values())

    blockers: list[str] = []
    if len(manifest) < MIN_PAIRS:
        blockers.append(f"пар {len(manifest)}, требуется >= {MIN_PAIRS}")
    thin = {f: n for f, n in counts.items() if n < MIN_PER_FAMILY}
    if thin:
        blockers.append("семейства ниже порога "
                        f"{MIN_PER_FAMILY}: " + ", ".join(f"{f}={n}" for f, n in sorted(thin.items())))
    missing_cost = sum(1 for c in (baseline, candidate)
                       for r in c["records"] if r.get("pair_id") in manifest
                       and not isinstance(r.get("cost_usd"), (int, float)))
    if missing_cost:
        blockers.append(f"{missing_cost} выборок без ИЗМЕРЕННОЙ стоимости "
                        "(локальный ресурсный вектор ещё не адаптирован)")

    report: dict[str, Any] = {
        "schema_version": 1, "pairs": len(manifest), "families": dict(counts),
        "notes": notes, "blockers": blockers,
        "baseline_sha": baseline.get("commit_sha"), "candidate_sha": candidate.get("commit_sha"),
    }
    if blockers:
        report["performance_verdict"] = "INSUFFICIENT_EVIDENCE"
        report["evaluation"] = None
    else:
        report["evaluation"] = evaluate(manifest,
                                        _to_measurements(baseline, manifest),
                                        _to_measurements(candidate, manifest),
                                        bootstrap_samples=args.bootstrap, seed=args.seed)
        report["performance_verdict"] = report["evaluation"]["verdict"]

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                 encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False) if args.json
          else _render(report))
    return 0 if report["performance_verdict"] == "MET" else 1


def _render(report: dict[str, Any]) -> str:
    lines = ["", "EPOCH 4 — ПАРНОЕ СРАВНЕНИЕ ПРОИЗВОДИТЕЛЬНОСТИ", "=" * 62,
             f"baseline  {report['baseline_sha'] or 'НЕИЗВЕСТЕН'}",
             f"candidate {report['candidate_sha'] or 'НЕИЗВЕСТЕН'}",
             f"пар: {report['pairs']}", ""]
    for family, n in sorted(report["families"].items()):
        lines.append(f"  {family}: {n}")
    if report["notes"]:
        lines += ["", "замечания:"] + [f"  - {n}" for n in report["notes"]]
    if report["blockers"]:
        lines += ["", "недостаёт для вывода:"] + [f"  - {b}" for b in report["blockers"]]
    lines += ["", "=" * 62, f"PERFORMANCE_VERDICT = {report['performance_verdict']}"]
    evaluation = report.get("evaluation")
    if evaluation:
        lines.append(f"certified={evaluation['certified']}  "
                     f"source_trust={evaluation['source_trust']}")
        for gate, ok in (evaluation.get("gates") or {}).items():
            lines.append(f"  {'OK  ' if ok else 'FAIL'} {gate}")
        for reason in evaluation.get("reasons", []):
            lines.append(f"  причина: {reason}")
    else:
        lines.append("Это правильный ответ при нехватке подлинных выборок, а не "
                     "недоработка: протокол не обходится синтетикой.")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="epoch4_performance", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")

    collect = sub.add_parser("collect", help="снять коллекцию из корпуса реальных нагрузок")
    collect.add_argument("--label", choices=("baseline", "candidate"), required=True)
    collect.add_argument("--out", type=Path, required=True)
    collect.add_argument("--corpus", type=Path, default=None)
    for name in ("hardware", "platform", "models", "permissions", "resource-envelope", "workload"):
        collect.add_argument(f"--{name}", dest=name.replace("-", "_"), default="")
    collect.set_defaults(func=cmd_collect)

    compare = sub.add_parser("compare", help="сравнить две коллекции по парному протоколу")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    compare.add_argument("--bootstrap", type=int, default=1000)
    compare.add_argument("--seed", type=int, default=0)
    compare.add_argument("--json", action="store_true")
    compare.add_argument("--json-out", type=Path, default=None)
    compare.set_defaults(func=cmd_compare)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
