#!/usr/bin/env python
"""Re-runnable lane-C probe: skills load receipts + OSS catalog audit.

  python tools/tree_proof/skills_oss_probe.py skills   # -> evidence/skills.json + evidence/out/<node>.txt
  python tools/tree_proof/skills_oss_probe.py oss      # -> evidence/oss-audit.json + oss-audit.md
  python tools/tree_proof/skills_oss_probe.py md       # re-render md from oss-audit.json (+ oss-shortlist.json)

Read-only on the seed. Skill probes run in a temp HOME / BCC_DATA_DIR; never the owner's data.
"""
from __future__ import annotations
import argparse, concurrent.futures as cf, datetime as dt, hashlib, json, os
import re, subprocess, sys, tempfile, time, urllib.request, urllib.error
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "command-center/bcc/capability_tree_seed.json"
EV = ROOT / "docs/architecture/bossman-tree-20261005/evidence"
OUT = EV / "out"
sys.path.insert(0, str(ROOT / "command-center"))


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def head_sha() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def nodes() -> list[dict]:
    return json.loads(SEED.read_text(encoding="utf-8"))["nodes"]


# ---------------------------------------------------------------- skills worker
def skill_worker(rel: str) -> int:
    """Load one SKILL.md with the repo's own loader/validator. Exit 0 = PASS."""
    from bcc.v2.skill_library import parse_skill, SkillLibrary, default_skill_roots
    from bcc.v2.skill_catalog import SkillCatalog, scan_policy, CATALOG_ROOT
    p = (ROOT / rel).resolve()
    ok = True

    def chk(name, cond, info=""):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"[{'ok' if cond else 'FAIL'}] {name} {info}")

    chk("file exists", p.is_file(), rel)
    if not p.is_file():
        return 1
    home = Path(tempfile.mkdtemp(prefix="skillprobe_"))
    raw = p.read_text(encoding="utf-8", errors="replace")
    chk("frontmatter block present", raw.startswith("---\n") or raw.startswith("---\r\n"))
    sk = parse_skill(p, p.parent.parent)
    chk("parse_skill: name", bool(sk.name), repr(sk.name))
    chk("parse_skill: description non-empty", bool(sk.description.strip()), f"len={len(sk.description)}")
    chk("frontmatter parsed to dict", isinstance(sk.frontmatter, dict) and bool(sk.frontmatter), f"keys={sorted(sk.frontmatter)}")
    chk("body non-empty", len(sk.body.strip()) > 20, f"len={len(sk.body)}")
    crit = [v.code for v in scan_policy(sk.body) if v.critical]
    chk("no critical policy violation", not crit, str(crit))
    if CATALOG_ROOT.resolve() in p.parents:
        e = next((x for x in SkillCatalog().entries() if x.path.resolve() == p), None)
        chk("SkillCatalog discovers it", e is not None)
        if e:
            chk("provenance sha256 matches (not QUARANTINED)", e.status != "QUARANTINED", f"status={e.status} reason={e.status_reason}")
            lic = e.provenance.get("license") or e.provenance.get("licence")
            chk("provenance has licence", bool(lic), f"licence={lic!r} keys={sorted(e.provenance)}")
            print("catalog status:", e.status, "id:", e.id, "sha256:", e.sha256[:16])
    else:
        lib = SkillLibrary(default_skill_roots(ROOT, home), ROOT)
        found = [s for s in lib.discover() if s.path.resolve() == p]
        if found:
            chk("SkillLibrary(default_skill_roots).discover finds it", True)
        else:
            print("[note] not under default_skill_roots; parsed directly only")
    print("VERDICT", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def run_logged(cmd: list[str], env=None) -> dict:
    t0 = now()
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=900)
    return {"started_at": t0, "finished_at": now(), "exit_code": r.returncode, "text": (r.stdout or "") + (r.stderr or "")}


def do_skills() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sha = head_sha()
    tmp = tempfile.mkdtemp(prefix="skills_probe_data_")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", BCC_DATA_DIR=tmp, HOME=tmp, USERPROFILE=tmp)
    receipts = []
    for n in nodes():
        if n["parent"] != "skills":
            continue
        nid, status = n["id"], n["status"]
        rel = next((s["path"] for s in n.get("sources", []) if s["path"].endswith("SKILL.md")), None)
        base = {"node_id": nid, "label": n["label"], "seed_status": status, "sha": sha}
        if status == "code":
            tests = [f"command-center/tests/{t}" for t in ("test_skill_catalog.py", "test_skill_catalog_crlf.py", "test_skill_discovery_memoized.py", "test_feat_skills.py")]
            cmd = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *tests]
            r = run_logged(cmd, env)
            verdict = "PASS" if r["exit_code"] == 0 else "FAIL"
            kind, proposed = "skill_runtime_tests", ("code" if verdict == "PASS" else None)
            extra = {"note": "unit tests of loader/catalog in temp data dir; proves code works, not live routing"}
            probe = "pytest skill loader/catalog tests"
        elif rel and (ROOT / rel).is_file():
            cmd = [sys.executable, str(Path(__file__).relative_to(ROOT)), "skill-worker", rel]
            r = run_logged(cmd, env)
            verdict = "PASS" if r["exit_code"] == 0 else "FAIL"
            kind, proposed = "skill_load", ("prepared" if verdict == "PASS" else None)
            extra = {"note": "loads + validates with repo loader; not live use; not reported"}
            probe = f"skill_loader:{rel}"
        else:
            continue  # not present in this worktree: no receipt
        f = OUT / f"{nid}.txt"
        f.write_text(r["text"], encoding="utf-8", newline="\n")
        receipts.append({**base, "probe": probe, "command": " ".join(cmd),
                         "exit_code": r["exit_code"], "started_at": r["started_at"], "finished_at": r["finished_at"],
                         "output_sha256": hashlib.sha256(f.read_bytes()).hexdigest(),
                         "output_tail": r["text"][-1500:], "verdict": verdict, "kind": kind,
                         "proposed_status": proposed, **extra})
    (EV / "skills.json").write_text(json.dumps({"generated_at": now(), "sha": sha, "receipts": receipts}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(Counter((x["kind"], x["verdict"]) for x in receipts), "receipts:", len(receipts))


# ---------------------------------------------------------------- OSS audit
UA = {"User-Agent": "bossman-tree-oss-audit/1"}
GH = re.compile(r"^https?://github\.com/([^/\s]+)/([^/\s#?]+)")
stop_gh = {"stop": False, "why": ""}


def probe_url(url: str) -> dict:
    last = None
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(url, method=method, headers=UA)
            with urllib.request.urlopen(req, timeout=10) as r:
                return {"reachable": True, "http": r.status}
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {"reachable": False, "http": 404}
            last = {"reachable": e.code in (401, 403, 429), "http": e.code}
        except Exception as e:  # noqa
            last = {"reachable": False, "http": None, "error": type(e).__name__}
    return last or {"reachable": False, "http": None}


def gh_license(url: str) -> dict:
    m = GH.match(url or "")
    if not m:
        return {"license": "n/a (non-github)"}
    if stop_gh["stop"]:
        return {"license": "unknown (api stopped: " + stop_gh["why"] + ")"}
    owner, repo = m.group(1), re.sub(r"\.git$", "", m.group(2))
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{owner}/{repo}", headers={**UA, "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            j = json.load(r)
            lic = (j.get("license") or {}).get("spdx_id") or "none-detected"
            return {"license": lic, "stars": j.get("stargazers_count"), "archived": j.get("archived"), "pushed_at": j.get("pushed_at")}
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            stop_gh.update(stop=True, why=f"HTTP {e.code}")
            return {"license": f"unknown (HTTP {e.code}, stopped)"}
        return {"license": f"unknown (HTTP {e.code})"}
    except Exception as e:  # noqa
        return {"license": f"unknown ({type(e).__name__})"}


def integrated(label: str, url: str) -> list[str]:
    names = {label.split("/")[-1], label}
    if url:
        names.add(re.sub(r"\.git$", "", url.rstrip("/").split("/")[-1]))
    hits: list[str] = []
    for nm in sorted(x for x in names if len(x) >= 5):
        r = subprocess.run(["git", "grep", "-il", "-F", nm, "--", "*.py", "*.toml", "*.txt", "*.json", "*.yml", "*.yaml", "*.js", "*.ts",
                            ":!command-center/bcc/capability_tree_seed.json", ":!docs", ":!tools/tree_proof", ":!command-center/bcc/skills_catalog"],
                           cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        hits += [x for x in r.stdout.split("\n") if x]
    return sorted(set(hits))[:5]


def do_oss() -> None:
    L = [n for n in nodes() if n["parent"] == "oss"]

    def work(n):
        url = n.get("external_url") or ""
        row = {"id": n["id"], "label": n["label"], "url": url}
        row.update(probe_url(url) if url else {"reachable": None, "http": None})
        return row

    with cf.ThreadPoolExecutor(8) as ex:
        rows = list(ex.map(work, L))
    for r in rows:  # GitHub API sequential, stops at first 403/429
        r.update(gh_license(r["url"]))
        r["referenced_in_code"] = integrated(r["label"], r["url"])
    (EV / "oss-audit.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    write_md(rows)


def write_md(rows: list[dict]) -> None:
    reach = Counter("reachable" if r["reachable"] else ("dead/blocked" if r["reachable"] is False else "no-url") for r in rows)
    lic = Counter(r["license"] for r in rows)
    integ = [r for r in rows if r["referenced_in_code"]]
    md = [f"# OSS catalog audit (lane C, {len(rows)} entries)", "",
          f"Generated by tools/tree_proof/skills_oss_probe.py at {head_sha()[:10]}. Nothing installed. No entry is marked green: status stays 'recorded' (reference only).", "",
          "## Summary", "", f"- URL reachability: {dict(reach)}",
          f"- Licences: {dict(lic.most_common())}",
          f"- Referenced in non-doc code/config (git grep by name, may include false positives): {len(integ)}", ""]
    sp = EV / "oss-shortlist.json"
    if sp.is_file():
        md += ["## Ranked shortlist: top 10 adopt-and-harden candidates", "",
               "Criteria: owner rule 'prefer working OSS over building our own'; reachable, permissive licence, relevant to Bossman (agent loop, skills, evals, review, memory, local models). Ranking is a judgment, not a measurement.", ""]
        for i, s in enumerate(json.loads(sp.read_text(encoding="utf-8")), 1):
            md.append(f"{i}. **{s['label']}** - {s['reason']}")
        md.append("")
    md += ["## Per-entry table", "", "| id | label | URL | HTTP | reachable | licence | referenced in code |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['id']} | {r['label']} | {r['url'] or '-'} | {r.get('http')} | {r['reachable']} | {r['license']} | {', '.join(r['referenced_in_code']) or '-'} |")
    (EV / "oss-audit.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(dict(reach), dict(lic.most_common(8)), "referenced:", len(integ))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["skills", "oss", "skill-worker", "md"])
    ap.add_argument("arg", nargs="?")
    a = ap.parse_args()
    if a.cmd == "skill-worker":
        sys.exit(skill_worker(a.arg))
    EV.mkdir(parents=True, exist_ok=True)
    if a.cmd == "skills":
        do_skills()
    elif a.cmd == "oss":
        do_oss()
    else:
        write_md(json.loads((EV / "oss-audit.json").read_text(encoding="utf-8")))
