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
