"""Run the existing button inventory against an isolated installed application.

This is a heuristic sweep, not proof of every nested state or model ability.
Run with the bundled Python and -I; only the reviewed sweep driver is loaded
from the checkout. App modules, UI registry and Chromium come from the bundle.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
from types import SimpleNamespace


def stop_owned_process_tree(process, stop_parent) -> None:
    """Stop only descendants of this sweep's app, before deleting its data.

    Managed apps intentionally survive a BCC restart. They must not survive
    disposal of this isolated test installation, especially on Windows where
    an active child keeps its working directory locked.
    """
    import psutil
    try:
        parent = psutil.Process(process.pid)
        # Some managed execution environments remap Popen PIDs. Never touch a
        # process tree unless its command line matches the child we launched.
        if (parent.cmdline()[1:] != list(process.args)[1:]
                or not os.path.samefile(parent.exe(), process.args[0])):
            raise RuntimeError('Cannot verify the sweep process identity for cleanup')
        children = parent.children(recursive=True)
    except psutil.NoSuchProcess:
        children = []
    stop_parent(process)
    for child in reversed(children):
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(children, timeout=5)
    for child in alive:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(alive, timeout=5)
    alive = [child for child in alive if child.status() != psutil.STATUS_ZOMBIE]
    if alive:
        raise RuntimeError('Sweep child processes did not stop; private data retained')


def write_report(output, sha, identity, pages, clicks):
    counts = {}
    for click in clicks:
        counts[click.verdict] = counts.get(click.verdict, 0) + 1
    review = any(counts.get(name, 0) for name in ('dead', 'error', 'disabled_silent', 'vanished'))
    report = {'status': 'REVIEW_REQUIRED' if review else 'PASS', 'source_sha': sha,
              'identity': identity, 'pages': pages, 'counts': counts,
              'scope': 'Visible button classes in fresh empty app; nested dialogs, owner accounts and model work need separate scenarios.',
              'clicks': [asdict(click) for click in clicks]}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding='utf-8')
    print(json.dumps({'status': report['status'], 'pages': len(pages), 'counts': counts}))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--expected-sha', required=True)
    args = parser.parse_args()
    from bcc.owner_acceptance import installed_identity, launch, connect, stop
    from bcc.auth import TOKEN_FILE
    from bcc.config import Settings
    identity = installed_identity()
    if identity['source_sha'] != args.expected_sha:
        raise RuntimeError('Installed sweep source SHA differs from requested SHA')
    # No real accounts or cloud requests belong in the destructive UI sandbox.
    for name in list(os.environ):
        if name.endswith('_API_KEY') or name in ('BCC_DATA_DIR', 'DATABASE_URL', 'BCC_UI_DIR'):
            os.environ.pop(name, None)
    driver = Path(__file__).resolve().parents[1] / 'scripts' / 'ui_acceptance_sweep.py'
    spec = importlib.util.spec_from_file_location('_bossman_sweep_driver', driver)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    before = list(sys.path)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = before
    with tempfile.TemporaryDirectory(prefix='bossman-installed-ux-') as folder:
        data = Path(folder)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        registry = Settings(data_dir=data).ui_dir / 'pages' / 'index.js'
        pages = re.findall(r"lazyPage\(\{\s*id:\s*'([^']+)'", registry.read_text(encoding='utf-8'))
        if not pages:
            raise RuntimeError('Packaged UI page registry is empty')
        with (data / 'private-server.log').open('w', encoding='utf-8') as log:
            process = launch(data, port, log)
            client = None
            try:
                client, _ = connect(process, data, port)
                app = SimpleNamespace(url=f'http://127.0.0.1:{port}',
                    svc=SimpleNamespace(auth=SimpleNamespace(token=(data / TOKEN_FILE).read_text().strip())))
                clicks = module.sweep(app, pages, headed=False)
                # Preserve observations even if process/data cleanup fails.
                # The failed CI job still prevents a successful freeze.
                write_report(args.output, args.expected_sha, identity, pages, clicks)
            finally:
                try:
                    if client is not None:
                        # The app manager knows its detached children too.
                        # Restore permission only in this disposable sandbox,
                        # because the sweep may have toggled it off.
                        client.put('/api/apps/control/policy', json={'enabled': True}).raise_for_status()
                        response = client.get('/api/apps')
                        response.raise_for_status()
                        for app in response.json()['apps']:
                            client.post('/api/apps/' + app['id'] + '/stop').raise_for_status()
                except Exception as error:
                    print('App API cleanup needs scoped process fallback: ' + type(error).__name__)
                finally:
                    if client is not None:
                        client.close()
                    stop_owned_process_tree(process, stop)
    # Review findings remain visible in the freeze manifest; they are not
    # automatically called product defects or hidden behind a green assertion.
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
