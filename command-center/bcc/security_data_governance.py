"""Data classification, owner-only ACL enforcement, retention/delete/export lifecycle,
and cloud transmission gate for documents and Telegram exports (P2.10).
"""
from __future__ import annotations

from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any

from .auth import _restrict_to_owner
from .plugin_security import redact_text


class DataClassification(str, Enum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL_OWNER = "CONFIDENTIAL_OWNER"
    RESTRICTED_TELEGRAM_EXPORT = "RESTRICTED_TELEGRAM_EXPORT"
    RESTRICTED_DOCUMENT = "RESTRICTED_DOCUMENT"


_TELEGRAM_EXPORT_NAMES = frozenset({"result.json", "messages.html", "telegram_export.json", "telegram_export.jsonl"})
_TELEGRAM_CONTENT_RE = re.compile(
    r'(?i)(?:"type"\s*:\s*"(?:personal_chat|private_group|private_supergroup|saved_messages)"|'
    r'"from_id"\s*:\s*"user\d+"|class="history"|telegram[_\s-]export|CLASSIFIED:RESTRICTED_TELEGRAM_EXPORT)'
)
_DOCUMENT_EXTS = frozenset({".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".rtf", ".odt"})
_DOCUMENT_CONTENT_RE = re.compile(
    r"(?i)(?:\[CLASSIFIED:RESTRICTED(?:_DOCUMENT)?\]|ДОГОВОР\s+ПОСТАВКИ|ГОСУДАРСТВЕННЫЙ\s+КОНТРАКТ|_extracted\.txt)"
)


def classify_text(text: str) -> DataClassification:
    """Classify text content to prevent accidental cloud exfiltration of Telegram dumps or owner documents."""
    raw = str(text or "")
    if _TELEGRAM_CONTENT_RE.search(raw):
        return DataClassification.RESTRICTED_TELEGRAM_EXPORT
    if _DOCUMENT_CONTENT_RE.search(raw):
        return DataClassification.RESTRICTED_DOCUMENT
    return DataClassification.INTERNAL


def classify_artifact(path: Path) -> DataClassification:
    """Classify a file on disk (Telegram export, document extraction, or general owner artifact)."""
    p = Path(path)
    name_low = p.name.lower()
    if name_low in _TELEGRAM_EXPORT_NAMES or "telegram" in str(p).lower():
        return DataClassification.RESTRICTED_TELEGRAM_EXPORT
    if name_low.endswith("_extracted.txt") or p.suffix.lower() in _DOCUMENT_EXTS:
        return DataClassification.RESTRICTED_DOCUMENT
    if p.is_file():
        try:
            head = p.read_text(encoding="utf-8", errors="ignore")[:8192]
            cls = classify_text(head)
            if cls in (DataClassification.RESTRICTED_TELEGRAM_EXPORT, DataClassification.RESTRICTED_DOCUMENT):
                return cls
        except OSError:
            pass
    return DataClassification.CONFIDENTIAL_OWNER


def _registry_path(data_dir: Path) -> Path:
    return Path(data_dir) / "security" / "classified-artifacts.json"


def _load_registry(data_dir: Path) -> dict[str, Any]:
    path = _registry_path(data_dir)
    if not path.is_file():
        return {"version": 1, "items": {}}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(doc, dict) and isinstance(doc.get("items"), dict):
            return doc
    except (OSError, ValueError):
        pass
    return {"version": 1, "items": {}}


def _save_registry(data_dir: Path, doc: dict[str, Any]) -> None:
    path = _registry_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    _restrict_to_owner(path.parent)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    _restrict_to_owner(tmp)
    tmp.replace(path)
    _restrict_to_owner(path)


def register_classified_artifact(
    data_dir: Path,
    artifact_path: Path,
    *,
    retention_days: int = 30,
    classification: DataClassification | None = None,
) -> dict[str, Any]:
    """Apply owner-only ACL to artifact_path and register its classification and retention expiry."""
    target = Path(artifact_path).resolve()
    if target.exists():
        _restrict_to_owner(target)
    cls = classification or classify_artifact(target)
    now = time.time()
    expires_at = now + max(0, int(retention_days)) * 86400
    sha256 = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else ""
    entry = {
        "path": str(target),
        "classification": cls.value,
        "created_at": now,
        "expires_at": expires_at,
        "retention_days": int(retention_days),
        "sha256": sha256,
        "cloud_export_allowed": False,
        "owner_acl_applied": True,
    }
    reg = _load_registry(data_dir)
    reg["items"][str(target)] = entry
    _save_registry(data_dir, reg)
    return entry


def purge_expired_classified_files(data_dir: Path, *, now_ts: float | None = None) -> dict[str, Any]:
    """Delete classified artifacts whose retention period has expired."""
    now = float(now_ts if now_ts is not None else time.time())
    reg = _load_registry(data_dir)
    items = reg.get("items", {})
    deleted: list[str] = []
    remaining: dict[str, Any] = {}
    for path_str, meta in list(items.items()):
        exp = float(meta.get("expires_at", now + 1))
        if exp <= now:
            p = Path(path_str)
            try:
                if p.is_file():
                    p.unlink()
            except OSError:
                pass
            deleted.append(path_str)
        else:
            remaining[path_str] = meta
    reg["items"] = remaining
    _save_registry(data_dir, reg)
    return {"deleted": deleted, "remaining_count": len(remaining)}


def delete_classified_artifact(data_dir: Path, artifact_path: Path) -> bool:
    """Explicitly delete a classified artifact and remove it from the registry."""
    target = str(Path(artifact_path).resolve())
    reg = _load_registry(data_dir)
    existed = reg["items"].pop(target, None) is not None
    p = Path(target)
    if p.is_file():
        p.unlink(missing_ok=True)
        existed = True
    _save_registry(data_dir, reg)
    return existed


def export_sanitized_bundle(data_dir: Path, artifact_path: Path) -> dict[str, Any]:
    """Export a sanitized/redacted metadata + content summary with owner-only ACL."""
    target = Path(artifact_path).resolve()
    cls = classify_artifact(target)
    raw_text = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
    scrubbed = redact_text(raw_text)
    scrubbed = re.sub(r'"from_id"\s*:\s*"[^"]+"', '"from_id": "[REDACTED]"', scrubbed)
    export_dir = Path(data_dir) / "security" / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    _restrict_to_owner(export_dir)
    out_file = export_dir / f"{target.stem}.sanitized.json"
    payload = {
        "source_name": target.name,
        "classification": cls.value,
        "redacted": True,
        "exported_at": int(time.time()),
        "sanitized_preview": scrubbed[:4000],
    }
    out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _restrict_to_owner(out_file)
    return {**payload, "export_path": str(out_file)}
