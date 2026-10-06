# agcloud lane classification (agents, cloud, computer)

Counts: {'GREEN': 41, 'KEEP': 3}; TOP: ['cap-21', 'cap-22', 'cap-40', 'mod-economy_swarm', 'mod-nl_permissions', 'mod-router']

| id | label | verdict | reason | value |
|---|---|---|---|---|
| cap-21 | Free providers · лимиты · fallback | GREEN | import in clean subprocess + tests passed | TOP |
| cap-22 | OpenRouter · OpenAI-compatible · Anthropic | GREEN | live free-OpenRouter call (model nvidia/nemotron-3-ultra-550b-a55b:free) + import + tests | TOP |
| cap-23 | Кэширование и экономия | GREEN | import in clean subprocess + tests passed; focused behavior test authored by lane | OK |
| cap-24 | Токен-аудитор всех моделей в Telegram | KEEP | idea: Telegram/phone remote operator is a design doc only (no code), needs real work | OK |
| cap-25 | Browser · bounded actions | GREEN | import in clean subprocess + tests passed | OK |
| cap-26 | Jev · desktop operator | GREEN | import in clean subprocess + tests passed | OK |
| cap-27 | Computer Use · observe/act | KEEP | blocked: computer-use act path needs owner WAIT_APPROVAL on the real desktop; not run by this lane | OK |
| cap-28 | OpenDots sidecar | KEEP | idea: OpenDots sidecar is a design doc only (no code), needs real work | OK |
| cap-38 | Agentic Rave | GREEN | import in clean subprocess + tests passed | OK |
| cap-39 | Codex / Claude через CMD | GREEN | import in clean subprocess + tests passed; focused behavior test authored by lane | OK |
| cap-40 | Миссии · scheduler · долгие задачи | GREEN | import in clean subprocess + tests passed | TOP |
| cap-41 | Организация · fleet · swarm | GREEN | import in clean subprocess + tests passed | OK |
| mod-action_router | action_router | GREEN | import in clean subprocess + tests passed | OK |
| mod-browser | browser | GREEN | import in clean subprocess + tests passed | OK |
| mod-browser_help | browser_help | GREEN | import in clean subprocess + tests passed | OK |
| mod-cache_intel | cache_intel | GREEN | import in clean subprocess + tests passed; focused behavior test authored by lane | OK |
| mod-coding_limit_saver_v16 | coding_limit_saver_v16 | GREEN | import in clean subprocess + tests passed | OK |
| mod-coding_recipes | coding_recipes | GREEN | import in clean subprocess + tests passed | OK |
| mod-coding_sessions | coding_sessions | GREEN | import in clean subprocess + tests passed; focused behavior test authored by lane | OK |
| mod-coding_tasks | coding_tasks | GREEN | import in clean subprocess + tests passed; focused behavior test authored by lane | OK |
| mod-control_plane | control_plane | GREEN | import in clean subprocess + tests passed | OK |
| mod-economy_swarm | economy_swarm | GREEN | import in clean subprocess + tests passed | TOP |
| mod-free_providers | free_providers | GREEN | import in clean subprocess + tests passed | OK |
| mod-jev | jev | GREEN | import in clean subprocess + tests passed | OK |
| mod-missions | missions | GREEN | import in clean subprocess + tests passed | OK |
| mod-nl_orchestra | nl_orchestra | GREEN | import in clean subprocess + tests passed | OK |
| mod-nl_permissions | nl_permissions | GREEN | import in clean subprocess + tests passed | TOP |
| mod-openrouter | openrouter | GREEN | live free-OpenRouter call (model nvidia/nemotron-3-ultra-550b-a55b:free) + import + tests | OK |
| mod-organization | organization | GREEN | import in clean subprocess + tests passed; focused behavior test authored by lane | OK |
| mod-provider_fleet_v16 | provider_fleet_v16 | GREEN | import in clean subprocess + tests passed | OK |
| mod-rave | rave | GREEN | import in clean subprocess + tests passed | OK |
| mod-router | router | GREEN | import in clean subprocess + tests passed | TOP |
| mod-spend_meter | spend_meter | GREEN | import in clean subprocess + tests passed | OK |
| mod-task_exchange | task_exchange | GREEN | import in clean subprocess + tests passed | OK |
| mod-terminal | terminal | GREEN | import in clean subprocess + tests passed | OK |
| mod-tools_browser | tools_browser | GREEN | import in clean subprocess + tests passed | OK |
| mod-tools_computer | tools_computer | GREEN | import in clean subprocess + tests passed | OK |
| mod-tools_terminal | tools_terminal | GREEN | import in clean subprocess + tests passed | OK |
| mod-v15_economy | v15_economy | GREEN | import in clean subprocess + tests passed | OK |
| free-NVIDIA-NIM | NVIDIA NIM | GREEN | import in clean subprocess + tests passed | OK |
| free-Groq | Groq | GREEN | import in clean subprocess + tests passed | OK |
| free-Google-AI-Studio | Google AI Studio | GREEN | import in clean subprocess + tests passed | OK |
| agents-graph | Карта агентов · граф оркестров | GREEN | import in clean subprocess + tests passed; focused behavior test authored by lane | OK |
| agents-lab | Лаборатория агентов · сравнение стратегий | GREEN | import in clean subprocess + tests passed | OK |
