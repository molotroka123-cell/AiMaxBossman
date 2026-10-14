"""Чистый модуль политики Argon PC-Agent: пути, процессы, службы и секреты (без БД и сети)."""
from __future__ import annotations
import contextlib, os, re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from ..autonomy.policy import is_protected_path
from ..plugin_security import redact_text

HARD_INVARIANTS = (
    "csam_or_minors_forbidden", "non_consensual_real_person_sexual_imagery_forbidden",
    "owner_personal_media_never_leaves_machine_to_cloud",
)
_CSAM_RE = re.compile(
    r"(?iu)\b(csam|child\s*(?:sex|porn|nude)|underage\s*(?:sex|nude|erotic)|несовершеннолетн\w*\s+"
    r"(?:секс\w*|обнаж\w*|эротик\w*|порн\w*)|(?:детск\w*|школьни\w*|подростк\w*)\s+"
    r"(?:порн\w*|эротик\w*|обнаж\w*|интим\w*))\b"
)
_NCII_RE = re.compile(
    r"(?iu)\b(deepfake\s*nude|undress\s*(?:photo|her|him|person)|nudify|раздень\w*\s+(?:по\s+фото|человека"
    r"|девушку|женщину|мужчину)|обнаж\w+\s+по\s+фото\s+реальн\w+|без\s+согласия\s+(?:обнаж|разде|интим))\b"
)
_CLOUD_PERSONAL_MEDIA_RE = re.compile(
    r"(?iu)(?:отправ\w*|загруз\w*|upload|send|exfiltrat\w*)\s+[^.!?\n]{0,60}(?:личн\w+\s+фото|мои\s+фото"
    r"|personal\s+photo\w*|family\s+photo\w*|camera\s+roll)[^.!?\n]{0,60}"
    r"(?:в\s+облако|на\s+сервер|в\s+интернет|to\s+cloud|external)"
)
_T3_SECRET_PATTERNS = tuple(re.compile(p) for p in (
    r"(?i)(?:^|[\\/]|[_.-]|backup-\d+-)(?:credentials\.enc|vault\.key|provider-keys\.env|api-token"
    r"|companion\.enc(?:\.key)?|companion\.sqlite3(?:-wal|-shm)?|bossman\.db(?:-wal|-shm)?"
    r"|id_(?:rsa|ed25519|ecdsa|dsa)(?:\.pub)?|pin\.(?:json|sha256|txt))(?:$|[._-])",
    r"(?i)(?:^|[\\/]|backup-\d+-)token(?:$|[._-])"
    r"|(?:^|[\\/]|[_.-]|backup-\d+-)(?:[a-z0-9_-]+\.)?env(?:\.[a-z0-9_-]+)?$",
    r"(?i)constitution(?:\.sha256|_v\d+|\.md)|bossman_constitution|adopt_constitution_v2\.ps1"
    r"|[\\/]pc-agent[\\/](?:STOP|audit\.jsonl)$|[\\/]pc-agent[\\/](?:tasks|rollback)(?:[\\/]|$)"
    r"|[\\/]AppData[\\/]Local[\\/]Bossman[\\/](?:keys|autonomy|telegram-companion)(?:[\\/]|$)",
    r"(?i)[\\/]User Data[\\/](?:(?:Default|Profile [^\\/]+)[\\/](?:Network[\\/])?"
    r"(?:Login Data|Cookies|Web Data)(?:-journal)?|Local State)$"
    r"|(?:start\s*menu[\\/]programs[\\/]startup|currentversion[\\/]run\b)",
    r"(?i)(?:^|[\\/])\.(?:git-credentials|netrc|_netrc|npmrc|pypirc|claude\.json)$"
    r"|[\\/]\.(?:aws|azure|kube|docker|codex|claude|ssh)(?:[\\/]|$)|\.(?:pem|key|pfx|p12|kdbx)$|\bdpapi\b",
))
_SENSITIVE_ENV_RE = re.compile(
    r"(?i)(TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|APIKEY|PRIVATE_KEY|VAULT|CREDENTIAL"
    r"|OPENROUTER|ANTHROPIC|OPENAI|GEMINI_API|MISTRAL|DEEPSEEK|TG_COMPANION|BCC_TOKEN)"
)
_PROMPT_INJECTION_RE = re.compile(
    r"(?iu)(?:ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions|disregard\s+(?:all\s+)?"
    r"(?:previous|prior)\s+instructions|system\s*override|new\s+instructions\s*:|игнорируй\w*\s+(?:все\s+)?"
    r"(?:предыдущ\w+|прежни\w+)\s+(?:инструкц\w+|правил\w+|указани\w+)|забудь\w*\s+(?:все\s+)?"
    r"(?:предыдущ\w+|прежни\w+)\s+(?:инструкц\w+|правил\w+)|выполни\s+команду\s+в\s+обход"
    r"|отправь\s+токен|exfiltrate|curl\s+http[^|\n]*\|\s*(?:sh|bash|pwsh|powershell))"
)
PROTECTED_PROCESS_NAMES = frozenset((
    "bcc bcc.exe bossman bossman.exe ollama ollama.exe ollama_llama_server ollama_llama_server.exe "
    "lsass lsass.exe csrss csrss.exe wininit wininit.exe services services.exe svchost svchost.exe "
    "windefend msmpeng msmpeng.exe nissrv nissrv.exe wdnissvc securityhealthservice "
    "securityhealthservice.exe sense sense.exe wscsvc mpssvc smss smss.exe winlogon winlogon.exe dwm dwm.exe system"
).split())
PROTECTED_CMDLINE_TOKENS = (
    "bcc.main", "bcc\\main", "bcc/main", "telegram_companion", "telegram-companion",
    "run_backend_source", "run_pult_source", "uvicorn", ":8801", "--port 8801", "ollama serve", "ollama_llama_server",
)
PROTECTED_SERVICE_NAMES = frozenset(
    "windefend wdnissvc sense wscsvc securityhealthservice mpssvc lsass rpcss dcomlaunch "
    "eventlog samss bfe cryptsvc bossman ollama".split()
)
_UNSAFE_WRITE_SUFFIXES = frozenset(
    ".exe .bat .cmd .ps1 .psm1 .psd1 .vbs .vbe .js .jse .wsf .wsh .msi .msp .scr .hta "
    ".cpl .msc .jar .reg .lnk .url .dll .sys .com .pif .py .pyw .sh .bash".split()
)
_PROTECTED_PROC_SUB = "bossman ollama lsass csrss wininit svchost defender msmpeng wdnis sense winlogon dwm".split()
_PROTECTED_REPO_MARKERS = (
    "docs/constitution/ command-center/bcc/autonomy/ command-center/bcc/approvals.py "
    "command-center/bcc/permissions.py command-center/bcc/auth.py command-center/bcc/secrets.py "
    "command-center/bcc/features/control_plane.py command-center/bcc/features/pc_agent.py "
    "command-center/bcc/features/pc_agent_policy.py command-center/bcc/features/pc_agent_policy_paths.py".split()
)
_PROTECTED_BASENAMES = "approvals.py permissions.py secrets.py pc_agent_policy.py pc_agent_policy_paths.py".split()
_MUTATION_VERBS = (
    "напиши", "сохрани", "создай", "измени", "удали", "запусти", "выполни", "открой блокнот и напиши",
    "открой и введи", "установи", "перезагрузи", "скопируй", "скоприруй", "перемести", "переименуй",
    "отредактируй", "убей", "заверши", "останови", "write", "save", "create", "delete", "run",
    "execute", "install", "type", "click", "kill", "stop", "move", "copy", "edit",
)
_READONLY_VERBS = (
    "прочитай", "покажи", "проверь", "список", "что в файле", "что написано",
    "какой заголовок", "скриншот", "экран", "статус", "read", "show", "list",
)
@dataclass(frozen=True)
class PolicyVerdict:
    tier: str; decision: str; category: str; reason: str; always_owner_button: bool = False; to_dict = asdict
def is_protected_process(name: str = "", cmdline: str = "", pid: int | None = None) -> tuple[bool, str]:
    if pid is not None and (pid <= 4 or pid == os.getpid()): return True, f"процесс PID {pid} защищён (T3)"
    low_name, low_cmd = str(name or "").strip().lower(), str(cmdline or "").strip().lower()
    low_base = low_name.removesuffix(".exe")
    if low_name in PROTECTED_PROCESS_NAMES or low_base in PROTECTED_PROCESS_NAMES or any(
        p in low_base for p in _PROTECTED_PROC_SUB
    ): return True, f"процесс {name or pid} входит в список защищённых процессов ОС/Bossman (T3)"
    if low_cmd and any(tok in low_cmd for tok in PROTECTED_CMDLINE_TOKENS):
        return True, f"процесс {name or pid} обслуживает ядро/пульт Bossman (`{low_cmd[:80]}`) и защищён (T3)"
    return False, ""
def _check_proc_tree_protected(pid_int: int) -> tuple[bool, str]:
    import psutil
    with contextlib.suppress(Exception):
        po = psutil.Process(pid_int)
        for p in [po, *po.children(recursive=True)]:
            with contextlib.suppress(Exception):
                pr, why = is_protected_process(p.name() or "", " ".join(p.cmdline() or []), p.pid)
                if pr: return True, why
    return False, ""
def is_protected_service(name: str) -> tuple[bool, str]:
    low = str(name or "").strip().lower()
    if low and (low in PROTECTED_SERVICE_NAMES or any(s in low for s in PROTECTED_SERVICE_NAMES)):
        return True, f"служба безопасности/ядра «{name}» защищена от остановки и изменения (T3)"
    return False, ""
def _collect_runtime_secrets(data_dir: Path | None = None) -> tuple[str, ...]:
    vals: list[str] = []
    for fname in (("token", "api-token") if data_dir is not None else ()):
        p = Path(data_dir) / fname  # type: ignore[arg-type]
        with contextlib.suppress(OSError):
            if p.is_file() and len(tok := p.read_text(encoding="utf-8", errors="replace").strip()) >= 6:
                if tok not in vals: vals.append(tok)
    return tuple(vals)
def scrub_secrets(text: str, extra_secrets: tuple[str, ...] = ()) -> str:
    from bossman.obs import redact
    out = str(text or "")
    for val in extra_secrets:
        if val and len(val) >= 6: out = out.replace(val, "[REDACTED]")
    out = re.sub(r"(?<!\d)\d{6,16}:[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])", "[REDACTED]", out)
    out = re.sub(r"\bsk-[A-Za-z0-9_-]{16,}\b", "[REDACTED]", out)
    out = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]{12,}", r"\1[REDACTED]", out)
    out = re.sub(
        r"(?i)\b(bot_token|core_token|cloud_token|local_token|api_key|vault_key|password|secret)"
        r"(\s*[=:]\s*['\"]?)([^'\"\s,;}{]+)", r"\1\2[REDACTED]", out,
    )
    return redact_text(redact(out))
def check_hard_content_invariants(text: str) -> PolicyVerdict | None:
    raw = str(text or "")
    if _CSAM_RE.search(raw): return PolicyVerdict("T3", "deny", HARD_INVARIANTS[0], "Запрещён контент CSAM.", True)
    if _NCII_RE.search(raw): return PolicyVerdict("T3", "deny", HARD_INVARIANTS[1], "Запрещён NCII (T3).", True)
    if _CLOUD_PERSONAL_MEDIA_RE.search(raw): return PolicyVerdict("T3", "deny", HARD_INVARIANTS[2], "T3.", True)
    return None
def _norm_path(path_str: str) -> str:
    s = os.path.expandvars(str(path_str or "").strip()).replace("\\", "/")
    if not s: return ""
    s = s[4:] if s.startswith("//?/") else s
    drive, rest = (s[:2], s[2:]) if (len(s) >= 2 and s[0].isalpha() and s[1] == ":") else ("", s)
    rest = rest.split(":", 1)[0] if ":" in rest else rest
    return drive + "/".join(seg.rstrip(". ") if seg not in {".", ".."} else seg for seg in rest.split("/"))
def _has_ntfs_ads(path_str: str) -> bool:
    s = str(path_str or "").strip().replace("\\", "/")
    s = s[4:] if s.startswith("//?/") else s
    return ":" in (s[2:] if (len(s) >= 2 and s[0].isalpha() and s[1] == ":") else s)
def _is_unc(p: str) -> bool:
    return (s := str(p or "").strip().replace("\\", "/")).startswith("//") and not s.startswith("//?/")
def _is_system32_bin(p: str) -> bool:
    with contextlib.suppress(OSError, ValueError):
        parent = Path(_norm_path(p)).expanduser().resolve().parent
        return parent in {Path("C:/Windows/System32").resolve(), Path("C:/Windows").resolve()}
    return False
def is_t3_secret_or_protected_path(
    path_str: str | Path, *, for_write: bool = False, repo_root: Path | None = None, data_dir: Path | None = None,
) -> tuple[bool, str]:
    raw = str(path_str or "").strip()
    if _is_unc(raw) or _is_unc(os.path.expandvars(raw)): return True, f"UNC-путь ({raw}) запрещён (T3)"
    if for_write and _has_ntfs_ads(raw): return True, f"запись в NTFS ADS ({raw}) запрещена (T3)"
    norm = _norm_path(raw)
    if not norm: return True, "пустой путь"
    if re.search(r"%[A-Za-z_][A-Za-z0-9_]*%", norm) or re.search(r"\$env:[A-Za-z_]", norm, re.I):
        return True, f"нераскрытая переменная окружения в пути ({norm}) запрещена (T3)"
    try: resolved_norm = _norm_path(str(Path(norm).expanduser().resolve()))
    except OSError: resolved_norm = norm
    for check_s in (norm, raw.replace("\\", "/"), resolved_norm):
        if any(pat.search(check_s) for pat in _T3_SECRET_PATTERNS):
            return True, f"доступ к защищённому секрету/пину ({Path(norm).name}) запрещён (T3)"
    for c_git in (norm.lower(), resolved_norm.lower()):
        if "/.git/hooks" in c_git or c_git.endswith("/.git/config") or c_git in {".git/hooks", ".git/config"}:
            return True, f"доступ к чувствительному контуру Git ({norm}) запрещён (T3)"
    candidates = [norm, resolved_norm]
    for r_root in [r for r in (repo_root, Path(__file__).resolve().parents[3]) if r is not None]:
        with contextlib.suppress(Exception):
            candidates.append(str(Path(resolved_norm).relative_to(r_root.resolve())).replace("\\", "/"))
    for cand in candidates:
        low_c = cand.lower()
        if any(b in Path(low_c).name for b in _PROTECTED_BASENAMES) and (for_write or "secrets.py" in low_c):
            return True, f"путь {cand} защищён правилами гейта (T3)"
        for marker in _PROTECTED_REPO_MARKERS:
            m = marker.rstrip("/")
            if (low_c == m or low_c.endswith("/" + m) or marker in low_c) and (
                for_write or "constitution" in low_c or "secrets.py" in low_c
            ):
                return True, f"путь {cand} защищён Конституцией v2 / правилами гейта (T3)"
        if for_write and is_protected_path(cand): return True, f"запись в {cand} запрещена (T3)"
    return False, ""
def default_safe_roots(data_dir: Path | None = None) -> list[Path]:
    h = Path.home()
    tmp = Path(os.environ.get("TEMP", str(h / "AppData/Local/Temp")))
    roots = [h / "Desktop", h / "Documents", h / "Downloads", tmp]
    if data_dir is not None: roots.append(Path(data_dir) / "pc-agent" / "workspace")
    return [r.resolve() for r in roots]
def _is_safe_root_itself(path_str: str, safe_roots: list[Path]) -> bool:
    with contextlib.suppress(OSError, ValueError):
        return Path(_norm_path(path_str)).expanduser().resolve() in {r.resolve() for r in safe_roots}
    return False
def is_within_safe_roots(path_str: str, safe_roots: list[Path] | None = None) -> bool:
    norm = _norm_path(path_str)
    if not norm or _has_ntfs_ads(path_str) or _is_unc(path_str): return False
    try:
        raw_path = Path(norm).expanduser()
        if raw_path.exists() and raw_path.is_symlink(): return False
        target = raw_path.resolve()
    except OSError: return False
    t_low = str(target).replace("\\", "/").lower()
    suffix = Path(Path(norm).name.rstrip(". ").lower()).suffix.lower()
    if "/.git/" in t_low or t_low.endswith("/.git") or "start menu/programs/startup" in t_low or (
        suffix in _UNSAFE_WRITE_SUFFIXES
    ): return False
    for root in (safe_roots if safe_roots is not None else default_safe_roots()):
        with contextlib.suppress(ValueError, OSError):
            if target != root.resolve() and target.is_relative_to(root.resolve()): return True
    return False
def PromptIntentReadOnly(task_prompt: str) -> bool:
    text = str(task_prompt or "").strip().lower()
    return bool(text) and not any(v in text for v in _MUTATION_VERBS) and any(v in text for v in _READONLY_VERBS)
def classify_path_proc_service_action(
    tool: str, args: dict[str, Any], *, task_prompt: str, trust_active: bool,
    repo_root: Path | None, eff_roots: list[Path], data_dir: Path | None,
) -> PolicyVerdict | None:
    if tool in {"pc.read_file", "pc.list_dir"}:
        path = str(args.get("path") or "").strip()
        bad, why = is_t3_secret_or_protected_path(path, for_write=False, repo_root=repo_root, data_dir=data_dir)
        if bad: return PolicyVerdict("T3", "deny", "secret_or_protected_path", why, True)
        return PolicyVerdict("T0", "auto", "filesystem_read", f"Чтение {path} (T0).")
    if tool in {"pc.write_file", "pc.edit_file", "pc.copy_file", "pc.move_file"}:
        raw_p, raw_d = str(args.get("path") or "").strip(), str(args.get("dst") or "").strip()
        if raw_p and raw_d and _norm_path(raw_p) != _norm_path(raw_d):
            return PolicyVerdict("T3", "deny", "inconsistent_path_dst", "Несовпадающие path и dst (T3).", True)
        dst_path, src_path = (raw_p or raw_d), str(args.get("src") or "").strip()
        bad_d, why_d = is_t3_secret_or_protected_path(dst_path, for_write=True, repo_root=repo_root, data_dir=data_dir)
        if bad_d or _is_safe_root_itself(dst_path, eff_roots):
            return PolicyVerdict("T3", "deny", "secret_or_protected_path", why_d or "Корень запрещён (T3).", True)
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
            return PolicyVerdict("T3", "deny", "unrequested_mutation", "Запись отклонена в чтении.", True)
        if is_within_safe_roots(dst_path, eff_roots) and (
            tool != "pc.move_file" or not src_path or is_within_safe_roots(src_path, eff_roots)
        ): return PolicyVerdict("T1", "auto", "filesystem_write_safe", f"Запись {dst_path} с откатом (T1).")
        return PolicyVerdict("T2", "ask", "filesystem_write_outside_workspace", f"Запись {dst_path} (T2).", True)
    if tool == "pc.delete_file":
        path = str(args.get("path") or "").strip()
        bad, why = is_t3_secret_or_protected_path(path, for_write=True, repo_root=repo_root, data_dir=data_dir)
        if bad or _is_safe_root_itself(path, eff_roots):
            return PolicyVerdict("T3", "deny", "secret_or_protected_path", why or "Удаление корня (T3).", True)
        if PromptIntentReadOnly(task_prompt):
            return PolicyVerdict("T3", "deny", "unrequested_mutation", "Удаление отклонено в чтении.", True)
        in_temp, temp_env = False, str(os.environ.get("TEMP") or "").strip()
        if temp_env:
            with contextlib.suppress(OSError, ValueError):
                res_p, tmp_r = Path(_norm_path(path)).expanduser().resolve(), Path(temp_env).expanduser().resolve()
                in_temp = res_p != tmp_r and res_p.is_relative_to(tmp_r)
        dec = "trust_window" if (trust_active and in_temp) else "ask"
        return PolicyVerdict("T2", dec, "file_delete", "T2.", not in_temp)
    if tool == "pc.kill_process":
        if PromptIntentReadOnly(task_prompt):
            return PolicyVerdict("T3", "deny", "unrequested_mutation", "Kill отклонён в задаче чтения.", True)
        raw_pid, raw_name = args.get("pid"), str(args.get("name") or "").strip()
        if raw_pid is None and len(raw_name) < 2:
            return PolicyVerdict("T3", "deny", "invalid_process_name", "Пустое имя процесса (T3).", True)
        try: pid_int = int(raw_pid) if raw_pid is not None else None
        except (ValueError, TypeError): return PolicyVerdict("T3", "deny", "invalid_pid", "PID (T3).", True)
        prot, why_p = is_protected_process(raw_name, "", pid_int)
        if not prot and pid_int is not None: prot, why_p = _check_proc_tree_protected(pid_int)
        if prot: return PolicyVerdict("T3", "deny", "protected_process", why_p, True)
        return PolicyVerdict("T2", "trust_window" if trust_active else "ask", "process_kill", "Kill (T2).")
    if tool == "pc.window_act":
        action = str(args.get("action") or "focus").strip().lower()
        if action in {"focus", "minimize", "maximize", "restore"}:
            return PolicyVerdict("T1", "auto", "window_reversible", f"Окно ({action}, T1).")
        dec = "trust_window" if (trust_active and action == "close") else "ask"
        return PolicyVerdict("T2", dec, "window_close" if action == "close" else "window_unknown", f"Окно {action}.")
    if tool == "pc.control_service":
        svc, action = str(args.get("name") or "").strip(), str(args.get("action") or "status").strip().lower()
        if action == "status": return PolicyVerdict("T0", "auto", "service_status", f"Служба {svc} (T0).")
        if PromptIntentReadOnly(task_prompt):
            return PolicyVerdict("T3", "deny", "unrequested_mutation", "Служба отклонена в чтении.", True)
        prot_s, why_s = is_protected_service(svc)
        if prot_s: return PolicyVerdict("T3", "deny", "protected_service", why_s, True)
        return PolicyVerdict("T2", "ask", "service_control", f"Служба {svc} ({action}, T2).", True)
    return None
