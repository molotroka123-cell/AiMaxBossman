# Telegram calls — continue

## The ONE next step

Record verified sha256 hashes for `telethon==1.45.0`, `pyaes`, `rsa` and `pyasn1` (and check whether `py-tgcalls`
3.0.0 needs further runtime dependencies) on a machine with network access, put them into `MANIFEST` in
`command-center/bcc/telegram_calls/addons.py` in place of `UNPINNED_NEEDS_HASH`, and add a test that the manifest has
no unpinned entry. Until then `bossman call install` refuses to install anything (by design) and no real call can be
attempted.

## Done since

The `activate(data_dir)` call is now in `__main__.py`; independent audit findings (STOP while ringing, dial idempotency, unconfirmed hangup, record_audio, CLI confirm) are fixed with tests.

## State

* Done and tested on fakes: installer, doctor, offline self-test, docs (see `ACCEPTANCE.md`).
* Not done: everything marked NOT_RUN in `ACCEPTANCE.md`, above all the real two-account call (owner only).
* `windows_bundle_lock.txt` and `tools/release_candidate.json` are untouched; the `calls` extra is outside `runtime`.
