# Bossman 1.1 — финальная передача ветки Claude

Дата: 2026-09-22 UTC / 23 сентября в Праге.
Ветка: **codex/bossman-v1.1-evolution**.
Статус: код экспериментального цикла и исследование переданы; живой owner-прогон и интеграция полной сборки ещё не пройдены.

## Короткая задача Claude на завтра

Заверши текущую 1.0 в `release/bossman-owner`. Затем в отдельном worktree перенеси
**только изменения модуля evolution и его документацию** из этой ветки в общую
интеграционную сборку. Сохрани новые коммиты остальных участников. Не делай
force-push, не переключай чужой worktree и не выдавай эту исследовательскую ветку
за уже принятую полную 1.1.

Прочитай сначала этот файл, затем `BOSSMAN_V1_1_CLAUDE_IMPLEMENTATION.md` и
`docs/evolution/LOCAL_CHAMPIONS_88GB.md`. Не исследуй заново все ветки.

## Реально передано

- Фиксированные pytest-сценарии и повторный baseline; нестабильность или отсутствие зависимости блокирует эксперимент.
- Турнир 1–5 альтернатив на общей базе; выбор по исправленным проверкам и размеру патча.
- Отдельная модель reviewer, полное покрытие изменённых файлов, привязка ответа к nonce / base SHA / hash патча. Неудачное ревью не меняет champion.
- Отдельные Git worktrees и экспериментальные candidate refs; CLI запускает модельные правки только через ограниченный Docker executor.
- Сохраняемые резервы бюджета proposer + reviewer, отчёт фактической известной стоимости, ошибки и отрицательный опыт в существующем LearningStore.
- Хеши evidence manifests и команда `verify`; изменение сохранённого доказательства блокирует продолжение кампании. Это контроль относительно checkpoint, не внешняя аттестация.
- Однократный отдельный `validate`: holdout не пересекается с train/regression по файлам, не попадает в память обучения и закрывает дальнейшие эксперименты этой кампании даже при неуспехе.
- Исправление числовых acceptance gates Self Improvement Lab: NaN/Infinity/отрицательные и невалидные метрики не принимаются.
- Три основных локальных model/quant профиля с точными размерами, HF revisions и SHA256; источники Cloudflare / Alibaba / Hermes и полезные паттерны GEPA / DGM / autoresearch.

Ревью внешними Cloudflare / Alibaba пока **PENDING**. Их исходники закреплены,
но адаптеры не установлены и их ревью не выдаётся за пройденное. Ни один кандидат
автоматически не получает VERIFIED и не заменяет production через обход LearningGuard.

## Проверка этой поставки

**120 passed, 1 warning**, Linux / Python 3.12. Прежний `SyntaxWarning` относится
к escape-последовательности в анализируемом исходнике теста context slice.

```bash
PYTHONPATH=.:bossman-core:command-center PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/test_evolution_runner.py tests/test_evolution_metrics.py tests/test_evolution_protocol.py tests/test_context_bytes_checkpoint.py tests/test_context_slice.py tests/test_cache_observation.py tests/test_cache_intelligence.py tests/test_learning_store_authority.py tests/test_audit_p0_trace_identity.py bossman-core/tests/test_v3_compound_resume.py bossman-core/tests/test_v3_self_improvement.py
```

Это настоящие Git/pytest-прогоны с явно тестовыми provider fixtures. Проверены
турнир, отказ reviewer, replay/coverage, целостность, сохранение бюджета, успешный
и проваленный holdout, запрет повторного использования holdout, ограничение вывода.
Docker binary, Claude CLI и целевой AMD здесь отсутствуют: live LLM, контейнер,
Windows installer, скорость и пиковая память на машине владельца не проверены.
Полный CI всей сборки этот результат не заменяет.

## Порядок интеграции в одну ветку

1. Fetch только `release/bossman-owner` и `codex/bossman-v1.1-evolution`, запиши их SHA.
   После принятия 1.0 создай от owner отдельный integration worktree.
2. Наш исходный feature commit: `9a325d566b306908f833b083e453b552285f5b44`.
   Следующий commit в этой ветке — усиление цикла и эта передача. Найди его через
   `git log --oneline origin/codex/bossman-v1.1-evolution -- docs/evolution/V1_1_FINAL_HANDOFF.md`.
   Перенеси эти feature commits по порядку, предварительно проверив, не перенесены
   ли они уже. Не вливай всю историю предков исследовательской ветки вслепую.
3. При конфликте существующие gateway, memory authority, approvals и упаковку
   оставь едиными: адаптируй модуль к ним, не создавай параллельные реализации.
4. Упакуй repo-level `tools/`, `config/evolution/` и документы вместе с приложением.
   Свяжи текущий Self Improvement Lab UI/API с запуском/остановкой и отчётом этой
   кампании; UI не должен сам повышать эксперимент в trusted skill.
5. Повтори целевые тесты, штатные CI/owner gates и реальный Docker baseline.
   Сохрани все результаты на **одном итоговом SHA**. Верни изменения в
   `release/bossman-owner` обычным fast-forward/PR после успешной приёмки.
6. Настрой существующий scheduler только после живого smoke. Одна активная
   кампания, ограниченные время/число кандидатов/расходы, STOP и утренний отчёт.
   Если сценарии проходят, `NEEDS_NEW_SCENARIOS` честно завершает цикл: следующий
   фиксированный набор строится из реальных дефектов, не из выдуманного прогресса.

## Запуск и независимая оценка

```text
docker build -t bossman-evolution:1.1 config/evolution
python tools/bossman_evolve.py assess --executor docker
python tools/bossman_evolve.py run --backend claude --model ACTUAL_CLAUDE_MODEL_ID --allow-cloud --review-backend local --review-url http://127.0.0.1:8088/v1 --review-model meta-models/Muse-Glimmer-30B --iterations 3 --candidates 3 --max-usd 2 --max-seconds 1800
python tools/bossman_evolve.py verify
python tools/bossman_evolve.py report
```

Замени `ACTUAL_CLAUDE_MODEL_ID` фактически доступной моделью. Загрузи Muse и проверь
её JSON/final-output контракт до запуска. Здесь одна локальная модель, поэтому
не требуется держать большой worker и reviewer одновременно в UMA.

Для all-local режима CLI требует доступных builder и reviewer endpoints.
Последовательную смену моделей между этими вызовами должен обеспечивать существующий
runtime manager; **CLI сам не выгружает/загружает веса**. Простое указание трёх model ID
не реализует routing. При отдельном smoke загружать модели по одной. Суммарную
резидентную память всех одновременно работающих серверов проверять против 88 GB,
а не проверять каждый сервер отдельно. Большой Coder-Next не держать вместе с Muse.

Текущий LocalProposer отправляет `temperature=0.1`, `max_tokens=4096` и `json_object`;
эти поля перекрывают server defaults из model guide. Для reasoning-моделей Claude
должен связать профиль generation с существующим gateway и измерить валидный final
JSON; обрезанный ответ остаётся отказом. Не утверждать, что model-specific sampling
или reasoning parsing уже подключены этим manifest.

Holdout suite формата version=1: у каждого case `role: "holdout"`, `editable: []`,
только явные `tests: [".../test_*.py"]`; тесты заранее зафиксированы в base checkout,
не входят в train/regression. Это скрытие от prompt данной кампании, не доказательство
отсутствия знакомых модели публичных задач.

```text
python tools/bossman_evolve.py validate --holdout-suite PATH_TO_FROZEN_HOLDOUT.json
python tools/bossman_evolve.py verify
```

Встроенный holdout с выдуманной бизнес-валидностью не подставлен. Claude должен
зафиксировать отдельные реальные owner-сценарии до первого эксперимента. Не
перезапускать новый campaign directory для подгонки под раскрытый holdout.
Изменение evaluator/runtime требует новой кампании; её общий расход учитывать в
существующем бюджете Bossman, а не обнулять лимит через смену каталога.

## Завтрашние OpenRouter и Gemini

Владелец планирует дать ключ OpenRouter и подключить Gemini; среди облачных
кандидатов рассматривает GLM 5.3 и сильный DeepSeek. Это намерение включено в план,
**ключ сейчас не получен и эти provider adapters здесь не подключены**.

- В день запуска проверить точные доступные model IDs, capability, стоимость и
  контекст у провайдера. Названия GLM/DeepSeek не являются уже выбранными endpoint IDs.
- Ключ передать штатному secrets/provider manager Bossman, не в git, manifest,
  prompts, experiment evidence или память. Использовать существующий gateway,
  routing, circuit breaker и LOCAL_ONLY; не ослаблять loopback-only LocalProposer.
- Основная связка: local worker делает дешёвые эксперименты; выбранный облачный
  builder решает сложные; отдельный Gemini/Muse/другая модель опровергает патч.
  Назначать роли по измеренной успешности на задачах, не по названию провайдера.
- Облачный adapter реализует тот же контракт `(prompt, cwd, budget, timeout) ->
  (validated_json, actual_cost_or_none)`. Вызов и ревью входят в один резерв бюджета.
  Неизвестная стоимость не равна нулю; max token/retry/time ограничены gateway.
- Контролировать общий лимит расходов во всех запусках и запрет облака при LOCAL_ONLY.
  Провайдерные retries и сторонний `ocr` тоже должны учитываться.
- Их вклад в «обучение» здесь — проверенные исправления, опыт ошибок и подготовка
  VERIFIED-примеров. До отдельного воспроизводимого fine-tune/LoRA run нельзя
  писать, что изменились веса локальной модели.

## Что вернуть владельцу

Один интеграционный SHA и сборку, точные команды, измеренный baseline → candidate,
имена/кванты реально запущенных моделей, peak memory, время, стоимость, review/holdout
статусы и список оставшихся блокеров. Мировое первое место, ускорение по геометрической
прогрессии и доход за неделю не установлены этими тестами.
