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
