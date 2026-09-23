# HW-01…HW-13 — owner hardware (OR0923)

Ryzen AI MAX+ 395 / Radeon 8060S / 119.6 GB · Windows 11 Pro 26200 · Smart App Control ON · ADMIN_RUN (unless marked) · RUNTIME=OLLAMA_PROXY.
PASS needs a verified effect, not a click or a model sentence. Approvals in the test scope = DELEGATED_BY_OWNER_TEST_SCOPE.

| Case | Status | Verified result / reason |
|---|---|---|
| HW-01 Local model & routing | PASS (runtime substituted) | UI «Найти локальные» found Ollama; models/agents created in the window; real answers (Прага, 391, 144); tool_call works; llama.cpp blocked by SAC → Ollama 0.34.3 + no-think proxy; MAIN ~10 tok/s. Speeds not comparable with llama.cpp numbers |
| HW-02 Windows control | FAIL (product) | computer STOP/resume/persistence work; Notepad task via agent impossible: action contract exposes only apps_* and hides agent computer.* tools (tasks 31–33). No false PASS |
| HW-03 Browser | PASS (download) / P1+P2 found | public PDF downloaded after delegated approval: 13 264 B, %PDF-1.4, sha256 3df79d34…adb4 checked independently; deny → no 2nd file. P1: «Скачай PDF-файл …» wording → false PASS without download; P2 re-download loop after verify FAIL. Login handover NOT_RUN |
| HW-04 Files | PARTIAL | file ops via Web Designer (save/reopen/versions), coding path diffs, MVČR package files, downloads verified. Chat agents have no file tools by default |
| HW-05 Restart/resume | PASS | restarts ×6: tasks/approvals/STOP persist; interrupted coding task → UNKNOWN_OUTCOME, no blind retry; memory recalled after restart. P2 old backend lingering ~8 min when closed mid coding task |
| HW-06 Telegram | OWNER_ACTION_REQUIRED (product path) | companion not started against the test instance (one poller per token; it is bound to the owner's working setup). Owner↔Claude correction channel via the same bot used (sendMessage + inbox) — not the product HW-06 path |
| HW-07 Image Studio | see media section (MEDIA in CHECKPOINT) | providers: Mock + ComfyUI «not connected» |
| HW-08 Video Studio | see media section | — |
| HW-09 Agents/tools | PARTIAL | planner→executor→verifier via coding path: executor = local sidecar, verifier = Bossman verify + hidden verifier; results in learning/. Multi-agent team page NOT_RUN |
| HW-10 MVČR | PASS (synthetic data, live official sources) | Owner-Run mvcr: GET only mv.gov.cz / ipc.gov.cz; correct form with provenance+sha256; timeline only from confirmed facts; fee conflict → question; PARTIAL_MISSING_DATA; nothing submitted/signed/paid |
| HW-11 Unseen task | see D2 in MODEL_AND_LEARNING_RESULTS.json | — |
| HW-12 Long autonomy | NOT_RUN | no 24h/48h soak credited |
| HW-13 Cloud route | PASS with P2 | local-first holds; unpriced cloud blocked; no silent paid fallback with 457 OpenRouter models; explicit cloud agent call cost $0.000039 recorded; global spend meter OFF by default (flag) → owner cap $3 enforced by enabling it; premium approval NOT_RUN |
