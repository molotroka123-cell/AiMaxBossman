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

## Tournament remains unrun

Do not count a standalone model API prompt as Bossman evidence. The next valid run requires the Bossman Command Center to be available at its actual owner route and a saved task corpus with sealed tests. The requested quick round is 10 tasks; finalist round is 15 tasks × 2 runs for each of two finalists. Record every attempt in structured JSON with `source_sha`, model revision/quant, runner config, route ID, item ID, random seed, start/end timestamps, test outcome, tool calls and arguments, retry/step counts, TTFT, prompt/output tokens, generation rate, peak RAM/VRAM, restart status and safety decision. Scrub prompt contents that contain secrets before persisting logs.

Run the same Bossman CMD/API path, tool registry, system instructions, decoding parameters, time/token budgets, and task order for every candidate. Only unseal hidden expected outputs after candidate runs finish. Treat model-card and leaderboard results as market selection evidence; never add them to the local score.

No shell command in this file runs the tournament: at capture, the documented CMD listener was absent and starting it would launch against the live owner data directory.
