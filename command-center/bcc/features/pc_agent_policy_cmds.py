"""Политика Argon PC-Agent: команды оболочки (без БД и сети)."""
from __future__ import annotations

import contextlib
import os
import re
from pathlib import Path
from typing import Any

from .pc_agent_policy_consts import (
    PolicyVerdict,
    _DANGEROUS_SHELL_CHAIN_RE,
    _GIT_BRANCH_SAFE_ARGS,
    _GIT_READONLY_SUBCOMMANDS,
    _SAFE_ARGV_HEADS,
    _T2_ALWAYS_BUTTON_PATTERNS,
    _T2_CMD_PATTERNS,
    _T3_CMD_PATTERNS,
    _T3_SECRET_PATTERNS,
)
from .pc_agent_policy_paths import (
    PromptIntentReadOnly,
    _is_system32_bin,
    _norm_path,
    is_t3_secret_or_protected_path,
    is_within_safe_roots,
)
from .pc_agent_policy_guards import validate_powershell_ast_or_tokens
from .pc_agent_policy_procs import _check_proc_tree_protected, is_protected_process

_GIT_HEADS = {"git", "git.exe"}
_PY_HEADS = {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}
_GUI_HEADS = set("notepad calc mspaint charmap".split())


def _is_git_readonly_invocation(argv: list[str]) -> bool:
    if len(argv) < 2 or argv[1].lower() not in _GIT_READONLY_SUBCOMMANDS:
        return False
    bad_o = set("-c --config-env --exec-path --git-dir --work-tree --no-index --ext-diff --textconv".split())
    bad_p = ("--output", "--exec=", "--git-dir=", "--work-tree=")
    if any(a.lower() in bad_o or a.lower().startswith(bad_p) for a in argv[2:]):
        return False
    if argv[1].lower() == "branch":
        return all(a in _GIT_BRANCH_SAFE_ARGS for a in argv[2:])
    return True


def _is_readonly_sys_cmd(argv: list[str]) -> bool:
    if not argv:
        return False
    head = Path(argv[0]).name.lower()
    if head.removesuffix(".exe") not in (_SAFE_ARGV_HEADS - _GUI_HEADS):
        return False
    rest = [a.strip().lower() for a in argv[1:]]
    if head.startswith("ipconfig"):
        return all(a in {"/all", "-all"} for a in rest)
    if head.startswith(("systeminfo", "tasklist")):
        remote_flags = {"/s", "-s", "/u", "-u", "/p", "-p"}
        return not any(a in remote_flags or a.startswith(("/s:", "/u:", "/p:")) for a in rest)
    return len(rest) == 0 if head.startswith(("whoami", "hostname")) else True


def _classify_argv_secrets(
    argv: list[str], expanded_argv: list[str], repo_root: Path | None, data_dir: Path | None
) -> PolicyVerdict | None:
    sec_kw = (
        "constitution vault.key credentials.enc companion.enc bossman.db api-token "
        "token secrets.py approvals.py permissions.py autonomy"
    ).split()
    for token in [*argv, *expanded_argv]:
        norm_tok = _norm_path(token)
        if any(p.search(token) or p.search(norm_tok) for p in _T3_SECRET_PATTERNS):
            return PolicyVerdict(
                "T3",
                "deny",
                "secret_path_in_argv",
                f"Аргумент секрета ({token}, T3).",
                True,
            )
        if "/" in norm_tok or "\\" in token or any(k in norm_tok.lower() for k in sec_kw):
            bad_t, why_t = is_t3_secret_or_protected_path(
                token, for_write=True, repo_root=repo_root, data_dir=data_dir
            )
            if bad_t:
                return PolicyVerdict("T3", "deny", "protected_path_in_argv", why_t, True)
    return None


def _classify_kill_argv(argv: list[str], joined: str) -> PolicyVerdict | None:
    if not re.search(r"(?i)\b(?:taskkill(?:\.exe)?|Stop-Process|kill)\b", joined):
        return None
    for tok in argv[1:]:
        if tok.startswith(("/", "-")):
            continue
        if tok.isdecimal():
            pr, why_pr = _check_proc_tree_protected(int(tok))
        else:
            pr, why_pr = is_protected_process(tok, "")
        if pr:
            return PolicyVerdict("T3", "deny", "protected_process", why_pr, True)
    return None


def _classify_cwd(
    cwd_raw: str, head: str, repo_root: Path | None, eff_roots: list[Path]
) -> PolicyVerdict | None:
    eff_repo = (repo_root or Path(__file__).resolve().parents[3]).resolve()
    with contextlib.suppress(OSError, ValueError):
        res_cwd = Path(_norm_path(cwd_raw)).expanduser().resolve()
        in_safe = is_within_safe_roots(cwd_raw, eff_roots) or res_cwd in {r.resolve() for r in eff_roots}
        if not in_safe and not res_cwd.is_relative_to(eff_repo):
            return PolicyVerdict("T2", "ask", "command_cwd_outside_workspace", f"CWD {cwd_raw} (T2).", True)
        if head in _GIT_HEADS and not res_cwd.is_relative_to(eff_repo):
            return PolicyVerdict(
                "T2",
                "ask",
                "git_foreign_cwd",
                f"Git в {cwd_raw} требует кнопки (T2).",
                True,
            )
    return None


def _classify_head_specific(
    argv: list[str], head: str, joined: str, expanded_joined: str, trust_active: bool
) -> PolicyVerdict:
    git_re = (
        r"(?i)^(?:-c|--(?:config-env|exec-path|ext-diff|textconv|"
        r"upload-pack|receive-pack|git-dir|work-tree|output|exec=))"
    )
    if head in _GIT_HEADS and any(re.match(git_re, a) for a in argv[1:]):
        return PolicyVerdict("T2", "ask", "git_option_exec", f"Опция git (`{joined[:90]}`) (T2).", True)
    if head in _GIT_HEADS:
        if _is_git_readonly_invocation(argv):
            return PolicyVerdict("T0", "auto", "git_read", "Чтение git (T0).")
        return PolicyVerdict(
            "T2", "trust_window" if trust_active else "ask", "git_mutation", f"Git `{joined[:90]}`."
        )
    if head in _PY_HEADS:
        if len(argv) == 2 and argv[1] in {"--version", "-V"}:
            return PolicyVerdict("T0", "auto", "python_version", "T0.")
        return PolicyVerdict("T2", "ask", "interpreter_execution", f"Python (`{joined[:90]}`) (T2).", True)
    t2_heads = _SAFE_ARGV_HEADS | {"taskkill", "taskkill.exe", "stop-process", "kill", "powercfg", "powercfg.exe"}
    for pat, cat in _T2_CMD_PATTERNS:
        if head in t2_heads and (pat.search(joined) or pat.search(expanded_joined)):
            decision = "trust_window" if trust_active else "ask"
            return PolicyVerdict("T2", decision, cat, f"Команда {cat} (T2).", False)
    if head not in _SAFE_ARGV_HEADS:
        return PolicyVerdict("T2", "ask", "unlisted_executable", f"{head!r} (T2).", True)
    if _is_readonly_sys_cmd(argv):
        return PolicyVerdict("T0", "auto", "command_read", f"Команда {head} (T0).")
    remote_heads = set(
        "tasklist tasklist.exe systeminfo systeminfo.exe whoami whoami.exe hostname hostname.exe".split()
    )
    if head in remote_heads:
        reason = f"Команда {joined[:90]} (T2)."
        return PolicyVerdict("T2", "ask", "remote_or_credentialed_sys_command", reason, True)
    if head in {"ipconfig", "ipconfig.exe", "systeminfo", "systeminfo.exe"}:
        decision = "trust_window" if trust_active else "ask"
        return PolicyVerdict("T2", decision, "network_or_remote_sys_command", "T2.")
    return PolicyVerdict("T1", "auto", "command_safe", f"Разрешённая команда {head} (T1).")


def _classify_run_command(
    args: dict[str, Any],
    task_prompt: str,
    trust_active: bool,
    repo_root: Path | None,
    eff_roots: list[Path],
    data_dir: Path | None,
) -> PolicyVerdict:
    argv = args.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x.strip() for x in argv):
        return PolicyVerdict(
            "T3",
            "deny",
            "invalid_argv",
            "Разрешён только непустой список argv.",
            True,
        )
    cwd_raw = str(args.get("cwd") or "").strip()
    if cwd_raw:
        bad_c, why_c = is_t3_secret_or_protected_path(
            cwd_raw, for_write=False, repo_root=repo_root, data_dir=data_dir
        )
        if bad_c:
            return PolicyVerdict("T3", "deny", "secret_or_protected_cwd", why_c, True)
    expanded_argv = [os.path.expandvars(x) for x in argv]
    joined, expanded_joined = " ".join(argv), " ".join(expanded_argv)
    for pat, cat in _T3_CMD_PATTERNS:
        if pat.search(joined) or pat.search(expanded_joined):
            return PolicyVerdict(
                "T3",
                "deny",
                cat,
                f"Команда обращается к ресурсу T3 ({cat}).",
                True,
            )
    ps_ok, ps_why = validate_powershell_ast_or_tokens(joined)
    if not ps_ok:
        return PolicyVerdict("T3", "deny", ps_why, f"Отказ PowerShell ({ps_why}).", True)
    verdict = _classify_argv_secrets(argv, expanded_argv, repo_root, data_dir)
    if verdict is None:
        verdict = _classify_kill_argv(argv, joined)
    if verdict is not None:
        return verdict
    head = Path(argv[0]).name.lower()
    is_safe_read = (
        _is_readonly_sys_cmd(argv)
        or (head in _GIT_HEADS and _is_git_readonly_invocation(argv))
        or (head in _PY_HEADS and len(argv) == 2 and argv[1] in {"--version", "-V"})
    )
    if PromptIntentReadOnly(task_prompt) and (not is_safe_read or _DANGEROUS_SHELL_CHAIN_RE.search(joined)):
        return PolicyVerdict(
            "T3",
            "deny",
            "mutation_command_in_readonly",
            "Команда отклонена в чтении.",
            True,
        )
    if ("/" in argv[0] or "\\" in argv[0] or ":" in argv[0]) and not _is_system32_bin(argv[0]):
        return PolicyVerdict(
            "T2",
            "ask",
            "unlisted_executable_path",
            f"Путь {argv[0]} требует кнопки (T2).",
            True,
        )
    if cwd_raw:
        verdict = _classify_cwd(cwd_raw, head, repo_root, eff_roots)
        if verdict is not None:
            return verdict
    for pat, cat in _T2_ALWAYS_BUTTON_PATTERNS:
        if pat.search(joined) or pat.search(expanded_joined):
            return PolicyVerdict(
                "T2",
                "ask",
                cat,
                f"Команда «{cat}» ({joined[:90]}) требует кнопки (T2).",
                True,
            )
    if _DANGEROUS_SHELL_CHAIN_RE.search(joined) or _DANGEROUS_SHELL_CHAIN_RE.search(expanded_joined):
        return PolicyVerdict(
            "T2",
            "ask",
            "shell_chain",
            f"Цепочка ({joined[:90]}) требует кнопки (T2).",
            True,
        )
    return _classify_head_specific(argv, head, joined, expanded_joined, trust_active)
