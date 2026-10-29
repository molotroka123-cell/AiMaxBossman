"""TZ 2.5 M0 / Задача 0: archive the TEST conversations out of the owner's working chat list.

Test conversations are picked by METADATA ONLY (threads-meta.json: title prefix of our test/proof tools, project
marker) — a session file is never parsed; its bytes are hashed and encrypted as opaque bytes. Prints only ids, the
matched marker and counts.

    python tools/archive_test_conversations.py --dry-run
    python tools/archive_test_conversations.py --run              # archive via the live backend, copy to D:, manifest
    python tools/archive_test_conversations.py --verify-restore <D:\\...\\<date>>   # restore every archive on a COPY

Archiving goes through the backend's own encrypted archive (`POST /api/chat/threads/{id}/archive-encrypted`:
AES-256-GCM, DPAPI-wrapped key, verify-before-delete), so the backend's caches stay coherent; the archive files are
then copied to `D:\\Bossman\\archive\\test-conversations\\<date>\\` with `ids.txt` and `manifest.json` (SHA-256 of
the live bytes and of every archive). Restore is verified on a throw-away copy, never on the working data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
TEST_TITLE_PREFIXES = ("PROOF", "DEMO rehearsal", "TEST", "[test]", "SMOKE", "E2E", "eval500", "SYNTHETIC", "Synthetic")
TEST_PROJECTS = {"test", "tests", "proof", "proofs", "eval500", "synthetic"}
THREAD_ID_RE = re.compile(r"[A-Za-z0-9_-]{4,64}")
ARCHIVE_SUFFIX = ".bcarchive"
DEFAULT_OUT = Path(r"D:\Bossman\archive\test-conversations")


def data_dir() -> Path:
    env = os.environ.get("BCC_DATA_DIR")
    if env:
        return Path(env)
    return Path(os.environ["LOCALAPPDATA"]) / "Bossman" / "CommandCenter"


def _meta(ddir: Path) -> dict:
    try:
        raw = json.loads((ddir / "terminal" / "threads-meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    threads = raw.get("threads", raw) if isinstance(raw, dict) else {}
    return threads if isinstance(threads, dict) else {}


def marker(entry: dict) -> str | None:
    """The test marker of one metadata entry, or None. Never looks at the conversation itself."""
    title = entry.get("title")
    if isinstance(title, str):
        for p in TEST_TITLE_PREFIXES:
            if title.strip().startswith(p):
                return f"title:{p}"
    project = entry.get("project")
    if isinstance(project, str) and project.strip().casefold() in TEST_PROJECTS:
        return f"project:{project.strip().casefold()}"
    if entry.get("test") is True:
        return "flag:test"
    return None


def candidates(ddir: Path) -> list[dict]:
    out = []
    for tid, entry in sorted(_meta(ddir).items()):
        if not isinstance(entry, dict) or not THREAD_ID_RE.fullmatch(str(tid)):
            continue
        m = marker(entry)
        live = ddir / "terminal" / "sessions" / f"{tid}.json"
        if m and live.is_file():
            out.append({"id": tid, "marker": m})
    return out


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(ddir: Path, out_root: Path, api) -> dict:
    found = candidates(ddir)
    day = out_root / time.strftime("%Y-%m-%d")
    day.mkdir(parents=True, exist_ok=True)
    rows, failed = [], []
    for c in found:
        live = ddir / "terminal" / "sessions" / f"{c['id']}.json"
        live_sha = _sha(live)                              # opaque bytes, for the restore check only
        try:
            api("POST", f"/api/chat/threads/{c['id']}/archive-encrypted", {})
        except Exception as exc:                           # one busy chat must not stop the rest
            failed.append({"id": c["id"], "error": type(exc).__name__})
            continue
        arc = ddir / "chat-archive" / f"{c['id']}{ARCHIVE_SUFFIX}"
        dst = day / arc.name
        shutil.copy2(arc, dst)
        if _sha(dst) != _sha(arc):
            failed.append({"id": c["id"], "error": "copy_sha_mismatch"})
            continue
        rows.append({**c, "live_sha256": live_sha, "archive_sha256": _sha(dst), "archive_bytes": dst.stat().st_size})
    (day / "ids.txt").write_text("".join(r["id"] + "\n" for r in rows), encoding="utf-8")
    manifest = {"schema": "bossman.test-conversations-archive.v1", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "selection": "metadata only (threads-meta.json title prefix / project / test flag)", "archived": rows,
                "failed": failed, "remaining_test_marked": len(candidates(ddir))}
    (day / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    manifest["manifest_sha256"] = _sha(day / "manifest.json")
    manifest["dir"] = str(day)
    return manifest


def verify_restore(day: Path) -> dict:
    """Restore every archive of a manifest into a temporary COPY data dir and compare with the recorded live hash."""
    sys.path.insert(0, str(ROOT / "command-center"))
    from bcc.features import chat_archive  # noqa: E402  (real decrypt + restore path, real key, throw-away data dir)
    manifest = json.loads((day / "manifest.json").read_text(encoding="utf-8"))
    results = []
    with tempfile.TemporaryDirectory(prefix="bcc-restore-check-") as tmp:
        svc = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp))
        (Path(tmp) / "terminal" / "sessions").mkdir(parents=True)
        (Path(tmp) / "chat-archive").mkdir()
        for row in manifest["archived"]:
            src = Path(tmp) / "chat-archive" / f"{row['id']}{ARCHIVE_SUFFIX}"
            shutil.copy2(day / src.name, src)
            live = Path(tmp) / "terminal" / "sessions" / f"{row['id']}.json"
            err = None
            try:
                chat_archive._restore_sync(svc, row["id"], src, live)
                ok = _sha(live) == row["live_sha256"]
            except Exception as exc:
                ok, err = False, type(exc).__name__
            results.append({"id": row["id"], "restored_sha_matches": ok, **({"error": err} if err else {})})
    return {"checked": len(results), "ok": sum(r["restored_sha_matches"] for r in results), "results": results}


def _api():
    sys.path.insert(0, str(ROOT / "tools"))
    import prove_chat_attachments as p  # token + loopback api helper shared with the other proofs
    return p.api


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--run", action="store_true")
    g.add_argument("--verify-restore", metavar="DIR")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    a = ap.parse_args(argv)
    ddir = data_dir()
    if a.dry_run:
        found = candidates(ddir)
        print(json.dumps({"data_dir": str(ddir), "count": len(found), "candidates": found}, ensure_ascii=False, indent=1))
        return 0
    if a.run:
        res = run(ddir, Path(a.out), _api())
        print(json.dumps({k: v for k, v in res.items() if k != "archived"} | {"archived_ids": [r["id"] for r in res["archived"]]},
                         ensure_ascii=False, indent=1))
        return 0 if not res["failed"] and res["remaining_test_marked"] == 0 else 1
    res = verify_restore(Path(a.verify_restore))
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0 if res["ok"] == res["checked"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
