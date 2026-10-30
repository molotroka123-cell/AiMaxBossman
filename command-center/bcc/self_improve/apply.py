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


def invalidate_stale_evaluations(
    root: str | os.PathLike,
    *,
    target_rel: str,
    new_sha256: str,
    hypothesis_id: str = "",
    reason: str = "agent_modified",
) -> dict:
    """Сбрасывает старые оценки после изменения агента (arXiv:2610.01073 Safety Must Survive Self-Improvement).

    Любые прежние закэшированные оценки (baseline.frozen / result) для более старого sha256
    помечаются в журнале как недействительные, чтобы устаревшая оценка никогда не оставляла
    активной непроверенную сборку.
    """
    store = Store(root)
    stale_ids: list[str] = []
    for e in store.journal.entries():
        k = str(e.get("kind") or "")
        p = e.get("payload") if isinstance(e.get("payload"), dict) else {}
        if k in ("self_improve.baseline.frozen", "self_improve.result"):
            hid = str(p.get("hypothesis_id") or "")
            old_sha = str(p.get("instruction_sha256") or p.get("draft_sha256") or "")
            if old_sha != new_sha256 and hid:
                stale_ids.append(hid)
    rec = {
        "target_rel": target_rel,
        "new_sha256": new_sha256,
        "hypothesis_id": hypothesis_id,
        "invalidated_hypotheses": sorted(set(stale_ids)),
        "invalidated_count": len(set(stale_ids)),
        "reason": reason,
    }
    store.record("scores.invalidated", rec)
    return rec


def apply_and_verify_or_rollback(
    root: str | os.PathLike,
    *,
    hypothesis_id: str,
    draft_id: str,
    token: str | None,
    apply_root: str | os.PathLike | None,
    critical_checks: list[tuple[str, object]],
    limits: Limits | None = None,
    actor: str = "unknown",
) -> dict:
    """Применяет изменение, сбрасывает старые оценки и запускает полный набор критичных
    (включая составные) проверок. Если хотя бы одна проверка падает — немедленно откатывает
    к последней валидированной сборке, проверяет прохождение тестов после отката и пишет
    причины в журнал (arXiv:2610.01073).
    """
    entry = apply_draft(
        root,
        hypothesis_id=hypothesis_id,
        draft_id=draft_id,
        token=token,
        apply_root=apply_root,
        limits=limits,
        actor=actor,
    )
    target_path = Path(entry["target"])
    candidate_text = target_path.read_text(encoding="utf-8")
    inv = invalidate_stale_evaluations(
        root,
        target_rel=str(target_path.name),
        new_sha256=entry["after_sha256"],
        hypothesis_id=hypothesis_id,
    )
    failed_checks: list[dict] = []
    passed_checks: list[str] = []
    for check_name, check_fn in critical_checks:
        ok = False
        detail = ""
        try:
            res = check_fn(candidate_text) if callable(check_fn) else False
            if isinstance(res, tuple):
                ok, detail = bool(res[0]), str(res[1])
            else:
                ok, detail = bool(res), ("ok" if res else "check returned False")
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"exception: {type(exc).__name__}: {exc}"
        if ok:
            passed_checks.append(check_name)
        else:
            failed_checks.append({"check": check_name, "reason": detail})

    store = Store(root)
    if failed_checks:
        rb = rollback(root, apply_id=entry["apply_id"], apply_root=apply_root, actor="safety-gate")
        restored_text = target_path.read_text(encoding="utf-8") if target_path.exists() else ""
        post_rb_passed: list[str] = []
        post_rb_failed: list[str] = []
        for check_name, check_fn in critical_checks:
            res = check_fn(restored_text) if callable(check_fn) else False
            ok = bool(res[0]) if isinstance(res, tuple) else bool(res)
            (post_rb_passed if ok else post_rb_failed).append(check_name)
        reason_entry = {
            "hypothesis_id": hypothesis_id,
            "apply_id": entry["apply_id"],
            "status": "ROLLED_BACK",
            "rejected_sha256": entry["after_sha256"],
            "restored_sha256": rb["restored_sha256"],
            "matches_validated_build": rb["matches_before"],
            "unvalidated_active": False,
            "stale_scores_reset": True,
            "invalidated_count": inv["invalidated_count"],
            "failed_checks": failed_checks,
            "passed_checks_before_rollback": passed_checks,
            "post_rollback_all_passed": len(post_rb_failed) == 0,
            "post_rollback_passed_count": len(post_rb_passed),
            "post_rollback_total_checks": len(critical_checks),
        }
        backup_dir = Path(root) / "rollback" / entry["apply_id"]
        (backup_dir / "safety_rollback_reasons.json").write_text(
            json.dumps(reason_entry, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        store.record("safety_rollback", reason_entry)
        return reason_entry

    val_entry = {
        "hypothesis_id": hypothesis_id,
        "apply_id": entry["apply_id"],
        "status": "VALIDATED_ACTIVE",
        "active_sha256": entry["after_sha256"],
        "unvalidated_active": False,
        "stale_scores_reset": True,
        "invalidated_count": inv["invalidated_count"],
        "passed_checks": passed_checks,
        "total_checks": len(critical_checks),
    }
    store.record("validated_build", val_entry)
    return val_entry


__all__ = [
    "ApprovalRequired",
    "apply_and_verify_or_rollback",
    "apply_draft",
    "invalidate_stale_evaluations",
    "rollback",
    "sha256_text",
]
