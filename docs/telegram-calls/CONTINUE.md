# Telegram calls — continue

## The ONE next step

Record verified sha256 hashes for `telethon==1.45.0`, `pyaes`, `rsa` and `pyasn1` (and check whether `py-tgcalls`
3.0.0 needs further runtime dependencies) on a machine with network access, put them into `MANIFEST` in
`command-center/bcc/telegram_calls/addons.py` in place of `UNPINNED_NEEDS_HASH`, and add a test that the manifest has
no unpinned entry. Until then `bossman call install` refuses to install anything (by design) and no real call can be
attempted.

## Needed change outside this line's files (Line A owns them)

The worker runs as `python -I -m bcc.telegram_calls`, so `PYTHONPATH` and user site are ignored and the add-on
directory is not importable. Minimal change in `command-center/bcc/telegram_calls/__main__.py`, right after
`_data_dir()` is known and BEFORE `.call.worker` / the transport are imported:

```python
from .addons import activate
activate(data_dir)      # idempotent; puts <data_dir>/addons/telegram-calls/packages on sys.path
```

`manager.py` already passes `BCC_DATA_DIR` to the worker, so nothing else is needed there. The Command Center process
itself never imports the add-on (doctor reads package versions from metadata and loads ntgcalls only in a `-I` subprocess).

## State

* Done and tested on fakes: installer, doctor, offline self-test, docs (see `ACCEPTANCE.md`).
* Not done: everything marked NOT_RUN in `ACCEPTANCE.md`, above all the real two-account call (owner only).
* `windows_bundle_lock.txt` and `tools/release_candidate.json` are untouched; the `calls` extra is outside `runtime`.
