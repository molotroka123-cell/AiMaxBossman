"""Build the evolution video's reproducible Git facts.

Usage: python build_data.py [--fetch] [--as-of YYYY-MM-DD]
All-branch statistics use refs visible in this local clone at build time.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
PRAGUE = ZoneInfo("Europe/Prague")


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8").strip()


def classify(author: str) -> str:
    low = author.lower()
    if low == "molotroka123-cell":
        return "owner_handle"
    if low in {"asd", "pazatrahan228"}:
        return "local_handles_unverified"
    if low.startswith("claude") or low.startswith("bossman repair engineer"):
        return "claude"
    if low.startswith("codex"):
        return "codex"
    if low.startswith("argon"):
        return "argon"
    return "other"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="update remote refs before calculation")
    ap.add_argument("--refresh-refs", action="store_true", help="record a new immutable refs snapshot")
    ap.add_argument("--as-of", default="2026-10-15", help="documented snapshot date (YYYY-MM-DD)")
    args = ap.parse_args()
    date.fromisoformat(args.as_of)
    if args.fetch:
        subprocess.run(["git", "fetch", "--all"], cwd=ROOT, check=True)

    source_sha = git("rev-parse", "origin/main")
    lock = HERE / "refs.lock.json"
    if args.refresh_refs or not lock.exists():
        refs = [r for r in git("for-each-ref", "--format=%(refname) %(objectname)").splitlines()
                if not r.startswith("refs/heads/docs/bossman-evolution-video-20261015 ")]
        lock.write_bytes((json.dumps({"source_sha": source_sha, "refs": sorted(refs)}, indent=2) + "\n").encode("utf-8"))
    snapshot = json.loads(lock.read_text(encoding="utf-8"))
    if snapshot["source_sha"] != source_sha:
        raise RuntimeError("origin/main moved: refresh refs and rebuild intentionally")
    refs = snapshot["refs"]
    refs_hash = hashlib.sha256(("\n".join(sorted(refs)) + "\n").encode()).hexdigest()
    raw = git("log", *(r.split()[1] for r in refs), "--format=%H%x09%aI%x09%an%x09%s")
    commits = []
    seen = set()
    for line in raw.splitlines():
        sha, stamp, author, subject = line.split("\t", 3)
        if sha in seen:
            continue
        seen.add(sha)
        when = datetime.fromisoformat(stamp)
        commits.append((sha, when, author, subject))
    if not commits:
        raise RuntimeError("No commits visible")
    commits.sort(key=lambda c: (c[1].timestamp(), c[0]))
    per_day = Counter(c[1].astimezone(PRAGUE).date().isoformat() for c in commits)
    authors = Counter(classify(c[2]) for c in commits)
    files = git("ls-tree", "-r", "--name-only", "origin/main").splitlines()
    prs = [int(m.group(1)) for c in commits if (m := re.search(r"Merge pull request #(\d+)", c[3]))]
    first, last = commits[0], commits[-1]
    first_day = min(per_day)
    last_day = max(per_day)
    span = (date.fromisoformat(last_day) - date.fromisoformat(first_day)).days + 1
    timeline = []
    d = date.fromisoformat(first_day)
    while d <= date.fromisoformat(last_day):
        timeline.append([d.isoformat(), per_day[d.isoformat()]])
        d = date.fromordinal(d.toordinal() + 1)

    # Every displayed milestone is anchored to a real commit, never a remembered date.
    patterns = [
        ("desktop", r"feat.*desktop|one.click desktop"),
        ("studio", r"feat\(studio|feat\(studio-v2"),
        ("telegram", r"feat\(pult\)|feat\(telegram"),
        ("motion", r"feat\(motion\)|motion studio"),
        ("tree", r"feat\(tree\)|tree: (apply|sync)|living capability tree"),
        ("face_swap", r"face.swap"),
        ("bossman_one", r"bossman.one|argon.one"),
    ]
    milestones = []
    for key, pattern in patterns:
        matches = [c for c in commits if re.search(pattern, c[3], re.I) and not re.match(r"(docs|test|merge)[:( ]", c[3], re.I)]
        if matches:
            c = matches[0]
            milestones.append({"key": key, "day": c[1].astimezone(PRAGUE).date().isoformat(), "sha": c[0], "subject": c[3]})
    milestones.sort(key=lambda m: (m["day"], m["key"]))

    data = {
        "source_sha": source_sha,
        "calculated_on": args.as_of,
        "timezone": "Europe/Prague",
        "refs_sha256": refs_hash,
        "commits_all": len(commits),
        "commits_main": int(git("rev-list", "--count", "origin/main")),
        "first_commit": {"sha": first[0], "utc": first[1].astimezone(ZoneInfo("UTC")).isoformat(), "prague_day": first_day, "subject": first[3]},
        "last_commit": {"sha": last[0], "prague_day": last_day},
        "day_span": span,
        "per_day": timeline,
        "prs": max(prs, default=0),
        "prs_method": "maximum Merge pull request #N in local Git history",
        "files": len(files),
        "py_files": sum(p.endswith(".py") for p in files),
        "test_files": sum(Path(p).name.startswith("test_") and p.endswith(".py") for p in files),
        "authors": dict(sorted(authors.items())),
        "milestones": milestones,
    }
    output = HERE / "data.json"
    output.write_bytes((json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(output)
    print(f"source={source_sha} all={len(commits)} main={data['commits_main']} refs={refs_hash}")


if __name__ == "__main__":
    main()
