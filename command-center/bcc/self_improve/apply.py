"""Apply / rollback of an approved draft. REFUSES without a valid owner approval token.

* the target must lie inside an ``apply_root`` that the OWNER passes in (there is no default: no root
  means BLOCKED, so nothing is ever applied "somewhere by accident");
* protected files (constitution, limits, long_run.json, permission gates, settings, this package)
  are refused even with a token - those are human edits;
* the previous content of the target is saved with its hash BEFORE writing; ``rollback`` restores it
  and refuses if the file changed after the apply (so a later human edit is never overwritten);
* every step is a journal entry that says how to undo it.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from ..autonomy.journal import atomic_write_bytes
from .approval import ApprovalRequired, verify_and_consume
from .drafts import DraftStore, sha256_text
from .limits import BLOCKED, LimitViolation, Limits, guard_write_target
from .store import Store


def _sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "ABSENT"


def apply_draft(root: str | os.PathLike, *, hypothesis_id: str, draft_id: str, token: str | None,
                apply_root: str | os.PathLike | None, limits: Limits | None = None, actor: str = "unknown") -> dict:
    store = Store(root)
    drafts = DraftStore(root, limits or Limits())
    draft, text = drafts.load(draft_id)
    if apply_root is None:
        store.record("refusal", {"action": "apply", "hypothesis_id": hypothesis_id, "draft_id": draft_id,
                                 "actor": actor, "reason": "no apply_root configured by the owner", "status": BLOCKED})
        raise LimitViolation("BLOCKED: no apply_root configured by the owner")
    base = Path(apply_root).resolve()
    target = (base / draft.target_rel).resolve()
    if target != base and base not in target.parents:
        raise LimitViolation("target escapes the apply root")
    try:
        guard_write_target(target)
    except LimitViolation as exc:
        store.record("refusal", {"action": "apply", "hypothesis_id": hypothesis_id, "draft_id": draft_id,
                                 "actor": actor, "reason": str(exc), "status": BLOCKED})
        raise
    # approval is checked AFTER the cheap structural refusals and BEFORE any file is touched
    verify_and_consume(store, root, token, hypothesis_id=hypothesis_id, draft_id=draft_id,
                       draft_sha256=draft.proposed_sha256)
    before = _sha_file(target)
    if draft.base_sha256 != "N/A" and before != draft.base_sha256:
        store.record("refusal", {"action": "apply", "hypothesis_id": hypothesis_id, "draft_id": draft_id,
                                 "reason": "target changed since the draft was made", "status": BLOCKED})
        raise LimitViolation("BLOCKED: target changed since the draft was made")
    apply_id = f"{hypothesis_id}-{draft.draft_id}-v{draft.version}"
    backup_dir = Path(root) / "rollback" / apply_id
    backup_dir.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.copyfile(target, backup_dir / "before.bin")
    atomic_write_bytes(target, text.encode("utf-8"))
    after = _sha_file(target)
    entry = {"hypothesis_id": hypothesis_id, "apply_id": apply_id, "draft_id": draft_id, "version": draft.version,
             "target": str(target), "before_sha256": before, "after_sha256": after, "backup": str(backup_dir),
             "actor": actor,
             "rollback": f"python -m bcc.self_improve rollback --root {root} --apply-id {apply_id} "
                         f"--apply-root {base}"}
    (backup_dir / "apply.json").write_text(json.dumps(entry, indent=1), encoding="utf-8")
    store.record("apply", entry)
    return entry


def rollback(root: str | os.PathLike, *, apply_id: str, apply_root: str | os.PathLike, actor: str = "unknown") -> dict:
    store = Store(root)
    backup_dir = Path(root) / "rollback" / apply_id
    info = json.loads((backup_dir / "apply.json").read_text(encoding="utf-8"))
    base = Path(apply_root).resolve()
    target = Path(info["target"]).resolve()
    if base not in target.parents:
        raise LimitViolation("target is outside the apply root")
    guard_write_target(target)
    if _sha_file(target) != info["after_sha256"]:
        store.record("refusal", {"action": "rollback", "apply_id": apply_id, "status": BLOCKED,
                                 "reason": "target changed after apply; not overwriting a later edit"})
        raise LimitViolation("BLOCKED: target changed after apply")
    if info["before_sha256"] == "ABSENT":
        target.unlink(missing_ok=True)
    else:
        atomic_write_bytes(target, (backup_dir / "before.bin").read_bytes())
    restored = _sha_file(target)
    out = {"hypothesis_id": info["hypothesis_id"], "apply_id": apply_id, "restored_sha256": restored,
           "matches_before": restored == info["before_sha256"], "actor": actor}
    store.record("rollback", out)
    return out


__all__ = ["ApprovalRequired", "apply_draft", "rollback", "sha256_text"]
