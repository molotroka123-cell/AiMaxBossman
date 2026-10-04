# Reproduce this discovery snapshot

Run these read-only commands from PowerShell on the captured owner-PC. They capture the live resource/runtime facts without starting, stopping, downloading, or loading a model:

```powershell
$ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
& $ollama --version
& $ollama list
Invoke-RestMethod http://127.0.0.1:11434/api/version
Invoke-RestMethod http://127.0.0.1:11434/api/ps | ConvertTo-Json -Depth 6
Invoke-RestMethod http://127.0.0.1:11434/api/show -Method Post -ContentType 'application/json' -Body '{"name":"qwen38-ab-baseline:latest"}' |
  Select-Object details,model_info | ConvertTo-Json -Depth 5
Get-CimInstance Win32_OperatingSystem |
  Select-Object @{n='FreeGiB';e={[math]::Round($_.FreePhysicalMemory/1MB,1)}}
Get-PSDrive C | Select-Object @{n='FreeGB';e={[math]::Round($_.Free/1GB,1)}}
Get-NetTCPConnection -State Listen |
  Select-Object LocalAddress,LocalPort,OwningProcess | Sort-Object LocalPort
Get-Content C:\Users\asd\Bossman\start-models.ps1
```

Market links and candidate revisions currently discovered are recorded in [MODEL_MARKET_20261004.md](MODEL_MARKET_20261004.md). Pin each upstream commit and exact quant before acquiring it; `main` and search snippets are not immutable revisions.

## Isolated route smoke (completed)

The live `127.0.0.1:8800` owner service was left untouched. For reproducible API observations, the source commit `fee3abb01a8bba0040b744f86e9672f30d200e0d` ran in a separate worktree with its own data directory on `127.0.0.1:18800`; the API token was stored only in that local isolated directory and is excluded from the committed files. llama.cpp b10964 provided one model at a time on :18801 (Qwen), :18802 (Ornith), or :18803 (Muse). The exact task, response, token counts, task IDs and timestamps are in `logs/quick_smoke_runs.json`.

The llama-server invocation used the downloaded Ollama blob and identical flags for each model:

```powershell
$llama = 'C:\Users\asd\Bossman\runtime\llama.cpp\llama-server.exe'
& $llama -m '<model-blob-path>' -ngl 999 -c 32768 -t 16 --host 127.0.0.1 --port 1880X --jinja --reasoning off --no-webui -np 1
```

For Muse, include `--mmproj '<projector-blob-path>'`; its model and projector SHA256s are in `manifest.json`. In the isolated Bossman API, create one provider whose `base_url` is the chosen local `http://127.0.0.1:1880X/v1`, then one model and a no-tools agent (`max_steps=1`, `max_tokens=128`). Submit the identical `POST /api/tasks` title/prompt recorded in `logs/quick_smoke_runs.json`. The run database and API token are excluded from Git; create a fresh isolated data directory and token when repeating it.

Equivalent Bossman tasks were submitted through `POST /api/tasks`, not directly to the model endpoint. The exact control prompt was `Reply with exactly the word PASS and nothing else.` Each candidate returned `PASS`. This validates basic route compatibility only; it does not measure coding, tools, vision, robustness or relative agent quality. The local runner was stopped after evidence capture. The two downloaded GGUF packages remain in Ollama's shared model store; no live route/config was changed.

## Final tournament (completed)

The 15-task finalist round ran twice per model through the isolated Bossman CMD/API at `127.0.0.1:18810`, with one candidate server active at a time. The same task order and fixed decoding/configuration were used. Hidden coding tests were applied after collecting candidate outputs. Results and per-task logs are in `benchmarks/model-market-20261004/`; scoring logic is in `score_final_tournament.py` and hidden-diff scoring is in `score_coding_diffs.py`.

Evidence limitation: the archived finalist JSON has `runner_sha: null`. Git history shows the checked-in runner was committed after those runs started, and the isolated BCC database is excluded. Therefore the saved task records and scores can be reviewed and recomputed, but the exact historical runner and API records cannot be independently pinned from this repository snapshot. The current runner records its own SHA256 in any future run; that does not retroactively identify the runner used in the archived run.

To recompute the published scores from the saved run artifacts, from the repository root run:

```powershell
python benchmarks/model-market-20261004/score_coding_diffs.py
python benchmarks/model-market-20261004/score_final_tournament.py
```

These recompute from saved outputs and do not start a model or alter owner configuration. For a new full run, review the runner's fixed route and task configuration in `run_final_tournament.py`; provision the isolated API and two candidate agents exactly as documented in the saved result metadata. Do not send live owner traffic to tournament models. This procedure does not claim owner-stack restart or integration acceptance.
