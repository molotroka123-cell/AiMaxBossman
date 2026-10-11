"""MP21-A project-map generator: module inventory vs tests / docs / TODO / tree leaves.

Static analysis only (reads files, one `git log`); runs no tests. A row is `works`
only when an executed test file (EXECUTED_TESTS, with rc recorded in
docs/owner/runs/MP21_A_CHECKS_20261011.md) imports the module; every other row is
capped at `partial`. "tests refs" counts test files that merely contain the name
as a word, so generic names ("tools", "config") over-count.

  python tools/mp21_project_map.py > map_tables.md
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXECUTED_TESTS = [
    "test_offline_mode.py", "test_computer_stop_race.py", "test_computer_use_tools.py",
    "test_computer_verify_and_approval.py", "test_action_contract_verified_computer_launch.py",
    "test_queue_retry.py", "test_autonomy_journal.py", "test_jeff_queue_survival.py",
    "test_rc19_model_recovery.py", "test_fence_event_cancellation.py", "test_local_only_switch.py",
    "test_openrouter_provider_isolation.py", "test_p1_link_local_encodings.py",
    "test_capability_tree.py", "test_tree_apply_evidence.py",
]
WORD = re.compile(r"\w+")


def git(*a: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


def main() -> None:
    files = [f for f in git("ls-files").split("\n") if f]
    texts: dict[str, str] = {}
    for f in files:
        is_test = f.endswith(".py") and (f.startswith(("command-center/tests/", "tests/", "bossman-core/tests/")))
        is_doc = f.endswith(".md") and (f.startswith("docs/") or "/" not in f)
        if is_test or is_doc:
            try:
                texts[f] = (ROOT / f).read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
    tests = {k: v for k, v in texts.items() if k.endswith(".py")}
    docs = {k: v for k, v in texts.items() if k.endswith(".md")}
    test_tokens = {k: set(WORD.findall(v)) for k, v in tests.items()}
    doc_tokens = {k: set(WORD.findall(v)) for k, v in docs.items()}
    executed_text = "\n".join(v for k, v in tests.items() if Path(k).name in EXECUTED_TESTS)
    seed = json.loads((ROOT / "command-center/bcc/capability_tree_seed.json").read_text(encoding="utf-8"))["nodes"]
    leaf_by_path: dict[str, list[str]] = {}
    for n in seed:
        for s in n.get("sources") or []:
            leaf_by_path.setdefault(s["path"], []).append(n["id"])
    last: dict[str, str] = {}
    cur = ""
    for line in git("log", "--since=2026-08-01", "--format=@%cs", "--name-only").split("\n"):
        if line.startswith("@"):
            cur = line[1:]
        elif line and line not in last:
            last[line] = cur

    def row(kind: str, name: str, paths: list[str]) -> str:
        loc = 0
        todo = 0
        for p in paths:
            try:
                t = (ROOT / p).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            loc += t.count("\n") + 1
            todo += len(re.findall(r"\b(?:TODO|FIXME|XXX)\b", t))
        if re.fullmatch(r"\w+", name):
            nt = sum(1 for v in test_tokens.values() if name in v)
            nd = sum(1 for v in doc_tokens.values() if name in v)
        else:
            nt = sum(1 for v in tests.values() if name in v)
            nd = sum(1 for v in docs.values() if name in v)
        leaves = sorted({lf for p in paths for lf in leaf_by_path.get(p, [])})
        lc = max([last.get(p, "") for p in paths] or [""]) or "<2026-08-01"
        k = re.escape(name)
        strict = re.compile(
            r"bcc[./](?:features[./])?" + k + r"\b"
            r"|from bcc(?:\.features)? import [^\n]*\b" + k + r"\b"
            r"|ui[/.]pages[/.]" + k + r"|tools[/.]" + k + r"\b")
        if kind == "ui-page":
            hit = re.search(r"ui[/.]pages[/.]" + k, executed_text)
        else:
            hit = strict.search(executed_text)
        if hit and len(paths) > 1:
            st = "partial (package; at least one module is exercised by executed tests)"
        elif hit:
            st = "works (executed tests import it, rc=0)"
        elif nt:
            st = "partial (tests mention the name; not executed here)"
        elif loc:
            st = "partial (code only, no test reference)"
        else:
            st = "missing"
        return f"| {kind} | `{name}` | {loc} | {nt} | {nd} | {todo} | {len(leaves)} | {lc} | {st} |"

    out = ["| kind | item | LOC | tests refs | docs refs | TODO | tree leaves | last commit | status |",
           "|---|---|---:|---:|---:|---:|---:|---|---|"]
    bcc = "command-center/bcc/"
    pk: dict[str, list[str]] = {}
    for f in files:
        if f.startswith(bcc) and f.endswith(".py"):
            parts = f[len(bcc):].split("/")
            if len(parts) == 1:
                pk.setdefault(parts[0][:-3], []).append(f)
            elif parts[0] == "features":
                pk.setdefault("features." + parts[1].removesuffix(".py"), []).append(f)
            else:
                pk.setdefault(parts[0] + "/", []).append(f)
    for k in sorted(pk):
        if k != "__init__":
            out.append(row("bcc", k.rstrip("/").split(".")[-1], pk[k]))
    for f in sorted(files):
        if f.startswith("command-center/ui/pages/") and f.endswith(".js"):
            out.append(row("ui-page", Path(f).stem, [f]))
    tl: dict[str, list[str]] = {}
    for f in files:
        if f.startswith("tools/") and f.endswith((".py", ".ps1")):
            parts = f.split("/")
            tl.setdefault(parts[1].rsplit(".", 1)[0] if len(parts) == 2 else parts[1] + "/", []).append(f)
    for k in sorted(tl):
        out.append(row("tool", k.rstrip("/"), tl[k]))
    print("\n".join(out))


if __name__ == "__main__":
    sys.exit(main())
