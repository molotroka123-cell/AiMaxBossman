"""GLM BUG-003: exact-byte manifests, including CRLF and legacy cache reuse.

No model or owner desktop involved. Byte fixtures make the Windows failure
reproducible on Linux instead of hiding it behind a platform skip.
"""
import hashlib
import json
from pathlib import Path

import pytest
from tools.context_slice import failing_test_slice, repo_map


@pytest.mark.parametrize('source', [
    b'X = 1\n',
    b'X = 1\r\n',
    b'X = 1\r\nY = 2\n',
    b'# invalid UTF-8: \xff\r\nX = 1\r\n',
])
@pytest.mark.parametrize('operation', ['map', 'slice'])
def test_manifest_digest_matches_raw_bytes(tmp_path, source, operation):
    root = tmp_path / 'project'
    root.mkdir()
    path = root / 'test_example.py'
    path.write_bytes(source)
    if operation == 'map':
        record = repo_map(root, cache_dir=tmp_path / 'cache')['files'][path.name]
    else:
        record = failing_test_slice(root, path, depth=0)['files'][0]
    assert record['sha256'] == hashlib.sha256(source).hexdigest()[:16]


def test_legacy_text_digest_cache_is_rebuilt_under_same_key(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    path = root / 'test_example.py'
    source = b'X = 1\r\n'
    path.write_bytes(source)
    cache = tmp_path / 'cache'
    first = repo_map(root, cache_dir=cache)
    cache_file = cache / f"repo_map-{first['fingerprint']}.json"
    legacy = json.loads(cache_file.read_text(encoding='utf-8'))
    legacy.pop('schema', None)
    legacy['files'][path.name]['sha256'] = hashlib.sha256(b'X = 1\n').hexdigest()[:16]
    cache_file.write_text(json.dumps(legacy), encoding='utf-8')
    rebuilt = repo_map(root, cache_dir=cache)
    assert rebuilt['cache'] == 'miss', 'legacy cached text digest survived the code fix'
    assert rebuilt['files'][path.name]['sha256'] == hashlib.sha256(source).hexdigest()[:16]
    assert repo_map(root, cache_dir=cache)['cache'] == 'hit'


def test_symbols_and_token_estimate_remain_usable_with_crlf(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    path = root / 'test_example.py'
    path.write_bytes(b'def hello():\r\n    return 1\r\n')
    record = repo_map(root, cache_dir=tmp_path / 'cache')['files'][path.name]
    assert record['symbols'] == ['hello']
    assert record['tokens'] == len('def hello():\n    return 1\n') // 4
