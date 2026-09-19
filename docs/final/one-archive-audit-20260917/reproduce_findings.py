#!/usr/bin/env python3
"""Isolated local audit probes. NO Windows/product/model acceptance is claimed.

Executes exact upstream stdlib-only validator sources copied via GitHub connector.
Git blob identities are verified before import. All reports/subprocess results
are synthetic fixtures in TemporaryDirectory; no credentials/network are used.
Run from this directory: python reproduce_findings.py
"""
from pathlib import Path
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import subprocess
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
SHA = 'c3f41cc687734f75c2a479c232fba4edf61b6b4d'
BLOBS = {
    'astra6_freeze': '44b8d2affe740d8e8341268c554a689be27896a9',
    'bundle_evening_test': 'd8aeb6d3c136c182431b1ee7a01b5b784844fb5e',
}

def load(name):
    path = ROOT / 'source' / (name + '.py')
    data = path.read_bytes()
    blob = hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest()
    assert blob == BLOBS[name], (path, blob, BLOBS[name])
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

freeze = load('astra6_freeze')
evening = load('bundle_evening_test')
results = []

def reports():
    models = ['synthetic/model-a:free', 'synthetic/model-b:free']
    return {
        'bundle-acceptance.json': {'expected_sha': SHA, 'status': 'PASS', 'problems': [],
                                  'details': {'archive_sha256': 'a' * 64}},
        'ui-sweep.json': {'source_sha': SHA, 'status': 'PASS', 'pages': 30, 'counts': {'works': 40}},
        'live-model.json': {'source_sha': SHA, 'status': 'PASS', 'identity': {'source_sha': SHA},
                            'models': models,
                            'tasks': [dict(source_sha=SHA, model=model, case=case,
                                           task_id=f'{model}:{case}', status='PASS', restart_persistence='PASS')
                                      for model in models
                                      for case in ['arithmetic', 'structured_data', 'instruction_following']]},
    }

def run_freeze(name, mutation=None, count=40, foreign_xml_sha=False, expected='FROZEN'):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        data = reports()
        if mutation:
            mutation(data)
        for filename, value in data.items():
            (root / filename).write_text(json.dumps(value), encoding='utf-8')
        xml = ET.Element('testsuite', tests=str(count))
        if foreign_xml_sha:
            props = ET.SubElement(xml, 'properties')
            ET.SubElement(props, 'property', name='source_sha', value='0' * 40)
        for i in range(count):
            ET.SubElement(xml, 'testcase', classname='synthetic.owner', name=f'case_{i}')
        (root / 'results.xml').write_bytes(ET.tostring(xml))
        out = freeze.build_manifest(root, SHA, ['success', 'success'])
        assert out['status'] == expected, out
        results.append({'probe': name, 'scope': 'synthetic_validator_probe',
                        'observed': out['status'], 'tests': count, 'blockers': out['blockers']})

run_freeze('control: 40 cases + 2 models x 3 tasks accepted')
run_freeze('OA-02: only 13 tests still accepted by final aggregator', count=13)
run_freeze('OA-02: 12 tests correctly rejected (negative control)', count=12, expected='BLOCKED')
run_freeze('OA-02: 6 identical rows of one model/one task accepted',
           lambda d: d['live-model.json'].update(tasks=[copy.deepcopy(d['live-model.json']['tasks'][0]) for _ in range(6)]))
run_freeze('OA-02: XML explicitly names different SHA but is accepted', foreign_xml_sha=True)
run_freeze('control: live model FAIL blocks freeze',
           lambda d: d['live-model.json'].update(status='FAIL'), expected='BLOCKED')
run_freeze('control: JSON wrong source SHA blocks freeze',
           lambda d: d['ui-sweep.json'].update(source_sha='0'*40), expected='BLOCKED')


def run_evening(name, doctor_stdout='{"checks": []}', doctor_rc=0, missing=False, skip=False,
                expected='PASS'):
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        support = home / 'app-support'
        support.mkdir()
        if not missing:
            (support / 'bossman_doctor.py').touch()
        (support / 'verify_installed_product.py').touch()
        (home / 'MANIFEST.json').write_text(json.dumps({'source_sha': SHA,
            'artifact': 'SYNTHETIC-NOT-A-PRODUCT', 'required_downloads': []}), encoding='utf-8')
        evidence = home / 'evidence'
        evidence.mkdir()
        evening.HOME, evening.SUPPORT = home, support
        evening._evidence_root = lambda _: evidence
        evening._console_utf8 = lambda: None
        def fake_run(args, **kwargs):
            if Path(args[1]).name == 'bossman_doctor.py':
                return subprocess.CompletedProcess(args, doctor_rc, doctor_stdout, '')
            assert Path(args[1]).name == 'verify_installed_product.py'
            Path(args[args.index('--out') + 1]).write_text(json.dumps({'status': 'PASS', 'source_sha': SHA}), encoding='utf-8')
            return subprocess.CompletedProcess(args, 0, 'SYNTHETIC VERIFIER PASS', '')
        evening._run = fake_run
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = evening.main(['--skip-doctor'] if skip else [])
        out = json.loads((evidence / 'OWNER_EVENING_RESULT.json').read_text())
        assert out['verdict'] == expected, out
        results.append({'probe': name, 'scope': 'synthetic_owner_diagnostic_probe',
                        'observed': out['verdict'], 'doctor': out['doctor'], 'exit_code': rc})

run_evening('control: readable successful doctor + verifier accepted')
run_evening('OA-01: doctor missing still yields overall PASS', missing=True)
run_evening('OA-01: doctor invalid JSON still yields overall PASS', doctor_stdout='NOT JSON')
run_evening('OA-01: doctor exit 9 with empty object still yields overall PASS', doctor_rc=9, doctor_stdout='{}')
run_evening('OA-01: explicitly skipped doctor still yields overall PASS', skip=True)
run_evening('control: doctor BLOCKED does not yield overall PASS',
            doctor_stdout='{"checks":[{"status":"BLOCKED","name":"model"}]}', expected='OWNER_REQUIRED')

out = {'source_sha': SHA, 'verified_git_blobs': BLOBS,
       'scope': 'LOCAL SYNTHETIC VALIDATOR/DIAGNOSTIC REPRODUCTIONS ONLY',
       'not_performed': ['Windows execution', 'archive byte download/hash', 'live model calls', 'full test suites'],
       'probe_count': len(results), 'results': results}
(ROOT / 'reproduction_results.json').write_text(json.dumps(out, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
print(json.dumps(out, indent=2, ensure_ascii=False))
