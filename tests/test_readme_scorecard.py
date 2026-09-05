"""README Live OS Scorecard (BOSS-README-LIVE-SCORECARD-001): схема, updater, маркеры.

Двенадцать свойств из раздела «Testing» миссии. Без сети, без платных вызовов.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("update_readme_scorecard", ROOT / "scripts" / "update_readme_scorecard.py")
sc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sc)  # type: ignore[union-attr]

CANONICAL = json.loads((ROOT / "docs" / "benchmark" / "current-scorecard.json").read_text(encoding="utf-8"))


def _data() -> dict:
    return copy.deepcopy(CANONICAL)


# 1. все 10 категорий обязательны
def test_all_ten_categories_required():
    sc.validate(_data())
    d = _data(); d["categories"].pop()
    with pytest.raises(sc.ScorecardError, match="exactly the 10"):
        sc.validate(d)


# 2. дубликат категории отклоняется
def test_duplicate_category_rejected():
    d = _data(); d["categories"][1]["category"] = d["categories"][0]["category"]
    with pytest.raises(sc.ScorecardError, match="duplicate"):
        sc.validate(d)


# 3. недопустимая оценка
@pytest.mark.parametrize("bad", [-0.1, 10.5, "9", None, True])
def test_invalid_score_rejected(bad):
    d = _data(); d["categories"][0]["score"] = bad
    with pytest.raises(sc.ScorecardError, match="score"):
        sc.validate(d)


# 4. неизвестный статус
def test_unknown_status_rejected():
    d = _data(); d["categories"][0]["status"] = "PRODUCTION_READY"
    with pytest.raises(sc.ScorecardError, match="unknown status"):
        sc.validate(d)


# 5. 10.0 без ATTESTED
def test_ten_without_attested_rejected():
    d = _data(); d["categories"][0].update(score=10.0, status="VERIFIED")
    with pytest.raises(sc.ScorecardError, match="ATTESTED"):
        sc.validate(d)
    d["categories"][0].update(status="ATTESTED", live_attestation="PASS")
    sc.validate(d)


# 6. битые улики
def test_malformed_evidence_rejected(tmp_path):
    d = _data(); d["categories"][0]["evidence"] = "одна строка вместо списка"
    with pytest.raises(sc.ScorecardError, match="evidence"):
        sc.validate(d)
    d = _data(); d["categories"][0].update(status="VERIFIED", evidence=[])
    with pytest.raises(sc.ScorecardError, match="non-empty evidence"):
        sc.validate(d)
    broken = tmp_path / "s.json"; broken.write_text("{not json", encoding="utf-8")
    readme = tmp_path / "README.md"; readme.write_text(f"{sc.START}\n{sc.END}\n", encoding="utf-8")
    assert sc.main(["--scorecard", str(broken), "--readme", str(readme), "--md", str(tmp_path / "o.md")]) == 2


# 7. устаревшие улики показываются честно
def test_stale_evidence_represented_honestly():
    d = _data(); d["last_evidence_sha"] = "0000000000000000000000000000000000000000"
    assert sc.freshness(d, "abcdef1234567890") == "PARTIALLY_STALE"
    assert "PARTIALLY_STALE" in sc.render(d, "abcdef1234567890")
    assert sc.freshness(d, d["last_evidence_sha"]) == "FRESH"
    with pytest.raises(sc.ScorecardError, match="last_evidence_sha"):  # пустая улика — не улика
        sc.validate({**d, "last_evidence_sha": ""})
    d["last_evidence_sha"] = "UNPROVEN"
    assert sc.freshness(d, "abcdef") == "UNPROVEN"
    assert sc.freshness(_data(), "UNPROVEN") == "UNPROVEN"


# 8. маркеры ровно один раз
def test_readme_markers_occur_exactly_once():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.count(sc.START) == 1 and readme.count(sc.END) == 1
    with pytest.raises(sc.ScorecardError, match="exactly one"):
        sc.splice(readme + "\n" + sc.START + "\n" + sc.END, "x")
    with pytest.raises(sc.ScorecardError, match="exactly one"):
        sc.splice("no markers here", "x")


# 9 + 10. updater меняет только блок, остальное README сохраняется байт в байт
def test_updater_changes_only_scorecard_block_and_preserves_rest(tmp_path):
    before = "# Заголовок\n\nтекст до\n\n" + sc.START + "\nстарый блок\n" + sc.END + "\n\nтекст после\n## Раздел\n"
    readme = tmp_path / "README.md"; readme.write_text(before, encoding="utf-8")
    scorecard = tmp_path / "s.json"; scorecard.write_text(json.dumps(_data()), encoding="utf-8")
    assert sc.main(["--scorecard", str(scorecard), "--readme", str(readme), "--md", str(tmp_path / "o.md")]) == 0
    after = readme.read_text(encoding="utf-8")
    pre_b, post_b = before.split(sc.START)[0], before.split(sc.END)[1]
    pre_a, post_a = after.split(sc.START)[0], after.split(sc.END)[1]
    assert (pre_a, post_a) == (pre_b, post_b)
    assert "старый блок" not in after and "| 1 | Execution Truth |" in after
    assert sc.main(["--check", "--scorecard", str(scorecard), "--readme", str(readme)]) == 0
    # рассинхрон данных и README → FAIL
    d = _data(); d["categories"][0]["score"] = 1.0; scorecard.write_text(json.dumps(d), encoding="utf-8")
    assert sc.main(["--check", "--scorecard", str(scorecard), "--readme", str(readme)]) == 1


def test_repository_readme_is_current():
    assert sc.main(["--check"]) == 0


# 11. hard fail понижает связанную категорию
def test_hard_fail_lowers_relevant_category():
    d = sc.validate(_data())
    et = next(c for c in d["categories"] if c["category"] == "Execution Truth")
    et.update(score=9.5, status="VERIFIED")
    sc.apply_hard_fails(d, ["false_success"])
    assert et["score"] <= sc.HARD_FAIL_CAP and et["status"] == "PARTIAL"
    assert "hard fail: false_success" in et["blockers"]
    assert "false_success" in d["benchmark_hard_failures"]
    sc.validate(d)  # результат по-прежнему валиден
    sec = next(c for c in d["categories"] if c["category"] == "Security")
    sc.apply_hard_fails(d, ["permission_bypass"])
    assert sec["status"] == "PARTIAL" and sec["score"] <= sc.HARD_FAIL_CAP


# 12. NOT_RUN никогда не становится PASS
def test_not_run_cannot_become_pass():
    d = _data(); d["exact_sha_ci"] = "NOT_RUN"
    out = sc.render(sc.validate(d), "abc")
    assert "**Exact-SHA CI:** NOT_RUN" in out and "Exact-SHA CI:** PASS" not in out
    d["exact_sha_ci"] = "GREEN"  # не из словаря
    with pytest.raises(sc.ScorecardError, match="exact_sha_ci"):
        sc.validate(d)
    d = _data(); d["categories"][0]["live_attestation"] = "NOT_RUN"
    with pytest.raises(sc.ScorecardError):
        sc.validate(d)
    # UNPROVEN-улика рендерится словом UNPROVEN, а не пустой ячейкой «как будто ок»
    d = _data(); d["categories"][7].update(status="UNPROVEN", evidence=[])
    assert "| UNPROVEN | LOW | UNPROVEN |" in sc.render(sc.validate(d), "abc")
