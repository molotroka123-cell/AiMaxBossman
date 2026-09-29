# KNOWN LIMITATIONS

These limitations must be read together with the exact candidate certificate.

- Real local-model certification requires the owner's Windows machine and selected local runtime/model.
- Real Telegram delivery requires the owner's authorized Telegram configuration; mock transport proves only the contract.
- External services without idempotency guarantees require reconciliation after uncertain outcomes; Bossman must not blindly retry.
- Human-only CAPTCHA/BankID/eGovernment/biometric checkpoints require owner takeover.
- Image generation depends on a configured real provider; native Image Studio editing is independent of provider credentials.
- A clean Windows artifact must be tested outside a source checkout before release.
- Intelligence-retention evidence is fail-closed when its current measurement artifact is absent.

## Added for 1.0-RC (2026-09-21)

- **Computer Use** is Windows-only (pywinauto UIA + pyautogui). CI covers the decision plane with a mock desktop and the owner STOP/Resume controls with real Chromium; the live Notepad run in `owner-repair/evidence/computer-use-live-notepad.json` belongs to the previous commit line and must be repeated on the new archive. Input into Bossman's own windows, UAC, Windows Security and credential dialogs is refused by window identity; typing ordinary text (including Bossman paths) is allowed. Approval of a consequential desktop action comes only from the Command Center approval row, bound to the consequence kind and re-checked on a fresh screen; the model's `semantic` claim is never a permission.
- **Local media engine (stable-diffusion.cpp)** ships as code and manifest tooling only: no model weights or engine binary are in the archive, no real generation ran in CI (fake engine runs are labelled MOCK_ENGINE). Model bytes are verified by sha256 against the manifest before use; the observed backend is read from the engine log and is UNVERIFIED until a real run. Only the 832×480 / 20-step variant produced a meaningful clip in the previous handoff; the low-profile noise cause is not isolated, and no preset is declared working — tomorrow's A/B plan is `app-support/media_ab_preset.py`. A missing media engine never blocks Bossman startup.
- **Coaching / learning**: the lesson loop (attempt → failure → correction → verified lesson → retrieval after restart) is tested in CI through the canonical LearningStore with a MOCK model backend. Status: COACHING_PIPELINE_TESTED, LOCAL_LEARNING_GAIN_NOT_MEASURED, WEIGHTS_UNCHANGED. No EVO/self-modification is enabled.
- **Owner-only measurements** (not done today, listed in START_TOMORROW_RU.md): MAIN/FAST speed on the new archive, desktop focus/typing/STOP on the real desktop, real T2V/I2V generation, coaching gain on the local model, MVČR to the human boundary, independent red team on the installed product.
- Startup animation (owner wish) is not implemented; the desktop shortcut path is the existing `bcc.desktop_install` one.

No item above may be used to reclassify missing implementation code as hardware-required.

## Added for 1.9 freeze (a19/runtime2)

- **Free-only cloud (command-center registry)**: `Registry.adapter_for` and task admission refuse inference on a cloud model that is not provably free (positive price; OpenRouter without a `:free` id; a hand-typed 0/0 on an arbitrary host). Free-tier presets (NVIDIA NIM / Groq / Google AI Studio, `caps.free_tier` on the preset's own host), local endpoints and `:free` OpenRouter models work unchanged. `BOSSMAN_ALLOW_PAID_CLOUD=1` is the explicit operator/test opt-out (same switch as the core gateway).
- **OWNER_REQUIRED (not changed, needs an owner decision)**: (a) direct `anthropic` provider stays available behind the existing 3.00 USD hard Fable cap (`fable_cap`); (b) `economy_orchestrator` (GLM via OpenRouter, `BOSSMAN_15_GLM_BUDGET_USD`, default 0.25) and Studio OpenRouter generation use their own reservation/budget, not the registry guard; (c) free-tier presets are free only for accounts without billing (e.g. Groq with a card is paid) — that is an account fact Bossman cannot verify.
- **Streaming**: the command-center agent path emits the finished assistant message (`run.assistant_message`) per model step over a replayable SSE (`/api/events/stream?after=`); provider token streaming (first-token latency) is not implemented there (feature, post-freeze).
