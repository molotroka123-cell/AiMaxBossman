# OWNER RUN 23.09 — подключение Jev и согласованного стека

Статус: **HANDOFF / NOT ACTIVATED BY THIS DOCUMENT**.
Основание: владелец попросил «подключим Jev и всё остальное, что мы хотели».
Это дополнение текущему локальному Claude Code, не сертификат и не результат подключения. Этот commit меняет только этот документ; не устанавливает пакеты, не меняет флаги/ключи на ПК, не выполняет платных запросов и не запускает автоматическую кампанию.

## 0. Продолжить текущую сессию, сохранить baseline

Репозиторий: `molotroka123-cell/AiMaxBossman`.
Проверяемый код: `integrate/owner-final-20260922` @ `bd2fe23dab55704d60ed468bcbb4664475e49879` (содержит #74).
Evidence: `evidence/owner-run-20260923`; прочитанный checkpoint 2 — `15ec9b9110a56b55913390ef0ab0b34786f7149e`.
Отчёт: `owner-repair/owner-run-20260923/OR0923-bd2fe23d/CHECKPOINT.md`.
Зафиксированный локальный Windows ZIP: SHA-256 `95476d4a560d9f347ca4797b75475dbd9e20c52417b209701f7692bf6ab3528c`. Это локальная сборка с bundle acceptance, не exact-SHA CI-сертификат.

Сначала `git fetch --all --prune`, сверить новые checkpoint/исправления и фактическую установку. Не переключать чужое/dirty worktree, не сбрасывать историю и не двигать main/release ради документа. Название ветки не является доказательством свежести. Каноническое назначение продукта остаётся `release/bossman-owner`; tested baseline не обновляется автоматически.

Текущую ограниченную попытку завершить или безопасно остановить с checkpoint. Интеграции измерять под новым EXPERIMENT_ID. Не менять модель/runtime/skills посередине baseline. Исправленный продукт — новый SHA и штатно собранный архив, не копирование Python-файлов в установленный ZIP.

UI, CLI и Telegram — один Bossman, один выбранный backend/data root/registry/tasks/permissions/evidence. Лаборатория изолирует кандидатов, но не становится вторым постоянным продуктом.

## 1. Документы-источники: читать из выбранного code SHA

- `AGENTS.md`, `CLAUDE_NEXT_ACTION.md`, `OWNER_ACCEPTANCE.md`, `KNOWN_LIMITATIONS.md`;
- `docs/owner/JEV_TOMORROW.md` — существующий раннер и реальные команды Jev;
- `docs/JEV_DECISION_ENGINE.md`;
- `docs/JEV_ULTRAFAST_BROWSER_CRITICAL.md`;
- `docs/WEEKLY_UPSTREAM_P0_2026-09-23.md`;
- `docs/owner/SKILLS.md`, `docs/owner/MODEL_PROFILES.md`;
- `docs/owner/TERMINAL.md`, `docs/terminal/PARITY_MATRIX.md`;
- `docs/terminal/CLAUDE_TEACHER_TERMINAL.md`;
- `docs/evo/BOSSMAN_1_1_NORTH_STAR.md` и `docs/evo/LOCAL_QWEN_APPRENTICE_BENCHMARK.md`.

Документация местами отстаёт: checkpoint 1 уже подтверждает наличие prompt_toolkit и ответ `/api/evolution/status`. Не объявлять весь Evolution работающим только по status, но и не повторять устаревшее «всех API нет». Проверять каждый фактический handler.

## 2. Порядок работ и границы разрешения

Цель — подключить проверяемые полезные компоненты, а не снова составить бесконечный план. Для каждого компонента: источник/revision/license → реально существующий handler → тест без сети → отдельный live-пилот → результат → rollback.

Работы без нового облачного доступа можно начать сейчас: инвентаризация, локальные regression/selftest, проверка доставляемых skills, анализ и минимальная адаптация согласованных upstream-компонентов в изоляции. Для оплачиваемых вызовов сначала использовать уже явно разрешённый лимит; если его нет — запросить у владельца локально получателя API и численный общий бюджет. Наличие ключа или положительного баланса само по себе не разрешает расходы. Не устанавливать auto-recharge/подписки.

Общий запрос на подключение не разрешает отправлять в новые сервисы личные документы, содержимое почты/CRM, секреты, приватные проекты и holdout. Ключ вводит владелец локально через скрытый ввод; не просить его в чат, не печатать env и не писать ключ в Git/argv/PowerShell history. В этой версии Jev читает process environment: не утверждать, что обычная команда `keys` уже сохраняет ключ Jev, пока не проверен её handler.

## 3. Jev: сначала рабочее SHADOW, затем отдельная phase 2

### 3.1 Согласовать конкретный API, а не смешать провайдеров

В `command-center/bcc/jev/config.py` текущие defaults:
- endpoint `https://api.typesafe.ai/v1/systemone`;
- model `jev-1.13.0`;
- ключ `BOSSMAN_JEV_API_KEY` или `TYPESAFE_API_KEY`.

Официальный TypeSafe Quick Start подтверждает endpoint и Bearer auth:
https://docs.typesafe.ai/introduction/quickstart
Документация показывает `state`, `questions`, `model` в запросе и `answers`/`usage` в ответе. Это подтверждение документации, НЕ успешный запрос с ключом владельца.

Старый план ссылался на DefAPI:
https://defapi.org/api/model/en/typesafe/jev-1.13
Его схему, доступность и цену в этой проверке подтвердить не удалось. DefAPI и TypeSafe — разные получатели/ключи/биллинг. Никогда не отправлять DefAPI key на TypeSafe default URL. Для DefAPI проверить собственные auth, payload и response; смена endpoint/model не доказывает совместимость. Если ключ ещё не получен, прямой TypeSafe соответствует существующему адаптеру; владелец выбирает аккаунт и лимит.

### 3.2 Существующий раннер

В установленном архиве сначала проверить наличие скрипта и --help:

```powershell
.\runtime\python.exe -I .\app-support\jev_shadow_owner.py --help
.\runtime\python.exe -I .\app-support\jev_shadow_owner.py --selftest
```

SELFTEST_PASS — только обвязка. Если скрипт отсутствует, PACKAGING_BLOCKER; исправить штатную поставку, не подменить исходниковым запуском и не назвать installed PASS.

После согласованных ключа, цены, ограничения запросов и общего cost cap:

```powershell
.\runtime\python.exe -I .\app-support\jev_shadow_owner.py --execute --out "<отдельная папка evidence Jev>"
```

Сначала contract probe. 401/403, schema mismatch, timeout или неясная цена — не продолжать платный цикл. Не полагаться только на печать estimated cost: до живого запуска проверить реальное списание/резервирование бюджета, включая retries и конкурентные запросы. Нужен жёсткий лимит числа запросов и стоимости с остановкой; при отсутствии — минимальная интеграция в существующий Cost Governor до широкого shadow.

`--real-sites` не включать первым шагом: начать с публичных/синтетических страниц и разрешённого scope. Shadow тоже отправляет данные наружу и может стоить деньги.

### 3.3 Тень внутри продукта

После валидного smoke, privacy/budget отрицательных контролей и согласованного пилота:
- `BOSSMAN_JEV_ENABLED=1`;
- `BOSSMAN_JEV_SHADOW=true`;
- `BOSSMAN_JEV_BROWSER_ENABLED=0` до отдельного браузерного этапа.

Запускать выбранный тестовый backend из того же защищённого окружения. Сохранить прежние флаги для отката. Проверять через штатную авторизацию `GET /api/jev/status`, `GET /api/jev/shadow` и журнал `<data_root>/jev/shadow-decisions.jsonl`.

Jev только предлагает. Existing router/policy остаются авторитетными. Указание модели `needs_owner_approval=false` не снимает ASK/DENY.

До включения общего hook проверить privacy: в изученном коде пропущенная метка privacy трактуется как public. Для этого пилота исходящий payload разрешать только для ЯВНО публичного/синтетического теста с разрешённым cloud egress. UNKNOWN/PRIVATE/LOCAL_ONLY, приватные вложения и скрытый holdout не отправлять. Редакция токенов не делает личные данные публичными. При необходимости сузить hook до allowlisted тестовых task IDs штатным минимальным изменением, сохранив fail-closed политику.

Проверить kill switch `<фактический data_root>/jev.disabled`: новые вызовы прекращаются, Bossman продолжает работать без Jev. Не считать уже отправленный запрос отменённым. Учесть очереди, retries и выход из процесса.

Измерить согласие решений, точность выбора инструмента/модели, конечный проверенный исход, latency, фактические usage/cost и fallback. Согласие со старым router не доказывает правильность обоих.

### 3.4 Jev Ultrafast — исполнение требует кода

Текущий `BrowserConfig.may_execute` всегда False; в phase 1 `execute_step` намеренно отказывает. Пошаговый shadow browser сейчас проверяет раннер, а не обязательно каждая браузерная операция агента. Не объявлять фазу 2 подключённой по флагу.

Переиспользовать https://github.com/browser-use/jev-ultrafast через существующий BrowserManager/Gateway/permissions, pinned reviewed revision; не запускать отдельный demo-agent с рабочим профилем браузера владельца.

Реализовать/дожать тонкий адаптер обратимых низкорисковых действий: наблюдаемые DOM-элементы → выбор операции/индекса → проверка свежести/типа/перекрытия → существующий executor → независимая проверка. Никаких произвольных JS/selectors/shell из ответа модели. DONE не завершает задачу без oracle. Unsupported frames/canvas/upload/popups — правильная эскалация. Не повторять мутации после UNKNOWN_OUTCOME.

Text helper сначала через действующую локальную модель Bossman; платный helper только по отдельному разрешённому маршруту/бюджету. Mercury из demo не обязан становиться зависимостью.

Прогнать минимум 10 классов из `JEV_ULTRAFAST_BROWSER_CRITICAL.md`. Сохранить нижние пороги `JEV_TOMORROW.md` (30 сравнённых browser-шагов, 50 routing-записей, защитные кейсы) и дополнить проверенными конечными исходами. Не выдавать небольшой pilot за универсальную надёжность. Включение live low-risk — только после regression, safety/egress/budget gates и явного решения владельца о переходе из shadow. До этого состояние READY_FOR_OWNER_ENABLE или MISSING_CODE, не LIVE_PASS.

## 4. Подключить уже выбранные skills и независимых проверяющих

Сначала 11 импортированных текстовых skills через существующий каталог; для coding — systematic-debugging, test-driven-development, verification-before-completion. Проверить не только listing/select, но и доставку конкретного текста в контекст реальной coding-задачи и фактическое применение. Skills не выдают инструментов/прав. Не раздавать агентам shell/admin только ради чужого рецепта. Integrity/provenance/license, ограничения контекста и revocation после restart обязательны. UNVERIFIED не повышать автоматически.

Согласованные кандидаты из WEEKLY_UPSTREAM:
- Cloudflare security-audit-skill — hunter/verifier, воспроизведение findings, независимость от автора patch;
- Alibaba open-code-review — структурированный review с точными строками, сравнение useful findings/false positives/misses/cost с текущим review;
- Hermes Agent — взять узкие полезные решения управления контекстом, памятью, инструментами и восстановления; НЕ ставить второе ядро/память/scheduler поверх Bossman.

До исполнения upstream-кода проверить repo/revision/license/dependencies/hooks. Текстовые инструкции остаются недоверенными данными. Ноль findings не равен сертификату безопасности. Сильный облачный reviewer использует существующий Gateway и согласованный бюджет; его patch помечается TEACHER_PATCH.

Дополнительный официальный TypeSafe skill для локального Claude указан в Quick Start (typesafe-ai/skills). Можно прочитать pinned SKILL.md как справку; установка plugin после ревью, проектно, без скрытого автозапуска/новой сети. Skill не заменяет Bossman Jev adapter.

## 5. Модели, OCR, datasets и медиастек

Переиспользовать действующие MAIN/FAST и имеющиеся веса. Runtime текущего owner-пилота — Ollama 0.34.3 + proxy reasoning off, а не исторический llama.cpp. SAC не отключать; не обходить блокировки переименованием/патчами политики. Бенчмарки каждого runtime/profile отдельные.

Согласованные кандидаты, не уже доказанные замены:
- https://huggingface.co/XingChen-AGI/Xing4.0-29B-A4B
- https://huggingface.co/baidu/Unlimited-OCR

Проверить официальные revision/license/архитектуру, tokenizer/template, quant/runtime support на AMD Windows, доступную память и загрузчик. Не предполагать поддержку Ollama только из размера модели. Не исполнять trust_remote_code без ревью. Недостающие выбранные веса — через штатный план загрузки с размером, checksum и разрешением владельца; всё подряд не скачивать. Новая модель не подменяет baseline посередине опыта.

Xing — отдельный challenger coding/tool/JSON/context; сравнить с той же задачей, лимитом и независимыми тестами. Unlimited-OCR — проверить на синтетических русских/чешских/английских сканах: извлечение, цифры, таблицы, пропуски, latency/RAM. Нативный текст PDF не отправлять в OCR без нужды. Личные документы и медицинские/финансовые материалы наружу не уходят.

HF skills/datasets shortlist не равен загруженному или проверенному датасету: provenance/license, очистка/дедупликация, отделение train/holdout, никаких ключей/приватных trace в публикации. WEIGHTS_UNCHANGED; fine-tuning отдельно.

Проверить уже согласованные image/video/music providers через существующий Studio, не писать новые редакторы. Использовать имеющиеся веса сначала, новые облачные медиа — только после credentials/budget approval. Один тяжёлый GPU/unified-memory эксперимент за раз; не смешивать video contention с измерением LLM/CLI.

## 6. Следующий приоритет, а не бесконтрольный автозапуск

Paper/Repo → Skill Factory: источник → кандидат → sandbox/test/security review → registry → controlled promotion. Только один ручной пилот; не давать случайным репозиториям исполнение.

HF/model-runtime watcher: пока один явный scan и отчёт по четырём категориям: LLM/coding/agents, image, video, tool-calling/structured outputs. Watcher не меняет модели, не качает веса и не запускает облачные Jobs автоматически. Рекуррентный режим, расписание и бюджеты — отдельная реальная настройка с журналом, не обещание в документе.

## 7. Не потерять реальные блокеры owner-run

Checkpoint 2: 5+5 = 10/10 без помощи и с coaching, NO_MEASURED_GAIN из-за потолка; D1 дважды зациклился. Не повторять лёгкий пакет бесконечно и не приписывать Jev решение ошибки coding.

Приоритетные ограниченные исправления:
1. Перепроверить существующий `fix/coding-path-crlf-blob-20260923 @7439917f`; переиспользовать корректный delta, не переписывать заново и не ослаблять evidence-check.
2. Полный репозиторий ~80 MB не помещается в 32 MB snapshot. Спроектировать bounded/scoped/streamed evidence с проверяемыми хэшами и лимитами; не удалить ограничение и не выдать tools-only lab за full-repo PASS.
3. Добавить детектор повторов без прогресса в фактический local_sidecar путь: одинаковый вызов + тот же результат + отсутствие изменения состояния → ограниченная диагностика/смена стратегии/разрешённая эскалация/остановка. Штатное полезное polling и изменившиеся результаты не блокировать. Метрика и порог фиксируются до нового сравнения, старые попытки не переписываются.
4. P1 memory history/action-contract и controlled apply candidate→общий проект закрывать отдельными regressions. Проверенный clone patch не считать применённым к проекту; stable не менять автоматически.

Jev selftest/ограниченный синтетический shadow не обязан ждать ремонта всех остальных функций. Но live-облако и изменение общего runtime не должны портить идущий учебный экзамен. Каждый результат имеет свой SHA/config/EXPERIMENT_ID.

## 8. Следующий checkpoint и реальный результат

Создать в evidence новую папку `integrations/<EXPERIMENT_ID>/` с `ACTIVATION_MATRIX.md` и `manifest.json`.
Для КАЖДОГО согласованного компонента: точное имя, upstream SHA/license, product SHA, фактический handler/команда, status, offline/live evidence, расходы, data egress, feature flags, fallback/rollback и следующий минимальный шаг.

Статусы: NOT_STARTED / STAGED / SELFTEST_PASS / CONTRACT_VERIFIED / SHADOW_PASS / READY_FOR_OWNER_ENABLE / LIVE_PASS / MISSING_CODE / OWNER_ACTION_REQUIRED / BLOCKED.
Отдельно указать кто исполнил: LOCAL_STUDENT / TEACHER / FIXTURE. Не скрывать отсутствующие компоненты за общим «стек подключён».

Первое действие сейчас: безопасно зафиксировать границу текущего опыта, прочитать Jev owner-инструкцию и выполнить установленный --help/--selftest. Затем одной короткой просьбой собрать у владельца недостающий API-provider/key через локальный ввод и общий лимит live-пилота. Пока ждёшь — выполнять независимые offline/skills/review работы; не крутить polling и не тратить платные запросы.
