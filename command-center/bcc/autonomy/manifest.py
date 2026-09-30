"""Hash-linked freeze manifest.

``build_manifest(journal, artifacts, required)`` lists every artifact with its
sha256 and size, names the required ones that are missing, links the manifest
to the journal head (seq + hash) it was built on, and appends the manifest's
own sha256 to the journal (``freeze.manifest``). ``verify_manifest`` re-hashes
the files, recomputes the manifest hash, checks that the journal still
verifies, that the linked head is an entry of the chain, and that the
journal's ``freeze.manifest`` entry names this exact manifest hash.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, Mapping

from .journal import Journal, canonical, sha256_bytes, sha256_file, utc_now

SCHEMA = "bossman.autonomy.freeze_manifest.v1"


def _body_hash(manifest: Mapping) -> str:
    return sha256_bytes(canonical({k: v for k, v in manifest.items() if k != "manifest_sha256"}))


def build_manifest(journal: Journal, artifacts: Mapping[str, str | os.PathLike], required: Iterable[str] = (), *,
                   label: str = "", candidate_sha: str = "") -> dict:
    verdict = journal.verify()
    if not verdict.ok:
        raise ValueError(f"journal does not verify: {verdict.reason}")
    items = {}
    for name, path in sorted(artifacts.items()):
        p = Path(path)
        if p.is_file():
            items[name] = {"path": str(p), "sha256": sha256_file(p), "bytes": p.stat().st_size}
        else:
            items[name] = {"path": str(p), "sha256": None, "bytes": None}
    required = sorted(set(required))
    missing = sorted(n for n in required if n not in items or items[n]["sha256"] is None)
    manifest = {"schema": SCHEMA, "label": label, "candidate_sha": candidate_sha, "created_at": utc_now(),
                "journal": {"seq": verdict.entries, "head": verdict.head}, "required": required,
                "artifacts": items, "missing": missing, "complete": not missing}
    manifest["manifest_sha256"] = _body_hash(manifest)
    journal.append("freeze.manifest", {"manifest_sha256": manifest["manifest_sha256"], "label": label,
                                       "candidate_sha": candidate_sha, "complete": manifest["complete"],
                                       "missing": missing, "journal_head": verdict.head})
    return manifest


def write_manifest(manifest: Mapping, path: str | os.PathLike) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(manifest, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


def verify_manifest(manifest: Mapping, journal: Journal) -> list[str]:
    """Problems found; an empty list means the manifest and its artifacts are intact."""
    problems: list[str] = []
    if manifest.get("schema") != SCHEMA:
        problems.append("unknown manifest schema")
    if _body_hash(manifest) != manifest.get("manifest_sha256"):
        problems.append("manifest content changed (manifest_sha256 mismatch)")
    for name, item in (manifest.get("artifacts") or {}).items():
        if item.get("sha256") is None:
            continue
        p = Path(item.get("path", ""))
        if not p.is_file():
            problems.append(f"{name}: artifact missing")
        elif sha256_file(p) != item["sha256"]:
            problems.append(f"{name}: artifact changed")
    if manifest.get("missing"):
        problems.append(f"required artifacts missing at freeze: {manifest['missing']}")
    v = journal.verify()
    if not v.ok:
        problems.append(f"journal does not verify: {v.reason}")
        return problems
    head = (manifest.get("journal") or {}).get("head")
    entries = journal.entries()
    hashes = {e["hash"] for e in entries} | ({"0" * 64} if (manifest.get("journal") or {}).get("seq") == 0 else set())
    if head not in hashes:
        problems.append("linked journal head is not in the journal chain")
    if not any(e["kind"] == "freeze.manifest" and e["payload"].get("manifest_sha256") == manifest.get(
            "manifest_sha256") for e in entries):
        problems.append("the journal has no freeze.manifest entry for this manifest")
    return problems


__all__ = ["SCHEMA", "build_manifest", "verify_manifest", "write_manifest"]
