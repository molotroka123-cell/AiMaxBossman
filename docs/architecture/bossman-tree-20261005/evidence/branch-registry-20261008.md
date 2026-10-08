# Реестр полноты веток — 08.10.2026

Сравнение каждой ветки GitHub с линией `integrate/bossman-2.1-one-20261006` по patch-id (`git cherry`).
Источник SHA линии — в последнем комментарии PR #98 (файл не может содержать SHA своего коммита).
«0 уникальных» = все изменения ветки уже есть в линии под другим SHA. Вердикты с текстом — разбор агентами
реестра (что в ветке, есть ли в линии, что делать). Ничего не сливалось механически.

`LOCAL_COMPLETENESS=UNVERIFIED`: локальные копии, stash и незапушенные коммиты на ПК владельца отсюда не видны.
Собрать: `python tools\local_completeness_manifest.py --root %USERPROFILE%\Bossman --out <файл>.json`.

Итог: FULLY_INTEGRATED (patch-id) — 135, НЕ РАЗОБРАНА — 57, HISTORY_ONLY — 23, FULLY_INTEGRATED — 23, PARTIALLY_INTEGRATED — 18, NOT_INTEGRATED — 13, ОСНОВА ЛИНИИ — 3

| Дата | Ветка | Tip | Уник. | Вердикт | Что внутри / решение |
|---|---|---|---|---|---|
| 2026-10-08 | `integrate/bossman-2.1-one-20261006` | e63e9d81 | 0 | ОСНОВА ЛИНИИ | влита целиком (merge) |
| 2026-10-08 | `main` | 278f27d2 | 0 | ОСНОВА ЛИНИИ | влита целиком (merge) |
| 2026-10-07 | `green/tree-leaves-20261006` | 4a14c26c | 0 | ОСНОВА ЛИНИИ | влита целиком (merge) |
| 2026-10-07 | `swarm/direct-gen-ui-20261007` | 66ac57cb | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-10-07 | `swarm/merged-main-20261007` | a302a404 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-10-07 | `swarm/selfrepair-20261007` | 01d4561f | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-10-07 | `swarm/direct-gen-20261007` | 44a06f6b | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-10-06 | `audit/motion-concert-20261006` | c01a16d9 | 1 | PARTIALLY_INTEGRATED | One docs commit. BENCHMARK_REPORT.md is byte-identical (blob 3aa08f80) to tools/motion_concert/BENCHMARK_REPORT_20261006.md in the candidate. AUDIT.md, animate_comfyui.log, animate_test_results.json and concert_qc.json exist only on the branch. Their key facts (the Animate-14B fp8 load crash, the Demucs vocal-stem plan, the Ollama handshake fix 916f7c5e) are reflected in candidate runbooks and history. |
| 2026-10-06 | `zone/ux-20261006` | 95b7708f | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-10-06 | `zone/plugins-20261006` | 33023f2f | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-10-06 | `zone/memory-20261006` | e9dff0cc | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-10-06 | `zone/apps-20261006` | a2cde5e6 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-10-06 | `fix/verifier-pytest-runtime-20261006` | 7b433b92 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-10-06 | `goal/bossman-self-improvement-tree-20261005` | d3fd6bcd | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-10-06 | `night/bossman-windows-bundle-20261006` | ca662c6f | 1 | HISTORY_ONLY | Its only patch-unique commit, 90dd13fb, adds one HTML comment to START_TOMORROW_RU.md so the path filter would trigger the Windows bundle workflow. That commit is the build identity of artifact BOSSMAN-Windows-x64-90dd13fb92c4.zip. The built code (6d9c1f56) and the run audit (ba2f7c77) are ancestors of 4e08a314. docs/audits/2026-10-06-windows-artifact-90dd13fb.md:11-23 records the SHA and PASS. The tip ca662c6f is… |
| 2026-10-06 | `lab/agent-modal-race` | 12e003e7 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-10-06 | `poker-vision/pipeline-20261005` | 96cec27e | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-10-06 | `poker-vision/skips-registry-20261006` | 6bc5de51 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-10-06 | `poker-vision/bundle-packaging-20261006` | 2c9e25ca | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-10-06 | `poker-lora/hu-nlhe-20261006` | 974e5e73 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-10-06 | `poker-vision/coach-executor-20261006` | 870cac29 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-10-05 | `docs/bossman-capability-tree-20261005` | 67016be0 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-10-04 | `feat/tencent-hy-image-20261004` | aa544619 | 15 | HISTORY_ONLY | The branch name is misleading: the diff from merge-base 0c3e22ff to the tip has 28 files, all model-market, owner-audit or CLAUDE/AGENTS files, and no Tencent/HunyuanImage code. 12 of its 15 patch-unique commits are shared with research/model-market-20261004 and are covered by those rows. The 3 own commits (provenance, Qwen3.6 isolated validation, public benchmark crosswalk) are evidence. The candidate pins this t… |
| 2026-10-04 | `research/model-market-20261004` | afae766c | 12 | HISTORY_ONLY | 12 patch-unique commits. Six are the Aster/GLM53 owner-audit checkpoints from 2026-09-21, also on audit/owner-aster-20260921. Their P1 finding (approvals missing after restart with ?status=all) is fixed in the candidate by 84cbd788. One adds an owner-local CLAUDE.md/AGENTS.md guardrail file, deferred to the owner. Five are the 2026-10-04 local model-market tournament report, harness and evidence; none of it is in … |
| 2026-10-03 | `audit/k1m6a-training-20261003` | e29fa36b | 4 | NOT_INTEGRATED | порт: субтитры не теряются при сбое yt-dlp (+2 теста); отчёты #23–#25. 4 patch-unique commits; none of their unique content is in the candidate. One small real fix with a regression test is ready to port now: download_subtitles keeps caption tracks already saved when yt-dlp fails on a later language. The new K1m6a study tools (smart frames, incremental vision review, lesson extraction), their tests, the owner deep-study doc section and the 24h gap-study plan need an owner decision. T… |
| 2026-10-03 | `handoff/continuation-20260929` | f9028003 | 2 | FULLY_INTEGRATED | 2 patch-unique snapshot commits (3,240 added files under docs/owner/snapshot-20260929/ plus 2 pointer lines in CONTINUATION_PROMPT_20260929.md). 3,215 of the snapshot files are byte-identical to files in the candidate. The other 25 are older versions of 7 docs that exist in the candidate in evolved form. Nothing is lost. |
| 2026-10-03 | `evidence/owner-run-20261001` | fa96945c | 3 | PARTIALLY_INTEGRATED | порт: BUTTONS_SWEEP_20261001.md (на него ссылается тест). An orphan history: root 495ff73a has no parent. Of 7 commits, 3 are patch-unique. FINAL_REPORT_20261001.md (final version) and HANDOFF_20261001.md are byte-identical to docs/owner/runs/AUDIT_20261001.md and docs/owner/HANDOFF_20261001.md in the candidate. BUTTONS_SWEEP_20261001.md is the provenance doc that the candidate test command-center/tests/test_buttons_sweep_1001.py:1 cites at a path that does not exist, so… |
| 2026-10-03 | `integrate/bossman-1.7-unified-20260925` | f13ae123 | 6 | PARTIALLY_INTEGRATED | 6 patch-unique docs commits. The README 'Oct 2 audit' link is present in the candidate through 7061b73c (README.md:3). The formatting restore fixed breakage that never reached the candidate. The Viggle doc is superseded by the candidate's more detailed spec (975691f9). The Qwen3.8 'not installed / NOT RUN' README section and doc contradict the 09-21 Aster audit and the 10-04 tournament: retire them. CURRENT_AUDIT_… |
| 2026-10-03 | `claude/bossman-1.9-owner-bugtest-20260930` | 0ec2ff52 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-10-03 | `feat/bossman-1.9-final-freeze` | e4539337 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-10-03 | `feat/bossman-1.9-freeze-20260929` | 017d5fe7 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-10-03 | `candidate/freeze-20260926` | 4387de7a | 1 | FULLY_INTEGRATED | One patch-unique commit (29f7a7dd) adds the README link to the Oct 2 owner audit. The candidate has the same link, with the same target URL, through 7061b73c at README.md:3. |
| 2026-10-03 | `claude/bossman-control-v03-43igbk` | 2c14c75a | 21 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-10-03 | `ux/ai-3d-maker-adapter-20261004` | 2c14c75a | 21 | PARTIALLY_INTEGRATED | Despite its name, the 21 patch-unique commits contain no 3D-maker adapter code. They are old default-branch history (CASE-002/003 raw trading logs, a Sonnet VIP-demo prompt, a fake Windows stress workflow, a root-release-ci experiment, README pointers) plus 2.1 operator docs. The 2.1 operator doc (with OpenDots) and the Oct-2 README audit link are in 4e08a314. The CI experiments are superseded by stricter workflow… |
| 2026-10-03 | `docs/voice-background-effects-spec-20260927` | 8d13c44f | 7 | NOT_INTEGRATED | 7 patch-unique docs commits (PR #85). The chat-first UX prototype and training spec are superseded by the real chat surface command-center/ui/chat.html (02cd7181). The voice MP3-upload UX draft, the background sound-effects draft, the Jeff Elite voice spec and the web-design prompt-library plan are unimplemented feature specs that need an owner decision. The 2026-09-27 master prompt and branch-map refreshes are hi… |
| 2026-10-03 | `feat/bossman-1.8-unrestricted-motion-studio` | 3c449dff | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-10-03 | `claude/telegram-live-calls-ah9gwl` | d223e8d6 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-10-03 | `claude/bossman-freeze-closure-ohvmon` | 68a65803 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-10-03 | `feat/bossman-autonomy` | 12b62415 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-10-03 | `claude/busy-carson-ydh991` | 0b30b923 | 22 | PARTIALLY_INTEGRATED | 22 patch-unique commits. 17 are shared with ux/ai-3d-maker-adapter-20261004 and are covered by those rows. Of its own 5: the README Oct-2 audit link is integrated (7061b73c). The self-improvement gap analysis and the Codex master prompt are absent and deferred to the owner: they plan new features, and the prompt says main is historical, which contradicts the 2026-10-08 main-canonical amendment. |
| 2026-09-29 | `feat/bossman-autonomy-b` | 4c770578 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-29 | `feat/bossman-autonomy-c` | 30948baf | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-29 | `wip/cv-e-20260929` | aeff89c1 | 1 | FULLY_INTEGRATED | A stash snapshot with an empty index commit. Its worktree change (live-catalog presence marks withdrawn models 'unavailable'; auto-selection skips them) is in the candidate through e20f3688, with test_catalog_presence.py. |
| 2026-09-29 | `wip/cv-d-20260929` | abbb5d87 | 1 | FULLY_INTEGRATED | A stash snapshot with an empty index commit. Its runtime.py change (_generation_resumable_after_restart) is in the candidate through 38232bfe, covered by test_pit_generation_recovery.py. |
| 2026-09-29 | `wip/motion56-20260929` | a1593fe5 | 1 | NOT_INTEGRATED | порт: LF-запись в build_library.py; генератор 56 кандидатов — владельцу. A stash snapshot with an empty index commit. The worktree holds the 56-candidate Motion Studio generator, its tests and a doc hunk, none of which is in 4e08a314. Candidate docs record it as 'not ported' and give porting steps. The feature needs an owner decision. The small LF-write fix inside it can be ported now. |
| 2026-09-29 | `wip/autonomy-b-20260929` | 0ff02b4f | 1 | FULLY_INTEGRATED | Tip is a git stash (0ff02b4f, parents 40d30f6d + index 85c6a3c1). Its only patch-unique commit is the empty stash index. The stashed WIP for autonomy-b cycle/fakes is an older version of code that the candidate later rewrote (4c770578, 6b511b47). Nothing unique is left to port. |
| 2026-09-29 | `wip/autonomy-c-20260929` | da91ac0d | 1 | FULLY_INTEGRATED | A stash snapshot (index 23d44020 plus worktree) whose tree is byte-identical to candidate commit 30948baf (git diff da91ac0d 30948baf is empty). Later candidate commits 38232bfe and 1eb61ae4 extend it. |
| 2026-09-29 | `feat/bossman-autonomy-a` | a00ed185 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-29 | `fix/k1m6a-discovery-limit` | 872bfb1b | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-29 | `feat/bossman-autonomy-funding` | 54de1545 | 1 | NOT_INTEGRATED | A 9-file financing-application package (NOT_SUBMITTED), absent from the candidate. Applications to third parties are owner-only decisions, and its 'measured' claims cite older SHAs. Deferred to the owner. |
| 2026-09-29 | `feat/jeff-2.0` | 2ff3ab79 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-29 | `feat/jeff-2.0-x` | d876f4c8 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-29 | `feat/jeff-2.0-z` | 1d344fd6 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-29 | `feat/jeff-2.0-y` | 966fdb34 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-29 | `feat/bossman-perf-2.0` | 79cbecba | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-29 | `feat/jeff-1.8` | 0c9a219e | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-29 | `claude/bossman-1.9-final-freeze-cert` | 0139fe27 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-29 | `feat/jeff-1.1` | 98afc075 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-29 | `feat/master-parser-2.0` | 2b8baa4c | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-29 | `feat/jeff-1.5` | b9eda199 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-29 | `claude/telegram-live-calls-s7-work` | fed588d8 | 7 | FULLY_INTEGRATED | All 7 commits are present by meaning. a6a1b03b and a0087401 were ported in ab6d6d71 and 358edacf. 12db1299 was cherry-picked as ea558d2f. The Jeff self-disclosure and LFM-denial fixes are 20010a4c, 5d4c1342, 622da142 and 91931e5c. The older ACCEPTANCE table is superseded by the 2026-09-30 ACCEPTANCE.md from 358edacf. |
| 2026-09-29 | `cv/j` | 49e9f943 | 4 | FULLY_INTEGRATED | 4 Jeff fixes (LFM denial, speaking only when asked, model-reply self-disclosure, red-team pass) are all in the candidate as 622da142 (+91931e5c), 728269ee (+a2581f40 LF restore), 20010a4c and 5d4c1342. |
| 2026-09-29 | `calls19` | 1bde3130 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-29 | `scratch/root-ci-debug-19` | 3bebb613 | 2 | HISTORY_ONLY | Two scratch commits that add DEBUG assertion messages to a test. The candidate has the real, documented test (tests/test_installed_ui_sweep.py:225). Retire. |
| 2026-09-29 | `a19/streaming` | 18eab129 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-29 | `tgcalls-work` | 5c681d5b | 4 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-29 | `tgcalls/agent-d-20260929` | 5c681d5b | 4 | HISTORY_ONLY | A parallel 'Line D' Telegram-calls implementation built on the 1.0 base (separate speech/brain.py, stt.py, tts.py, addons.py, doctor.py, terminal_cli/call.py). The candidate delivers every function through its own Jeff-based line (84fb2e92, dd819e51, 8b4ce169, 62c0edcb, 358edacf, ea558d2f). The branch is history, not a port source. |
| 2026-09-29 | `a19/runtime2` | b4f43539 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-29 | `c19/verify` | e606e176 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-29 | `b19/jeff-ux` | 5f2e4476 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-29 | `a19/runtime` | ddd9b055 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `cv/b` | e92b6d73 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `rc19/t-jeff-admin` | 970dbe70 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `cv/e` | e94a79e9 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `rc19/integration` | 235a6f54 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `cv/g` | ad542552 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `cv/h` | 3d79eca3 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `cv/d` | 7bc8d053 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `cv/f` | 53dae07d | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `cv/i` | bfda8a38 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `cv/a` | 02f2d627 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `feat/jeff-ux-integration-test-20260926` | cc946e13 | 6 | PARTIALLY_INTEGRATED | Jeff UX files (identical blobs in candidate history) and the NVIDIA vendor/Quick Test fix are integrated. The paid cloud video and Telegram send scripts are deferred to the owner. night_send_as_jeff.py is retired as unsafe. The smoke result and continuation handoff docs stay on the branch. |
| 2026-09-28 | `claude/rc19-audit-integration` | 612b2f7c | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `rc19/r-jeff-next` | 3573d58d | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `rc19/o-models` | 7828fcaa | 1 | PARTIALLY_INTEGRATED | A WIP checkpoint whose tests were never confirmed green. The candidate reimplemented its core differently: Ollama reasoning off (e94a79e9), llm_vision only with references (e952ca23), catalog presence/unavailable (e20f3688) and model recovery (ca002267). Absent and deferred: wrong-service health for local ports, periodic local/catalog probes, live-catalog router pricing, hiding thinking in CMD, and removal of the … |
| 2026-09-28 | `rc19/m-ip` | 1ae271ea | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `rc19/q-parser` | dc63a36f | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `rc19/h-3d` | 11ec4a6a | 9 | NOT_INTEGRATED | voxel3d high-poly, neural (TripoSR) and prompt-to-model work, plus the game-3d-model skill. All absent from the candidate. The RC19 audit lists the Bossman-vs-Claude evaluation as unfinished. Deferred to the owner, together with the p-green voxel3d base. |
| 2026-09-28 | `rc19/p-green` | dadfc525 | 12 | PARTIALLY_INTEGRATED | GREEN-LIGHT integration branch. Its Agentic Rave and Motion Studio merges are in the candidate, and so is the rave skip-registry row. The human-like collector (bcc/collector, 45 tests) and the voxel3d pipeline are absent and deferred to the owner. One doc hunk (stale AGENTIC_RAVE.md status) is worth porting. |
| 2026-09-28 | `rc19/n-self` | 73049233 | 3 | NOT_INTEGRATED | Learning 24/7 goal reports to the owner's пульт, plus owner-approved lesson promotion (goal_reporter, lesson_pipeline, owner_notify, start_learning_247.ps1). None of it is in the candidate. It overlaps the candidate's bcc/autonomy loop and relaxes a companion approval check, so it is deferred to the owner. |
| 2026-09-28 | `rc19/k-one` | ace1827d | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `claude/rc19-audit-learn` | c3de3364 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `claude/rc19-audit-video` | 7c07a115 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `claude/rc19-audit-cu` | 57750901 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `rc19/post-rc` | 18115e20 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `rc19/r-jeff-next-bugs` | 18115e20 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `rc19/i-collector` | 1cedac56 | 1 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-28 | `claude/rc19-audit-sec` | f6a2d73a | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `claude/rc19-audit-jeff` | c0ba48fd | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `claude/rc19-audit-life` | 0e20d08a | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `claude/rc19-audit-models` | 071a62da | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `claude/rc19-full-audit-20260928` | 4fe5a184 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `rc19/integration-pre-rebase` | e39455fc | 1 | FULLY_INTEGRATED | One CI commit makes the owner journeys importorskip in root-ci and runs them in command-center-ci. The candidate replaced it with 9cbf680a: dependency-specific importorskips plus a core-runtime step at command-center-ci.yml:185-186. |
| 2026-09-28 | `rc19/c-jeff` | c0ba41a9 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `rc19/d-learn` | b47d31fa | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `rc19/f-ux` | 0cdcef63 | 2 | HISTORY_ONLY | Two 20,000-record test-period session journals. Historical evidence only. |
| 2026-09-28 | `rc19/a-freeze` | 9e5a9c19 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `rc19/g-rave` | 38d9892a | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `codex/motion-animation-56-20260928` | ef1c3909 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `rc19/e-motion` | ef1c3909 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `rc19/s-motion` | ef1c3909 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-28 | `rc19/b-cu` | 48037446 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-27 | `feat/bossman-local-voice-clone-20260928` | 3cd609f9 | 2 | FULLY_INTEGRATED | The voice-clone adapter, worker, profile and latency-gate doc were converged by 31398e4f. The sendVideo strict-receipt hunk was deliberately not carried because it is wrong for silent MP4 and breaks 8 contracts; it is recorded as backlog in VOICE_AND_PHONE.md:72. |
| 2026-09-26 | `docs/bossman-1.8-oss-14x2-20260926` | c7644f82 | 1 | NOT_INTEGRATED | A 1.8 open-source reference map (14 directions x 2 repos) and a link to it from BOSSMAN_18_EVOLUTION_ENGINE.md. Absent from the candidate. Small, docs-only, worth porting. |
| 2026-09-26 | `fix/gitignore-utf8-20260926` | 2b687eef | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-26 | `feat/jeff-ux-voice-avatar-20260926` | 025834d6 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-25 | `feat/bossman-1.7-personal-identity-training-20260925` | c3776834 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-25 | `feat/bossman-1.6-bossnet-foundation-20260925` | 1c1f750c | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-25 | `feat/bossman-1.5-economy-orchestrator-20260924` | fa594b7c | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-24 | `feat/bossman-1.6-self-improvement-20260925` | bf4ceb8a | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-24 | `feat/bossman-1.6-self-evolution-20260925` | e574b497 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-24 | `feat/bossman-1.6-secret-intake-20260925` | 7eb5c592 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-24 | `release/bossman-1.5-rc2-20260925` | 21a8092b | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-24 | `release/bossman-1.5-rc-20260925` | e329a010 | 1 | HISTORY_ONLY | Historical 1.5 RC freeze pin (config/v1.5/release-candidate.json, source_sha 5d7fbcef). Not applicable to the 2.1 candidate. |
| 2026-09-24 | `feat/bossman-1.5-economy-learning-20260924` | 71f59159 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-24 | `feat/v15-autonomy-core-20260924` | ae2f94fa | 1 | FULLY_INTEGRATED | The v1.5 autonomy core code and test have identical blobs at the candidate (251144b8). The CI paths, README section and OSS reuse doc are present; the candidate's doc is a superset. |
| 2026-09-24 | `feat/market-levels-multihorizon-youtube-20260924` | c9bf4239 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-24 | `feat/jev-twitch-collector-20260924` | 90a807b0 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-24 | `release/bossman-owner` | 90a807b0 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-24 | `release/bossman-1.0-cert5-rerun` | 0f0d138c | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-24 | `test/aster-owner-twitch-20260924` | a79981c9 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-24 | `claude/bossman-1-0-exact-20260924` | c489cc62 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-24 | `evidence/owner-run-20260924` | 499d9dc2 | 19 | HISTORY_ONLY | Owner-run evidence from 23-24.09: FINAL-1.0 EOD and certification docs, OR0923 CLI/UI checkpoints, the SwapMe creative run, the Jev activation handoff and 0924 checkpoint 01. Every product SHA these files reference is an ancestor of 4e08a314. Evidence only. |
| 2026-09-24 | `feat/nemotron-fleet-20260924` | 7ca2d2c4 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-23 | `evidence/owner-run-20260923` | 4a0babfe | 18 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-23 | `claude/bossman-1-0-cert-20260924` | 5a2b6f13 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-23 | `fix/owner-run-20260923-p1` | 16a5b402 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-23 | `feat/studio-vision-review-20260923` | f017e570 | 0 | FULLY_INTEGRATED (patch-id) | порт: f017e570 (vision ждёт свободный GPU). все коммиты уже в линии |
| 2026-09-23 | `fix/studio-seedance-durations-20260923` | 99970958 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-23 | `feat/cli-claude-parity-20260923` | 12612c81 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-23 | `fix/coding-path-crlf-blob-20260923` | 7439917f | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-23 | `integrate/owner-final-20260922` | bd2fe23d | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-23 | `claude/bossman-cloud-closure-owner-a6s1ki` | 3707d6ab | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-23 | `claude/bossman-1-0-rc-owner-ready-cfesui` | d6b20cdb | 3 | PARTIALLY_INTEGRATED | The bench flake fix was superseded by a stricter _PacedAdapter (c40843d0, 681c10a2), and the scorecard was regenerated later. Two pieces are still absent and worth porting: the negative-control test for recommendations, and the CDP-attach diagnostics for the desktop test. |
| 2026-09-22 | `codex/bossfield-amd-30s` | 037cd140 | 2 | FULLY_INTEGRATED | Bossfield Seedance 2.5 and the owner-run tool were converged by 981f61ef (identical blobs, including the continuity fix) and extended afterwards. The catalog and doc were deliberately adapted. |
| 2026-09-22 | `codex/bossman-v1.1-evolution` | 3bc81aee | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-22 | `audit/aster6-full-surface-20260922` | 528f0de3 | 1 | FULLY_INTEGRATED | The Telegram Codex bridge was explicitly not merged (CONVERGENCE_1_1.md:42: a second poller that conflicts and that the owner disabled). Owner-only Codex/Claude control lives in the companion's agent_bridge.py. |
| 2026-09-22 | `feat/coaching-exam-20260922` | f3eaa5a0 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-22 | `fix/root-suite-windows-20260922` | 27271992 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-22 | `fix/partial-video-rts5-20260922` | def77bed | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-22 | `feat/memory-lifecycle-20260922` | 9ed73374 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-22 | `feat/video-duration-presets` | 8f8d0607 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-22 | `fix/media-lifecycle-20260922` | 54eb7473 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-22 | `feat/telegram-owner-console-20260922` | bd598fa6 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-22 | `docs/media-candidates-20260922` | 5b839e04 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-22 | `audit/codex-full-surface-20260922` | 833a6a2a | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-22 | `feat/telegram-local-llm-20260922` | fc266856 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-22 | `feat/telegram-video-lengths` | fc266856 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-22 | `owner/rc5-close-20260922` | 737a31b1 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-22 | `fix/ci-flaky-20260922` | 7b2a367a | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-22 | `integrate/1.0-20260922` | 3e71e08f | 3 | FULLY_INTEGRATED | MEDIA-RESTART was superseded by 6451029a MEDIA-LIFECYCLE. DESK-EDGE-RELAUNCH is in the candidate as 8df57439 (identical files). CU STOP R6 was superseded by 908ccd15 and b8134d54. |
| 2026-09-22 | `fix/media-restart-orphan-20260922` | 00650364 | 1 | FULLY_INTEGRATED | The orphan sd.cpp engine fix was superseded by 6451029a (kill-on-close Job Object, kill tree checked by PID and create_time) and c16ba1ba (reconcile_orphans), with test_studio_media_lifecycle.py. |
| 2026-09-22 | `fix/cu-stop-button-opencode-20260922` | adb65ee2 | 1 | FULLY_INTEGRATED | Computer Use STOP/Resume (R6) was reimplemented in the candidate: stop epoch 908ccd15, durable STOP and control-page buttons b8134d54. The candidate's race test is a superset. |
| 2026-09-22 | `docs/bossman-critical-guides-20260922` | 1ba4befc | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-22 | `fix/desk-edge-relaunch-20260922` | 33cd3317 | 1 | FULLY_INTEGRATED | Same change as 8df57439, which is an ancestor of the candidate: the 3 files are identical. |
| 2026-09-22 | `evidence/owner-pass2-20260922` | 465c04ea | 5 | HISTORY_ONLY | Pass-2 checkpoints, the Qwen apprentice scoreboard and benchmark manifest, and the MEDIA-RESTART repro. The defects they cover are fixed in the candidate (6451029a, 8df57439). Evidence only. |
| 2026-09-21 | `audit/owner-glm53-20260921` | ead40d61 | 8 | HISTORY_ONLY | GLM-5.3 owner-hardware audit ledger on 0c3e22ff (verdict NOT CERTIFIED). The defects it found are fixed in the candidate: B4 79a12276, AP-ALL 84cbd788, TEL-001 8fe29925. Evidence only. |
| 2026-09-21 | `audit/owner-aster-20260921` | 2ce36d64 | 6 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-21 | `feat/viral-vfx-observatory-20260921` | b715b68e | 3 | NOT_INTEGRATED | An isolated feature candidate: Viral VFX (24 effects and 6 phonk presets in the Video Studio renderer) plus a six-view Observatory and opt-in API X-Ray, with its own feature CI workflow. None of it is at 4e08a314; the capability tree lists it with status 'branch'. Its own evidence records merge_ready=false, a blocked running-app browser check and no full CI PASS. Deferred to the owner as a new feature. |
| 2026-09-21 | `claude/bossman-final-convergence-hu2702` | cc92b73a | 18 | PARTIALLY_INTEGRATED | не порт: prep08-проба «установлено» (1000 строк) конфликтует с текущим responsiveness_probe.py — отдельный порт; GPT Image (платные POST без одобрения) — владельцу. 18 patch-unique commits (plus merges e75493d3/81e2da74, which are not in the candidate either). Already handled at 4e08a314: the metrics cancellation fix (replaced by single_flight.await_shared, 1ac7472c), the PREP-03 tariff gate (replaced by the stricter BL-084 catalog gate, 5f04ffcd) and the README scorecard markers. Missing and small enough to port: the responsiveness-probe honesty fixes (refuse a source-backed… |
| 2026-09-21 | `feat/bossman-v1-imagegen-dashboard-next` | 7db0db97 | 1 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-21 | `feat/openai-image-nextgen-20260920` | 43bc5e4b | 6 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-19 | `claude/vip-demo-mode-ui-bx9rrd` | 875b2a6f | 19 | NOT_INTEGRATED | порт: BOM в start-bossman.ps1; VIP-демо (выдуманный успех) — RETIRE. The batch assigns 6 of this branch's 19 patch-unique commits here. The other 13 (trading CASE-002/003 logs, Windows 100-scenario CI, VIP master prompt) are shared with ux/ai-3d-maker-adapter-20261004 and covered in batch_01. Of the 6: the start-bossman.ps1 UTF-8 BOM fix is a real, missing launcher fix (PORT_NOW). The VIP demo interceptor and canned travel demo would show invented success, and the technical-page hi… |
| 2026-09-19 | `night/v7-convergence-20260908` | 10653d91 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-12 | `v7/adaptive-reality-os-20260907` | 8befbccf | 6 | HISTORY_ONLY | V7 planning docs plus a standalone Phase 1 core in bossman-core/v7_*.py. The candidate implemented V7 differently in command-center/bcc/reality/ on top of bossman_shared/mission_ir.py, and that package says outright that it does not define a second Mission IR or world state. The code is superseded; the docs are historical and stay on the branch. |
| 2026-09-09 | `claude/bossman-final-completion-kymr05` | 5b673262 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-09 | `claude/bossman-final-integration-pass-04nffa` | 7fc916d2 | 14 | FULLY_INTEGRATED | File Intelligence (sidecar, routes declared once, effect-time binary check, the four test suites) was ported by meaning in c93a3312. The run-provenance, trader series-identity and AP-001/STOP_GRACE work is present at 4e08a314 in an equal or stricter form, as c93a3312 records and the test-name comparison confirms. Only the freeze/owner-test docs for the abandoned 92e89e0 line are left; they stay on the branch. |
| 2026-09-09 | `claude/bossman-final-audit-closure-aucx9x` | 5c19eea5 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-09 | `freeze/final-audit-closure-20260908` | 5c19eea5 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-08 | `audit/astra-v6-latest-breaker-20260908` | d33288f7 | 5 | HISTORY_ONLY | Astra/Codex breaker audit report, reproducers and suite logs. Its findings F1-F7 and the Web Designer apply bug were ingested as source A into docs/final_closure/MASTER_FINDINGS.md (MF-003..MF-010) and fixed in 69df482, an ancestor of 4e08a314. The regression test files exist in the candidate. The audit document itself is absent, and MASTER_FINDINGS.md:12 cites its path. |
| 2026-09-08 | `audit/cloud-qa-20260908` | 1531b160 | 34 | PARTIALLY_INTEGRATED | The freeze checkpoints, final report, backlog, AUDIT_CLOUD_QA and the V4_V5 re-verification section are in the candidate unchanged. The owner-manual audit was ingested as source B in MASTER_FINDINGS. The visible-breaker report and session log, about 84 MB of raw testing-period journals, and the UI proposal remain only on the branch. |
| 2026-09-08 | `feature/ai-streamer-higgsfield-browser-20260908` | 12832471 | 32 | PARTIALLY_INTEGRATED | Ported as behaviour: the durable job store (social_farm/jobs/store.py via edfe4191/d88c322d), the BL-105 capability-demotion fix (41e7dabf) and the Social Farm skips-registry coverage (2da38b2b). Superseded: the media router, selector repair, social-farm CI and the ffprobe test. The Higgsfield browser generation pipeline (adapter, worker, contracts, content buffer, persona, soak, docs) is absent; docs/v8/SALVAGE_P… |
| 2026-09-08 | `claude/fable-system-hardening-lpqq9r` | cd3da44d | 4 | PARTIALLY_INTEGRATED | The Web Designer escape and symlink fixes are superseded by stricter candidate code (_safe_path, escape at web_designer.py:483). The terminal rm-spelling evasion is superseded by BL-100 unrecoverable_delete_target. Still absent: the browser check of where a page lands after click/back/reload, plus the network egress guard (a P0 fix in 8605a2cb). Also absent: the hard-deny rule for dd/shred to raw block devices, an… |
| 2026-09-08 | `feat/token-shunt-routing-20260908` | 97903479 | 2 | NOT_INTEGRATED | Token Shunt routing spec and master prompt only. No implementation exists, and docs/final/FINAL_OWNER_TEST_CANDIDATE_20260908.md:169-172,183-184 explicitly defers it as new functionality until after acceptance. |
| 2026-09-08 | `audit/total-da67-d021-20260908` | 1da5b577 | 1 | HISTORY_ONLY | Independent total audit document. It was ingested as source C in docs/final_closure/MASTER_FINDINGS.md:14 (MF-016..MF-021), whose rows are superseded or fixed by commits that are ancestors of the candidate. MF-021 is still EXTERNAL_EVIDENCE_REQUIRED. |
| 2026-09-08 | `ux/bossman-visual-v3-miniupdate-20260908` | f6fd43eb | 4 | NOT_INTEGRATED | Docs only: the Visual V3 UI mini-update spec, master prompt, 4 small reference .webp files, and a Claude audit note. The UI feature is not built in the candidate. No Living Mode/Visual V3 code: git grep -i 'living[ _-]?mode' at 4e08a314 finds nothing. A candidate PR body (docs/astra/baseline_20260910/prs.json:6) says Visual V3 stays spec-only. The audit note's only actionable finding (TA-01, Trader CVD/OI series c… |
| 2026-09-08 | `design/bossman-living-ui-mini-update-20260908` | dbd4cf67 | 2 | NOT_INTEGRATED | Docs only: the Living UI mini-update spec (it marks itself 'DESIGN-ONLY / POST-FREEZE IMPLEMENTATION CANDIDATE') and its master prompt. Not implemented in the candidate. |
| 2026-09-08 | `v6/velocity-phase0-baseline-20260907` | 7b4ab765 | 2 | HISTORY_ONLY | The OpenHands docs reconcile and 1.44.1 pin are superseded by ca1178aa (real SDK run, OPENHANDS_STATUS_20260908.md, requirements-openhands.txt:1-9) and by isolated_worktree.py:102, which now builds a standalone clone. The V7 transition plan and nightly prompt are historical; that work was done on the night/v7 line (6d813bf7, caa727a6). |
| 2026-09-08 | `acceptance/total-local-20260906` | 9c97c732 | 2 | PARTIALLY_INTEGRATED | The super-test report and machine traces are in the candidate unchanged (ACCEPTANCE_FINAL_20260908.md, tasks-trace.jsonl, ui-walkthrough.json, brought in with caa727a6). Still on the branch: the audit-12 notes, github-audit.md, the task output files and 11 PNG screenshots. ACCEPTANCE_FINAL_20260908.md:19 and :35 reference some of these missing files. |
| 2026-09-08 | `v7/phase1-reality-core-20260908` | 17f11312 | 6 | FULLY_INTEGRATED | The bossman-core/bossman/reality Mission IR, World State Graph and strategy/shadow router were superseded by a newer implementation: command-center/bcc/reality/* (e5d62687 reality core, ca0fbe38 World State Graph) and bossman_shared/mission_ir.py. The branch's own package is not in the product; nothing remains to port. |
| 2026-09-08 | `acceptance-t3-20260908-002609` | f077cdfc | 8 | FULLY_INTEGRATED | The PR #49 canary-closure commits were ported by path into the candidate in 196a55ea. The terminal-run guard was later extended to PostgreSQL in 2da38b2b. The acceptance-task INSTALL.md edit (f077cdfc) is superseded because INSTALL.md was rewritten and the old Stage 12 text was archived (702c7185). |
| 2026-09-07 | `audit/v7-multimodel-20260907` | ca7c8508 | 25 | HISTORY_ONLY | 8 of 25 patch-unique commits are listed here: Perplexity, Perplexity-2 and Claude-Opus V7 audits, scorecards and the PATH_TO_100 guide, all under docs/v7/audits. The rest are covered by batch_06 (12) and the audit/perplexity-v7-independent rows (5). These are V6-era opinions superseded by the candidate's V7 synthesis and runtime (command-center/bcc/reality/*). Kept on the branch. |
| 2026-09-07 | `v7/adaptive-reality-os-audit-20260907` | c61151ee | 8 | HISTORY_ONLY | 8 V7 charter, architecture, implementation-spec and audit-prompt documents. The tip c61151ee is pinned in docs/night/V7_MULTIMODEL_AUDIT_REFERENCES_20260908.md (item 2) and synthesized. The synthesis explicitly marks the IMPLEMENTATION_TZ freeze gate as STALE. Kept on the branch. |
| 2026-09-07 | `v7/multi-model-audit-20260907` | 2b7080b6 | 1 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-07 | `v7/multi-model-architecture-audit-20260907` | f3361835 | 3 | HISTORY_ONLY | Three V7 audit and protocol documents. The branch tip is pinned as mandatory input in docs/night/V7_MULTIMODEL_AUDIT_REFERENCES_20260908.md (item 5), and its content was synthesized into docs/v7/MULTIMODEL_AUDIT_SYNTHESIS_20260908.md (d03c9ddb). The source documents stay on the branch. |
| 2026-09-07 | `audit/perplexity-v7-independent-20260907` | d08814ee | 18 | PARTIALLY_INTEGRATED | 6 of 18 patch-unique commits are listed here; the other 12 are covered under audit/cloud-qa-20260908 in batch_06. AP-001, SEC-002/003/004 with the stronger scanner, session-log CI unblock and DO-001/017 preflight are all present in the candidate. The Perplexity V7 audit document is pinned reference material. One gap: SEC-001 containment is incomplete, because the leaked vault-password literal is still in 3 tracked… |
| 2026-09-07 | `v7/frontier-multimodel-audit-20260907` | 94594b6c | 2 | HISTORY_ONLY | Two intake-prompt and lane README documents. The tip 94594b6c is pinned (item 4) in docs/night/V7_MULTIMODEL_AUDIT_REFERENCES_20260908.md. Kept on the branch. |
| 2026-09-07 | `v7/audit-convergence-20260907` | a548050f | 7 | HISTORY_ONLY | 7 V7 convergence documents: prompt pack, launch order, CURRENT_AUDIT, architecture charter and synthesis prompts. The tip a548050f is pinned (item 3) in docs/night/V7_MULTIMODEL_AUDIT_REFERENCES_20260908.md and used by the candidate synthesis. Kept on the branch. |
| 2026-09-07 | `ux/vision-full-page-pass-20260907` | 571cf8ef | 11 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-07 | `claude/v4-v5-canary-port-live-20260907` | b6f34450 | 18 | HISTORY_ONLY | 1 of 18 patch-unique commits is listed here: the freeze-ledger §17 recording canary P0 closed at 2ededc88. The other 17 are covered by batch_06 (12) and the audit/perplexity-v7-independent rows (5). The canary code closure is in the candidate (196a55ea). The ledger cites 2ededc88, which is not an ancestor of 4e08a314, so it stays as history. |
| 2026-09-07 | `claude/v4-v5-freeze-p0-gates-l56exm` | f714a6c1 | 1 | FULLY_INTEGRATED | Its single unique commit (app icon cut-out 40bb5e4f) was ported by meaning in 71bad67d. That commit made icon-1024.png the single master (identical blob) and regenerates all derived sizes with tools/app_icons.py, checked by tests/test_app_icon_assets.py. |
| 2026-09-07 | `docs/v6-audit-20260907` | da6ec40b | 3 | HISTORY_ONLY | Three V6-era audit documents: quickstart pinned to old SHA ddea2111, acceleration guide, self-routing spec, a teacher-trace audit of an unintegrated branch module, and a decision-cost ledger. Model-routing direction now lives in docs/v1.5/MODEL_ROUTING_STACK_20260925.md. Kept on the branch. |
| 2026-09-07 | `claude/bossman-v4v5-freeze-v6-perf-t25pvx` | a604b3c7 | 17 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-07 | `fix/testing-period-findings-20260907` | ca9a4a7a | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-07 | `chore/untrack-build-artifacts-20260907` | 8f99ec82 | 1 | FULLY_INTEGRATED | All 21 untracked artifacts (19 root zips, IMG_3955.png, .bossman-state/context-cache.json) are absent at 4e08a314. .gitignore:8 (/*.zip) and :97 (/.bossman-state/) prevent them coming back, and the deliberately kept BOSSMAN_SOCIAL_FARM_APP4_TECH_SPEC_V1_1.zip test fixture is still tracked. |
| 2026-09-07 | `build/local-bundle-20260907` | 210cc525 | 1 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-07 | `v6/teacher-traces-local-model-learning` | 2d985209 | 14 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-07 | `claude/v4-v5-p0-1-canary-production-caller` | 1efb5471 | 7 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-07 | `feat/web-designer-live-panel` | f80890d6 | 29 | PARTIALLY_INTEGRATED | 29 patch-unique commits. Web Designer live panel and its follow-up fixes are in the candidate, landed by meaning in 160edb8c plus later hardening. The CI fix is superseded, and the freeze-20260907 acceptance docs are identical or a superset in the candidate. Not integrated: the solana_volume_suite virtual-bot hardening and real-money preparation (owner decision), the TreasuryGuard budget-bypass fix (PORT_NOW), Hig… |
| 2026-09-07 | `v6/design-workstream-docs` | 6d4f872f | 13 | PARTIALLY_INTEGRATED | 3 of 13 patch-unique commits are listed here; the other 10 are the plan/performance-optimization docs, already identical in the candidate. Workstream J (design refresh plan, DESIGN_REFRESH_WORKSTREAM.md and edits to 5 docs/v6 files) is absent and needs an owner decision. The README/scorecard regeneration is superseded by the candidate's tool-generated scorecard, enforced in root-ci. |
| 2026-09-07 | `ux/app-icon-cutout-20260907` | 40bb5e4f | 1 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-07 | `claude/v5-closure-at-reconcile-xdh12f` | 67905ee2 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-07 | `astra/sandbox-user-run-fixes-20260907` | eb6a3ffb | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-07 | `plan/performance-optimization-20260907` | 7729f7db | 10 | FULLY_INTEGRATED | 9 documentation commits whose files are identical blobs at 4e08a314: docs/v6 Epoch 6 pack, docs/optimization/*, docs/audits/*. The README 'NEXT WAVE - PLANNED/NOT STARTED' banner commit is superseded: docs/v6/README.md indexes those docs and the README no longer carries the stale banner. |
| 2026-09-07 | `claude/evening-acceptance-hardening-m6op12` | dc61294f | 1 | PARTIALLY_INTEGRATED | One commit mixing a sandbox auto-list change, a Fleet 'experimental' marker and a set of owner-UI honesty fixes. Sandbox and Fleet are superseded by stricter or equivalent candidate code. The UI honesty defects (false all-clear on failed fetches, live Start button that is certain to fail, silent unknown-route fallback, missing glyphs, dead caret check, broken task deep link) are confirmed still present at 4e08a314… |
| 2026-09-07 | `claude/v4-v5-local-models-optimization-ya3utu` | 15d50307 | 10 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-07 | `perplexity/os-performance-audit-20260907` | bf8c6066 | 2 | HISTORY_ONLY | Two external Perplexity audit and prompt documents (OS local-LLM performance audit, audit index, master audit prompt v3). They differ from the Perplexity performance audit already in the candidate. The candidate's optimization lane (docs/optimization/*, docs/audits/2026-09-07__perplexity-total-performance__audit__v1.md) holds the adopted material. Kept on the branch. |
| 2026-09-07 | `ux/post-freeze-update-20260907` | df89e223 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-07 | `ux/app-icon-now-20260907` | 7c0fe760 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-07 | `skills/voltagent-curated-20260907` | 0c52f5cd | 1 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-07 | `feat/curated-openclaw-skills-20260907` | 8ac39104 | 5 | NOT_INTEGRATED | An OpenClaw community-skill install bridge (bossman-core/bossman/openclaw_bridge.py, CLI, curated shortlist, dated security review, hostile tests), absent from the candidate. The candidate's own OpenClaw bridge (command-center/bcc/v2/openclaw_bridge.py, a4dc2aa4) is channel-gateway-only and deliberately lists skills.install in NEVER_PROXY. This is a feature that needs an owner decision. |
| 2026-09-07 | `audit/astra-v5-independent-9cb1fb4-20260907` | f7c7aa0f | 1 | HISTORY_ONLY | One commit publishing an independent V5 verification overlay pinned to 9cb1fb4. Every reproduced finding (canary failure monotonicity and typing, promotion partition, baseline binding, finite score) is fixed in the candidate by f1ece12d, with regressions in tests/test_v5_freeze_regressions.py. AT-01 and ledger durability are covered by the candidate's own test suites. Only the historical evidence overlay remains o… |
| 2026-09-06 | `claude/intelligent-brown-6k9rcn` | f2d81247 | 1 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-06 | `claude/repo-audit-critical-issues-pbvphm` | dd08a3a0 | 2 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-06 | `fix/editor-save-proof-20260906` | af54bc9b | 1 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-06 | `fix/editor-user-baseline-20260906` | 794bf8c4 | 5 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-06 | `fix/takeover-admission-ci-20260906` | 8b86a0cb | 5 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-06 | `fix/astra-three-lane-20260906` | 9e8f1266 | 6 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-06 | `opus/finish-claude-residuals-v2-20260906` | cee5c72e | 2 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-06 | `opus/finish-claude-residuals-20260906` | 185e28dd | 3 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-06 | `kimi/final-residual-closure-20260906` | 30fce6c4 | 7 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-06 | `audit/unified-intake-20260906` | 98ea069e | 3 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-06 | `claude/acceleration-65z3ly` | 4339d34e | 1 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-06 | `dependabot/github_actions/actions/upload-artifact-7` | 26d56769 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-06 | `dependabot/github_actions/actions/setup-python-7` | a216bab6 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-06 | `dependabot/github_actions/actions/checkout-7` | d5e37341 | 1 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-06 | `fix/higgsfield-human-speed-20260906` | fb6591e2 | 24 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-06 | `fix/astra-v4-continuity-boundary-20260906` | 5f4f76b9 | 3 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-06 | `fix/astra-epoch-residual-20260906` | 0d559bc3 | 7 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-06 | `test/astra-human-speed-gates-20260906` | b53a5b19 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-06 | `integration/continuity-steward-closure-20260906` | c1e186b6 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-06 | `sol/v4-continuity-hardening-20260906` | e07f6c2f | 2 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-06 | `claude/bossman-closure-engineering-b8h722` | 954785f1 | 7 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-06 | `sol/v5-fable-implementation-pack-20260906` | e3c53d1d | 22 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-06 | `audit/fable-closure-source-20260906` | 58f8c3a0 | 11 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-06 | `fix/fable-media-fleet-20260906` | deed6be8 | 7 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-06 | `integration/bossman-unified-20260906` | beea20fb | 6 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-06 | `sol/fable-handoff-repair-20260906` | b28260f4 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-09-06 | `sol/fable-handoff-pack` | 21490552 | 5 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-06 | `astra/epoch4-plan-20260905` | d6d8d648 | 9 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-06 | `codex/video-studio` | 976602db | 12 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-06 | `sol/intelligence-preservation-on-head` | c22e55a4 | 4 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-06 | `sol/intelligence-preservation-gate` | 0e21c428 | 5 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-06 | `astra/epoch4-evolution` | fa963c2d | 9 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-06 | `astra/video-frame-edit` | a9a66cee | 12 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-06 | `astra/web-designer-properties` | d62e92ff | 19 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-06 | `codex/astra-v4-integration` | 357a9347 | 1 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-05 | `codex/reality-compiler-v010` | 45e69c90 | 12 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-05 | `codex/astra-virtual-bot-hardening` | debee693 | 6 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-05 | `claude/v2-ui-sidebar-compact` | 10fbed95 | 2 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-04 | `feature/osiris-data-acquisition` | d4e84a45 | 3 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-09-03 | `feature/cognitive-10-10-memory-context-reasoning-longtasks` | d4830a9d | 1 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-08-31 | `dependabot/github_actions/peter-evans/create-pull-request-8` | cf1340bf | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-08-30 | `fix/p0-p1-schema-memory-pg-gate` | 84af8dfd | 1 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-08-30 | `feature/hard-reasoning-v2-master-prompt` | 97f76297 | 1 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-08-30 | `security/audit-fixes-10-issues` | 143f87f8 | 1 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-08-30 | `claude/v2-reasoning-engine` | 6ebc9b75 | 2 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-08-30 | `claude/self-learning-orchestrator` | 32f69d63 | 1 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-08-30 | `claude/audit-lane3-integration` | b8aef92d | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-08-30 | `claude/audit-lane4-ux-v2` | 699c7d6e | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |
| 2026-08-30 | `claude/audit-lane2-coding` | fe874c06 | 2 | НЕ РАЗОБРАНА | агент реестра не дошёл (лимит сессии); решение не принято |
| 2026-08-28 | `stable/v2.2-phase-closed` | e520c687 | 0 | FULLY_INTEGRATED (patch-id) | все коммиты уже в линии |

Отложено владельцу (новые функции или внешние действия): глубокое изучение K1m6a (smart frames, инкрементальный
vision, извлечение уроков) из `audit/k1m6a-training-20261003`; голос/фоновые эффекты (спецификации);
генератор Motion 56; пакет заявок на финансирование; Viral VFX + Observatory; voxel3d/TripoSR; collector;
обучение 24/7 с отчётами в Пульт (`rc19/n-self` ослабляет проверку одобрения); GPT Image; open-news/Mimik.
Автоответчик Jeff (F1–F4) и keys_guard/key-intake на GitHub отсутствуют — не влиты.
