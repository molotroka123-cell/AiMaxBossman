# RC TEST B — INTERMEDIATE 2026-08-30 12:40

START_HEAD: 17cd4633dfa7690b1f8b506ac5f5f04f0fdacfdf
CURRENT_HEAD: 17cd463 (origin synced, clean before this commit)

## PUSH STATUS (requested by user)

- 4fb8b6f feat: add Pythia World Intelligence drop-in integration — PUSHED
- 17cd463 fix(bcc): align FactStore API parameters + static audit — PUSHED
- Both commits visible on GitHub: https://github.com/molotroka123-cell/AiMaxBossman branch claude/bossman-control-v03-43igbk
- Local and origin in sync: git status clean (after removing temp txt), gh api confirms 17cd463

## RC TEST C — LIVE COMMAND CENTER (already executed, results to be pushed now)

- Tools already present in tools/rc_test_c/ (mock_llm.py, rc_live.py) producing rc_results_2026-08-30.json
- 74 endpoints/actions tested, ALL PASS (see rc_results_2026-08-30.json)
- Includes dashboard sweep, schedule create fix (title->name 422 enforcement), approval flow (approve/reject with real fact stored check)

## RC TEST B — LOCAL LLM LIVE (in progress, this doc is intermediate)

### 1. LOCAL MODEL

- Available models (ollama list):
  - qwen2.5:7b 4.7GB Q4_K_M
  - llama3.2:latest 2.0GB 3.2B Q4_K_M  <- CHOSEN (fits 8GB VRAM, already installed, 100% GPU)
  - qwen2.5-coder:14b 9.0GB (too large, SKIP)
- GPU: NVIDIA GeForce RTX 4060 Laptop 8188 MiB total, used ~3800 MiB with model loaded, ~6767 free before load
- Quant: Q4_K_M (4-bit)
- VRAM peak with llama3.2: 3882 MiB (nvidia-smi), ollama ps shows 2.6GB SIZE, 100% GPU, 100% GPU processor
- Simple prompt via ollama CLI: PASS (subprocess.run ['ollama','run','llama3.2','say hi'] -> Hello in <1s after warm, 24s cold start, tokens ~30/s prompt eval 328 tok/s per llama_server log)
- Gateway local route: PENDING — Ollama HTTP API currently unstable via portproxy (0.0.0.0:11434 -> 192.168.64.156:11434 WSL forwarding, but WSL curl Connection refused, Windows python socket ConnectionReset). CLI works, HTTP API times out. Investigating: Windows ollama.exe serve + WSL forwarding conflict. Need to fix gateway config to use direct ollama CLI or localhost without WSL proxy.
- Cloud isolation: PENDING (will check gateway logs for cloud_policy=never after fix)

### 2-5 PENDING

- NOTEPAD LIVE, SECOND ACTION, NEGATIVE, CLOUD_ISOLATION — waiting for Gateway local route fix

### NEXT STEPS

1. Stabilize Ollama HTTP API (kill stale WSL portproxy, ensure Windows ollama binds to 127.0.0.1:11434 directly)
2. Create gateway.yaml pointing bossman-fast -> ollama/llama3.2 local, verify /v1/chat/completions via gateway with cloud_policy=never returns local
3. Run NOTEPAD live via production chain (Planner->Policy->Router->Windows)
4. Second action TYPE, negative DENIED, prove CLOUD_CALLS 0, then targeted+full regression

