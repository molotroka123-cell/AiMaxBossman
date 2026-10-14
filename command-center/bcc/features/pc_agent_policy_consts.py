"""Политика Argon PC-Agent: константы, регулярные выражения и вердикт."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class PolicyVerdict:
    tier: str
    decision: str
    category: str
    reason: str
    always_owner_button: bool = False
    to_dict = asdict


HARD_INVARIANTS = (
    "csam_or_minors_forbidden",
    "non_consensual_real_person_sexual_imagery_forbidden",
    "owner_personal_media_never_leaves_machine_to_cloud",
)
_CSAM_RE = re.compile(
    r"(?iu)\b(csam|child\s*(?:sex|porn|nude)|underage\s*(?:sex|nude|erotic)|несовершеннолетн\w*\s+"
    r"(?:секс\w*|обнаж\w*|эротик\w*|порн\w*)|"
    r"(?:детск\w*|школьни\w*|подростк\w*)\s+"
    r"(?:порн\w*|эротик\w*|обнаж\w*|интим\w*))\b"
)
_NCII_RE = re.compile(
    r"(?iu)\b(deepfake\s*nude|undress\s*(?:photo|her|him|person)|nudify|"
    r"раздень\w*\s+(?:по\s+фото|человека"
    r"|девушку|женщину|мужчину)|"
    r"обнаж\w+\s+по\s+фото\s+реальн\w+|"
    r"без\s+согласия\s+(?:обнаж|разде|интим))\b"
)
_CLOUD_PERSONAL_MEDIA_RE = re.compile(
    r"(?iu)(?:отправ\w*|загруз\w*|upload|send|exfiltrat\w*)\s+[^.!?\n]{0,60}"
    r"(?:личн\w+\s+фото|мои\s+фото"
    r"|personal\s+photo\w*|family\s+photo\w*|camera\s+roll)[^.!?\n]{0,60}"
    r"(?:в\s+облако|на\s+сервер|в\s+интернет|to\s+cloud|external)"
)
_T3_SECRET_PATTERNS = tuple(
    re.compile(p)
    for p in (
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
        r"|[\\/]\.(?:aws|azure|kube|docker|codex|claude|ssh)(?:[\\/]|$)"
        r"|\.(?:pem|key|pfx|p12|kdbx)$|\bdpapi\b",
    )
)
_SENSITIVE_ENV_RE = re.compile(
    r"(?i)(TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|APIKEY|PRIVATE_KEY|VAULT|CREDENTIAL"
    r"|OPENROUTER|ANTHROPIC|OPENAI|GEMINI_API|MISTRAL|DEEPSEEK|TG_COMPANION|BCC_TOKEN)"
)
_PROMPT_INJECTION_RE = re.compile(
    r"(?iu)(?:ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions|disregard\s+(?:all\s+)?"
    r"(?:previous|prior)\s+instructions|system\s*override|new\s+instructions\s*:|игнорируй\w*\s+(?:все\s+)?"
    r"(?:предыдущ\w+|прежни\w+)\s+(?:инструкц\w+|правил\w+|указани\w+)|"
    r"забудь\w*\s+(?:все\s+)?"
    r"(?:предыдущ\w+|прежни\w+)\s+(?:инструкц\w+|правил\w+)|"
    r"выполни\s+команду\s+в\s+обход"
    r"|отправь\s+токен|exfiltrate|curl\s+http[^|\n]*\|\s*(?:sh|bash|pwsh|powershell))"
)
PROTECTED_PROCESS_NAMES = frozenset(
    (
        "bcc bcc.exe bossman bossman.exe ollama ollama.exe ollama_llama_server ollama_llama_server.exe "
        "lsass lsass.exe csrss csrss.exe wininit wininit.exe services services.exe svchost svchost.exe "
        "windefend msmpeng msmpeng.exe nissrv nissrv.exe wdnissvc securityhealthservice "
        "securityhealthservice.exe sense sense.exe wscsvc mpssvc smss smss.exe winlogon winlogon.exe dwm dwm.exe "
        "system"
    ).split()
)
PROTECTED_CMDLINE_TOKENS = (
    "bcc.main",
    "bcc\\main",
    "bcc/main",
    "telegram_companion",
    "telegram-companion",
    "run_backend_source",
    "run_pult_source",
    "uvicorn",
    ":8801",
    "--port 8801",
    "ollama serve",
    "ollama_llama_server",
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
    "command-center/bcc/features/pc_agent_policy.py command-center/bcc/features/pc_agent_policy_paths.py "
    "command-center/bcc/features/pc_agent_policy_consts.py command-center/bcc/features/pc_agent_policy_procs.py "
    "command-center/bcc/features/pc_agent_policy_cmds.py "
    "command-center/bcc/features/pc_agent_policy_api.py"
).split()
_PROTECTED_BASENAMES = (
    "approvals.py permissions.py secrets.py pc_agent_policy.py pc_agent_policy_paths.py "
    "pc_agent_policy_consts.py pc_agent_policy_procs.py pc_agent_policy_cmds.py "
    "pc_agent_policy_api.py"
).split()
_MUTATION_VERBS = (
    "напиши", "сохрани", "создай", "измени", "удали", "запусти", "выполни",
    "открой блокнот и напиши", "открой и введи", "установи",
    "перезагрузи",
    "скопируй", "скоприруй", "перемести", "переименуй", "отредактируй",
    "убей", "заверши", "останови", "write", "save", "create", "delete", "run",
    "execute", "install", "type", "click", "kill", "stop", "move", "copy", "edit",
)
_READONLY_VERBS = (
    "прочитай", "покажи", "проверь", "список", "что в файле",
    "что написано",
    "какой заголовок", "скриншот", "экран", "статус", "read", "show", "list",
)

_T3_CMD_PATTERNS = tuple(
    (re.compile(p, re.I), c)
    for p, c in (
        (
            r"\bgit\b[^\n]*\bpush\b(?:[^\n]*\s(?:--force|-f|--force-with-lease)\b"
            r"|[^\n]*\s\+[A-Za-z0-9_./-]+)",
            "force_push_forbidden",
        ),
        (
            r"\bgit\b[^\n]*\b(?:commit|push|merge)\b[^\n]*\s(?:--no-verify|-n)\b",
            "selftest_bypass_forbidden",
        ),
        (
            r"\b(?:STOP_WATCHDOG|DISABLE_AUDIT|BOSSMAN_SKIP_SELFTEST)\b",
            "disable_watchdog_or_audit_forbidden",
        ),
        (r"\b(?:Set-MpPreference|Add-MpPreference)\b[^\n]*Disable", "disable_defender_forbidden"),
        (r"\bnetsh\b[^\n]*\badvfirewall\b[^\n]*\bstate\s+off\b", "disable_firewall_forbidden"),
        (r"\breg\s+(?:add|delete)\b[^\n]*Policies\\System[^\n]*EnableLUA", "disable_uac_forbidden"),
        (
            r"\b(?:adopt_constitution_v2\.ps1|constitution\.sha256)\b",
            "constitution_pin_owner_only",
        ),
        (
            r"\b(?:vault\.key|credentials\.enc|provider-keys\.env|api-token|companion\.enc)\b",
            "secret_store_forbidden",
        ),
        (r"\b(?:CryptUnprotectData|ProtectedData::Unprotect)\b", "dpapi_unprotect_forbidden"),
        (r"\bbcdedit(?:\.exe)?\b", "bcdedit_boot_config_forbidden"),
    )
)
_T2_ALWAYS_BUTTON_PATTERNS = tuple(
    (re.compile(p, re.I), c)
    for p, c in (
        (r"\b(?:shutdown(?:\.exe)?|Restart-Computer|Stop-Computer)\b", "system_power"),
        (
            r"\bgit\b[^\n]*\b(?:push|merge|reset\s+--hard|clean\s+-[A-Za-z]*f|checkout\s+--)\b",
            "git_destructive_or_promotion",
        ),
        (r"\b(?:Remove-Item|del|erase|rm|rmdir|rd)\b", "file_delete"),
        (r"\b(?:Format-Volume|Clear-Disk|cipher\s+/w)\b", "disk_wipe"),
        (
            r"\b(?:powershell|pwsh)(?:\.exe)?\b[^\n]*(?:\s-(?:e|ec|enc\w*|encodedcommand|c|command|f|file)\b"
            r"|\bIEX\b|\bInvoke-Expression\b)",
            "powershell_inline_exec",
        ),
        (r"\bcmd(?:\.exe)?\b[^\n]*\s/[ck]\b", "cmd_inline_exec"),
        (
            r"\b(?:schtasks|certutil|bitsadmin|wmic|setx|icacls|takeown)(?:\.exe)?\b",
            "system_persistence_or_acl",
        ),
        (r"\bnet(?:\.exe|1\.exe)?\s+(?:user|localgroup|group)\b", "account_mutation"),
        (r"\b(?:python|python3|py)(?:\.exe)?\b[^\n]*\s-[cm]\b", "python_inline_exec"),
        (r"\b(?:node|deno|bun)(?:\.exe)?\b[^\n]*\s(?:-e|-p|--eval)\b", "node_inline_exec"),
        (
            r"\b(?:curl|scp|sftp|ftp)(?:\.exe)?\b"
            r"|\b(?:Invoke-WebRequest|Invoke-RestMethod|Start-BitsTransfer)\b",
            "network_transfer",
        ),
        (
            r"\b(?:reg(?:\.exe)?\b|New-ItemProperty|Set-ItemProperty|Remove-ItemProperty)\b",
            "registry_mutation",
        ),
        (
            r"\b(?:sc(?:\.exe)?\b|Stop-Service|Set-Service|New-Service|Restart-Service)\b",
            "system_service",
        ),
        (
            r"\b(?:msiexec|winget|choco)(?:\.exe)?\b|\b(?:pip|pip3|npm|pnpm|yarn)\s+install\b"
            r"|\buv\s+pip\s+install\b",
            "software_install",
        ),
    )
)
_T2_CMD_PATTERNS = tuple(
    (re.compile(p, re.I), c)
    for p, c in (
        (r"\b(?:taskkill(?:\.exe)?|Stop-Process|kill)\b", "process_kill"),
        (r"\bwhoami(?:\.exe)?\b[^\n]*(?:/priv|/groups|/all)\b", "privilege_enumeration"),
        (r"\bpowercfg(?:\.exe)?\b", "power_config"),
    )
)
_SAFE_ARGV_HEADS = frozenset(
    (
        "where where.exe whoami whoami.exe hostname hostname.exe systeminfo systeminfo.exe ipconfig "
        "ipconfig.exe tasklist tasklist.exe notepad notepad.exe calc calc.exe mspaint mspaint.exe "
        "charmap charmap.exe"
    ).split()
)
_GIT_READONLY_SUBCOMMANDS = frozenset("status diff log branch rev-parse show".split())
_GIT_BRANCH_SAFE_ARGS = frozenset("--show-current -a --all -r --remotes -v -vv --list".split())
_DANGEROUS_SHELL_CHAIN_RE = re.compile(
    r"(?:&&|\|\||;\s*(?:Remove-Item|del|rm|taskkill|shutdown|reg|curl|Invoke-WebRequest)"
    r"|\bIEX\b|\bInvoke-Expression\b|\bEncodedCommand\b|-enc\b)",
    re.I,
)
_OBSERVE_TOOLS = frozenset(
    "pc.observe_screen pc.list_windows pc.list_processes pc.list_services pc.system_info pc.bossman_status".split()
)
