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

## 5. Photo Studio 2.0: графы ComfyUI с несуществующими узлами (частично исправлено)
Проверено живьём: графы `DynamicWorkflowCompiler` отправлены на `/prompt` установленного ComfyUI v0.37.0 (CPU, без кастомных узлов). До исправления
(`photo_engine_comfyui_before_fix.json`): `txt2img_studio` и `inpaint` проходили проверку структуры (остальные ошибки: нет файлов моделей и картинок), а четыре режима отвергались `missing_node_type`:
`pose_and_structure` — `ApplyControlNetUnion` НЕ СУЩЕСТВУЕТ (настоящие узлы `SetUnionControlNetType` + `ControlNetApplyAdvanced`), `outpaint` — `PadImageForOutpainting` НЕ СУЩЕСТВУЕТ (настоящий `ImagePadForOutpaint`, входы
в пикселях, а не `left_pct`), `character_lock` — нужен пакет IPAdapter_plus, `detailer_and_upscale` — нужен Impact Pack/Ultralytics (оба пакета не установлены).
Кроме того, `pose_and_structure` по умолчанию брал Flux и подключал к нему SDXL ControlNet Union (несовместимо).
Сделано: цепочка `LoadImage -> SetUnionControlNetType -> ControlNetApplyAdvanced` с передачей positive и negative в сэмплер, Flux для этого режима отвергается, outpaint на `ImagePadForOutpaint` с отступами в пикселях;
после исправления ComfyUI принимает структуру обоих режимов (остались только ожидаемые ошибки отсутствующих файлов). Тесты: `tests/test_photo_engine_comfyui_nodes.py` (6, сверяют типы узлов с исходниками установленного ComfyUI).
НЕ исправлено и НЕ проверено: `character_lock` использует `IPAdapterFaceIDPlusV2`, а в IPAdapter_plus (cubiq) такого узла нет: есть `IPAdapterFaceID` и `IPAdapterUnifiedLoaderFaceID`, а «FaceID Plus V2» — пресет загрузчика
([runcomfy: IPAdapterFaceID](https://www.runcomfy.com/comfyui-nodes/ComfyUI_IPAdapter_plus/IPAdapterFaceID)). Режим не заработает даже после установки пакета, переписывать его без самого пакета вслепую я не стал.
Граф `detailer_and_upscale` и узлы `ColorMatch`, `FaceDetailer`, `SAMLoader`, `UltralyticsDetectorProvider` существуют в сторонних пакетах, но их входы не сверены (пакеты не установлены).

## 6. SpendFirewall: гейт «бесплатно» обходился (исправлено), по умолчанию выключен (не исправлено)
Проверено пробами. `SpendFirewall.is_free_model` искал текст `127.0.0.1` или `localhost` где угодно в URL провайдера: хватало метки хоста (`https://127.0.0.1.attacker.example`), пути или части перед `@`; платный вызов считался
бесплатным даже при настоящей ненулевой цене. Любое имя модели с суффиксом `:free` считалось бесплатным у любого провайдера, а флаг `free` перебивал реальную цену. Исправлено: URL разбирается, локальным считается только loopback-хост,
`:free` действует только у OpenRouter, явная ненулевая цена сильнее флагов и имён, отсутствующая цена не равна нулю, облачные модели через локальный Ollama (`:cloud`) не бесплатны. Тесты: `tests/test_spend_firewall_free_gate.py` (18 проверок).
НЕ исправлено, решение владельца: в `economy_orchestrator.py` `SpendFirewall` включается только при `BOSSMAN_ENFORCE_SPEND_FIREWALL=1` или заданном токене, то есть по умолчанию он ВЫКЛЮЧЕН, и вызывается он всего из двух модулей
(`economy_orchestrator`, `features/studio`); основной периметр остаётся прежним (`Registry.adapter_for` и `provider_governance`). Формулировка «fail-closed на любой платный вызов» поэтому неверна. Включение по умолчанию заблокирует платные роли
(GLM, Haiku), которые ты одобрил, пока для них нет токена, поэтому без твоего решения я его не включал.

## 7. Cloud Horde: заявление ПОДТВЕРЖДЕНО
`docs/audits/2026-10-28-cloud-horde-execution/receipt.json`: все 5 задач и запусков (id 725, 728-731 / run 722, 725-728) сверены с живой базой Bossman (только идентификаторы, статусы, токены, стоимость и модели; содержимое не читалось):
статус `completed`, стоимость 0.0, токены и модели совпадают. Оговорка: 0.0 записан самим Bossman, со счетом провайдера не сверялся (модели бесплатные); задачи короткие (120-156 токенов на вход), это проверка прохождения, а не нагрузка.

## 8. Ветка 3: `AppActionPassport` не подключён ни к одному приложению
Заявлено: «единый контракт для всех 6 дочерних приложений (War Terminal, Social Farm, Accountant, Shorts Factory, BossBlocks, Poker Vision)», статус PASS. Проверено: `bcc/v2/app_passport.py` (199 строк) — корректный класс
(валидация полей, отказ на ключи T3 в payload, колбэк остановки, квитанция SHA-256), но поиск по всему репозиторию (19 приложений в `apps/`, `bcc`, `bossman-core`) находит его импорт только в одном тесте. Ни одно приложение паспорт не создаёт и не проверяет.
Квитанция — простой SHA-256 по полям (HMAC только если передан ключ), то есть защиты от подделки не даёт. Исправлено описание модуля (честная пометка «NOT WIRED»), строка таблицы Аргона отозвана. Подключение к приложениям не сделано: это отдельная работа по каждому приложению.

## 9. «Проба камеры Stage 6»: PASS без поиска камеры (исправлено)
`VirtualUVCCameraProbe` в `bcc/devices/worker_node.py` не искал камеру: в коде стояло `has_physical = False` («mock check»), а ответ всегда был `status: PASS`, `presence_detected: True`, `calibrated: True`, `resolution 1280x720`, `fps 30` и хэш константной строки
как «кадр». На этом ПК камера есть («AMD Camera Device», статус OK), проба её не видела. Сделано: проба перечисляет present-устройства класса Camera/Image средствами Windows (PnP, кадр не снимается, лампочка не загорается) и честно отвечает
`DETECTED` / `NO_CAMERA`, без выдуманных разрешения, fps и калибровки; синтетический самотест помечен `SYNTHETIC_ONLY`. Живой результат на этой машине: `DETECTED`, устройство «AMD Camera Device». Тесты переписаны (2 вместо 1).
Кроме того, два теста Аргона закрепляли фасады мобильного пульта (`voice/turn` с канной репликой «Выполняю», `rollback` и `blast-radius` с выдуманными нулями), они переписаны на честное поведение; `blast-radius` при недоступном Bossman теперь отвечает 502, а не падает.
Квитанция `docs/audits/2026-10-28-mobile-dag-devices-freeze/receipt.json` (status PASS) в части мобильного пульта, пробы камеры и AppActionPassport недействительна; Stars BL-121, Swarm DAG, Memory v16 и heartbeat/cascade STOP устройств мной не перепроверялись.

## Что это значит для слияния
Пока таблица `PROGRESS_RU.md` называет PASS то, что было фасадом или выдумкой, ей нельзя верить без проверки. Остальные заявления этой таблицы (ветки 3, 5, 6, 7, Cloud Horde, 17 skills) мной не перепроверялись.
