"""PREP-08: installed backend/browser diagnostics, not native desktop acceptance.

Invoked by responsiveness_probe.py with the bundle's Python -I. No source BCC,
owner profile, provider credentials, model calls, or rebuild of accepted bytes.
Cold/warm native desktop start remains OWNER_REQUIRED. A short run cannot pass
an hour-long soak budget. See docs/final/owner-prep-20260920/SEARCH_REPAIR_RU.md.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile
import time
import uuid
import zipfile

MIN_SOAK_SECONDS = 3600.0
MAX_SOAK_SECONDS = 7200.0


def digest_stream(stream) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        digest.update(block)
    return digest.hexdigest()


def digest_file(path: Path) -> str:
    with path.open('rb') as stream:
        return digest_stream(stream)


def verify_payload(root: Path, archive: Path, expected_hash: str) -> dict:
    """Read-only: compare EVERY extracted file to a hash-pinned application ZIP.

    Does not extract untrusted paths. Rejects Actions wrappers, duplicate/case
    aliases, symlinks, missing/extra files, and modified payloads before imports.
    """
    if re.fullmatch(r'[0-9a-f]{64}', expected_hash or '') is None:
        raise ValueError('expected application SHA-256 must be 64 lowercase hex digits')
    root = root.resolve(strict=True)
    archive = archive.resolve(strict=True)
    if not root.is_dir() or archive.is_relative_to(root):
        raise ValueError('archive must be outside the extracted application directory')
    with archive.open('rb') as raw:
        if digest_stream(raw) != expected_hash:
            raise ValueError('application archive SHA-256 mismatch')
        raw.seek(0)
        with zipfile.ZipFile(raw) as bundle:
            entries = bundle.infolist()
            if not entries or len(entries) > 50000 or sum(i.file_size for i in entries) > 8 * 1024**3:
                raise ValueError('unexpected application archive size or entry count')
            files, prefixes, aliases = {}, set(), set()
            for info in entries:
                name = info.filename
                path = PurePosixPath(name)
                if ('\\' in name or ':' in name or path.is_absolute() or len(path.parts) < 2
                        or any(p in ('.', '..') or p.endswith((' ', '.')) for p in path.parts)
                        or stat.S_ISLNK(info.external_attr >> 16)):
                    raise ValueError('unsafe archive path')
                prefixes.add(path.parts[0])
                rel = PurePosixPath(*path.parts[1:]).as_posix()
                folded = rel.casefold()
                if folded in aliases:
                    raise ValueError('duplicate or case-aliased archive entry')
                aliases.add(folded)
                if not info.is_dir():
                    files[rel] = info
            if len(prefixes) != 1 or not {'MANIFEST.json', 'runtime/python.exe', 'Start-Bossman.cmd'} <= files.keys():
                raise ValueError('not a standalone application ZIP (possibly an Actions wrapper)')
            actual = set()
            for candidate in root.rglob('*'):
                if candidate.is_symlink() or (hasattr(candidate, 'is_junction') and candidate.is_junction()):
                    raise ValueError('links/junctions are not accepted inside the installed payload')
                if candidate.is_file():
                    actual.add(candidate.relative_to(root).as_posix())
            if actual != set(files):
                raise ValueError('extracted application has missing or extra files')
            for relative, info in files.items():
                target = root.joinpath(*PurePosixPath(relative).parts)
                if not target.resolve(strict=True).is_relative_to(root):
                    raise ValueError('installed path escapes application root')
                if target.stat().st_size != info.file_size:
                    raise ValueError('installed file size differs from application ZIP')
                with bundle.open(info) as member:
                    if digest_stream(member) != digest_file(target):
                        raise ValueError('installed file bytes differ from application ZIP')
        size = raw.seek(0, 2)
    return {'archive_sha256': expected_hash, 'archive_bytes': size,
            'archive_name': archive.name, 'verified_files': len(files)}


def require_runtime(root: Path, *, platform: str, isolated: bool, executable: Path) -> None:
    if platform != 'win32':
        raise ValueError('installed Windows diagnostics require Windows')
    if not isolated or not executable.samefile(root / 'runtime/python.exe'):
        raise ValueError('run with the exact application runtime/python.exe -I')


@contextlib.contextmanager
def private_environment(root: Path, work: Path):
    """Do not inherit BCC routes, cloud keys, proxies, or the owner's data path."""
    original = os.environ.copy()
    keep = {key: original[key] for key in ('SystemRoot', 'WINDIR', 'COMSPEC', 'PATHEXT') if key in original}
    keep.update(TEMP=str(work), TMP=str(work), LOCALAPPDATA=str(work), APPDATA=str(work),
                HOME=str(work), USERPROFILE=str(work), BCC_DATA_DIR=str(work / 'data'),
                BCC_TOKEN_STDOUT='0', PLAYWRIGHT_BROWSERS_PATH=str(root / 'browser'),
                PYTHONDONTWRITEBYTECODE='1')
    system32 = str(Path(original.get('SystemRoot', r'C:\Windows')) / 'System32')
    keep['PATH'] = os.pathsep.join((str(root / 'runtime'), str(root / 'media'), system32))
    os.environ.clear()
    os.environ.update(keep)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)


class TreeSampler:
    """Track only this probe's processes, including already-seen detached children.

    The observer and Playwright driver are INCLUDED, not silently subtracted.
    Process identity is (pid, create_time), never PID alone. This is sampled
    accounting, not ETW: an unobserved short-lived child is not covered.
    """
    def __init__(self, root_pid: int):
        import psutil
        self.psutil = psutil
        self.root = psutil.Process(root_pid)
        self.tracked = {(self.root.pid, self.root.create_time()): self.root}

    def sample(self) -> dict:
        errors, rows = [], []
        for identity, proc in list(self.tracked.items()):
            try:
                if not proc.is_running() or proc.create_time() != identity[1]:
                    self.tracked.pop(identity, None)
                    continue
                for child in proc.children(recursive=True):
                    self.tracked.setdefault((child.pid, child.create_time()), child)
            except self.psutil.Error as exc:
                errors.append(type(exc).__name__)
        for identity, proc in list(self.tracked.items()):
            try:
                with proc.oneshot():
                    if not proc.is_running() or proc.create_time() != identity[1]:
                        raise self.psutil.NoSuchProcess(proc.pid)
                    cpu = proc.cpu_times()
                    rows.append({'pid': identity[0], 'created': identity[1],
                                 'rss': proc.memory_info().rss, 'cpu': cpu.user + cpu.system})
            except self.psutil.Error as exc:
                errors.append(type(exc).__name__)
        root_present = any(r['pid'] == self.root.pid and r['created'] == self.root.create_time() for r in rows)
        return {'at': time.monotonic(), 'processes': rows,
                'complete': not errors and root_present, 'errors': sorted(set(errors)),
                'rss_mib': sum(r['rss'] for r in rows) / (1024 * 1024)}


def cpu_percent_between(before: dict, after: dict) -> float | None:
    """Refuse churn/missing counters instead of subtracting vanished CPU totals."""
    elapsed = after['at'] - before['at']
    if not before['complete'] or not after['complete'] or not math.isfinite(elapsed) or elapsed <= 0:
        return None
    first = {(r['pid'], r['created']): r['cpu'] for r in before['processes']}
    last = {(r['pid'], r['created']): r['cpu'] for r in after['processes']}
    if not first or first.keys() != last.keys():
        return None
    deltas = [last[k] - first[k] for k in first]
    if any(not math.isfinite(d) or d < 0 for d in deltas):
        return None
    return sum(deltas) / elapsed * 100


def atomic_report(path: Path, report: dict) -> None:
    """A killed writer must not replace a checkpoint with partial JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run_installed(probe, *, root: Path, archive: Path, expected_hash: str,
                  expected_sha: str, output: Path, soak_seconds: float, headed: bool = False) -> dict:
    """Run the SAME probe primitives against a verified wheel, never LiveApp."""
    if isinstance(soak_seconds, bool) or not math.isfinite(soak_seconds) or not 0 <= soak_seconds <= MAX_SOAK_SECONDS:
        raise ValueError('soak duration must be between 0 and 7200 seconds')
    if re.fullmatch(r'[0-9a-f]{40}', expected_sha or '') is None:
        raise ValueError('expected source SHA must be 40 lowercase hex digits')
    root, archive, output = root.resolve(), archive.resolve(), output.resolve()
    if output.exists() or output.is_relative_to(root) or output == archive:
        raise ValueError('choose a NEW evidence JSON outside the application and archive')
    require_runtime(root, platform=sys.platform, isolated=bool(sys.flags.isolated), executable=Path(sys.executable))
    binding = verify_payload(root, archive, expected_hash)
    # Import app modules ONLY after isolation and payload verification.
    from bcc import owner_acceptance as owner
    from bcc.auth import TOKEN_FILE
    from bcc.browser_runtime import chromium_executable
    from bcc.config import Settings
    from playwright.sync_api import sync_playwright

    if not Path(owner.__file__).resolve().is_relative_to(root / 'runtime'):
        raise ValueError('BCC was imported from outside the verified installed runtime')
    identity = owner.installed_identity()
    if identity['source_sha'] != expected_sha:
        raise ValueError('installed source SHA mismatch')
    binding.update(source_sha=expected_sha, run_id=str(uuid.uuid4()),
                   harness_sha=None,
                   harness_sha256=digest_file(Path(__file__)),
                   probe_sha256=digest_file(Path(probe.__file__)),
                   budget_sha256=digest_file(probe.BUDGET_FILE))
    budget = probe.load_budget()
    lines = {key: probe.Line(key, spec) for key, spec in budget['budgets'].items()}
    for key in ('cold_start_to_interactive_s', 'warm_start_to_interactive_s'):
        lines[key].owner_required('Native Start-Bossman.cmd + cache state not measured by backend/browser diagnostics')
    report = {'type': 'bossman.responsiveness', 'schema': 2, 'mode': 'installed',
              'binding': binding, 'identity': identity, 'completed': False,
              'verdict': probe.INSUFFICIENT, 'measurement_executed': False,
              'budget_fixed_at': budget['fixed_at'], 'release_ready': False,
              'navigation_samples_ms': [], 'process_samples': [],
              'notes': ['Installed backend + bundled Chromium diagnostic, NOT owner GUI acceptance.',
                        'Probe/controller overhead INCLUDED. No external model server is exercised.',
                        'Sampled process tree: unobserved short-lived children are not covered.',
                        'External reviewed harness; harness file digests recorded, Git SHA not independently attested.'],
              'refusal_rules': budget['refusal_rules']}

    def save() -> None:
        report['lines'] = [line.as_dict() for line in lines.values()]
        atomic_report(output, report)

    output.parent.mkdir(parents=True, exist_ok=True)
    # Reserve rather than overwrite any prior run or owner file.
    with output.open('x', encoding='utf-8') as stream:
        json.dump({'completed': False, 'verdict': probe.INSUFFICIENT}, stream)
    save()
    work = Path(tempfile.mkdtemp(prefix='bossman-installed-perf-', dir=output.parent))
    process, client, clean = None, None, False
    try:
        with private_environment(root, work):
            data = work / 'data'
            data.mkdir()
            settings = Settings(data_dir=data)
            if not settings.ui_dir.resolve().is_relative_to(root / 'runtime'):
                raise ValueError('UI registry is outside installed runtime')
            # The existing shipped inventory parser, not a duplicated route list.
            sweep = probe._load('_installed_perf_routes', root / 'app-support/ui_acceptance_sweep.py')
            cleanup = probe._load('_installed_perf_cleanup', root / 'app-support/installed_ui_sweep.py')
            routes = sweep.page_routes((settings.ui_dir / 'pages/index.js').read_text(encoding='utf-8'))
            if len(routes) < 2:
                raise ValueError('installed UI registry has fewer than two routes')
            browser_exe = chromium_executable(headless=not headed)
            if not browser_exe or not Path(browser_exe).resolve().is_relative_to(root / 'browser'):
                raise ValueError('Chromium must come from the exact application ZIP')
            port = owner.free_port()
            base_url = f'http://127.0.0.1:{port}'
            with (work / 'private-server.log').open('w', encoding='utf-8') as log:
                process = owner.launch(data, port, log)
                try:
                    client, _ = owner.connect(process, data, port)
                    with sync_playwright() as pw:
                        browser = pw.chromium.launch(executable_path=str(browser_exe), headless=not headed)
                        try:
                            context = browser.new_context(viewport={'width': 1440, 'height': 900}, service_workers='block')
                            # Only this new loopback server, never references or owner accounts.
                            def route_request(route):
                                if route.request.url.startswith(base_url + '/'):
                                    route.continue_()
                                else:
                                    route.abort()
                            context.route('**/*', route_request)
                            page = context.new_page()
                            page.goto(base_url + '/', wait_until='domcontentloaded')
                            page.fill('#login-token', (data / TOKEN_FILE).read_text().strip())
                            page.click('#login-submit')
                            page.wait_for_selector('#shell:not([hidden])', timeout=30000)
                            walker = probe.Walker(page, routes)
                            # Choose a different starting route, rather than retrying a same-route failure.
                            walker.park(avoid=routes[1])
                            report['measurement_executed'] = True
                            samples = [walker.step() for _ in range(probe.NAV_SAMPLE)]
                            report['navigation_samples_ms'] = samples
                            import statistics
                            ordered = sorted(samples)
                            lines['navigation_p50_ms'].observe(statistics.median(samples), reference=False)
                            lines['navigation_p95_ms'].observe(ordered[min(len(ordered) - 1, int(round(.95 * (len(ordered) - 1))))], reference=False)
                            sampler = TreeSampler(os.getpid())
                            first = sampler.sample()
                            for _ in range(50):
                                walker.step()
                            last = sampler.sample()
                            report['process_samples'].extend([first, last])
                            if first['complete'] and last['complete'] and first['rss_mib'] > 0:
                                growth = max(0, (last['rss_mib'] / first['rss_mib'] - 1) * 100)
                                lines['open_close_cycles_rss_growth_pct'].observe(growth, reference=False)
                            seeded = 0
                            for count, metric in ((100, 'gallery_100_first_paint_ms'), (1000, 'gallery_1000_first_paint_ms')):
                                probe._seed_gallery(data, str(settings.database_url), count - seeded, seeded)
                                seeded = count
                                walker.park(avoid='images?studio=1')
                                lines[metric].observe(walker.goto('images?studio=1', selector='article.studio-card'), reference=False)
                            save()
                            if soak_seconds > 0:
                                start = sampler.sample()
                                started = time.monotonic()
                                report['process_samples'].append(start)
                                last_checkpoint = started
                                while time.monotonic() - started < soak_seconds:
                                    if process.poll() is not None:
                                        raise RuntimeError('installed server exited during soak')
                                    current = page.evaluate("() => location.hash.replace(/^#\\/?/, '')")
                                    next_route = routes[(walker.index + 1) % len(routes)]
                                    if current == next_route:
                                        walker.park(avoid=next_route)
                                    walker.step()
                                    page.wait_for_timeout(2000)
                                    report['process_samples'].append(sampler.sample())
                                    report['elapsed_load_seconds'] = time.monotonic() - started
                                    if time.monotonic() - last_checkpoint >= 60:
                                        save()
                                        last_checkpoint = time.monotonic()
                                loaded = time.monotonic() - started
                                before_idle = sampler.sample()
                                idle_start = time.monotonic()
                                idle_samples = [before_idle]
                                while time.monotonic() - idle_start < probe.idle_window_for(soak_seconds):
                                    page.wait_for_timeout(1000)
                                    if process.poll() is not None:
                                        raise RuntimeError('installed server exited during idle')
                                    idle_samples.append(sampler.sample())
                                    report['process_samples'].append(idle_samples[-1])
                                after_idle = sampler.sample()
                                report['process_samples'].extend([before_idle, after_idle])
                                report['elapsed_load_seconds'] = loaded
                                report['idle_seconds'] = after_idle['at'] - before_idle['at']
                                if loaded >= MIN_SOAK_SECONDS and soak_seconds >= MIN_SOAK_SECONDS:
                                    if all(s['complete'] for s in report['process_samples']) and start['rss_mib'] > 0:
                                        lines['soak_rss_growth_pct'].observe(max(0, (after_idle['rss_mib'] / start['rss_mib'] - 1) * 100), reference=False)
                                    idle_samples.append(after_idle)
                                    pairs = list(zip(idle_samples, idle_samples[1:]))
                                    cpus = [cpu_percent_between(a, b) for a, b in pairs]
                                    if all(value is not None for value in cpus):
                                        cpu_seconds = sum(value * (b['at'] - a['at']) / 100
                                                          for value, (a, b) in zip(cpus, pairs))
                                        cpu = 100 * cpu_seconds / (after_idle['at'] - before_idle['at'])
                                        lines['soak_idle_cpu_pct_of_one_core'].observe(cpu, reference=False)
                                    else:
                                        lines['soak_idle_cpu_pct_of_one_core'].detail = 'Process churn/missing counters: full idle CPU not proven'
                                else:
                                    report['notes'].append('Short diagnostic: soak budgets require at least 3600 seconds of load')
                        finally:
                            browser.close()
                finally:
                    if client is not None:
                        client.close()
                    cleanup.stop_owned_process_tree(process, owner.stop)
                    clean = True
        report['completed'] = True
        report['verdict'] = probe.overall_verdict([line.verdict for line in lines.values()])
    except Exception as exc:
        report['verdict'] = 'FAIL'
        report['error_type'] = type(exc).__name__
        report['notes'].append('Diagnostic failed; private logs/data retained locally for review')
    finally:
        save()
        if clean and report['completed']:
            shutil.rmtree(work)
    return report
