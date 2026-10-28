"""Immutable audit-log guard (audit 28.10): of 20 ways to touch audit.jsonl the guard protected 12.

Missed: rename, overwrite (Out-File), truncate-by-copy, wildcards, a name glued from quoted pieces. A name built in a
variable is caught by the both-halves rule (audit + jsonl in a mutating command).
"""
from __future__ import annotations

import pytest

from bcc.features.pc_agent_policy_guards import is_immutable_audit_log_target

PROTECTED = [
    r"Remove-Item .\audit.jsonl",
    r"del audit.jsonl",
    r"Rename-Item audit.jsonl audit.bak",
    r"ren audit.jsonl audit.bak",
    r"move audit.jsonl ..\x.jsonl",
    r"Move-Item audit.jsonl $env:TEMP",
    r"> audit.jsonl",
    r"Set-Content audit.jsonl ''",
    r"Out-File audit.jsonl",
    r"copy /y nul audit.jsonl",
    r"[IO.File]::Delete('audit.jsonl')",
    r"Remove-Item .\aud*.jsonl",
    r"del *.jsonl",
    r"Remove-Item (Join-Path $PWD 'audit.json'+'l')",
    r"Remove-Item audit.jsonl:hidden",
]

HARMLESS = [
    r"Get-Content audit.jsonl",
    r"journal.jsonl",
    r"notes.txt",
]


@pytest.mark.parametrize("cmd", PROTECTED)
def test_ways_to_touch_the_audit_log_are_protected(cmd):
    assert is_immutable_audit_log_target(cmd) is True


@pytest.mark.parametrize("cmd", HARMLESS)
def test_reading_or_other_files_stay_allowed(cmd):
    assert is_immutable_audit_log_target(cmd) is False


def test_variable_built_name_is_protected_by_both_halves_rule():
    assert is_immutable_audit_log_target(r"$f='audit'; Remove-Item ($f+'.jsonl')") is True
