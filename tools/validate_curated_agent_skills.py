#!/usr/bin/env python3
"""Read-only checks for the curated instruction pack, not a runtime skill engine.

This checks an owner-reviewed manifest and local files without network, imports
of skill code, command execution, installation, promotion, or permission grants.
Checksums do not establish provenance trust if text AND manifest are replaced.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any

LOCK = 'docs/skills/voltagent-agent-skills.lock.json'
MAX_SKILL_BYTES = 6144
MAX_MANIFEST_BYTES = 131072
SHA40 = re.compile(r'^[0-9a-f]{40}$')
SHA256 = re.compile(r'^[0-9a-f]{64}$')
SKILL_ID = re.compile(r'^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$')
SOURCES = {
    'obra/superpowers': ('MIT', 'docs/skills/licenses/SUPERPOWERS-MIT.txt'),
    'anthropics/skills': ('Apache-2.0', 'docs/skills/licenses/Apache-2.0.txt'),
    'trailofbits/skills': ('CC-BY-SA-4.0', 'docs/skills/licenses/TRAILOFBITS-CC-BY-SA-4.0.md'),
}
ACTIVATION = {
    'mode': 'instructions_only', 'auto_execute': False, 'grants': [],
    'max_selected_skills': 2, 'inject_all_into_system_prompt': False,
    'standing_autonomy': False,
}


class PackError(ValueError):
    """Malformed, altered or out-of-scope instruction pack."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PackError(message)


def pairs_unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        require(key not in result, f'duplicate JSON key: {key}')
        result[key] = value
    return result


def relative_path(value: Any) -> PurePosixPath:
    require(isinstance(value, str) and bool(value), 'path must be a nonempty string')
    require('\\' not in value and ':' not in value and '\x00' not in value, 'non-portable path')
    path = PurePosixPath(value)
    require(not path.is_absolute() and all(p not in ('', '.', '..') for p in value.split('/')), 'unsafe path')
    return path


def read_bounded(root: Path, relative: str, limit: int) -> bytes:
    path = relative_path(relative)
    cursor = root
    try:
        for part in path.parts:
            cursor = cursor / part
            require(not cursor.is_symlink(), f'symlink refused: {relative}')
        info = cursor.stat()
        require(stat.S_ISREG(info.st_mode), f'not a regular file: {relative}')
        require(info.st_size <= limit, f'file exceeds byte limit: {relative}')
        with cursor.open('rb') as stream:
            data = stream.read(limit + 1)
        require(len(data) <= limit, f'file grew beyond byte limit: {relative}')
        return data
    except OSError as exc:
        raise PackError(f'cannot read {relative}: {type(exc).__name__}') from exc


def frontmatter(text: str) -> tuple[dict[str, str], dict[str, str]]:
    """Accept only this pack's small portable scalar frontmatter subset."""
    lines = text.splitlines()
    require(bool(lines) and lines[0] == '---', 'missing frontmatter')
    try:
        end = lines.index('---', 1)
    except ValueError as exc:
        raise PackError('unterminated frontmatter') from exc
    fields, metadata = {}, {}
    in_metadata = False
    for line in lines[1:end]:
        require(bool(line.strip()), 'blank frontmatter line')
        if line == 'metadata:':
            require(not in_metadata, 'duplicate metadata block')
            in_metadata = True
            continue
        if in_metadata:
            require(line.startswith('  ') and not line.startswith('   '), 'invalid metadata indentation')
            target, line = metadata, line[2:]
        else:
            require(not line[0].isspace(), 'unexpected frontmatter nesting')
            target = fields
        match = re.fullmatch(r'([a-z_]+): (.+)', line)
        require(match is not None, 'unsupported frontmatter key or value')
        key, value = match.groups()
        require(key not in target, f'duplicate frontmatter field: {key}')
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except ValueError as exc:
                raise PackError('invalid quoted frontmatter scalar') from exc
        require(isinstance(value, str) and bool(value), 'frontmatter values must be strings')
        target[key] = value
    require(set(fields) == {'name', 'description', 'license', 'compatibility'}, 'unexpected or missing frontmatter fields')
    require(set(metadata) == {'owner', 'version', 'adaptation', 'upstream', 'upstream_commit', 'release_scope'}, 'unexpected metadata fields')
    return fields, metadata


def validate_pack(root: Path) -> dict[str, Any]:
    root = Path(root).resolve(strict=True)
    try:
        manifest = json.loads(read_bounded(root, LOCK, MAX_MANIFEST_BYTES).decode('utf-8'), object_pairs_hook=pairs_unique)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PackError('invalid UTF-8 JSON manifest') from exc
    require(isinstance(manifest, dict), 'manifest must be an object')
    require(type(manifest.get('schema_version')) is int and manifest['schema_version'] == 1, 'unsupported schema')
    require(SHA40.fullmatch(str(manifest.get('target_base_sha', ''))) is not None, 'invalid base SHA')
    # JSON comparison keeps booleans distinct from 0/1; do not accept coercion.
    require(json.dumps(manifest.get('activation'), sort_keys=True) == json.dumps(ACTIVATION, sort_keys=True), 'pack cannot grant or auto-activate capabilities')
    discovery = manifest.get('discovery')
    require(isinstance(discovery, dict), 'missing discovery provenance')
    require(discovery.get('repository') == 'VoltAgent/awesome-agent-skills', 'unexpected discovery source')
    require(SHA40.fullmatch(str(discovery.get('commit', ''))) is not None, 'discovery needs an immutable pin')
    require(discovery.get('url') == f"https://github.com/{discovery['repository']}/blob/{discovery['commit']}/README.md", 'discovery URL mismatch')
    entries = manifest.get('skills')
    require(isinstance(entries, list) and 0 < len(entries) <= 16, 'empty or excessive skill pack')
    ids = set()
    total = 0
    for entry in entries:
        require(isinstance(entry, dict), 'skill entry must be an object')
        sid = entry.get('id')
        require(isinstance(sid, str) and SKILL_ID.fullmatch(sid) is not None, 'invalid skill ID')
        require(sid not in ids, f'duplicate skill ID: {sid}')
        ids.add(sid)
        require(entry.get('path') == f'.agents/skills/{sid}/SKILL.md', f'noncanonical path: {sid}')
        require(entry.get('stage') in ('freeze', 'postfreeze'), f'unknown stage: {sid}')
        require(entry.get('behavioral_validation') == 'NOT_RUN', 'integrity cannot certify behavior')
        source = entry.get('source')
        require(isinstance(source, dict) and source.get('repository') in SOURCES, f'unknown source: {sid}')
        repo = source['repository']
        expected_license, expected_license_file = SOURCES[repo]
        require(source.get('license') == expected_license and entry.get('license_file') == expected_license_file, 'license mismatch')
        require(SHA40.fullmatch(str(source.get('commit', ''))) is not None, 'upstream commit must be pinned')
        require(SHA40.fullmatch(str(source.get('blob_sha', ''))) is not None, 'upstream blob SHA missing')
        source_path = str(relative_path(source.get('path')))
        require(source_path.endswith(f'/{sid}/SKILL.md'), 'upstream skill/path mismatch')
        require(source.get('url') == f"https://github.com/{repo}/blob/{source['commit']}/{source_path}", 'source URL mismatch')
        license_path = source_path.rsplit('/', 1)[0] + '/LICENSE.txt' if repo == 'anthropics/skills' else 'LICENSE'
        require(source.get('license_url') == f"https://github.com/{repo}/blob/{source['commit']}/{license_path}", 'license URL mismatch')
        require(bool(read_bounded(root, expected_license_file, 65536)), 'license notice is empty')
        data = read_bounded(root, entry['path'], MAX_SKILL_BYTES)
        require(type(entry.get('bytes')) is int and len(data) == entry['bytes'], f'byte length mismatch: {sid}')
        require(SHA256.fullmatch(str(entry.get('sha256', ''))) is not None and hashlib.sha256(data).hexdigest() == entry['sha256'], f'digest mismatch: {sid}')
        try:
            text = data.decode('utf-8')
        except UnicodeError as exc:
            raise PackError(f'invalid UTF-8 skill: {sid}') from exc
        fields, metadata = frontmatter(text)
        require(fields['name'] == sid and fields['license'] == expected_license, 'skill identity/license mismatch')
        require(40 <= len(fields['description']) <= 1024, 'description must be concise and useful')
        require(metadata['owner'] == 'bossman' and metadata['upstream'] == repo and metadata['upstream_commit'] == source['commit'], 'metadata provenance mismatch')
        require(metadata['release_scope'] == entry['stage'], 'stage mismatch')
        require('## Authority and execution boundary' in text and '## Attribution and changes' in text, 'missing boundary or attribution')
        require(source['url'] in text and source['license_url'] in text, 'skill lacks pinned attribution')
        companions = entry.get('existing_companions')
        require(isinstance(companions, list) and companions and all(isinstance(c, str) and SKILL_ID.fullmatch(c) and c != sid for c in companions), 'invalid companion skill IDs')
        total += len(data)
    return {'status': 'PASS', 'scope': 'PACK_INTEGRITY_ONLY', 'skill_count': len(ids),
            'skill_bytes': total, 'runtime_acceptance': 'NOT_RUN', 'model_effectiveness': 'NOT_RUN',
            'upstream_network_revalidation': 'NOT_RUN', 'granted_capabilities': [], 'skills': sorted(ids)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    try:
        report = validate_pack(args.root)
    except (PackError, OSError) as exc:
        print(json.dumps({'status': 'FAIL', 'scope': 'PACK_INTEGRITY_ONLY', 'error': str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
