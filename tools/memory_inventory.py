"""Memory inventory of the owner's PC: what is active, what is useful archive, what is a duplicate, what is a cache (owner 24.10).

    python tools/memory_inventory.py [--out docs/audits/2026-10-24-memory/inventory.json]

METADATA ONLY: names, sizes, file counts, newest modification time. No file content is opened, so chats, photos, keys
and personal data are never read (owner rule 21.10). Nothing is deleted or moved here: every entry that could free
space is a CANDIDATE with its size, the reason and a reversible plan (copy to D: + hash verify, C: kept until the owner
approves). Classification is by well-known names and by age; anything it cannot place is ``unknown`` and stays untouched.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path

CATEGORIES = ("active_memory", "useful_archive", "duplicate", "temp_cache", "unknown")
# (regex on the directory/file name, category, reason). First match wins; order matters.
RULES = [
    (r"\.bak[-_.]|[-_.]bak$|^owner-backup-|^CommandCenter\.bak", "duplicate", "backup copy of data that still exists in its live form"),
    (r"^CommandCenter$", "active_memory", "live Bossman data dir: bcc.db, chats, tasks, skills, receipts (the backend runs on it)"),
    (r"^(telegram-companion|telegram-pollers|codex-telegram)$", "active_memory", "pult/companion state"),
    (r"^(autonomy|keys|secrets|security|private|evening|maintenance|night|ops|remote|owner-run)$", "active_memory",
     "operational state, keys or owner runs"),
    (r"^backups$", "useful_archive", "Bossman's own backup folder"),
    (r"^(tool-cache|pytest-|stage22-proof)", "temp_cache", "regenerable cache or test scratch"),
    (r"^(pip|npm|\.cache|__pycache__|Temp|Cache)", "temp_cache", "package/tool cache, regenerable"),
    (r"^(model-evals|one-bossman-logs|agent-watchdog)$", "useful_archive", "evidence and logs: kept for audits"),
    (r"^(models|motion-tts|motion-venv|ComfyUI-Video|poker-host)$", "useful_archive",
     "model weights / runtimes: large, reproducible only by re-download, never deleted"),
    (r"^(wt-|integration-|worker-src|evo-tree-src|app$|one-bossman$|runtime$|llama-vulkan$|media-runtime$)", "useful_archive",
     "code worktrees and runtimes the live system uses"),
]
RECENT_DAYS = 14


def classify(name: str, newest_mtime: float | None, now: float | None = None) -> tuple[str, str]:
    """(category, reason). Unmatched names stay ``unknown``; age alone never promotes anything to a duplicate."""
    for pattern, category, reason in RULES:
        if re.search(pattern, name):
            return category, reason
    now = now or time.time()
    if newest_mtime is not None and (now - newest_mtime) / 86400 <= RECENT_DAYS:
        return "unknown", "recently used, not recognised: left alone"
    return "unknown", "not recognised and not recent: needs the owner's decision"


def candidate_action(category: str, size_bytes: int) -> str | None:
    """A reversible plan for entries that could free space, else None. Never 'delete'."""
    if size_bytes < 500 * 2**20:
        return None
    if category == "duplicate":
        return "copy to D:/Bossman/archive/<name>, verify sha256 of every file, keep the C: copy until the owner approves its removal"
    if category == "temp_cache":
        return "regenerable: clear through the owning tool (pip cache purge / tool-cache clean), keep a size receipt"
    if category == "useful_archive":
        return "move only a COLD copy to D: after hash verify; C: stays the reserve until the owner approves"
    return None


REPARSE = 0x400          # FILE_ATTRIBUTE_REPARSE_POINT: symlinks AND junctions; following them can loop forever on Windows


def scan(path: Path, *, budget_s: float = 300.0) -> dict:
    """Recursive size, file count and newest mtime from directory entries only (no file is opened).

    Reparse points (junctions, symlinks) are NOT followed (a junction cycle held a first run for 90+ minutes) and a time
    budget caps every entry: an incomplete scan says so (``complete: false``) instead of hanging."""
    size, files, newest, skipped = 0, 0, 0.0, 0
    deadline = time.monotonic() + budget_s
    stack = [str(path)]
    complete = True
    while stack:
        if time.monotonic() > deadline:
            complete = False
            break
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for e in it:
                    try:
                        st = e.stat(follow_symlinks=False)
                        if e.is_symlink() or getattr(st, "st_file_attributes", 0) & REPARSE:
                            skipped += 1
                            continue
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            size += st.st_size
                            files += 1
                            newest = max(newest, st.st_mtime)
                    except OSError:
                        continue
        except OSError:
            continue
    return {"size_bytes": size, "files": files, "newest_mtime": newest or None, "complete": complete, "reparse_skipped": skipped}


def inventory(roots: list[Path], now: float | None = None) -> dict:
    entries = []
    for root in roots:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if child.is_dir():
                meta = scan(child)
            else:
                st = child.stat()
                meta = {"size_bytes": st.st_size, "files": 1, "newest_mtime": st.st_mtime, "complete": True, "reparse_skipped": 0}
            category, reason = classify(child.name, meta["newest_mtime"], now)
            entries.append({"path": str(child), **meta, "category": category, "reason": reason,
                            "candidate_action": candidate_action(category, meta["size_bytes"])})
    totals = {c: {"entries": 0, "size_gb": 0.0} for c in CATEGORIES}
    for e in entries:
        totals[e["category"]]["entries"] += 1
        totals[e["category"]]["size_gb"] = round(totals[e["category"]]["size_gb"] + e["size_bytes"] / 1e9, 2)
    return {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "roots": [str(r) for r in roots],
            "content_read": False, "incomplete_entries": [e["path"] for e in entries if not e.get("complete", True)],
            "totals": totals, "entries": entries}


def default_roots() -> list[Path]:
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman"
    return [r for r in (local, Path.home() / "Bossman", Path.home() / ".ollama", Path.home() / ".cache") if r.exists()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="docs/audits/2026-10-24-memory/inventory.json")
    ap.add_argument("--root", action="append", default=None)
    a = ap.parse_args(argv)
    doc = inventory([Path(r) for r in a.root] if a.root else default_roots())
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(json.dumps(doc["totals"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
