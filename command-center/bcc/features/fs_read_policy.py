"""Read-only whole-PC file access policy (owner 11.11.2026 «доступ Qwen ко всем файлам компьютера, read-only,
чаты не читаем»). Pure functions, no I/O besides ``os.path.realpath``/``os.stat``.

A path is judged twice: the lexical absolute path AND its fully resolved target (symlinks, junctions, ``..``). The
DENY list always wins and is checked on both, so a junction from an innocent folder into a browser profile is refused.
Constitution v3: chats/DMs, personal data, photos, keys/tokens/vault/DPAPI/seed phrases are never exposed.
"""
from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass

MAX_READ_BYTES = 256 * 1024
MAX_LIST_ENTRIES = 500


@dataclass(frozen=True)
class Verdict:
    ok: bool
    code: str = ""
    why: str = ""
    path: str = ""          # the resolved path when ok


# --- DENY classes: (code, human reason, segment names, basename globs) -------------------------------------------
_CLASSES: tuple[tuple[str, str, frozenset[str], tuple[str, ...]], ...] = (
    ("FS_DENY_BROWSER", "профили браузеров (куки, пароли, история) закрыты",
     frozenset({"user data", "profiles", "brave-software", "opera software", "vivaldi", "mozilla",
                "firefox", "tor browser", "bravesoftware", "chromium"}),
     ("login data*", "cookies*", "web data*", "history*", "local state", "places.sqlite*", "key4.db",
      "key3.db", "logins.json", "cert9.db", "formhistory.sqlite", "extension cookies*", "network persistent state",
      "top sites*", "favicons*", "sessions", "session storage")),
    ("FS_DENY_CHAT", "чаты и мессенджеры не читаем",
     frozenset({"telegram desktop", "telegram", "tdata", "whatsapp", "discord", "signal", "slack", "viber",
                "skype", "microsoft teams", "teams", "element", "chat_attachments", "chat_files", "chats",
                "telegram-companion", "pult_chats"}),
     ("bcc.db*", "companion*.db*", "chat*.db*", "messages*.db*", "*.sqlite", "*.sqlite3", "*.sqlite-wal",
      "*.sqlite-shm", "*.db", "*.db-wal", "*.db-shm", "*.db-journal")),
    ("FS_DENY_SECRET", "ключи, токены, vault, DPAPI, seed-фразы не показываем",
     frozenset({"protect", "credentials", "vault", ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".gcloud",
                "metamask", "exodus", "electrum", "wallets", "wallet", "keystore", "keystores", "1password",
                "bitwarden", "lastpass", "keepass", "dashlane", "secrets", "dpapi", "certificates"}),
     ("*.pem", "*.key", "*.pfx", "*.p12", "*.ppk", "*.kdbx", "*.kdb", "*.keystore", "*.jks", "*.gpg", "*.asc",
      "*.enc", "*.wallet", "wallet.dat", "*seed*", "*mnemonic*", "*seedphrase*", "provider-keys*",
      ".env", ".env.*", "*.env", "companion.env", "*.vault", "vault*", "id_rsa*", "id_ed25519*", "id_ecdsa*",
      "id_dsa*", ".npmrc", ".netrc", ".git-credentials", ".pypirc", "credentials*", "token", "api-token",
      "*.token", "secrets.*", "*.secret", "master.key", "hosts.yml", "*.kdbx.*", "*.dpapi", "*.credential")),
    ("FS_DENY_HIVE", "системные кусты реестра и файлы подкачки закрыты",
     frozenset({"system volume information", "$recycle.bin", "regback"}),
     ("sam", "sam.*", "security", "security.*", "system", "system.*", "software", "software.*", "default",
      "default.*", "components", "ntuser.dat*", "usrclass.dat*", "pagefile.sys", "hiberfil.sys", "swapfile.sys",
      "ntds.dit", "*.hve", "*.regtrans-ms")),
    ("FS_DENY_MEDIA", "личные фото и видео не читаем",
     frozenset({"pictures", "my pictures", "photos", "dcim", "videos", "my videos", "camera roll",
                "saved pictures", "screenshots"}),
     ()),
    ("FS_DENY_MAIL", "почтовые хранилища закрыты",
     frozenset({"thunderbird", "outlook", "mailspring", "microsoft.windowscommunicationsapps", "gmail"}),
     ("*.pst", "*.ost", "*.mbox", "*.eml", "*.msg", "*.nst")),
    ("FS_DENY_GRECHKA", "каталог Гречка — личные данные",
     frozenset({"гречка", "grechka", "grechka-trap", "grechka_trap"}),
     ()),
)

# multi-segment locations (lowercase, forward slashes, matched as a contiguous run of path segments)
_SEQUENCES: tuple[tuple[str, str, str], ...] = (
    ("FS_DENY_HIVE", "windows/system32/config", "кусты реестра SAM/SYSTEM/SECURITY"),
    ("FS_DENY_HIVE", "windows/repair", "резервные кусты реестра"),
    ("FS_DENY_SECRET", "microsoft/protect", "DPAPI мастер-ключи"),
    ("FS_DENY_SECRET", "microsoft/credentials", "учётные данные Windows"),
    ("FS_DENY_SECRET", "microsoft/vault", "Windows Vault"),
    ("FS_DENY_SECRET", "microsoft/systemcertificates", "сертификаты пользователя"),
    ("FS_DENY_SECRET", "windows/system32/dpapi", "DPAPI"),
    ("FS_DENY_BROWSER", "google/chrome", "профиль Chrome"),
    ("FS_DENY_BROWSER", "microsoft/edge", "профиль Edge"),
    ("FS_DENY_CHAT", "bossman/commandcenter", "данные Bossman (базы чатов)"),
    ("FS_DENY_CHAT", "appdata/local/bossman", "данные Bossman (базы чатов)"),
    ("FS_DENY_CHAT", "appdata/roaming/telegram desktop", "Telegram Desktop"),
    ("FS_DENY_CHAT", "microsoft/outlook", "Outlook"),
)

_HIVE_BARE = frozenset({"sam", "security", "system", "software", "default", "components"})
_DRIVE_RE = re.compile(r"^[a-zA-Z]:")
_DEVICE_RE = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\.|$)", re.I)


def _norm(p: str) -> str:
    """Lowercase, forward slashes, no ``\\\\?\\`` prefix, no trailing slash."""
    s = str(p).replace("\\", "/")
    if s.startswith("//?/"):
        s = s[4:]
    return s.rstrip("/").lower()


def _classify_one(path: str, extra_roots: tuple[str, ...]) -> tuple[str, str] | None:
    s = _norm(path)
    for root in extra_roots:
        r = _norm(root)
        if r and (s == r or s.startswith(r + "/")):
            return "FS_DENY_CHAT", "данные Bossman (базы чатов, токены) закрыты"
    segs = [x for x in s.split("/") if x]
    joined = "/" + "/".join(segs) + "/"
    for code, needle, why in _SEQUENCES:
        if f"/{needle}/" in joined:
            return code, why
    base = segs[-1] if segs else ""
    for code, why, names, globs in _CLASSES:
        # segment names: every component except the drive letter
        for seg in segs[1:] if _DRIVE_RE.match(segs[0] if segs else "") else segs:
            if seg in names:
                # «user data»/«profiles» only mean a browser inside appdata (a project folder may use them)
                if code == "FS_DENY_BROWSER" and seg in {"user data", "profiles", "mozilla", "firefox", "chromium"}:
                    if "appdata" not in segs and "programdata" not in segs:
                        continue
                if code == "FS_DENY_CHAT" and seg in {"element", "teams", "chats", "signal", "telegram"}:
                    if "appdata" not in segs and "programdata" not in segs and "users" not in segs:
                        continue
                if code == "FS_DENY_SECRET" and seg in {"vault", "secrets", "wallet", "wallets", "certificates",
                                                       "credentials", "keystore", "keystores", "protect"}:
                    # generic words: only secret when inside a profile / appdata / bossman data, or when it is a
                    # Windows/Bossman special; a source folder called «vault» inside a repo stays readable only
                    # if it is not directly a vault store. Be conservative: refuse everywhere.
                    pass
                return code, why
        for g in globs:
            if fnmatch.fnmatchcase(base, g):
                if code == "FS_DENY_BROWSER" and "appdata" not in segs and "programdata" not in segs:
                    continue        # «history.md» in a project is not a browser store
                if code == "FS_DENY_HIVE" and g in _HIVE_BARE and (len(segs) < 2 or segs[-2] != "config"):
                    continue        # bare «system»/«default» only mean a hive inside ...\config
                return code, why
    return None


def _env_extra_roots() -> tuple[str, ...]:
    roots: list[str] = []
    for var in ("BCC_DATA_DIR",):
        v = os.environ.get(var)
        if v:
            roots.append(v)
    la = os.environ.get("LOCALAPPDATA")
    if la:
        roots.append(os.path.join(la, "Bossman"))
    return tuple(roots)


def check_path(raw: str, *, extra_deny_roots: tuple[str, ...] = ()) -> Verdict:
    """Decide whether ``raw`` may be read/listed. DENY wins on the lexical path and on the resolved target."""
    text = str(raw or "").strip().strip('"')
    if not text or "\x00" in text:
        return Verdict(False, "FS_BAD_PATH", "пустой путь")
    text = os.path.expandvars(os.path.expanduser(text))
    norm = text.replace("/", "\\")
    if norm.startswith("\\\\") and not norm.startswith("\\\\?\\"):
        return Verdict(False, "FS_UNC", "сетевые и устройственные пути не поддерживаются")
    if norm.startswith("\\\\?\\") and norm[4:6].upper() == "UN":
        return Verdict(False, "FS_UNC", "сетевые пути не поддерживаются")
    if re.search(r"^(\\\\\?\\)?[a-zA-Z]:.*:", norm) or (not _DRIVE_RE.match(norm) and ":" in norm):
        return Verdict(False, "FS_BAD_PATH", "альтернативные потоки (:) не читаем")
    if not os.path.isabs(text):
        return Verdict(False, "FS_BAD_PATH", "нужен абсолютный путь")
    if any(_DEVICE_RE.match(part) for part in re.split(r"[\\/]", text) if part):
        return Verdict(False, "FS_BAD_PATH", "имя устройства")
    lexical = os.path.abspath(text)                    # collapses `..`
    extra = tuple(extra_deny_roots) + _env_extra_roots()
    hit = _classify_one(lexical, extra)
    if hit:
        return Verdict(False, hit[0], hit[1])
    try:
        real = os.path.realpath(lexical)
    except (OSError, ValueError):
        return Verdict(False, "FS_BAD_PATH", "путь не разрешается")
    if _norm(real) != _norm(lexical):
        hit = _classify_one(real, extra)
        if hit:
            return Verdict(False, hit[0], hit[1] + " (через ссылку/junction)")
        if real.startswith("\\\\"):
            return Verdict(False, "FS_UNC", "ссылка ведёт на сетевой путь")
    return Verdict(True, path=real)


# --- output redaction ------------------------------------------------------------------------------------------
_RX = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S),
    re.compile(r"\b[0-9a-fA-F]{41,}\b"),                                   # long hex (a 40-char git sha stays)
    re.compile(r"(?<![A-Za-z0-9+_-])(?=[A-Za-z0-9+_-]*[A-Z])(?=[A-Za-z0-9+_-]*[a-z])(?=[A-Za-z0-9+_-]*\d)"
               r"[A-Za-z0-9+_-]{40,}={0,2}(?![A-Za-z0-9+_-])"),            # long mixed base64/base64url
    re.compile(r"(?i)\b(pass(?:word|wd|phrase)?|secret|api[_-]?key|token|seed(?:_?phrase)?|mnemonic)"
               r"(\s*[=:]\s*)(['\"]?)([^'\"\s,;]{3,})"),
)


def redact(text: str) -> tuple[str, int]:
    """Mask obvious secrets; returns (text, number_of_masks)."""
    n = 0
    out = str(text)
    for rx in _RX:
        if rx.groups >= 4:
            out, k = rx.subn(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}[REDACTED]", out)
        else:
            out, k = rx.subn("[REDACTED]", out)
        n += k
    try:        # the repo's own scrubber on top (Telegram tokens, Bearer, secret-named variables)
        from .pc_agent_policy_paths import scrub_secrets
        out2 = scrub_secrets(out)
        if out2 != out:
            n += 1
            out = out2
    except Exception:  # noqa: BLE001 - redaction layer above is already applied
        pass
    return out, n


def looks_binary(chunk: bytes) -> bool:
    if chunk.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf")):
        return False
    if b"\x00" in chunk:
        return True
    if not chunk:
        return False
    bad = sum(1 for b in chunk if b < 9 or (13 < b < 32))
    return bad / len(chunk) > 0.05


def decode_text(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    return data.decode("utf-8-sig", errors="replace")
