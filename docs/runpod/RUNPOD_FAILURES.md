# RUNPOD FAILURES / GAPS LOG

## GAP-001: A/B RSS sampler under-reports Ollama memory
- TIME=2026-09-01T18:15Z PHASE=5_SMALL AB RESOURCES
- FUNCTION=tools/local_hardware_ab.py resource sampler
- SYMPTOM: peak_ollama_rss_mib=50.4 while a 7B model is loaded (VRAM 6894 MiB)
- ROOT_CAUSE: psutil matcher sees only the `ollama` serve/router process; model tensors live in the spawned runner subprocess (`ollama runner`/llama server), which is not matched
- CLASS: metric gap in benchmark harness, NOT a Bossman product bug
- RESOURCE_EFFECT: honest footprint = VRAM (nvidia-smi/torch) — 6894 MiB recorded
- REMAINING_RISK: low; RSS metric for Ollama not comparable across hosts
- STATUS=OPEN (fix only if trivial later; VRAM is the primary metric)

## NOTE-001: model stays loaded in VRAM after A/B
- ollama default keep_alive keeps qwen2.5:7b resident (6894 MiB) after run
- POLICY: unload (`ollama stop <model>`) before tier switch per GPU policy; reload from disk cache is fast
