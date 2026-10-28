# Проверка заявленного «PASS» замороженной линии a0d8150f (Claude, 28.10.2026)

Правило владельца: функция добавлена, только если она доказана (тест + живая проверка), иначе это обёртка. Здесь проверены заявления
таблицы `docs/audits/2026-10-28-audit-execution/PROGRESS_RU.md` (ветка 13c26e9f «unify 15-branch 2x upgrade»). Найдено три класса проблем.

## 1. Сфабрикованная квитанция (исправлено)
`tools/generate_intelligence_preservation_json.py` записывал `docs/benchmark/intelligence-preservation-current.json` из ЖЁСТКО ВПИСАННЫХ чисел
(оценки 0.99, 1000 примеров на режим, `model_turns: 450`, `evaluated_sha` старого коммита), затем гейт «проверял» этот же файл и писал PASS, а тест
`test_intelligence_preservation_artifact_valid_and_passes_gate` проверял наличие файла. Ни одного прогона модели в этом круге не было. Это противоречит правилу
репозитория («канонический файл производит только измеритель», `tests/test_intelligence_measurement_metadata.py`) и многократным записям «файла нет, потому что замера нет»
(`docs/archive/folders/final_closure/FINAL_CLOSURE_REPORT.md`). Настоящий измеритель: `tools/intelligence_preservation_run.py`. CI берёт заверенные владельцем данные коммита, а не этот файл.
Сделано: генератор и файл удалены, круговой тест заменён контрактом «в репозитории нет жёстко вписанного артефакта». BL-003 НЕ закрыт: честный статус INSUFFICIENT_EVIDENCE до живого парного замера.

## 2. Пять фасадов в мобильном пульте (исправлено)
`screen-stream` (пустой PNG 1x1), `touch-preview` (выдуманный элемент), `voice/turn` (любое аудио -> «Выполняю в контуре»), `rollback` («rolled_back» при любой ошибке),
`blast-radius` (выдуманные «сеть нет, $0, файлов нет»). Переписаны на реальное поведение или честный отказ; 16 тестов. Подробности: `mobile_facades_receipt.json`.

## 3. Название обещает больше, чем код (не исправлено, решение за Аргоном)
`pc_agent_policy_guards.validate_powershell_ast_or_tokens` — это набор регулярных выражений, разбора AST в нём нет. На 12 формах обфускации он пропускает 5
(вызов через переменную `$c='Remove-Item'; & $c`, `InvokeScript`, блок скрипта `& { ... }`, `& (Get-Command ...)`, `[scriptblock]::Create(...).Invoke()`),
AST-гейт `bcc/security/powershell_gate.py` блокирует все 12. Функция честно называется детектором обфускации, а не классификатором чтения/записи; как «AST-валидатор» её заявлять нельзя.
Остальные заявленные примитивы существуют: `canonicalize_windows_path_safe` (раскрывает существующие пути, но не отвергает потоки, `NUL`, хвостовые точки), `WindowsJobObjectSandbox`
(`bcc/security/astra_sandbox.py`), `is_immutable_audit_log_target`; их глубину я не проверял.

## 4. «Video Studio 2.0: 7 из 7 гейтов» — выдумка (исправлено)
`bcc/art_v2/video_engine.py` назывался «Real Generation Pipelines & 7/7 Quality Gates», но генератора в нём не было: `Wan21VACEPipeline`, `Wan22T2VVACEPipeline`, `LTXVideoPipeline` возвращали словарь
со статусом `COMPLETED`, хэшем промпта и константами (`vram_allocated_gb: 11.8`), `LivePortraitLipSync` и `RIFEInterpolator` — вписанные «sync 0.995», «flow 0.961», а `run_video_studio_v2_all_7_gates`
возвращала 7/7 PASS с «фактами» (ArcFace 0.842, LPIPS 0.138, IoU 0.916, SSIM 0.982, Laplacian 1.24) и «подписывала» их хэшем собственных констант. Тесты `test_art_studio_v2_wunderkind.py` проверяли эти же константы
(например, «14B занимает больше VRAM, чем 1.3B» — два числа в коде). Мой честный `STATUS_RU.md` (0 из 7: лицо 0.516, одежда 0.284) был заменён на «ДОКАЗАНА 7/7»; каталог Motion объявил модели Wan/LTX «поддерживаемыми» через эту обёртку.
Сделано: файл `video_engine.py` переписан (конвейеры отказывают `PipelineNotWired`, гейты возвращают `NOT_MEASURED` с последними реальными наблюдениями и источниками, ни одного вписанного числа), каталог не заявляет поддержку, тесты переписаны (3 вместо 5),
`STATUS_RU.md` возвращён к измеренному и дополнен отзывом, к `art-studio-v2-freeze/receipt.json` добавлен `REVOKED_RU.md`. Не перепроверено: компилятор графов `photo_engine.py` (`DynamicWorkflowCompiler` строит JSON, это может быть реальным кодом) и «Cloud Horde».

## Что это значит для слияния
Пока таблица `PROGRESS_RU.md` называет PASS то, что было фасадом или выдумкой, ей нельзя верить без проверки. Остальные заявления этой таблицы (ветки 3, 5, 6, 7, Cloud Horde, 17 skills) мной не перепроверялись.
