"""Verify open-source candidates against LIVE registries (owner rule: prefer OSS, verify, never assume).

Existence, latest version, licence, stars/last push/archived - from PyPI, npm and the GitHub REST API (public, read-only).
A candidate gets flags: COPYLEFT (GPL/AGPL/LGPL), NO-LICENSE, ARCHIVED, STALE (no push > 12 months), ERR (not found).

  python tools/oss_check.py --pypi litellm,rapidfuzz --npm promptfoo --gh BerriAI/litellm [--json] [--strict]
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import sys
import urllib.request

COPYLEFT = ("GPL", "AGPL", "LGPL", "SSPL", "EUPL")
STALE_DAYS = 365


def fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "bossman-oss-check"})
    with urllib.request.urlopen(req, timeout=25) as r:      # noqa: S310 - fixed public https registries
        return json.loads(r.read().decode())


def flags_for(row: dict, today: dt.date | None = None) -> list[str]:
    today = today or dt.date.today()
    out = []
    lic = str(row.get("license") or "").strip()
    if not lic or lic.upper() in ("NONE", "NOASSERTION", "UNKNOWN"):
        out.append("NO-LICENSE")
    elif any(tag in lic.upper() for tag in COPYLEFT):
        out.append("COPYLEFT")
    if row.get("archived"):
        out.append("ARCHIVED")
    pushed = row.get("pushed")
    if pushed:
        try:
            if (today - dt.date.fromisoformat(pushed[:10])).days > STALE_DAYS:
                out.append("STALE")
        except ValueError:
            pass
    return out


def check(kind: str, name: str, get=fetch) -> dict:
    row = {"kind": kind, "name": name}
    try:
        if kind == "pypi":
            i = get(f"https://pypi.org/pypi/{name}/json")["info"]
            lic = i.get("license_expression") or (i.get("license") or "")[:60] or ",".join(
                c.split("::")[-1].strip() for c in i.get("classifiers", []) if c.startswith("License"))
            row.update(version=i["version"], license=lic)
        elif kind == "npm":
            d = get(f"https://registry.npmjs.org/{name.replace('/', '%2F')}/latest")
            row.update(version=d.get("version"), license=d.get("license"))
        else:
            d = get(f"https://api.github.com/repos/{name}")
            row.update(stars=d["stargazers_count"], license=(d.get("license") or {}).get("spdx_id"),
                       pushed=d["pushed_at"], archived=bool(d["archived"]))
    except Exception as e:  # noqa: BLE001
        row.update(error=str(e)[:100], flags=["ERR"])
        return row
    row["flags"] = flags_for(row)
    return row


def line(row: dict) -> str:
    head = f"{row['kind'].upper():6s} {row['name']}"
    if row.get("error"):
        return f"{head} ERR {row['error']}"
    bits = [str(row["version"])] if row.get("version") else []
    if "stars" in row:
        bits += [f"stars {row['stars']}", f"pushed {row['pushed'][:10]}"]
    bits.append(f"lic {row.get('license') or '-'}")
    return f"{head} " + " | ".join(bits) + (f"  !! {','.join(row['flags'])}" if row["flags"] else "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    for k in ("pypi", "npm", "gh"):
        ap.add_argument(f"--{k}", default="", help="comma-separated names")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="exit 1 if any candidate has a flag")
    a = ap.parse_args(argv)
    jobs = [("github" if k == "gh" else k, n.strip()) for k in ("pypi", "npm", "gh")
            for n in getattr(a, k).split(",") if n.strip()]
    with cf.ThreadPoolExecutor(8) as ex:
        rows = list(ex.map(lambda j: check(*j), jobs))
    print(json.dumps(rows, indent=1) if a.json else "\n".join(line(r) for r in rows))
    return 1 if a.strict and any(r.get("flags") for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
