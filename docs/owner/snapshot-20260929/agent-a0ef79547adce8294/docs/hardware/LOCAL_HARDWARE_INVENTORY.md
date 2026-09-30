# Local Hardware Inventory — measured

Measured: 2026-08-31T20:40Z (campaign restart at remote head bfa2b0e).
All values measured live on this host; nothing inferred.

## OS / CPU / RAM

| Item | Value |
|---|---|
| OS | Microsoft Windows 11 Home, 10.0.26200, build 26200 |
| CPU | Intel Core i9-14900HX, 24 cores / 32 logical threads |
| RAM total | 15.63 GiB |
| RAM free at measure time | 4.72 GiB |
| Disk C: | NTFS, 374.01 GiB free / 552.21 GiB used |

## GPU

| Item | Value |
|---|---|
| GPU | NVIDIA GeForce RTX 4060 Laptop GPU |
| VRAM total | 8188 MiB (8 GiB) |
| VRAM used at measure time | 873 MiB |
| Driver | 580.88 |

## Toolchain

| Item | Value |
|---|---|
| Python | 3.14.3 |
| Node | v25.8.2 |
| Docker | 29.3.0 (daemon NOT running at inventory time; Docker Desktop start requested) |
| PostgreSQL binaries on PATH | none found; used previously via container on port 5433 |
| Ollama | 0.33.2 |

## Local models (ollama list via OLLAMA_HOST=127.0.0.1:11435)

| Model | Size | Note |
|---|---|---|
| qwen2.5:7b | 4.7 GB | primary A/B candidate |
| llama3.2:latest | 2.0 GB | small fallback |
| qwen2.5-coder:14b | 9.0 GB | exceeds comfortable 8 GB VRAM at full context |
| minimax-m2.7:cloud | — | cloud-tagged; NOT usable for local-only runs |

Ollama endpoint 127.0.0.1:11435 responds; 11434 also accepts connections at inventory time.

## GUI / browser / network

- Interactive GUI session available (Windows desktop).
- Chrome and Edge available (from prior session log; re-verified during P14).
- Network: available (git fetch to origin succeeded at 20:30Z).

## PostgreSQL

- No native Windows PostgreSQL installation found (services, Program Files, PATH).
- README/tests expect live cluster at port 5433 (DSN `postgresql://bossman:bossman@127.0.0.1:5433/bossman`),
  previously run as a container (pgvector/pgvector:pg17 in bossman-infra/compose.yaml).
- Status at inventory time: DOWN (ports 5432/5433 closed, no process). Recovery attempt via Docker Desktop in progress.
