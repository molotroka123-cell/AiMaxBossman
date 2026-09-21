"""The creative owner instructions must ride an already declared package file.

Checks the actual packaging allowlist without importing Windows tooling. This
is a source/package contract, not execution of the Windows application.
"""
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OWNER = ROOT / 'docs/v8/owner-final-run'


def test_owner_folder_has_no_undeclared_files():
    tree = ast.parse((ROOT / 'tools/build_windows_bundle.py').read_text(encoding='utf-8'))
    declaration = next(n for n in tree.body if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == 'OWNER_RUN_FILES' for t in n.targets))
    declared = set(ast.literal_eval(declaration.value))
    actual = {p.name for p in OWNER.iterdir() if p.is_file()}
    assert actual == declared, {'extra': sorted(actual-declared), 'missing': sorted(declared-actual)}


def test_creative_steps_are_inside_the_shipped_owner_readme():
    text = (OWNER / 'README_RU.md').read_text(encoding='utf-8')
    for marker in ('ИИ: творческий бриф (локально)', 'NOT CONFIGURED',
                   'browser_quality_gates=NOT_RUN', 'перезапустите Bossman',
                   'явно указанным локальным HTTP-stub'):
        assert marker in text


def test_shipped_owner_readme_checksum_is_current():
    checks = json.loads((OWNER / 'PREPARATION_FILE_CHECKS.json').read_text(encoding='utf-8'))
    expected = checks['file_sha256']['README_RU.md']
    assert hashlib.sha256((OWNER / 'README_RU.md').read_bytes()).hexdigest() == expected
