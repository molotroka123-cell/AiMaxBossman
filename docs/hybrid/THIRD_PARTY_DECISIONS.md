# Third-Party OSS Supply-Chain and License Decisions

This document records the independent architectural, supply-chain, license, and capability audit for candidate open-source software (OSS) components evaluated for Bossman Hybrid integration.

---

## Evaluation Gate Summary

| Candidate Component | Upstream / Domain | License | Decision | Primary Technical Reason |
| :--- | :--- | :--- | :--- | :--- |
| **Windows-MCP** | Windows UI Automation MCP | MIT / Apache-2.0 | **ADOPT_AS_OPTIONAL_BACKEND** | Useful for auxiliary Windows UI mechanics, but must be strictly gated by pre-effect fingerprinting and independent Bossman post-state verification. |
| **browser-use** | Browser agent mechanics | MIT | **REFERENCE_ONLY** | Bossman's native Playwright session already enforces strict human challenge takeover (`WAITING_FOR_OWNER_CHALLENGE`), domain fencing, and deterministic retry bounds. browser-use risks uncontrolled retries and false completion claims. |
| **GrapesJS** | Web visual editor / canvas | BSD-3-Clause | **ADOPT_AS_OPTIONAL_BACKEND** | Mature visual editing mechanics suitable as an optional canvas frontend for Web Designer; project schema, persistence, and idempotent apply must remain Bossman-owned. |
| **Weave** | Video composition / timeline | Apache-2.0 / MIT | **REFERENCE_ONLY** | Bossman's native FFmpeg 6.1.1 pipeline is already proven, deterministic, and CFR-compliant (343 tests). Weave adds unnecessary packaging bloat and process overhead. |
| **LocalAI** | Local model serving | MIT / Apache-2.0 | **REJECT** | Unnecessary orchestration overhead. Fails to provide optimal unified memory utilization on AMD Ryzen AI Max+ (Radeon 8060S, 128 GB unified memory). Direct llama.cpp / ROCm / Vulkan gateway is vastly superior. |
| **OpenContext** | Cross-agent project context store | MIT | **ADOPT_AS_OPTIONAL_BACKEND** | Shadow mirror only. Bossman's own store stays authoritative and stays the only one; namespace isolation is re-checked on Bossman's side over whatever the backend returns. |
| **ai-file-sorter** | Content-aware file naming | AGPL-3.0-or-later | **ADOPT_AS_OPTIONAL_BACKEND** | External sidecar, never vendored. The AGPL is precisely why: copyleft with a network clause would attach to a combined work, so the two programs share a process boundary and nothing else. |
| **Open-Source-Face-Recognition-SDK** | Face detection / embeddings | **NONE — no licence file exists** | **REJECT** | No LICENSE, LICENSE.md, LICENSE.txt or COPYING at the pinned SHA (all four 404). An unlicensed work grants no distribution rights; a shields.io badge reading "Open Source" is not a licence. |
| **video-shotcraft** | Cinematic shot recipes / Remotion | Apache-2.0 (plus Remotion's own commercial terms) | **REFERENCE_ONLY** | The value is the knowledge layer — 152 shot recipes, storyboard and rhythm patterns — and knowledge ports without code. The executable half is a second browser engine per render on top of a proven FFmpeg pipeline. |

---

## Exact versions these decisions were taken against

Every SHA below was read with `git ls-remote <upstream> HEAD` on 2026-09-11, not
copied from a README. `docs/hybrid/sources.lock.json` carries the same values in
machine-readable form together with network, telemetry, subprocess, memory-cost
and fallback fields.

| Project | Decision | Exact SHA | Default? | Memory cost | Failure fallback |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Windows-MCP | ADOPT_AS_OPTIONAL_BACKEND | `08ddee78c26182b103d62c1c84c1fbec82a280b2` | No | sidecar process, task-scoped | legacy desktop runtime; fails closed |
| browser-use | REFERENCE_ONLY | `50f205533fe10ba35b553d2a3689c77b87bd5d0a` | No | 0 — not integrated | native Playwright browser runtime |
| GrapesJS | ADOPT_AS_OPTIONAL_BACKEND | `2bdeda85b82b8b9ceae42fd6558cbbcae5ec2d21` | No | in-page JS, no process | native Web Designer editing |
| Weave (`wandb/weave`) | REFERENCE_ONLY | `cc4a636dc27a482317470c9c7d8191b49469a8b7` | No | 0 — not integrated | native FFmpeg pipeline |
| LocalAI | REJECT | `c0993e580aed393f5ab509514396207b55ac1755` | No | 0 — not integrated | direct local gateway |
| OpenContext | ADOPT_AS_OPTIONAL_BACKEND (shadow) | `0649e7134346f6f5038a9b29cc5c824ae6a54f3f` | No | 0 without a transport | native memory; mirror failure is recorded, never raised |
| ai-file-sorter | ADOPT_AS_OPTIONAL_BACKEND (external sidecar) | `4dc374df69b5e63d5354e121097d92e25bbd32da` | No | sidecar process, task-scoped | native File Intelligence; unverifiable binary reports VERSION_UNVERIFIED |
| Open-Source-Face-Recognition-SDK | REJECT | `621718e7d3c6c708631e15bbaaedbd88ac1b439c` | No | 0 — not integrated | none: the product has no face-recognition capability |
| video-shotcraft | REFERENCE_ONLY | `5e71af35a2daee492dd3ea93e5e8903f32dcd13c` | No | 0 — knowledge costs no runtime memory | native Video Studio planning and render |

---

## Detailed Project Audits

### 1. Windows-MCP
- **Decision:** `ADOPT_AS_OPTIONAL_BACKEND`
- **License & Dependencies:** MIT / Permissive. Minimal external binary bloat when used as a sidecar.
- **Security & Authorization:** Protocol preserves basic window element identifiers, but lacks effect-time target verification out of the box. Windows-MCP alone cannot prevent race conditions where a target window shifts focus between planning and click execution.
- **Integration Boundary:** Must be placed behind the `DesktopRuntime` capability interface and `AdapterRegistry`. Must execute through a guarded sidecar process.
- **Bossman Authority Guard:** Every click operation requires an immediate target fingerprint pre-check and an independent post-state verification check (e.g. window state, file creation, or process table check). External engine success status never satisfies Bossman task completion truth.

### 2. browser-use
- **Decision:** `REFERENCE_ONLY`
- **License & Dependencies:** MIT. Requires extensive browser agent dependencies and autonomous loop tooling.
- **Evaluation against Existing Bossman Baseline:** Bossman already features a battle-tested browser runtime (`bcc/browser_runtime.py`, `AccountBrowserSession`, `test_p0_completion_truth.py`) which correctly handles anti-bot challenges (`WAITING_FOR_OWNER_CHALLENGE`), owner takeover, and domain isolation.
- **Identified Failure Modes:** browser-use employs autonomous internal looping that hides retry budgets from the Bossman governor. Furthermore, its natural-language status output frequently asserts task success even when real page effects were blocked by CAPTCHA or DOM mutation.
- **Verdict:** Do not replace Bossman's browser runtime. Maintain as reference for prompt evaluation only.

### 3. GrapesJS
- **Decision:** `ADOPT_AS_OPTIONAL_BACKEND`
- **License & Dependencies:** BSD-3-Clause. Clean JavaScript/TypeScript library with no restrictive copyleft implications.
- **Architectural Fit:** Provides rich visual DOM manipulation and styling canvas. Can serve as an enhanced UI canvas component for Web Designer.
- **Constraints & Guardrails:** AI edits must be dispatched via typed operation frames rather than unrestricted innerHTML injection. Existing Bossman web designer projects must migrate losslessly. Apply operations must remain idempotent (verified by positive and negative controls). Project persistence, recovery UX, and file export remain strictly Bossman-owned.

### 4. Weave
- **Decision:** `REFERENCE_ONLY`
- **License & Dependencies:** Apache-2.0. Requires separate runtime environment and schema bridges.
- **Comparison with Native Video Studio:** Bossman Video Studio features an established, highly optimized FFmpeg pipeline with 343 passing tests, frame-accurate CFR rendering, container selection, and render receipts.
- **Supply-Chain & Packaging:** Bundling Weave or executing it as an external sidecar complicates packaging and introduces IPC overhead without providing demonstrable rendering gains over direct FFmpeg composition.

### 5. LocalAI
- **Decision:** `REJECT`
- **License & Dependencies:** MIT / Apache-2.0. Large binary footprint with multiple bundled backend engines.
- **Hardware Target Mismatch:** The target hardware is AMD Ryzen AI Max+ with Radeon 8060S and 128 GB unified memory. LocalAI's multi-backend abstraction adds latency and memory allocation overhead, and does not provide first-class ROCm/Vulkan memory fencing compared to Bossman's direct gateway architecture.
- **Memory & Admission Authority:** LocalAI manages its own model lifecycle opaque to Bossman's resource governor. Bossman requires authoritative control over RAM/VRAM budget allocation (`bcc/features/resources.py` and `test_v7_resource_pressure_edges.py`). Adding LocalAI creates an unnecessary intermediary layer with no architectural benefit.

### 6. OpenContext
- **Decision:** `ADOPT_AS_OPTIONAL_BACKEND` — shadow mirror only, never the default.
- **Exact version:** `0649e7134346f6f5038a9b29cc5c824ae6a54f3f` (`git ls-remote HEAD`, 2026-09-11).
- **License:** MIT. Compatible with distributing Bossman; nothing is vendored regardless.
- **What it is allowed to own:** nothing. Bossman's own store stays authoritative and
  stays the only store: `BossmanNativeContextStore` writes to the same `decisions`
  table `bcc.context_os.DecisionStore` already uses, versioned by the same
  `superseded_by` column. A second memory beside the first would drift, and then
  "we decided this" stops being one fact.
- **Boundary:** `ContextStoreRuntime` (`command-center/bcc/hybrid/context_store.py`).
  The shadow adapter's candidates carry `is_authoritative=False` as a hard-coded
  property, not a constructor parameter — making an external store authoritative
  must require a code change and a review, not an environment variable.
- **Namespace isolation is checked twice** — once in the request, once over the
  answer. The second check is the only place where a misconfiguration or a drifted
  protocol in a third-party store does not become one repository's context leaking
  into an answer about another. The external store is a source of data, never a
  source of truth about whom that data belongs to.
- **Secrets:** the filter sits in front of *both* stores. Guarding only the mirror
  would leave Bossman's own memory as a place where a secret lies in the clear.
  Credential files are refused outright (there is no redacted form of a `.env`);
  a secret inside otherwise useful text is replaced by a marker that names the
  *kind* of finding, and the redaction is recorded in the record's provenance.
- **Failure behaviour:** absent, unreachable, malformed and silent are four
  different cases with one outcome — the native write stands, the task does not
  fail, and the mirror's silence is visible in `last_mirror_error` rather than
  read as agreement.
- **Promotion to primary:** not now, and not by configuration. §7 requires
  prolonged equivalence/superiority testing first; nothing in this run measured that.

### 7. ai-file-sorter
- **Decision:** `ADOPT_AS_OPTIONAL_BACKEND` — an external sidecar the owner installs.
- **Exact version:** `4dc374df69b5e63d5354e121097d92e25bbd32da` (`git ls-remote HEAD`,
  2026-09-11 — unchanged from the SHA already pinned in
  `integrations/ai-file-sorter/integration.json`).
- **License:** AGPL-3.0-or-later. This is the preflight §8 asked for, and it is
  the reason the integration is shaped the way it is: the AGPL is copyleft with a
  network clause, and vendoring the code into Bossman would attach it to the
  combined work. No file under `bossman-core/`, `command-center/`, `learning/` or
  `bossman_shared/` derives from the upstream tree; the only upstream artefacts in
  this repository are the manifest, the NOTICE, and the protocol constants those
  two record.
- **What Bossman keeps:** permissions, preview, approval, path protection, conflict
  detection, the rename journal, undo, evidence and completion truth. The manifest
  lists `--auto-apply` and its variants as forbidden flags and sets
  `auto_apply_allowed: false`.
- **Honesty about the binary:** a pinned manifest does not make a running binary
  pinned. Discovery reports `VERSION_UNVERIFIED` when the installed executable
  cannot prove its source identity — which is a refusal, not a silent success.

### 8. Open-Source-Face-Recognition-SDK
- **Decision:** `REJECT`. No `VisionIdentityRuntime` port was written and no code was taken.
- **Exact version examined:** `621718e7d3c6c708631e15bbaaedbd88ac1b439c`.
- **License: none.** Measured, not assumed: at that SHA, `LICENSE`, `LICENSE.md`,
  `LICENSE.txt` and `COPYING` all return HTTP 404. The only licence-shaped thing in
  the README is a shields.io badge whose text reads "license: Open Source" — an
  image, not a grant. A work published without a licence is protected by default,
  so the distribution rights available to us are zero.
- **Secondary reason:** the Python entry point drives native blobs through `ctypes`;
  their provenance is not verifiable from the repository.
- **Consequence, stated plainly:** Bossman has no face-recognition capability. §9
  explicitly allows this optional subsystem to be unsuitable without failing the
  run, and it is not required for the product to boot.
- **What would reopen it:** the rights holder publishing an explicit, compatible
  licence. Until then: no vendoring, no shipping, no optional backend.

### 9. video-shotcraft
- **Decision:** `REFERENCE_ONLY` — adopt the knowledge, decline the runtime.
- **Exact version:** `5e71af35a2daee492dd3ea93e5e8903f32dcd13c`.
- **License:** Apache-2.0 for the project itself. Its render path is Remotion, which
  carries its own commercial-use terms for companies — a second licensing question
  layered on top of a pipeline that already works.
- **What is worth having:** the shot-recipe corpus, storyboard structure, motion and
  rhythm patterns, and the screenshot-to-promo flow. That is a knowledge layer, and
  a knowledge layer ports without importing a dependency.
- **What is not worth having:** Remotion plus a headless Chromium per render, beside
  the Chromium Bossman already ships. §16 forbids duplicate browser engines and idle
  daemons; §10 asks specifically not to replace the proven export pipeline for the
  sake of attractive demos.
- **Correction recorded against the earlier Weave row:** the candidate named "Weave"
  in §14 is supposed to be a video composition/timeline engine, but the URL carried
  in the lock file — `wandb/weave` — is an LLM observability and tracing toolkit,
  unrelated to video editing. No video-engine URL was ever supplied. The candidate is
  therefore **not identified**, and adopting a project when you cannot say which
  project it is would not be a decision. `REFERENCE_ONLY` stands, now for the real
  reason; the independent reason also stands on its own — the native FFmpeg pipeline
  is deterministic and covered, and §14 permits switching a production path only when
  the new backend is at least as reliable as the old one.

---

## MCP Connectors (§25, decided 2026-09-14)

These five were evaluated as *connectors* — external processes Bossman speaks MCP
to — rather than as libraries to vendor. The gate was a purpose-built acceptance
lane (`tools/mcp_acceptance.py`, 16 probe modes, its own minimal stdio client, no
third-party dependency), and every row below cites a measurement rather than a
README. Evidence: `docs/final/mcp_acceptance.json`,
`docs/final/connector_benchmarks.json`. Full reasoning:
`docs/final/CONNECTOR_INTEGRATION_REPORT.md`.

| Connector | Exact SHA | License | Decision | Default? | Idle cost | Acceptance result |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| markitdown-mcp | `eb31b5c9453628def5e6758a27a8e3a87b4ab101` | MIT (LICENSE read at the SHA) | **ADOPT_AS_OPTIONAL_BACKEND** (off, task-scoped, allowlisted paths, URL converters disabled) | No | 171 MiB, 1 process, 10 ms CPU / 30 s | 15 PASS, 1 NOT_RUN |
| playwright-mcp | `e73d72e01f162054a3d0a6b0fe8d4affffb095ee` | Apache-2.0 (LICENSE read at the SHA) | **REFERENCE_ONLY** | No | 913 MiB, 11 processes with a page open | 15 PASS, 1 NOT_RUN |
| github-mcp-server | `7d13a7ad6f2a17f351a6d77ce280c85ae1821f4d` | MIT (LICENSE read at the SHA) | **NOT_ACCEPTED_PENDING_CREDENTIALED_RUN** | No | 0 — does not start without credentials | 1 NOT_RUN (clean fail-closed) |
| context7 | `b653c3a07d7936bdc4c23fc1c88903120e0ece77` | MIT (LICENSE read at the SHA) | **REJECT_AS_RUNTIME_CONNECTOR** | No | not measured — never started | none run |
| MCP Inspector | `795b1bb30ac845b7baa7cb3df8ec0b693882ca1d` | MIT **declared in package.json; no licence file exists at this SHA** | **DEV_TOOL_ONLY** | No | 0 in production by construction | not applicable |

**Connectors enabled by default: zero. Idle production cost added by this
section: zero.**

### Why each, in one paragraph

**markitdown-mcp — the only adoption, and the boundary is narrow.** §24 wants a
demonstrated improvement, so one was measured. On a corpus written by *other
people's* writers (openpyxl, python-pptx, PIL; the PDF and DOCX assembled to the
file-format spec) MarkItDown recovered 19 of 19 planted strings against the native
`parse_file`'s 17 of 19. The entire difference is two markers inside an `.epub`,
which the native path sees as an ordinary zip. On everything both handle the native
path is faster, and the honest number is not the headline one: the totals are 73 ms
vs 137 ms, but 57 of the native 73 are a one-time `pypdf` import inside the process
rather than work on files — excluding it, ~16 ms vs ~126 ms. And that comparison
was library-to-library, charging MarkItDown nothing for the subprocess and stdio
round trip it costs in its connector form. The native path also carries provenance
— `sha256`, `ref=page=1`, `ref=A1` — that MarkItDown does not emit at all. Hence the rule:
MarkItDown is consulted **only** where the native path raised `ParseUnavailable` or
returned a container listing for something that is not a container — epub, `.msg`,
legacy `.xls`, audio. A third discrepancy, PDF, turned out to be **our** defect
(BL-036: `pypdf` was imported but declared in no dependency or extra anywhere), not
a win for external code, and is fixed by declaring the `documents` extra. The
YouTube / Wikipedia / Bing / RSS converters are disabled: they are precisely the
"arbitrary URL fetching" §7 forbids by default, and they would turn reading a file
into an outbound request. Output is an OBSERVATION, tagged as external data,
granting no authority (§18).

**playwright-mcp — healthy, and still declined.** It passed 15 of 16 probes,
declares its own protocol version instead of echoing ours, survives garbage on the
wire and restarts after SIGKILL. The refusal is not about quality: no capability
beyond the native browser runtime was demonstrated, and the price was measured —
913 MiB across 11 processes with one page open, a second language runtime, and a
second browser channel (it would not start until given `--executable-path` and
`--no-sandbox`). `bcc/browser_runtime.py` already does owner takeover on a
challenge, domain fencing and bounded retries. One measured detail is worth
keeping for §6: navigating to a 200 and to a 404 both come back with **no**
`isError` and no JSON-RPC error, so the two are indistinguishable from the result
envelope; the 404 status is present but only as prose in the body, and on the
successful reply no such line exists at all. The connector is not lying — but a
machine that trusts `isError` concludes the navigation succeeded. That is the
measured basis for re-reading page state ourselves.

**github-mcp-server — nothing to accept yet, and that is what is recorded.** It
was deliberately started **without** credentials: §5 forbids keeping GitHub
authentication in project memory and asks for dedicated minimum-privilege
credentials, and this session's token is neither, nor reproducible for the owner.
Exactly one thing was measured, and it is a good one — the server **fails closed**:
no half-start, no empty tool list, no pretending, just `authentication required:
set GITHUB_PERSONAL_ACCESS_TOKEN…`. The lane recorded `NOT_RUN` rather than `FAIL`,
because a policy refusal is not a defect. But `NOT_RUN` is not an acceptance either:
one probe out of sixteen, and no capability tested. Unblocking condition is named —
a dedicated GitHub App scoped to read the needed repositories, `--read-only`,
narrowed toolsets, and a re-run of the lane.

**context7 — not measured, therefore not adopted.** No probe was run. Under §24
that alone settles it, and inventing technical reasons after the fact instead of
measuring would be fitting the report to a conclusion. The reason it would not be
a default even after acceptance is also named: every lookup tells a third party
which library the product is studying, and retrieved documentation is DATA that
grants no authority (§18).

**MCP Inspector — a developer's tool, and only that.** §9 is verbatim: it must not
ship as an always-running production process, zero idle production cost. It does
not ship at all — the acceptance lane is our own client, so Inspector is not needed
even for debugging in production. A supply-chain note, checked rather than assumed:
**no licence file exists at the pinned SHA.** `LICENSE`, `LICENSE.md`,
`LICENSE.txt`, `COPYING`, `license` and `LICENCE` all return 404 while the control
request for `package.json` at the same SHA returns 200 — so the 404s are an absent
file, not a network failure. MIT is declared by the `license` field in
`package.json`, which npm treats as sufficient; for a tool that is never
distributed with the product that is enough, and the fact is recorded so the
decision is not revisited from memory.

---

## Low-overhead candidates (§6 of the next-run brief, decided 2026-09-15)

Five projects were proposed on the grounds of being cheap: small at rest, no
separate daemon, tied to something already measured in this repository. §24
still applies to every one of them — **a proposal without a number is not a
reason** — so the question asked of each was not "is it good?" but "what does
it improve, measured against what?"

| Project | Exact SHA | License | Decision | Measured? |
| :--- | :--- | :--- | :--- | :--- |
| uv | `643950ce49e45b7ec0ffe5baffa319d92ea00a96` | Apache-2.0 OR MIT (both files read at the SHA) | **ADOPT_AS_BUILD_TOOL** | **yes — 11.4× cold, 115× warm** |
| watchfiles | `42257e82089c3ef880aba8a2f2fc1662a663d61e` | MIT | **REFERENCE_ONLY** | premise withdrawn before measuring |
| fastembed | `0dab99c23e659e630f642b42c5888af87befb6e2` | Apache-2.0 | **REJECT** | yes, previously — verdict stands |
| sqlite-vec | `04d28bd21773981e2d266bbf6aa4efbd011eb4f6` | Apache-2.0 OR MIT (both files read at the SHA) | **DEFERRED** | nothing to measure against |
| faster-whisper | `ed9a06cd89a93e47838f564998a6c09b655d7f43` | MIT | **DEFERRED** | nothing to measure against |

**Runtime dependencies added: zero.** The single adoption never runs in the
product.

### uv — the only one with a number

The nine runtime dependencies of Command Center, installed into a clean
environment by each tool in turn:

| | pip | uv | |
|---|---:|---:|---|
| cold tool cache | 12.5 s | **1.1 s** | 11.4× |
| warm cache | 11.5 s | **0.1 s** | 115× |
| packages installed | 32 | 32 | identical outcome |

Both phases are reported because the first run of either tool warms its own
cache, and quoting only the warm figure would flatter uv. It is adopted strictly
as a **build tool**: absent from the runtime, absent from every dependency list,
so its idle cost is zero and §7's rule about daemons does not apply to it at
all. `pip` stays a working path and is exercised by the same acceptance script —
that is the rollback.

**Where uv must not go, stated so the gap is not mistaken for unfinished work.**
`scripts/verify_clean_install.py` deliberately keeps installing with `pip`, and
its installer must not be swapped. That script exists to prove **the owner** can
install the product, and the owner installs with pip; replacing the installer
would produce a green acceptance for a path nobody walks. uv does not always
resolve identically to pip, so "passed under uv" is not "will pass under pip".
uv's place is the developer and CI loop, where speed matters and reproducing the
owner's exact path does not.

### watchfiles — the premise was withdrawn, not the project

It was proposed for exactly one named number: *"3.0 % of one core on polling the
queue once a second."* That number was re-measured and turned out to describe a
different place. An idle Command Center burned **~4.5 % of a core**, and not on
the queue poll: `task_exchange` re-read nine `app.manifest.yaml` files at **nine
parses per second**. Closed with a `(mtime_ns, size)` cache — **no new
dependency** — down to **~1.2 %** (BL-037).

So the justification for adopting it no longer exists. The remaining ~1.2 %
must first be measured and attributed before anyone adds a Rust dependency;
adopting on the strength of a withdrawn premise would be adoption by link
rather than by measurement, which §24 forbids.

### fastembed — already measured, verdict unchanged

Not a new decision. `docs/research/qdrant.md` and `docs/research/lancedb.md`
already measured it and recorded **"не устанавливать"**: `qdrant-client[fastembed]`
downloads ONNX models from the network on first use — a hidden network trip and
a hidden memory cost. An encoder, if one ever appears, must be explicit and
ours. No new evidence was offered to reopen it.

### sqlite-vec and faster-whisper — deferred, and the condition is named

Neither is refused on merit; both are **unmeasurable today**, which is a
different thing and is recorded as such.

- **sqlite-vec:** the product has no vector search at all, so there is no
  baseline for the before/after §24 demands. It is cheap on its face — an
  extension to the SQLite already in use, not one new process — which is
  precisely why it stays in the register rather than being discarded. Unblocked
  by the owner's decision on dense embeddings, still open per `qdrant.md`.
- **faster-whisper:** the question is not the library — `transcribe` already
  exists in `bcc/video_studio/analysis.py`. The question is that it carries a
  **local model**, i.e. resident memory on a 128 GB unified bus — exactly the
  budget §7 leaves to Bossman's governor and exactly what must not be reserved
  without a decision. Until the owner names a model and a ceiling, there is
  nothing to benchmark, and integrating it would spend that memory on their
  behalf.
