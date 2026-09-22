"""Measured tournament, independent review, evidence and one-shot validation."""
import json
import sys

import pytest

from test_evolution_runner import project, proposal, host_run
from bossman_v3.self_improvement import runner as evo
from bossman_v3.self_improvement import protocol as p
from bossman_v3.self_improvement.validation import validate, verify_campaign


def reviewer(prompt, *args):
    context = json.loads(prompt.split("REVIEW_INPUT\n", 1)[1])
    return {**{k: context[k] for k in ("nonce", "base_sha", "patch_sha256")},
            "covered_files": list(context["sources"]), "verdict": "accept", "findings": []}, 0.05


def reviewed(repo, suite, work, **kw):
    return host_run(repo, suite, work, proposer=lambda *a: (proposal(), 0.1),
                    reviewer=reviewer, model="builder", reviewer_model="critic",
                    require_review=True, iterations=1, **kw)


def test_tournament_selects_smaller_verified_patch(project):
    repo, suite, work = project
    choices = [proposal(), proposal()]
    choices[0]["edits"][0]["new"] = "result = 2\n    return result"
    result = host_run(repo, suite, work, proposer=lambda *a: (choices.pop(0), 0.1),
                      reviewer=reviewer, model="builder", reviewer_model="critic",
                      require_review=True, candidates=2, iterations=1)
    assert len(result["attempts"]) == 2
    assert result["rounds"][0]["selected"] == result["attempts"][1]["id"]
    assert result["champion_sha"] == result["attempts"][1]["candidate_sha"]
    assert result["reserved_usd"] == 1.25
    assert result["reported_cost_usd"] == pytest.approx(0.25)
    assert verify_campaign(work)["receipts"] == 3
    assert not result["production_promoted"]


def test_reviewer_rejection_prevents_champion_change(project):
    repo, suite, work = project
    def reject(prompt, *args):
        response, cost = reviewer(prompt, *args)
        response["verdict"] = "inconclusive"
        return response, cost
    result = host_run(repo, suite, work, proposer=lambda *a: (proposal(), 0.1),
                      reviewer=reject, model="builder", reviewer_model="critic",
                      require_review=True, iterations=1)
    assert result["status"] == "QUARANTINED"
    assert result["champion_sha"] == result["base_sha"]
    assert result["attempts"][0]["review_status"] == "BLOCKED"


@pytest.mark.parametrize("mutation", ["nonce", "base_sha", "patch_sha256", "coverage", "finding", "verdict"])
def test_review_cannot_be_replayed_or_omit_coverage(mutation):
    context = p.review_input("base", "patch", {"calc.py": "return 2"}, "repair")
    response, _ = reviewer(p.review_prompt(context))
    if mutation in {"nonce", "base_sha", "patch_sha256"}:
        response[mutation] = "other-candidate"
    elif mutation == "coverage":
        response["covered_files"] = []
    elif mutation == "finding":
        response["findings"] = [{"path": "calc.py", "line": 20, "reason": "outside source"}]
    else:
        response["verdict"] = "reject"
    with pytest.raises(ValueError):
        p.validate_review(response, context)


def test_same_model_cannot_review_itself(project):
    repo, suite, work = project
    with pytest.raises(ValueError, match="distinct"):
        host_run(repo, suite, work, proposer=lambda *a: pytest.fail("no call"),
                 reviewer=reviewer, model="builder:MODEL", reviewer_model="reviewer:model")


def test_changed_evidence_blocks_resume(project):
    repo, suite, work = project
    result = reviewed(repo, suite, work)
    target = work / "runs" / result["attempts"][0]["id"] / "independent-review.json"
    target.write_text('{}')
    with pytest.raises(ValueError, match="Evidence changed"):
        verify_campaign(work)
    with pytest.raises(ValueError, match="Evidence changed"):
        reviewed(repo, suite, work)


@pytest.mark.parametrize("expected", [2, 3])
def test_holdout_consumed_once_without_entering_memory(project, expected):
    repo, suite, work = project
    (repo / "test_heldout.py").write_text(f"from calc import value\ndef test_heldout():\n    assert value() == {expected}\n")
    data = {"version": 1, "cases": [{"id": "private-validation", "role": "holdout",
                                     "goal": "One-shot validation", "tests": ["test_heldout.py"], "editable": []}]}
    (repo / "holdout.json").write_text(json.dumps(data))
    evo.git(repo, "add", ".")
    evo.git(repo, "-c", "user.name=test", "-c", "user.email=t@localhost", "commit", "-qm", "freeze holdout")
    reviewed(repo, suite, work)
    heldout = evo.load_suite(repo, repo / "holdout.json", holdout=True)
    result = validate(repo, heldout, work, executor="host")
    assert result["status"] == ("HOLDOUT_PASSES" if expected == 2 else "HOLDOUT_BLOCKED")
    assert verify_campaign(work)["receipts"] == 3
    with pytest.raises(ValueError, match="already consumed"):
        validate(repo, heldout, work, executor="host")
    with pytest.raises(ValueError, match="sealed"):
        reviewed(repo, suite, work)
    assert all(row["task"] != "private-validation" for row in evo.LearningStore(work / "learning").failed())


def test_holdout_reusing_training_files_is_rejected(project):
    repo, suite, work = project
    reviewed(repo, suite, work)
    heldout = {"fingerprint": "test", "cases": [{"id": "h", "role": "holdout", "tests": ["test_target.py"]}]}
    with pytest.raises(ValueError, match="overlaps"):
        validate(repo, heldout, work, executor="host")
    assert not json.loads((work / "state.json").read_text()).get("validation_consumed")


def test_process_output_and_xml_symlinks_are_bounded(tmp_path):
    with pytest.raises(ValueError, match="output limit"):
        evo.command([sys.executable, "-c", "print('x' * 20000)"], tmp_path, max_output=1024)
    real = tmp_path / "real.xml"
    real.write_text('<testsuite><testcase name="pass"/></testsuite>')
    link = tmp_path / "link.xml"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("Symlink privilege unavailable")
    assert evo.parse_junit(link, 0, "")["status"] == "BLOCKED"
