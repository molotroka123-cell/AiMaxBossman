"""Политика Argon PC-Agent: классификатор T0–T3 (без БД и сети).

Публичные имена сохранены: константы, пути, процессы и команды
ре-экспортируются из подмодулей.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from .pc_agent_policy_guards import (
    BROWSER_PAY_CLICK_RE,
    _BROWSER_CONSEQUENTIAL_CLICK_RE,
    _DESKTOP_CONSEQUENTIAL_RE,
    _DESKTOP_LAUNCH_ALLOWED,
    _DESKTOP_PAY_RE,
    _DESKTOP_PAY_SEMANTICS,
    _DESKTOP_REVERSIBLE_ACTIONS,
    _EARN_T0_ACTIONS,
    _EARN_T3_ACTIONS,
    _EARN_T3_EDITIONS,
    _EARN_T3_MODES,
    browser_guard_verdict,
    canonicalize_windows_path_safe,
    desktop_guard_verdict,
    is_immutable_audit_log_target,
    validate_powershell_ast_or_tokens,
)
from .pc_agent_policy_api import (  # noqa: F401  (ре-экспорт публичных имён)
    classify_bossman_api,
    is_bossman_api_token_allowed,
    normalize_bossman_api_path,
)
from .pc_agent_policy_cmds import (  # noqa: F401
    _classify_run_command,
    _is_git_readonly_invocation,
    _is_readonly_sys_cmd,
)
from .pc_agent_policy_consts import (  # noqa: F401
    HARD_INVARIANTS,
    PROTECTED_CMDLINE_TOKENS,
    PROTECTED_PROCESS_NAMES,
    PROTECTED_SERVICE_NAMES,
    PolicyVerdict,
    _CLOUD_PERSONAL_MEDIA_RE,
    _CSAM_RE,
    _DANGEROUS_SHELL_CHAIN_RE,
    _GIT_BRANCH_SAFE_ARGS,
    _GIT_READONLY_SUBCOMMANDS,
    _NCII_RE,
    _OBSERVE_TOOLS,
    _PROMPT_INJECTION_RE,
    _SAFE_ARGV_HEADS,
    _SENSITIVE_ENV_RE,
    _T2_ALWAYS_BUTTON_PATTERNS,
    _T2_CMD_PATTERNS,
    _T3_CMD_PATTERNS,
    _T3_SECRET_PATTERNS,
    _UNSAFE_WRITE_SUFFIXES,
)
from .pc_agent_policy_paths import (  # noqa: F401
    PromptIntentReadOnly,
    _collect_runtime_secrets,
    _has_ntfs_ads,
    _is_safe_root_itself,
    _is_system32_bin,
    _is_unc,
    _norm_path,
    check_hard_content_invariants,
    default_safe_roots,
    is_t3_secret_or_protected_path,
    is_within_safe_roots,
    scrub_secrets,
)
from .pc_agent_policy_procs import (  # noqa: F401
    _check_proc_tree_protected,
    classify_path_proc_service_action,
    is_protected_process,
    is_protected_service,
)

_BROWSER_PAY_CLICK_RE = BROWSER_PAY_CLICK_RE


def _classify_desktop_act(
    args: dict[str, Any],
    task_prompt: str,
    trust_active: bool,
    repo_root: Path | None,
    eff_roots: list[Path],
    data_dir: Path | None,
) -> PolicyVerdict:
    action = str(args.get("action") or "").strip().lower()
    target = str(args.get("target") or "").strip()
    semantic = str(args.get("semantic") or "").strip().lower()
    desk_path = str(args.get("path") or args.get("dst") or "").strip()
    if semantic in _DESKTOP_PAY_SEMANTICS or re.search(_DESKTOP_PAY_RE, f"{target} {semantic}"):
        reason = f"Цель «{target or semantic}» (T3)."
        return PolicyVerdict("T3", "deny", "desktop_payment_or_gate", reason, True)
    guard = desktop_guard_verdict(action, target, semantic, args)
    if guard:
        return guard
    if action == "notepad_roundtrip" or desk_path:
        if data_dir:
            def_p = Path(data_dir) / "pc-agent" / "workspace" / "notepad_note.txt"
        else:
            def_p = Path.home() / "Desktop" / "notepad_note.txt"
        check_p = desk_path or str(def_p)
        bad_p, why_p = is_t3_secret_or_protected_path(
            check_p, for_write=True, repo_root=repo_root, data_dir=data_dir
        )
        if bad_p:
            return PolicyVerdict("T3", "deny", "secret_or_protected_path", why_p, True)
        if PromptIntentReadOnly(task_prompt):
            return PolicyVerdict("T3", "deny", "unrequested_mutation", "Запись в чтении (T3).", True)
        if not is_within_safe_roots(check_p, eff_roots):
            return PolicyVerdict("T2", "ask", "filesystem_write_outside_workspace", f"Путь {check_p} (T2).", True)
    if action == "launch":
        app_raw = str(args.get("app") or target or "notepad").strip()
        has_sep = "/" in app_raw or "\\" in app_raw or ":" in app_raw
        base_app = Path(app_raw).name.lower().removesuffix(".exe")
        if (has_sep and not _is_system32_bin(app_raw)) or base_app not in _DESKTOP_LAUNCH_ALLOWED:
            return PolicyVerdict("T2", "ask", "desktop_launch_unlisted", "Запуск (T2).", True)
    if re.search(_DESKTOP_CONSEQUENTIAL_RE, f"{target} {semantic}"):
        decision = "trust_window" if trust_active else "ask"
        return PolicyVerdict("T2", decision, "desktop_consequential", "T2.")
    if action in _DESKTOP_REVERSIBLE_ACTIONS:
        return PolicyVerdict("T1", "auto", "desktop_reversible", f"Действие {action} (T1).")
    return PolicyVerdict("T2", "ask", "desktop_unknown", f"Действие {action} (T2).")


def _classify_browser_act(args: dict[str, Any], trust_active: bool) -> PolicyVerdict:
    action = str(args.get("action") or "open").strip().lower()
    url = str(args.get("url") or "").strip()
    if action in {"purchase", "payment", "wallet", "bank_transfer"}:
        return PolicyVerdict("T3", "deny", "browser_payment_forbidden", f"Оплата ({action}).", True)
    scheme = url.split(":", 1)[0].lower() if ":" in url else ""
    if url and scheme not in {"http", "https"}:
        return PolicyVerdict("T3", "deny", "browser_invalid_scheme", "Только http/https.", True)
    if action in {"read_dom", "snapshot", "screenshot"}:
        return PolicyVerdict("T0", "auto", "browser_read", f"Чтение браузера ({action}, T0).")
    if action in {"open", "navigate", "click", "type", "select", "back", "reload"}:
        lbl = str(args.get("target") or args.get("selector") or args.get("text") or "")
        guard = browser_guard_verdict(action, url, lbl)
        if guard:
            return guard
        if action == "click" and re.search(_BROWSER_PAY_CLICK_RE, lbl):
            return PolicyVerdict("T3", "deny", "browser_payment_click", f"Клик оплаты «{lbl}» (T3).", True)
        if action == "click" and re.search(_BROWSER_CONSEQUENTIAL_CLICK_RE, lbl):
            return PolicyVerdict("T2", "ask", "browser_consequential_click", f"Клик «{lbl}» (T2).", True)
        if action in {"click", "type"} and args.get("ref") is not None and not lbl.strip():
            decision = "trust_window" if trust_active else "ask"
            return PolicyVerdict("T2", decision, "browser_opaque_ref", "Ref (T2).")
        return PolicyVerdict("T1", "auto", "browser_interactive", f"Браузер ({action}, T1).")
    if action in {"submit", "upload", "download", "login"}:
        always_btn = action in {"submit", "login", "upload"}
        dec = "trust_window" if (trust_active and not always_btn) else "ask"
        return PolicyVerdict("T2", dec, f"browser_{action}", f"Браузер {action} (T2).", always_btn)
    return PolicyVerdict("T2", "ask", "browser_other", f"Действие браузера {action} (T2).")


def _classify_earn(args: dict[str, Any]) -> PolicyVerdict:
    action = str(args.get("action") or "status").lower()
    mode_arg = str(args.get("mode") or "").strip().lower()
    ed_arg = str(args.get("edition") or "").strip().lower()
    try:
        rm = float(args.get("real_money") or 0.0)
        rm = 1.0 if not math.isfinite(rm) else rm
    except (TypeError, ValueError):
        rm = 1.0
    external = action in _EARN_T3_ACTIONS or mode_arg in _EARN_T3_MODES or ed_arg in _EARN_T3_EDITIONS
    if external or rm > 0.0:
        return PolicyVerdict("T3", "deny", "earn_external_or_live_forbidden", f"Действие {action} (T3).", True)
    if action == "fresh_vibes_publish":
        return PolicyVerdict("T2", "ask", "fresh_vibes_external_publish", "Публикация (T2).", True)
    if action in _EARN_T0_ACTIONS:
        return PolicyVerdict("T0", "auto", f"earn_{action}", f"Чтение Earn ({action}, T0).")
    return PolicyVerdict("T1", "auto", f"earn_{action}", f"Earn ({action}, SIMULATED, T1).")


def classify_pc_action(
    tool: str,
    args: dict[str, Any],
    *,
    task_prompt: str = "",
    tainted_by_injection: bool = False,
    trust_active: bool = False,
    repo_root: Path | None = None,
    safe_roots: list[Path] | None = None,
    data_dir: Path | None = None,
) -> PolicyVerdict:
    args = dict(args or {})
    blob = json.dumps({"tool": tool, "args": args}, ensure_ascii=False, default=str)
    inv = check_hard_content_invariants(blob) or check_hard_content_invariants(task_prompt)
    if inv is not None:
        return inv
    if is_immutable_audit_log_target(task_prompt) or (
        tool not in _OBSERVE_TOOLS and is_immutable_audit_log_target(blob)
    ):
        return PolicyVerdict(
            "T3", "deny", "blocked_immutable_audit_log",
            "Журнал аудита защищён от удаления и модификации (T3).", True,
        )
    if tainted_by_injection and tool not in _OBSERVE_TOOLS:
        return PolicyVerdict("T3", "deny", "prompt_injection_blocked", "Prompt injection (T3).", True)
    if tool in _OBSERVE_TOOLS:
        return PolicyVerdict("T0", "auto", "observe", "Чтение состояния ПК (T0).")
    eff_roots = safe_roots if safe_roots is not None else default_safe_roots(data_dir)
    pps = classify_path_proc_service_action(
        tool, args, task_prompt=task_prompt, trust_active=trust_active,
        repo_root=repo_root, eff_roots=eff_roots, data_dir=data_dir,
    )
    if pps is not None:
        return pps
    if tool == "pc.desktop_act":
        return _classify_desktop_act(args, task_prompt, trust_active, repo_root, eff_roots, data_dir)
    if tool == "pc.browser_act":
        return _classify_browser_act(args, trust_active)
    if tool == "pc.run_command":
        cmd_text = str(args.get("cmd") or args.get("command") or "")
        if cmd_text:
            safe_ps, ps_reason = validate_powershell_ast_or_tokens(cmd_text)
            if not safe_ps:
                return PolicyVerdict(
                    "T3", "deny", "blocked_powershell_obfuscation",
                    f"Обфускация PowerShell: {ps_reason} (T3).", True,
                )
        return _classify_run_command(args, task_prompt, trust_active, repo_root, eff_roots, data_dir)
    if tool == "pc.bossman_api":
        return classify_bossman_api(
            str(args.get("method") or "GET"), str(args.get("path") or ""),
            body=args.get("payload") or args.get("body"), data_dir=data_dir,
        )
    if tool == "pc.earn":
        return _classify_earn(args)
    if tool == "pc.record_screen":
        url_arg = str(args.get("url") or "").strip().lower()
        if url_arg and not url_arg.startswith(("https://", "http://", "tg://")):
            return PolicyVerdict("T3", "deny", "record_invalid_url", "Схема URL запрещена (T3).", True)
        return PolicyVerdict("T1", "auto", "screen_record", "Запись экрана (T1).")
    if tool == "pc.code_act":
        act_c = str(args.get("action") or "search").strip().lower()
        path_c = str(args.get("path") or "").strip()
        ro_c = act_c in {"search", "expand", "status", "workspace_map", "diff"}
        if path_c:
            bad_c, why_c = is_t3_secret_or_protected_path(
                path_c, for_write=(not ro_c), repo_root=repo_root, data_dir=data_dir
            )
            if bad_c:
                return PolicyVerdict("T3", "deny", "secret_or_protected_path", why_c, True)
        if ro_c:
            return PolicyVerdict("T0", "auto", "code_read", f"Анализ кода ({act_c}, T0).")
        return PolicyVerdict("T2", "ask", "code_other", f"Код {act_c} (T2).", True)
    return PolicyVerdict("T2", "ask", "unknown_tool", f"Неизвестный инструмент {tool} (T2).", True)

