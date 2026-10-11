"""Archive/identity/lifecycle contracts; fake ZIPs are NEVER run as products."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def driver():
    spec = importlib.util.spec_from_file_location('installed_perf_contracts', ROOT / 'tools/installed_responsiveness_probe.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def payload(tmp_path):
    root = tmp_path / 'App with spaces'
    archive = tmp_path / 'app.zip'
    contents = {'MANIFEST.json': b'{}', 'runtime/python.exe': b'NOT AN EXECUTABLE', 'Start-Bossman.cmd': b'NOT RUN'}
    with zipfile.ZipFile(archive, 'w') as z:
        for name, content in contents.items():
            z.writestr('BOSSMAN-Windows-x64-test/' + name, content)
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    return root, archive, hashlib.sha256(archive.read_bytes()).hexdigest()


def test_exact_payload_and_size_are_bound(driver, payload):
    root, archive, digest = payload
    report = driver.verify_payload(root, archive, digest)
    assert report['archive_sha256'] == digest
    assert report['archive_bytes'] == archive.stat().st_size
    assert report['verified_files'] == 3


@pytest.mark.parametrize('mutation', ['modified', 'extra', 'missing'])
def test_modified_extracted_payload_refused(driver, payload, mutation):
    root, archive, digest = payload
    if mutation == 'modified':
        (root / 'runtime/python.exe').write_bytes(b'X' * len(b'NOT AN EXECUTABLE'))
    elif mutation == 'extra':
        (root / 'runtime/unreviewed.pth').write_text('unreviewed')
    else:
        (root / 'Start-Bossman.cmd').unlink()
    with pytest.raises(ValueError):
        driver.verify_payload(root, archive, digest)


def test_archive_hash_mismatch_refused(driver, payload):
    root, archive, _ = payload
    with pytest.raises(ValueError, match='SHA-256 mismatch'):
        driver.verify_payload(root, archive, '0' * 64)


def test_actions_wrapper_is_not_application(driver, payload):
    root, archive, _ = payload
    wrapper = archive.with_name('wrapper.zip')
    with zipfile.ZipFile(wrapper, 'w') as z:
        z.writestr('app.zip', archive.read_bytes())
    with pytest.raises(ValueError):
        driver.verify_payload(root, wrapper, driver.digest_file(wrapper))


@pytest.mark.parametrize('name', ['BOSSMAN/../outside', 'BOSSMAN/runtime/C:bad', '/absolute/file', 'BOSSMAN\\file', 'BOSSMAN/trailing.'])
def test_unsafe_member_refused_without_extraction(driver, payload, name):
    root, archive, _ = payload
    with zipfile.ZipFile(archive, 'a') as z:
        z.writestr(name, b'x')
    with pytest.raises(ValueError, match='unsafe'):
        driver.verify_payload(root, archive, driver.digest_file(archive))
    assert not (root.parent / 'outside').exists()


def test_case_alias_refused(driver, payload):
    root, archive, _ = payload
    with zipfile.ZipFile(archive, 'a') as z:
        z.writestr('BOSSMAN-Windows-x64-test/RUNTIME/PYTHON.EXE', b'x')
    with pytest.raises(ValueError, match='case-aliased'):
        driver.verify_payload(root, archive, driver.digest_file(archive))


@pytest.mark.parametrize('platform,isolated', [('linux', True), ('win32', False)])
def test_other_platform_or_nonisolated_interpreter_refused(driver, payload, platform, isolated):
    root, _, _ = payload
    with pytest.raises(ValueError):
        driver.require_runtime(root, platform=platform, isolated=isolated, executable=root / 'runtime/python.exe')


def test_wrong_interpreter_refused_even_on_windows(driver, payload, tmp_path):
    root, _, _ = payload
    other = tmp_path / 'other-python.exe'
    other.write_bytes(b'fake')
    with pytest.raises(ValueError, match='exact application'):
        driver.require_runtime(root, platform='win32', isolated=True, executable=other)
    driver.require_runtime(root, platform='win32', isolated=True, executable=root / 'runtime/python.exe')


def test_environment_does_not_inherit_provider_or_owner_profile(driver, payload, tmp_path, monkeypatch):
    root, _, _ = payload
    monkeypatch.setenv('BCC_DATA_DIR', 'original-owner-data')
    monkeypatch.setenv('HTTP_PROXY', 'http://invalid.test')
    monkeypatch.setenv('EXAMPLE_API_KEY', 'synthetic-placeholder')
    before = dict(os.environ)
    with driver.private_environment(root, tmp_path):
        assert os.environ['BCC_DATA_DIR'] == str(tmp_path / 'data')
        assert 'HTTP_PROXY' not in os.environ
        assert 'EXAMPLE_API_KEY' not in os.environ
        assert os.environ['PLAYWRIGHT_BROWSERS_PATH'] == str(root / 'browser')
    assert dict(os.environ) == before


def snap(at, *rows, complete=True):
    return {'at': at, 'complete': complete, 'processes': [dict(pid=p, created=c, cpu=t) for p, c, t in rows]}


def test_cpu_lifetime_sum_never_subtracts_vanished_child(driver):
    before = snap(10, (1, 1, 5), (2, 2, 50))
    after = snap(20, (1, 1, 6))
    assert driver.cpu_percent_between(before, after) is None


def test_pid_reuse_and_missing_access_cannot_pass(driver):
    before = snap(10, (1, 1, 5))
    assert driver.cpu_percent_between(before, snap(20, (1, 3, 6))) is None
    assert driver.cpu_percent_between(before, snap(20, (1, 1, 6), complete=False)) is None
    assert driver.cpu_percent_between(before, snap(20, (1, 1, 4))) is None
    assert driver.cpu_percent_between(before, snap(10, (1, 1, 6))) is None


def test_stable_cpu_pair_uses_all_processes(driver):
    before = snap(10, (1, 1, 5), (2, 2, 50))
    after = snap(20, (1, 1, 6), (2, 2, 52))
    assert driver.cpu_percent_between(before, after) == 30


def test_atomic_checkpoint_preserves_previous_on_nonfinite_json(driver, tmp_path):
    out = tmp_path / 'checkpoint.json'
    driver.atomic_report(out, {'completed': False, 'stage': 'navigation'})
    with pytest.raises(ValueError):
        driver.atomic_report(out, {'bad': float('nan')})
    assert json.loads(out.read_text()) == {'completed': False, 'stage': 'navigation'}
    assert not list(tmp_path.glob('*.tmp'))
    driver.atomic_report(out, {'completed': True})
    assert json.loads(out.read_text()) == {'completed': True}


def test_short_soak_cannot_meet_hour_budget(driver):
    assert driver.MIN_SOAK_SECONDS == 3600
    assert driver.MAX_SOAK_SECONDS == 7200


def test_real_child_process_is_included_in_snapshot(driver, tmp_path):
    # Real portable process test, NOT a Windows/Bossman performance run.
    ready = tmp_path / 'ready'
    child = subprocess.Popen([sys.executable, '-c',
        'import pathlib,sys,time; payload=bytearray(16*1024*1024); '
        'pathlib.Path(sys.argv[1]).write_text("ready"); time.sleep(20)', str(ready)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            if child.poll() is not None:
                raise AssertionError('test child exited before readiness')
            time.sleep(.02)
        assert ready.exists()
        sampler = driver.TreeSampler(os.getpid())
        sample = sampler.sample()
        rows = [row for row in sample['processes'] if row['pid'] == child.pid]
        assert len(rows) == 1
        assert rows[0]['rss'] >= 16 * 1024 * 1024
        assert sample['rss_mib'] >= rows[0]['rss'] / (1024 * 1024)
    finally:
        child.terminate()
        child.wait(timeout=10)


def test_new_output_required_before_any_launch(driver, payload, tmp_path):
    root, archive, digest = payload
    output = tmp_path / 'existing.json'
    output.write_text('original owner evidence')
    with pytest.raises(ValueError, match='NEW evidence'):
        driver.run_installed(None, root=root, archive=archive, expected_hash=digest,
                             expected_sha='a' * 40, output=output, soak_seconds=0)
    assert output.read_text() == 'original owner evidence'


@pytest.mark.parametrize('seconds,cleanup_failure', [(0, False), (1, False), (3600, False), (0, True)])
def test_orchestration_truth_and_cleanup_with_synthetic_runtime(
    driver, payload, tmp_path, monkeypatch, seconds, cleanup_failure
):
    """Synthetic wiring/clock only: NOT a Windows, browser or 1-hour run."""
    from types import ModuleType, SimpleNamespace as NS
    spec = importlib.util.spec_from_file_location('synthetic_perf_probe', ROOT / 'tools/responsiveness_probe.py')
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    root, archive, digest = payload
    output = tmp_path / 'synthetic-evidence.json'
    ui = root / 'runtime/ui/pages'
    ui.mkdir(parents=True)
    (ui / 'index.js').write_text('synthetic registry')
    events, clock = [], [1.0]
    owner = ModuleType('bcc.owner_acceptance')
    owner.__file__ = str(root / 'runtime/bcc/owner_acceptance.py')
    owner.installed_identity = lambda: {'source_sha': 'a' * 40}
    owner.free_port = lambda: 13123
    process = NS(poll=lambda: None)
    client = NS(close=lambda: events.append('client_closed'))
    def launch(data, port, log):
        assert 'EXAMPLE_API_KEY' not in os.environ
        (data / 'token').write_text('synthetic-token')
        events.append('launch')
        return process
    owner.launch = launch
    owner.connect = lambda *args: (client, {})
    owner.stop = lambda p: events.append('stopped')
    bcc = ModuleType('bcc')
    bcc.owner_acceptance = owner
    auth = ModuleType('bcc.auth')
    auth.TOKEN_FILE = 'token'
    runtime = ModuleType('bcc.browser_runtime')
    def browser_path(*, headless):
        assert headless is False, '--headed must not select the headless shell'
        return str(root / 'browser/chrome.exe')
    runtime.chromium_executable = browser_path
    config = ModuleType('bcc.config')
    config.Settings = lambda **kwargs: NS(ui_dir=ui.parent, database_url='synthetic-db')
    class Page:
        def goto(self, *args, **kwargs): pass
        def fill(self, *args): pass
        def click(self, *args): pass
        def wait_for_selector(self, *args, **kwargs): pass
        def evaluate(self, *args): return 'not-next-route'
        def wait_for_timeout(self, millis): clock[0] += millis / 1000
    page = Page()
    context = NS(route=lambda *args: events.append('egress_route_installed'), new_page=lambda: page)
    browser = NS(new_context=lambda **kwargs: context, close=lambda: events.append('browser_closed'))
    class PW:
        chromium = NS(launch=lambda **kwargs: browser)
        def __enter__(self): return self
        def __exit__(self, *args): pass
    pw_module = ModuleType('playwright.sync_api')
    pw_module.sync_playwright = PW
    for key, value in {'bcc': bcc, 'bcc.owner_acceptance': owner, 'bcc.auth': auth,
                       'bcc.browser_runtime': runtime, 'bcc.config': config,
                       'playwright.sync_api': pw_module}.items():
        monkeypatch.setitem(sys.modules, key, value)
    monkeypatch.setattr(driver, 'require_runtime', lambda *args, **kwargs: None)
    # Fake installed bytes are never executed; archive verification has its own real tests.
    monkeypatch.setattr(driver, 'verify_payload', lambda *args: {'archive_sha256': digest})
    monkeypatch.setattr(driver, 'time', NS(monotonic=lambda: clock[0]))
    class Sampler:
        def __init__(self, pid): pass
        def sample(self):
            clock[0] += .001
            return {'at': clock[0], 'complete': True, 'errors': [], 'rss_mib': 100,
                    'processes': [{'pid': 100, 'created': 1, 'rss': 104857600, 'cpu': clock[0] * .001}]}
    monkeypatch.setattr(driver, 'TreeSampler', Sampler)
    class Walker:
        index = 0
        def __init__(self, *args): pass
        def park(self, **kwargs): pass
        def step(self): return 1.0
        def goto(self, *args, **kwargs): return 1.0
    monkeypatch.setattr(probe, 'Walker', Walker)
    def cleanup(p, stop):
        events.append('cleanup')
        if cleanup_failure:
            raise RuntimeError('synthetic cleanup refusal')
        stop(p)
    monkeypatch.setattr(probe, '_load', lambda name, path: (
        NS(stop_owned_process_tree=cleanup) if name.endswith('cleanup') else NS(page_routes=lambda src: ['a', 'b'])))
    monkeypatch.setattr(probe, '_seed_gallery', lambda data, db, count, offset: events.append(('seed', count, offset)))
    monkeypatch.setenv('EXAMPLE_API_KEY', 'synthetic-placeholder')
    result = driver.run_installed(probe, root=root, archive=archive, expected_hash=digest,
                                  expected_sha='a' * 40, output=output, soak_seconds=seconds, headed=True)
    assert json.loads(output.read_text()) == result
    assert result['release_ready'] is False
    assert result['binding']['harness_sha'] is None
    assert result['measurement_executed'] is True
    assert len(result['navigation_samples_ms']) == 20
    assert ('seed', 100, 0) in events and ('seed', 900, 100) in events
    assert events.index('browser_closed') < events.index('client_closed') < events.index('cleanup')
    assert os.environ['EXAMPLE_API_KEY'] == 'synthetic-placeholder'
    rows = {row['metric']: row for row in result['lines']}
    assert rows['cold_start_to_interactive_s']['verdict'] == 'OWNER_REQUIRED'
    assert rows['warm_start_to_interactive_s']['verdict'] == 'OWNER_REQUIRED'
    if cleanup_failure:
        assert result['verdict'] == 'FAIL' and result['completed'] is False
    else:
        assert result['completed'] is True
        if seconds < 3600:
            assert rows['soak_rss_growth_pct']['measured'] is None
            assert rows['soak_idle_cpu_pct_of_one_core']['verdict'] == 'INSUFFICIENT_EVIDENCE'
        else:
            assert rows['soak_rss_growth_pct']['verdict'] == 'PASS'
            assert rows['soak_idle_cpu_pct_of_one_core']['verdict'] == 'PASS'
            assert result['verdict'] == 'OWNER_REQUIRED'
