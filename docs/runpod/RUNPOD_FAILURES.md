# RUNPOD FAILURES / GAPS LOG

## BUG-002: pgvector extension missing + superuser privilege (ENV SETUP, not product bug)
- TIME=2026-09-01T18:25Z PHASE=FIRST_REAL_TASK
- FUNCTION=bossman/db.py schema apply (db/schema.sql CREATE EXTENSION vector)
- REAL_REPRO: fresh Ubuntu 24.04 postgresql-16 package → DEPENDENCY_UNAVAILABLE: extension "vector" not available; after postgresql-16-pgvector install → InsufficientPrivilegeError (CREATE EXTENSION needs superuser)
- ROOT_CAUSE: canonical deploy assumes pgvector-enabled PG + bootstrap privileges (docker-compose image has it); apt PG does not
- MINIMAL_FIX: apt install postgresql-16-pgvector; CREATE EXTENSION vector/pg_trgm/pgcrypto as postgres superuser in bossman DB; app role stays non-superuser
- TEST_EVIDENCE: schema applied → 21 tables; serve startup complete; tasks done
- SECURITY_EFFECT: none (app role not elevated)
- REMAINING_RISK: none for container; document pgvector as deploy requirement
- CLASS: environment setup — repo docs correct, OS package incomplete

## DISC-001: fail-fast agent alias validation (WORKS AS DESIGNED)
- run_task worker died with ValueError: alias bossman-coder missing from gateway config; task stuck 'queued' (worker loop crashed)
- OBSERVED BEHAVIOR: honest fail-fast on portfolio misconfig; worker does NOT silently skip
- NOTE: worker exception breaks the loop until restart — queued tasks are retried on serve restart (verified: task #1 completed after restart). Potential improvement (worker resilience) = future item, not a bug fix now
- MINIMAL_FIX (config): added bossman-coder alias to gateway.runpod.yaml
- EVIDENCE: serve log Task-6 exception; task #1 done after restart

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
