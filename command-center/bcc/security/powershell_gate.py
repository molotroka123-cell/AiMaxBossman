"""PowerShell read-only gate on the REAL syntax tree (audit 28.10, CMP-UP-3 / 6.2: «PS без AST»).

A regex over the command text is fooled by `& ('Set-' + 'Content')`, `rm`, a backtick inside a word, `iex`, a static
.NET call or a scriptblock. Here PowerShell's own parser (System.Management.Automation.Language.Parser, always present
with Windows PowerShell) builds the tree; PowerShell only EXTRACTS facts (commands, redirections, member calls, parse
errors) and the policy below is applied in Python. Nothing is executed: ParseInput never runs the code.

Policy (conservative, default deny): read-only means ALL of
  * the text parses without errors;
  * every command has a static name that is on READONLY_COMMANDS (or is `git` with a read-only first argument);
  * no file redirection (`>`, `>>`; `2>&1` merging is fine);
  * no method call except harmless string/number helpers;
  * a nested `powershell/pwsh -Command "<static text>"` is unwrapped and judged the same way; -EncodedCommand is refused.
It is a primitive: it is not wired into action_contract here.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from typing import Any

READONLY_COMMANDS = {
    # content / filesystem reads
    "get-content", "gc", "cat", "type", "get-childitem", "gci", "ls", "dir", "get-item", "gi", "get-itemproperty",
    "test-path", "resolve-path", "get-location", "pwd", "get-filehash", "split-path", "join-path",
    # process / system reads
    "get-process", "gps", "ps", "get-service", "gsv", "get-ciminstance", "get-wmiobject", "get-computerinfo",
    "get-date", "get-command", "get-module", "get-psdrive", "get-hotfix", "get-netipaddress", "get-nettcpconnection",
    # shaping
    "select-object", "select", "where-object", "where", "?", "sort-object", "sort", "measure-object", "measure",
    "group-object", "group", "foreach-object", "foreach", "%", "select-string", "sls", "format-table", "ft", "format-list",
    "fl", "format-wide", "out-string", "convertto-json", "convertfrom-json", "convertto-csv", "convertfrom-csv",
    "write-output", "echo", "write-host", "compare-object", "get-unique", "get-random", "get-member", "get-variable",
}
READONLY_GIT = {"status", "log", "diff", "show", "branch", "rev-parse", "ls-files", "remote", "describe", "shortlog"}
SHELLS = {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}
SAFE_METHODS = {"tostring", "trim", "trimstart", "trimend", "split", "tolower", "toupper", "contains", "startswith",
                "endswith", "substring", "indexof", "round", "floor", "ceiling", "join", "format"}

_EXTRACT = r"""
$ErrorActionPreference = 'Stop'
$items = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:BOSSMAN_PS_B64)) | ConvertFrom-Json
$T = @{}
foreach ($n in 'CommandAst','FileRedirectionAst','InvokeMemberExpressionAst','StringConstantExpressionAst','CommandParameterAst') { $T[$n] = [type]('System.Management.Automation.Language.' + $n) }
$out = foreach ($code in $items) {
  $tokens = $null; $errs = $null
  $ast = [System.Management.Automation.Language.Parser]::ParseInput([string]$code, [ref]$tokens, [ref]$errs)
  $cmds = @($ast.FindAll({ param($n) $n -is $T['CommandAst'] }, $true) | ForEach-Object {
    $els = @($_.CommandElements)
    $static = @($els | Select-Object -Skip 1 | ForEach-Object { if ($_ -is $T['StringConstantExpressionAst']) { [string]$_.Value } elseif ($_ -is $T['CommandParameterAst']) { '-' + [string]$_.ParameterName } else { $null } })
    [pscustomobject]@{ name = $_.GetCommandName(); args = $static; invocation = [string]$_.InvocationOperator }
  })
  $files = @($ast.FindAll({ param($n) $n -is $T['FileRedirectionAst'] }, $true)).Count
  $members = @($ast.FindAll({ param($n) $n -is $T['InvokeMemberExpressionAst'] }, $true) | ForEach-Object {
    $m = $_.Member
    [pscustomobject]@{ name = $(if ($m -is $T['StringConstantExpressionAst']) { [string]$m.Value } else { $null }); static = [bool]$_.Static }
  })
  [pscustomobject]@{ errors = @($errs).Count; commands = $cmds; file_redirections = $files; members = $members }
}
ConvertTo-Json -InputObject @($out) -Depth 6 -Compress
"""


class PowerShellGateError(RuntimeError):
    pass


def _powershell() -> str:
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        raise PowerShellGateError("no PowerShell found to parse with")
    return exe


def extract_facts(texts: list[str], timeout: float = 60.0) -> list[dict[str, Any]]:
    """Parse every text with PowerShell's parser (one process for the batch) and return the syntax facts."""
    exe = _powershell()
    env = dict(os.environ, BOSSMAN_PS_B64=base64.b64encode(json.dumps(texts).encode("utf-8")).decode("ascii"))
    proc = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", _EXTRACT], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, env=env)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise PowerShellGateError(f"parser run failed: {proc.stderr.strip()[:300]}")
    data = json.loads(proc.stdout)
    return data if isinstance(data, list) else [data]


def _as_list(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _judge(facts: dict[str, Any], depth: int) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if facts.get("errors"):
        reasons.append("does not parse cleanly")
    if facts.get("file_redirections"):
        reasons.append("writes to a file by redirection")
    for m in _as_list(facts.get("members")):
        name = (m.get("name") or "").lower()
        if name not in SAFE_METHODS:
            reasons.append(f"method call {m.get('name') or '<dynamic>'}{' (static)' if m.get('static') else ''}")
    for c in _as_list(facts.get("commands")):
        name = c.get("name")
        args = _as_list(c.get("args"))
        if not name:
            reasons.append("command name is not static (dynamic invocation)")
            continue
        low = name.lower()
        if low in SHELLS:
            reasons.append(f"nested shell {name}")
        elif low == "git":
            first = next((a for a in args if a and not a.startswith("-")), None)
            if (first or "").lower() not in READONLY_GIT:
                reasons.append(f"git {first or ''} is not read-only")
        elif low not in READONLY_COMMANDS:
            reasons.append(f"command {name} is not on the read-only list")
    return (not reasons), reasons


def _unwrap_shell(facts: dict[str, Any]) -> list[str] | None:
    """Static inner texts of `powershell -Command "<text>"` calls; None if one of them cannot be read statically."""
    inner: list[str] = []
    for c in _as_list(facts.get("commands")):
        if (c.get("name") or "").lower() in SHELLS:
            args = _as_list(c.get("args"))
            low = [(a or "").lower() for a in args]
            if any(a.startswith("-") and len(a) >= 2 and "encodedcommand".startswith(a[1:]) for a in low if a):
                return None                                           # -EncodedCommand and its abbreviations (-e, -ec, -enc ...)
            for i, a in enumerate(low):
                if a in ("-command", "-c", "-commandwithargs") and i + 1 < len(args) and args[i + 1]:
                    inner.append(args[i + 1])
                    break
            else:
                return None
    return inner


def classify_many(texts: list[str]) -> list[dict[str, Any]]:
    """For each text: {"readonly": bool, "reasons": [...], "commands": [names]}. Default deny."""
    results: list[dict[str, Any]] = []
    facts_list = extract_facts(texts)
    for facts in facts_list:
        readonly, reasons = _judge(facts, 0)
        shells = [c for c in _as_list(facts.get("commands")) if (c.get("name") or "").lower() in SHELLS]
        if shells:
            inner = _unwrap_shell(facts)
            reasons = [r for r in reasons if not r.startswith("nested shell")]
            if inner is None:
                reasons.append("nested shell with an unreadable or encoded command")
            else:
                for ifacts in (extract_facts(inner) if inner else []):
                    _ok, why = _judge(ifacts, 1)
                    reasons += [f"inner: {r}" for r in why]
            readonly = not reasons
        results.append({"readonly": bool(readonly), "reasons": reasons,
                        "commands": [c.get("name") for c in _as_list(facts.get("commands"))]})
    return results


def classify(text: str) -> dict[str, Any]:
    return classify_many([text])[0]
