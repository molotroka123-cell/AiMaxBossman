"""How fast does each author really ship? Real git history, not impressions.

Per author (commits carrying a `Co-Authored-By: Claude` trailer are counted as "Claude"): commits, lines added/deleted,
"code" lines (source extensions only, docs/evidence excluded), active hours (consecutive commits <= --gap-min apart count as
one working session). Lines changed are NOT value: read big commits before trusting the number.

  python tools/git_velocity.py --ref origin/main --since 2026-10-14 [--gap-min 60] [--json]
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import subprocess
import sys

CODE_EXT = (".py", ".js", ".mjs", ".ts", ".ps1", ".css", ".html")


def is_code(path: str) -> bool:
    return path.endswith(CODE_EXT) and not path.startswith("docs/") and "/docs/" not in path and "evidence" not in path


def parse_log(text: str) -> list[dict]:
    commits, cur = [], None
    for line in text.splitlines():
        if line.startswith("@@"):
            sha, when, author, trailers, subject = (line[2:].split("|", 4) + [""] * 5)[:5]
            cur = {"sha": sha, "t": dt.datetime.fromisoformat(when), "author": author,
                   "claude": "claude" in trailers.lower(), "subject": subject, "add": 0, "del": 0, "code_add": 0, "files": 0}
            commits.append(cur)
        elif line.strip() and cur is not None:
            a, d, path = (line.split("\t", 2) + ["", ""])[:3]
            if not a.isdigit():
                continue
            cur["add"] += int(a)
            cur["del"] += int(d)
            cur["files"] += 1
            if is_code(path):
                cur["code_add"] += int(a)
    return commits


def summarize(commits: list[dict], gap_s: float = 3600.0) -> dict[str, dict]:
    by: dict[str, list[dict]] = collections.defaultdict(list)
    for c in commits:
        by["Claude" if c["claude"] else c["author"]].append(c)
    out = {}
    for who, cs in by.items():
        cs.sort(key=lambda c: c["t"])
        active, prev = 0.0, None
        for c in cs:
            if prev is not None and (c["t"] - prev).total_seconds() <= gap_s:
                active += (c["t"] - prev).total_seconds()
            prev = c["t"]
        code, hours = sum(c["code_add"] for c in cs), active / 3600
        out[who] = {"commits": len(cs), "added": sum(c["add"] for c in cs), "deleted": sum(c["del"] for c in cs),
                    "code_added": code, "active_hours": round(hours, 2),
                    "code_per_active_hour": round(code / max(hours, 0.1)),
                    "biggest_commit_code": max((c["code_add"] for c in cs), default=0),
                    "per_day": dict(sorted(collections.Counter(c["t"].strftime("%m-%d") for c in cs).items()))}
    return out


def git_log(ref: str, since: str, repo: str = ".") -> str:
    fmt = "@@%H|%cI|%an|%(trailers:key=Co-Authored-By,valueonly,separator=;)|%s"
    return subprocess.run(["git", "-C", repo, "log", ref, f"--since={since}", "--no-merges", f"--format={fmt}", "--numstat"],
                          capture_output=True, text=True, encoding="utf-8", check=True).stdout


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ref", default="origin/main")
    ap.add_argument("--since", required=True)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--gap-min", type=float, default=60.0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    res = summarize(parse_log(git_log(a.ref, a.since, a.repo)), a.gap_min * 60)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    for who, r in sorted(res.items(), key=lambda kv: -kv[1]["commits"]):
        print(f"{who}: {r['commits']} commits, +{r['added']}/-{r['deleted']} lines, code +{r['code_added']}, "
              f"active ~{r['active_hours']} h, ~{r['code_per_active_hour']} code lines/active hour, "
              f"biggest commit code +{r['biggest_commit_code']}, per day {r['per_day']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
