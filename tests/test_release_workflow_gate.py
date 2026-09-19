"""BL-089 в исполняемом виде: отсутствующее задание — ОТКАЗ, а не тишина.

Решающая часть гейта — чистые функции, и проверяются именно они. Сеть сюда не
ходит: тест, которому нужен GitHub, в корневом наборе не живёт.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("release_workflow_gate",
                                               ROOT / "tools" / "release_workflow_gate.py")
gatemod = importlib.util.module_from_spec(_SPEC)
sys.modules["release_workflow_gate"] = gatemod
_SPEC.loader.exec_module(gatemod)  # type: ignore[union-attr]

SHA = "a" * 40
OTHER = "b" * 40
DECLARED = ["windows-bundle.yml", "root-ci.yml"]


def run(name: str, status: str, conclusion: str | None = None, sha: str = SHA) -> dict:
    return {"path": f".github/workflows/{name}", "status": status,
            "conclusion": conclusion, "head_sha": sha}


# --- классификация одного прогона -------------------------------------------

@pytest.mark.parametrize("status,conclusion,expected", [
    ("completed", "success", gatemod.PASS),
    ("completed", "failure", gatemod.FAIL),
    ("completed", "timed_out", gatemod.FAIL),
    ("completed", "startup_failure", gatemod.FAIL),
    ("completed", "cancelled", gatemod.UNKNOWN_CANCELLED),
    ("completed", "neutral", gatemod.UNKNOWN_OTHER),
    ("completed", "skipped", gatemod.UNKNOWN_OTHER),
    ("completed", "action_required", gatemod.UNKNOWN_OTHER),
    ("queued", None, gatemod.UNKNOWN_QUEUED),
    ("in_progress", None, gatemod.UNKNOWN_QUEUED),
    ("pending", None, gatemod.UNKNOWN_QUEUED),
])
def test_a_run_is_classified_without_charity(status, conclusion, expected):
    assert gatemod.classify(run("x.yml", status, conclusion)) == expected


# --- собственно урок BL-089 --------------------------------------------------

def test_a_missing_workflow_blocks_the_release():
    """Сердце гейта. Ноль прогонов — это ОТКАЗ, а не отсутствие замечаний."""
    report = gatemod.gate(DECLARED, [run("root-ci.yml", "completed", "success")], SHA)
    assert report["workflows"]["windows-bundle.yml"] == gatemod.MISSING
    assert report["verdict"] == gatemod.FAIL
    assert "windows-bundle.yml" in report["blocking"]


def test_everything_green_passes():
    """Положительная половина: без неё «всё блокирует» тоже давало бы отказ."""
    report = gatemod.gate(DECLARED, [run(n, "completed", "success") for n in DECLARED], SHA)
    assert report["verdict"] == gatemod.PASS
    assert report["blocking"] == {}


def test_a_queued_workflow_is_not_a_pass():
    report = gatemod.gate(DECLARED, [run("root-ci.yml", "completed", "success"),
                                     run("windows-bundle.yml", "queued")], SHA)
    assert report["workflows"]["windows-bundle.yml"] == gatemod.UNKNOWN_QUEUED
    assert report["verdict"] == gatemod.FAIL


def test_a_cancelled_workflow_is_not_a_pass():
    """Прогоны 133/134/135 отменены подряд — и это не было доказательством."""
    report = gatemod.gate(DECLARED, [run("root-ci.yml", "completed", "success"),
                                     run("windows-bundle.yml", "completed", "cancelled")], SHA)
    assert report["workflows"]["windows-bundle.yml"] == gatemod.UNKNOWN_CANCELLED
    assert report["verdict"] == gatemod.FAIL


def test_a_run_on_a_different_sha_proves_nothing_here():
    """Зелёный прогон соседнего коммита не переносится на кандидата."""
    report = gatemod.gate(DECLARED, [run("root-ci.yml", "completed", "success"),
                                     run("windows-bundle.yml", "completed", "success", sha=OTHER)],
                          SHA)
    assert report["workflows"]["windows-bundle.yml"] == gatemod.MISSING
    assert report["verdict"] == gatemod.FAIL


def test_a_rerun_that_succeeded_counts_but_two_failures_do_not():
    """Повторный запуск — законный способ пережить срыв инфраструктуры.

    Пара: успех среди повторов принимается, а из двух НЕуспешных лучший не
    выбирается — иначе «отменён + в очереди» превратилось бы в проход.
    """
    passed = gatemod.gate(DECLARED, [run("root-ci.yml", "completed", "success"),
                                     run("windows-bundle.yml", "completed", "failure"),
                                     run("windows-bundle.yml", "completed", "success")], SHA)
    assert passed["verdict"] == gatemod.PASS

    still_blocked = gatemod.gate(DECLARED, [run("root-ci.yml", "completed", "success"),
                                            run("windows-bundle.yml", "completed", "cancelled"),
                                            run("windows-bundle.yml", "queued")], SHA)
    assert still_blocked["verdict"] == gatemod.FAIL


def test_an_undeclared_workflow_cannot_rescue_a_missing_one():
    report = gatemod.gate(DECLARED, [run("root-ci.yml", "completed", "success"),
                                     run("some-other.yml", "completed", "success")], SHA)
    assert report["verdict"] == gatemod.FAIL
    assert report["workflows"]["windows-bundle.yml"] == gatemod.MISSING


# --- объявление --------------------------------------------------------------

def test_the_declaration_names_real_workflow_files():
    for name in gatemod.load_declaration():
        assert (ROOT / ".github" / "workflows" / name).exists(), (
            f"обязательным объявлено {name}, а файла задания нет — гейт ждал бы "
            f"прогона, который некому запустить")


def test_an_empty_declaration_is_refused(tmp_path):
    """Пустой список сделал бы гейт зелёным всегда."""
    empty = tmp_path / "d.json"
    empty.write_text(json.dumps({"mandatory": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        gatemod.load_declaration(empty)


def test_the_cli_refuses_rather_than_passes_without_a_token(tmp_path, monkeypatch, capsys):
    """Нет ключа — это OWNER_REQUIRED и код 3, а не молчаливый проход."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    code = gatemod.main(["--sha", SHA, "--repo", ""])
    assert code == 3
    assert "OWNER_REQUIRED" in capsys.readouterr().out


def test_the_cli_reports_failure_for_a_missing_workflow(tmp_path, capsys):
    runs = tmp_path / "runs.json"
    runs.write_text(json.dumps([run("root-ci.yml", "completed", "success")]), encoding="utf-8")
    code = gatemod.main(["--sha", SHA, "--runs-from", str(runs)])
    assert code == 1
    assert "RELEASE_WORKFLOW_GATE=FAIL" in capsys.readouterr().out
