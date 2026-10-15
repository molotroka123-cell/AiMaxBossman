"""Политика Argon PC-Agent: белый список Bossman API (без БД и сети)."""
from __future__ import annotations

import json
import posixpath
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .pc_agent_policy_consts import PolicyVerdict
from .pc_agent_policy_paths import (
    default_safe_roots,
    is_t3_secret_or_protected_path,
    is_within_safe_roots,
)


def normalize_bossman_api_path(raw_path: str) -> tuple[str, bool]:
    raw = str(raw_path or "").strip()
    if not raw:
        return "", True
    cur = raw.split("?", 1)[0].split("#", 1)[0]
    suspicious = bool(re.search(r"(?i)(?:%2e|%2f|%5c|%00|\.\.|\\|//)", raw))
    for _ in range(3):
        if unquote(cur) == cur:
            break
        cur = unquote(cur)
    cur = cur.replace("\\", "/")
    suspicious = suspicious or (".." in cur.split("/")) or ("\x00" in cur)
    norm = posixpath.normpath(re.sub(r"/+", "/", cur)).lower()
    return (norm if norm.startswith("/") else "/" + norm), suspicious


_API_FORBIDDEN = (
    "/api/approvals /api/autonomy /api/control-plane /api/providers /api/secrets /api/auth "
    "/api/pc-agent/trust /api/pc-agent/untrust /api/pc-agent/stop /api/pc-agent/resume /approve /rollback"
).split()
_API_T0_PATHS = {"/api/system", "/api/models", "/api/agents", "/api/status", "/api/health/live"}
_API_PATH_TOKEN_RE = r"[A-Za-z]:[\\/][^\"\'\s,;}{]+|\\\\[^\"\'\s,;}{]+|\.[\\/][^\"\'\s,;}{]+"


def _classify_motion_preview(m: str, norm_path: str, body: Any, data_dir: Path | None) -> PolicyVerdict:
    if body is not None:
        for m_tok in re.findall(_API_PATH_TOKEN_RE, json.dumps(body, ensure_ascii=False, default=str)):
            bad_b, why_b = is_t3_secret_or_protected_path(m_tok, for_write=False, data_dir=data_dir)
            if bad_b:
                return PolicyVerdict("T3", "deny", "bossman_api_secret_path", why_b, True)
            if not is_within_safe_roots(m_tok, default_safe_roots(data_dir)):
                return PolicyVerdict("T2", "ask", "bossman_api_motion_unsafe_path", f"Путь {m_tok} (T2).", True)
    return PolicyVerdict("T1", "auto", "bossman_api_motion", f"Вызов {m} {norm_path} (T1).")


def classify_bossman_api(
    method: str, raw_path: str, body: Any = None, data_dir: Path | None = None
) -> PolicyVerdict:
    m = str(method or "GET").strip().upper()
    norm_path, suspicious = normalize_bossman_api_path(raw_path)
    if suspicious or not norm_path.startswith("/api/") or any(f in norm_path for f in _API_FORBIDDEN):
        return PolicyVerdict(
            "T3",
            "deny",
            "bossman_api_forbidden",
            f"Вызов {m} {raw_path} запрещён (T3).",
            True,
        )
    if m == "GET" and (norm_path in _API_T0_PATHS or norm_path.startswith("/api/motion-")):
        return PolicyVerdict("T0", "auto", "bossman_api_read", f"Чтение Bossman API {norm_path} (T0).")
    if m == "POST" and norm_path in {"/api/motion-studio/preview"}:
        return _classify_motion_preview(m, norm_path, body, data_dir)
    reason = f"Вызов {m} {norm_path} вне белого списка (T2)."
    return PolicyVerdict("T2", "ask", "bossman_api_unlisted", reason, True)


def is_bossman_api_token_allowed(
    method: str, raw_path: str, body: Any = None, data_dir: Path | None = None
) -> bool:
    v = classify_bossman_api(method, raw_path, body=body, data_dir=data_dir)
    return v.tier in {"T0", "T1"} and v.decision == "auto"
