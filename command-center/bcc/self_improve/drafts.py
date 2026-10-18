"""Proposals as reviewable, versioned drafts. A draft is DATA: writing one never changes a target.

Layout: ``<root>/drafts/<draft_id>/v<N>/{meta.json,proposed.txt,change.diff}``. A new version never
overwrites an old one. The kind is one of instruction / routing / tool / workflow; the target is a
path RELATIVE to an apply root that only the owner configures at apply time.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .limits import LimitViolation, Limits, guard_write_target
from .redact import assert_clean

KINDS = ("instruction", "routing", "tool", "workflow")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,60}$")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Draft:
    draft_id: str
    version: int
    kind: str
    target_rel: str
    base_sha256: str          # sha of the text the diff was made against ("N/A" for a new file)
    proposed_sha256: str
    author_role: str
    author_model: str
    justification: str
    path: str


class DraftStore:
    def __init__(self, root: str | os.PathLike, limits: Limits):
        self.root = Path(root) / "drafts"
        self.limits = limits

    def save(self, draft_id: str, *, kind: str, target_rel: str, base_text: str | None, proposed_text: str,
             author_role: str, author_model: str, justification: str) -> Draft:
        if not _ID.match(draft_id):
            raise LimitViolation("bad draft id")
        if kind not in KINDS:
            raise LimitViolation(f"draft kind must be one of {KINDS}")
        rel = target_rel.replace("\\", "/")
        if os.path.isabs(rel) or Path(rel).drive or ".." in Path(rel).parts or not rel:
            raise LimitViolation("draft target must be a relative path without '..'")
        guard_write_target("/" + rel)  # protected files can not even be PROPOSED for change
        if len(proposed_text.encode("utf-8")) > self.limits.max_draft_bytes:
            raise LimitViolation("draft too large")
        assert_clean({"justification": justification, "proposed": proposed_text})
        d = self.root / draft_id
        d.mkdir(parents=True, exist_ok=True)
        version = 1 + max([int(p.name[1:]) for p in d.glob("v*") if p.name[1:].isdigit()] or [0])
        vdir = d / f"v{version}"
        vdir.mkdir()
        diff = "".join(difflib.unified_diff((base_text or "").splitlines(True), proposed_text.splitlines(True),
                                            f"a/{rel}", f"b/{rel}"))
        (vdir / "proposed.txt").write_text(proposed_text, encoding="utf-8", newline="")
        (vdir / "change.diff").write_text(diff, encoding="utf-8", newline="")
        meta = {"draft_id": draft_id, "version": version, "kind": kind, "target_rel": rel,
                "base_sha256": sha256_text(base_text) if base_text is not None else "N/A",
                "proposed_sha256": sha256_text(proposed_text), "author_role": author_role,
                "author_model": author_model, "justification": justification, "applied": False}
        (vdir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        return Draft(path=str(vdir), **{k: meta[k] for k in (
            "draft_id", "version", "kind", "target_rel", "base_sha256", "proposed_sha256", "author_role",
            "author_model", "justification")})

    def load(self, draft_id: str, version: int | None = None) -> tuple[Draft, str]:
        if not _ID.match(draft_id):
            raise LimitViolation("bad draft id")
        d = self.root / draft_id
        versions = sorted(int(p.name[1:]) for p in d.glob("v*") if p.name[1:].isdigit())
        if not versions:
            raise FileNotFoundError(draft_id)
        v = version or versions[-1]
        vdir = d / f"v{v}"
        meta = json.loads((vdir / "meta.json").read_text(encoding="utf-8"))
        text = (vdir / "proposed.txt").read_text(encoding="utf-8")
        if sha256_text(text) != meta["proposed_sha256"]:
            raise LimitViolation("draft content does not match its recorded hash (tampered)")
        return Draft(path=str(vdir), **{k: meta[k] for k in (
            "draft_id", "version", "kind", "target_rel", "base_sha256", "proposed_sha256", "author_role",
            "author_model", "justification")}), text
