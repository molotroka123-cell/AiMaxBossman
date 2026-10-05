"""Exact-SHA release certification is falsifiable: only completed success runs on
the same commit certify; skipped/cancelled/in-progress/other-SHA never do."""
from __future__ import annotations

import json
import io
import subprocess
import sys
import urllib.error
import urllib.parse
from types import SimpleNamespace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import exact_sha_certify as esc  # noqa: E402

SHA = "a" * 40
OTHER = "b" * 40
REQ = ("root-ci", "Core CI", "CC CI")


def _run(name, *, sha=SHA, status="completed", conclusion="success", number=1, attempt=1, rid=None):
    rid = rid or number * 10 + attempt
    return {"name": name, "head_sha": sha, "status": status, "conclusion": conclusion,
            "run_number": number, "run_attempt": attempt, "id": rid,
            "html_url": f"https://example.invalid/{name}/{number}",
            "jobs": [{"id": rid * 100, "run_id": rid, "head_sha": sha,
                      "status": "completed", "conclusion": "success", "name": "checks"}],
            "jobs_evidence": {"run_id": rid, "run_attempt": attempt, "head_sha": sha,
                              "total_count": 1, "complete": True}}


def test_all_required_green_on_exact_sha_is_certified():
    report = esc.certify(SHA, [_run(n) for n in REQ], REQ)
    assert report["verdict"] == esc.CERTIFIED and report["final"] is True
    assert all(w["result"] == "PASS" for w in report["workflows"].values())


@pytest.mark.parametrize("conclusion", ["skipped", "cancelled", "neutral", "timed_out", "failure",
                                        "action_required", "stale", "startup_failure", ""])
def test_non_success_conclusion_is_not_pass(conclusion):
    runs = [_run("root-ci"), _run("Core CI"), _run("CC CI", conclusion=conclusion)]
    report = esc.certify(SHA, runs, REQ)
    assert report["verdict"] == esc.NOT_CERTIFIED
    assert report["workflows"]["CC CI"]["result"] == "NOT_PASS"
    assert conclusion in report["workflows"]["CC CI"]["reason"] or "none" in report["workflows"]["CC CI"]["reason"]


def test_green_run_on_another_sha_never_transfers():
    runs = [_run("root-ci"), _run("Core CI"), _run("CC CI", sha=OTHER)]
    report = esc.certify(SHA, runs, REQ)
    assert report["verdict"] == esc.NOT_CERTIFIED
    assert report["workflows"]["CC CI"]["result"] == "MISSING"
    assert report["ignored_other_sha"] == 1


def test_in_progress_run_is_not_final_and_not_pass():
    runs = [_run("root-ci"), _run("Core CI"), _run("CC CI", status="in_progress", conclusion="")]
    report = esc.certify(SHA, runs, REQ)
    assert report["verdict"] == esc.NOT_CERTIFIED and report["final"] is False
    assert report["workflows"]["CC CI"]["result"] == "NOT_FINAL"


def test_rerun_supersedes_earlier_attempt_on_same_sha_only():
    runs = [_run("root-ci"), _run("Core CI"),
            _run("CC CI", conclusion="cancelled", number=5, attempt=1),
            _run("CC CI", conclusion="success", number=5, attempt=2)]
    assert esc.certify(SHA, runs, REQ)["verdict"] == esc.CERTIFIED
    # a later, green attempt on ANOTHER commit does not rescue this one
    runs[-1] = _run("CC CI", conclusion="success", number=6, attempt=1, sha=OTHER)
    report = esc.certify(SHA, runs, REQ)
    assert report["verdict"] == esc.NOT_CERTIFIED
    assert report["workflows"]["CC CI"]["conclusion"] == "cancelled"


def test_missing_required_workflow_is_never_assumed():
    report = esc.certify(SHA, [_run("root-ci"), _run("Core CI")], REQ)
    assert report["verdict"] == esc.NOT_CERTIFIED
    assert report["workflows"]["CC CI"]["result"] == "MISSING"


def test_no_runs_is_insufficient_evidence_not_pass():
    report = esc.certify(SHA, [], REQ)
    assert report["verdict"] == esc.INSUFFICIENT and report["final"] is False


@pytest.mark.parametrize("sha", ["abc123", "", "a" * 39, "g" * 40])
def test_abbreviated_or_invalid_sha_cannot_be_certified(sha):
    with pytest.raises(esc.CertificationError):
        esc.certify(sha, [_run(n, sha=sha) for n in REQ], REQ)


def test_sha_case_is_normalized_not_a_different_commit():
    report = esc.certify("A" * 40, [_run(n) for n in REQ], REQ)
    assert report["sha"] == SHA and report["verdict"] == esc.CERTIFIED


def test_empty_requirement_set_certifies_nothing():
    with pytest.raises(esc.CertificationError):
        esc.certify(SHA, [_run("root-ci")], ())


def test_default_required_names_match_workflow_files():
    root = Path(__file__).resolve().parents[1]
    names = set()
    for wf in (root / ".github" / "workflows").glob("*.yml"):
        for line in wf.read_text(encoding="utf-8").splitlines():
            if line.startswith("name:"):
                names.add(line[len("name:"):].strip().strip("'\""))
                break
    missing = set(esc.DEFAULT_REQUIRED) - names
    assert not missing, f"required workflow names not found in .github/workflows: {missing}"


def test_scorecard_pass_claim_requires_certification_of_that_sha():
    good = esc.certify(SHA, [_run(n) for n in REQ], REQ)
    assert esc.check_scorecard(good, {"exact_sha_ci": "PASS", "last_evidence_sha": SHA}) == ""
    assert esc.check_scorecard(good, {"exact_sha_ci": "UNPROVEN", "last_evidence_sha": OTHER}) == ""
    stale = esc.check_scorecard(good, {"exact_sha_ci": "PASS", "last_evidence_sha": OTHER})
    assert "OLD_SHA_PASS" in stale
    bad = esc.certify(SHA, [_run("root-ci"), _run("Core CI"), _run("CC CI", conclusion="cancelled")], REQ)
    assert "NOT_CERTIFIED" in esc.check_scorecard(bad, {"exact_sha_ci": "PASS", "last_evidence_sha": SHA})


def test_cli_exit_codes_and_report(tmp_path):
    tool = Path(__file__).resolve().parents[1] / "tools" / "exact_sha_certify.py"
    runs = tmp_path / "runs.json"
    out = tmp_path / "report.json"
    runs.write_text(json.dumps({"workflow_runs": [_run(n) for n in REQ]}), encoding="utf-8")
    base = [sys.executable, str(tool), "--sha", SHA, "--runs-json", str(runs), "--required", *REQ]
    assert subprocess.run(base + ["--output", str(out)], capture_output=True, text=True).returncode == 0
    assert json.loads(out.read_text(encoding="utf-8"))["verdict"] == "CERTIFIED"
    runs.write_text(json.dumps({"workflow_runs": [_run(n, conclusion="skipped") for n in REQ]}), encoding="utf-8")
    res = subprocess.run(base, capture_output=True, text=True)
    assert res.returncode == 1 and "NOT_PASS" in res.stdout and "skipped" in res.stdout
    runs.write_text(json.dumps({"workflow_runs": []}), encoding="utf-8")
    assert subprocess.run(base, capture_output=True, text=True).returncode == 2
    runs.write_text("{not json", encoding="utf-8")
    assert subprocess.run(base, capture_output=True, text=True).returncode == 2
    # a scorecard PASS claim for an uncertified SHA is a contradiction (exit 1)
    runs.write_text(json.dumps({"workflow_runs": [_run(n, conclusion="cancelled") for n in REQ]}), encoding="utf-8")
    card = tmp_path / "card.json"
    card.write_text(json.dumps({"exact_sha_ci": "PASS", "last_evidence_sha": SHA}), encoding="utf-8")
    res = subprocess.run(base + ["--scorecard", str(card)], capture_output=True, text=True)
    assert res.returncode == 1 and "SCORECARD_CONTRADICTION" in res.stdout


# --- BL-089: целевая платформа владельца обязана быть в требованиях ----------

def test_both_windows_workflows_are_required_for_certification():
    """Сертификат не выдаётся SHA, на котором продукт владельца не собирался.

    До BL-089 ни одно задание Windows не было обязательным, и сертификат мог
    быть выдан коммиту, который на целевой платформе владельца не собирался
    вовсе. Отсутствующий прогон не окрашен никак.
    """
    required = set(esc.DEFAULT_REQUIRED)
    assert "One-download Windows application" in required
    assert "Windows owner run — light, medium and super-long task" in required


def test_the_owner_scoreboard_is_required_too():
    """Готовность определяют владельческие сценарии, а не регрессия.

    Без этой строки сертификат выдавался бы SHA, на котором табло не
    считалось вовсе, — а отсутствующий прогон не окрашен никак и читается как
    «замечаний нет». Поведенческая половина уже есть выше
    (`test_a_missing_windows_run_blocks_certification` снимает ЛЮБОЕ требуемое
    имя), второй её экземпляр здесь заводить незачем.
    """
    assert "Owner scenarios (integrated, not unit tests)" in set(esc.DEFAULT_REQUIRED)


# Сторожа «каждое требуемое имя существует как задание» здесь НЕТ намеренно:
# он уже есть выше — test_default_required_names_match_workflow_files. Второй
# экземпляр того же правила — это ровно та дублирующая архитектура, которую
# запрещает директива, и я чуть не завёл её сам.
def test_a_missing_windows_run_blocks_certification():
    """Поведенческая пара к объявлению выше."""
    sha = "c" * 40
    runs = [_run(name, sha=sha)
            for name in esc.DEFAULT_REQUIRED
            if name != "One-download Windows application"]
    report = esc.certify(sha, runs)
    assert report["workflows"]["One-download Windows application"]["result"] == "MISSING"
    assert report["verdict"] != esc.CERTIFIED

    # Положительная половина: добавь недостающий прогон — и SHA сертифицируется.
    runs.append(_run("One-download Windows application", sha=sha))
    assert esc.certify(sha, runs)["verdict"] == esc.CERTIFIED


@pytest.mark.parametrize('fault', [
    'missing_jobs', 'zero_jobs', 'missing_evidence', 'incomplete', 'wrong_total',
    'duplicate_job', 'job_sha', 'job_run', 'job_attempt', 'envelope_sha',
    'envelope_run', 'envelope_attempt', 'missing_job_id', 'invalid_job_id',
    'invalid_run_id', 'invalid_attempt', 'malformed_job', 'bool_total',
])
def test_successful_run_requires_complete_bound_nonempty_jobs(fault):
    run = _run('root-ci')
    job = run['jobs'][0]
    evidence = run['jobs_evidence']
    if fault == 'missing_jobs':
        del run['jobs']
    elif fault == 'zero_jobs':
        run['jobs'] = []
        evidence['total_count'] = 0
    elif fault == 'missing_evidence':
        del run['jobs_evidence']
    elif fault == 'incomplete':
        evidence['complete'] = False
    elif fault == 'wrong_total':
        evidence['total_count'] = 2
    elif fault == 'duplicate_job':
        run['jobs'].append(dict(job))
        evidence['total_count'] = 2
    elif fault == 'job_sha':
        job['head_sha'] = OTHER
    elif fault == 'job_run':
        job['run_id'] += 1
    elif fault == 'job_attempt':
        job['run_attempt'] = 2
    elif fault.startswith('envelope_'):
        key = {'envelope_sha': 'head_sha', 'envelope_run': 'run_id',
               'envelope_attempt': 'run_attempt'}[fault]
        evidence[key] = OTHER if key == 'head_sha' else evidence[key] + 1
    elif fault == 'missing_job_id':
        del job['id']
    elif fault == 'invalid_job_id':
        job['id'] = True
    elif fault == 'invalid_run_id':
        run['id'] = True
    elif fault == 'invalid_attempt':
        run['run_attempt'] = 0
    elif fault == 'malformed_job':
        run['jobs'] = [None]
    elif fault == 'bool_total':
        evidence['total_count'] = True
    report = esc.certify(SHA, [run], ('root-ci',))
    assert report['verdict'] != esc.CERTIFIED, fault
    assert report['workflows']['root-ci']['result'] != 'PASS'


@pytest.mark.parametrize('status,conclusion', [
    ('completed', 'skipped'), ('completed', 'failure'), ('completed', 'cancelled'),
    ('completed', 'neutral'), ('completed', 'timed_out'), ('completed', None),
    ('queued', None), ('in_progress', 'success'), ('unknown', 'success'),
])
def test_job_must_itself_complete_successfully(status, conclusion):
    run = _run('root-ci')
    run['jobs'][0].update(status=status, conclusion=conclusion)
    report = esc.certify(SHA, [run], ('root-ci',))
    assert report['verdict'] == esc.NOT_CERTIFIED
    if status != 'completed':
        assert report['final'] is False


def test_new_attempt_cannot_inherit_previous_jobs():
    earlier = _run('root-ci', rid=40)
    later = _run('root-ci', rid=40, attempt=2)
    later['jobs_evidence'] = earlier['jobs_evidence']
    assert esc.certify(SHA, [earlier, later], ('root-ci',))['verdict'] != esc.CERTIFIED


def _mock_api(monkeypatch, responses):
    calls = []

    def open_request(request, timeout):
        calls.append(request.full_url)
        assert timeout == 30
        value = responses[len(calls) - 1]
        if isinstance(value, Exception):
            raise value
        return io.BytesIO(json.dumps(value).encode())

    monkeypatch.setattr(esc.urllib.request, 'urlopen', open_request)
    return calls


def test_fetch_binds_all_job_pages_to_exact_attempt(monkeypatch):
    run = _run('root-ci', rid=41, attempt=2)
    job = run.pop('jobs')[0]
    run.pop('jobs_evidence')
    calls = _mock_api(monkeypatch, [
        {'total_count': 1, 'workflow_runs': [run]},
        {'total_count': 2, 'jobs': [job]},
        {'total_count': 2, 'jobs': [{**job, 'id': job['id'] + 1, 'run_attempt': 2}]},
    ])
    fetched = esc.fetch_runs('owner/repo', SHA, None, per_page=1, required=('root-ci',))
    assert esc.certify(SHA, fetched, ('root-ci',))['verdict'] == esc.CERTIFIED
    assert len(calls) == 3
    assert '/actions/runs/41/attempts/2/jobs?' in calls[1]
    assert urllib.parse.parse_qs(urllib.parse.urlparse(calls[2]).query)['page'] == ['2']
    assert fetched[0]['jobs_evidence'] == {
        'run_id': 41, 'run_attempt': 2, 'head_sha': SHA, 'total_count': 2, 'complete': True}


@pytest.mark.parametrize('fault', ['missing_total', 'missing_list', 'wrong_type',
                                  'duplicate', 'truncated', 'changed_total', 'page_limit',
                                  'http_error', 'wrong_run', 'wrong_sha', 'wrong_attempt'])
def test_fetch_jobs_fails_closed_on_partial_or_unbound_evidence(monkeypatch, fault):
    run = _run('root-ci', rid=41, attempt=2)
    job = run.pop('jobs')[0]
    run.pop('jobs_evidence')
    first = {'total_count': 2, 'jobs': [job]}
    second = {'total_count': 2, 'jobs': [{**job, 'id': job['id'] + 1}]}
    max_pages = 5
    if fault == 'missing_total':
        first.pop('total_count')
    elif fault == 'missing_list':
        first.pop('jobs')
    elif fault == 'wrong_type':
        first['jobs'] = [None]
    elif fault == 'duplicate':
        second['jobs'] = [dict(job)]
    elif fault == 'truncated':
        second['jobs'] = []
    elif fault == 'changed_total':
        second['total_count'] = 3
    elif fault == 'page_limit':
        max_pages = 1
    elif fault == 'http_error':
        second = urllib.error.HTTPError('https://api.github.com/synthetic', 403, 'Forbidden', {}, None)
    elif fault == 'wrong_run':
        job['run_id'] += 1
    elif fault == 'wrong_sha':
        job['head_sha'] = OTHER
    elif fault == 'wrong_attempt':
        job['run_attempt'] = 1
    _mock_api(monkeypatch, [{'total_count': 1, 'workflow_runs': [run]}, first, second])
    with pytest.raises(esc.CertificationError):
        esc.fetch_runs('owner/repo', SHA, None, per_page=1, max_pages=max_pages, required=('root-ci',))


def test_fetch_does_not_certify_truncated_run_inventory(monkeypatch):
    _mock_api(monkeypatch, [{'total_count': 2, 'workflow_runs': [_run('root-ci')]}])
    with pytest.raises(esc.CertificationError, match='pagination'):
        esc.fetch_runs('owner/repo', SHA, None, per_page=1, max_pages=1)


def test_fetch_uses_only_latest_runs_jobs(monkeypatch):
    old = _run('root-ci', rid=10)
    latest = _run('root-ci', rid=11, number=2, attempt=3)
    job = latest['jobs'][0]
    for run in (old, latest):
        run.pop('jobs')
        run.pop('jobs_evidence')
    calls = _mock_api(monkeypatch, [
        {'total_count': 2, 'workflow_runs': [old, latest]},
        {'total_count': 1, 'jobs': [job]},
    ])
    fetched = esc.fetch_runs('owner/repo', SHA, None, required=('root-ci',))
    assert esc.certify(SHA, fetched, ('root-ci',))['verdict'] == esc.CERTIFIED
    assert len(calls) == 2 and '/runs/11/attempts/3/jobs?' in calls[1]


def test_fetch_ignores_unrelated_jobs_and_keeps_required_unfinished_status(monkeypatch):
    ready = _run('root-ci', rid=51)
    pending = _run('Core CI', rid=52, status='queued', conclusion=None)
    unrelated = _run('Release certification', rid=53, status='in_progress', conclusion=None)
    job = ready['jobs'][0]
    for run in (ready, pending, unrelated):
        run.pop('jobs')
        run.pop('jobs_evidence')
    calls = _mock_api(monkeypatch, [
        {'total_count': 3, 'workflow_runs': [ready, pending, unrelated]},
        {'total_count': 1, 'jobs': [job]},
    ])
    fetched = esc.fetch_runs('owner/repo', SHA, None, required=('root-ci', 'Core CI'))
    assert len(calls) == 2 and '/runs/51/attempts/1/jobs?' in calls[1]
    assert esc.certify(SHA, fetched, ('root-ci',))['verdict'] == esc.CERTIFIED
    blocked = esc.certify(SHA, fetched, ('root-ci', 'Core CI'))
    assert blocked['verdict'] == esc.NOT_CERTIFIED and blocked['final'] is False
    assert blocked['workflows']['Core CI']['result'] == 'NOT_FINAL'


@pytest.mark.parametrize('key', ['id', 'run_number', 'run_attempt'])
@pytest.mark.parametrize('value', [None, 0, True, '2'])
def test_malformed_new_run_cannot_be_discarded_in_favor_of_old_green(key, value):
    old = _run('root-ci', rid=11)
    new = _run('root-ci', rid=12, number=2, conclusion='failure')
    new[key] = value
    report = esc.certify(SHA, [old, new], ('root-ci',))
    assert report['verdict'] != esc.CERTIFIED
    assert report['workflows']['root-ci']['result'] == 'INSUFFICIENT_RUN_EVIDENCE'


def test_fetch_rejects_malformed_relevant_order_before_fetching_jobs(monkeypatch):
    old = _run('root-ci', rid=11)
    new = _run('root-ci', rid=12, number=2, conclusion='failure')
    del new['run_number']
    calls = _mock_api(monkeypatch, [{'total_count': 2, 'workflow_runs': [old, new]}])
    with pytest.raises(esc.CertificationError, match='run_number'):
        esc.fetch_runs('owner/repo', SHA, None, required=('root-ci',))
    assert len(calls) == 1


def test_conflicting_duplicate_saved_run_cannot_keep_the_first_green():
    green = _run('root-ci')
    failed = {**green, 'conclusion': 'failure'}
    for runs in ([green, failed], [failed, green]):
        report = esc.certify(SHA, runs, ('root-ci',))
        assert report['verdict'] != esc.CERTIFIED
        assert report['workflows']['root-ci']['result'] == 'INSUFFICIENT_RUN_EVIDENCE'


def test_saved_run_pages_must_match_declared_total_and_unique_ids():
    first, second = _run('root-ci', rid=11), _run('Core CI', rid=12)
    pages = [{'total_count': 2, 'workflow_runs': [first]},
             {'total_count': 2, 'workflow_runs': [second]}]
    assert esc._runs_from_payload(pages) == [first, second]
    with pytest.raises(esc.CertificationError, match='incomplete'):
        esc._runs_from_payload(pages[0])
    with pytest.raises(esc.CertificationError, match='duplicate'):
        esc._runs_from_payload([pages[0], pages[0]])
    with pytest.raises(esc.CertificationError, match='changed'):
        esc._runs_from_payload([pages[0], {**pages[1], 'total_count': 3}])
    with pytest.raises(esc.CertificationError, match='total_count'):
        esc._runs_from_payload([pages[0], {'workflow_runs': [second]}])


def test_owner_release_scenario_79_synthetic_fixture_obeys_job_contract(tmp_path, monkeypatch):
    scenario_dir = Path(__file__).resolve().parent / 'owner_scenarios'
    monkeypatch.syspath_prepend(str(scenario_dir))
    from scenario_runner import RunContext, PRODUCT_CONTRACTS
    from scn_18_recovery_release import os79_certification_refuses_a_missing_required_run

    context = RunContext(SimpleNamespace(depth=PRODUCT_CONTRACTS), tmp_path, None)
    os79_certification_refuses_a_missing_required_run(context)
    checks = [check.to_report() for check in context.checks]
    assert checks and all(check['ok'] for check in checks), checks
    assert {'positive', 'negative'} <= {check['kind'] for check in checks}
