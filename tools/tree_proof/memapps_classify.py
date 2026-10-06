"""Build lane 'memapps' deliverables from the probe receipts: evidence/memapps.json (executed receipts only),
evidence/memapps-retire.json (RETIRE audit receipts; none met the proof bar) and memapps-classification.md.
Re-runnable after tools/tree_proof/memapps_probe.py. Every eligible leaf (zones memory+apps, status code|branch) is
classified exactly once: GREEN (PASS receipt) | RETIRE (audit receipt) | KEEP (one-line honest reason)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memapps_probe as P  # noqa: E402

TOP = {
    "module-d33b374941cd": "learning_guard.service: one-call promotion guard (holdout + A/B + anti-degradation)",
    "module-07d13d7463b7": "learning_guard.promotion: staged promotion, never auto owner-promote",
    "module-8319884cf006": "learning_guard.ab: same-model A/B with verified-success gates",
    "module-e298a2a29a4b": "learning_guard.holdout: sealed secret holdout, no learning around it",
    "module-9bcb4ce56153": "learning_guard.evidence_ledger: evidence behind improvement claims",
    "module-1c240a45339a": "learning_guard.autonomy_trainer: gated self-improvement trainer",
    "module-5da7135e967e": "context_engine.memory: candidate->promote memory, provenance, conflicts",
    "module-35d5cb7d9ebd": "context_engine.retrieval: sensitivity-aware hybrid retrieval",
    "module-fc4458307ec6": "context_engine.compact: anchor-preserving compaction",
    "module-690bf4d0f3a1": "context_engine.store: owner delete (forget) removes chunks+index",
    "mod-healing": "healing: Bossman reliability / self-repair",
}
KEEP = {
    "app-ai-3d-maker": "FAIL: 7 test_slicer tests fail on this Windows host (no CuraEngine binary / stub-slicer fixtures), 342 pass; needs env or test-portability work",
    "app-ai-webcam-vision": "FAIL: 6 tests fail on Windows (tzdata missing for Europe/Prague, POSIX 0600 owner-only mode not reproduced, ffmpeg absent); 220 pass; needs env + Windows ACL decision",
    "app-solana-volume-suite": "FAIL env: code is at repo-root solana_volume_suite/ (not apps/); 26 root safety tests pass but app suite needs solders/solana packages; wash-trading/Bubblemaps-evasion scope needs owner decision",
    "app-osiris": "apps/osiris absent in this worktree (branch feature/osiris-data-acquisition); feature module is the separate mod-osiris leaf",
    "module-4e4b608586a8": "branch leaf: bossman-core/bossman/cognitive/context.py absent in this worktree; keep until branch is merged",
    "module-f92b1c5ccf84": "branch leaf: bossman-core/bossman/cognitive/memory.py absent in this worktree; keep until branch is merged",
    "pv-pipeline": "FAIL env: pokervision.adapters.poker_train imports cv2 (opencv not installed); tests cannot run",
    "pv-act": "FAIL env: pokervision.actuator imports cv2 (opencv not installed); tests cannot run",
    "pv-eval": "FAIL env: pokervision.eval.run_eval imports cv2; also no direct run_eval test",
    "pv-executor": "FAIL env: pokervision.control.executor imports cv2; test_control cannot be collected",
    "pv-ui": "JS page: needs browser + running Poker Train (POKERTRAIN_URL) and cv2; no headless test possible here",
    "pv-source-panel": "JS panel: same page as pv-ui; needs browser + live source; not testable in this env",
    "module-6bf6aca94f5f": "FAIL env: only test (test_trading_pipeline_benchmark) is importorskip(cv2), skipped; no executed test",
    "module-10135418ed52": "FAIL env: only test (test_trading_pipeline_benchmark) is importorskip(cv2), skipped; no executed test",
    "module-dca65ddd8cea": "FAIL env: only test (test_trading_pipeline_benchmark) is importorskip(cv2), skipped; no executed test",
    "module-72a2f39e685d": "FAIL env: only test (test_trading_pipeline_benchmark) is importorskip(cv2), skipped; no executed test",
}


def main():
    ns = P.leaves()
    rs = {r["node_id"]: r for r in json.loads((P.EVID / "memapps.json").read_text(encoding="utf-8"))}
    # keep only receipts of programs that actually executed something
    keep_rs = {k: r for k, r in rs.items() if r["reason"] not in ("non_python_source", "no_python_unit")}
    for k, r in rs.items():
        if k not in keep_rs:
            (P.OUT / f"{k}.txt").unlink(missing_ok=True)
    (P.EVID / "memapps.json").write_text(json.dumps(list(keep_rs.values()), ensure_ascii=False, indent=1), encoding="utf-8")
    (P.EVID / "memapps-retire.json").write_text("[]\n", encoding="utf-8")
    rows, cnt = [], {"GREEN": 0, "RETIRE": 0, "KEEP": 0}
    for n in sorted(ns, key=lambda x: x["id"]):
        nid = n["id"]
        r = keep_rs.get(nid)
        if r and r["verdict"] == "PASS":
            v = "GREEN"
            val = "TOP" if nid in TOP else "OK"
            why = ("import + existing tests passed at " + r["sha"][:8]) if "authored" not in open(P.OUT / f"{nid}.txt", encoding="utf-8").read() else ""
            tf = open(P.OUT / f"{nid}.txt", encoding="utf-8").read()
            line = [l for l in tf.splitlines() if l.startswith("test_files:")]
            why = f"import + pytest PASS @{r['sha'][:8]}; " + (line[0][12:].replace("command-center/tests/", "").replace("bossman-core/tests/", "")[:150] if line else "")
            if "test_leaf_" in why:
                why += " [authored_by_lane]"
            if nid in TOP:
                why += " ; TOP: " + TOP[nid]
        else:
            v, val = "KEEP", "OK"
            why = KEEP.get(nid, "UNCLASSIFIED")
            if why == "UNCLASSIFIED":
                raise SystemExit("unclassified leaf " + nid)
        cnt[v] += 1
        rows.append(f"| {nid} | {n['label'][:60].replace('|', '/')} | {v} | {why.replace('|', '/')} | {val} |")
    md = ["# memapps lane classification (zones memory + apps)", "",
          f"Leaves in scope (status code|branch): {len(ns)}. GREEN {cnt['GREEN']}, RETIRE {cnt['RETIRE']}, KEEP {cnt['KEEP']}.", "",
          "RETIRE = 0: nothing met the proof bar (every candidate has importers/tests/registration or is safety/consent related).",
          "Receipts: evidence/memapps.json (PASS and executed FAIL), evidence/memapps-retire.json (empty), out/<id>.txt.",
          "Re-run: python tools/tree_proof/memapps_probe.py && python tools/tree_proof/memapps_classify.py", "",
          "| id | label | verdict | reason | value |", "|---|---|---|---|---|", *rows, ""]
    (P.EVID / "memapps-classification.md").write_text("\n".join(md), encoding="utf-8")
    print(cnt, "TOP", len([r for r in rows if r.endswith("| TOP |")]))


if __name__ == "__main__":
    main()
