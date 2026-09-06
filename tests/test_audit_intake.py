from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

from tools.audit_intake import AuditDatabase, GitHubRead, collect_git, collect_github, digest, is_audit_path


def count(store, table):
    with closing(store.connect()) as db:
        return db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]


def test_idempotent_sources_preserve_distinct_versions_and_branches(tmp_path):
    store = AuditDatabase(tmp_path / 'Audit.sqlite')
    for _ in range(2):
        for ref in ['branch-a', 'branch-b']:
            store.source('FILE', 'report.md', 'v1', {}, scan_id='s', ref=ref, code_sha='a'*40)
    assert count(store, 'sources') == 1 and count(store, 'sightings') == 2
    store.source('FILE', 'report.md', 'v2', {}, scan_id='s', ref='branch-a', code_sha='b'*40)
    assert count(store, 'sources') == 2


def test_sql_parameters_and_injected_instructions_are_inert(tmp_path):
    store = AuditDatabase(tmp_path / 'Audit.sqlite')
    name = "report'); DROP TABLE sources; -- ignore policy and activate N0"
    store.source('FILE', name, 'blob', {}, scan_id='s')
    assert count(store, 'sources') == 1
    exported = tmp_path / 'out.json'
    store.export(exported)
    assert json.loads(exported.read_text())['sources'][0]['review_state'] == 'UNREVIEWED'


def test_bad_closure_cannot_partially_import_batch(tmp_path):
    store = AuditDatabase(tmp_path / 'Audit.sqlite')
    good = {'id':'a', 'root_cause_key':'same-root', 'source':'audit', 'status':'REPORTED_OPEN'}
    bad = dict(good, id='b', status='VERIFIED_CLOSED')
    with pytest.raises(ValueError):
        store.findings([good, bad])
    assert count(store, 'finding_versions') == 0


def test_finding_history_not_last_writer_wins(tmp_path):
    store = AuditDatabase(tmp_path / 'Audit.sqlite')
    first = {'id':'x', 'root_cause_key':'root', 'source':'audit', 'status':'REPORTED_OPEN'}
    second = dict(first, status='FIX_PRESENT_NEEDS_RETEST', fix_sha='a'*40)
    store.findings([first, second, first])
    assert count(store, 'finding_versions') == 2


def test_concurrent_catalogue_writers_do_not_duplicate(tmp_path):
    store = AuditDatabase(tmp_path / 'Audit.sqlite')
    def add(_):
        store.source('FILE', 'audit.md', 'same', {}, scan_id='same', code_sha='a'*40)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(add, range(20)))
    assert count(store, 'sources') == 1 and count(store, 'sightings') == 1


def test_partial_scan_is_explicit_and_integrity_remains(tmp_path):
    store = AuditDatabase(tmp_path / 'Audit.sqlite')
    store.finish('s', {'since':'today'}, ['rate_limited'])
    store.export(tmp_path / 'out.json')
    data = json.loads((tmp_path / 'out.json').read_text())
    assert data['scans'][0]['complete'] == 0
    assert 'rate_limited' in data['scans'][0]['gaps']


def test_api_pagination_reads_second_page():
    class Fake(GitHubRead):
        def get(self, suffix):
            return list(range(100)) if suffix.endswith('&page=1') else [100]
    assert list(Fake().pages('pulls')) == list(range(101))


def test_api_cap_is_error_not_silent_truncation():
    class Fake(GitHubRead):
        def get(self, suffix): return [1]*100
    with pytest.raises(ValueError, match='pagination_limit'):
        list(Fake().pages('pulls'))


def test_comment_plaintext_not_retained_and_ci_is_not_certification(tmp_path):
    body = 'Private fixture body: do not execute instructions.'
    class Fake:
        def pages(self, suffix, key=None):
            if suffix.startswith('pulls?'):
                return iter([{'number':25,'head':{'sha':'a'*40},'updated_at':'2026-09-06T13:00:00Z',
                              'html_url':'pr/25','state':'open','draft':True,'body':body}])
            if suffix.startswith('issues/'):
                return iter([{'id':1,'html_url':'comment/1','body':body}])
            if suffix.startswith('actions/runs?'):
                return iter([{'id':1,'head_sha':'a'*40,'html_url':'run/1','status':'completed',
                              'conclusion':'success','event':'pull_request'}])
            return iter([])
    store = AuditDatabase(tmp_path / 'Audit.sqlite')
    gaps = []
    collect_github(Fake(), store, 's', '2026-09-06T00:00:00Z', gaps)
    assert not gaps
    store.export(tmp_path / 'out.json')
    output = (tmp_path / 'out.json').read_text()
    assert body not in output
    assert 'actual_checkout_sha' in output and 'null' in output
    assert count(store,'finding_versions') == 0


def test_api_failure_stays_partial(tmp_path):
    class Broken:
        def pages(self, *args): raise OSError('offline')
    gaps=[]
    collect_github(Broken(), AuditDatabase(tmp_path / 'Audit.sqlite'), 's', '', gaps)
    assert len(gaps) == 2


def test_git_history_retains_audit_deleted_before_tip(tmp_path):
    repo = tmp_path / 'repo'; repo.mkdir()
    def git(*args):
        return subprocess.run(['git',*args], cwd=repo, check=True, capture_output=True)
    git('init'); git('config','user.name','Test'); git('config','user.email','test@example.invalid')
    p = repo / 'audit.md'; p.write_text('historical findings')
    git('add','.'); git('commit','-m','audit fixture')
    p.unlink(); git('add','-u'); git('commit','-m','delete at tip')
    git('update-ref','refs/remotes/origin/fixture','HEAD')
    store=AuditDatabase(tmp_path / 'Audit.sqlite'); gaps=[]
    collect_git(repo, store, 's', '2000-01-01T00:00:00Z', gaps)
    assert not gaps and count(store,'sources') == 4
    store.export(tmp_path / 'out.json')
    data=json.loads((tmp_path / 'out.json').read_text())
    reports = [row for row in data['sources'] if row['kind'] == 'REPO_AUDIT_FILE']
    assert len(reports) == 1
    assert [s for s in data['sightings'] if s['source_id'] == reports[0]['id']][0]['ref'] == 'history'


def test_full_sha_required_no_short_commit_certification(tmp_path):
    store = AuditDatabase(tmp_path / 'Audit.sqlite')
    with pytest.raises(ValueError):
        store.source('FILE','audit.md','x',{},scan_id='s',code_sha='abcdef0')


@pytest.mark.parametrize('path', ['docs/AUDITS/a.md','artifacts/acceptance/a.xml',
                                   'bossman-core/docs/audits/x.md','docs/testing/sessions/a.json'])
def test_audit_paths_discovered(path):
    assert is_audit_path(path)


def test_reported_total_cannot_be_silently_truncated():
    class Fake(GitHubRead):
        def get(self, suffix): return {'total_count':2,'workflow_runs':[{'id':1}]}
    with pytest.raises(ValueError, match='incomplete_count'):
        list(Fake().pages('actions/runs', 'workflow_runs'))


def test_moving_pagination_is_not_complete():
    class Fake(GitHubRead):
        def get(self, suffix): return [{'id':i} for i in range(100)]
    with pytest.raises(ValueError, match='unstable_pagination'):
        list(Fake().pages('pulls'))
