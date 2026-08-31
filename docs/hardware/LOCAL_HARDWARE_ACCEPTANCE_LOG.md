# Local Hardware Acceptance — Continuity Log

This append-only log is a recovery aid for the local-hardware acceptance run.
It records observed state only; a running test is never counted as a pass.

## 2026-08-31 22:20 Europe/Prague — start and live-model execution

- `START_REMOTE_SHA`: `4aaa17b4388829e56760e8a4d9d3cb33e01e3342`
- `LOCAL_SHA`: `4aaa17b4388829e56760e8a4d9d3cb33e01e3342`
- `FINAL_REMOTE_SHA`: pending final fetch.
- Existing local changes and `.audit-learning-guard-3c47010/` were detected and preserved.  `stash@{0}` is also present; it was not applied.
- Hardware inventory: Windows 11 Home 10.0.26200; Intel i9-14900HX; 15.63 GiB RAM; RTX 4060 Laptop GPU (8 GiB physical VRAM reported by `nvidia-smi`); Python 3.14.3; GUI session and Chrome/Edge available.
- Ollama 0.33.2 is live at the operator-provided `OLLAMA_HOST=127.0.0.1:11435`.  The legacy `127.0.0.1:11434` endpoint timed out.  Available local model selected for A/B: `qwen2.5:7b`.
- Focused gateway regression: `bossman-core/tests/test_alias_and_auth_boundary.py` — **14 passed**.
- A real direct-vs-Gateway A/B run is in progress using the same local model, temperature 0, 3 repeats for six task classes.  The short-lived Gateway is loopback-only on `127.0.0.1:8767`; GPU utilisation is active.  Result pending; no cloud fallback is configured.
