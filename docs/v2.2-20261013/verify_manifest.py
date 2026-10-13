"""Verify this documentation package; LF/CRLF are explicitly canonicalized.

No product import, network, credentials or file writes. This does NOT certify a release.
"""
from pathlib import Path
import hashlib
import json


def main():
    root = Path(__file__).resolve().parent
    manifest = json.loads((root / 'DOCUMENT_MANIFEST.json').read_text(encoding='utf-8'))
    if manifest.get('hash_basis') != 'UTF8_TEXT_CRLF_TO_LF_ONLY_NO_OTHER_TRANSFORMS':
        raise SystemExit('DOCUMENT_MANIFEST=FAIL unsupported hash convention')
    expected = manifest['files_sha256']
    actual_paths = {p.relative_to(root).as_posix() for p in root.rglob('*')
                    if p.is_file() and p.name != 'DOCUMENT_MANIFEST.json'
                    and '__pycache__' not in p.parts}
    failed = sorted(actual_paths ^ set(expected))
    for relative, digest in expected.items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            failed.append(relative)
            continue
        raw = path.read_bytes()
        raw.decode('utf-8')
        canonical = raw.replace(b'\r\n', b'\n')
        if hashlib.sha256(canonical).hexdigest() != digest:
            failed.append(relative)
    if failed:
        print(json.dumps({'document_manifest':'FAIL', 'paths':sorted(set(failed))}))
        raise SystemExit(1)
    print(json.dumps({'document_manifest':'PASS', 'files':len(expected),
                      'hash_basis':manifest['hash_basis'], 'product_freeze':'NOT_CERTIFIED'}))


if __name__ == '__main__':
    main()
