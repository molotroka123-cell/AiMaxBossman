"""Политика Argon PC-Agent: пути, секреты, безопасные корни."""
from __future__ import annotations

import contextlib
import os
import re
from pathlib import Path

from ..autonomy.policy import is_protected_path
from ..plugin_security import redact_text
from .pc_agent_policy_consts import (  # noqa: F401  (ре-экспорт публичных имён)
    HARD_INVARIANTS,
    PROTECTED_CMDLINE_TOKENS,
    PROTECTED_PROCESS_NAMES,
    PROTECTED_SERVICE_NAMES,
    PolicyVerdict,
    _CLOUD_PERSONAL_MEDIA_RE,
    _CSAM_RE,
    _MUTATION_VERBS,
    _NCII_RE,
    _PROMPT_INJECTION_RE,
    _PROTECTED_BASENAMES,
    _PROTECTED_REPO_MARKERS,
    _READONLY_VERBS,
    _SENSITIVE_ENV_RE,
    _T3_SECRET_PATTERNS,
    _UNSAFE_WRITE_SUFFIXES,
)


def _collect_runtime_secrets(data_dir: Path | None = None) -> tuple[str, ...]:
    vals: list[str] = []
    for fname in (("token", "api-token") if data_dir is not None else ()):
        p = Path(data_dir) / fname  # type: ignore[arg-type]
        with contextlib.suppress(OSError):
            if p.is_file() and len(tok := p.read_text(encoding="utf-8", errors="replace").strip()) >= 6:
                if tok not in vals:
                    vals.append(tok)
    return tuple(vals)


def scrub_secrets(text: str, extra_secrets: tuple[str, ...] = ()) -> str:
    from bossman.obs import redact

    out = str(text or "")
    for val in extra_secrets:
        if val and len(val) >= 6:
            out = out.replace(val, "[REDACTED]")
    out = re.sub(r"(?<!\d)\d{6,16}:[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])", "[REDACTED]", out)
    out = re.sub(r"\bsk-[A-Za-z0-9_-]{16,}\b", "[REDACTED]", out)
    out = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]{12,}", r"\1[REDACTED]", out)
    out = re.sub(
        r"(?i)\b(bot_token|core_token|cloud_token|local_token|api_key|vault_key|password|secret)"
        r"(\s*[=:]\s*['\"]?)([^'\"\s,;}{]+)",
        r"\1\2[REDACTED]",
        out,
    )
    return redact_text(redact(out))


def check_hard_content_invariants(text: str) -> PolicyVerdict | None:
    raw = str(text or "")
    if _CSAM_RE.search(raw):
        return PolicyVerdict("T3", "deny", HARD_INVARIANTS[0], "Запрещён контент CSAM.", True)
    if _NCII_RE.search(raw):
        return PolicyVerdict("T3", "deny", HARD_INVARIANTS[1], "Запрещён NCII (T3).", True)
    if _CLOUD_PERSONAL_MEDIA_RE.search(raw):
        return PolicyVerdict("T3", "deny", HARD_INVARIANTS[2], "T3.", True)
    return None


def _norm_path(path_str: str) -> str:
    s = os.path.expandvars(str(path_str or "").strip()).replace("\\", "/")
    if not s:
        return ""
    s = s[4:] if s.startswith("//?/") else s
    has_drive = len(s) >= 2 and s[0].isalpha() and s[1] == ":"
    drive, rest = (s[:2], s[2:]) if has_drive else ("", s)
    rest = rest.split(":", 1)[0] if ":" in rest else rest
    segments = (seg.rstrip(". ") if seg not in {".", ".."} else seg for seg in rest.split("/"))
    return drive + "/".join(segments)


def _has_ntfs_ads(path_str: str) -> bool:
    s = str(path_str or "").strip().replace("\\", "/")
    s = s[4:] if s.startswith("//?/") else s
    has_drive = len(s) >= 2 and s[0].isalpha() and s[1] == ":"
    return ":" in (s[2:] if has_drive else s)


def _is_unc(p: str) -> bool:
    s = str(p or "").strip().replace("\\", "/")
    return s.startswith("//") and not s.startswith("//?/")


def _is_system32_bin(p: str) -> bool:
    with contextlib.suppress(OSError, ValueError):
        parent = Path(_norm_path(p)).expanduser().resolve().parent
        return parent in {Path("C:/Windows/System32").resolve(), Path("C:/Windows").resolve()}
    return False


def is_t3_secret_or_protected_path(
    path_str: str | Path,
    *,
    for_write: bool = False,
    repo_root: Path | None = None,
    data_dir: Path | None = None,
) -> tuple[bool, str]:
    raw = str(path_str or "").strip()
    if _is_unc(raw) or _is_unc(os.path.expandvars(raw)):
        return True, f"UNC-путь ({raw}) запрещён (T3)"
    if for_write and _has_ntfs_ads(raw):
        return True, f"запись в NTFS ADS ({raw}) запрещена (T3)"
    norm = _norm_path(raw)
    if not norm:
        return True, "пустой путь"
    if re.search(r"%[A-Za-z_][A-Za-z0-9_]*%", norm) or re.search(r"\$env:[A-Za-z_]", norm, re.I):
        return True, (
            f"нераскрытая переменная окружения в пути ({norm}) "
            "запрещена (T3)"
        )
    try:
        resolved_norm = _norm_path(str(Path(norm).expanduser().resolve()))
    except OSError:
        resolved_norm = norm
    for check_s in (norm, raw.replace("\\", "/"), resolved_norm):
        if any(pat.search(check_s) for pat in _T3_SECRET_PATTERNS):
            name = Path(norm).name
            return True, (
                f"доступ к защищённому секрету/пину ({name}) "
                "запрещён (T3)"
            )
    for c_git in (norm.lower(), resolved_norm.lower()):
        git_sensitive = "/.git/hooks" in c_git or c_git.endswith("/.git/config")
        if git_sensitive or c_git in {".git/hooks", ".git/config"}:
            return True, (
                f"доступ к чувствительному контуру Git ({norm}) "
                "запрещён (T3)"
            )
    candidates = [norm, resolved_norm]
    for r_root in [r for r in (repo_root, Path(__file__).resolve().parents[3]) if r is not None]:
        with contextlib.suppress(Exception):
            rel = Path(resolved_norm).relative_to(r_root.resolve())
            candidates.append(str(rel).replace("\\", "/"))
    for cand in candidates:
        low_c = cand.lower()
        protected_name = any(b in Path(low_c).name for b in _PROTECTED_BASENAMES)
        if protected_name and (for_write or "secrets.py" in low_c):
            return True, f"путь {cand} защищён правилами гейта (T3)"
        for marker in _PROTECTED_REPO_MARKERS:
            m = marker.rstrip("/")
            marker_hit = low_c == m or low_c.endswith("/" + m) or marker in low_c
            if marker_hit and (for_write or "constitution" in low_c or "secrets.py" in low_c):
                return True, (
                    f"путь {cand} защищён Конституцией v2 / "
                    "правилами гейта (T3)"
                )
        if for_write and is_protected_path(cand):
            return True, f"запись в {cand} запрещена (T3)"
    return False, ""


def default_safe_roots(data_dir: Path | None = None) -> list[Path]:
    h = Path.home()
    tmp = Path(os.environ.get("TEMP", str(h / "AppData/Local/Temp")))
    roots = [h / "Desktop", h / "Documents", h / "Downloads", tmp]
    if data_dir is not None:
        roots.append(Path(data_dir) / "pc-agent" / "workspace")
    return [r.resolve() for r in roots]


def _is_safe_root_itself(path_str: str, safe_roots: list[Path]) -> bool:
    with contextlib.suppress(OSError, ValueError):
        return Path(_norm_path(path_str)).expanduser().resolve() in {r.resolve() for r in safe_roots}
    return False


def is_within_safe_roots(path_str: str, safe_roots: list[Path] | None = None) -> bool:
    norm = _norm_path(path_str)
    if not norm or _has_ntfs_ads(path_str) or _is_unc(path_str):
        return False
    try:
        raw_path = Path(norm).expanduser()
        if raw_path.exists() and raw_path.is_symlink():
            return False
        target = raw_path.resolve()
    except OSError:
        return False
    t_low = str(target).replace("\\", "/").lower()
    suffix = Path(Path(norm).name.rstrip(". ").lower()).suffix.lower()
    unsafe_location = "/.git/" in t_low or t_low.endswith("/.git") or "start menu/programs/startup" in t_low
    if unsafe_location or suffix in _UNSAFE_WRITE_SUFFIXES:
        return False
    for root in safe_roots if safe_roots is not None else default_safe_roots():
        with contextlib.suppress(ValueError, OSError):
            if target != root.resolve() and target.is_relative_to(root.resolve()):
                return True
    return False


def PromptIntentReadOnly(task_prompt: str) -> bool:
    text = str(task_prompt or "").strip().lower()
    has_mutation = any(v in text for v in _MUTATION_VERBS)
    return bool(text) and not has_mutation and any(v in text for v in _READONLY_VERBS)
