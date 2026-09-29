# Telegram calls — open questions for the owner (non-blocking list)

1. **Hashes for telethon 1.45.0, pyaes, rsa, pyasn1.** They could not be verified offline, so they are marked
   `UNPINNED_NEEDS_HASH` and the installer refuses them (it never skips verification). Who records them, and on
   which machine? Is the dependency closure of `py-tgcalls==3.0.0` complete in the manifest (only `ntgcalls` is
   assumed)? Also unverified: the exact wheel file name of `ntgcalls` 3.0.0 (the installer finds the file by sha256,
   not by name).
2. **Windows bundle lock.** The `calls` extra is outside `runtime`, so the lock is unchanged. When calls join the
   release line the lock must be recomputed on the Windows runner (`tools/windows_bundle_lock.py record` runs only
   there). Decision needed on shipping them as a separate add-on artifact vs. inside the bundle.
3. **Models.** Which Whisper size (compute type) and which Russian Piper voice is the owner's choice for the test?
   Nothing is downloaded automatically; the doctor only tells where to put them.
4. **"1.9".** Not defined in the repo; treated as «next line after 1.5 plus this module». Confirm or correct.
5. **Latency target.** No target number is claimed anywhere. Which p50/p95 does the owner consider acceptable for
   «real time»? It can only be measured in a real call (`LATENCY.md`).
6. **Selftest brain.** The self-test uses a scripted brain on purpose (no LLM call, deterministic). Should a second
   mode with the real local LLM be added to measure full response latency offline?
7. **Silero VAD.** Falls back to an energy VAD when `pysilero-vad` is missing (flagged as degraded, WARN in doctor).
   Is that acceptable for the owner test, or should it be a hard requirement?
