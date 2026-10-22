# ARGON — КОРРЕКЦИЯ 22.10 (мини-промт, 7 пунктов, после осмотра веток в 10:05 UTC)

Смотрел: `integrate/north-star-final-20261023` @ `180c87f6` (CP-INT-01/02, шаги 1–3 влиты, merge-tree с main чистый) и
`perf/north-star-2x-20261023` @ `9433b159` (CP-PERF-00…06). Хорошо: порядок шагов соблюдён, checkpoints пишутся, Vulkan MTP
sweep на 8060S дал 1.88–2.21x по decode (cp-perf-02). Ниже — что исправить, прежде чем идти дальше.

1. **«4.9x» в `cp_perf_06_ab_results.json` — не 2x end-to-end.** Все 6 lanes — миллисекунды API-пути без модели
   (`e2e_agent_message_ms` 92.6 → 21.6 мс, `combined_critical_path_ms` 275 → 56 мс). По контракту из
   `BOSSMAN_2X_PERFORMANCE_NORTH_STAR_20261023_RU.md` метрика — медиана от нажатия Send до проверенного результата на
   предрегистрированной матрице задач С МОДЕЛЬЮ. Переименуй lane в `api_overhead_*`, запиши это как «снято 219 мс
   накладных на сообщение» и посчитай долю от реальной длительности задачи из cp-perf-00 (там задачи — десятки секунд: это
   ≈1%). Слово «speedup_x» для этих lanes убрать; «2x» нигде не писать до E2E-замера.
2. **Кандидат не установлен.** `installed_head_sha` в обоих файлах = `288f79f5` (рантайм 21.10). Числа «candidate» сняты
   in-process с кода ветки, baseline — тоже in-process, но E2E-гейт G0 требует: live `:8801` SHA == candidate SHA. Перезапуск —
   только при пустой очереди генераций (`/api/direct-gen/jobs` без queued/loading/generating), с записью SHA до/после.
3. **Ты влил устаревший SHA моей ветки.** CP-INT-01 взял `integrate/claude-north-star-20261022` @ `33488c07`; актуальный —
   `6fe370eb` (+ тесты 5 инструментов `tests/test_claude_ops_tools.py`, 20-задачный бенч `tools/llm_bench_tasks.py`,
   `docs/owner/PULT_MODEL_VIDEO_PIPELINE_20261022_RU.md`, §6 мастер-промта, evidence bench20 и аудит киберзащиты).
   `git merge --no-ff origin/integrate/claude-north-star-20261022` ещё раз — конфликтов быть не должно.
4. **`cloud.ask` автоодобряется в AUTO.** В `agent_opencode._NEVER_AUTO_APPROVE_KINDS` (строка ~2274 в твоей ветке) нет
   `cloud.ask`/`send`-категории. Одна строка + негативный тест: сессия с `bypass_permissions=True` → одобрение `cloud.ask`
   остаётся `pending`. Без этого шаг 3b не закрыт.
5. **Шаг 4 (история после STOP) — перенос, не merge.** `fix/chat-history-after-stop-20261021` @ `ad4cf2e9` конфликтует в
   `agent_opencode.py`: перенести три исправления (task_id=0 → уникальный отрицательный id; «нечитаемо» ≠ «удалено», 503
   вместо 404; `turns` не list → отказ, не `[]`) и бэкап `.json.bak`; тесты `test_chat_history_after_stop.py` 12/12 на
   текущем коде. Решение конфликта описать в коммите.
6. **Бенч моделей — только как evidence.** `bench20`: MiniMax M3 18/20 @74 с, DeepSeek V4.1 Flash 18/20 @20 с при той же
   цене; Nemotron free — 502 (NOT_RUN). В роутер не переносить без парного прогона на ≥20 задачах и quality-noninferiority.
7. **Два бесплатных воркера уже работают на мою ветку** (задачи `5a0c399234c2` gemini-3-flash-free →
   `tools/chat_structure_check.py` + тесты; `f3bca1bc6589` groq-gpt-oss-120b-free → `tests/test_llm_bench_tasks.py`,
   эталонные решения 16 задач). Их diff'ы НЕ применять автоматически: receipt → review → порт в ветку → тесты.

Формат ответа по каждому пункту: `DONE <sha> / NOT_DONE <почему>`, плюс строка в `CHECKPOINTS.md`. Честный вердикт
`INSUFFICIENT_EVIDENCE` допустим; «READY»/«2x» — нет, пока нет файла с E2E-числами на установленном SHA.
