"""Verify reviewed transport bytes; default is dry-run, --apply edits worktree only.

Does not commit, push, access network, enable features or touch owner data.
Fail closed on drift. This is transport, NOT a second runtime implementation.
"""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tempfile


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(['git', *args], cwd=root)


def blob(data: bytes) -> str:
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    root = Path(git(here, 'rev-parse', '--show-toplevel').decode().strip())
    manifest = json.loads((here / 'manifest.json').read_text())
    if git(root, 'status', '--porcelain'):
        raise SystemExit('BLOCKED: clean disposable checkout required')
    if set(manifest['before']) - set(manifest['after']):
        raise SystemExit('BLOCKED: deletion outside reviewed contract')
    payload = bytearray()
    for i, expected in enumerate(manifest['parts']):
        data = (here / 'parts' / f'{i:02}.bin').read_bytes()
        if blob(data) != expected:
            raise SystemExit(f'BLOCKED: part {i} hash mismatch')
        payload.extend(data)
    if len(payload) != manifest['gzip_size'] or hashlib.sha256(payload).hexdigest() != manifest['gzip_sha256']:
        raise SystemExit('BLOCKED: gzip size/hash mismatch')
    patch = gzip.decompress(payload)
    if len(patch) != manifest['patch_size'] or hashlib.sha256(patch).hexdigest() != manifest['patch_sha256']:
        raise SystemExit('BLOCKED: patch size/hash mismatch')
    names = set(manifest['after'])
    for name in names:
        rel = PurePosixPath(name)
        if rel.is_absolute() or '..' in rel.parts or rel.parts[0] not in {'bossman-core', 'command-center', 'docs', 'handoffs'}:
            raise SystemExit('BLOCKED: non-allowlisted patch path')
        path = root / name
        if any(p.is_symlink() for p in (path, *path.parents) if p != root):
            raise SystemExit('BLOCKED: symlink target')
        expected = manifest['before'].get(name)
        if expected is None:
            if path.exists():
                raise SystemExit(f'BLOCKED: new target already exists: {name}')
        elif not path.is_file() or blob(path.read_bytes()) != expected:
            raise SystemExit(f'BLOCKED: upstream changed: {name}')
    with tempfile.TemporaryDirectory(prefix='bossman-reviewed-patch-') as tmp:
        patch_path = Path(tmp) / 'changes.patch'
        patch_path.write_bytes(patch)
        paths = {row.split('\t', 2)[2] for row in git(root, 'apply', '--numstat', str(patch_path)).decode().splitlines()}
        if paths != names:
            raise SystemExit('BLOCKED: patch paths differ from reviewed manifest')
        git(root, 'apply', '--check', str(patch_path))
        if args.apply:
            git(root, 'apply', str(patch_path))
            for name, expected in manifest['after'].items():
                if blob((root / name).read_bytes()) != expected:
                    raise SystemExit(f'BLOCKED: applied bytes differ: {name}; do not commit')
            git(root, 'diff', '--check')
    print('APPLIED_AND_HASH_VERIFIED' if args.apply else 'DRY_RUN_VERIFIED')
    print(f'paths={len(names)} patch_sha256={manifest["patch_sha256"]}')


if __name__ == '__main__':
    main()
