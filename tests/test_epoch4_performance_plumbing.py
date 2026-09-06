"""Ингест подлинных выборок в парный протокол Epoch 4.

Оценщик `bossman_shared.epoch4_metrics` существовал давно, но кормили его
только синтетикой из тестов. Синтетика, названная измерением, — это не быстрый
путь к цифре, а ложная цифра. Здесь проверяется противоположное свойство:
инструмент отказывается выдать вердикт, пока подлинных парных наблюдений
недостаточно, и говорит, чего именно не хватает.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def perf():
    return _load("epoch4_performance")


def _sample(pair: str, family: str, *, verified=True, duration=1.5, cost=0.01,
            interventions=0, task=None) -> dict:
    task = task or f"task-{pair}"
    return {"record_type": "bossman.real_workload_sample", "schema_version": 1,
            "task_id": task, "pair_id": pair, "workload_family": family,
            "duration_s": duration, "verified": verified,
            "status": "passed" if verified else "failed",
            "human_interventions": interventions, "mandatory_approvals": 0,
            "unsafe_events": 0, "cost_usd": cost, "plan_digest": pair,
            "evidence_ref": f"task_journal:{task}@{pair}"}


def _collection(perf, tmp_path, label, records, sha, *, dirty=False) -> Path:
    body = {"schema_version": 1, "record_type": "bossman.epoch4_collection", "label": label,
            "commit_sha": sha, "dirty_tree": dirty,
            "configuration": {"hardware": "h", "platform": "p", "models": "m",
                              "permissions": "perm", "resource_envelope": "env",
                              "workload": "real-owner-tasks", "execution_mode": "serial"},
            "records": records, "rejected": [],
            "families": sorted({r["workload_family"] for r in records})}
    path = tmp_path / f"{label}.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


# ------------------------------------------------------------------- сбор

def test_collection_reads_only_the_real_corpus(perf, tmp_path):
    corpus = tmp_path / "real_workloads.jsonl"
    corpus.write_text("\n".join(json.dumps(x) for x in [
        _sample("p1", "demo"),
        {"record_type": "something.else", "task_id": "not-ours"},   # чужая строка
        "not a dict",
    ][:2]) + '\n"not a dict"\n', encoding="utf-8")
    rows, path = perf._corpus_records(corpus)
    assert [r["task_id"] for r in rows] == ["task-p1"]
    assert path == corpus


def test_an_unpairable_sample_is_rejected_with_a_stated_reason(perf):
    ok, why = perf._usable({**_sample("p1", "demo"), "pair_id": ""})
    assert not ok and "pair_id" in why
    ok, why = perf._usable({**_sample("p1", "demo"), "duration_s": 0})
    assert not ok and "длительность" in why
    ok, why = perf._usable({**_sample("p1", "demo"), "evidence_ref": ""})
    assert not ok and "улику" in why
    assert perf._usable(_sample("p1", "demo"))[0] is True


def test_collection_records_a_dirty_tree_truthfully(perf, tmp_path, monkeypatch):
    """Соврать про грязное дерево — значит сравнить неизвестно что с неизвестно чем."""
    corpus = tmp_path / "c.jsonl"
    corpus.write_text(json.dumps(_sample("p1", "demo")) + "\n", encoding="utf-8")
    monkeypatch.setattr(perf, "_source_identity", lambda: ("a" * 40, True))
    args = perf.main.__globals__["argparse"].Namespace(
        label="baseline", out=tmp_path / "out.json", corpus=corpus,
        hardware="h", platform="p", models="m", permissions="perm",
        resource_envelope="env", workload="w")
    assert perf.cmd_collect(args) == 0
    body = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert body["dirty_tree"] is True and len(body["records"]) == 1


def test_a_duplicate_pair_is_rejected_not_averaged(perf, tmp_path, monkeypatch):
    """Парный протокол требует РОВНО одно наблюдение на пару."""
    corpus = tmp_path / "c.jsonl"
    corpus.write_text("\n".join(json.dumps(x) for x in
                                (_sample("p1", "demo", task="a"),
                                 _sample("p1", "demo", task="b"))) + "\n", encoding="utf-8")
    monkeypatch.setattr(perf, "_source_identity", lambda: ("a" * 40, False))
    args = perf.main.__globals__["argparse"].Namespace(
        label="baseline", out=tmp_path / "out.json", corpus=corpus, hardware="",
        platform="", models="", permissions="", resource_envelope="", workload="")
    perf.cmd_collect(args)
    body = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert len(body["records"]) == 1 and len(body["rejected"]) == 1
    assert "повторная выборка" in body["rejected"][0]["reason"]


# ---------------------------------------------------------------- манифест

def test_only_workloads_observed_in_both_versions_are_paired(perf):
    base = {"records": [_sample("p1", "a"), _sample("p2", "a"), _sample("p3", "b")]}
    cand = {"records": [_sample("p1", "a"), _sample("p3", "b"), _sample("p9", "b")]}
    manifest, notes = perf.build_manifest(base, cand)
    assert manifest == {"p1": "a", "p3": "b"}
    assert any("только в baseline" in n for n in notes)
    assert any("только в candidate" in n for n in notes)


def test_a_pair_whose_family_moved_is_excluded_and_named(perf):
    base = {"records": [_sample("p1", "a")]}
    cand = {"records": [_sample("p1", "b")]}
    manifest, notes = perf.build_manifest(base, cand)
    assert manifest == {} and any("семейство различается" in n for n in notes)


# --------------------------------------------------------------- сравнение

def test_too_few_pairs_is_insufficient_evidence_not_a_verdict(perf, tmp_path, capsys):
    records = [_sample(f"p{i}", "demo") for i in range(5)]
    args = perf.main.__globals__["argparse"].Namespace(
        baseline=_collection(perf, tmp_path, "baseline", records, "a" * 40),
        candidate=_collection(perf, tmp_path, "candidate", records, "b" * 40),
        bootstrap=200, seed=0, json=True, json_out=tmp_path / "r.json")
    assert perf.cmd_compare(args) == 1
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["performance_verdict"] == "INSUFFICIENT_EVIDENCE"
    assert any("требуется >= 100" in b for b in report["blockers"])
    assert report["evaluation"] is None, "вердикта без улик быть не должно"


def test_a_thin_family_blocks_the_verdict(perf, tmp_path):
    """Одно многочисленное семейство не выкупает пустое соседнее."""
    records = ([_sample(f"a{i}", "alpha") for i in range(120)]
               + [_sample(f"b{i}", "beta") for i in range(5)])
    args = perf.main.__globals__["argparse"].Namespace(
        baseline=_collection(perf, tmp_path, "baseline", records, "a" * 40),
        candidate=_collection(perf, tmp_path, "candidate", records, "b" * 40),
        bootstrap=200, seed=0, json=True, json_out=tmp_path / "r.json")
    perf.cmd_compare(args)
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["performance_verdict"] == "INSUFFICIENT_EVIDENCE"
    assert any("beta=5" in b for b in report["blockers"])


def test_unmeasured_cost_blocks_the_verdict_instead_of_becoming_zero(perf, tmp_path):
    """Ноль вместо неизмеренной стоимости дал бы «бесплатно» — самый дешёвый способ
    получить трёхкратное улучшение по цене."""
    records = [{**_sample(f"p{i}", "demo"), "cost_usd": None} for i in range(120)]
    args = perf.main.__globals__["argparse"].Namespace(
        baseline=_collection(perf, tmp_path, "baseline", records, "a" * 40),
        candidate=_collection(perf, tmp_path, "candidate", records, "b" * 40),
        bootstrap=200, seed=0, json=True, json_out=tmp_path / "r.json")
    perf.cmd_compare(args)
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["performance_verdict"] == "INSUFFICIENT_EVIDENCE"
    assert any("стоимост" in b for b in report["blockers"])


def test_sufficient_but_unimproved_evidence_is_not_met(perf, tmp_path):
    """Достаточно улик — но одинаковые числа обязаны дать NOT_MET, а не MET."""
    records = [_sample(f"p{i}", "demo") for i in range(120)]
    args = perf.main.__globals__["argparse"].Namespace(
        baseline=_collection(perf, tmp_path, "baseline", records, "a" * 40),
        candidate=_collection(perf, tmp_path, "candidate", records, "b" * 40),
        bootstrap=200, seed=0, json=True, json_out=tmp_path / "r.json")
    assert perf.cmd_compare(args) == 1
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["performance_verdict"] == "NOT_MET"
    assert report["evaluation"]["certified"] is False
    assert report["evaluation"]["gates"]["throughput_3x"] is False


def test_a_result_is_never_certified_by_this_tool(perf, tmp_path):
    """Даже MET здесь означает только «числа сошлись». Сертификации нет."""
    fast = [{**_sample(f"p{i}", "demo"), "duration_s": 0.1, "cost_usd": 0.001}
            for i in range(120)]
    slow = [_sample(f"p{i}", "demo") for i in range(120)]
    args = perf.main.__globals__["argparse"].Namespace(
        baseline=_collection(perf, tmp_path, "baseline", slow, "a" * 40),
        candidate=_collection(perf, tmp_path, "candidate", fast, "b" * 40),
        bootstrap=200, seed=0, json=True, json_out=tmp_path / "r.json")
    perf.cmd_compare(args)
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["evaluation"]["certified"] is False
    assert report["evaluation"]["source_trust"] == "UNVERIFIED_REPORTED_MEASUREMENTS"


def test_a_dirty_tree_cannot_produce_a_verdict(perf, tmp_path):
    records = [_sample(f"p{i}", "demo") for i in range(120)]
    args = perf.main.__globals__["argparse"].Namespace(
        baseline=_collection(perf, tmp_path, "baseline", records, "a" * 40, dirty=True),
        candidate=_collection(perf, tmp_path, "candidate", records, "b" * 40),
        bootstrap=200, seed=0, json=True, json_out=tmp_path / "r.json")
    perf.cmd_compare(args)
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["performance_verdict"] == "INSUFFICIENT_EVIDENCE"
    assert any("dirty" in r or "source tree" in r
               for r in report["evaluation"]["reasons"])
