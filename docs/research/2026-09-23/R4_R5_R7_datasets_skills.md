# Исследование 2026-09-23 — датасеты и скилы (линии R4, R5, R7)

Метки: **VERIFIED** — источник прочитан сейчас (в основном raw.githubusercontent.com);
**SNIPPET/SEARCH** — только поисковая выдача; **RECALLED** — по памяти, не проверено;
**BLOCKED** — закрыто сетевой политикой облака.

Закрыты из облака: huggingface.co (403 CONNECT / EGRESS_BLOCKED), arxiv.org, openreview, aclanthology,
swebench.com, nebius.com, snyk.io, skillleaderboard.com. Поэтому **лицензии и размеры датасетов на HF не
подтверждены** — их нужно сверить с машины, где HF доступен, до любого обучения.

Веса не обучались (WEIGHTS_UNCHANGED). Датасеты ниже — для eval, рецептов памяти и возможной будущей LoRA.

## R4 — исправление кода / SWE

| # | Датасет | Суть | Лицензия | Доказательство | Роль в Bossman |
|---|---|---|---|---|---|
| 1 | `nebius/SWE-rebench-leaderboard` (+`SWE-rebench`) | ежемесячные свежие задачи из issues, Docker-образы; ~21k задач в основе (SNIPPET) | CC BY 4.0 (SNIPPET) | arXiv 2505.20411, оценка без загрязнения (SNIPPET) | **Основной eval**, только задачи после даты обучения моделей |
| 2 | `SWE-bench-Live/SWE-bench-Live` | +50 проверенных задач в месяц; агенту только problem_statement | код MIT (VERIFIED), данные — не проверено | NeurIPS; RepoLaunch ≥98 % сборки (VERIFIED README) | **Второй eval**; правило «только problem_statement» — в гейт |
| 3 | `SWE-bench/SWE-smith` + `-trajectories` | 52k синтетических багов, 26k траекторий (VERIFIED) | инструмент MIT (VERIFIED) | SWE-agent-LM-32B: 40.2 % на Verified, +32 п.п. (VERIFIED) | рецепты/RAG, кандидат LoRA; **генератор задач из репозитория самого Bossman** |
| 4 | `R2E-Gym/R2E-Gym-Subset` | 8.1k сред из коммитов (VERIFIED) | код Apache-2.0 (VERIFIED) | 34.4 %, с гибридными верификаторами 51 % (VERIFIED) | схема «коммит → среда → тест»; гибридный верификатор |
| 5 | `SWE-Gym/SWE-Gym` | 2.4k Python-задач, Lite 234 (VERIFIED) | код Apache-2.0 (VERIFIED) | <500 траекторий → +14 % (VERIFIED) | быстрый локальный eval, critic для best-of-N |

Дополнение: **SWT-bench** (MIT, VERIFIED) — метрика качества регрессионных тестов (падает до фикса, проходит после).
Отклонены: SWE-bench Verified как основной eval (загрязнён/насыщен — SNIPPET со ссылкой на OpenAI), SWE-bench full/Lite,
SWE-bench Pro (тяжёлый, copyleft-часть — RECALLED), OpenCodeReasoning (олимпиадные задачи, не ремонт репозиториев),
CommitPack (нет исполняемой проверки).

## R5 — агентные траектории и tool calling

| # | Датасет | Суть | Лицензия | Доказательство | Роль |
|---|---|---|---|---|---|
| 1 | `nebius/SWE-rebench-openhands-trajectories` | 67k траекторий OpenHands (SEARCH) | CC-BY-4.0 (SEARCH) | RFT Qwen3-30B-A3B: 50.3 % на Verified (SEARCH; базовый балл не проверен) | LoRA кодового агента того же класса, что Qwen3.6-35B-A3B; few-shot рецепты |
| 2 | `Agent-Ark/Toucan-1.5M` | 1.5M траекторий реальных MCP-вызовов (VERIFIED README) | код MIT (VERIFIED), данные — не проверено | τ-bench 38.76→42.33, BFCL +~9 п.п. (SEARCH) | LoRA на подмножестве инструментов Bossman |
| 3 | `Salesforce/APIGen-MT-5k` | 5k многоходовых проверенных траекторий | **CC-BY-NC-4.0** (VERIFIED) | xLAM-2-70b: τ-bench 56.2 % (VERIFIED) — внутри домена | few-shot; LoRA только некоммерчески; после обучения τ-airline/retail не eval |
| 4 | `SWE-bench/SWE-smith-trajectories` | см. R4 | — | — | — |
| 5 | `nvidia/When2Call` | когда НЕ вызывать инструмент (VERIFIED README) | LICENSE-файла нет (404) | RPO > SFT (VERIFIED, без чисел) | дешёвый eval против галлюцинаций инструментов; DPO |

Инструменты оценки: **τ²/τ³-bench** (MIT, VERIFIED; результаты до/после 1.0.1 несравнимы), **BFCL**.
Важно: Qwen3 — Hermes-стиль `<tool_call>`, GPT-OSS — harmony (RECALLED): любой набор нужно конвертировать в шаблон модели.

## R7 — skills и MCP

**Риск цепочки поставок — главный вывод.** Skills/MCP = исполняемый код. Инциденты (только SNIPPET): ClawHavoc
(~1184 скомпрометированных skills в ClawHub), кампания со стилером для Claude Code/OpenClaw, postmark-mcp 1.0.16
(скрытая BCC-копия писем). Правило Bossman: без маркетплейсов и `npx skills add`; фиксировать коммит; переносить
только текст методики в формат рецепта; `scripts/` — только после ревью.

| # | Репозиторий | Суть | Лицензия | Риск | Профиль |
|---|---|---|---|---|---|
| 1 | obra/superpowers | systematic-debugging, TDD, verification-before-completion | MIT | С (хуки, телеметрия логотипа) | PLAN_EXECUTE_VERIFY, TOOL_FIRST |
| 2 | anthropics/skills | эталон SKILL.md, skill-creator, webapp-testing | Apache-2.0; docx/pdf/pptx/xlsx — source-available | Н–С | TOOL_FIRST, USER_UX |
| 3 | addyosmani/agent-skills | spec→plan→build→verify→review→ship, evals | MIT | Н | PLAN_EXECUTE_VERIFY, RED_TEAM |
| 4 | trailofbits/skills | differential-review, static-analysis | CC-BY-SA-4.0 (атрибуция/копилефт текста) | С | RED_TEAM |
| 5 | microsoft/playwright-mcp | браузер через accessibility-снимки | Apache-2.0 | С | USER_UX |
| 6 | ChromeDevTools/chrome-devtools-mcp | консоль/сеть/трассы | Apache-2.0 | В по умолчанию (телеметрия; отключается флагами) | USER_UX |
| 7 | oraios/serena | LSP-навигация и правка | GPL-3.0+ — код не копировать | В (выключить shell-инструмент) | TOOL_FIRST |
| 8 | modelcontextprotocol/servers | git/filesystem/fetch/memory/time | MIT/Apache-2.0 | С («reference, not production») | TOOL_FIRST, MEMORY |
| 9 | github/github-mcp-server | репо/issues/PR | MIT | С–В (токен; только `--read-only`) | PLAN_EXECUTE_VERIFY |
| 10 | upstash/context7 | документация библиотек | MIT (клиент) | В (облачный бэкенд) | только по согласию |

Звёзды — из пересказа страницы WebFetch, через API не сверены.

### Черновики рецептов для памяти Bossman (перенос смысла, не кода)
1. **systematic-debugging**: третий подряд фикс не помог → гипотезы без проверки → воспроизвести и сравнить с рабочим аналогом → одна гипотеза = одна правка, после трёх провалов пересмотр → красный тест, затем зелёный; контрпример: очевидная опечатка из трейса.
2. **verification-before-completion**: «тесты проходят» без свежего вывода → запустить команду-доказательство, приложить вывод и код возврата; контрпример: исследование без изменений.
3. **watch-it-fail**: новый тест сразу зелёный → откатить фикс, тест обязан упасть по ожидаемой причине; контрпример: характеризационные тесты легаси.
4. **differential-review**: ревью видит только diff → `git log -p` и все вызывающие → на каждый ослабленный инвариант тест, отвергающий плохой случай; контрпример: чисто документационный diff.
5. **webapp-testing** (RECALLED): «UI работает» без браузера → снимок/ассерт DOM после действия + консоль без ошибок; контрпример: CLI без интерфейса.

## Что проверить с машины с доступом к HF
Лицензии и размеры всех датасетов на HF; карточки nebius, Toucan, APIGen-MT, When2Call; базовые баллы до RFT.
