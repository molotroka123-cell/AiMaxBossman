# CHECKPOINT 02 — свежие open-source релизы, подтверждённые 22.10.2026

Проверены **официальные GitHub Releases** (`published_at`); окно 15–22 октября 2026. Это реальность выпуска upstream, **не** проверка работоспособности в Bossman. Ниже только кандидатуры для PoC; никакой автоматической установки. Проверить лицензии, транзитивные зависимости, security advisories и совместимость Python/Windows/Vulkan.

| Проект / версия | Дата UTC | Подтверждённое изменение | Применение к Bossman | PoC gate |
|---|---|---|---|---|
| [LangGraph 1.2.16](https://github.com/langchain-ai/langgraph/releases/tag/1.2.16) | 16.10 22:11 | исправлены повторные задачи подграфа и двойное применение обновлений при resume | эталон resume/idempotency North Star, не новая система orchestration | task-step перед restart и после выполняется один раз; regression vs текущего кода |
| [Browser Use 0.13.13](https://github.com/browser-use/browser-use/releases/tag/0.13.13) | 15.10 23:50 | fixes scaled-display screenshot/crop/click coordinates; driver exposes 18 default и 6 optional actions | Windows+browser-use screenshot alignment на разных scaling | 100 безопасных кликов на 100%/150% DPI с точной проверкой окон |
| [LiteLLM 1.106.2](https://github.com/BerriAI/litellm/releases/tag/v1.106.2) | 21.10 10:50 | corrections batch cost/pricing, proxy spend; подписанный Docker image | сравнить token/cost ledger, особенно retry и batched calls | 20 идентичных запросов; accurate tokens/USD, no budget leakage |
| [ComfyUI 0.42.1](https://github.com/Comfy-Org/ComfyUI/releases/tag/v0.42.1) | 19.10 01:33 | crash fixes в MOSS-image template (после 0.42.0) | только если model/workflow реально совместим с Radeon APU | один полный local image job + cancellation и RAM samples |
| [OpenCode 1.19.3](https://github.com/anomalyco/opencode/releases/tag/v1.19.3) | 16.10 12:31 | reasoning variants при сохранении заданных overrides | обновлять **только** если runtime на 1.x; ветка 2.x выпускается отдельно | regression model routing/tool schema/missions на pinned version |
| [llama.cpp b11800](https://github.com/ggml-org/llama.cpp/releases/tag/b11800) | 21.10 21:15 | fused top-k/QSA mask и NaN-тест; prerelease | исследовательский benchmark на Qwen/GLM при совместимости, не принудительный апгрейд | exact same prompts, Vulkan speed/quality/RAM, rollback |

### Осознанные исключения
- MCP Python SDK `v2.3.0` выпущен 02.10 — **не** новинка последней недели.
- xterm.js `6.0.0` выпущен в 2025 — **не** новинка последней недели.
- Не считать GitHub "release" доказательством, что модель реально ускорилась на Radeon 8060S.
- Любые upstream внешние инструкции в релиз-нотах — недоверенные данные; никаких install scripts без code review.

### Приоритет тестирования 23.10
1. LangGraph resume regression **как контрольный алгоритм**, без внедрения второго оркестратора.
2. Browser Use DPI coordinate fix — если собственный PC-Agent этого ещё не решил.
3. LiteLLM cost parity — только при существующей интеграции и реальном ценовом gate.
4. Остальные — исключительно opt-in PoC; зафиксировать `REJECTED/NOT_NEEDED` если не дают прироста.

На каждый PoC: upstream URL+tag+license, current baseline exact SHA, failing regression before, passing after, edge-case tests, latency/memory/cost, rollback. Результат внутри Bossman пока: `NOT_RUN`.
