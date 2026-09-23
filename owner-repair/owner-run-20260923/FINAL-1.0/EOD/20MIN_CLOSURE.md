# 20MIN_CLOSURE — 23.09.2026 (supersedes the "head = f72af6a8" statements in FINAL-1.0/REPORT_RU.md and EOD docs written before cb13c2fd)

| BLOCKER | BEFORE | FIX SHA | TEST | RESULT | INCLUDED IN CANDIDATE |
|---|---|---|---|---|---|
| C1 P0 snapshot `kind` path traversal (DB copy outside data dir) | reproduced by independent re-auditor; 8 FAIL | `cb13c2fd` | test_snapshot_kind_containment.py 9 pass; snapshot suites 31 pass | FIXED (independent re-attack on the fixed SHA: NOT_RUN) | YES |
| PDF false PASS (DOWNLOAD-FALSE-SUCCESS) | 14 FAIL | `c0a7e039` | test_download_false_success.py 27 pass (real Chromium) | FIXED_NEEDS_LIVE_RETEST | YES |
| computer.* agent contract (HW-02) | 6 FAIL | `aa6bee3d` | test_contract_keeps_agent_tools.py 15 pass; S2/STOP suites green | FIXED_NEEDS_LIVE_RETEST | YES |
| Full repo 74 MB > 32 MB coding evidence | 7 FAIL | `f72af6a8` | test_openhands_snapshot_bounds.py 9 pass; apprentice 144/18s | FIXED (limit not raised; streaming digests) | YES |
| Controlled apply candidate → project | route absent | `14392574` | test_coding_apply.py 17; neighbours (test_coding*, test_approval_*, f013, terminal cli) 155 pass; apprentice 144/18s — run by coordinator, logs in this session only | FIXED_NEEDS_LIVE_RETEST | YES |
| C2 opencode worktree created outside roots before 403 (`../` refuted by git; sibling `<proj>-<name>` leaks) | confirmed, low | — | — | OPEN (P1/P2) | NO |
| C3 `/api/terminal/roots` accepts any root (e.g. `C:\`), string stored as char list | confirmed; owner-token only, by design no approval | — | — | OPEN (P1 pending decision) | NO |
| Windows-100 real stress | fake print-loop on default branch | — | — | NOT_RUN (designed, not written) | NO |
| Exact-SHA CI / ZIP for new head | — | — | — | NOT_RUN | NO |
| SHA4 full regression failures (root 6, core 7+2E) | unclassified | — | — | OPEN | — |

Targeted regression on `cb13c2fd` (coordinator): command-center P0/P1/security/terminal 244 pass; bossman-core apprentice + S1/S2/S4/sibling/stage13 redteam 255 pass / 18 skip. Independent auditor on `cb13c2fd`: 68 + 9 pass (fix-specific files).

CURRENT_CANDIDATE_SHA = `cb13c2fdc9ef7c2555d5359c217908f920cbd321`
BRANCH = `fix/owner-run-20260923-p1` (pushed; release/bossman-owner untouched at `e0bf948d`, fast-forward possible)

P0_OPEN = 0 known (C1 fixed; no independent re-attack on cb13c2fd yet)
P1_OPEN = 2 (C2 worktree-before-containment, C3 terminal roots validation) + live retests pending
P2_OPEN = 20+ from owner run (BUGS.md)
P0_FIXED_NOW = 1 (C1) · P1_FIXED_NOW = 4 (download, computer.*, 32 MB, apply)
SECURITY = PARTIAL (S1–S4 regressions green on cb13c2fd; C2/C3 open)
CODING = FIXED in unit/integration tests; live not rerun
TERMINAL = cli unit/e2e green on cb13c2fd; TR-01…22 live last measured on SHA1–SHA3
WINDOWS = NOT_RUN on new head
CI = NOT_RUN on new head
NOT_RUN_DUE_TO_TIME = Windows-100, exact-SHA CI, ZIP, live TR/HW, Jev shadow on new head, full regression on new head, self-repair ×3
BLOCKED_EXTERNAL = HW-06 Telegram (one poller per bot token / owner action), ComfyUI (Smart App Control)
COMMITS_PUSHED = aa6bee3d, c0a7e039, f72af6a8, 14392574, cb13c2fd (fix/owner-run-20260923-p1); evidence on evidence/owner-run-20260923
UNPUSHED_CHANGES = none of value (local worktrees wt-l3-*, wt-eod-* hold only copies of pushed commits; wt-l4-stress clean)
READY_FOR_FINAL_CERTIFICATION = NO — C2/C3 open, full regression + Windows-100 + exact-SHA CI not run
NEXT_EXACT_COMMAND = see FINAL-1.0/CONTINUE.md, step 1
