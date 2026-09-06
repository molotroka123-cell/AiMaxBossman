"""Materialize a hash-bound plain source diff; no branch or owner runtime changes."""
from __future__ import annotations
import base64
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
MANIFEST = json.loads((HERE / 'manifest.json').read_text())
ROOT = Path(os.environ['RUNNER_TEMP']) / 'bossman-candidate'
OUT = Path(os.environ['RUNNER_TEMP']) / 'bossman-delivery-result'


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(['git', *args], cwd=cwd, text=True).strip()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def blob(data: bytes) -> str:
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def validate_paths() -> list[str]:
    paths = [row['path'] for row in MANIFEST['files']]
    if len(paths) != len(set(paths)) or len(paths) > 30:
        raise ValueError('duplicate/unbounded paths')
    for name in paths:
        p = PurePosixPath(name)
        if p.is_absolute() or '..' in p.parts or p.parts[0] not in {'bossman-core', 'command-center', 'docs', 'tests'}:
            raise ValueError('path outside declared source scope')
    return paths


def materialize() -> None:
    paths = validate_paths()
    first = (HERE / 'three-lane-patch.gz.b64.1').read_bytes()
    second = (HERE / 'three-lane-patch.gz.b64.2').read_bytes()[-MANIFEST['transport_part2_tail_bytes']:]
    if digest(second) != MANIFEST['transport_part2_sha256']:
        raise ValueError('transport part 2 mismatch; nothing applied')
    packed = base64.b64decode(first + second, validate=True)
    if digest(packed) != MANIFEST['gzip_sha256']:
        raise ValueError('compressed patch mismatch; nothing applied')
    with gzip.GzipFile(fileobj=io.BytesIO(packed)) as handle:
        patch = handle.read(1_000_001)
    if len(patch) > 1_000_000 or digest(patch) != MANIFEST['patch_sha256']:
        raise ValueError('plain patch mismatch; nothing applied')
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'transport-source.patch').write_bytes(patch)
    git('worktree', 'add', '--detach', str(ROOT), MANIFEST['base_sha'])
    assert git('rev-parse', 'HEAD^{tree}', cwd=ROOT) == MANIFEST['base_tree']
    for row in MANIFEST['files']:
        p = ROOT / row['path']
        assert (blob(p.read_bytes()) if p.exists() else None) == row['before'], row['path']
    subprocess.run(['git', 'apply', '--check', '--unidiff-zero', str(OUT / 'transport-source.patch')], cwd=ROOT, check=True)
    subprocess.run(['git', 'apply', '--unidiff-zero', str(OUT / 'transport-source.patch')], cwd=ROOT, check=True)
    for row in MANIFEST['files']:
        p = ROOT / row['path']
        assert p.is_file() and not p.is_symlink() and blob(p.read_bytes()) == row['after'], row['path']
    git('add', '--', *paths, cwd=ROOT)
    tree = git('write-tree', cwd=ROOT)
    assert tree == MANIFEST['target_tree'], f'unexpected base candidate tree {tree}'
    revision = json.loads((HERE / 'revision.json').read_text())
    assert revision['base_target_tree'] == tree
    allowed = {'command-center/tests/test_takeover_home_ui.py', 'tests/test_packaging_installed.py'}
    assert len(revision['overrides']) == 2
    assert {row['path'] for row in revision['overrides']} == allowed
    by_path = {row['path']: row for row in MANIFEST['files']}
    for row in revision['overrides']:
        p = ROOT / row['path']
        assert p.is_file() and not p.is_symlink() and blob(p.read_bytes()) == row['before'], row['path']
        data = row['content'].encode('utf-8')
        assert blob(data) == row['after'], row['path']
        p.write_bytes(data)
        if row['path'] in by_path:
            by_path[row['path']]['after'] = row['after']
        else:
            MANIFEST['files'].append({k:row[k] for k in ('path','before','after')})
    paths = validate_paths()
    git('add', '--', *paths, cwd=ROOT)
    tree = git('write-tree', cwd=ROOT)
    assert tree == revision['target_tree'], f'unexpected revised tree {tree}'
    MANIFEST['target_tree'] = tree
    MANIFEST['validation_revision'] = 'event-bound UI test and isolated wheel install'
    (OUT/'source.patch').write_bytes(subprocess.check_output(['git','diff','--cached','--binary'],cwd=ROOT))
    for row in MANIFEST['files']:
        target = OUT / 'source' / row['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / row['path'], target)
    (OUT / 'manifest.json').write_text(json.dumps(MANIFEST, indent=2), encoding='utf-8')
    print(f'PINNED_BASE={MANIFEST["base_sha"]}\nTESTED_TREE={tree}\nSOURCE_FILES={len(paths)}')
    with open(os.environ['GITHUB_ENV'], 'a', encoding='utf-8') as handle:
        handle.write('CANDIDATE=' + str(ROOT) + '\n')
        handle.write('RESULTS=' + str(OUT) + '\n')
        handle.write('PYTHONPATH=' + os.pathsep.join(map(str, [ROOT, ROOT/'bossman-core', ROOT/'command-center'])) + '\n')


def publish_blobs() -> None:
    # Only after all three test lanes pass. No branch updates or deployment.
    import urllib.request
    assert os.environ['GITHUB_REPOSITORY'] == 'molotroka123-cell/AiMaxBossman'
    paths = validate_paths()
    result = []
    for row in MANIFEST['files']:
        data = (ROOT / row['path']).read_bytes()
        assert blob(data) == row['after']
        payload = json.dumps({'content': base64.b64encode(data).decode(), 'encoding': 'base64'}).encode()
        request = urllib.request.Request('https://api.github.com/repos/' + os.environ['GITHUB_REPOSITORY'] + '/git/blobs',
            data=payload, headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
                                  'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(request, timeout=30) as response:
            stored = json.load(response)
        assert stored['sha'] == row['after'], row['path']
        result.append({'path': row['path'], 'mode': '100644', 'type': 'blob', 'sha': stored['sha']})
    (OUT/'published-blobs.json').write_text(json.dumps({'base_sha':MANIFEST['base_sha'],
        'tested_tree':MANIFEST['target_tree'], 'entries':result},indent=2),encoding='utf-8')
    print(f'{len(paths)} verified immutable blobs stored; no branch updated')


if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in {'materialize', 'blobs'}:
        raise SystemExit('expected materialize or blobs')
    materialize()
    if sys.argv[1] == 'blobs':
        publish_blobs()
