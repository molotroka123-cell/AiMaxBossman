"""Shipped sources must stay valid Python 3.11 (requires-python >= 3.11, CI matrix 3.11/3.12).

CI 799a8887: root-ci `compileall` failed on py3.11 only, because
tools/tg_post_pipeline/analyze.py reused the enclosing quote inside an f-string
replacement field (`f"...{x if m else "?"}..."`). That is legal since 3.12 (PEP 701)
and a SyntaxError on 3.11, so a 3.12 dev PC never notices. This test catches it on
every interpreter: on 3.12+ it walks the f-string tokens and flags a nested string that
reuses the quote of an enclosing f-string; on 3.11 it simply compiles each file.
"""
from __future__ import annotations

import io
import sys
import tokenize
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
#: Same scope as the root-ci compileall step.
SCOPE = ("bossman-core/bossman", "command-center/bcc", "learning", "bossman_shared", "tools")
HAS_FSTRING_TOKENS = hasattr(tokenize, "FSTRING_START")


def _quote(token_text: str) -> str:
    body = token_text.lstrip("rRbBuUfFtT")
    return body[:3] if body[:3] in ('"""', "'''") else body[:1]


def pep701_only_quotes(source: str) -> list[int]:
    """Line numbers where a string nested in an f-string reuses an enclosing f-string's quote."""
    if not HAS_FSTRING_TOKENS:
        return []
    bad: list[int] = []
    stack: list[str] = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        nested = tok.type == tokenize.STRING or tok.type == tokenize.FSTRING_START
        if nested and stack:
            q = _quote(tok.string)
            for outer in stack:
                if (len(outer) == 1 and q[0] == outer) or (len(outer) == 3 and q == outer):
                    bad.append(tok.start[0])
                    break
        if tok.type == tokenize.FSTRING_START:
            stack.append(_quote(tok.string))
        elif tok.type == tokenize.FSTRING_END and stack:
            stack.pop()
    return bad


def _sources():
    for part in SCOPE:
        yield from sorted((ROOT / part).rglob("*.py"))


def test_detector_flags_the_ci_line_and_accepts_311_spellings():
    if not HAS_FSTRING_TOKENS:
        pytest.skip("3.11 tokenizer: the compile-based test below is the check")
    bad = 'm = None\nprint(f"videos={m.group(2) if m else "?"} ok")\n'
    assert pep701_only_quotes(bad) == [2]                        # negative control: the 799a8887 line
    assert pep701_only_quotes('print(f"videos={1 if 0 else \'?\'}")\n') == []
    assert pep701_only_quotes('print(f"""a {"b"} c""")\n') == []
    assert pep701_only_quotes("print(f'{x!r:>{w}}')\n") == []


def test_every_shipped_source_is_valid_python_311():
    offenders = []
    for path in _sources():
        src = path.read_text(encoding="utf-8-sig", errors="replace")
        rel = path.relative_to(ROOT).as_posix()
        if HAS_FSTRING_TOKENS:
            try:
                lines = pep701_only_quotes(src)
            except (tokenize.TokenError, SyntaxError, IndentationError):
                continue                                        # not valid on this interpreter either: compileall reports it
            offenders += [f"{rel}:{n}" for n in lines]
        else:
            try:
                compile(src, rel, "exec", dont_inherit=True)
            except SyntaxError as exc:
                offenders.append(f"{rel}:{exc.lineno}: {exc.msg}")
    assert not offenders, f"Python 3.12+-only syntax (breaks py3.11 CI): {offenders[:20]}"
