"""MD5/SHA-1 in shipped components must say they are not for security (bandit B324).

CI 9340a16e: the command-center "SAST and dependency gate" (tools/astra_security_gate.py,
bandit --severity-level high) failed on one `hashlib.md5(...)` in bcc/art_v2/vlm_critic.py.
Bandit is not installed in the root suite or on a dev PC, so the same rule is pinned
here as a plain AST check over the bandit scopes of both components.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
#: tools/astra_security_gate.py source scopes (command-center + bossman-core).
SCOPES = ("command-center/bcc", "bossman-core/bossman", "bossman-core/bossman_v3", "bossman_shared")
WEAK = {"md5", "sha1"}


def weak_hash_calls(source: str) -> list[int]:
    """Lines calling hashlib.md5/sha1 (or hashlib.new("md5"/"sha1")) without usedforsecurity=False."""
    bad = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name == "new" and node.args and isinstance(node.args[0], ast.Constant):
            name = str(node.args[0].value).lower()
        if name not in WEAK:
            continue
        if isinstance(fn, ast.Attribute) and not (isinstance(fn.value, ast.Name) and fn.value.id == "hashlib"):
            continue
        safe = any(k.arg == "usedforsecurity" and isinstance(k.value, ast.Constant) and k.value.value is False
                   for k in node.keywords)
        if not safe:
            bad.append(node.lineno)
    return bad


def test_detector_negative_control():
    assert weak_hash_calls("import hashlib\nx = hashlib.md5(b'a').hexdigest()\n") == [2]
    assert weak_hash_calls("import hashlib\nx = hashlib.new('sha1', b'a')\n") == [2]
    assert weak_hash_calls("import hashlib\nx = hashlib.md5(b'a', usedforsecurity=False)\n") == []
    assert weak_hash_calls("import hashlib\nx = hashlib.sha256(b'a')\n") == []


def test_no_weak_hash_for_security_in_shipped_components():
    offenders = []
    for scope in SCOPES:
        for path in sorted((ROOT / scope).rglob("*.py")):
            try:
                lines = weak_hash_calls(path.read_text(encoding="utf-8-sig"))
            except SyntaxError:
                continue                                  # compileall reports it
            offenders += [f"{path.relative_to(ROOT).as_posix()}:{n}" for n in lines]
    assert not offenders, ("hashlib.md5/sha1 without usedforsecurity=False fails the bandit B324 "
                           f"high-severity CI gate; use sha256 or mark it: {offenders}")
