# Как закончить: всё дерево в одной версии `main` — план и очередь (08.10.2026)

Назначение: владелец включает ПК и запускает модель по мастер-промту
`docs/owner/MASTER_PROMPT_PC_RUN_20261008.md`. Этот файл — материал, на который промт ссылается: что уже в `main`,
чего нет, в каком порядке вносить и что считать доказательством пользы.

Same-product Terminal Run: CLI, окно и Telegram — один Bossman, один backend :8801, одни данные, память, модели,
задачи, разрешения и STOP. Ничего параллельного не строится.
North Star: достигнуто SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT и SELF_REPAIR_SINGLE_CYCLE_PASS. Не достигнуто:
SELF_REPAIR_3_CYCLE_PASS, TRANSFER_MEASURED_GAIN, 24H/48H_SOAK, WEEK_MODE_READY, REVENUE_CAPABLE_PILOT.

## 1. Где мы сейчас

- `main` содержит весь код зелёных листьев: твоя ветка `green/tree-leaves-20261006` (4a14c26c) влита PR #98
  (merge-коммит 2f4af952), плюс ночные исправления.
- PR #99 (следующий шаг, если ещё открыт — проверить на GitHub): лимит Windows-задания `owner-experience` 45→75 мин
  (причина красной проверки на #98: таймаут, а не зависание), 8 исправлений «где может лагать UX», `keys_guard`,
  команда `/key` в «Пульте». После его слияния брать `main` за основу.
- Версия НЕ сертифицирована: `tools/exact_sha_certify.py` требует зелёные обязательные проверки на точном SHA;
  «measured intelligence retention» ждёт твой замер на ПК (комментарий владельца к точному коммиту).
- Данные дерева — `command-center/bcc/capability_tree_seed.json` (876 листьев). Статусы — заявления, не сертификат:
  `docs/architecture/tree-registry.json` на 08.10 содержит 0 листьев уровня `ci` и 0 уровня `owner_pc`.
  Менять статус листа руками нельзя — только `tools/tree_registry_sync.py` / `tools/tree_apply_evidence.py` с файлами доказательств.

## 2. Что значит «внедрить лист» — ворота пользы (по порядку, пропускать нельзя)

1. **Нужда.** Какую реальную задачу владельца лист решает; как это измерить до и после (число, не слово «лучше»).
2. **Сравнение.** Два open source конкурента (где применимо), оценка по принятой шкале G·P·D·S·V; внедряется
   только ≥ 9/10, доказанное замером (правило из `docs/audits/2026-10-08-tree-audit-13-zones/README.md`).
   Больше кода, меньше новых функций: готовое и доказанное важнее нового.
3. **Воспроизвести → тест красный на текущем коде → исправление → тест зелёный.** Не ослаблять и не пропускать тесты.
4. **Один backend.** Лист подключается к существующему backend, памяти, очереди, разрешениям; второго ядра нет.
5. **PR в `main`** (изолированная ветка, без force-push), обязательный CI зелёный на точном SHA.
6. **Доказательство на ПК** (уровень OWNER_HW) — для всего, что касается Windows, AMD, Telegram, голоса, GPU, экрана.
7. **Только после 5–6** статус листа меняется через `tools/tree_apply_evidence.py` с приложенным файлом доказательств.

Не считать доказательством: «код написан», попадание в память, правка от учителя, повтор известной задачи, слабый тест,
сгенерированный скриншот, новая точка входа CLI. Уровни считаются раздельно: CODE → TEST → CI → OWNER_HW.

## 3. Границы (не меняются ни одним листом)

Ключи, vault, личные данные, веса и кэши не публиковать и не печатать (в логах только имена). Платёжные действия,
регистрации, вход в аккаунты, капчи, отправка наружу, торговля — только с подтверждением владельца. Платных воркеров
автоматически не включать. Не выключать компьютер и не останавливать Jeff, Пульт и backend без бэкапа. STOP владельца
сильнее всего. Автоответчик Jeff не включать, пока открыты F1–F4. `LOCAL_UNRESTRICTED` — про поведение модели, не про
власть над ключами, деньгами и стабильным кодом. Не force-push; не создавать новую «финальную» ветку.

## 4. Сначала — шаги на ПК (из облака недоступны)

1. Поставить сборку с точного SHA `main` по `docs/owner/OWNER_RUN_20261008.md` (хэш архива из последнего комментария PR, откат — `rollback.ps1`).
2. Собрать локальный manifest: `python tools\local_completeness_manifest.py --root %USERPROFILE%\Bossman --out <файл>.json`.
   До этого `LOCAL_COMPLETENESS=UNVERIFIED`; секреты, веса и кэши инструмент не открывает.
3. Запушить с ПК то, чего нет на GitHub: ветку `consolidate/jeff-20261007` (автоответчик Jeff, F1–F4) и всё, что покажет manifest
   (незапушенные коммиты, stash, чужие рабочие копии). Без этого вносить нечего.
4. `python tools\keys_guard.py verify` (0 — ключи на месте). Ключ можно прислать в «Пульт»: `/key ИМЯ=значение` (только владелец).
5. Выложить замер «measured intelligence retention» комментарием к точному коммиту `main` (иначе этот гейт остаётся красным).
6. K1m6a: `docs/trading/K1M6A_DAY_RUNBOOK_20261008.md` (продолжить с ролика #26; экзамен и вердикт — `k1m6a_exam aggregate`).

## 5. Очередь листьев, которых нет в `main` (из данных дерева на 08.10)

Порядок: сверху вниз; внутри пункта — по одному листу, каждый своим PR. Пути указаны по дереву; «где код» — коммит ветки.

### A. Модули «в отдельной ветке» (код есть только в старых ветках)
| Лист | Где код | Замечание |
|---|---|---|
| assistant_plan | 954785f1 | `command-center/bcc/features/assistant_plan.py` |
| candidate_generator, eval_engine, trace_recorder | 6ebc9b75 | цикл кандидатов/оценки; сверить с `bossman_v3/self_improvement` |
| mimik, open_news | cc92b73a | навыки; в ветке `claude/bossman-final-convergence-hu2702` |
| observatory, viral_vfx | b715b68e | ветка `feat/viral-vfx-observatory-20260921`, `merge_ready=false` |
| twitter | d4e84a45 | ветка `feature/osiris-data-acquisition` |
| `bossman/apprentice/selector_repair.py` | 12832471 | |
| `bossman/cognitive/*` (context, memory, reasoning, runtime, storage, tasks, verify) | d4830a9d | старая раскладка путей; сначала проверить, не заменён ли в `bossman_v3` |
| `bossman/reality/*` (mission_ir, strategy, world_state) | 17f11312 | ветка `codex/reality-compiler-v010` |
| Jeff: пути `telegram_calls/*` (8 листов) | — | код УЖЕ в `main` под новыми путями (`account/stopflag.py`, `addon.py`, `doctor_rows.py`, `account/guard.py`, `speech/jeff_engines.py`); исправить пути в данных дерева, не переносить код |
| Звонки (cap-10), BOSSMAN_VERIFY_PYTHON, рой 07.10 | — | подтвердить на ПК живым звонком / прогоном |

Из реестра веток (`docs/architecture/bossman-tree-20261005/evidence/branch-registry-20261008.md`) дополнительно подтверждено «перенести малым PR»:
двойной клик в `computer_operator/adapters/playwright_browser.py` (fb6591e2), защита Home от ложного «всё спокойно»
и от двойной отправки (`command-center/ui/pages/home.js`, 1969ce0b и 9e8f1266). Остальные 57 веток реестра разобраны не до конца —
перепроверить перед переносом (50 проверяющих агентов упёрлись в лимит сессии).

### B. «Код написан», проверки нет
- Приложения: ai-3d-maker, ai-webcam-vision, osiris, solana-volume-suite (виртуально, без подписи).
- Poker Vision: pipeline, act, eval, ui, executor, source-panel, lora (только свой тренажёр; реальные деньги и чужие столы запрещены).
- Плагины-заглушки (честно отвечают «не выполнено», нужны ключи/живые сервисы и одобрение): github.issue_create, gmail.search/send,
  calendar.search/create, drive.search/write, telegram.send, n8n.workflow_list/run, browser.form_submit. `mcp.tool_call` уже сделан в #99/#98.
- trading_learning (benchmark, cli, frames, ingest) — тесты есть; кадры нужен OpenCV, ролики — K1m6a на ПК.

### C. Подготовлено / идеи (навыки и OSS)
- Подготовлено: skill-51 executing-plans; 10 навыков браузера и агентов (`skill-browser-*`, `skill-agent-*`, `skill-code-tdd-review`, `skill-writing-evidence-docs`); память дерева (cap-37); концерт Faint (d0710-concert-faint-clip).
- Идеи: Colibri, токен-аудитор в Telegram, OpenDots sidecar, VoiceStudio/HunyuanImage/Open Muse, Unsloth/LoRA, ускорение AMD, прямой генератор клипов (45 шотов), 10 навыков s0710-top-01…10, OSS-кандидаты GEPA, promptfoo, LiteLLM proxy, mini-swe-agent, OpenHands SDK.
  Каждый — только через ворота из раздела 2 (сравнение двух конкурентов и замер).

### D. Блокеры — закрываются прогоном и замером, не кодом
24–48 часов непрерывной работы Jeff; ускорение UX на 30% (замер холодной страницы до/после); Computer Use observe/act (живые клики);
YouTube/K1m6a → навыки; автономное самоулучшение (3 цикла + перенос на новый случай); Freeze / owner-ready; адаптер TON Poker;
Cygnet/Winnow/Jev как кликер; LoRA на Windows/AMD.

### E. Отложено владельцу (новые функции или внешние действия)
GPT Image провайдер (платные POST), пакет заявок на финансирование, голос/фоновые эффекты (спецификации), генератор Motion 56,
voxel3d/TripoSR, collector, обучение 24/7 с отчётами в Пульт (ослабляет проверку одобрения), VIP-демо (выдуманный успех — снято).

## 6. Где может лагать UX на ПК (проверить измерением)

Список — в `docs/owner/BOSSMAN_SIMPLE_STATE_20261008.md`. Исправления PR #99 уменьшают блокировки, но замер на Windows ещё не сделан:
сверить «до/после» на холодной странице (это же закрывает блокер «UX +30%»).

## 7. Что вернуть владельцу после каждой волны

Короткий отчёт: какие листья внесены (PR, SHA), уровень по каждому (CODE/TEST/CI/OWNER_HW раздельно), что не удалось и почему,
что осталось на ПК, прогресс по лестнице North Star, подтверждение контракта «один Bossman». Не писать «готово» без доказательства.
