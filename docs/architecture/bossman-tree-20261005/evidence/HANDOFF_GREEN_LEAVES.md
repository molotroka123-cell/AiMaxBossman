# Handoff: green leaves 06.10 (для чата, собирающего единый Bossman)

Ветка `green/tree-leaves-20261006` от `integrate/bossman-2.1-one-20261006` @ 3b3e2c32. Не влита, не запушена. Влить: cherry-pick/merge этой ветки (коммиты 28a4bf72 механизм, 20e2c879 фикс плагинов, a6296e2f/edb414f2/efb2e146 расписки, 42b53821 применение).

Было (фото владельца): reported 11 / code 510 / branch 100. Стало (локальный рендер, after-tree-20261006.png): reported 209 / code 315 / branch 97.

Зелёным стали только листья с проверяемой распиской (tools/tree_apply_evidence.py: PASS, exit 0, sha-предок HEAD, sha256 вывода): ops +192 (import + существующие pytest), plugins +5 (http.get, monitor.feed, sql.read, obsidian.read/write — реальные вызовы), skills +1 (skills-runtime, 39 тестов).

НЕ зелёные и почему:
- 18 плагинов: общий stub NOT_TESTED_LIVE (нет обработчика). openrouter.chat/ollama.chat/github.repo_read/mcp.* не доказаны. Это работа для интеграции: реальные обработчики.
- 8 outward-плагинов (gmail.send, github.issue_create...): только отказ шлюза без одобрения (*.gate-only.nogreen), эффект не проверялся и не должен без владельца.
- 44 навыка: загружаются (skills.load-only.nogreen), максимум 'prepared'. skill-22 solana-volume-suite: critical telemetry по scan_policy. skill-36 compact: нет description.
- ops: 43 FAIL (29 no_tests, 7 env, 3 tests_failed: mod-autonomy, mod-osiris, module-575dcc5a99f5; 3 timeout; 1 import).
- OSS 121: ссылки, не зелёные. Аудит audit/ и oss-audit.md, шорт-лист: gepa, mini-swe-agent, agent-lightning, RouteLLM, letta, MCP python-sdk, pydantic-ai, repomix, PySceneDetect, apprise. Копилефт (searxng, ComfyUI, shotcut...) только отдельным процессом.

Оговорки: PASS модуля = импорт + хотя бы один тест, упоминающий модуль (текстовое совпадение), не семантическая сертификация. Расписки ops перепривязаны с сиротского 3c6f39dc на a6296e2f (код продукта идентичен). Jev не использовался (нет ключа). Фикс safe_get без регрессионного теста. Опубликованный сайт НЕ обновлён: нужен tree.export.json в его data/tree.json (scripts/sync_bossman.py сайта).
