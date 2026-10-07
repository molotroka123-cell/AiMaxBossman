# Handoff: owner Telegram key intake (07.10)

Status: NOT implemented. No product code exists yet; nothing was applied.

Worker runs (source_repo C:\Users\asd\Bossman\worker-src, branch worker/keyintake at e101a4fc, clean):
- dde7af5944be glm-flash (paid, started before the free-only rule): failed, stop_reason=max_steps, no changes.
- 4172e21ff733 nvidia-nim: failed, max_steps (40); wrote key_intake.py/service.py drafts in sandbox, no tests, not applied.
- eee82edd0b50 nvidia-nim (2nd, with budget hint): failed, max_steps; many `no_tool_call` turns (model answers in prose), no changes.
- 3efbb365a5c5 openrouter-free: was still RUNNING at checkpoint; check its status via GET /api/coding-tasks/3efbb365a5c5.

Findings for the next attempt:
- Free models burn the 40-step budget on text-only turns (`no_tool_call`); the instruction now says every turn must be a tool call and to write files in one call each.
- Design decision already fixed in the instruction: the hook goes in Companion.ingest() BEFORE looks_like_secret_message and store.ingest, using store.acknowledge_without_body, so key text never reaches SQLite or the LLM path; deletion via self._delete_secret_message.
- Remaining allowed coders in order: openrouter-free (1 attempt used or running), nemotron-ultra-free, openrouter-code-free, local sidecar (worker=None, retry once on cut-off tool calls). If none passes, report to coordinator; do not hand-write.
- Then: audit diff, run verify tests (test_companion_key_intake.py + test_companion_sweet_drops.py + -k "companion or telegram_companion"), Haiku 5.5 second look, Apply gate, commit.

Task instruction text: scratchpad instr.txt of the 07.10 session (spec items 1-6 verbatim from the owner). No key values appear anywhere.
