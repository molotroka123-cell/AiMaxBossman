"""Чистый модуль политики Argon PC-Agent: команды, API и классификатор T0–T3 (без БД и сети)."""
from __future__ import annotations
import contextlib, json, math, os, posixpath, re
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from .pc_agent_policy_paths import (
    HARD_INVARIANTS, PROTECTED_CMDLINE_TOKENS, PROTECTED_PROCESS_NAMES, PROTECTED_SERVICE_NAMES,
    PolicyVerdict, PromptIntentReadOnly, _CLOUD_PERSONAL_MEDIA_RE, _CSAM_RE, _NCII_RE,
    _PROMPT_INJECTION_RE, _SENSITIVE_ENV_RE, _T3_SECRET_PATTERNS, _UNSAFE_WRITE_SUFFIXES,
    _check_proc_tree_protected, _collect_runtime_secrets, _has_ntfs_ads, _is_safe_root_itself,
    _is_system32_bin, _is_unc, _norm_path, check_hard_content_invariants,
    classify_path_proc_service_action, default_safe_roots, is_protected_process,
    is_protected_service, is_t3_secret_or_protected_path, is_within_safe_roots, scrub_secrets,
)
_T3_CMD_PATTERNS = tuple((re.compile(p, re.I), c) for p, c in (
    (r"\bgit\b[^\n]*\bpush\b(?:[^\n]*\s(?:--force|-f|--force-with-lease)\b|[^\n]*\s\+[A-Za-z0-9_./-]+)",
     "force_push_forbidden"),
    (r"\bgit\b[^\n]*\b(?:commit|push|merge)\b[^\n]*\s(?:--no-verify|-n)\b", "selftest_bypass_forbidden"),
    (r"\b(?:STOP_WATCHDOG|DISABLE_AUDIT|BOSSMAN_SKIP_SELFTEST)\b", "disable_watchdog_or_audit_forbidden"),
    (r"\b(?:Set-MpPreference|Add-MpPreference)\b[^\n]*Disable", "disable_defender_forbidden"),
    (r"\bnetsh\b[^\n]*\badvfirewall\b[^\n]*\bstate\s+off\b", "disable_firewall_forbidden"),
    (r"\breg\s+(?:add|delete)\b[^\n]*Policies\\System[^\n]*EnableLUA", "disable_uac_forbidden"),
    (r"\b(?:adopt_constitution_v2\.ps1|constitution\.sha256)\b", "constitution_pin_owner_only"),
    (r"\b(?:vault\.key|credentials\.enc|provider-keys\.env|api-token|companion\.enc)\b", "secret_store_forbidden"),
    (r"\b(?:CryptUnprotectData|ProtectedData::Unprotect)\b", "dpapi_unprotect_forbidden"),
    (r"\bbcdedit(?:\.exe)?\b", "bcdedit_boot_config_forbidden"),
))
_T2_ALWAYS_BUTTON_PATTERNS = tuple((re.compile(p, re.I), c) for p, c in (
    (r"\b(?:shutdown(?:\.exe)?|Restart-Computer|Stop-Computer)\b", "system_power"),
    (r"\bgit\b[^\n]*\b(?:push|merge|reset\s+--hard|clean\s+-[A-Za-z]*f|checkout\s+--)\b",
     "git_destructive_or_promotion"),
    (r"\b(?:Remove-Item|del|erase|rm|rmdir|rd)\b", "file_delete"),
    (r"\b(?:Format-Volume|Clear-Disk|cipher\s+/w)\b", "disk_wipe"),
    (r"\b(?:powershell|pwsh)(?:\.exe)?\b[^\n]*(?:\s-(?:e|ec|enc\w*|encodedcommand|c|command|f|file)\b"
     r"|\bIEX\b|\bInvoke-Expression\b)", "powershell_inline_exec"),
    (r"\bcmd(?:\.exe)?\b[^\n]*\s/[ck]\b", "cmd_inline_exec"),
    (r"\b(?:schtasks|certutil|bitsadmin|wmic|setx|icacls|takeown)(?:\.exe)?\b", "system_persistence_or_acl"),
    (r"\bnet(?:\.exe|1\.exe)?\s+(?:user|localgroup|group)\b", "account_mutation"),
    (r"\b(?:python|python3|py)(?:\.exe)?\b[^\n]*\s-[cm]\b", "python_inline_exec"),
    (r"\b(?:node|deno|bun)(?:\.exe)?\b[^\n]*\s(?:-e|-p|--eval)\b", "node_inline_exec"),
    (r"\b(?:curl|scp|sftp|ftp)(?:\.exe)?\b|\b(?:Invoke-WebRequest|Invoke-RestMethod|Start-BitsTransfer)\b",
     "network_transfer"),
    (r"\b(?:reg(?:\.exe)?\b|New-ItemProperty|Set-ItemProperty|Remove-ItemProperty)\b", "registry_mutation"),
    (r"\b(?:sc(?:\.exe)?\b|Stop-Service|Set-Service|New-Service|Restart-Service)\b", "system_service"),
    (r"\b(?:msiexec|winget|choco)(?:\.exe)?\b|\b(?:pip|pip3|npm|pnpm|yarn)\s+install\b|\buv\s+pip\s+install\b",
     "software_install"),
))
_T2_CMD_PATTERNS = tuple((re.compile(p, re.I), c) for p, c in (
    (r"\b(?:taskkill(?:\.exe)?|Stop-Process|kill)\b", "process_kill"),
    (r"\bwhoami(?:\.exe)?\b[^\n]*(?:/priv|/groups|/all)\b", "privilege_enumeration"),
    (r"\bpowercfg(?:\.exe)?\b", "power_config"),
))
_SAFE_ARGV_HEADS = frozenset((
    "where where.exe whoami whoami.exe hostname hostname.exe systeminfo systeminfo.exe ipconfig "
    "ipconfig.exe tasklist tasklist.exe notepad notepad.exe calc calc.exe mspaint mspaint.exe charmap charmap.exe"
).split())
_GIT_READONLY_SUBCOMMANDS = frozenset("status diff log branch rev-parse show".split())
_GIT_BRANCH_SAFE_ARGS = frozenset("--show-current -a --all -r --remotes -v -vv --list".split())
_DANGEROUS_SHELL_CHAIN_RE = re.compile(
    r"(?:&&|\|\||;\s*(?:Remove-Item|del|rm|taskkill|shutdown|reg|curl|Invoke-WebRequest)"
    r"|\bIEX\b|\bInvoke-Expression\b|\bEncodedCommand\b|-enc\b)", re.I,
)
_OBSERVE_TOOLS = frozenset(
    "pc.observe_screen pc.list_windows pc.list_processes pc.list_services pc.system_info pc.bossman_status".split()
)
def _is_git_readonly_invocation(argv: list[str]) -> bool:
    if len(argv) < 2 or argv[1].lower() not in _GIT_READONLY_SUBCOMMANDS: return False
    bad_o = set("-c --config-env --exec-path --git-dir --work-tree --no-index --ext-diff --textconv".split())
    bad_p = ("--output", "--exec=", "--git-dir=", "--work-tree=")
    if any(a.lower() in bad_o or a.lower().startswith(bad_p) for a in argv[2:]): return False
    return all(a in _GIT_BRANCH_SAFE_ARGS for a in argv[2:]) if argv[1].lower() == "branch" else True
def _is_readonly_sys_cmd(argv: list[str]) -> bool:
    if not argv: return False
    head, gui_h = Path(argv[0]).name.lower(), set("notepad calc mspaint charmap".split())
    if head.removesuffix(".exe") not in (_SAFE_ARGV_HEADS - gui_h): return False
    rest = [a.strip().lower() for a in argv[1:]]
    if head.startswith("ipconfig"): return all(a in {"/all", "-all"} for a in rest)
    if head.startswith(("systeminfo", "tasklist")):
        return not any(a in {"/s", "-s", "/u", "-u", "/p", "-p"} or a.startswith(("/s:", "/u:", "/p:")) for a in rest)
    return len(rest) == 0 if head.startswith(("whoami", "hostname")) else True
def normalize_bossman_api_path(raw_path: str) -> tuple[str, bool]:
    raw = str(raw_path or "").strip()
    if not raw: return "", True
    cur, suspicious = raw.split("?", 1)[0].split("#", 1)[0], bool(re.search(r"(?i)(?:%2e|%2f|%5c|%00|\.\.|\\|//)", raw))
    for _ in range(3):
        if unquote(cur) == cur: break
        cur = unquote(cur)
    cur = cur.replace("\\", "/")
    suspicious = suspicious or (".." in cur.split("/")) or ("\x00" in cur)
    norm = posixpath.normpath(re.sub(r"/+", "/", cur)).lower()
    return (norm if norm.startswith("/") else "/" + norm), suspicious
def classify_bossman_api(method: str, raw_path: str, body: Any = None, data_dir: Path | None = None) -> PolicyVerdict:
    m, (norm_path, suspicious) = str(method or "GET").strip().upper(), normalize_bossman_api_path(raw_path)
    forbidden = (
        "/api/approvals /api/autonomy /api/control-plane /api/providers /api/secrets /api/auth "
        "/api/pc-agent/trust /api/pc-agent/untrust /api/pc-agent/stop /api/pc-agent/resume /approve /rollback".split()
    )
    if suspicious or not norm_path.startswith("/api/") or any(f in norm_path for f in forbidden):
        return PolicyVerdict("T3", "deny", "bossman_api_forbidden", f"Вызов {m} {raw_path} запрещён (T3).", True)
    t0_paths = {"/api/system", "/api/models", "/api/agents", "/api/status", "/api/health/live"}
    if m == "GET" and (norm_path in t0_paths or norm_path.startswith("/api/motion-")):
        return PolicyVerdict("T0", "auto", "bossman_api_read", f"Чтение Bossman API {norm_path} (T0).")
    if m == "POST" and norm_path in {"/api/motion-studio/preview"}:
        if body is not None:
            pat = r'[A-Za-z]:[\\/][^"\'\s,;}{]+|\\\\[^"\'\s,;}{]+|\.[\\/][^"\'\s,;}{]+'
            for m_tok in re.findall(pat, json.dumps(body, ensure_ascii=False, default=str)):
                bad_b, why_b = is_t3_secret_or_protected_path(m_tok, for_write=False, data_dir=data_dir)
                if bad_b: return PolicyVerdict("T3", "deny", "bossman_api_secret_path", why_b, True)
                if not is_within_safe_roots(m_tok, default_safe_roots(data_dir)):
                    return PolicyVerdict("T2", "ask", "bossman_api_motion_unsafe_path", f"Путь {m_tok} (T2).", True)
        return PolicyVerdict("T1", "auto", "bossman_api_motion", f"Вызов {m} {norm_path} (T1).")
    return PolicyVerdict("T2", "ask", "bossman_api_unlisted", f"Вызов {m} {norm_path} вне белого списка (T2).", True)
def is_bossman_api_token_allowed(method: str, raw_path: str, body: Any = None, data_dir: Path | None = None) -> bool:
    v = classify_bossman_api(method, raw_path, body=body, data_dir=data_dir)
    return v.tier in {"T0", "T1"} and v.decision == "auto"
def _classify_run_command(
    args: dict[str, Any], task_prompt: str, trust_active: bool,
    repo_root: Path | None, eff_roots: list[Path], data_dir: Path | None,
) -> PolicyVerdict:
    argv = args.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x.strip() for x in argv):
        return PolicyVerdict("T3", "deny", "invalid_argv", "Разрешён только непустой список argv.", True)
    cwd_raw = str(args.get("cwd") or "").strip()
    if cwd_raw:
        bad_c, why_c = is_t3_secret_or_protected_path(cwd_raw, for_write=False, repo_root=repo_root, data_dir=data_dir)
        if bad_c: return PolicyVerdict("T3", "deny", "secret_or_protected_cwd", why_c, True)
    expanded_argv = [os.path.expandvars(x) for x in argv]
    joined, expanded_joined = " ".join(argv), " ".join(expanded_argv)
    for pat, cat in _T3_CMD_PATTERNS:
        if pat.search(joined) or pat.search(expanded_joined):
            return PolicyVerdict("T3", "deny", cat, f"Команда обращается к ресурсу T3 ({cat}).", True)
    sec_kw = (
        "constitution vault.key credentials.enc companion.enc bossman.db api-token "
        "token secrets.py approvals.py permissions.py autonomy".split()
    )
    for token in [*argv, *expanded_argv]:
        norm_tok = _norm_path(token)
        if any(p.search(token) or p.search(norm_tok) for p in _T3_SECRET_PATTERNS):
            return PolicyVerdict("T3", "deny", "secret_path_in_argv", f"Аргумент секрета ({token}, T3).", True)
        if "/" in norm_tok or "\\" in token or any(k in norm_tok.lower() for k in sec_kw):
            bad_t, why_t = is_t3_secret_or_protected_path(token, for_write=True, repo_root=repo_root, data_dir=data_dir)
            if bad_t: return PolicyVerdict("T3", "deny", "protected_path_in_argv", why_t, True)
    if re.search(r"(?i)\b(?:taskkill(?:\.exe)?|Stop-Process|kill)\b", joined):
        for tok in argv[1:]:
            if not tok.startswith(("/", "-")):
                pr, why_pr = _check_proc_tree_protected(int(tok)) if tok.isdecimal() else is_protected_process(tok, "")
                if pr: return PolicyVerdict("T3", "deny", "protected_process", why_pr, True)
    head, py_heads = Path(argv[0]).name.lower(), {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}
    if PromptIntentReadOnly(task_prompt) and (
        not (_is_readonly_sys_cmd(argv) or (head in {"git", "git.exe"} and _is_git_readonly_invocation(argv))
             or (head in py_heads and len(argv) == 2 and argv[1] in {"--version", "-V"}))
        or _DANGEROUS_SHELL_CHAIN_RE.search(joined)
    ): return PolicyVerdict("T3", "deny", "mutation_command_in_readonly", "Команда отклонена в чтении.", True)
    if ("/" in argv[0] or "\\" in argv[0] or ":" in argv[0]) and not _is_system32_bin(argv[0]):
        return PolicyVerdict("T2", "ask", "unlisted_executable_path", f"Путь {argv[0]} требует кнопки (T2).", True)
    if cwd_raw:
        eff_repo = (repo_root or Path(__file__).resolve().parents[3]).resolve()
        with contextlib.suppress(OSError, ValueError):
            res_cwd = Path(_norm_path(cwd_raw)).expanduser().resolve()
            in_safe = is_within_safe_roots(cwd_raw, eff_roots) or res_cwd in {r.resolve() for r in eff_roots}
            if not in_safe and not res_cwd.is_relative_to(eff_repo):
                return PolicyVerdict("T2", "ask", "command_cwd_outside_workspace", f"CWD {cwd_raw} (T2).", True)
            if head in {"git", "git.exe"} and not res_cwd.is_relative_to(eff_repo):
                return PolicyVerdict("T2", "ask", "git_foreign_cwd", f"Git в {cwd_raw} требует кнопки (T2).", True)
    for pat, cat in _T2_ALWAYS_BUTTON_PATTERNS:
        if pat.search(joined) or pat.search(expanded_joined):
            return PolicyVerdict("T2", "ask", cat, f"Команда «{cat}» ({joined[:90]}) требует кнопки (T2).", True)
    if _DANGEROUS_SHELL_CHAIN_RE.search(joined) or _DANGEROUS_SHELL_CHAIN_RE.search(expanded_joined):
        return PolicyVerdict("T2", "ask", "shell_chain", f"Цепочка ({joined[:90]}) требует кнопки (T2).", True)
    git_re = (r"(?i)^(?:-c|--(?:config-env|exec-path|ext-diff|textconv|"
              r"upload-pack|receive-pack|git-dir|work-tree|output|exec=))")
    if head in {"git", "git.exe"} and any(re.match(git_re, a) for a in argv[1:]):
        return PolicyVerdict("T2", "ask", "git_option_exec", f"Опция git (`{joined[:90]}`) (T2).", True)
    if head in {"git", "git.exe"}:
        if _is_git_readonly_invocation(argv): return PolicyVerdict("T0", "auto", "git_read", "Чтение git (T0).")
        return PolicyVerdict("T2", "trust_window" if trust_active else "ask", "git_mutation", f"Git `{joined[:90]}`.")
    if head in py_heads:
        if len(argv) == 2 and argv[1] in {"--version", "-V"}:
            return PolicyVerdict("T0", "auto", "python_version", "T0.")
        return PolicyVerdict("T2", "ask", "interpreter_execution", f"Python (`{joined[:90]}`) (T2).", True)
    t2_heads = _SAFE_ARGV_HEADS | {"taskkill", "taskkill.exe", "stop-process", "kill", "powercfg", "powercfg.exe"}
    for pat, cat in _T2_CMD_PATTERNS:
        if head in t2_heads and (pat.search(joined) or pat.search(expanded_joined)):
            return PolicyVerdict("T2", "trust_window" if trust_active else "ask", cat, f"Команда {cat} (T2).", False)
    if head not in _SAFE_ARGV_HEADS: return PolicyVerdict("T2", "ask", "unlisted_executable", f"{head!r} (T2).", True)
    if _is_readonly_sys_cmd(argv): return PolicyVerdict("T0", "auto", "command_read", f"Команда {head} (T0).")
    if head in set("tasklist tasklist.exe systeminfo systeminfo.exe whoami whoami.exe hostname hostname.exe".split()):
        return PolicyVerdict("T2", "ask", "remote_or_credentialed_sys_command", f"Команда {joined[:90]} (T2).", True)
    if head in {"ipconfig", "ipconfig.exe", "systeminfo", "systeminfo.exe"}:
        return PolicyVerdict("T2", "trust_window" if trust_active else "ask", "network_or_remote_sys_command", "T2.")
    return PolicyVerdict("T1", "auto", "command_safe", f"Разрешённая команда {head} (T1).")
def classify_pc_action(
    tool: str, args: dict[str, Any], *, task_prompt: str = "", tainted_by_injection: bool = False,
    trust_active: bool = False, repo_root: Path | None = None,
    safe_roots: list[Path] | None = None, data_dir: Path | None = None,
) -> PolicyVerdict:
    args = dict(args or {})
    blob = json.dumps({"tool": tool, "args": args}, ensure_ascii=False, default=str)
    inv = check_hard_content_invariants(blob) or check_hard_content_invariants(task_prompt)
    if inv is not None: return inv
    if tainted_by_injection and tool not in _OBSERVE_TOOLS:
        return PolicyVerdict("T3", "deny", "prompt_injection_blocked", "Заблокировано (prompt injection).", True)
    if tool in _OBSERVE_TOOLS: return PolicyVerdict("T0", "auto", "observe", "Чтение состояния ПК (T0).")
    eff_roots = safe_roots if safe_roots is not None else default_safe_roots(data_dir)
    pps = classify_path_proc_service_action(
        tool, args, task_prompt=task_prompt, trust_active=trust_active,
        repo_root=repo_root, eff_roots=eff_roots, data_dir=data_dir,
    )
    if pps is not None: return pps
    if tool == "pc.desktop_act":
        action = str(args.get("action") or "").strip().lower()
        target, semantic = str(args.get("target") or "").strip(), str(args.get("semantic") or "").strip().lower()
        desk_path = str(args.get("path") or args.get("dst") or "").strip()
        pay_sem = {"pay", "payment", "purchase", "wallet", "bank_transfer", "resume", "approve yourself"}
        pay_re = r"(?iu)\b(оплат\w*|перевод\w*|купить|pay|purchase|wallet|bossman\s+approvals)\b"
        if semantic in pay_sem or re.search(pay_re, f"{target} {semantic}"):
            return PolicyVerdict("T3", "deny", "desktop_payment_or_gate", f"Цель «{target or semantic}» (T3).", True)
        if action == "notepad_roundtrip" or desk_path:
            def_p = (Path(data_dir) / "pc-agent" / "workspace" / "notepad_note.txt") if data_dir else (
                Path.home() / "Desktop" / "notepad_note.txt"
            )
            check_p = desk_path or str(def_p)
            bad_p, why_p = is_t3_secret_or_protected_path(
                check_p, for_write=True, repo_root=repo_root, data_dir=data_dir
            )
            if bad_p: return PolicyVerdict("T3", "deny", "secret_or_protected_path", why_p, True)
            if PromptIntentReadOnly(task_prompt):
                return PolicyVerdict("T3", "deny", "unrequested_mutation", "Запись через GUI в чтении (T3).", True)
            if not is_within_safe_roots(check_p, eff_roots):
                return PolicyVerdict("T2", "ask", "filesystem_write_outside_workspace", f"Путь {check_p} (T2).", True)
        if action == "launch":
            app_raw = str(args.get("app") or target or "notepad").strip()
            has_sep = "/" in app_raw or "\\" in app_raw or ":" in app_raw
            base_app = Path(app_raw).name.lower().removesuffix(".exe")
            if (has_sep and not _is_system32_bin(app_raw)) or base_app not in {"notepad", "calc", "mspaint", "charmap"}:
                return PolicyVerdict("T2", "ask", "desktop_launch_unlisted", "Запуск требует кнопки (T2).", True)
        conseq_re = r"(?iu)\b(удал\w*|delete|uninstall|форматир\w*|отправ\w*|публик\w*|send|publish|submit)\b"
        if re.search(conseq_re, f"{target} {semantic}"):
            return PolicyVerdict("T2", "trust_window" if trust_active else "ask", "desktop_consequential", "T2.")
        rev_acts = set(
            "launch focus focus_window click double_click type key hotkey scroll wait notepad_roundtrip".split()
        )
        if action in rev_acts: return PolicyVerdict("T1", "auto", "desktop_reversible", f"Действие {action} (T1).")
        return PolicyVerdict("T2", "ask", "desktop_unknown", f"Действие {action} (T2).")
    if tool == "pc.browser_act":
        action, url = str(args.get("action") or "open").strip().lower(), str(args.get("url") or "").strip()
        if action in {"purchase", "payment", "wallet", "bank_transfer"}:
            return PolicyVerdict("T3", "deny", "browser_payment_forbidden", f"Платежи запрещены ({action}).", True)
        if url and (url.split(":", 1)[0].lower() if ":" in url else "") not in {"http", "https"}:
            return PolicyVerdict("T3", "deny", "browser_invalid_scheme", "Разрешены только http/https.", True)
        if action in {"read_dom", "snapshot", "screenshot"}:
            return PolicyVerdict("T0", "auto", "browser_read", f"Чтение браузера ({action}, T0).")
        if action in {"open", "navigate", "click", "type", "select", "back", "reload"}:
            lbl = str(args.get("target") or args.get("selector") or args.get("text") or "")
            if action == "click" and re.search(r"(?iu)\b(оплат\w*|купить|buy|pay|checkout)\b", lbl):
                return PolicyVerdict("T3", "deny", "browser_payment_click", f"Клик оплаты «{lbl}» (T3).", True)
            conseq_click = r"(?iu)\b(удал\w*|отправ\w*|опублик\w*|delete|submit|send|publish)\b"
            if action == "click" and re.search(conseq_click, lbl):
                return PolicyVerdict("T2", "ask", "browser_consequential_click", f"Клик «{lbl}» (T2).", True)
            if action in {"click", "type"} and args.get("ref") is not None and not lbl.strip():
                return PolicyVerdict("T2", "trust_window" if trust_active else "ask", "browser_opaque_ref", "Ref (T2).")
            return PolicyVerdict("T1", "auto", "browser_interactive", f"Действие в браузере ({action}, T1).")
        if action in {"submit", "upload", "download", "login"}:
            always_btn = action in {"submit", "login", "upload"}
            dec = "trust_window" if (trust_active and not always_btn) else "ask"
            return PolicyVerdict("T2", dec, f"browser_{action}", f"Браузер {action} (T2).", always_btn)
        return PolicyVerdict("T2", "ask", "browser_other", f"Действие браузера {action} (T2).")
    if tool == "pc.run_command":
        return _classify_run_command(args, task_prompt, trust_active, repo_root, eff_roots, data_dir)
    if tool == "pc.bossman_api":
        return classify_bossman_api(
            str(args.get("method") or "GET"), str(args.get("path") or ""),
            body=args.get("payload") or args.get("body"), data_dir=data_dir,
        )
    if tool == "pc.earn":
        action = str(args.get("action") or "status").lower()
        mode_arg, ed_arg = str(args.get("mode") or "").strip().lower(), str(args.get("edition") or "").strip().lower()
        try:
            rm = float(args.get("real_money") or 0.0)
            rm = 1.0 if not math.isfinite(rm) else rm
        except (TypeError, ValueError): rm = 1.0
        t3_acts = set(
            "pay withdraw live_trade publish_external submit_bid "
            "fincept_enterprise fincept_live_broker vibe_trading_live".split()
        )
        if action in t3_acts or mode_arg in {"live", "real", "real_money", "broker"} or ed_arg in {
            "enterprise", "exclusive", "exclusive+", "exclusive_pro", "paid"
        } or rm > 0.0:
            return PolicyVerdict("T3", "deny", "earn_external_or_live_forbidden", f"Действие {action} (T3).", True)
        if action == "fresh_vibes_publish":
            return PolicyVerdict("T2", "ask", "fresh_vibes_external_publish", "Публикация в Instagram (T2).", True)
        t0_earn = set(
            "status arena exam roi poker studio oss product weekly report "
            "blender fincept vibe_trading vibe_trade".split()
        )
        if action in t0_earn: return PolicyVerdict("T0", "auto", f"earn_{action}", f"Чтение Earn ({action}, T0).")
        return PolicyVerdict("T1", "auto", f"earn_{action}", f"Earn ({action}, SIMULATED, T1).")
    return PolicyVerdict("T2", "ask", "unknown_tool", f"Неизвестный инструмент {tool} (T2).", True)
