# Stack on the owner PC — what is really installed (23.10.2026)

Answer to the owner's review «стек свежий, но не весь проверен»: versions are read from the machine, not from docs.

- `stack-versions.json` — ollama version + every installed model (name, size, family, quant), engine binaries
  (stable-diffusion.cpp commits of sd-cli/sd-server, llama-server) with file dates.
- `black-october-exposure.json` — `tools/advisory_exposure_check.py` (from PR #126, merged here): verdict PASS,
  DENY 0 / UNPINNED 0 / INDICATOR 0; PRESENT 1 = litellm referenced in `bossman-infra/compose.yaml`, now pinned.

Honest status:
- Qwen3.8-27B (`bossman-main-qwen38-27b-q5`, `qwen38-27b-uncensored`), Qwen3.6-35B-A3B, Gemma 4 31B: INSTALLED.
- Standalone llama.cpp with MTP / Vulkan tuning: NOT INSTALLED on this PC (only ollama's bundled llama-server).
- «Best model on the owner's real tasks»: NOT PROVEN — needs a preregistered A/B on owner tasks while the owner is idle.
