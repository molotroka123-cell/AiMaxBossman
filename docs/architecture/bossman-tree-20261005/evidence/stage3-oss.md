# Stage 3 · zone `oss` (Open source · каталог, 121 leaves)

Rule (owner: adopt-and-harden OSS; a reference stays a reference unless Bossman demonstrably uses it):
a `recorded` leaf turns `reported` only through a `kind: integration` receipt (PASS, exit 0, output hash,
sha ancestor of HEAD, non-empty `integration_code` paths that exist in the repo). Enforced in
[tools/tree_apply_evidence.py](../../../../tools/tree_apply_evidence.py), tests in
[command-center/tests/test_tree_apply_evidence.py](../../../../command-center/tests/test_tree_apply_evidence.py).

Proof method ([tools/tree_proof/stage3_oss_integration.py](../../../../tools/tree_proof/stage3_oss_integration.py)):
Bossman's OWN tests for each integration, run at HEAD with the third-party packages of the installed
build (BOSSMAN-Windows-x64-eba6dc592ad9), then a `sys.modules` check that the listed integration modules
were loaded from this checkout (plus a safe local probe for prompt_toolkit and the Codex CLI). Nothing
installed, no models downloaded, no services started, :8801 untouched.

**Counts:** integrated+proven 15 · retired 1 (receipt only, not applied) · still references 105.

"Proven" means Bossman's integration code passed its own tests. For 6 projects the upstream itself does
not run on this machine (contract tests against fakes, see *note*). It is not `working` ("works in the
installed Bossman"), which needs a separate installed-build replay.

Licences: [audit/oss-audit.json](audit/oss-audit.json); unknowns re-queried on the GitHub API on 2026-10-07
(33 still rate-limited). Copyleft to keep out-of-process: AGPL-3.0 (searxng, ai-file-sorter, VoiceStudio),
GPL-3.0 (ComfyUI, shotcut).

| id | project | URL | licence | verdict | integration code | note |
|---|---|---|---|---|---|---|
| oss-1 | cloudflare/security-audit-skill | https://github.com/cloudflare/security-audit-skill | MIT | referenced-only | [config/evolution/sources.lock.json](../../../../config/evolution/sources.lock.json) | pinned in sources.lock.json: upstream skill and validators not installed |
| oss-2 | alibaba/open-code-review | https://github.com/alibaba/open-code-review | Apache-2.0 | referenced-only | [config/evolution/sources.lock.json](../../../../config/evolution/sources.lock.json) | pinned in sources.lock.json; campaign.py only names it as a review source; not installed |
| oss-3 | NousResearch/hermes-agent | https://github.com/NousResearch/hermes-agent | MIT | referenced-only | [config/evolution/sources.lock.json](../../../../config/evolution/sources.lock.json) | pinned in sources.lock.json; not installed |
| oss-4 | gepa-ai/gepa | https://github.com/gepa-ai/gepa | MIT | referenced-only | - | no use in Bossman code |
| oss-5 | karpathy/autoresearch | https://github.com/karpathy/autoresearch | none-detected | referenced-only | - | no use in Bossman code |
| oss-6 | microsoft/agent-lightning | https://github.com/microsoft/agent-lightning | MIT | referenced-only | - | no use in Bossman code |
| oss-7 | SWE-agent/mini-swe-agent | https://github.com/SWE-agent/mini-swe-agent | MIT | referenced-only | - | no use in Bossman code |
| oss-9 | browser-use/jev-ultrafast | https://github.com/browser-use/jev-ultrafast | MIT | referenced-only | [command-center/bcc/jev/upstream.py](../../../../command-center/bcc/jev/upstream.py) | bcc/jev re-implements two ideas (MIT notice kept); upstream not vendored, API contract CONTRACT_UNVERIFIED |
| oss-10 | APUS-AI-Lab/fast-browser-use | https://github.com/APUS-AI-Lab/fast-browser-use | MIT | referenced-only | - | no use in Bossman code |
| oss-11 | mostlygeek/llama-swap | https://github.com/mostlygeek/llama-swap | MIT | referenced-only (integration code present, unproven) | [bossman-core/bossman/api.py](../../../../bossman-core/bossman/api.py)<br>[bossman-core/bossman/config.py](../../../../bossman-core/bossman/config.py)<br>[bossman-core/compose.core.yaml](../../../../bossman-core/compose.core.yaml) | client for /running, /upstream/&lt;m&gt;/health, /unload exists, but no Bossman test exercises it and no llama-swap runs here |
| oss-12 | open-webui/open-webui | https://github.com/open-webui/open-webui | NOASSERTION | referenced-only | [bossman-core/compose.core.yaml](../../../../bossman-core/compose.core.yaml) | docker compose service of the Bossman Core server stack only; no test |
| oss-13 | kyuz0/amd-strix-halo-toolboxes | https://github.com/kyuz0/amd-strix-halo-toolboxes | none-detected | referenced-only | [bossman-infra/compose.server.yaml](../../../../bossman-infra/compose.server.yaml) | compose image reference only |
| oss-14 | searxng/searxng | https://github.com/searxng/searxng | AGPL-3.0 | **integrated+proven** | [command-center/bcc/features/web_research/net.py](../../../../command-center/bcc/features/web_research/net.py)<br>[command-center/bcc/features/web_research/config.py](../../../../command-center/bcc/features/web_research/config.py)<br>[command-center/bcc/features/web_research/sources.py](../../../../command-center/bcc/features/web_research/sources.py) | contract tests (SearXNG-shaped backend, mocked socket/DNS); no SearXNG instance running here; receipt [stage3.json](stage3.json), [out/oss-14.txt](out/oss-14.txt) |
| oss-15 | VoltAgent/awesome-agent-skills | https://github.com/VoltAgent/awesome-agent-skills | MIT | referenced-only | - | URL capture bug fixed (stray trailing '.'): now HTTP 200, MIT ([out/stage3-oss-15-url.txt](out/stage3-oss-15-url.txt)); reference doc only |
| oss-16 | ggml-org/llama.cpp | https://github.com/ggml-org/llama.cpp | MIT | **integrated+proven** | [command-center/bcc/providers.py](../../../../command-center/bcc/providers.py)<br>[command-center/bcc/discovery.py](../../../../command-center/bcc/discovery.py) | llama-server catalog/router API contract tests (mock transport); no llama-server running here; receipt [stage3.json](stage3.json), [out/oss-16.txt](out/oss-16.txt) |
| oss-17 | docling-project/docling | https://github.com/docling-project/docling | MIT | **integrated+proven** | [command-center/bcc/oss/docling.py](../../../../command-center/bcc/oss/docling.py)<br>[command-center/bcc/pit/master_parser/documents.py](../../../../command-center/bcc/pit/master_parser/documents.py) | real docling 2.127.0 (installed build); receipt [stage3.json](stage3.json), [out/oss-17.txt](out/oss-17.txt) |
| oss-18 | qdrant/qdrant | https://github.com/qdrant/qdrant | Apache-2.0 | referenced-only | - | Bossman uses qdrant-client local (embedded) mode only (oss-28); the Qdrant server is never used |
| oss-19 | SYSTRAN/faster-whisper | https://github.com/SYSTRAN/faster-whisper | MIT | **integrated+proven** | [command-center/bcc/oss/whisper.py](../../../../command-center/bcc/oss/whisper.py) | real faster-whisper 1.2.1 decoder (installed build); receipt [stage3.json](stage3.json), [out/oss-19.txt](out/oss-19.txt) |
| oss-20 | Comfy-Org/ComfyUI | https://github.com/Comfy-Org/ComfyUI | GPL-3.0 | **integrated+proven** | [command-center/bcc/oss/comfyui.py](../../../../command-center/bcc/oss/comfyui.py) | ComfyUI /prompt, /history, /view contract incl. a real loopback HTTP fake; ComfyUI not running here; receipt [stage3.json](stage3.json), [out/oss-20.txt](out/oss-20.txt) |
| oss-21 | bytedance/UI-TARS | https://github.com/bytedance/UI-TARS | Apache-2.0 | referenced-only (adapter, inference unverified) | [bossman-core/bossman/computer_operator/uitars.py](../../../../bossman-core/bossman/computer_operator/uitars.py)<br>[command-center/bcc/oss/uitars.py](../../../../command-center/bcc/oss/uitars.py) | UI-TARS planner adapter exists; its own status reports inference_verified=False; model not installed |
| oss-22 | GrapesJS/grapesjs | https://github.com/GrapesJS/grapesjs | NOASSERTION | **integrated+proven** | [command-center/bcc/web_designer_visual.py](../../../../command-center/bcc/web_designer_visual.py)<br>[command-center/ui/vendor/grapesjs/grapes.min.js](../../../../command-center/ui/vendor/grapesjs/grapes.min.js)<br>[command-center/ui/pages/web_designer_visual.js](../../../../command-center/ui/pages/web_designer_visual.js) | vendored GrapesJS run in headless Chromium against a live temp Bossman server; receipt [stage3.json](stage3.json), [out/oss-22.txt](out/oss-22.txt) |
| oss-23 | 0xranx/OpenContext | https://github.com/0xranx/OpenContext | MIT | referenced-only (adapter port only) | [command-center/bcc/hybrid/context_store.py](../../../../command-center/bcc/hybrid/context_store.py) | OpenContextShadowStore has no real transport implementation; tests use a stub transport |
| oss-24 | hyperfield/ai-file-sorter | https://github.com/hyperfield/ai-file-sorter | AGPL-3.0 | **integrated+proven** | [command-center/bcc/file_intelligence/service.py](../../../../command-center/bcc/file_intelligence/service.py)<br>[command-center/bcc/file_intelligence/discovery.py](../../../../command-center/bcc/file_intelligence/discovery.py)<br>[integrations/ai-file-sorter/integration.json](../../../../integrations/ai-file-sorter/integration.json) | review-only argv/contract tests with a fake aifilesorter CLI; upstream binary not installed here; receipt [stage3.json](stage3.json), [out/oss-24.txt](out/oss-24.txt) |
| oss-25 | Faceplugin-ltd/Open-Source-Face-Recognition-SDK | https://github.com/Faceplugin-ltd/Open-Source-Face-Recognition-SDK | none-detected | referenced-only | - | no use in Bossman code |
| oss-26 | Vincentwei1021/video-shotcraft | https://github.com/Vincentwei1021/video-shotcraft | Apache-2.0 | referenced-only | - | storyboard.py states REFERENCE_ONLY: no shotcraft code used |
| oss-27 | wandb/weave | https://github.com/wandb/weave | Apache-2.0 | referenced-only | - | only a backend enum name in hybrid/registry.py |
| oss-28 | qdrant/qdrant-client | https://github.com/qdrant/qdrant-client | Apache-2.0 | **integrated+proven** | [command-center/bcc/oss/qdrant.py](../../../../command-center/bcc/oss/qdrant.py) | real qdrant-client 1.15.1 local mode (installed build); receipt [stage3.json](stage3.json), [out/oss-28.txt](out/oss-28.txt) |
| oss-29 | openclaw/openclaw | https://github.com/openclaw/openclaw | MIT | **integrated+proven** | [command-center/bcc/v2/openclaw_bridge.py](../../../../command-center/bcc/v2/openclaw_bridge.py)<br>[command-center/bcc/features/tools_openclaw.py](../../../../command-center/bcc/features/tools_openclaw.py) | Gateway bridge/tools contract tests with a fake Gateway; OpenClaw not running here; receipt [stage3.json](stage3.json), [out/oss-29.txt](out/oss-29.txt) |
| oss-30 | opendatalab/OmniDocBench | https://github.com/opendatalab/OmniDocBench | Apache-2.0 | referenced-only | - | no use in Bossman code |
| oss-31 | CopilotKit/OpenDots | https://github.com/CopilotKit/OpenDots | MIT | referenced-only | - | no use in Bossman code |
| oss-32 | JustVugg/colibri | https://github.com/JustVugg/colibri | Apache-2.0 | referenced-only | - | no use in Bossman code |
| oss-33 | debpalash/VoiceStudio | https://github.com/debpalash/VoiceStudio | AGPL-3.0 | referenced-only | - | no use in Bossman code |
| oss-34 | facebookincubator/muse-gadget-sdk | https://github.com/facebookincubator/muse-gadget-sdk | Apache-2.0 | referenced-only | - | no use in Bossman code |
| oss-35 | chatwoot/chatwoot | https://github.com/chatwoot/chatwoot | NOASSERTION | referenced-only | - | no use in Bossman code |
| oss-36 | chatwoot/docs | https://github.com/chatwoot/docs | MIT | referenced-only | - | no use in Bossman code |
| oss-37 | legenhand/n8n-nodes-instagram-api | https://github.com/legenhand/n8n-nodes-instagram-api | MIT | referenced-only | - | no use in Bossman code |
| oss-38 | devgine/n8n-ig-comments-management | https://github.com/devgine/n8n-ig-comments-management | MIT | referenced-only | - | no use in Bossman code |
| oss-39 | Boudofski/instagramautomation | https://github.com/Boudofski/instagramautomation | NOASSERTION | referenced-only | - | no use in Bossman code |
| oss-40 | aldoprianandi/ig-autodm-worker | https://github.com/aldoprianandi/ig-autodm-worker | MIT | referenced-only | - | no use in Bossman code |
| oss-41 | getzep/graphiti | https://github.com/getzep/graphiti | Apache-2.0 | referenced-only | - | facts.py borrows the bi-temporal idea; no graphiti code or service |
| oss-42 | letta-ai/letta | https://github.com/letta-ai/letta | Apache-2.0 | referenced-only | - | no use in Bossman code |
| oss-43 | mem0ai/mem0 | https://github.com/mem0ai/mem0 | Apache-2.0 | referenced-only | - | facts.py borrows fact-quality rules; no mem0 code or service |
| oss-44 | HKUDS/LightRAG | https://github.com/HKUDS/LightRAG | MIT | referenced-only | - | no use in Bossman code |
| oss-45 | aexy-io/graphzep | https://github.com/aexy-io/graphzep | Apache-2.0 | referenced-only | - | no use in Bossman code |
| oss-46 | Zylann/godot_voxel | https://github.com/Zylann/godot_voxel | MIT | referenced-only | - | game bootstrap plan lists download URLs; nothing runs Godot |
| oss-47 | godotengine/godot | https://github.com/godotengine/godot | MIT | referenced-only | - | game bootstrap plan lists download URLs; nothing runs Godot |
| oss-48 | XingChen-AGI/Xing4.0-29B-A4B | https://github.com/XingChen-AGI/Xing4.0-29B-A4B | Apache-2.0 | referenced-only | - | no use in Bossman code |
| oss-49 | wide-trace/open-higgsfield | https://github.com/wide-trace/open-higgsfield | none-detected | referenced-only | - | no use in Bossman code |
| oss-50 | mltframework/shotcut | https://github.com/mltframework/shotcut | GPL-3.0 | **integrated+proven** | [command-center/bcc/video_studio/shotcut.py](../../../../command-center/bcc/video_studio/shotcut.py) | MLT/Shotcut XML writer tests pass; 2 real-render tests SKIPPED (Shotcut/melt not installed); receipt [stage3.json](stage3.json), [out/oss-50.txt](out/oss-50.txt) |
| oss-51 | AcademySoftwareFoundation/OpenTimelineIO | https://github.com/AcademySoftwareFoundation/OpenTimelineIO | Apache-2.0 | **integrated+proven** | [command-center/bcc/video_studio/interchange.py](../../../../command-center/bcc/video_studio/interchange.py) | real OpenTimelineIO 0.18.1 (installed build); receipt [stage3.json](stage3.json), [out/oss-51.txt](out/oss-51.txt) |
| oss-52 | ggml-org/whisper.cpp | https://github.com/ggml-org/whisper.cpp | MIT | referenced-only (integration code present, unproven) | [command-center/bcc/video_studio/analysis.py](../../../../command-center/bcc/video_studio/analysis.py) | transcribe() drives the FFmpeg whisper (whisper.cpp) filter; its only test is SKIPPED without a local ggml model |
| oss-53 | Lightricks/LTX-2 | https://github.com/Lightricks/LTX-2 | NOASSERTION | referenced-only | - | model-routing config entry only |
| oss-54 | letta-ai/letta-code | https://github.com/letta-ai/letta-code | Apache-2.0 | referenced-only | - | no use in Bossman code |
| oss-55 | MineDojo/Voyager | https://github.com/MineDojo/Voyager | MIT | referenced-only | - | no use in Bossman code |
| oss-56 | lm-sys/RouteLLM | https://github.com/lm-sys/RouteLLM | Apache-2.0 | referenced-only | - | no use in Bossman code |
| oss-57 | ulab-uiuc/LLMRouter | https://github.com/ulab-uiuc/LLMRouter | MIT | referenced-only | - | no use in Bossman code |
| oss-58 | ag2ai/ag2 | https://github.com/ag2ai/ag2 | Apache-2.0 | referenced-only | - | no use in Bossman code |
| oss-59 | OpenHands/benchmarks | https://github.com/OpenHands/benchmarks | MIT | referenced-only | - | audit name match is a false positive (generic repo name) |
| oss-60 | google/benchmark | https://github.com/google/benchmark | Apache-2.0 | referenced-only | - | audit name match is a false positive (generic repo name) |
| oss-61 | higgsfield-ai/higgsfield-client | https://github.com/higgsfield-ai/higgsfield-client | Apache-2.0 | referenced-only | - | no use in Bossman code |
| oss-62 | higgsfield-ai/higgsfield-js | https://github.com/higgsfield-ai/higgsfield-js | none-detected | referenced-only | - | no use in Bossman code |
| oss-63 | higgsfield-ai/cli | https://github.com/higgsfield-ai/cli | MIT | referenced-only | - | no use in Bossman code |
| oss-64 | higgsfield-ai/skills | https://github.com/higgsfield-ai/skills | MIT | referenced-only | - | audit name match is a false positive (generic repo name) |
| oss-65 | Lightricks/LTX-Desktop | https://github.com/Lightricks/LTX-Desktop | Apache-2.0 | referenced-only | - | no use in Bossman code |
| oss-66 | peter-evans/create-pull-request | https://github.com/peter-evans/create-pull-request | MIT | referenced-only (used by CI) | [.github/workflows/bossman-v2-repair.yml](../../../../.github/workflows/bossman-v2-repair.yml) | GitHub Action in bossman-v2-repair.yml; not Bossman runtime, no test |
| oss-67 | prompt-toolkit/python-prompt-toolkit | https://github.com/prompt-toolkit/python-prompt-toolkit | BSD-3-Clause | **integrated+proven** | [command-center/bcc/terminal_cli/chat.py](../../../../command-center/bcc/terminal_cli/chat.py) | real prompt_toolkit 3.0.52 FileHistory via live local probe of chat._make_history (secrets redacted); receipt [stage3.json](stage3.json), [out/oss-67.txt](out/oss-67.txt) |
| oss-68 | Textualize/rich | https://github.com/Textualize/rich | MIT | **integrated+proven** | [command-center/bcc/terminal_cli/console.py](../../../../command-center/bcc/terminal_cli/console.py)<br>[command-center/bcc/terminal_cli/human.py](../../../../command-center/bcc/terminal_cli/human.py)<br>[command-center/bcc/terminal_cli/theme.py](../../../../command-center/bcc/terminal_cli/theme.py) | real rich 15.0.0 (installed build); receipt [stage3.json](stage3.json), [out/oss-68.txt](out/oss-68.txt) |
| oss-69 | openai/codex | https://github.com/openai/codex | Apache-2.0 | **integrated+proven** | [command-center/bcc/autonomy/workers.py](../../../../command-center/bcc/autonomy/workers.py)<br>[command-center/bcc/rave/connectors.py](../../../../command-center/bcc/rave/connectors.py) | live local probe: Bossman codex_version() -> codex-cli 0.157.1; every flag Bossman passes is in `codex exec --help`; receipt [stage3.json](stage3.json), [out/oss-69.txt](out/oss-69.txt) |
| oss-70 | huggingface/skills | https://github.com/huggingface/skills | Apache-2.0 | referenced-only | - | audit name match is a false positive (generic repo name) |
| oss-71 | obra/superpowers | https://github.com/obra/superpowers | MIT | referenced-only | - | skill_catalog regexes mention the name; no superpowers code used |
| oss-72 | anthropics/skills | https://github.com/anthropics/skills | none-detected | referenced-only | - | audit name match is a false positive (generic repo name) |
| oss-73 | NUS-HPC-AI-Lab/FOCUS | https://github.com/NUS-HPC-AI-Lab/FOCUS | Apache-2.0 | referenced-only | - | audit name match is a false positive (generic repo name) |
| oss-74 | shaoguangwang/Q_Gate | https://github.com/shaoguangwang/Q_Gate | MIT | referenced-only | - | no use in Bossman code |
| oss-75 | jinlab-imvr/ReMem | https://github.com/jinlab-imvr/ReMem | none-detected | referenced-only | - | audit name match is a false positive (generic repo name) |
| oss-76 | zhangce01/LENS | https://github.com/zhangce01/LENS | MIT | referenced-only | - | no use in Bossman code |
| oss-77 | wuli55555/ISSF | https://github.com/wuli55555/ISSF | none-detected | referenced-only | - | no use in Bossman code |
| oss-78 | LeonHLJ/RSKP | https://github.com/LeonHLJ/RSKP | MIT | referenced-only | - | no use in Bossman code |
| oss-79 | fuleinist/video-to-skill | https://github.com/fuleinist/video-to-skill | MIT | referenced-only | - | no use in Bossman code |
| oss-80 | coah80/youtube-mcp | https://github.com/coah80/youtube-mcp | MIT | referenced-only | - | no use in Bossman code |
| oss-81 | bradautomates/claude-video | https://github.com/bradautomates/claude-video | unknown (API rate-limited) | referenced-only | - | no use in Bossman code |
| oss-82 | WebDevBar/watch-video | https://github.com/WebDevBar/watch-video | unknown (API rate-limited) | referenced-only | - | no use in Bossman code |
| oss-83 | m-bain/whisperX | https://github.com/m-bain/whisperX | unknown (API rate-limited) | referenced-only | - | no use in Bossman code |
| oss-84 | Breakthrough/PySceneDetect | https://github.com/Breakthrough/PySceneDetect | BSD-3-Clause | referenced-only | - | no use in Bossman code |
| oss-85 | QwenLM/Qwen2.5-VL | https://github.com/QwenLM/Qwen2.5-VL | unknown (API rate-limited) | referenced-only (adapter, inference unverified) | [command-center/bcc/pit/qwen_vision.py](../../../../command-center/bcc/pit/qwen_vision.py) | loopback OpenAI-compatible adapter expects a served Qwen2.5-VL model; no model here |
| oss-86 | minseokii/OTT-Vid | https://github.com/minseokii/OTT-Vid | unknown (API rate-limited) | referenced-only | - | no use in Bossman code |
| oss-87 | EvolvingLMMs-Lab/lmms-eval | https://github.com/EvolvingLMMs-Lab/lmms-eval | unknown (API rate-limited) | referenced-only | - | no use in Bossman code |
| oss-88 | RomGai/VideoStir | https://github.com/RomGai/VideoStir | unknown (API rate-limited) | referenced-only | - | no use in Bossman code |
| oss-89 | HKUDS/VideoRAG | https://github.com/HKUDS/VideoRAG | unknown (API rate-limited) | referenced-only | - | no use in Bossman code |
| oss-90 | city1517/OneClip-RAG | https://github.com/city1517/OneClip-RAG | unknown (API rate-limited) | referenced-only | - | no use in Bossman code |
| oss-91 | facebookresearch/egagent | https://github.com/facebookresearch/egagent | unknown (API rate-limited) | referenced-only | - | no use in Bossman code |
| oss-92 | Co-Messi/HyperData-Terminal | https://github.com/Co-Messi/HyperData-Terminal | unknown (API rate-limited) | referenced-only | - | no use in Bossman code |
| oss-93 | ccxt/ccxt | https://github.com/ccxt/ccxt | unknown (API rate-limited) | referenced-only | - | no use in Bossman code |
| oss-94 | binance/binance-connector-python | https://github.com/binance/binance-connector-python | unknown (API rate-limited) | referenced-only | - | no use in Bossman code |
| oss-95 | bybit-exchange/pybit | https://github.com/bybit-exchange/pybit | unknown (API rate-limited) | referenced-only | - | no use in Bossman code |
| oss-96 | nautechsystems/nautilus_trader | https://github.com/nautechsystems/nautilus_trader | unknown (API rate-limited) | referenced-only | - | no use in Bossman code |
| oss-97 | hummingbot/hummingbot | https://github.com/hummingbot/hummingbot | unknown (API rate-limited) | referenced-only | - | no use in Bossman code |
| oss-98 | polakowo/vectorbt | https://github.com/polakowo/vectorbt | unknown (API rate-limited) | referenced-only | - | no use in Bossman code |
| oss-99 | QuantConnect/Lean | https://github.com/QuantConnect/Lean | unknown (API rate-limited) | referenced-only | - | no use in Bossman code |
| oss-100 | freqtrade/freqtrade | https://github.com/freqtrade/freqtrade | unknown (API rate-limited) | referenced-only | - | no use in Bossman code |
| oss-101 | freqtrade/freqtrade-strategies | https://github.com/freqtrade/freqtrade-strategies | unknown (API rate-limited) | referenced-only | - | no use in Bossman code |
| oss-102 | AI4Finance-Foundation/FinRL | https://github.com/AI4Finance-Foundation/FinRL | unknown (API rate-limited) | referenced-only | - | no use in Bossman code |
| oss-103 | AI4Finance-Foundation/FinGPT | https://github.com/AI4Finance-Foundation/FinGPT | unknown (API rate-limited) | referenced-only | - | no use in Bossman code |
| oss-104 | tradingview/lightweight-charts | https://github.com/tradingview/lightweight-charts | unknown (API rate-limited) | referenced-only | - | no use in Bossman code |
| oss-105 | caronc/apprise | https://github.com/caronc/apprise | BSD-2-Clause | referenced-only | - | no use in Bossman code |
| oss-106 | yamadashy/repomix | https://github.com/yamadashy/repomix | MIT | referenced-only | - | no use in Bossman code |
| oss-107 | trailofbits/skills | https://github.com/trailofbits/skills | unknown (API rate-limited) | referenced-only | - | audit name match is a false positive (generic repo name) |
| oss-108 | petergyang/no-ai-slop | https://github.com/petergyang/no-ai-slop | unknown (API rate-limited) | referenced-only | - | no use in Bossman code |
| oss-109 | tonhowtf/omniget | https://github.com/tonhowtf/omniget | n/a (repo 404) | retired (receipt written, NOT applied) | - | repo and owner 404 on web and REST API; [stage3-retire.json](stage3-retire.json), [out/oss-109.txt](out/oss-109.txt); tree_apply refuses audit receipts on recorded leaves (new rule), so the status stays recorded |
| oss-110 | Untrivial-ai/agent-orchestrator | https://github.com/Untrivial-ai/agent-orchestrator | unknown (API rate-limited) | referenced-only | - | no use in Bossman code |
| oss-111 | pranshuparmar/witr | https://github.com/pranshuparmar/witr | unknown (API rate-limited) | referenced-only | - | no use in Bossman code |
| oss-112 | artf/codemirror-formatting | https://github.com/artf/codemirror-formatting | unknown (API rate-limited) | referenced-only | - | no use in Bossman code |
| oss-113 | openai/openai-agents-python | https://github.com/openai/openai-agents-python | MIT | referenced-only | - | no use in Bossman code |
| oss-114 | langchain-ai/langgraph | https://github.com/langchain-ai/langgraph | MIT | referenced-only | - | no use in Bossman code |
| oss-115 | pydantic/pydantic-ai | https://github.com/pydantic/pydantic-ai | MIT | referenced-only | - | no use in Bossman code |
| oss-116 | crewAIInc/crewAI | https://github.com/crewAIInc/crewAI | unknown (API rate-limited) | referenced-only | - | no use in Bossman code |
| oss-117 | modelcontextprotocol/python-sdk | https://github.com/modelcontextprotocol/python-sdk | MIT | **integrated+proven** | [command-center/bcc/v2/mcp_runtime.py](../../../../command-center/bcc/v2/mcp_runtime.py) | real mcp SDK 2.2.0 client against a real stdio MCP server; receipt [stage3.json](stage3.json), [out/oss-117.txt](out/oss-117.txt) |
| oss-118 | PrefectHQ/fastmcp | https://github.com/PrefectHQ/fastmcp | Apache-2.0 | referenced-only | - | tests use the official SDK's built-in mcp.server.fastmcp, not the PrefectHQ/fastmcp package |
| oss-119 | modelcontextprotocol/servers | https://github.com/modelcontextprotocol/servers | NOASSERTION | referenced-only | - | audit name match is a false positive (generic repo name) |
| oss-120 | ComposioHQ/composio | https://github.com/ComposioHQ/composio | unknown (API rate-limited) | referenced-only | - | no use in Bossman code |
| oss-121 | temporalio/sdk-python | https://github.com/temporalio/sdk-python | unknown (API rate-limited) | referenced-only | - | no use in Bossman code |
| oss-122 | SmythOS/smythos-studio | https://github.com/SmythOS/smythos-studio | unknown (API rate-limited) | referenced-only | - | no use in Bossman code |
