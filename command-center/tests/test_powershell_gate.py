"""PowerShell AST gate (audit 28.10, CMP-UP-3): the verdict comes from PowerShell's own parser, never from the text.

Nothing is executed (ParseInput only). The corpus has the read-only commands from Argon's D-026 test, its mutating
commands, and 23 evasions (dynamic names, aliases, a backtick inside a word, iex, .NET calls, scriptblocks, method
calls). The AST gate must reject every mutation and accept every read-only command; the regex of action_contract is
compared for the record only.
"""
from __future__ import annotations

import shutil

import pytest

from bcc.security import powershell_gate as g

pytestmark = pytest.mark.skipif(not (shutil.which("powershell") or shutil.which("pwsh")), reason="needs PowerShell to parse")

BT = chr(96)
READONLY = [
    "Get-Content README.md -TotalCount 20",
    "Get-ChildItem -Path command-center/bcc",
    "Get-CimInstance Win32_OperatingSystem",
    "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1MB",
    "Get-Process | Select-Object -First 10",
    "Select-String -Path command-center/bcc/api.py -Pattern 'health'",
    'powershell -NoProfile -Command "Get-Content README.md"',
    'powershell -NoProfile -ExecutionPolicy Bypass -Command "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1MB"',
    "git status --short",
    "Get-ChildItem | Where-Object { $_.Length -gt 10 }",
    "Get-Process 2>&1",
    "Get-Content a.txt | Select-String error | Measure-Object",
    "git log --oneline -5",
]
MUTATING = [
    "Set-Content -Path a.txt -Value 'hello'",
    "Remove-Item -Path a.txt -Force",
    'powershell -Command "Out-File -FilePath a.txt"',
    "Get-Content a.txt | Tee-Object -FilePath b.txt",
    "powershell -EncodedCommand ZWNobyBoZWxsbw==",
    "Get-Content a.txt > b.txt",
    "git commit -m 'fix'",
]
EVASIONS = [
    "& ('Set-' + 'Content') a.txt hi",
    "$c = 'Remove-Item'; & $c a.txt",
    'iex "Remove-Item a.txt"',
    "Invoke-Expression 'Remove-Item a.txt'",
    "rm a.txt", "del a.txt", "ri a.txt", "ni a.txt", "sc a.txt hi", "mv a b", "cp a b",
    "Set-Con" + BT + "tent a.txt hi",
    "[IO.File]::WriteAllText('a.txt','x')",
    "[System.IO.File]::Delete('a.txt')",
    "Get-Content a.txt | Out-File b.txt",
    "Get-Process | Stop-Process -Force",
    "(Get-Item a.txt).Delete()",
    "& { Remove-Item a.txt }",
    "Start-Process calc",
    "$ExecutionContext.InvokeCommand.InvokeScript('Remove-Item a')",
    "Get-Content a.txt | Add-Content b.txt",
    "New-Item -ItemType File x.txt",
    "Get-ChildItem | ForEach-Object { Remove-Item $_ }",
]


@pytest.fixture(scope="module")
def verdicts():
    texts = READONLY + MUTATING + EVASIONS
    return dict(zip(texts, g.classify_many(texts)))


@pytest.mark.parametrize("text", READONLY)
def test_read_only_commands_are_accepted(verdicts, text):
    assert verdicts[text]["readonly"] is True, verdicts[text]["reasons"]


@pytest.mark.parametrize("text", MUTATING)
def test_mutating_commands_are_rejected(verdicts, text):
    assert verdicts[text]["readonly"] is False and verdicts[text]["reasons"]


@pytest.mark.parametrize("text", EVASIONS)
def test_evasions_that_fool_text_matching_are_rejected(verdicts, text):
    assert verdicts[text]["readonly"] is False, f"AST gate let this through: {text}"


def test_the_instance_method_call_that_the_regex_misses_is_caught():
    from bcc.features import action_contract as ac
    text = "(Get-Item a.txt).Delete()"
    assert g.classify(text)["readonly"] is False
    # recorded for the audit: the regex in action_contract._looks_like_mutation does not see this one (22 of 23 evasions caught)
    if not ac._looks_like_mutation(text):
        print("regex misses:", text)


def test_unparseable_text_is_denied_and_empty_text_is_harmless():
    bad = g.classify("Get-Content (")
    assert bad["readonly"] is False and "does not parse cleanly" in bad["reasons"]
    assert g.classify("")["readonly"] is True


def test_nested_shell_is_judged_by_its_inner_text_and_encoded_is_refused():
    assert g.classify('powershell -Command "Get-Date"')["readonly"] is True
    inner = g.classify('powershell -Command "Remove-Item a.txt"')
    assert inner["readonly"] is False and any(r.startswith("inner:") for r in inner["reasons"])
    for flag in ("-e", "-enc", "-EncodedCommand"):
        assert g.classify(f"powershell {flag} ZWNobyBoZWxsbw==")["readonly"] is False


def test_parsing_never_executes_the_text(tmp_path):
    marker = tmp_path / "executed.txt"
    text = f"Set-Content -Path '{marker}' -Value x"
    assert g.classify(text)["readonly"] is False
    assert not marker.exists(), "the gate must only PARSE; the file got created"
