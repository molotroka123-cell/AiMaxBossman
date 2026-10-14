"""Политика Argon PC-Agent: процессы, службы и файловые инструменты."""
from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Any

from .pc_agent_policy_consts import (
    PROTECTED_CMDLINE_TOKENS,
    PROTECTED_PROCESS_NAMES,
    PROTECTED_SERVICE_NAMES,
    PolicyVerdict,
    _PROTECTED_PROC_SUB,
)
from .pc_agent_policy_paths import (
    PromptIntentReadOnly,
    _is_safe_root_itself,
    _norm_path,
    is_t3_secret_or_protected_path,
    is_within_safe_roots,
)


def is_protected_process(name: str = "", cmdline: str = "", pid: int | None = None) -> tuple[bool, str]:
    if pid is not None and (pid <= 4 or pid == os.getpid()):
        return True, f"процесс PID {pid} защищён (T3)"
    low_name, low_cmd = str(name or "").strip().lower(), str(cmdline or "").strip().lower()
    low_base = low_name.removesuffix(".exe")
    in_names = low_name in PROTECTED_PROCESS_NAMES or low_base in PROTECTED_PROCESS_NAMES
    if in_names or any(p in low_base for p in _PROTECTED_PROC_SUB):
        return True, (
            f"процесс {name or pid} входит в список защищённых процессов "
            "ОС/Bossman (T3)"
        )
    if low_cmd and any(tok in low_cmd for tok in PROTECTED_CMDLINE_TOKENS):
        shown = low_cmd[:80]
        return True, (
            f"процесс {name or pid} обслуживает ядро/пульт Bossman "
            f"(`{shown}`) и защищён (T3)"
        )
    return False, ""


def _check_proc_tree_protected(pid_int: int) -> tuple[bool, str]:
    import psutil

    with contextlib.suppress(Exception):
        po = psutil.Process(pid_int)
        for p in [po, *po.children(recursive=True)]:
            with contextlib.suppress(Exception):
                pr, why = is_protected_process(p.name() or "", " ".join(p.cmdline() or []), p.pid)
                if pr:
                    return True, why
    return False, ""


def is_protected_service(name: str) -> tuple[bool, str]:
    low = str(name or "").strip().lower()
    if low and (low in PROTECTED_SERVICE_NAMES or any(s in low for s in PROTECTED_SERVICE_NAMES)):
        return True, (
            f"служба безопасности/ядра «{name}» защищена "
            "от остановки и изменения (T3)"
        )
    return False, ""


def _classify_read_tool(args: dict[str, Any], repo_root: Path | None, data_dir: Path | None) -> PolicyVerdict:
    path = str(args.get("path") or "").strip()
    bad, why = is_t3_secret_or_protected_path(path, for_write=False, repo_root=repo_root, data_dir=data_dir)
    if bad:
        return PolicyVerdict("T3", "deny", "secret_or_protected_path", why, True)
    return PolicyVerdict("T0", "auto", "filesystem_read", f"Чтение {path} (T0).")


def _classify_write_tool(
    tool: str,
    args: dict[str, Any],
    task_prompt: str,
    repo_root: Path | None,
    eff_roots: list[Path],
    data_dir: Path | None,
) -> PolicyVerdict:
    raw_p, raw_d = str(args.get("path") or "").strip(), str(args.get("dst") or "").strip()
    if raw_p and raw_d and _norm_path(raw_p) != _norm_path(raw_d):
        return PolicyVerdict(
            "T3",
            "deny",
            "inconsistent_path_dst",
            "Несовпадающие path и dst (T3).",
            True,
        )
    dst_path, src_path = (raw_p or raw_d), str(args.get("src") or "").strip()
    bad_d, why_d = is_t3_secret_or_protected_path(dst_path, for_write=True, repo_root=repo_root, data_dir=data_dir)
    if bad_d or _is_safe_root_itself(dst_path, eff_roots):
        return PolicyVerdict(
            "T3",
            "deny",
            "secret_or_protected_path",
            why_d or "Корень запрещён (T3).",
            True,
        )
    if src_path:
        b0, w0 = is_t3_secret_or_protected_path(src_path, for_write=False, repo_root=repo_root, data_dir=data_dir)
        b1, w1 = is_t3_secret_or_protected_path(
            src_path, for_write=(tool == "pc.move_file"), repo_root=repo_root, data_dir=data_dir
        )
        if b0 or b1 or (tool == "pc.move_file" and _is_safe_root_itself(src_path, eff_roots)):
            return PolicyVerdict("T3", "deny", "secret_or_protected_path", w0 or w1 or "Корень (T3).", True)
        with contextlib.suppress(OSError):
            if Path(_norm_path(src_path)).expanduser().is_dir():
                return PolicyVerdict("T2", "ask", "directory_move_or_copy", f"Папка {src_path} (T2).", True)
    if PromptIntentReadOnly(task_prompt):
        return PolicyVerdict(
            "T3",
            "deny",
            "unrequested_mutation",
            "Запись отклонена в чтении.",
            True,
        )
    src_ok = tool != "pc.move_file" or not src_path or is_within_safe_roots(src_path, eff_roots)
    if is_within_safe_roots(dst_path, eff_roots) and src_ok:
        return PolicyVerdict("T1", "auto", "filesystem_write_safe", f"Запись {dst_path} с откатом (T1).")
    return PolicyVerdict("T2", "ask", "filesystem_write_outside_workspace", f"Запись {dst_path} (T2).", True)


def _classify_delete_tool(
    args: dict[str, Any],
    task_prompt: str,
    trust_active: bool,
    repo_root: Path | None,
    eff_roots: list[Path],
    data_dir: Path | None,
) -> PolicyVerdict:
    path = str(args.get("path") or "").strip()
    bad, why = is_t3_secret_or_protected_path(path, for_write=True, repo_root=repo_root, data_dir=data_dir)
    if bad or _is_safe_root_itself(path, eff_roots):
        return PolicyVerdict("T3", "deny", "secret_or_protected_path", why or "Удаление корня (T3).", True)
    if PromptIntentReadOnly(task_prompt):
        return PolicyVerdict(
            "T3",
            "deny",
            "unrequested_mutation",
            "Удаление отклонено в чтении.",
            True,
        )
    in_temp, temp_env = False, str(os.environ.get("TEMP") or "").strip()
    if temp_env:
        with contextlib.suppress(OSError, ValueError):
            res_p = Path(_norm_path(path)).expanduser().resolve()
            tmp_r = Path(temp_env).expanduser().resolve()
            in_temp = res_p != tmp_r and res_p.is_relative_to(tmp_r)
    dec = "trust_window" if (trust_active and in_temp) else "ask"
    return PolicyVerdict("T2", dec, "file_delete", "T2.", not in_temp)


def _classify_kill_tool(args: dict[str, Any], task_prompt: str, trust_active: bool) -> PolicyVerdict:
    if PromptIntentReadOnly(task_prompt):
        return PolicyVerdict(
            "T3",
            "deny",
            "unrequested_mutation",
            "Kill отклонён в задаче чтения.",
            True,
        )
    raw_pid, raw_name = args.get("pid"), str(args.get("name") or "").strip()
    if raw_pid is None and len(raw_name) < 2:
        return PolicyVerdict("T3", "deny", "invalid_process_name", "Пустое имя процесса (T3).", True)
    try:
        pid_int = int(raw_pid) if raw_pid is not None else None
    except (ValueError, TypeError):
        return PolicyVerdict("T3", "deny", "invalid_pid", "PID (T3).", True)
    prot, why_p = is_protected_process(raw_name, "", pid_int)
    if not prot and pid_int is not None:
        prot, why_p = _check_proc_tree_protected(pid_int)
    if prot:
        return PolicyVerdict("T3", "deny", "protected_process", why_p, True)
    return PolicyVerdict("T2", "trust_window" if trust_active else "ask", "process_kill", "Kill (T2).")


def _classify_window_tool(args: dict[str, Any], trust_active: bool) -> PolicyVerdict:
    action = str(args.get("action") or "focus").strip().lower()
    if action in {"focus", "minimize", "maximize", "restore"}:
        return PolicyVerdict("T1", "auto", "window_reversible", f"Окно ({action}, T1).")
    dec = "trust_window" if (trust_active and action == "close") else "ask"
    category = "window_close" if action == "close" else "window_unknown"
    return PolicyVerdict("T2", dec, category, f"Окно {action}.")


def _classify_service_tool(args: dict[str, Any], task_prompt: str) -> PolicyVerdict:
    svc, action = str(args.get("name") or "").strip(), str(args.get("action") or "status").strip().lower()
    if action == "status":
        return PolicyVerdict("T0", "auto", "service_status", f"Служба {svc} (T0).")
    if PromptIntentReadOnly(task_prompt):
        return PolicyVerdict(
            "T3",
            "deny",
            "unrequested_mutation",
            "Служба отклонена в чтении.",
            True,
        )
    prot_s, why_s = is_protected_service(svc)
    if prot_s:
        return PolicyVerdict("T3", "deny", "protected_service", why_s, True)
    return PolicyVerdict("T2", "ask", "service_control", f"Служба {svc} ({action}, T2).", True)


def classify_path_proc_service_action(
    tool: str,
    args: dict[str, Any],
    *,
    task_prompt: str,
    trust_active: bool,
    repo_root: Path | None,
    eff_roots: list[Path],
    data_dir: Path | None,
) -> PolicyVerdict | None:
    if tool in {"pc.read_file", "pc.list_dir"}:
        return _classify_read_tool(args, repo_root, data_dir)
    if tool in {"pc.write_file", "pc.edit_file", "pc.copy_file", "pc.move_file"}:
        return _classify_write_tool(tool, args, task_prompt, repo_root, eff_roots, data_dir)
    if tool == "pc.delete_file":
        return _classify_delete_tool(args, task_prompt, trust_active, repo_root, eff_roots, data_dir)
    if tool == "pc.kill_process":
        return _classify_kill_tool(args, task_prompt, trust_active)
    if tool == "pc.window_act":
        return _classify_window_tool(args, trust_active)
    if tool == "pc.control_service":
        return _classify_service_tool(args, task_prompt)
    return None
