"""Lane opsplug: deterministic RETIRE audit for a dead/duplicate leaf.

    python tools/tree_proof/opsplug_retire.py <node_id> <repo-relative source path> <importable word>

Exits 0 only when ALL hold (so a retire receipt can never be written for a live module):
  * the word is not referenced as a whole word anywhere in tracked files except the leaf's own file, its own
    tests, the capability-tree seed/export and the evidence folder (git grep -w);
  * the source file is either absent from HEAD with a deletion commit in HEAD's history, or present but unreferenced;
  * the path/word are not security/safety/permission/approval/budget/secret/backup/restore related.
The full evidence (git history + grep result) is printed to stdout for evidence/out/<node_id>.txt.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROTECTED = re.compile(r"secur|safe|permission|approval|approv|budget|secret|backup|restore|vault|credential|auth|guard|policy|gate",
                       re.I)
EXCLUDE = [":(exclude)docs/architecture/bossman-tree-20261005", ":(exclude)command-center/bcc/capability_tree_seed.json",
           ":(exclude)docs/architecture/tree-registry.json", ":(exclude).pytest_cache"]


def git(*args):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout.strip()


def main() -> int:
    nid, path, word = sys.argv[1:4]
    print(f"node_id: {nid}\nsource: {path}\nword: {word}")
    if PROTECTED.search(path) or PROTECTED.search(word):
        print("REFUSED: security/safety/permission/approval/budget/secret/backup/restore-related names are never retired")
        return 2
    exists = (ROOT / path).is_file()
    rc, log = git("log", "--oneline", "-n3", "HEAD", "--", path)
    print(f"\n$ git log --oneline -n3 HEAD -- {path}\n{log or '(no commits in HEAD ancestry)'}")
    rc, dels = git("log", "--diff-filter=D", "--oneline", "-n1", "HEAD", "--", path)
    print(f"\n$ git log --diff-filter=D --oneline -n1 HEAD -- {path}\n{dels or '(never deleted in HEAD ancestry)'}")
    print(f"\nfile_present_in_worktree: {exists}")
    own = re.compile(r"(^|/)(test_)?(leaf_)?" + re.escape(Path(path).stem) + r"(_.*)?\.py$")
    rc, out = git("grep", "-nwI", word, "--", ".", *EXCLUDE)
    hits = [ln for ln in out.splitlines() if not own.search(ln.split(":", 1)[0])]
    print(f"\n$ git grep -nwI {word} -- . <exclude seed/evidence>\n" + ("\n".join(hits) if hits else "(no references outside own file/tests)"))
    if hits:
        print("\nVERDICT: NOT_DEAD (still referenced)")
        return 1
    if not exists and not dels:
        print("\nVERDICT: INCONCLUSIVE (absent but no deletion record)")
        return 1
    print("\nVERDICT: DEAD (no references; " + ("deleted in history as duplicate" if dels else "present but unreferenced") + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main())
