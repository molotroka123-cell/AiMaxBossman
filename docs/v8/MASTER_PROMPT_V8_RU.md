# МАСТЕР-ПРОМТ V8 — Bossman Studio: свой Higgsfield, лучше и честнее

Единственная рабочая ветка: `claude/bossman-final-convergence-hu2702`.
Не применяй force-push/reset, не создавай второе ядро или вторую ветку
конвергенции. Aster пишет в ту же ветку параллельно: fetch перед push,
стадируй только свои пути, коммить каждый чекпоинт.

## 0. ПРАВИЛО, КОТОРОЕ НЕЛЬЗЯ СМЯГЧИТЬ

`completed` от провайдера → наблюдение → независимая проверка байтов Bossman
(декодирование, размеры, длительность, sha256) → улика в `studio_runs` → гейт
завершения → `COMPLETED`. Никогда не отображай статус провайдера прямо в
статус задачи. Отсутствующий ключ, 429/квота, недоступная модель, отказ по
контенту и неверный ответ — **пять разных результатов**; ни один не
превращается в другой и ни один не PASS.

## 1. ПРОЧТИ ПЕРЕД НАЧАЛОМ (не пересказывай — проверь)

`docs/v8/README.md` и весь пакет `docs/v8/`; `docs/final/CURRENT_STATE.md`
(кандидат `e39610de`), `docs/final/BUG_LEDGER.md` (BL-066, BL-069…BL-073),
`docs/final/MASTER_PROMPT_NEXT_RUN.md` §0/§2/§7/§8/§10,
`docs/final/one-archive-audit-20260917/MASTER_PROMPT_RU.md` §1/§7/§9,
`docs/final/SAFETY_INVARIANTS.md`, `docs/final/FAILURE_MATRIX.md`,
`docs/final/CONNECTOR_INTEGRATION_REPORT.md`, `docs/oss/README.md`.
Код: `bcc/features/images.py`, `bcc/v2/images_runtime.py`, `bcc/oss/comfyui.py`,
`bcc/v2/model_router.py`, `bcc/model_health.py`, `bcc/reality/recovery.py`,
`bcc/v2/openrouter_identity.py`, `bcc/provider_governance.py`, `bcc/v2/governor.py`,
`bcc/run_provenance.py`, `bcc/video_studio/{commands,storyboard,analysis,read_verification}.py`,
`tools/hailuo_rescue.py`, `tools/seedance_shorts.py`, `tools/mcp_acceptance.py`,
`tools/acceptance_registry.json`, `.github/workflows/windows-bundle.yml`.
Старые абзацы отчётов не переопределяют более свежие результаты.
`command-center/build/lib/**` — устаревшая копия, не читать.

## 2. ЧТО УЖЕ ЕСТЬ — НЕ ПИСАТЬ ВТОРОЙ РАЗ

Очередь с захватом и отменой (Images), Vault и разрешение ключей, словарь
состояний здоровья, лестница восстановления, governance цены/локальности,
бюджет governor, MCP-хаб и полоса приёмки, provenance прогонов,
проверка байтов Video Studio, реестр приёмки и заморозка, развёртка UI.
Студия использует их; новый код — только `bcc/studio/`,
`tools/studio_models.json`, страница `images` («Студия»), тесты, документы.
Новых зависимостей — ноль.

## 3. ПОРЯДОК РАБОТ (не менять местами)

Фазы 0 → 1 → 2 → 3 → 4 → 5 из `docs/v8/IMPLEMENTATION_PLAN.md`. Внутри фазы:
воспроизведение (тест, падающий на текущем коде) → негативный контроль парой
→ минимальная правка → `python tools/skips_registry.py --check` → коммит
явными путями → push → прогон Windows-архива, если менялись файлы из
фильтра путей workflow → обновление `docs/final` и `docs/v8` тем же или
следующим коммитом.

Фаза 0 начинается с замера baseline (импорт `bcc.app`, RSS/CPU покоя за 60 с,
первый ответ `/api/images/models`, размер архива) и записи его в
`docs/v8/METRICS_BASELINE.md`. Без baseline интеграция не принимается (§7).

## 4. КЛЮЧИ, ДЕНЬГИ, ЭГРЕСС

Ключи — только Vault приложения или переменные окружения / Actions Secrets.
Не помещай их в код, аргументы, журналы, улики, учебный корпус; в API и
логах — `…last4`. Старый ключ из переписки не считай действующим.
По умолчанию `free_only = true`; платный fallback запрещён; неизвестная цена
облачной модели дисквалифицирует её для автоматического выбора. Первая
отправка байтов владельца провайдеру — только после подтверждения в
интерфейсе; публичных загрузок нет. Нет ключа → `OWNER_REQUIRED`, не FAIL.

## 5. ПРОВАЙДЕРЫ

Порядок внедрения: ComfyUI (обёртка существующего) → OpenRouter
(`/api/v1/images`, `/api/v1/videos`, остаток `/api/v1/auth/key`; логика из
`tools/seedance_shorts.py`) → **официальный Higgsfield через его MCP-сервер**
(`generate_image / generate_video / generate_audio`, `jobs_wait`, `balance`),
допущенный полосой `tools/mcp_acceptance.py`. REST-адаптер Higgsfield пишется
ТОЛЬКО по официальной документации от владельца: на 17.09 контракт не
подтверждён (`docs.higgsfield.ai` закрыт egress-прокси), форма из чужого
клиента `open-higgsfield` — гипотеза, эндпоинты подбирать запрещено. Аккаунт
владельца: `credits = 0`, план `free` → живая генерация Higgsfield сейчас
`OWNER_REQUIRED` с причиной `insufficient_credit`. Каждый облачный адаптер — `ADOPT_AS_OPTIONAL_BACKEND`, выключен по
умолчанию, ленивый старт, остановка по бездействию, числа покоя приложены.
Каталог `tools/studio_models.json` — источник истины для интерфейса; проба
(`capability_probe`) — судья; `VERIFIED` в файле всегда `false`.

## 6. ПРИЁМКА

Сценарии студии добавляются в `tools/acceptance_registry.json` (профиль
`windows-installed`), минимум растёт ровно на их число; проверка —
`require_acceptance_results.py --profile windows-installed --source-sha` на
установленном архиве. Живой шаг `app-support/studio_live_owner.py` запускается
из архива: без ключа код 2 и `OWNER_REQUIRED`; с ключом — 1 изображение + 1
короткое видео на явно бесплатных маршрутах, provenance в уликах. Строки:
`BOSSMAN_STUDIO_CATALOG=`, `BOSSMAN_STUDIO_ACCEPTANCE=`, `BOSSMAN_STUDIO_LIVE=`,
`BOSSMAN_STUDIO_BUDGET=`, `BOSSMAN_STUDIO_EGRESS=`. Заморозка: `FROZEN` только
при `PASS` живого провайдера на архиве; иначе `WINDOWS_RC_READY_OWNER_REQUIRED`.

## 7. НЕ ПОДГОНЯЙ ТЕСТЫ

Не удаляй, не пропускай, не ослабляй тесты; не поднимай тайм-ауты без
замера; не мокай провайдера в живой проверке; не пиши `VERIFIED = true`
руками; не считай недоступный бэкенд успехом; не создавай
`intelligence-preservation-current.json` с выдуманными результатами.

## 8. ЧТО НЕ ДЕЛАТЬ

Собственные веса и обучение генеративных моделей; копирование кода
open-higgsfield (LICENSE нет) и интерфейса Higgsfield; новые экраны-двойники,
агенты, реестры, менеджеры сайдкаров; TikTok, сайты, 3D, маркетплейс,
«виральность»; фоновые опросы провайдеров; автоматическое включение тяжёлых
моделей; заявления о железе владельца по hosted Windows.

## 9. ЧТО ОБНОВИТЬ НА ВЫХОДЕ КАЖДОЙ ФАЗЫ

`docs/final/CURRENT_STATE.md` (раздел студии с числами прогона),
`docs/final/BUG_LEDGER.md` (каждый найденный дефект — своя запись с
воспроизведением и регрессией), `docs/final/KNOWN_GOOD_SHAS.json`,
`docs/oss/README.md` (новые направления по пяти ответам), `INSTALL.md`
(как включить провайдер, где ключ, что уходит наружу), `docs/v8/README.md`
(статус фазы), при коннекторе — `docs/final/CONNECTOR_INTEGRATION_REPORT.md`.

## 10. ФОРМАТ ФИНАЛЬНОГО ОТВЕТА (коротко, без воды)

```
STUDIO PHASE: <0..5>
FINAL SOURCE SHA / APPLICATION ZIP / SHA-256 / SIZE
WINDOWS INSTALLED ACCEPTANCE: N/N PASS
STUDIO CATALOG: n моделей, verified k, unverified m
STUDIO LIVE: openrouter:<…> higgsfield:<…> comfyui:<…>
STUDIO BUDGET: spent/cap | NOT_CAPTURED:<reason>
UI SWEEP: …
IDLE COST: import / RSS / CPU / first-answer — до → после
REPOSITORY-FIXABLE P0: … / P1: …
REMAINING OWNER-ONLY ITEMS: …
ROLLBACK: предыдущий SHA + архив
```

Не заканчивай планом или аудитом. Не заканчивай из-за отсутствия одного
внешнего ключа: репозиторная часть каждой фазы делается целиком, живая —
`OWNER_REQUIRED`. Реализуй → воспроизведи → почини → регрессия → собери →
проверь тот самый ZIP → заморозь → запушь.

## ГЛАВНЫЙ ПРИНЦИП

«Свой Higgsfield» — это своя студия и свой контроль над байтами, деньгами и
уликами, а не свои модели. Каждая строка отчёта — измерена, а не написана.
