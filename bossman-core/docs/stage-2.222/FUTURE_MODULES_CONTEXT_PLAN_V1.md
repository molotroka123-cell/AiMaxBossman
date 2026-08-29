# BOSSMAN — FUTURE MODULES & CONTEXT ROADMAP v1

**Status:** planning / pre-hardware implementation  
**Purpose:** define modules that can be prepared before the target AI machine arrives, then merged into the main BOSSMAN architecture.  
**Primary principle:** local-first, private-by-default, modular, observable, and reversible.

---

## 0. Priority Map

### P0 — Definitely build
1. **Personal Search Engine / Context Fabric** — huge priority.
2. **Local AI API Gateway** — needed for phone/private clients and model routing.
3. **AI Video Factory** — separate module in the main BOSSMAN dashboard.
4. **Knowledge Distillery** — long-term memory compression and context maintenance.
5. **Synthetic Data / Fine-tuning Lab** — with strict memory/storage monitoring.
6. **AI Sandbox** — manual enable only; completely inactive until explicitly started.

### P1 — Build, but lightweight
7. **Document / Office Agent** — useful, but deliberately simple.
8. **Security Mini-Agent** — low resource use, randomized schedule, visually quiet.

---

# 1. PERSONAL SEARCH ENGINE / CONTEXT FABRIC — P0

## Goal

Create a local semantic search + memory layer that becomes the shared context backbone for every BOSSMAN agent.

The system must answer questions such as:

- Where did we discuss this feature?
- Which project used a similar implementation?
- What did the last audit say?
- Which decision replaced the older decision?
- What files, chats, notes, emails or GitHub commits are relevant?
- What facts are stable and what is stale?
- Which model/agent already solved a similar task?

This is not only "RAG". It is the **context operating system** for BOSSMAN.

---

## 1.1 Data Sources

Initial supported sources:

- BOSSMAN project files
- Git repositories
- Markdown
- Obsidian vaults
- PDFs
- DOCX
- XLSX/CSV
- code files
- audit reports
- agent journals
- agent memories
- task outputs
- browser research results
- saved webpages
- local notes
- selected email content
- selected Telegram exports
- future private iPhone/desktop client conversations

Optional later:

- images with generated captions
- audio transcripts
- video transcripts
- OCR from scans
- local database snapshots

---

## 1.2 Context Storage Model

Use several layers instead of one giant vector DB.

### Layer A — Raw immutable archive

Stores original content.

```text
data/context/raw/
  github/
  docs/
  chats/
  mail/
  browser/
  media/
```

Never overwrite raw source silently.

### Layer B — Normalized documents

All sources converted into a common internal format:

```json
{
  "document_id": "...",
  "source_type": "github|markdown|mail|chat|browser|pdf|...",
  "source_uri": "...",
  "project": "...",
  "created_at": "...",
  "updated_at": "...",
  "author": "...",
  "text": "...",
  "metadata": {}
}
```

### Layer C — Chunks / searchable units

Chunk by semantic boundaries, not fixed characters only.

Each chunk stores:

- stable chunk ID
- document ID
- heading / section
- text
- token count
- timestamp
- source
- project
- importance
- freshness
- sensitivity
- embedding version
- content hash

### Layer D — Structured memory

Separate facts from raw documents:

- people/entities
- projects
- decisions
- constraints
- architecture choices
- user preferences
- unresolved questions
- risks
- TODOs
- timelines

### Layer E — Distilled memory

Compact summaries optimized for model context.

This layer is refreshed by Knowledge Distillery.

---

## 1.3 Retrieval Pipeline

Recommended retrieval should be hybrid:

```text
USER QUERY
   ↓
Query classifier
   ↓
Entity/project/time extraction
   ↓
┌──────────────────────────────┐
│ lexical / BM25               │
│ embeddings / vector          │
│ metadata filters             │
│ recency                      │
│ graph/entity relationships   │
└──────────────────────────────┘
   ↓
Merge candidates
   ↓
Reranker
   ↓
Deduplicate
   ↓
Context packer
   ↓
Agent/model
```

Do **not** rely only on embeddings.

---

## 1.4 Context Budgeting

Every retrieved item receives a score:

```text
final_score =
  semantic_relevance
+ lexical_relevance
+ entity_match
+ project_match
+ recency_weight
+ importance_weight
+ relationship_weight
- duplication_penalty
- stale_penalty
```

Context packer must fit the actual model window.

Suggested packs:

- **micro** — 2–4k tokens
- **normal** — 8–16k
- **deep** — 32–64k
- **research** — dynamic / iterative

The system must prefer better evidence instead of simply filling the entire context window.

---

## 1.5 Memory Types

Agents should not use one generic memory file.

Use:

```text
memory/
  identity/
  projects/
  decisions/
  preferences/
  facts/
  procedures/
  episodic/
  unresolved/
  distilled/
```

### Stable memory
Long-lived facts and architecture decisions.

### Episodic memory
What happened in a specific task/session.

### Working memory
Temporary active task context.

### Procedural memory
How to perform recurring tasks.

### Negative memory
Things that failed, bugs, bad approaches, rejected decisions.

Negative memory is important to avoid repeating failed work.

---

## 1.6 Provenance

Every remembered fact should retain its origin.

Example:

```json
{
  "fact": "ComputerUse requires approval for dangerous browser actions.",
  "source": "docs/architecture/computer-use.md",
  "source_hash": "...",
  "confidence": 0.98,
  "created_at": "...",
  "last_verified_at": "...",
  "status": "active"
}
```

Never let distilled memory erase provenance.

---

## 1.7 Freshness / Contradiction Handling

If two sources conflict:

1. do not silently overwrite;
2. store both;
3. score freshness and authority;
4. mark contradiction;
5. ask agent to resolve or request user confirmation when important.

Example states:

```text
active
stale
superseded
disputed
unverified
```

---

## 1.8 Proposed Tech Stack

Claude should evaluate the current repo before selecting exact dependencies.

Recommended direction:

- PostgreSQL for metadata / structured facts
- pgvector or Qdrant for embeddings
- SQLite acceptable for prototype
- Tantivy / PostgreSQL FTS / Meilisearch-like lexical layer
- local embedding model
- local reranker
- filesystem-backed raw archive
- optional graph relations in PostgreSQL first, not a separate graph DB unless truly needed

Avoid unnecessary infrastructure early.

---

# 2. LOCAL AI API GATEWAY — P0

## Goal

The AI machine exposes one private endpoint to all BOSSMAN apps and private clients.

Instead of clients knowing specific model servers:

```text
iPhone / Telegram / Desktop / BOSSMAN
              ↓
       BOSSMAN AI GATEWAY
              ↓
 ┌────────────┼────────────┐
 local LLM    vision       embeddings
 coder        reranker     cloud fallback
```

---

## 2.1 Required Capabilities

- OpenAI-compatible API where practical
- model aliases
- local model routing
- per-model health checks
- request queue
- concurrency limits
- context-window awareness
- streaming
- token accounting
- latency metrics
- memory consumption metrics
- fallback model
- optional cloud fallback
- per-client keys
- revocable client sessions
- audit logs
- rate limiting

---

## 2.2 Routing Rules

Example aliases:

```yaml
aliases:
  bossman-fast:
    route: small_local
  bossman-smart:
    route: strongest_available_local
  bossman-code:
    route: best_coder
  bossman-vision:
    route: best_vision
  bossman-research:
    route: research_router
```

The client should request a capability, not a raw model filename.

---

## 2.3 Phone Client Direction

Primary objective: private control from iPhone.

Possible clients:

### Option A — Private native iPhone app
Best long-term UX.

Features:

- encrypted login
- chat
- voice messages
- task status
- approvals
- notifications
- file upload
- camera/photo upload
- model/agent selector
- live streaming
- emergency lock button

### Option B — Telegram
Fastest prototype but not the desired final trust boundary for highly sensitive tasks.

Use only for:
- alerts
- low-risk commands
- approval notifications
- status summaries

### Option C — Signal-style private chat
Better conceptual security target, but integration complexity is higher.

### Preferred architecture

Build **our own thin private iPhone client** talking only to BOSSMAN Gateway over:

- WireGuard / Tailscale-like private tunnel
- HTTPS
- device-bound auth
- short-lived access tokens
- server-side revocation

Avoid exposing the raw model server directly to the internet.

---

## 2.4 Claude Code Sketch

Claude should prepare:

```text
bossman-gateway/
  app/
    api/
    auth/
    routing/
    health/
    telemetry/
    clients/
  tests/
  docker/
  docs/
```

Initial endpoints:

```text
POST /v1/chat/completions
POST /v1/responses
POST /v1/embeddings
GET  /v1/models
GET  /health
GET  /metrics
POST /auth/device
POST /auth/revoke
```

Do not hardcode the final models.

---

# 3. AI VIDEO FACTORY — P0

## Goal

Separate media-production module visible from the main BOSSMAN dashboard.

It should feel like a local **Higgsfield-style production workspace**, not be mixed with the photo generator.

Dashboard entry:

```text
BOSSMAN
 ├─ Agents
 ├─ Search / Memory
 ├─ Coding Factory
 ├─ Image Studio
 ├─ VIDEO FACTORY   ← separate app
 └─ ...
```

---

## 3.1 Pipeline

```text
Idea / script
   ↓
Scene planner
   ↓
Storyboard
   ↓
Reference images
   ↓
Video generation
   ├─ local model
   ├─ browser worker
   └─ external provider if user allows
   ↓
Result watcher
   ↓
Quality checker
   ↓
Retry / alternative prompt
   ↓
Upscale / interpolation
   ↓
FFmpeg assembly
   ↓
Final export
```

---

## 3.2 Required Features

- project folders
- scene list
- prompt per scene
- reference images
- negative prompt
- seed where supported
- duration
- aspect ratio
- provider/model
- job queue
- state:
  - queued
  - submitted
  - processing
  - ready
  - failed
  - retrying
- preview
- output download
- scene replacement
- automatic stitching
- prompt/version history
- crash recovery
- checkpoint after every completed scene

Browser automation must respect provider limits and must stop on CAPTCHA / account / billing / policy blocks instead of bypassing them.

---

# 4. DOCUMENT / OFFICE AGENT — P1 LIGHT

Keep this deliberately simple.

Capabilities:

- read PDF
- read/write DOCX
- read/write XLSX/CSV
- summarize
- extract tables
- convert formats
- fill simple templates
- create standard documents
- organize files

Do not turn this into a giant enterprise document platform.

Architecture:

```text
office-lite/
  ingest
  extract
  transform
  generate
  export
```

Use existing BOSSMAN file permissions and approvals.

---

# 5. SECURITY MINI-AGENT — P1 LIGHT

## Goal

Very small local security watcher that uses negligible resources and stays visually quiet.

It must **never interfere with normal work automatically**.

---

## 5.1 Randomized Scheduling

Do not run continuously.

Example policy:

```text
minimum interval: 8 hours
maximum interval: 36 hours
jitter: randomized
```

Additional manual deep scan button.

The schedule itself should avoid predictable resource spikes.

---

## 5.2 Lightweight Checks

- accidental secrets in tracked files
- new public repository exposure
- dangerous file permissions
- new unexpected listening ports
- suspicious `.env` tracking
- GitHub hygiene violations
- stale dependencies summary
- unexpected executable downloads
- browser profile permissions
- disk usage anomalies

No intrusive antivirus replacement.

---

## 5.3 Output

Only show:

```text
Security: OK
```

when normal.

Raise a visible notification only for meaningful findings.

Never auto-delete files or kill services without explicit approval.

---

# 6. KNOWLEDGE DISTILLERY — P0

## Goal

Periodically compress accumulated context into high-quality durable memory.

Raw knowledge remains intact.

Distillation creates smaller representations for agents.

---

## 6.1 Workflow

```text
new sessions / docs / tasks
        ↓
dedupe
        ↓
extract facts + decisions + failures
        ↓
compare to existing memory
        ↓
detect contradictions
        ↓
merge / supersede
        ↓
produce compact memory
        ↓
retain provenance links
```

---

## 6.2 Distillation Levels

### L0 Raw
Original source.

### L1 Extracted
Facts, entities, decisions.

### L2 Project summary
Compact project state.

### L3 Agent memory
What the specific agent needs.

### L4 Global BOSSMAN memory
Only the most reusable knowledge.

---

## 6.3 Rules

Never delete source because it was distilled.

Never promote a low-confidence statement into permanent fact silently.

Track:

- source
- timestamp
- confidence
- superseded_by
- verification state

---

# 7. SYNTHETIC DATA / FINE-TUNING LAB — P0

## Goal

Experiment with LoRA/fine-tuning/synthetic datasets while keeping strict visibility into hardware usage.

Primary focus is not "train everything".

Primary focus:

- measure
- compare
- control
- stop before resource exhaustion

---

## 7.1 Resource Dashboard — Mandatory

Always display:

- RAM total
- RAM used
- model memory
- KV/cache if observable
- GPU/unified memory
- disk free
- dataset size
- checkpoints size
- expected next-checkpoint size
- temperature if available
- current throughput
- elapsed time
- estimated remaining time

---

## 7.2 Hard Limits

User-configurable:

```yaml
limits:
  max_ram_percent: 88
  min_disk_free_gb: 100
  max_dataset_gb: 50
  max_checkpoints: 3
  auto_stop_on_limit: true
```

Never start training when projected disk usage violates reserve.

---

## 7.3 Dataset Lifecycle

```text
source data
  ↓
synthetic generation
  ↓
quality filter
  ↓
dedupe
  ↓
PII/secrets filter
  ↓
train/dev/test split
  ↓
version dataset
  ↓
train
  ↓
benchmark
  ↓
keep or discard adapter
```

Every dataset and adapter gets version + provenance.

---

# 8. AI SANDBOX — P0, MANUAL ENABLE ONLY

## Critical design rule

**Sandbox is completely inactive by default.**

It must not consume meaningful RAM/CPU/GPU when disabled.

It starts only when the user explicitly enables it.

Status in dashboard:

```text
AI SANDBOX
STATE: OFF
[ START SANDBOX ]
```

When OFF:

- no VM/container workers running
- no open inbound ports
- no background scanning
- no mounted private secrets
- no access to production browser profiles
- no access to main credentials

---

## 8.1 Purpose

Safe place for agents to:

- clone unknown GitHub repositories
- run untrusted scripts
- inspect packages
- test browser automation
- build experimental environments
- execute generated code
- intentionally break things

without exposing the host BOSSMAN environment.

---

## 8.2 Isolation Target

Preferred progression:

### Prototype
Rootless container with strict restrictions.

### Better
Dedicated VM.

### Best
Ephemeral VM/container created per task.

Sandbox gets:

- isolated filesystem
- restricted network
- CPU quota
- RAM quota
- disk quota
- process limit
- timeout
- no host Docker socket
- no SSH keys
- no production `.env`
- disposable workspace

---

## 8.3 Network Modes

```text
OFFLINE
ALLOWLIST
INTERNET
```

Default: `OFFLINE`.

`INTERNET` should require explicit user action.

---

## 8.4 Destruction

One button:

```text
DESTROY SANDBOX
```

Destroys the environment and temporary data.

Artifacts explicitly exported by the user survive.

---

# 9. SHARED OBSERVABILITY

All new modules should expose a minimal common telemetry format:

```json
{
  "service": "...",
  "state": "idle|busy|error|off",
  "ram_mb": 0,
  "disk_mb": 0,
  "cpu_percent": 0,
  "active_jobs": 0,
  "last_error": null,
  "updated_at": "..."
}
```

Main dashboard should show this without visually overwhelming the user.

---

# 10. MAIN DASHBOARD INTEGRATION

Suggested high-level structure:

```text
BOSSMAN CONTROL CENTER

CORE
 ├─ Agents
 ├─ Tasks
 ├─ Approvals
 └─ ComputerUse

KNOWLEDGE
 ├─ Search
 ├─ Memory
 └─ Distillery

BUILD
 ├─ Coding Factory
 ├─ Office Lite
 └─ AI Sandbox [OFF]

MEDIA
 ├─ Image Studio
 └─ Video Factory

AI INFRA
 ├─ Model Gateway
 ├─ Model Arena
 ├─ Fine-tuning Lab
 └─ Resource Monitor

SECURITY
 └─ Mini Security
```

Separate applications may open in a new tab, while the dashboard remains the control center.

---

# 11. CONTEXT IMPROVEMENT — NEXT IMPLEMENTATION FOCUS

This is the next priority after documenting the modules above.

## 11.1 Main Problem

Large context windows alone do not solve memory.

Problems:

- irrelevant context
- duplicate context
- stale facts
- contradiction
- forgotten decisions
- wasting tokens rereading the same files
- important information buried in huge histories

The solution is a **Context Fabric**, not simply increasing `context_length`.

---

## 11.2 Recommended Context Architecture

```text
                ┌──────────────────┐
                │ CURRENT TASK     │
                └────────┬─────────┘
                         ↓
                Intent / Entity Parse
                         ↓
       ┌─────────────────┼─────────────────┐
       ↓                 ↓                 ↓
Working Memory     Semantic Search    Structured Memory
       ↓                 ↓                 ↓
       └──────────── Candidate Pool ───────┘
                         ↓
                      Reranker
                         ↓
                  Contradiction Check
                         ↓
                    Context Packer
                         ↓
                       MODEL
                         ↓
                 Memory Candidate
                         ↓
                  Memory Gatekeeper
                         ↓
                 Long-term Storage
```

---

## 11.3 Four Context Pools

### A. System context
Rules, permissions, agent identity.

Keep short and stable.

### B. Task context
Only information needed for the active job.

Expires quickly.

### C. Retrieved context
Dynamically retrieved evidence.

Must include provenance.

### D. Memory context
Stable decisions/facts relevant to the query.

Never dump full memory blindly.

---

## 11.4 Reranking

The biggest practical gain after embeddings is reranking.

Pipeline:

```text
retrieve top 50–100 cheap candidates
        ↓
rerank top candidates
        ↓
keep best 5–20
```

Use a local cross-encoder/reranker if hardware permits.

---

## 11.5 Query Expansion

Before search, generate:

- alternate terms
- project aliases
- filenames
- entity names
- Russian/English variants
- likely code identifiers

Example:

```text
"browser управление агентами"

expands to:
ComputerUse
browser automation
Playwright
browser.py
agent tools
Chromium
computer use
```

This greatly improves retrieval across mixed-language projects.

---

## 11.6 Time-aware Retrieval

Questions often implicitly have time.

Examples:

- latest architecture
- before V1.2
- what changed after audit
- previous implementation

Store timestamps and version relationships.

Do not let an obsolete spec outrank the active one merely because wording matches better.

---

## 11.7 Decision Graph

Create explicit decision records:

```text
DEC-0001
Decision: Browser control is a shared Core capability.
Status: active
Supersedes: none
Sources: ...
```

Later:

```text
DEC-0015
Decision: Replace X with Y.
Status: active
Supersedes: DEC-0008
```

Search can then reason over active architecture instead of old documents.

---

## 11.8 Failure Memory

Every important failure should be searchable:

```json
{
  "type": "failure",
  "component": "computer-use",
  "symptom": "...",
  "cause": "...",
  "fix": "...",
  "verified_by": "emulator-e2e",
  "date": "..."
}
```

This is extremely valuable for coding agents.

---

## 11.9 Context Cache

Cache expensive retrieval results by:

- query fingerprint
- project
- active branch/version
- memory revision

Invalidate when relevant source content changes.

This reduces repeated embedding/search/reranking work.

---

## 11.10 Context Quality Metrics

Track per task:

- retrieved chunks
- used chunks
- source diversity
- duplicate ratio
- average age
- reranker score
- context tokens
- model output quality
- user correction rate

Over time BOSSMAN can learn which retrieval strategy works best.

---

# 12. CLAUDE CODE — FIRST CONTEXT MILESTONE

Do not build the entire context system in one pass.

### Milestone C1

Implement:

1. normalized document schema;
2. ingestion from Markdown + source code + agent memories;
3. lexical search;
4. vector search;
5. hybrid merge;
6. reranker interface;
7. provenance;
8. context packer;
9. deduplication;
10. tests.

### Milestone C2

Add:

- decisions
- contradictions
- freshness
- project/entity filters
- failure memory
- query expansion

### Milestone C3

Add:

- Knowledge Distillery
- automatic memory candidate extraction
- memory promotion rules
- quality analytics
- context cache

---

# 13. NON-GOALS

Do not:

- dump every document into every prompt;
- trust embeddings as the only retrieval method;
- give all agents all memories;
- silently overwrite contradictions;
- automatically run Sandbox;
- automatically execute downloaded files;
- expose raw model servers directly to the public internet;
- allow fine-tuning to consume all disk/RAM;
- make the lightweight security module visually noisy.

---

# 14. ACCEPTANCE PRINCIPLES

A feature is not complete until:

- tests pass;
- resource impact is measured;
- security assumptions are documented;
- restart behavior is tested;
- the feature can be disabled cleanly;
- logs are understandable;
- no secrets are committed;
- relevant audit documentation is updated.

---

# 15. NEXT DISCUSSION

**Next focus: improving model context.**

Priority order for design discussion:

1. ingestion format;
2. embedding model;
3. lexical engine;
4. reranker;
5. chunking;
6. structured memory;
7. decision graph;
8. Knowledge Distillery;
9. context packing;
10. evaluation benchmark.

