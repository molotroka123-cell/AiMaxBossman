"""AuditJournal v2: High-performance append-only audit log with O(1) head lookup,
msvcrt file locking, fsync durability, optional HMAC-SHA256 tamper-evident seals,
and tail corruption detection.
"""
from __future__ import annotations

import contextlib
import datetime
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from .features.pc_agent_policy_paths import scrub_secrets

PROFILE_NAME = "Argon"


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


class AuditJournal:
    """Append-only журнал действий (`audit.jsonl`) с цепочкой SHA-256,
    O(1) якорем головы (`.head`), блокировкой msvcrt, fsync и очисткой секретов."""

    def __init__(self, path: Path | str, *, hmac_key: str | bytes | None = None):
        self.path = Path(path)
        self.anchor_path = self.path.with_name(self.path.name + ".head")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._hmac_key: bytes | None = (
            hmac_key.encode("utf-8") if isinstance(hmac_key, str) else hmac_key
        )
        self._head_seq: int = 0
        self._head_hash: str = ""
        self._tampered: bool = False
        self._load_anchor()

    def _load_anchor(self) -> None:
        if self.anchor_path.is_file():
            with contextlib.suppress(Exception):
                anc = json.loads(self.anchor_path.read_text(encoding="utf-8"))
                self._head_seq = int(anc.get("count") or anc.get("seq") or 0)
                self._head_hash = str(anc.get("entry_hash") or "")
                self._tampered = bool(anc.get("tampered", False))

    def append(
        self,
        *,
        actor: str,
        task_id: int | None,
        run_id: int | None,
        tool: str,
        args: Any,
        tier: str,
        decision: str,
        result: Any,
        approval_id: int | None = None,
        verified: bool | None = None,
        extra_secrets: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Append a record in O(1) time using .head anchor when available and verified."""
        prev_hash = "0" * 64
        seq = 1

        if self.path.is_file():
            file_size = 0
            with contextlib.suppress(OSError):
                file_size = self.path.stat().st_size

            if file_size == 0:
                prev_hash = "0" * 64
                seq = 1
            elif not self._tampered and self.anchor_path.is_file() and self._head_seq > 0 and self._head_hash:
                # O(1) tail read: seek to last block instead of reading entire file
                try:
                    with self.path.open("rb") as f:
                        f.seek(max(0, file_size - 4096))
                        tail_chunk = f.read()
                    tail_lines = [ln.strip() for ln in tail_chunk.splitlines() if ln.strip()]
                    if tail_lines:
                        last_row = json.loads(tail_lines[-1].decode("utf-8", errors="replace"))
                        last_hash = str(last_row.get("entry_hash") or "")
                        last_seq = int(last_row.get("seq") or 0)
                        if last_hash == self._head_hash and last_seq == self._head_seq:
                            prev_hash = self._head_hash
                            seq = self._head_seq + 1
                        else:
                            self._tampered = True
                            prev_hash = last_hash or "0" * 64
                            seq = (last_seq or self._head_seq) + 1
                    else:
                        self._tampered = True
                except Exception:
                    self._tampered = True
                    prev_hash = self._head_hash or "0" * 64
                    seq = self._head_seq + 1
            else:
                # Fallback full read if tampered or missing anchor
                try:
                    lines = [ln for ln in self.path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
                    if lines:
                        last = json.loads(lines[-1])
                        prev_hash = str(last.get("entry_hash") or prev_hash)
                        seq = int(last.get("seq") or len(lines)) + 1
                except Exception:
                    self._tampered = True

        # Check for truncation/tampering vs anchor
        if self.anchor_path.is_file():
            try:
                anc = json.loads(self.anchor_path.read_text(encoding="utf-8"))
                anc_count = int(anc.get("count") or anc.get("seq") or 0)
                anc_hash = str(anc.get("entry_hash") or "")
                if anc.get("tampered") or (anc_count > 0 and seq <= anc_count and prev_hash != anc_hash):
                    self._tampered = True
            except Exception:
                self._tampered = True

        safe_args = json.loads(scrub_secrets(json.dumps(args, ensure_ascii=False, default=str), extra_secrets))
        safe_result = scrub_secrets(
            json.dumps(result, ensure_ascii=False, default=str) if not isinstance(result, str) else result,
            extra_secrets,
        )[:2000]

        record = {
            "seq": seq,
            "ts": time.time(),
            "iso_ts": _utcnow().isoformat(),
            "profile": PROFILE_NAME,
            "actor": str(actor or "owner"),
            "task_id": task_id,
            "run_id": run_id,
            "tool": str(tool),
            "tier": str(tier),
            "decision": str(decision),
            "approval_id": approval_id,
            "verified": verified,
            "args": safe_args,
            "result": safe_result,
            "prev_hash": prev_hash,
        }
        digest_source = json.dumps(record, sort_keys=True, ensure_ascii=False).encode("utf-8")
        record["entry_hash"] = hashlib.sha256(digest_source).hexdigest()
        if self._hmac_key:
            record["hmac_sha256"] = hmac.new(self._hmac_key, digest_source, hashlib.sha256).hexdigest()

        line_str = json.dumps(record, ensure_ascii=False) + "\n"
        line_bytes = line_str.encode("utf-8")

        # Atomic append with msvcrt locking and fsync
        with self.path.open("a+b") as fh:
            locked = False
            if sys.platform == "win32":
                try:
                    import msvcrt
                    # Lock 1 byte from current file position
                    fh.seek(0, os.SEEK_END)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    locked = True
                except Exception:
                    locked = False
            try:
                fh.seek(0, os.SEEK_END)
                fh.write(line_bytes)
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                if locked and sys.platform == "win32":
                    with contextlib.suppress(Exception):
                        import msvcrt
                        fh.seek(0, os.SEEK_END)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)

        if not self._tampered:
            self._head_seq = seq
            self._head_hash = record["entry_hash"]
            with contextlib.suppress(OSError):
                tmp_anchor = self.anchor_path.with_name(f".{self.anchor_path.name}.{uuid.uuid4().hex[:8]}.tmp")
                tmp_anchor.write_text(
                    json.dumps({"seq": seq, "count": seq, "entry_hash": record["entry_hash"]}, ensure_ascii=False),
                    encoding="utf-8",
                )
                tmp_anchor.replace(self.anchor_path)
        return record

    def verify_chain(self) -> dict[str, Any]:
        """Verify the integrity of the audit hash chain, anchor, and check for tail corruption."""
        if not self.path.is_file():
            if self.anchor_path.is_file() or self._head_seq > 0 or self._tampered:
                return {"ok": False, "count": 0, "broken_seq": 1, "reason": "audit_file_missing_with_head_anchor"}
            return {"ok": True, "count": 0}

        try:
            content = self.path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return {"ok": False, "count": 0, "broken_seq": 1, "reason": "corrupted_tail_detected"}

        raw_lines = content.splitlines()
        lines = [ln for ln in raw_lines if ln.strip()]
        prev_hash = "0" * 64

        for idx, ln in enumerate(lines, 1):
            try:
                row = json.loads(ln)
            except Exception:
                # Any malformed JSON line (especially partial write at tail)
                return {
                    "ok": False,
                    "count": idx - 1,
                    "broken_seq": idx,
                    "reason": "corrupted_tail_detected",
                }

            entry_hash = row.pop("entry_hash", "")
            row_hmac = row.pop("hmac_sha256", None)

            if int(row.get("seq") or idx) != idx:
                return {"ok": False, "count": idx, "broken_seq": row.get("seq"), "reason": "seq_mismatch"}
            if row.get("prev_hash") != prev_hash:
                return {"ok": False, "count": idx, "broken_seq": row.get("seq"), "reason": "prev_hash_mismatch"}

            digest_source = json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8")
            expected = hashlib.sha256(digest_source).hexdigest()
            if entry_hash != expected:
                return {"ok": False, "count": idx, "broken_seq": row.get("seq"), "reason": "entry_hash_mismatch"}

            if self._hmac_key and row_hmac:
                expected_hmac = hmac.new(self._hmac_key, digest_source, hashlib.sha256).hexdigest()
                if not hmac.compare_digest(row_hmac, expected_hmac):
                    return {"ok": False, "count": idx, "broken_seq": row.get("seq"), "reason": "hmac_signature_mismatch"}

            prev_hash = entry_hash

        if self._tampered:
            return {
                "ok": False,
                "count": len(lines),
                "broken_seq": self._head_seq or len(lines),
                "reason": "tail_truncated_or_head_mismatch",
            }

        if self.anchor_path.is_file():
            try:
                anchor = json.loads(self.anchor_path.read_text(encoding="utf-8"))
                expected_count = int(anchor.get("count") or anchor.get("seq") or 0)
                expected_head = str(anchor.get("entry_hash") or "")
                if anchor.get("tampered") or len(lines) != expected_count or prev_hash != expected_head:
                    return {
                        "ok": False,
                        "count": len(lines),
                        "broken_seq": expected_count,
                        "reason": "tail_truncated_or_head_mismatch",
                    }
            except Exception:
                return {"ok": False, "count": len(lines), "broken_seq": len(lines), "reason": "invalid_head_anchor"}
        elif self._head_seq > 0 and (len(lines) != self._head_seq or prev_hash != self._head_hash):
            return {
                "ok": False,
                "count": len(lines),
                "broken_seq": self._head_seq,
                "reason": "tail_truncated_or_head_mismatch",
            }

        return {"ok": True, "count": len(lines)}

    def read_recent(self, limit: int = 50, *, task_id: int | None = None) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            with contextlib.suppress(Exception):
                item = json.loads(line)
                if task_id is None or item.get("task_id") == task_id:
                    rows.append(item)
        return rows[-max(1, min(limit, 500)):]

    def read_receipts(self, limit: int = 50, *, task_id: int | None = None) -> list[dict[str, Any]]:
        """Read-view квитанций поверх единого журнала `audit.jsonl` без дублирования файла."""
        return self.read_recent(limit=limit, task_id=task_id)
