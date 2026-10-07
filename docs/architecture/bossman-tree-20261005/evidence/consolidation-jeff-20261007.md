# Consolidation for Jeff — 2026-10-07

Branch `consolidate/jeff-20261007` (worktree `C:\Users\asd\Bossman\wt-consolidate-1007`), started from
`green/tree-leaves-20261006` @ `01218fb2` (== `origin/integrate/bossman-2.1-one-20261006`). Not pushed.
Order: C, B, A (+ the answering machine found on disk), D.

## Decisions per source

| Source | Commit(s) | Decision | Reason / result |
|---|---|---|---|
| C origin/main | 8d82007e, 44a06f6b, 01d4561f, a302a404, 00a32d48 | **merged** (`a3ca2403`) | Direct/Assisted generation API + Direct Generator window + swarm docs. One conflict (tools/tree_self_repair_cycle.py), see below. swarm/merged-main-20261007 and swarm/direct-gen-20261007 are ancestors of origin/main: nothing extra. |
| B wt-calls-investor | eefc3705 | **merged with adjustment** (`f31d25a1`) | save_phone/saved_phone + encrypted full phone. Kept our DACL-safe `hardening.restrict_to_owner`; kept the `+••••1234` mask for the last4 fallback (the commit's `+***` would break the mask contract). Added a regression test (no callers exist yet in any branch). |
| B | d223e8d6 | skipped | README link to the 2 Oct audit — stale doc link. |
| A wt19-calls-s7 | a0087401 | already present | `bcc/auth.py` directory `(OI)(CI)F` grant + `hardening.restrict_to_owner` (358edacf); test ported in ab6d6d71 (our test is a superset). |
| A | 12db1299 | already present | ea558d2f (STOP/hangup win over dial, STOP flag replaced when unwritable) + 358edacf (`calls` plane in stop-all via `_calls_inventory`). |
| A | a6a1b03b | **partial** (`a3675a20`) | One prefix / aliases removed / panel browser acceptance already in 358edacf + ab6d6d71. Only a stale CLI comment about the removed `/api/calls` alias was missing. Its doc edits are superseded by our newer docs. |
| A | 41813595 | skipped (superseded) | our ACCEPTANCE.md (30.09, sections A–D, evidence levels) is newer and more detailed. |
| A | 35f7f4fe, 27163bb1 | already present | ports 20010a4c, 5d4c1342 (ours additionally resets the streamed draft and keeps the identity reminder last with the Jeff 2.0 notes). |
| A | fed588d8 | already present | 622da142 + 91931e5c (one Liquid/LFM policy via model_policy; denied local models dropped by `split_banned`). |
| A | 1f11e34e | already present | dc307168 (+ progressive-preview finalize in our line). |
| A+ wt-ace-answerer `claude/telegram-answering-machine` | 078a7fff (Codex, 2026-10-05) | **merged** (`3717ee13`) | **The answering machine for incoming calls** — not in the original inventory, found by the disk sweep (this branch = wt19-calls-s7 + 078a7fff). Emulator-verified, NOT live-tested. 13 conflicting files, see below. |
| D audit/motion-concert-20261006 | c01a16d9 | **merged** (`0d45ae15`, cherry-pick -x) | docs only. |
| D night/bossman-windows-bundle-20261006 | 90dd13fb, ca662c6f | skipped | 90dd13fb only adds an HTML comment to START_TOMORROW_RU.md so the Windows-bundle workflow builds that night branch (code = 6d9c1f56, already ours); ca662c6f merges ba2f7c77, already an ancestor of our line. |

## Disk sweep (worktrees/clones under C:\Users\asd\Bossman newer than 2026-10-03)

* Heads checked against our line: wt-ace-answerer (9 ahead -> 078a7fff taken), wt-ace-magic (11 ahead: coding-router benches, no calls/pit files — out of scope), worker-src (1 ahead, no calls/pit), bossman-2.0-completion, bossman-test, tomorrow-run, zone-apps/memory/plugins/ux, wt-verify-green, wt19-calls, wt-bugtest-0930 (all contained). wt-swarm-1007 and evo-tree-src not touched.
* `git log --all --since=2026-10-03` on telegram_calls / pit / telegram_companion, not in our line: only eefc3705, 078a7fff, ca662c6f (all handled above). Main-repo commits 98e6b073, 91931e5c, 6af9d0ca, ff30ebeb are already ours.
* **wt19-calls uncommitted "pit reactions" (2026-10-03)** — NOT taken: emoji reactions through a hard-coded `http://127.0.0.1:11434` + `bossman-community-qwen-uncensored` call per message (bypasses Jeff's model routing/policy), sticker mirroring, all errors swallowed, no tests, never committed; the bak folders are copies of the pre-edit files.
* **wt-bugtest-0930 uncommitted (2026-10-02..04)** — NOT taken: the same reactions code, a harsher `angry_today` preset ("Грубый"), and a call `custom_prompt` read from `settings.extra` and injected as a system message into the call brain (`extra` is documented as "never executed"); no tests, uncommitted. Owner decision needed if wanted.
* "Muse": `git log --all --grep/--author Muse` finds only afae766c (model-market llama.cpp route docs) and 2026-08 audits — no calls work under that name. The call improvements are Codex commits (wt19-calls-s7, wt-ace-answerer).

## Conflicts and resolution

* **C** `tools/tree_self_repair_cycle.py`: kept both — our `wire_worker()` and main's `CASES["discovery-none"]`.
* **B** `credentials.py::_write`: our `restrict_to_owner` helper + the new `phone` field.
* **078a7fff** (newer tested behaviour of our line preferred, no safety behaviour removed):
  * control_plane: armed-machine rule folded into our `_calls_inventory()`; duplicate inline `calls_plane()` dropped.
  * worker: our single-flight dial (`_dialing` / `_stop_epoch`) and bounded `op_stop` kept, with `answering.stop` / `wait_idle` added; `note_call_finished` stays guarded and is skipped for incoming calls; references to the removed `_dial_arming` / `_stop_requested` mapped to `_dialing` / the durable STOP flag.
  * manager: both new fields; doctor keeps our real voice/model rows + the new "Автоответчик" row.
  * session: AI-disclosure tracking kept around the uninterruptible greeting.
  * settings: `language` and the `answer_*` fields both kept.
  * speech factory / jeff_engines: `stopped=` and `answering=` both; English TTS model + call audit kept; answering summary prompt passed.
  * features/telegram_calls: settings lock = `mgr.busy` (+ `answer_greeting` allowed); our lag-safe STOP watcher + `_stop_guarded` + the armed-machine branch.
  * companion: `notify_zone_reports` and `notify_answering_reports` both run.
  * UI test import list: `answeringWords` and `EVENT_KINDS` both.
  * docs: our newer ACCEPTANCE / ARCHITECTURE / CONTINUE kept, answering-machine content appended (ACCEPTANCE section E, AM-1..AM-12 = source rows 16-27).
* No merge-fix commits were needed. LF only; `git diff --shortstat` == `--ignore-space-at-eol` for every commit.

## Tests (Windows, Python 3.12, run separately per directory)

| Step | Command | Result |
|---|---|---|
| C | command-center `pytest tests/test_direct_gen.py tests/test_direct_gen_client.py` | 24 passed |
| C | root `pytest tests/test_tree_self_repair_cycle.py` | 17 passed |
| C | command-center `pytest tests -k "telegram_calls or calls or pit or jeff or direct_gen or companion or plugin" -x` (ran concurrently with another pytest) | stopped at 1 failure: `test_direct_gen.py::test_cancel_queued_job_never_reaches_backend`; passes 5/5 alone and in the final run — timing race in the test under load (open issue 4) |
| B | command-center `pytest tests/telegram_calls` | 520 passed, 1 skipped |
| A+ | command-center `pytest tests/telegram_calls -k answering` | 213 passed, 1 skipped |
| A+ | `node ui/tests/telegram_calls.test.mjs` | 22/22 |
| final | command-center `pytest tests -k "telegram_calls or calls or pit or jeff or direct_gen or companion or plugin" --timeout=120` | **3042 passed, 7 skipped, 0 failed** (18 min 36 s) |
| final | command-center `pytest tests -k "control_plane or stop_all or direct_gen or credentials"` | 96 passed, 4 skipped |
| final | root `pytest tests/test_tree_self_repair_cycle.py tests/test_windows_owner_acceptance_kit.py tests/test_solana_safety.py` | 69 passed, 2 skipped |

## Open issues (reported, not papered over)

1. **The answering machine ignores the call `language` setting** (857d2758 English calls vs 078a7fff): greeting / closing / idle phrases are Russian constants (`DEFAULT_ANSWER_GREETING`, `CLOSING_TEXT`, SessionConfig defaults) while STT follows `language`. With `language=en` an incoming caller hears Russian. Needs a product decision (e.g. use `call_phrases`).
2. The answering machine is **emulator-only**; the py-tgcalls incoming path has never seen a real call (ACCEPTANCE AM-9..AM-12 BLOCKED / NOT_RUN, needs the owner login).
3. `save_phone` has no caller yet; after logout `phone_last4` is cleared but the saved full phone stays (public() then shows its mask). Decide whether logout should also forget the phone.
4. `test_cancel_queued_job_never_reaches_backend` (direct_gen) is timing-sensitive: under CPU load job A can be cancelled before it reaches the fake ComfyUI (`submitted == 0`). Product behaviour is correct; the test assumption is racy.
5. Not taken, owner decision: uncommitted pit reactions (wt19-calls, wt-bugtest-0930), the `angry_today` rewrite and the call `custom_prompt` (wt-bugtest-0930).
