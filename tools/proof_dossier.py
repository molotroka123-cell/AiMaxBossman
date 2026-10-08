#!/usr/bin/env python3
"""Bossman proof dossier: ONE self-contained HTML + MD report built only from evidence files that exist (stdlib only).

    python tools/proof_dossier.py [--date 2026-10-08] [--out-root C:\\Users\\asd\\Bossman\\handoff]
                                  [--extra-selfrepair DIR] [--extra-evidence DIR] [--no-live]

Output: <out-root>/proof-dossier-<date>/proof-dossier.html, proof-dossier.md, proof-dossier.json.

Rule: no claim without a file behind it. Every number carries the path and sha256 of the file it was read from; a fact
whose evidence is missing prints 'НЕТ ДОКАЗАТЕЛЬСТВА' instead of a value. The dossier reads, it never runs tests or
touches Bossman state (the only network call is GET /health/live on localhost, skipped by --no-live).

Sections: 1 build identity, 2 capability tree, 3 receipts by kind, 4 self-repair cycles, 5 UX sweep, 6 test runs,
7 rollback scripts, 8 open issues.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import html
import json
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
NO_EVIDENCE = "НЕТ ДОКАЗАТЕЛЬСТВА"
DEFAULT_APP_ROOT = Path(r"C:\Users\asd\Bossman\app")
DEFAULT_OUT_ROOT = Path(r"C:\Users\asd\Bossman\handoff")
DEFAULT_SELFREPAIR = [Path(r"C:\Users\asd\Bossman\bugtest-20261001\tree-1005\selfrepair")]
DEFAULT_TEST_DIRS = [Path(r"C:\Users\asd\Bossman\bugtest-20261001\tree-1005")]
SEED_REL = Path("command-center/bcc/capability_tree_seed.json")
EVID_REL = Path("docs/architecture/bossman-tree-20261005/evidence")


def sha256_file(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        return h.hexdigest()
    except OSError:
        return None


def ev(path: Path | str | None) -> dict[str, str] | None:
    """Evidence reference {path, sha256}; None when the file does not exist."""
    if path is None:
        return None
    p = Path(path)
    digest = sha256_file(p) if p.is_file() else None
    return {"path": str(p), "sha256": digest} if digest else None


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def fact(label: str, value: Any, *evidence: dict | None) -> dict:
    refs = [e for e in evidence if e]
    if value is None or not refs:
        return {"label": label, "value": NO_EVIDENCE, "evidence": refs, "missing": True}
    return {"label": label, "value": value, "evidence": refs, "missing": False}


# ----------------------------------------------------------------------------------------------- sections

def find_app_dir(root: Path, app_dir: Path | None) -> Path | None:
    if app_dir:
        return app_dir if (app_dir / "MANIFEST.json").is_file() else None
    cands = sorted((p for p in root.glob("BOSSMAN-Windows-x64-*") if (p / "MANIFEST.json").is_file()),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0] if cands else None


def fetch_live(url: str, save_to: Path) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:  # noqa: S310 - localhost health endpoint
            raw = r.read(65536)
        data = json.loads(raw)
    except (OSError, ValueError):
        return None
    save_to.write_bytes(raw)
    return data


def section_identity(args, out: Path) -> dict:
    app = find_app_dir(args.app_root, args.app_dir)
    manifest = ev(app / "MANIFEST.json") if app else None
    m = read_json(app / "MANIFEST.json") if app else None
    live_file = out / "health-live.json"
    live = fetch_live(args.health_url, live_file) if not args.no_live else None
    live_ev = ev(live_file) if live else None
    m_sha = (m or {}).get("source_sha")
    l_sha = (live or {}).get("build_sha")
    facts = [fact("MANIFEST source_sha (installed folder)", m_sha, manifest),
             fact("MANIFEST source_dirty", (m or {}).get("source_dirty") if m else None, manifest),
             fact("/health/live build_sha", l_sha, live_ev),
             fact("/health/live source_identity", (live or {}).get("source_identity"), live_ev)]
    match = None if not (m_sha and l_sha) else ("PASS: build_sha == MANIFEST source_sha" if m_sha == l_sha
                                                else f"FAIL: live {l_sha} != manifest {m_sha}")
    facts.append(fact("live build == installed MANIFEST", match, manifest, live_ev))
    return {"title": "1. Идентичность установленной сборки", "facts": facts}


def seed_nodes(repo: Path) -> tuple[list[dict] | None, dict | None]:
    p = repo / SEED_REL
    d = read_json(p)
    nodes = d.get("nodes") if isinstance(d, dict) else None
    return (nodes if isinstance(nodes, list) else None), ev(p)


def section_tree(args) -> dict:
    nodes, seed_ev = seed_nodes(args.repo)
    facts = []
    if nodes is None:
        facts.append(fact("статусы дерева", None, seed_ev))
    else:
        counts = collections.Counter(str(n.get("status")) for n in nodes)
        facts.append(fact("всего узлов", len(nodes), seed_ev))
        for status, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            facts.append(fact(f"статус «{status}»", n, seed_ev))
    return {"title": "2. Дерево возможностей (capability_tree_seed.json)", "facts": facts}


def section_receipts(args, extra: list[Path]) -> dict:
    facts = []
    dirs = [args.repo / EVID_REL] + extra
    by_kind: collections.Counter = collections.Counter()
    verdicts: collections.Counter = collections.Counter()
    files_used: list[dict] = []
    for d in dirs:
        for f in sorted(d.glob("*.json")) if d.is_dir() else []:
            data = read_json(f)
            if not isinstance(data, list) or not data or not all(isinstance(r, dict) for r in data):
                continue
            recs = [r for r in data if "node_id" in r and "kind" in r]
            if not recs:
                continue
            files_used.append(ev(f))
            for r in recs:
                by_kind[str(r["kind"])] += 1
                verdicts[str(r.get("verdict"))] += 1
    if not files_used:
        facts.append(fact("квитанции", None))
    else:
        facts.append(fact("всего квитанций", sum(by_kind.values()), *files_used[:1]))
        for kind, n in sorted(by_kind.items()):
            facts.append(fact(f"kind={kind}", n, *files_used))
        for v, n in sorted(verdicts.items()):
            facts.append(fact(f"verdict={v}", n, *files_used))
    return {"title": "3. Квитанции по видам (evidence/*.json)", "facts": facts,
            "note": f"прочитано файлов с квитанциями: {len(files_used)}"}


_STARTED = re.compile(r"task (\w+) started \(([\w-]+), worker ([\w-]+)\)")


def parse_cycle_log(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    row: dict[str, Any] = {"log": ev(path), "name": path.stem}
    m = _STARTED.search(text)
    if m:
        row.update(task=m.group(1), case=m.group(2), worker=m.group(3))
    v = re.search(r"^SELF_REPAIR=(\S+)", text, re.M)
    row["verdict"] = v.group(1) if v else None
    body = None
    if v:
        brace = text.find("{", v.end())
        if brace >= 0:
            try:
                body, _ = json.JSONDecoder().raw_decode(text[brace:])
            except ValueError:
                body = None
    if isinstance(body, dict):
        row.setdefault("case", body.get("case"))
        row.setdefault("worker", body.get("worker"))
        row["model"] = body.get("model")
        row["stages"] = [k for k, ok in (body.get("stages") or {}).items() if ok]
        row["stages_failed"] = [k for k, ok in (body.get("stages") or {}).items() if not ok]
        row["holdout_base"] = body.get("holdout_base")
        row["holdout_patched"] = body.get("holdout_patched")
    else:
        row["detail"] = text.strip().splitlines()[-1][:200] if text.strip() else "пустой лог"
    return row


def section_cycles(dirs: list[Path]) -> dict:
    rows: list[dict] = []
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.log"), key=lambda p: (len(p.stem), p.stem)):
            if f.name.endswith(".err.log") or (f.parent == d and not (f.name.startswith("cycle") or d.name == "selfrepair")):
                continue
            rows.append(parse_cycle_log(f))
    passed = [r for r in rows if r.get("verdict") in ("INDEPENDENT_VERIFICATION_PASS", "TRANSFER_PASS")]
    facts = [fact("циклов (логов) прочитано", len(rows) if rows else None, *(r["log"] for r in rows[:1])),
             fact("из них INDEPENDENT_VERIFICATION_PASS / TRANSFER_PASS", len(passed) if rows else None,
                  *(r["log"] for r in passed))]
    return {"title": "4. Циклы самолечения", "facts": facts, "cycles": rows}


def section_ux(paths: list[Path]) -> dict:
    found = next((p for p in paths if p.is_file()), None)
    d = read_json(found) if found else None
    e = ev(found)
    facts = []
    if not isinstance(d, dict) or not isinstance(d.get("summary"), dict):
        facts.append(fact("матрица UX-проверки", None, e))
    else:
        for k, v in d["summary"].items():
            if isinstance(v, (int, float, str)):
                facts.append(fact(f"summary.{k}", v, e))
            elif isinstance(v, dict):
                facts.append(fact(f"summary.{k}", json.dumps(v, ensure_ascii=False), e))
        recs = d.get("records") or []
        bad = [r for r in recs if r.get("verdict") in ("DEAD", "ERROR")]
        facts.append(fact("записей DEAD/ERROR", len(bad), e))
    return {"title": "5. UX-проверка (ux-sweep.json)", "facts": facts,
            "ux_bad": [r for r in ((d or {}).get("records") or []) if r.get("verdict") in ("DEAD", "ERROR")][:50]}


_PYTEST = re.compile(r"(\d+) passed(?:, (\d+) failed)?[^\n]*")


def section_tests(dirs: list[Path]) -> dict:
    facts = []
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("regress-*.log")):
            lines = [ln for ln in f.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
            summary = next((m.group(0) for ln in reversed(lines) if (m := _PYTEST.search(ln))), None)
            facts.append(fact(f.name, summary, ev(f)))
    if not facts:
        facts.append(fact("запуски тестов", None))
    return {"title": "6. Запуски тестов (regress-*.log)", "facts": facts}


def section_rollback(args) -> dict:
    cands = sorted({*args.repo.glob("tools/*rollback*"), *args.repo.glob("tools/remove_bossman_tomorrow_tasks.ps1"),
                    *args.repo.glob("docs/owner/ROLLBACK*.md"), *args.repo.glob("tools/*restore*")})
    facts = [fact(p.name, "файл существует", ev(p)) for p in cands]
    return {"title": "7. Откат (скрипты и инструкции)", "facts": facts or [fact("скрипты отката", None)]}


def section_issues(args, cycles: dict, ux: dict, sections: list[dict]) -> dict:
    nodes, seed_ev = seed_nodes(args.repo)
    issues: list[dict] = []
    for n in (nodes or []):
        if n.get("status") == "blocked":
            issues.append(fact(f"узел заблокирован: {n.get('id')}", n.get("label"), seed_ev))
    for r in cycles.get("cycles", []):
        if r.get("verdict") not in ("INDEPENDENT_VERIFICATION_PASS", "TRANSFER_PASS"):
            issues.append(fact(f"цикл {r.get('name')} ({r.get('case')}, {r.get('worker')})",
                               r.get("verdict") or "без вердикта: " + str(r.get("detail", "")), r.get("log")))
    for r in ux.get("ux_bad", []):
        issues.append(fact(f"UX {r.get('verdict')}: {r.get('route')}/{r.get('label')}", r.get("detail"),
                           next((f["evidence"][0] for f in ux["facts"] if f["evidence"]), None)))
    for s in sections:
        for f in s["facts"]:
            if f["missing"]:
                issues.append({"label": f"нет данных: {s['title']} / {f['label']}", "value": NO_EVIDENCE,
                               "evidence": [], "missing": True})
    return {"title": "8. Открытые проблемы", "facts": issues or [fact("открытые проблемы", None)]}


# ----------------------------------------------------------------------------------------------- rendering

def short(h: str) -> str:
    return h


def render_md(model: dict) -> str:
    L = [f"# Bossman proof dossier — {model['date']}", "",
         f"Собрано: {model['built_at']}. Правило: без файла нет утверждения; нет файла — «{NO_EVIDENCE}».", ""]
    for s in model["sections"]:
        L += [f"## {s['title']}", ""]
        if s.get("note"):
            L += [s["note"], ""]
        for f in s["facts"]:
            refs = "; ".join(f"`{e['path']}` sha256:{short(e['sha256'])}" for e in f["evidence"]) or "—"
            L.append(f"- **{f['label']}**: {f['value']}  \n  источник: {refs}")
        if s.get("cycles"):
            L += ["", "| лог | кейс | исполнитель | вердикт | этапы пройдены | holdout base (fail/total) | holdout patched (fail/total) |",
                  "|---|---|---|---|---|---|---|"]
            for r in s["cycles"]:
                hb, hp = r.get("holdout_base") or {}, r.get("holdout_patched") or {}
                fmt = lambda h: f"{h.get('failed')}/{h.get('total')}" if h and h.get('total') is not None else NO_EVIDENCE  # noqa: E731
                L.append(f"| `{r['log']['path']}` sha256:{short(r['log']['sha256'])} | {r.get('case') or '?'} | "
                         f"{r.get('worker') or '?'} | {r.get('verdict') or NO_EVIDENCE} | "
                         f"{', '.join(r.get('stages') or []) or '—'} | {fmt(hb)} | {fmt(hp)} |")
        L.append("")
    return "\n".join(L) + "\n"


def render_html(model: dict) -> str:
    e = html.escape
    out = ["<!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
           f"<title>Bossman proof dossier {e(model['date'])}</title><style>",
           ":root{--bg:#fff;--fg:#1a1a1a;--mut:#666;--bad:#b00020;--line:#ddd}"
           "@media(prefers-color-scheme:dark){:root{--bg:#161616;--fg:#eee;--mut:#aaa;--bad:#ff6b81;--line:#333}}"
           "body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0 auto;max-width:1000px;padding:16px}"
           "h2{border-bottom:1px solid var(--line);padding-bottom:4px}code{font-size:12px;word-break:break-all}"
           ".m{color:var(--mut);font-size:12px}.bad{color:var(--bad);font-weight:600}"
           "table{border-collapse:collapse;width:100%;font-size:13px}td,th{border:1px solid var(--line);padding:4px;vertical-align:top}"
           ".w{overflow-x:auto}</style></head><body>",
           f"<h1>Bossman proof dossier — {e(model['date'])}</h1>",
           f"<p class='m'>Собрано {e(model['built_at'])}. Без файла нет утверждения; нет файла — «{NO_EVIDENCE}».</p>"]
    for s in model["sections"]:
        out.append(f"<h2>{e(s['title'])}</h2>")
        if s.get("note"):
            out.append(f"<p class='m'>{e(s['note'])}</p>")
        out.append("<ul>")
        for f in s["facts"]:
            val = f"<span class='bad'>{e(str(f['value']))}</span>" if f["missing"] else e(str(f["value"]))
            refs = "<br>".join(f"<code>{e(x['path'])}</code> sha256:<code>{e(x['sha256'])}</code>" for x in f["evidence"])
            out.append(f"<li><b>{e(f['label'])}</b>: {val}" + (f"<div class='m'>{refs}</div>" if refs else "") + "</li>")
        out.append("</ul>")
        if s.get("cycles"):
            out.append("<div class='w'><table><tr><th>лог</th><th>кейс</th><th>исполнитель</th><th>вердикт</th><th>этапы</th>"
                       "<th>holdout base</th><th>holdout patched</th></tr>")
            for r in s["cycles"]:
                hb, hp = r.get("holdout_base") or {}, r.get("holdout_patched") or {}
                fmt = lambda h: f"{h.get('failed')}/{h.get('total')} fail" if h and h.get('total') is not None else NO_EVIDENCE  # noqa: E731
                out.append(f"<tr><td><code>{e(r['log']['path'])}</code><br><code>{e(r['log']['sha256'])}</code></td>"
                           f"<td>{e(str(r.get('case') or '?'))}</td><td>{e(str(r.get('worker') or '?'))}</td>"
                           f"<td>{e(str(r.get('verdict') or NO_EVIDENCE))}</td><td>{e(', '.join(r.get('stages') or []) or '—')}</td>"
                           f"<td>{e(fmt(hb))}</td><td>{e(fmt(hp))}</td></tr>")
            out.append("</table></div>")
    out.append("</body></html>")
    return "".join(out)


def build(args) -> Path:
    out = args.out_root / f"proof-dossier-{args.date}"
    out.mkdir(parents=True, exist_ok=True)
    extra_ev = [Path(p) for p in args.extra_evidence]
    cycle_dirs = list(args.selfrepair_dir) + [Path(p) for p in args.extra_selfrepair]
    ident = section_identity(args, out)
    tree = section_tree(args)
    receipts = section_receipts(args, extra_ev)
    cycles = section_cycles(cycle_dirs)
    ux = section_ux([Path(p) for p in args.ux] + [args.repo / EVID_REL / "ux-sweep.json"] + [p / "ux-sweep.json" for p in extra_ev])
    tests = section_tests(list(args.test_dir) + extra_ev)
    rollback = section_rollback(args)
    issues = section_issues(args, cycles, ux, [ident, tree, receipts, cycles, ux, tests, rollback])
    model = {"date": args.date, "built_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
             "sections": [ident, tree, receipts, cycles, ux, tests, rollback, issues]}
    (out / "proof-dossier.md").write_text(render_md(model), encoding="utf-8")
    (out / "proof-dossier.html").write_text(render_html(model), encoding="utf-8")
    (out / "proof-dossier.json").write_text(json.dumps(model, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    ap.add_argument("--repo", type=Path, default=ROOT)
    ap.add_argument("--app-root", type=Path, default=DEFAULT_APP_ROOT)
    ap.add_argument("--app-dir", type=Path, default=None)
    ap.add_argument("--health-url", default="http://127.0.0.1:8801/health/live")
    ap.add_argument("--no-live", action="store_true", help="do not call /health/live (identity then has no live evidence)")
    ap.add_argument("--selfrepair-dir", type=Path, action="append", default=None)
    ap.add_argument("--extra-selfrepair", action="append", default=[])
    ap.add_argument("--extra-evidence", action="append", default=[])
    ap.add_argument("--test-dir", type=Path, action="append", default=None)
    ap.add_argument("--ux", action="append", default=[])
    args = ap.parse_args(argv)
    args.selfrepair_dir = args.selfrepair_dir or list(DEFAULT_SELFREPAIR)
    args.test_dir = args.test_dir or list(DEFAULT_TEST_DIRS)
    out = build(args)
    print(f"proof dossier: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
