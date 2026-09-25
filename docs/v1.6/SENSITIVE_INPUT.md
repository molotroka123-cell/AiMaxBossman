# Bossman 1.6 Sensitive Input

Status: foundation implemented; owner live acceptance pending.

This lane handles short-lived owner-provided form values for a pre-bound local login screen.

Rules:
- exact bound owner private chat only;
- redacted pre-entry screenshot plus requested field names;
- one in-memory session with short TTL;
- reply bypasses durable inbox/history/learning;
- no model receives the sensitive value;
- deterministic local actuator uses exact semantic field bindings;
- fresh post-state is required for success;
- session is one-use and restart invalidates it;
- buffers are overwritten best-effort;
- Telegram cleanup is best-effort and never presented as an absolute external-provider guarantee;
- strict-local phone-to-PC intake remains the preferred future mode.

Implemented files:
- command-center/bcc/telegram_companion/secret_intake.py
- command-center/bcc/telegram_companion/service.py
- command-center/bcc/telegram_companion/adapters.py
- command-center/bcc/telegram_companion/store.py
- bossman-core/bossman/computer_operator/secret_executor.py
- command-center/tests/telegram_contracts/test_companion_secret_intake.py
- bossman-core/tests/test_secret_executor.py

Acceptance: prove no canary value in durable storage/logs/model requests, reject wrong identity/replay/expired session, verify exact page/field binding, verify fresh success state, and report Telegram cleanup honestly.


## Local-model-only entry

The 1.6 default controller is a local OpenAI-compatible model endpoint bound to an
explicit loopback IP only. Proxy environment is ignored and there is no cloud
fallback. The model controls the sequence of exact field fills and form submit,
but receives only opaque local handles such as secret:password rather than the
sensitive value itself. The control plane resolves those handles only at the
final local actuator boundary.

This means the local model controls the login interaction without placing the
owner value into model tokens, KV cache, prompt logs or learning memory.

After verified access:
- the owner reply containing the sensitive value is deleted from Telegram;
- the Bossman photo message containing the redacted login screenshot and the
  requested-field list is deleted too;
- deletion is treated separately from login verification and is reported
  honestly if Telegram does not confirm it.

---

## ENTRY FROM 1.5 IN THE SAME OWNER-RUN

This branch is the immediate continuation of Bossman 1.5. When 1.5 reaches `V1_5_NEAR_COMPLETE` or closes fully, the same owner-run switches here without waiting for a separate day.

1.5 branch:
https://github.com/molotroka123-cell/AiMaxBossman/tree/feat/bossman-1.5-economy-orchestrator-20260924

Single-run goal: `BOSSMAN_1_5_CLOSED -> BOSSMAN_1_6_CLOSED`.
