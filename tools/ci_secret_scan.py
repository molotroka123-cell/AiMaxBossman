"""Dependency-free accidental-secret guard for CI (TZ-02 §2.1 — секрет-скан 2.0).

Три слоя: (1) паттерны провайдеров/ключей; (2) энтропийный детектор Шеннона для
длинных токенов в коде и конфиге; (3) запрещённые файлы в индексе git и содержимое
ZIP-архивов (раньше `.zip` пропускался целиком). Пометка `ci-secret-scan: allow`
на той же строке снимает срабатывание ровно для неё (тестовые канарейки).
"""
from __future__ import annotations

import io
import math
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".pdf",
               ".sqlite", ".sqlite3", ".db", ".gguf", ".safetensors", ".woff", ".woff2", ".ttf"}
ZIP_SUFFIX = {".zip"}
# Файлы, которым в индексе git не место, независимо от содержимого.
FORBIDDEN_FILES = [re.compile(p) for p in (
    r"(^|/)\.env$", r"(^|/)\.env\.[^/]*$(?<!\.example)(?<!\.sample)(?<!\.template)", r"\.pem$", r"\.p12$", r"\.pfx$",
    r"(^|/)id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$", r"(^|/)[^/]*\.key$")]
PATTERNS = [
    ("openai key", re.compile(r"\bsk-(?!ant-|or-v1-)[A-Za-z0-9_-]{20,}\b")),
    ("anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("openrouter key", re.compile(r"\bsk-or-v1-[A-Za-z0-9_-]{20,}\b")),
    ("google api key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("slack token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")),
    ("telegram bot token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("github token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("aws access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("aws secret", re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*[\"']?[A-Za-z0-9/+=]{40}\b")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY(?: BLOCK)?-----")),
    # фраза-значение в кавычках (≥ 8 слов), а не аннотация типа/kwarg вида `mnemonic: str`
    ("wallet seed label", re.compile(r"(?i)\b(?:seed phrase|mnemonic)\b\s*[:=]\s*[\"'](?:[a-z]+\s+){7,}[a-z]+[\"']")),
    ("obvious password", re.compile(r"(?i)\b(?:password|passwd)\b\s*[:=]\s*[\"'][^\"']{8,}[\"']")),
]
# Энтропия — только для кода и конфигурации; документация/UI-ассеты дают ложные
# срабатывания на base64-картинках и хешах.
ENTROPY_SUFFIX = {".py", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".env", ".sh"}
# .json/.txt/.md — манифесты и документация: идентификаторы и base64-фрагменты дают ложные
# срабатывания; для них работают только паттерны провайдеров.
# `=` и `/` не входят в тело токена: `KEY=value` и пути иначе склеиваются в одну «случайную» строку
TOKEN = re.compile(r"[A-Za-z0-9+_-]{24,}={0,2}")
ENV_NAME = re.compile(r"^[A-Z0-9_]+$")
SEQUENTIAL = re.compile(r"0123456789|123456789|abcdefghij|ABCDEFGHIJ|9876543210")     # тестовые заполнители
# Публичный ключ/адрес Solana — base58 32–44 символа: идентификатор, не секрет (секретный ключ — 87–88 символов
# и остаётся под энтропийным детектором). Не снимается, если строка говорит о секрете.
BASE58_PUBKEY = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
SECRET_CONTEXT = re.compile(r"(?i)(secret|private|seed|mnemonic|keypair|passphrase)")
# Канарейки в самом теле шаблона (синтетический PEM для проверки редакции и т.п.)
CANARY_BODY = re.compile(r"(?i)(synthetic|example|canary|placeholder|dummy|fake)")
WORDY = re.compile(r"^(?:[A-Za-z]{3,}-){2,}[A-Za-z0-9]+$")                              # слова-через-дефис
HEX = re.compile(r"^[0-9a-fA-F]+$")
ENTROPY_THRESHOLD = 4.0
# Контексты, где длинная случайная строка — не секрет: хеши, коммиты, отпечатки, URL, импорты.
ENTROPY_CONTEXT_SKIP = re.compile(r"(?i)(sha256|sha1|sha512|blake2|md5|commit|digest|fingerprint|hash|https?://|"
                                  r"base64,|data:|nonce|uuid|import |lockfile|integrity|checksum|etag|signature|sig=|"
                                  r"\.gguf|/models/|\.safetensors|\.bin\b)")
# Слова из естественного языка/кода внутри токена — не случайный секрет.
DICT_HINT = re.compile(r"(?i)(test|fake|example|sample|placeholder|canary|dummy|token|secret|password|key|value|"
                       r"config|default|bossman|claude|openai|anthropic|redacted|xxxx|0000|aaaa)")
ALLOW_MARK = "ci-secret-scan: allow"
MAX_BYTES = 2_000_000
ZIP_MEMBER_SUFFIX = {".py", ".md", ".txt", ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".env", ".sh", ".js", ".ts"}


def shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def entropy_findings(text: str, rel: str) -> list[str]:
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARK in line or ENTROPY_CONTEXT_SKIP.search(line):
            continue
        for m in TOKEN.finditer(line):
            tok = m.group(0)
            body = tok.rstrip("=")
            if HEX.match(body) or DICT_HINT.search(tok) or ENV_NAME.match(tok) or SEQUENTIAL.search(tok) or WORDY.match(tok):
                continue
            if BASE58_PUBKEY.match(body) and not SECRET_CONTEXT.search(line):
                continue
            if "-" in body and HEX.match(body.split("-", 1)[1].replace("-", "") or "x") and len(body.split("-", 1)[0]) <= 12:
                continue                                          # prefix-<hex> (csrf-…, approval-…)
            if not (re.search(r"[A-Za-z]", tok) and re.search(r"[0-9]", tok)):
                continue
            if tok.count("-") + tok.count("_") >= 3 or tok.count("-") + tok.count("_") > len(tok) // 3:
                continue                                          # идентификаторы вида BCC-V2-…-001, snake_case
            h = shannon_entropy(tok)
            if h >= ENTROPY_THRESHOLD:
                out.append(f"{rel}:{i}: high-entropy token (H={h:.2f}, len={len(tok)})")
    return out


def pattern_findings(text: str, rel: str) -> list[str]:
    out = []
    lines = text.splitlines()
    for label, pattern in PATTERNS:
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            src = lines[line - 1] if 0 < line <= len(lines) else ""
            if ALLOW_MARK in src:
                continue
            if label == "private key" and CANARY_BODY.search(src):
                continue                                          # синтетический PEM-заголовок в пробе/тесте
            out.append(f"{rel}:{line}: {label}")
    return out


def scan_text(text: str, rel: str, *, entropy: bool) -> list[str]:
    found = pattern_findings(text, rel)
    if entropy:
        found += entropy_findings(text, rel)
    return found


def scan_zip(path: Path, rel: str) -> list[str]:
    out = []
    try:
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                suffix = Path(info.filename).suffix.lower()
                if info.is_dir() or suffix not in ZIP_MEMBER_SUFFIX or info.file_size > MAX_BYTES:
                    continue
                text = zf.read(info).decode("utf-8", "ignore")
                out += scan_text(text, f"{rel}!{info.filename}", entropy=False)      # в архивах — только паттерны
    except (zipfile.BadZipFile, OSError):
        return out
    return out


def scan_paths(paths: list[Path], root: Path) -> list[str]:
    findings: list[str] = []
    for path in paths:
        rel = path.relative_to(root).as_posix()
        if rel.endswith("tools/ci_secret_scan.py"):
            continue
        if any(rx.search(rel) for rx in FORBIDDEN_FILES):
            findings.append(f"{rel}: forbidden file in repository (.env / private key material)")
            continue
        suffix = path.suffix.lower()
        if suffix in SKIP_SUFFIX:
            continue
        try:
            if path.stat().st_size > MAX_BYTES and suffix not in ZIP_SUFFIX:
                continue
        except OSError:
            continue
        if suffix in ZIP_SUFFIX:
            findings += scan_zip(path, rel)
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        findings += scan_text(text, rel, entropy=suffix in ENTROPY_SUFFIX)
    return findings


def tracked_files() -> list[Path]:
    try:
        raw = subprocess.check_output(["git", "-C", str(ROOT), "ls-files", "-z"], stderr=subprocess.DEVNULL)
        return [ROOT / x for x in raw.decode("utf-8", "replace").split("\0") if x]
    except Exception:
        return [p for p in ROOT.rglob("*") if p.is_file()]


def main() -> int:
    findings = scan_paths([p for p in tracked_files() if p.is_file()], ROOT)
    if findings:
        print("Potential secrets detected:", file=sys.stderr)
        for item in findings:
            print(f"  {item}", file=sys.stderr)
        return 2
    print("Secret-pattern scan: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
