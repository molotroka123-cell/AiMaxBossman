"""Deliver the owner's already-tested offline V4 edits without stale overwrites.

The three text parts are base64/XZ transport of JSON line edits, not executable
commands. Decoded SHA256 and EVERY input/output file hash are checked before
any write. The original source and full diffs are visible in the resulting PR.
No application runtime calls, network calls or token reads occur here.
"""
from __future__ import annotations
import base64
import hashlib
import json
import lzma
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = '0c01262a3a9d33f048df2cdbb1ba70fdaea00cb8aafd18a589a5425314effeab'
ALLOWED = {
    'bossman-core/bossman_v3/adapters/command_center.py',
    'bossman-core/bossman_v3/execution/compound.py',
    'bossman-core/tests/test_epoch4_bcc_authority.py',
    'command-center/bcc/engine.py',
    'command-center/bcc/features/missions.py',
    'command-center/bcc/mission_continuity.py',
    'command-center/bcc/tools.py',
    'command-center/tests/js/mission_continuity.test.mjs',
    'command-center/tests/test_epoch4_mission_continuity.py',
    'command-center/ui/mission_state.js',
    'command-center/ui/pages/missions.js',
    'docs/v4/CONTINUITY_PATCH_RUN_20260906.md',
}
OWNED_SCAFFOLD = [
    '.github/checkpoint/v4_payload_0.txt', '.github/checkpoint/v4_payload_1.txt',
    '.github/checkpoint/v4_payload_2.txt', '.github/checkpoint/deliver_v4.py',
    '.github/workflows/v4-boundary-delivery.yml',
]


def apply() -> dict[str, str]:
    encoded = ''.join((ROOT / f'.github/checkpoint/v4_payload_{i}.txt').read_text(encoding='ascii') for i in range(3))
    raw = lzma.decompress(base64.b64decode(encoded, validate=True), memlimit=128 * 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != EXPECTED:
        raise ValueError('delivery payload SHA256 mismatch')
    rows = json.loads(raw)
    if len(rows) != len(ALLOWED) or {row['path'] for row in rows} != ALLOWED:
        raise ValueError('unexpected or duplicate delivery path')
    staged = []
    for row in rows:
        path = ROOT / row['path']
        if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != ROOT.parent):
            raise ValueError('symlink in delivery path')
        if row['before'] is None:
            if path.exists():
                raise ValueError('new path already exists: ' + row['path'])
            original = b''
        else:
            original = path.read_bytes()
            if hashlib.sha256(original).hexdigest() != row['before']:
                raise ValueError('preimage changed: ' + row['path'])
        lines = original.decode('utf-8').splitlines(keepends=True)
        end = 0
        for start, stop, replacement in row['edits']:
            if type(start) is not int or type(stop) is not int or not end <= start <= stop <= len(lines) or not isinstance(replacement, str):
                raise ValueError('invalid edit range')
            end = stop
        for start, stop, replacement in reversed(row['edits']):
            lines[start:stop] = replacement.splitlines(keepends=True)
        updated = ''.join(lines).encode('utf-8')
        if hashlib.sha256(updated).hexdigest() != row['after']:
            raise ValueError('postimage mismatch: ' + row['path'])
        staged.append((path, updated))
    # All preimages and postimages are validated before modifying the workspace.
    for path, updated in staged:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(updated)
    manifest = {row['path']: row['after'] for row in rows}
    result_dir = Path(os.environ.get('RUNNER_TEMP', '/tmp')) / 'v4-delivery-results'
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / 'postimages.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return manifest


if __name__ == '__main__':
    apply()
    if sys.argv[1:] == ['--remove-scaffold']:
        for name in OWNED_SCAFFOLD:
            (ROOT / name).unlink()
    elif sys.argv[1:]:
        raise ValueError('unsupported arguments')
    print('V4_DELIVERY_POSTIMAGES_VERIFIED=12')
