# Aster independent final audit

Tested product: `release/bossman-owner`  
Tested SHA: `0c3e22ffd44c9b2c3e4f90b2e86456c28ca43f39`  
Audit branch: `audit/owner-aster-20260921`

## TOTAL NEW SCENARIOS

22

## PASS

20

## FAIL

1 — pending approval disappeared across Bossman restart.

## OWNER_REQUIRED

1 — Computer Use/browser UI could not run because the environment exposed no apps and no browser provider. No external or owner-only action was attempted.

## P0

0

## P1

1 — `CP-BLOCKER-20260921T1838Z`: pending approval id 3 was absent after restart, with no explicit terminal/recovery state.

## P2

1 — Qwen3.6 returned 21 MB rather than the expected 14 MB on the independent duplicate-file arithmetic prompt; Qwen3.8 returned 14 MB. This is a model-quality caveat, not a Bossman state-safety failure.

## MAIN MODEL RESULT

Qwen3.8-27B UD Q5_K_M loaded through fixed llama.cpp Vulkan, revision `4ca720788d1e01f1bff70c033e0d0028fd02e502`; GGUF and mmproj hashes matched metadata. Russian, coding, strict JSON, tool schema, long context and vision passed. Observed TG throughput was 8.887–10.119 t/s and end-to-end request latency was 8.5–26.4 seconds in the tested prompts.

## FAST MODEL RESULT

Qwen3.6-35B-A3B UD Q5_K_M loaded through the same Vulkan runtime, revision `a483e9e6cbd595906af30beda3187c2663a1118c`; GGUF and mmproj hashes matched metadata. Russian, coding, strict JSON, tool schema, long context and vision passed. It was faster in these tests, with observed TG throughput 31.970–48.913 t/s. Arithmetic reasoning produced the P2 caveat above.

## ROUTING

PASS for authenticated router preview and long-context preview. Main and fast endpoints both answered concurrent requests. When FAST was stopped, its endpoint refused connections while Bossman stayed alive; FAST was restored and left healthy.

## VISION

PASS for both models with safe `vision-test.png`; both identified the test shapes and BOSSMAN-42 label.

## TOOLS

PASS for OpenAI tool-schema generation on both models. Unknown/malformed MCP target returned 404 without a 500. Repeated completed-task lifecycle actions returned stable 409 conflicts.

## LONG CONTEXT

PASS for 4.8k-token model probe and 7k-token Bossman router preview fixture; the sentinel was retained.

## RESTART

P1 FAIL: health and model endpoints recovered, but a pending approval disappeared across Bossman restart. See `CP-BLOCKER-20260921T1838Z.md`.

## SECURITY

Unauthenticated access returned 401; invalid login returned 401; valid local test login returned CSRF. Router prompt-injection text did not disclose the local token. No real payment, wallet, trading, government, BankID, Datová schránka, third-party communication, data deletion, security-setting change, or secret publication was attempted.

## COMPUTER USE

OWNER_REQUIRED / environment blocked: Computer Use inventory exposed no native apps, and browser creation returned `No browser is available`. Backend adversarial equivalents were executed where safe.

## BROWSER

OWNER_REQUIRED / environment blocked for live UI. Stale and closed-session API paths were exercised safely and returned 422.

## MEDIA

PASS for safe vision input. Wrong-extension/malformed media API payload returned 422. No external media service or real upload was used.

## CHECKPOINT COMMITS

- `180aa36f` — CP-01 model handoff and benchmark record
- `29bccc67` — immediate P1 blocker for approval loss across restart
- `fec5075d` — CP-02 independent adversarial scenarios
- final commit — this file and final evidence index update

Handoff: `C:\Users\asd\Bossman\handoff\models-ready.json`  
Endpoints left running: `http://127.0.0.1:8081/v1` and `http://127.0.0.1:8082/v1`.

No production code changed.
