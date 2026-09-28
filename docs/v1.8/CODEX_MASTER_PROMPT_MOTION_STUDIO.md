# Codex master prompt: integrate and test Motion Studio (owner-authorized)

Give this to Codex on the owner PC as is. Source branch: `feat/bossman-1.8-unrestricted-motion-studio`
(PR #86). Target: `claude/bossman-freeze-closure-ohvmon` (PR #84).

```text
OWNER-AUTHORIZED RUN — integrate Bossman Motion Studio into the current Bossman line and test it on the owner PC.

CONTEXT
- Motion Studio = tools-only feature (no runtime/permission/authority change): a local model writes a
  JSON scene spec, tools/motion_studio renders the video (engine.html + Kokoro voice + synthesized score + ffmpeg).
- Source: branch feat/bossman-1.8-unrestricted-motion-studio (PR #86), latest head.
- Target: claude/bossman-freeze-closure-ohvmon (PR #84, current product line). Owner explicitly authorizes
  adding this tools-only change there. FREEZE status/gates are NOT changed by this; never claim freeze PASS.
- Docs: docs/v1.8/MOTION_STUDIO.md, docs/v1.8/MOTION_STUDIO_OSS.md, tools/motion_studio/NOTICE.md.

HARD RULES
- No force-push, no history rewrite, no rebase of shared branches; integrate with a merge commit.
- Do not touch the running backend, Jeff/PIT poller, secrets, vault or configs. No second poller.
- Never print/log/commit tokens or keys. Evidence goes to the private owner-run dir, not Git.
- NO FAKE GREEN: every step reports PASS / FAIL / NOT_TESTED with the exact command and exit code.
- Do not weaken the validator, tests or checks to get green. Reproduce a failure before fixing it.

PHASE 1 — MERGE
1. git fetch origin; checkout claude/bossman-freeze-closure-ohvmon; pull --ff-only.
2. git merge --no-ff origin/feat/bossman-1.8-unrestricted-motion-studio
   -m "merge: Motion Studio (tools-only) into the product line — owner-authorized".
   Conflicts: resolve explicitly, keep both sides' intent; regenerate docs/testing/SKIPS_REGISTRY.md only
   with python tools/skips_registry.py (never by hand).
3. Fast gates before push: python tools/skips_registry.py --check; python tools/ci_secret_scan.py;
   git diff --check; python -m pytest -q tests/test_motion_studio.py tests/test_skips_registry.py.
4. Push (no force). Report the merge SHA.

PHASE 2 — SETUP ON THE OWNER PC (isolated venv, not the product runtime)
5. python -m venv %LOCALAPPDATA%\Bossman\motion-venv; install: numpy scipy soundfile playwright kokoro-onnx.
   Chromium: use the installed Edge/Chromium via --chromium, or `playwright install chromium` inside this venv.
   ffmpeg must be on PATH (ffmpeg -version).
6. Kokoro models: download kokoro-v1.0.onnx and voices-v1.0.bin from the kokoro-onnx GitHub release
   (model-files-v1.0) into %LOCALAPPDATA%\Bossman\motion-tts. Record sha256 of both.
7. espeak data needs a SHORT path: create a junction C:\esd -> <venv>\Lib\site-packages\espeakng_loader\espeak-ng-data
   and set ESPEAK_DATA_PATH=C:\esd for the commands below.

PHASE 3 — TESTS (each one = PASS/FAIL + evidence)
8.  python tools/motion_studio/lottie_assets.py fetch        -> expect LOTTIE_ASSETS 100/100 verified
9.  python tools/motion_studio/verify_lottie.py --out <evidence>\lottie --chromium <path>
                                                              -> expect LOTTIE_RENDER 100/100, 0 page errors
10. python tools/motion_studio/verify_library.py --out <evidence>\library --chromium <path>
                                                              -> expect LIBRARY_RENDER 44/44, 120 scenes
11. Full render with voice + music:
    python tools/motion_studio/make_video.py tools/motion_studio/examples/jeff_voice_12s.json
      --work <evidence>\render --tts-models %LOCALAPPDATA%\Bossman\motion-tts --chromium <path>
    Verify with ffprobe: duration 12.0 s, 720 video frames, AAC audio present; ffmpeg volumedetect
    mean between -20 and -8 dB. Record wall-clock render time (this is the first real owner-PC number).
    Also render tools/motion_studio/library/jeff_voice_notes.json (uses the Lottie sticker) and confirm
    the MP4 comment carries the Noto CC BY 4.0 attribution.
12. Local-model generation (the real test of "Bossman makes videos"):
    for 3 NEW briefs not in the dataset (e.g. "10 s announcement that Jeff now hears voice notes",
    "12 s weekly Bossman progress with real commit counts from git log", "8 s thank-you to testers"):
    python tools/motion_studio/generate_spec.py "<brief>" --model <installed local model, e.g. the
      Qwen3.6-35B-A3B used by the product> --endpoint http://127.0.0.1:11434/v1
      --facts <facts.json with REAL numbers only> --out <evidence>\gen\<n>.json --tries 4
    (since 2026-09-28 this uses native Ollama /api/chat with think:false, a 240 s per-call timeout and
    progress lines on stderr; on 2026-09-27 the OpenAI-compatible path never returned. Keep the stderr log
    as evidence; if a call times out, report the logged seconds, do not raise the timeout silently.)
    Record per brief: VALID/INVALID, tries used, seconds, errors left. Then preview-render each valid spec:
    make_video.py <spec> --work <evidence>\gen\<n> --preview 1 3 5 7 --chromium <path>
    and fully render the best one. Numbers on screen must come from facts.json — flag any invented number.
13. Owner review: show me the 3 previews + the full render; I rate 1-5. Only owner-approved specs may be
    appended to tools/motion_studio/dataset/brief_to_spec.jsonl (commit separately, no auto-append).

PHASE 4 — REPORT (one table, then push evidence summary only)
| step | command | exit | result | evidence |
Include: merge SHA, venv/tool versions, model name, render seconds, valid-rate x/3, tries, owner rating.
State clearly: Motion Studio is available as CLI tools in the checkout; it is NOT yet wired into the
Command Center UI or the Windows bundle (tools/ is not packaged) — say whether the installed archive
contains tools/motion_studio (check, don't assume). FREEZE unchanged; READY_FOR_1_8 unchanged.
```

Reference renders already in this branch: `docs/v1.8/media/jeff_voice_12s.mp4` and
`docs/v1.8/media/jeff_voice_notes_sticker_10s.mp4` (with the Lottie 🤨 sticker). Both were rendered in the
cloud sandbox from the JSON specs.
