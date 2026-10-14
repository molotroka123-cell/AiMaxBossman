# fmt: off
"""Чистый модуль политики безопасности Argon PC-Agent (T0 / T1 / T2 / T3).
Не содержит вызовов БД, сетевых запросов или побочных эффектов."""
from __future__ import annotations
import contextlib, json, math, os, posixpath, re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from ..autonomy.policy import is_protected_path
from ..plugin_security import redact_text

HARD_INVARIANTS = ("csam_or_minors_forbidden", "non_consensual_real_person_sexual_imagery_forbidden", "owner_personal_media_never_leaves_machine_to_cloud")
_CSAM_RE = re.compile(r"(?iu)\b(csam|child\s*(?:sex|porn|nude)|underage\s*(?:sex|nude|erotic)|несовершеннолетн\w*\s+(?:секс\w*|обнаж\w*|эротик\w*|порн\w*)|(?:детск\w*|школьни\w*|подростк\w*)\s+(?:порн\w*|эротик\w*|обнаж\w*|интим\w*))\b")
_NCII_RE = re.compile(r"(?iu)\b(deepfake\s*nude|undress\s*(?:photo|her|him|person)|nudify|раздень\w*\s+(?:по\s+фото|человека|девушку|женщину|мужчину)|обнаж\w+\s+по\s+фото\s+реальн\w+|без\s+согласия\s+(?:обнаж|разде|интим))\b")
_CLOUD_PERSONAL_MEDIA_RE = re.compile(r"(?iu)(?:отправ\w*|загруз\w*|upload|send|exfiltrat\w*)\s+[^.!?\n]{0,60}(?:личн\w+\s+фото|мои\s+фото|personal\s+photo\w*|family\s+photo\w*|camera\s+roll)[^.!?\n]{0,60}(?:в\s+облако|на\s+сервер|в\s+интернет|to\s+cloud|external)")

_T3_SECRET_PATTERNS = tuple(re.compile(p) for p in (
    r"(?i)(?:^|[\\/]|[_.-]|backup-\d+-)credentials\.enc(?:$|[._-])", r"(?i)(?:^|[\\/]|[_.-]|backup-\d+-)vault\.key(?:$|[._-])",
    r"(?i)(?:^|[\\/]|[_.-]|backup-\d+-)provider-keys\.env(?:$|[._-])", r"(?i)(?:^|[\\/]|[_.-]|backup-\d+-)api-token(?:$|[._-])",
    r"(?i)(?:^|[\\/]|backup-\d+-)token(?:$|[._-])", r"(?i)(?:^|[\\/]|[_.-]|backup-\d+-)companion\.enc(?:\.key)?(?:$|[._-])",
    r"(?i)(?:^|[\\/]|[_.-]|backup-\d+-)\.env(?:\.[a-z0-9_-]+)?$", r"(?i)(?:^|[\\/]|[_.-]|backup-\d+-)companion\.sqlite3(?:-wal|-shm)?(?:$|[._-])",
    r"(?i)(?:^|[\\/]|[_.-]|backup-\d+-)bossman\.db(?:-wal|-shm)?(?:$|[._-])", r"(?i)constitution(?:\.sha256|_v\d+|\.md)",
    r"(?i)bossman_constitution", r"(?i)adopt_constitution_v2\.ps1",
    r"(?i)(?:^|[\\/]|[_.-]|backup-\d+-)id_(?:rsa|ed25519|ecdsa|dsa)(?:\.pub)?(?:$|[._-])", r"(?i)[\\/]pc-agent[\\/](?:STOP|audit\.jsonl)$",
    r"(?i)[\\/]pc-agent[\\/](?:tasks|rollback)(?:[\\/]|$)", r"(?i)[\\/]AppData[\\/]Local[\\/]Bossman[\\/](?:keys|autonomy|telegram-companion)(?:[\\/]|$)|(?:^|[\\/]|[_.-]|backup-\d+-)pin\.(?:json|sha256|txt)(?:$|[._-])",
    r"(?i)[\\/]User Data[\\/](?:Default|Profile [^\\/]+)[\\/](?:Network[\\/])?(?:Login Data|Cookies|Web Data)(?:-journal)?$",
    r"(?i)[\\/]User Data[\\/]Local State$", r"(?i)(?:start\s*menu[\\/]programs[\\/]startup|currentversion[\\/]run\b)",
    r"(?i)(?:^|[\\/])\.(?:git-credentials|netrc|_netrc|npmrc|pypirc)$", r"(?i)[\\/]\.(?:aws|azure|kube|docker)[\\/](?:credentials|config(?:\.json)?)$",
    r"(?i)\bdpapi\b",
))
_SENSITIVE_ENV_RE = re.compile(r"(?i)(TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|APIKEY|PRIVATE_KEY|VAULT|CREDENTIAL|OPENROUTER|ANTHROPIC|OPENAI|GEMINI_API|MISTRAL|DEEPSEEK|TG_COMPANION|BCC_TOKEN)")
_PROMPT_INJECTION_RE = re.compile(r"(?iu)(?:ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions|disregard\s+(?:all\s+)?(?:previous|prior)\s+instructions|system\s*override|new\s+instructions\s*:|игнорируй\w*\s+(?:все\s+)?(?:предыдущ\w+|прежни\w+)\s+(?:инструкц\w+|правил\w+|указани\w+)|забудь\w*\s+(?:все\s+)?(?:предыдущ\w+|прежни\w+)\s+(?:инструкц\w+|правил\w+)|выполни\s+команду\s+в\s+обход|отправь\s+токен|exfiltrate|curl\s+http[^|\n]*\|\s*(?:sh|bash|pwsh|powershell))")

PROTECTED_PROCESS_NAMES = frozenset({"bcc", "bcc.exe", "bossman", "bossman.exe", "lsass", "lsass.exe", "csrss", "csrss.exe", "wininit", "wininit.exe", "services", "services.exe", "svchost", "svchost.exe", "windefend", "msmpeng", "msmpeng.exe", "nissrv", "nissrv.exe", "wdnissvc", "securityhealthservice", "securityhealthservice.exe", "sense", "sense.exe", "wscsvc", "mpssvc", "smss", "smss.exe", "winlogon", "winlogon.exe", "dwm", "dwm.exe", "system"})
PROTECTED_CMDLINE_TOKENS = ("bcc.main", "bcc\\main", "bcc/main", "telegram_companion", "telegram-companion", "run_backend_source", "run_pult_source", "uvicorn", ":8801", "--port 8801")
PROTECTED_SERVICE_NAMES = frozenset({"windefend", "wdnissvc", "sense", "wscsvc", "securityhealthservice", "mpssvc", "lsass", "rpcss", "dcomlaunch", "eventlog", "samss", "bfe", "cryptsvc", "bossman"})

_T3_CMD_PATTERNS = (
    (re.compile(r"(?i)\bgit\b[^\n]*\bpush\b(?:[^\n]*\s(?:--force|-f|--force-with-lease)\b|[^\n]*\s\+[A-Za-z0-9_./-]+)"), "force_push_forbidden"),
    (re.compile(r"(?i)\bgit\b[^\n]*\b(?:commit|push|merge)\b[^\n]*\s(?:--no-verify|-n)\b"), "selftest_bypass_forbidden"),
    (re.compile(r"(?i)\b(?:STOP_WATCHDOG|DISABLE_AUDIT|BOSSMAN_SKIP_SELFTEST)\b"), "disable_watchdog_or_audit_forbidden"),
    (re.compile(r"(?i)\b(?:Set-MpPreference|Add-MpPreference)\b[^\n]*Disable"), "disable_defender_forbidden"),
    (re.compile(r"(?i)\bnetsh\b[^\n]*\badvfirewall\b[^\n]*\bstate\s+off\b"), "disable_firewall_forbidden"),
    (re.compile(r"(?i)\breg\s+(?:add|delete)\b[^\n]*Policies\\System[^\n]*EnableLUA"), "disable_uac_forbidden"),
    (re.compile(r"(?i)\badopt_constitution_v2\.ps1\b"), "constitution_pin_owner_only"),
    (re.compile(r"(?i)\bconstitution\.sha256\b"), "constitution_pin_owner_only"),
    (re.compile(r"(?i)\b(?:vault\.key|credentials\.enc|provider-keys\.env|api-token|companion\.enc)\b"), "secret_store_forbidden"),
    (re.compile(r"(?i)\b(?:CryptUnprotectData|ProtectedData::Unprotect)\b"), "dpapi_unprotect_forbidden"),
    (re.compile(r"(?i)\bbcdedit(?:\.exe)?\b"), "bcdedit_boot_config_forbidden"),
)
_T2_ALWAYS_BUTTON_PATTERNS = (
    (re.compile(r"(?i)\b(?:shutdown(?:\.exe)?|Restart-Computer|Stop-Computer)\b"), "system_power"),
    (re.compile(r"(?i)\bgit\b[^\n]*\b(?:push|merge)\b"), "git_promotion"),
    (re.compile(r"(?i)\b(?:Remove-Item|del|erase|rm|rmdir|rd)\b[^\n]*(?:-Recurse|-Force|/s|/q|-rf)\b"), "destructive_delete"),
    (re.compile(r"(?i)\b(?:Format-Volume|Clear-Disk|cipher\s+/w)\b"), "disk_wipe"),
    (re.compile(r"(?i)\b(?:powershell|pwsh)(?:\.exe)?\b[^\n]*(?:\s-(?:e|ec|enc\w*|encodedcommand|c|command|f|file)\b|\bIEX\b|\bInvoke-Expression\b)"), "powershell_inline_exec"),
    (re.compile(r"(?i)\bcmd(?:\.exe)?\b[^\n]*\s/[ck]\b"), "cmd_inline_exec"),
    (re.compile(r"(?i)\b(?:schtasks|certutil|bitsadmin|wmic|setx|icacls|takeown)(?:\.exe)?\b"), "system_persistence_or_acl"),
    (re.compile(r"(?i)\bnet(?:\.exe|1\.exe)?\s+(?:user|localgroup|group)\b"), "account_mutation"),
    (re.compile(r"(?i)\b(?:python|python3|py)(?:\.exe)?\b[^\n]*\s-[cm]\b"), "python_inline_exec"),
    (re.compile(r"(?i)\b(?:node|deno|bun)(?:\.exe)?\b[^\n]*\s(?:-e|-p|--eval)\b"), "node_inline_exec"),
    (re.compile(r"(?i)\b(?:curl|scp|sftp|ftp)(?:\.exe)?\b|\b(?:Invoke-WebRequest|Invoke-RestMethod|Start-BitsTransfer)\b"), "network_transfer"),
    (re.compile(r"(?i)\b(?:reg(?:\.exe)?\b|New-ItemProperty|Set-ItemProperty|Remove-ItemProperty)\b"), "registry_mutation"),
    (re.compile(r"(?i)\b(?:sc(?:\.exe)?\b|Stop-Service|Set-Service|New-Service|Restart-Service)\b"), "system_service"),
    (re.compile(r"(?i)\b(?:msiexec|winget|choco)(?:\.exe)?\b|\b(?:pip|pip3|npm|pnpm|yarn)\s+install\b|\buv\s+pip\s+install\b"), "software_install"),
)
_T2_CMD_PATTERNS = (
    (re.compile(r"(?i)\b(?:Remove-Item|del|erase|rm|rmdir|rd)\b"), "file_delete"),
    (re.compile(r"(?i)\b(?:taskkill(?:\.exe)?|Stop-Process|kill)\b"), "process_kill"),
    (re.compile(r"(?i)\bgit\b[^\n]*\b(?:reset\s+--hard|clean\s+-f|checkout\s+--)\b"), "git_destructive"),
    (re.compile(r"(?i)\bwhoami(?:\.exe)?\b[^\n]*(?:/priv|/groups|/all)\b"), "privilege_enumeration"),
    (re.compile(r"(?i)\bpowercfg(?:\.exe)?\b"), "power_config"),
)
_SAFE_ARGV_HEADS = frozenset({"where", "where.exe", "whoami", "whoami.exe", "hostname", "hostname.exe", "systeminfo", "systeminfo.exe", "ipconfig", "ipconfig.exe", "tasklist", "tasklist.exe", "notepad", "notepad.exe", "calc", "calc.exe", "mspaint", "mspaint.exe", "charmap", "charmap.exe"})
_GIT_READONLY_SUBCOMMANDS = frozenset({"status", "diff", "log", "branch", "rev-parse", "show"})
_GIT_BRANCH_SAFE_ARGS = frozenset({"--show-current", "-a", "--all", "-r", "--remotes", "-v", "-vv", "--list"})
_UNSAFE_WRITE_SUFFIXES = frozenset({".exe", ".bat", ".cmd", ".ps1", ".psm1", ".psd1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".msi", ".msp", ".scr", ".hta", ".cpl", ".msc", ".jar", ".reg", ".lnk", ".url", ".dll", ".sys", ".com", ".pif", ".py", ".pyw", ".sh", ".bash"})
_DANGEROUS_SHELL_CHAIN_RE = re.compile(r"(?:&&|\|\||;\s*(?:Remove-Item|del|rm|taskkill|shutdown|reg|curl|Invoke-WebRequest)|\bIEX\b|\bInvoke-Expression\b|\bEncodedCommand\b|-enc\b)", re.I)

@dataclass(frozen=True)
class PolicyVerdict:
    tier: str; decision: str; category: str; reason: str; always_owner_button: bool = False
    def to_dict(self) -> dict[str, Any]: return asdict(self)

def is_protected_process(name: str = "", cmdline: str = "", pid: int | None = None) -> tuple[bool, str]:
    if pid is not None and (pid <= 4 or pid == os.getpid()): return True, f"системный/текущий процесс PID {pid} защищён (T3)"
    low_name, low_cmd = str(name or "").strip().lower(), str(cmdline or "").strip().lower()
    low_base = low_name.removesuffix(".exe")
    if low_name in PROTECTED_PROCESS_NAMES or low_base in PROTECTED_PROCESS_NAMES or any(p in low_base for p in ("bossman", "lsass", "csrss", "wininit", "svchost", "defender", "msmpeng", "wdnis", "sense", "winlogon", "dwm")):
        return True, f"процесс {name or pid} входит в список защищённых процессов ОС/Bossman (T3)"
    if low_cmd and any(tok in low_cmd for tok in PROTECTED_CMDLINE_TOKENS):
        return True, f"процесс {name or pid} обслуживает ядро/пульт Bossman (`{low_cmd[:80]}`) и защищён (T3)"
    return False, ""

def is_protected_service(name: str) -> tuple[bool, str]:
    low = str(name or "").strip().lower()
    if low and (low in PROTECTED_SERVICE_NAMES or any(s in low for s in PROTECTED_SERVICE_NAMES)):
        return True, f"служба безопасности/ядра «{name}» защищена от остановки и изменения (T3)"
    return False, ""

def _collect_runtime_secrets(data_dir: Path | None = None) -> tuple[str, ...]:
    vals: list[str] = []
    if data_dir is not None:
        for fname in ("token", "api-token"):
            p = Path(data_dir) / fname
            with contextlib.suppress(OSError):
                if p.is_file():
                    tok = p.read_text(encoding="utf-8", errors="replace").strip()
                    if len(tok) >= 6 and tok not in vals: vals.append(tok)
    return tuple(vals)

def scrub_secrets(text: str, extra_secrets: tuple[str, ...] = ()) -> str:
    from bossman.obs import redact
    out = str(text or "")
    for val in extra_secrets:
        if val and len(val) >= 6: out = out.replace(val, "[REDACTED]")
    out = re.sub(r"(?<!\d)\d{6,16}:[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])", "[REDACTED]", out)
    out = re.sub(r"\bsk-[A-Za-z0-9_-]{16,}\b", "[REDACTED]", out)
    out = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]{12,}", r"\1[REDACTED]", out)
    out = re.sub(r"(?i)\b(bot_token|core_token|cloud_token|local_token|api_key|vault_key|password|secret)(\s*[=:]\s*['\"]?)([^'\"\s,;}{]+)", r"\1\2[REDACTED]", out)
    return redact_text(redact(out))

def check_hard_content_invariants(text: str) -> PolicyVerdict | None:
    raw = str(text or "")
    if _CSAM_RE.search(raw): return PolicyVerdict("T3", "deny", "csam_or_minors_forbidden", "Неснимаемое правило (T3): запрещён любой сексуальный контент с несовершеннолетними.", True)
    if _NCII_RE.search(raw): return PolicyVerdict("T3", "deny", "non_consensual_real_person_sexual_imagery_forbidden", "Неснимаемое правило (T3): запрещены интимные изображения реального человека без согласия.", True)
    if _CLOUD_PERSONAL_MEDIA_RE.search(raw): return PolicyVerdict("T3", "deny", "owner_personal_media_never_leaves_machine_to_cloud", "Неснимаемое правило (T3): личные фото владельца не отправляются в облако.", True)
    return None

def _norm_path(path_str: str) -> str:
    s = os.path.expandvars(str(path_str or "").strip()).replace("\\", "/")
    if not s: return ""
    if s.startswith("//?/"): s = s[4:]
    drive, rest = (s[:2], s[2:]) if (len(s) >= 2 and s[0].isalpha() and s[1] == ":") else ("", s)
    if ":" in rest: rest = rest.split(":", 1)[0]
    return drive + "/".join(seg.rstrip(". ") if seg not in {".", ".."} else seg for seg in rest.split("/"))

def _has_ntfs_ads(path_str: str) -> bool:
    s = str(path_str or "").strip().replace("\\", "/")
    if s.startswith("//?/"): s = s[4:]
    return ":" in (s[2:] if (len(s) >= 2 and s[0].isalpha() and s[1] == ":") else s)

def is_t3_secret_or_protected_path(path_str: str | Path, *, for_write: bool = False, repo_root: Path | None = None, data_dir: Path | None = None) -> tuple[bool, str]:
    raw = str(path_str or "").strip()
    if for_write and _has_ntfs_ads(raw): return True, f"запись в NTFS Alternate Data Stream ({raw}) запрещена (T3)"
    norm = _norm_path(raw)
    if not norm: return True, "пустой путь"
    if re.search(r"%[A-Za-z_][A-Za-z0-9_]*%", norm) or re.search(r"\$env:[A-Za-z_]", norm, re.I):
        return True, f"нераскрытая переменная окружения в пути ({norm}) запрещена (T3)"
    try: resolved_norm = _norm_path(str(Path(norm).expanduser().resolve()))
    except OSError: resolved_norm = norm
    for check_s in (norm, raw.replace("\\", "/"), resolved_norm):
        for pat in _T3_SECRET_PATTERNS:
            if pat.search(check_s): return True, f"доступ к защищённому секрету/пину/автозагрузке ({Path(norm).name}) запрещён (T3)"
    for c_git in (norm.lower(), resolved_norm.lower()):
        if "/.git/hooks" in c_git or c_git.endswith("/.git/config") or c_git.startswith(".git/hooks") or c_git == ".git/config":
            return True, f"доступ к чувствительному контуру Git ({norm}) запрещён (T3)"
    candidates = [norm, resolved_norm]
    for r_root in [r for r in (repo_root, Path(__file__).resolve().parents[3]) if r is not None]:
        with contextlib.suppress(Exception): candidates.append(str(Path(resolved_norm).relative_to(r_root.resolve())).replace("\\", "/"))
    protected_markers = ("docs/constitution/", "command-center/bcc/autonomy/", "command-center/bcc/approvals.py", "command-center/bcc/permissions.py", "command-center/bcc/auth.py", "command-center/bcc/secrets.py", "command-center/bcc/features/control_plane.py", "command-center/bcc/features/pc_agent.py", "command-center/bcc/features/pc_agent_policy.py")
    for cand in candidates:
        low_c = cand.lower()
        if any(b in Path(low_c).name for b in ("approvals.py", "permissions.py", "secrets.py", "pc_agent_policy.py")) and (for_write or "secrets.py" in low_c):
            return True, f"путь {cand} защищён правилами гейта (T3)"
        for marker in protected_markers:
            m = marker.rstrip("/")
            if (low_c == m or low_c.endswith("/" + m) or marker in low_c) and (for_write or "constitution" in low_c or "secrets.py" in low_c):
                return True, f"путь {cand} защищён Конституцией v2 / правилами гейта (T3)"
        if for_write and is_protected_path(cand): return True, f"запись в защищённый путь {cand} запрещена (T3 PROTECTED_GLOBS)"
    return False, ""

def default_safe_roots(data_dir: Path | None = None) -> list[Path]:
    home = Path.home()
    roots = [home / "Desktop", home / "Documents", home / "Downloads", Path(os.environ.get("TEMP", str(home / "AppData" / "Local" / "Temp")))]
    if data_dir is not None: roots.append(Path(data_dir) / "pc-agent" / "workspace")
    out: list[Path] = []
    for r in roots:
        with contextlib.suppress(OSError): out.append(r.resolve())
    return out

def is_within_safe_roots(path_str: str, safe_roots: list[Path] | None = None) -> bool:
    norm = _norm_path(path_str)
    if not norm or _has_ntfs_ads(path_str): return False
    try:
        raw_path = Path(norm).expanduser()
        if raw_path.exists() and raw_path.is_symlink(): return False
        target = raw_path.resolve()
    except OSError: return False
    t_low = str(target).replace("\\", "/").lower()
    if "/.git/" in t_low or t_low.endswith("/.git") or "start menu/programs/startup" in t_low or Path(Path(norm).name.rstrip(". ").lower()).suffix.lower() in _UNSAFE_WRITE_SUFFIXES:
        return False
    for root in (safe_roots if safe_roots is not None else default_safe_roots()):
        with contextlib.suppress(ValueError, OSError):
            target.relative_to(root.resolve()); return True
    return False

def PromptIntentReadOnly(task_prompt: str) -> bool:
    text = str(task_prompt or "").strip().lower()
    if not text or any(v in text for v in ("напиши", "сохрани", "создай", "измени", "удали", "запусти", "выполни", "открой блокнот и напиши", "открой и введи", "установи", "перезагрузи", "скоприруй", "перемести", "отредактируй", "write", "save", "create", "delete", "run", "execute", "install", "type", "click")):
        return False
    return any(v in text for v in ("прочитай", "покажи", "проверь", "список", "что в файле", "что написано", "какой заголовок", "скриншот", "экран", "статус", "read", "show", "list"))

def _is_git_readonly_invocation(argv: list[str]) -> bool:
    if len(argv) < 2 or argv[1].lower() not in _GIT_READONLY_SUBCOMMANDS: return False
    rest = argv[2:]
    if any(a.lower() in {"-c", "--config-env", "--exec-path", "--git-dir", "--work-tree", "--no-index", "--ext-diff", "--textconv"} or a.lower().startswith(("--output", "--exec=", "--git-dir=", "--work-tree=")) for a in rest):
        return False
    return all(a in _GIT_BRANCH_SAFE_ARGS for a in rest) if argv[1].lower() == "branch" else True

def _is_readonly_sys_cmd(argv: list[str]) -> bool:
    if not argv: return False
    head = Path(argv[0]).name.lower()
    if head not in {"whoami", "whoami.exe", "hostname", "hostname.exe", "where", "where.exe", "systeminfo", "systeminfo.exe", "ipconfig", "ipconfig.exe", "tasklist", "tasklist.exe"}: return False
    rest = [a.strip().lower() for a in argv[1:]]
    if head.startswith("ipconfig"): return all(a in {"/all", "-all"} for a in rest)
    if head.startswith(("systeminfo", "tasklist")): return not any(a in {"/s", "-s", "/u", "-u", "/p", "-p"} or a.startswith(("/s:", "/u:", "/p:")) for a in rest)
    return len(rest) == 0 if head.startswith(("whoami", "hostname")) else True

def normalize_bossman_api_path(raw_path: str) -> tuple[str, bool]:
    raw = str(raw_path or "").strip()
    if not raw: return "", True
    cur = raw.split("?", 1)[0].split("#", 1)[0]
    suspicious = bool(re.search(r"(?i)(?:%2e|%2f|%5c|%00|\.\.|\\|//)", raw))
    for _ in range(3):
        nxt = unquote(cur)
        if nxt == cur: break
        cur = nxt
    cur = cur.replace("\\", "/")
    if ".." in cur.split("/") or "\x00" in cur: suspicious = True
    norm = posixpath.normpath(re.sub(r"/+", "/", cur)).lower()
    return (norm if norm.startswith("/") else "/" + norm), suspicious

def classify_bossman_api(method: str, raw_path: str) -> PolicyVerdict:
    m = str(method or "GET").strip().upper()
    norm_path, suspicious = normalize_bossman_api_path(raw_path)
    forbidden = ("/api/approvals", "/api/autonomy", "/api/control-plane", "/api/providers", "/api/secrets", "/api/auth", "/api/pc-agent/trust", "/api/pc-agent/untrust", "/api/pc-agent/stop", "/api/pc-agent/resume", "/approve", "/rollback")
    if suspicious or not norm_path.startswith("/api/") or any(f in norm_path for f in forbidden):
        return PolicyVerdict("T3", "deny", "bossman_api_forbidden", f"Вызов {m} {raw_path} запрещён политикой T3.", True)
    if m == "GET" and (norm_path in {"/api/system", "/api/models", "/api/agents", "/api/status", "/api/health/live"} or norm_path.startswith("/api/motion-")):
        return PolicyVerdict("T0", "auto", "bossman_api_read", f"Чтение Bossman API {norm_path} (T0).")
    if m == "POST" and norm_path in {"/api/motion-studio/preview"}:
        return PolicyVerdict("T1", "auto", "bossman_api_motion", f"Вызов {m} {norm_path} (T1).")
    return PolicyVerdict("T2", "ask", "bossman_api_unlisted", f"Вызов {m} {norm_path} не входит в белый список и требует кнопки владельца (T2).", True)

def is_bossman_api_token_allowed(method: str, raw_path: str) -> bool:
    v = classify_bossman_api(method, raw_path)
    return v.tier in {"T0", "T1"} and v.decision == "auto"

def classify_pc_action(tool: str, args: dict[str, Any], *, task_prompt: str = "", tainted_by_injection: bool = False, trust_active: bool = False, repo_root: Path | None = None, safe_roots: list[Path] | None = None, data_dir: Path | None = None) -> PolicyVerdict:
    args = dict(args or {})
    blob = json.dumps({"tool": tool, "args": args}, ensure_ascii=False, default=str)
    inv = check_hard_content_invariants(blob) or check_hard_content_invariants(task_prompt)
    if inv is not None: return inv
    if tainted_by_injection and tool not in {"pc.observe_screen", "pc.list_windows", "pc.list_processes", "pc.system_info"}:
        if PromptIntentReadOnly(task_prompt) or tool in {"pc.run_command", "pc.delete_file", "pc.write_file"}:
            return PolicyVerdict("T3", "deny", "prompt_injection_blocked", "Заблокировано защитой от prompt injection.", True)
    if tool in {"pc.observe_screen", "pc.list_windows", "pc.list_processes", "pc.list_services", "pc.system_info", "pc.bossman_status"}:
        return PolicyVerdict("T0", "auto", "observe", "Чтение состояния ПК и Bossman (T0).")
    if tool in {"pc.read_file", "pc.list_dir"}:
        path = str(args.get("path") or "").strip()
        bad, why = is_t3_secret_or_protected_path(path, for_write=False, repo_root=repo_root, data_dir=data_dir)
        return PolicyVerdict("T3", "deny", "secret_or_protected_path", why, True) if bad else PolicyVerdict("T0", "auto", "filesystem_read", f"Чтение {path} (T0).")
    if tool in {"pc.write_file", "pc.edit_file", "pc.copy_file", "pc.move_file"}:
        raw_p, raw_d = str(args.get("path") or "").strip(), str(args.get("dst") or "").strip()
        if raw_p and raw_d and _norm_path(raw_p) != _norm_path(raw_d):
            return PolicyVerdict("T3", "deny", "inconsistent_path_dst", "Несовпадающие path и dst запрещены (T3).", True)
        dst_path, src_path = (raw_p or raw_d), str(args.get("src") or "").strip()
        bad_dst, why_dst = is_t3_secret_or_protected_path(dst_path, for_write=True, repo_root=repo_root, data_dir=data_dir)
        if bad_dst: return PolicyVerdict("T3", "deny", "secret_or_protected_path", why_dst, True)
        if src_path:
            bad_src, why_src = is_t3_secret_or_protected_path(src_path, for_write=(tool == "pc.move_file"), repo_root=repo_root, data_dir=data_dir)
            if bad_src: return PolicyVerdict("T3", "deny", "secret_or_protected_path", why_src, True)
        if PromptIntentReadOnly(task_prompt): return PolicyVerdict("T3", "deny", "unrequested_mutation", "Модификация файлов отклонена в задаче чтения.", True)
        eff_roots = safe_roots if safe_roots is not None else default_safe_roots(data_dir)
        if is_within_safe_roots(dst_path, eff_roots) and (tool != "pc.move_file" or not src_path or is_within_safe_roots(src_path, eff_roots)):
            return PolicyVerdict("T1", "auto", "filesystem_write_safe", f"Обратимая запись {dst_path} с откатом (T1).")
        return PolicyVerdict("T2", "trust_window" if trust_active else "ask", "filesystem_write_outside_workspace", f"Запись вне рабочих папок ({dst_path}) требует подтверждения (T2).")
    if tool == "pc.delete_file":
        path = str(args.get("path") or "").strip()
        bad, why = is_t3_secret_or_protected_path(path, for_write=True, repo_root=repo_root, data_dir=data_dir)
        if bad: return PolicyVerdict("T3", "deny", "secret_or_protected_path", why, True)
        if PromptIntentReadOnly(task_prompt): return PolicyVerdict("T3", "deny", "unrequested_mutation", "Удаление не запрашивалось в задаче чтения.", True)
        in_temp, temp_env = False, str(os.environ.get("TEMP") or "").strip()
        if temp_env:
            with contextlib.suppress(OSError, ValueError):
                res_p, tmp_r = Path(_norm_path(path)).expanduser().resolve(), Path(temp_env).expanduser().resolve()
                in_temp = res_p != tmp_r and res_p.is_relative_to(tmp_r)
        return PolicyVerdict("T2", "trust_window" if (trust_active and in_temp) else "ask", "file_delete", f"Удаление {path} (T2).", not in_temp)
    if tool == "pc.kill_process":
        raw_pid, raw_name = args.get("pid"), str(args.get("name") or "").strip()
        if raw_pid is None and len(raw_name) < 2: return PolicyVerdict("T3", "deny", "invalid_process_name", "Пустое или слишком короткое имя процесса (T3).", True)
        pid_int = None
        if raw_pid is not None:
            try: pid_int = int(raw_pid)
            except (ValueError, TypeError): return PolicyVerdict("T3", "deny", "invalid_pid", f"Некорректный PID: {raw_pid!r} (T3).", True)
        prot, why_p = is_protected_process(raw_name, "", pid_int)
        if prot: return PolicyVerdict("T3", "deny", "protected_process", why_p, True)
        if pid_int is not None:
            import psutil
            with contextlib.suppress(Exception):
                po = psutil.Process(pid_int)
                prot2, why2 = is_protected_process(po.name() or "", " ".join(po.cmdline() or []), pid_int)
                if prot2: return PolicyVerdict("T3", "deny", "protected_process", why2, True)
        return PolicyVerdict("T2", "trust_window" if trust_active else "ask", "process_kill", f"Завершение процесса {raw_pid or raw_name} (T2).")
    if tool == "pc.window_act":
        action, target = str(args.get("action") or "focus").strip().lower(), str(args.get("title") or args.get("pid") or args.get("hwnd") or "").strip()
        if action in {"focus", "minimize", "maximize", "restore"}: return PolicyVerdict("T1", "auto", "window_reversible", f"Окно ({action}: {target}, T1).")
        return PolicyVerdict("T2", "trust_window" if (trust_active and action == "close") else "ask", "window_close" if action == "close" else "window_unknown", f"Действие с окном {action} (T2).")
    if tool == "pc.control_service":
        svc_name, action = str(args.get("name") or "").strip(), str(args.get("action") or "status").strip().lower()
        if action == "status": return PolicyVerdict("T0", "auto", "service_status", f"Проверка службы {svc_name} (T0).")
        prot_s, why_s = is_protected_service(svc_name)
        return PolicyVerdict("T3", "deny", "protected_service", why_s, True) if prot_s else PolicyVerdict("T2", "ask", "service_control", f"Действие «{action}» со службой «{svc_name}» требует кнопки владельца (T2).", True)
    if tool == "pc.desktop_act":
        action, target, semantic = str(args.get("action") or "").strip().lower(), str(args.get("target") or "").strip(), str(args.get("semantic") or "").strip().lower()
        desk_path = str(args.get("path") or args.get("dst") or "").strip()
        if semantic in {"pay", "payment", "purchase", "wallet", "bank_transfer", "resume", "approve yourself"} or re.search(r"(?iu)\b(оплат\w*|перевод\w*|купить|pay|purchase|wallet|bossman\s+approvals)\b", f"{target} {semantic}"):
            return PolicyVerdict("T3", "deny", "desktop_payment_or_gate", f"Цель «{target or semantic}» затрагивает оплату или гейт (T3).", True)
        if action == "notepad_roundtrip" or desk_path:
            check_p = desk_path or str((Path(data_dir) / "pc-agent" / "workspace" / "notepad_note.txt") if data_dir else (Path.home() / "Desktop" / "notepad_note.txt"))
            bad_p, why_p = is_t3_secret_or_protected_path(check_p, for_write=True, repo_root=repo_root, data_dir=data_dir)
            if bad_p: return PolicyVerdict("T3", "deny", "secret_or_protected_path", why_p, True)
            if PromptIntentReadOnly(task_prompt): return PolicyVerdict("T3", "deny", "unrequested_mutation", "Запись через GUI не запрашивалась в задаче чтения.", True)
            if not is_within_safe_roots(check_p, safe_roots if safe_roots is not None else default_safe_roots(data_dir)):
                return PolicyVerdict("T2", "ask", "filesystem_write_outside_workspace", f"Запись через Блокнот вне рабочих папок ({check_p}) требует подтверждения (T2).", True)
        if action == "launch" and Path(str(args.get("app") or target or "notepad")).name.lower().removesuffix(".exe") not in {"notepad", "calc", "mspaint", "charmap"}:
            return PolicyVerdict("T2", "ask", "desktop_launch_unlisted", "Запуск приложения требует кнопки владельца (T2).", True)
        if re.search(r"(?iu)\b(удал\w*|delete|uninstall|форматир\w*|отправ\w*|публик\w*|send|publish|submit)\b", f"{target} {semantic}"):
            return PolicyVerdict("T2", "trust_window" if trust_active else "ask", "desktop_consequential", f"Действие «{action}» по «{target}» (T2).")
        if action in {"launch", "focus", "focus_window", "click", "double_click", "type", "key", "hotkey", "scroll", "wait", "notepad_roundtrip"}:
            return PolicyVerdict("T1", "auto", "desktop_reversible", f"Действие на рабочем столе: {action} (T1).")
        return PolicyVerdict("T2", "ask", "desktop_unknown", f"Нестандартное действие {action} (T2).")
    if tool == "pc.browser_act":
        action, url = str(args.get("action") or "open").strip().lower(), str(args.get("url") or "").strip()
        if action in {"purchase", "payment", "wallet", "bank_transfer"}: return PolicyVerdict("T3", "deny", "browser_payment_forbidden", f"Платежи в браузере запрещены ({action}, T3).", True)
        if url and (url.split(":", 1)[0].lower() if ":" in url else "") not in {"http", "https"}: return PolicyVerdict("T3", "deny", "browser_invalid_scheme", "Разрешены только http/https.", True)
        if action in {"read_dom", "snapshot", "screenshot"}: return PolicyVerdict("T0", "auto", "browser_read", f"Чтение браузера ({action}, T0).")
        if action in {"open", "navigate", "click", "type", "select", "back", "reload"}:
            lbl = str(args.get("target") or args.get("selector") or args.get("text") or "")
            if action == "click" and re.search(r"(?iu)\b(оплат\w*|купить|buy|pay|checkout)\b", lbl): return PolicyVerdict("T3", "deny", "browser_payment_click", f"Клик по оплате «{lbl}» запрещён (T3).", True)
            if action == "click" and re.search(r"(?iu)\b(удал\w*|отправ\w*|опублик\w*|delete|submit|send|publish)\b", lbl): return PolicyVerdict("T2", "ask", "browser_consequential_click", f"Клик «{lbl}» требует подтверждения (T2).", True)
            return PolicyVerdict("T1", "auto", "browser_interactive", f"Действие в браузере ({action}, T1).")
        if action in {"submit", "upload", "download", "login"}:
            always_btn = action in {"submit", "login", "upload"}
            return PolicyVerdict("T2", "trust_window" if (trust_active and not always_btn) else "ask", f"browser_{action}", f"Действие браузера {action} (T2).", always_btn)
        return PolicyVerdict("T2", "ask", "browser_other", f"Действие браузера {action} (T2).")
    if tool == "pc.run_command":
        argv = args.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x.strip() for x in argv):
            return PolicyVerdict("T3", "deny", "invalid_argv", "Разрешён только непустой список аргументов argv.", True)
        expanded_argv = [os.path.expandvars(x) for x in argv]
        joined, expanded_joined = " ".join(argv), " ".join(expanded_argv)
        for pat, cat in _T3_CMD_PATTERNS:
            if pat.search(joined) or pat.search(expanded_joined): return PolicyVerdict("T3", "deny", cat, f"Команда обращается к запрещённому ресурсу T3 ({cat}).", True)
        for token in [*argv, *expanded_argv]:
            norm_tok = _norm_path(token)
            if any(p.search(token) or p.search(norm_tok) for p in _T3_SECRET_PATTERNS): return PolicyVerdict("T3", "deny", "secret_path_in_argv", f"Аргумент обращается к секрету ({token}, T3).", True)
            if "/" in norm_tok or "\\" in token or any(k in norm_tok.lower() for k in ("constitution", "vault.key", "credentials.enc", "companion.enc", "bossman.db", "api-token", "token", "secrets.py", "approvals.py", "permissions.py", "autonomy")):
                bad_tok, why_tok = is_t3_secret_or_protected_path(token, for_write=True, repo_root=repo_root, data_dir=data_dir)
                if bad_tok: return PolicyVerdict("T3", "deny", "protected_path_in_argv", why_tok, True)
        head = Path(argv[0]).name.lower()
        if PromptIntentReadOnly(task_prompt):
            is_ro = _is_readonly_sys_cmd(argv) or (head in {"git", "git.exe"} and _is_git_readonly_invocation(argv)) or (head in {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"} and len(argv) == 2 and argv[1] in {"--version", "-V"})
            if not is_ro or _DANGEROUS_SHELL_CHAIN_RE.search(joined): return PolicyVerdict("T3", "deny", "mutation_command_in_readonly", "Модифицирующая команда отклонена в задаче чтения.", True)
        for pat, cat in _T2_ALWAYS_BUTTON_PATTERNS:
            if pat.search(joined) or pat.search(expanded_joined): return PolicyVerdict("T2", "ask", cat, f"Команда «{cat}» ({joined[:120]}) требует кнопки владельца (T2).", True)
        if _DANGEROUS_SHELL_CHAIN_RE.search(joined) or _DANGEROUS_SHELL_CHAIN_RE.search(expanded_joined):
            return PolicyVerdict("T2", "ask", "shell_chain", f"Цепочка операторов ({joined[:120]}) требует кнопки владельца (T2).", True)
        for pat, cat in _T2_CMD_PATTERNS:
            if pat.search(joined) or pat.search(expanded_joined): return PolicyVerdict("T2", "trust_window" if trust_active else "ask", cat, f"Рискованная команда «{cat}» ({joined[:120]}, T2).", False)
        if head in {"git", "git.exe"} and any(a.lower() in {"-c", "--config-env", "--exec-path", "--ext-diff", "--textconv", "--upload-pack", "--receive-pack"} or a.lower().startswith(("--output", "--exec=", "--exec-path=", "--config-env=", "--upload-pack=", "--receive-pack=")) for a in argv[1:]):
            # -c core.pager=..., --output=..., --upload-pack=... run or write arbitrary things: never inside /trust
            return PolicyVerdict("T2", "ask", "git_option_exec", f"Опция git, исполняющая или пишущая произвольное (`{joined[:120]}`), требует кнопки владельца (T2).", True)
        if head in {"git", "git.exe"}:
            return PolicyVerdict("T0", "auto", "git_read", f"Чтение git ({argv[1].lower()}, T0).") if _is_git_readonly_invocation(argv) else PolicyVerdict("T2", "trust_window" if trust_active else "ask", "git_mutation", f"Команда git `{joined[:120]}` (T2).")
        if head in {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}:
            return PolicyVerdict("T0", "auto", "python_version", "Проверка версии Python (T0).") if (len(argv) == 2 and argv[1] in {"--version", "-V"}) else PolicyVerdict("T2", "ask", "interpreter_execution", f"Запуск интерпретатора Python (`{joined[:120]}`) требует кнопки владельца (T2).", True)
        if head not in _SAFE_ARGV_HEADS: return PolicyVerdict("T2", "ask", "unlisted_executable", f"Исполняемый файл {head!r} не входит в белый список — требуется кнопка владельца (T2).", True)
        if _is_readonly_sys_cmd(argv): return PolicyVerdict("T0", "auto", "command_read", f"Информационная команда {head} (T0).")
        if head in {"tasklist", "tasklist.exe", "systeminfo", "systeminfo.exe", "whoami", "whoami.exe", "hostname", "hostname.exe"}:
            # the read-only check refused it (remote /s /u /p or extra switches): not a safe local read any more
            return PolicyVerdict("T2", "ask", "remote_or_credentialed_sys_command", f"Команда {joined[:120]} с удалённой целью или учётными данными требует кнопки владельца (T2).", True)
        if head in {"ipconfig", "ipconfig.exe", "systeminfo", "systeminfo.exe"}: return PolicyVerdict("T2", "trust_window" if trust_active else "ask", "network_or_remote_sys_command", f"Команда {joined[:120]} (T2).")
        return PolicyVerdict("T1", "auto", "command_safe", f"Разрешённая команда {head} (T1).")
    if tool == "pc.bossman_api": return classify_bossman_api(str(args.get("method") or "GET"), str(args.get("path") or ""))
    if tool == "pc.earn":
        action, mode_arg, edition_arg = str(args.get("action") or "status").lower(), str(args.get("mode") or "").strip().lower(), str(args.get("edition") or "").strip().lower()
        try:
            rm = float(args.get("real_money") or 0.0)
            rm = 1.0 if not math.isfinite(rm) else rm
        except (TypeError, ValueError): rm = 1.0
        if action in {"pay", "withdraw", "live_trade", "publish_external", "submit_bid", "fincept_enterprise", "fincept_live_broker", "vibe_trading_live"} or mode_arg in {"live", "real", "real_money", "broker"} or edition_arg in {"enterprise", "exclusive", "exclusive+", "exclusive_pro", "paid"} or rm > 0.0:
            return PolicyVerdict("T3", "deny", "earn_external_or_live_forbidden", f"Действие {action} с реальными деньгами/внешней отправкой запрещено (T3).", True)
        if action == "fresh_vibes_publish": return PolicyVerdict("T2", "ask", "fresh_vibes_external_publish", "Публикация в Instagram требует кнопки владельца (T2).", True)
        if action in {"status", "arena", "exam", "roi", "poker", "studio", "oss", "product", "weekly", "report", "blender", "fincept", "vibe_trading", "vibe_trade"}:
            return PolicyVerdict("T0", "auto", f"earn_{action}", f"Чтение контура Bossman Earn ({action}, T0).")
        return PolicyVerdict("T1", "auto", f"earn_{action}", f"Задача Bossman Earn ({action}, SIMULATED/$0, T1).")
    return PolicyVerdict("T2", "ask", "unknown_tool", f"Неизвестный инструмент {tool} требует кнопки владельца (T2).", True)
