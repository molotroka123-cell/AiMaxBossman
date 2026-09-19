"""Запись, дописанная в корпус напрямую, не должна исчезать от чужого добавления.

Журнал авторитетен, снимки производны — и до сих пор из этого следовало
буквальное: запись, попавшая в ``fix_cases.jsonl`` мимо ``add``, стиралась при
первом же ``_materialize``. Не уходила в history, не попадала в
``failed_experiments`` — пропадала целиком. Хватало того, что кто-то другой
добавил совершенно несвязанный случай.

Проверяется ПАРА, а не только удобная половина: законная осиротевшая запись
обязана пережить чужое добавление, а подложенная или битая — обязана
по-прежнему отбрасываться. Усыновление, принимающее что попало, превратило бы
корпус в место, куда можно дописать себе любой «проверенный» случай.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from learning.trace import LearningStore, case_id  # noqa: E402
from tests.test_learning_trace import _case  # noqa: E402


def _store(tmp_path: Path) -> LearningStore:
    return LearningStore(data_dir=tmp_path / "data", docs_dir=tmp_path / "docs")


def _seed(tmp_path: Path, first: dict) -> LearningStore:
    """Корпус с одной законно добавленной записью — и, значит, с журналом."""
    store = _store(tmp_path)
    store.add(first, write_markdown=False)
    return store


def _append_directly(store: LearningStore, case: dict) -> str:
    """Дописать в снимок мимо add — ровно то, что делает соседний агент."""
    case = dict(case)
    case["case_id"] = case_id(case)
    case.setdefault("version", 1)
    path = store.verified_path if case["learning_status"] == "VERIFIED" else store.failed_path
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n")
    return case["case_id"]


def _ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {json.loads(line)["case_id"]
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def test_a_case_appended_straight_into_the_corpus_survives_someone_elses_add(tmp_path):
    store = _seed(tmp_path, _case(task_id="MINE-001"))
    orphan = _append_directly(store, _case(task_id="THEIRS-001", agent="codex-other"))
    assert orphan in _ids(store.verified_path)

    store.add(_case(task_id="MINE-002"), write_markdown=False)

    assert orphan in _ids(store.verified_path), "чужая запись уничтожена чужим добавлением"
    assert orphan in {json.loads(l)["case"]["case_id"]
                      for l in store.journal_path.read_text(encoding="utf-8").splitlines() if l.strip()}, \
        "запись выжила в снимке, но не попала в журнал — исчезнет в следующий раз"


def test_the_adopted_case_keeps_its_own_author_and_version(tmp_path):
    store = _seed(tmp_path, _case(task_id="MINE-001"))
    orphan = _append_directly(store, _case(task_id="THEIRS-001", agent="codex-other"))
    store.add(_case(task_id="MINE-002"), write_markdown=False)
    kept = next(json.loads(line) for line in store.verified_path.read_text(encoding="utf-8").splitlines()
                if line.strip() and json.loads(line)["case_id"] == orphan)
    assert kept["agent"] == "codex-other", "усыновление не должно переписывать авторство"
    assert int(kept.get("version") or 1) == 1, "усыновление не должно поднимать версию чужой записи"


def test_a_planted_case_whose_id_does_not_match_its_content_is_still_discarded(tmp_path):
    """Негативный контроль. Усыновление, принимающее что попало, сделало бы
    корпус местом, куда дописывают себе любой «проверенный» случай."""
    store = _seed(tmp_path, _case(task_id="MINE-001"))
    planted = _case(task_id="PLANTED-001")
    planted["case_id"] = "0000000000000000"   # отпечаток не сходится с содержимым
    planted["version"] = 1
    with store.verified_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(planted, ensure_ascii=False, sort_keys=True) + "\n")

    store.add(_case(task_id="MINE-002"), write_markdown=False)

    assert "0000000000000000" not in _ids(store.verified_path)
    assert "0000000000000000" not in store.journal_path.read_text(encoding="utf-8")


def test_a_case_that_fails_the_current_schema_is_not_adopted(tmp_path):
    """Второй негативный контроль: битую запись нельзя делать авторитетной."""
    store = _seed(tmp_path, _case(task_id="MINE-001"))
    broken = _case(task_id="BROKEN-001")
    del broken["root_cause"]
    broken["case_id"] = case_id(broken)
    broken["version"] = 1
    with store.verified_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(broken, ensure_ascii=False, sort_keys=True) + "\n")

    store.add(_case(task_id="MINE-002"), write_markdown=False)

    assert broken["case_id"] not in store.journal_path.read_text(encoding="utf-8")


def test_a_corpus_without_orphans_is_left_exactly_as_it_was(tmp_path):
    """Усыновление не должно шевелить журнал, когда усыновлять нечего."""
    store = _seed(tmp_path, _case(task_id="MINE-001"))
    store.add(_case(task_id="MINE-002"), write_markdown=False)
    before = store.journal_path.read_text(encoding="utf-8")
    store.add(_case(task_id="MINE-003"), write_markdown=False)
    after = store.journal_path.read_text(encoding="utf-8")
    assert after.startswith(before), "журнал переписан там, где должен был только дополниться"


def test_adoption_survives_a_second_unrelated_add(tmp_path):
    """Однажды усыновлённая запись не должна зависеть от удачи следующего раза."""
    store = _seed(tmp_path, _case(task_id="MINE-001"))
    orphan = _append_directly(store, _case(task_id="THEIRS-001", agent="codex-other"))
    store.add(_case(task_id="MINE-002"), write_markdown=False)
    store.add(_case(task_id="MINE-003"), write_markdown=False)
    assert orphan in _ids(store.verified_path)


@pytest.mark.parametrize("status", ["VERIFIED", "FAILED_EXPERIMENT"])
def test_both_corpora_are_healed_not_only_the_verified_one(tmp_path, status):
    store = _seed(tmp_path, _case(task_id="MINE-001"))
    extra = {"learning_status": status}
    if status != "VERIFIED":
        extra.update(verified_by=[], external_verification="", evidence=[])
    orphan = _append_directly(store, _case(task_id=f"THEIRS-{status}", agent="codex-other", **extra))
    store.add(_case(task_id="MINE-002"), write_markdown=False)
    target = store.verified_path if status == "VERIFIED" else store.failed_path
    assert orphan in _ids(target)
