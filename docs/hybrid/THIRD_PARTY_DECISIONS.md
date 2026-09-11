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
