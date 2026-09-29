"""Backup and restore of the canonical memory layers, with a hashed manifest.

What is backed up: canonical Markdown notes, an export of the temporal facts, and the
LearningStore corpus. What is NOT backed up: derived indexes — they are rebuildable and,
per the contract, "never restore an older derived index over newer canonical memory".
BOOT rebuilds them after a restore.

The restore has two modes, and the difference between them is the whole point:

``clean``  the destination is empty (a new machine, a fresh directory). Everything from
           the backup is written and the manifest hashes are verified. This is the
           migration drill: restore -> rebuild indexes -> verify counts -> recall smoke.

``merge``  the destination already holds newer state. The LearningStore journal is
           append-only and authoritative, so only transactions the destination does not
           already have are appended, and later transactions keep winning. A record that
           was retired AFTER the backup was taken therefore stays retired: restoring an
           old backup must not resurrect a deleted record into retrieval. Notes are never
           overwritten in this mode for the same reason.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 1

NOTES_DIR = "notes"
LEARNING_DIR = "learning"
FACTS_FILE = "facts.json"

#: LearningStore files. ``journal.jsonl`` is the authority; the rest are snapshots it
#: rebuilds, kept in the backup only so a clean restore is byte-comparable.
LEARNING_FILES = ("journal.jsonl", "fix_cases.jsonl", "failed_experiments.jsonl", "history.jsonl")


class BackupError(RuntimeError):
    pass


@dataclass(slots=True)
class RestoreReport:
    mode: str
    notes_restored: int
    notes_skipped: int
    learning_txns_applied: int
    learning_txns_skipped: int
    facts_restored: int
    hash_mismatches: list[str]
    #: merge: backup transactions refused because they claim VERIFIED without the
    #: store's identity/evidence invariants (a backup file is not a verifier).
    learning_txns_rejected: int = 0

    def as_dict(self) -> dict:
        return {"mode": self.mode, "notes_restored": self.notes_restored,
                "notes_skipped": self.notes_skipped,
                "learning_txns_applied": self.learning_txns_applied,
                "learning_txns_skipped": self.learning_txns_skipped,
                "learning_txns_rejected": self.learning_txns_rejected,
                "facts_restored": self.facts_restored,
                "hash_mismatches": list(self.hash_mismatches)}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------- backup
def backup(dest: Path | str, *, notes_root: Path | str | None = None,
           learning_dir: Path | str | None = None,
           facts: Iterable[dict] | None = None) -> dict:
    """Copy the canonical layers into ``dest`` and return the manifest (also written there)."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "version": MANIFEST_VERSION,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "files": {}, "counts": {}, "derived_excluded": ["memory index", "code index"],
    }

    if notes_root is not None:
        root = Path(notes_root)
        out = dest / NOTES_DIR
        count = 0
        for src in sorted(root.rglob("*.md")):
            if any(part in {".git", ".obsidian", ".trash", "node_modules"} for part in src.parts):
                continue
            rel = src.relative_to(root)
            target = out / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            manifest["files"][f"{NOTES_DIR}/{rel.as_posix()}"] = sha256_file(target)
            count += 1
        manifest["counts"]["notes"] = count

    if learning_dir is not None:
        src_dir = Path(learning_dir)
        out = dest / LEARNING_DIR
        out.mkdir(parents=True, exist_ok=True)
        txns = 0
        for name in LEARNING_FILES:
            src = src_dir / name
            if not src.is_file():
                continue
            shutil.copy2(src, out / name)
            manifest["files"][f"{LEARNING_DIR}/{name}"] = sha256_file(out / name)
            if name == "journal.jsonl":
                txns = sum(1 for _ in _read_jsonl(src))
        manifest["counts"]["learning_txns"] = txns

    if facts is not None:
        rows = [dict(r) for r in facts]
        target = dest / FACTS_FILE
        target.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        manifest["files"][FACTS_FILE] = sha256_file(target)
        manifest["counts"]["facts"] = len(rows)

    (dest / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    return manifest


def verify(backup_dir: Path | str) -> list[str]:
    """Files whose content no longer matches the manifest (empty list = intact)."""
    backup_dir = Path(backup_dir)
    manifest = _load_manifest(backup_dir)
    bad: list[str] = []
    for rel, digest in (manifest.get("files") or {}).items():
        path = backup_dir / rel
        if not path.is_file() or sha256_file(path) != digest:
            bad.append(rel)
    return bad


# ---------------------------------------------------------------- restore
def restore(backup_dir: Path | str, *, notes_root: Path | str | None = None,
            learning_dir: Path | str | None = None, mode: str = "clean",
            facts_writer=None) -> RestoreReport:
    if mode not in ("clean", "merge"):
        raise BackupError(f"unknown restore mode: {mode}")
    backup_dir = Path(backup_dir)
    manifest = _load_manifest(backup_dir)
    mismatches = verify(backup_dir)

    notes_restored = notes_skipped = 0
    if notes_root is not None and (backup_dir / NOTES_DIR).is_dir():
        root = Path(notes_root)
        if mode == "clean" and root.exists() and any(root.rglob("*.md")):
            raise BackupError(f"clean restore wants an empty notes root, {root} already has notes")
        for src in sorted((backup_dir / NOTES_DIR).rglob("*.md")):
            rel = src.relative_to(backup_dir / NOTES_DIR)
            target = root / rel
            if mode == "merge" and target.exists():
                # Never write an older canonical note over a newer one.
                notes_skipped += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            notes_restored += 1

    applied = skipped = rejected = 0
    if learning_dir is not None and (backup_dir / LEARNING_DIR / "journal.jsonl").is_file():
        applied, skipped, rejected = _restore_learning(backup_dir / LEARNING_DIR, Path(learning_dir), mode)

    facts_restored = 0
    if facts_writer is not None and (backup_dir / FACTS_FILE).is_file():
        rows = json.loads((backup_dir / FACTS_FILE).read_text(encoding="utf-8"))
        for row in rows:
            facts_writer(row)
            facts_restored += 1

    return RestoreReport(mode=mode, notes_restored=notes_restored, notes_skipped=notes_skipped,
                         learning_txns_applied=applied, learning_txns_skipped=skipped,
                         facts_restored=facts_restored, hash_mismatches=mismatches,
                         learning_txns_rejected=rejected)


def _restore_learning(src_dir: Path, dest_dir: Path, mode: str) -> tuple[int, int, int]:
    """The journal is append-only and authoritative, so restore is a journal operation.

    ``clean``: copy the journal, then let the store rebuild its snapshots from it.
    ``merge``: append only the transactions the destination does not already have. The
    destination's LATER transactions therefore keep winning — which is exactly why a
    record retired after the backup was taken does not come back to life.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    src_journal = src_dir / "journal.jsonl"
    dest_journal = dest_dir / "journal.jsonl"
    src_txns = list(_read_jsonl(src_journal))

    if mode == "clean":
        if dest_journal.is_file() and list(_read_jsonl(dest_journal)):
            raise BackupError(f"clean restore wants an empty learning dir, {dest_dir} has a journal")
        shutil.copy2(src_journal, dest_journal)
        for name in LEARNING_FILES:
            if name != "journal.jsonl" and (src_dir / name).is_file():
                shutil.copy2(src_dir / name, dest_dir / name)
        _rebuild_snapshots(dest_dir)
        return len(src_txns), 0, 0

    from .trace import LearningStore
    store = LearningStore(dest_dir, dest_dir / "docs")
    applied = skipped = rejected = 0
    # Under the store's own lock and as ONE atomic rewrite: an unlocked append raced
    # a concurrent add() (its read-rewrite dropped the merged transactions), and a
    # crash mid-append left a torn tail in the authoritative journal.
    with store._locked():
        have = {_txn_identity(t) for t in _read_jsonl(dest_journal)}
        lines: list[str] = []
        for txn in src_txns:
            if _txn_identity(txn) in have:
                skipped += 1
                continue
            if _unverifiable(txn):
                rejected += 1
                continue
            have.add(_txn_identity(txn))
            lines.append(json.dumps(txn, ensure_ascii=False) + "\n")
            applied += 1
        if lines:
            # Existing bytes verbatim (even a corrupt line stays exactly as it was).
            existing = dest_journal.read_bytes() if dest_journal.is_file() else b""
            if existing and not existing.endswith(b"\n"):
                existing += b"\n"
            _atomic_write_bytes(dest_journal, existing + "".join(lines).encode("utf-8"))
    _rebuild_snapshots(dest_dir)
    return applied, skipped, rejected


def _atomic_write_bytes(dest: Path, data: bytes) -> None:
    import os
    import tempfile
    from .trace import replace_with_retry
    fd, tmp = tempfile.mkstemp(prefix=dest.name + ".", suffix=".tmp", dir=str(dest.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        replace_with_retry(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _unverifiable(txn: dict) -> bool:
    """A backup transaction that claims VERIFIED must carry the same typed independent
    verifier and bound evidence the store demands of add(); a backup file is not a
    verifier, and the merge would otherwise make it authoritative unchecked."""
    case = txn.get("case")
    if not isinstance(case, dict):
        return True
    if case.get("learning_status") != "VERIFIED":
        return False
    from .trace import _evidence_record_errors, _identity_errors
    try:
        return bool(_identity_errors(case) or _evidence_record_errors(case))
    except Exception:  # noqa: BLE001 — malformed is unverifiable
        return True


def _rebuild_snapshots(learning_dir: Path) -> None:
    """The derived corpora are rebuilt from the journal, never trusted from the backup."""
    from .trace import LearningStore
    store = LearningStore(learning_dir, learning_dir / "docs")
    store._sync()


def _txn_identity(txn: dict) -> tuple:
    from .trace import _as_int
    case = txn.get("case") or {}
    if not isinstance(case, dict):
        return ("", 0)
    return (str(case.get("case_id") or ""), _as_int(case.get("version"), 0))


def _read_jsonl(path: Path):
    if not Path(path).is_file():
        return
    for raw in Path(path).read_bytes().splitlines():
        try:
            line = raw.decode("utf-8").strip()
            if not line:
                continue
            row = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue                      # a corrupt tail is skipped, never authoritative
        if isinstance(row, dict):
            yield row


def _load_manifest(backup_dir: Path) -> dict:
    path = Path(backup_dir) / MANIFEST_NAME
    if not path.is_file():
        raise BackupError(f"no {MANIFEST_NAME} in {backup_dir}")
    return json.loads(path.read_text(encoding="utf-8"))


__all__ = ["BackupError", "LEARNING_FILES", "MANIFEST_NAME", "RestoreReport", "backup",
           "restore", "sha256_file", "verify"]
