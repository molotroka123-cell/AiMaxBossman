# Bossman 1.5 — Telegram Login Privacy Contract

Date: 2026-09-25

## Secret boundary

- Passwords are stored only in the local encrypted Bossman credential vault.
- The local model does **not** receive the password value. It selects `credential_id`; the runtime injects the secret directly into the browser field.
- Telegram never receives the password, OTP, CVV, card number, API key, seed phrase or private key.
- Cloud workers never receive these values.
- Telegram `/fill` and owner-input continue to reject authentication/payment secret field names.

## Owner Telegram trace

For a login flow Bossman may send only:

1. account/login identifier;
2. names of ordinary fields that may be needed after login;
3. one fresh screenshot after the login is independently verified.

The pre-login notice explicitly says the password is local-only.

## Verified login receipt

`browser.login` can accept `success_url_contains`. After submit:

- Bossman reads the fresh browser URL;
- only when the expected post-login URL fragment is present does it create a `SUCCESS` receipt;
- it takes one fresh PNG screenshot;
- the Telegram bridge sends that screenshot to the owner;
- the receipt is marked consumed and the local screenshot file is removed.

If success cannot be verified, Bossman reports `UNVERIFIED_POST_SUBMIT`; it does not claim LOGIN PASS and does not send a success screenshot.

## Ephemeral Telegram cleanup

Every transient message id for this login receipt is tracked locally.

After success:
- account/login notice and control screenshot are kept briefly for owner visibility;
- the temporary Telegram chain is deleted automatically;
- deletion is attempted by exact `message_id`;
- the local transient list is cleared.

Default post-login visibility window: 90 seconds.

On failure/unverified submit, the temporary chain is cleaned sooner.

## Acceptance

Tomorrow's owner test must prove:
- credential is bound to the expected domain;
- login route uses a local-only agent;
- no secret appears in model/tool/Telegram output;
- post-login URL verification succeeds;
- exactly one fresh post-login screenshot is delivered;
- the tracked transient Telegram messages are deleted;
- a failed/unverified login produces no false PASS.


---

## Immediate handoff to Bossman 1.6

As soon as Bossman 1.5 reaches OWNER_READY closure — or only explicitly non-blocking P2 / optional OWNER_REQUIRED items remain — continue the SAME owner run in:

[Bossman 1.6 self-evolution branch](https://github.com/molotroka123-cell/AiMaxBossman/tree/feat/bossman-1.6-self-evolution-20260925)

Start with `BOSSMAN_1_6_START_HERE.md`.

One-run objective:

`BOSSMAN_1_5_OWNER_READY -> 1.6 HANDOFF -> BOSSMAN_SELF_EVOLUTION_RUNNING`

Do not use this handoff to hide an open 1.5 P0 or release-blocking P1. The goal is to close both stages in one continuous owner run whenever the critical 1.5 gates allow it.
