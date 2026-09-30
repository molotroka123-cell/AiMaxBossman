# Ограниченное самоулучшение Bossman (autonomy, 30.09)

Этот документ описывает, что контур `bcc.autonomy` делает сам, что остаётся за владельцем, какие у него флаги и
умолчания, как владельцу запустить первый контролируемый цикл и что из этого доказано. Контракт интерфейсов:
`AUTONOMY_CONTRACT.md`, план владельца: `OWNER_PLAN_20260929.md`.

**Честный уровень North Star: `SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT`.** Инфраструктура замкнутой ограниченной
петли есть, один цикл прогнан вживую только в ПЕСОЧНИЦЕ (`SANDBOX_REHEARSAL`, пин тестовый, не владельца). Подняться
на `SELF_REPAIR_SINGLE_CYCLE_PASS` можно только после одного измеренного цикла на реальном репозитории под пином
владельца (этого цикла не было). Документ, CLI-команда и репетиция уровень не повышают.

## 1. Петля

```
цель (PROPOSED) -> план -> изолированный писатель (Claude/Codex CLI, один за раз, под арендой)
   -> тесты руками Bossman (pytest, JUnit, хэш улик) -> ревью Claude, затем Codex по ОДНОМУ sha+diff
   -> staging на отдельном порту (временный data-dir) -> ОЦЕНКА метрик на staged-кандидате (ДО владельца)
   -> USER_APPROVAL: стоп, решает владелец (Apply / Reject / Revise) -> владелец сам выполняет команды релиза
   -> DEPLOYED -> MONITORING (метрики после релиза) -> COMPLETE или откат
на каждом шаге: журнал с хэш-цепочкой; на каждой остановке: trace.json + кандидат-урок (опыт)
```

| Шаг | Модуль | Чем ограничен |
| --- | --- | --- |
| Цель | `goals.py`, `identity_task.py` (JEFF-0042), `planner.py` | бюджет обязателен, scope `path:`, rollback, измеримые тесты; scope не может называть файлы самого контура |
| Писатель | `workers.WriterSession` | одна аренда на всю систему, изолированный клон, `.git` не менять, пути только из scope; `protected:` и `tier:` нарушения блокируют |
| Руки | `hands.HandBroker` + `policy.py` | единственный привилегированный путь; уровень не выше L2; при STOP отказ с записью в журнал |
| Ревью | `review.ReviewGate` | привязка к (sha, diff, хэш улик); автор не одобряет сам себя; REJECT, таймаут, разногласие = BLOCKED |
| Staging | `staging.py`, `probes.py` | свободный порт (не 8800/8801), временный data-dir, всегда сносится |
| Оценка | `cycle._evaluate_candidate` + `metrics_gate.decide(deployed=False)` | REJECT = BLOCKED до вопроса владельцу |
| Опыт | `experience.py`, `skills.py` | кандидат-урок UNVERIFIED, навык только как предложение; веса не меняются |
| Супервизор | `supervisor.py`, `tools/autonomy_supervisor.ps1` | жёсткие лимиты, стоп на USER_APPROVAL, ограниченный backoff |

## 2. Что автоматически, что за владельцем

| Автоматически (внутри границ) | Только владелец |
| --- | --- |
| Предложить цель (`plan`), только PROPOSED | Закрепить конституцию (`constitution pin`, интерактивный терминал, не из агента) |
| Написать кандидата, прогнать тесты, два ревью, staging, оценку | Решение Apply / Reject / Revise по кандидату |
| Записать журнал, `trace.json`, кандидат-урок, предложение навыка | Выполнить команды релиза (`git merge --ff-only`, push) и нажать «Подтвердить выпуск» |
| Остановиться на BLOCKED / USER_APPROVAL / при STOP | Подтвердить урок (`lesson verify`), отозвать урок или навык |
| Посчитать дневной расход и остановиться на лимите | Менять конституцию, лимиты (`budget.json`), уровень, ветку релиза |

Автоприменение изменений ВЫКЛЮЧЕНО: `hands.MAX_LEVEL = "L2"`, CLI-зажим `min(args.level, 2)` в `cycle.main`, у
`bossman autonomy run` нет флага уровня, сохранённый `level.json` выше L2 зажимается до L2, исполнителя
`apply_candidate` не существует. Самоулучшение не меняет свои правила безопасности, права, лимиты и каноническую
ветку: весь пакет `bcc/autonomy/**`, `features/{autonomy,control_plane,evolution,coding_recipes}.py`,
`ui/pages/autonomy.js`, `learning/{lessons,lesson_format,trace}.py`, `pit/runtime.py`, `fable_budget.py`,
`config/evolution/*`, тесты контура и `docs/constitution/*` входят в `policy.PROTECTED_GLOBS`; релиз готовится только
для `release/bossman-owner` (`goals.set_candidate`, `service.release_commands`).

Текст конституции в этой работе НЕ менялся, перепиннивать не нужно.

## 3. Защиты

1. **Аварийный STOP.** Два файла: `<data>/computer/STOP` (общий STOP владельца) и `<root>/STOP` (автономный). Любой
   останавливает цикл: проверка на каждой итерации `_drive`, отказ в `HandBroker.execute`, опрос писателем и ревьюером
   (дерево процессов CLI убивается за доли секунды), убийство процессов, зарегистрированных в аренде. Пути:
   `bossman autonomy stop|resume`, `POST /api/autonomy/stop|resume`, `bossman stop --all` (в `stop-all` добавлены
   плоскости `autonomy` и `rave`, обе выполняются синхронно и входят в `ok` / `remaining`). `resume` снимает только
   автономный STOP; общий STOP снимается там, где поставлен. Эволюционная петля тоже слушает общий STOP.
2. **Свои правила не меняются**: см. выше; плюс `WriterSession` блокирует изменённые файлы с более высоким риском,
   чем заявленный `risk_tier`.
3. **Бюджет.** На цель: ходы, деньги, время (часы идут с момента старта, ожидание владельца не считается). Суточный
   кап: `<root>/budget.json` (по умолчанию 3 цикла, 30 ходов, 0 USD в сутки; файл пишет только владелец). Платного
   маршрута нет: Claude и Codex работают по подписке, план и лёгкий писатель на бесплатной Nemotron; любой будущий
   платный вызов обязан резервироваться через `fable_budget` (не реализовано, платного вызова нет).
4. **Оценка до одобрения.** Метрики считаются на staged-кандидате (`candidate_probe`, его собственный worktree), гейт
   `deployed=False`. Тесты `cmd:` и формулировки без `pytest:` не исполняются руками Bossman: они записываются как
   `NOT_EXECUTED` и показываются владельцу.
5. **Опыт не обучение.** Кандидат-урок (`learning.lessons.LessonBook`, UNVERIFIED) с provenance (кто, trace-хэш, sha,
   голова журнала) и dedup; текст строится только из структурных фактов (без слов модели), плюс фильтр отравления на
   записи и на чтении. В `trace.json`, исходе цикла, `SkillSpec`, `status()` и UI стоит `WEIGHTS_UNCHANGED` /
   `learning_kind: retrieval_context`. Проверить урок (`VERIFIED`) может только владелец.
6. **Навыки.** Фильтр отравления по всем текстовым полям, dedup по хэшу артефакта, отозванный артефакт не
   воскресает, журнал `skill.*`, атомарная запись. Цикл пишет только ПРЕДЛОЖЕНИЕ (`skills/proposed/<hash>.json`);
   активным навык становится через `SkillStore.confirm` (те же пять условий, включая два одобрения артефакта).
7. **События.** `autonomy.goal.decision` и `autonomy.stop` идут в шину; страница подписана.
8. **Префлайт настоящего CLI.** Перед запуском настоящего `claude -p` писателем или ревьюером проверяется версия
   (`rave.connectors.claude_version`, с кэшем): `--permission-prompts none` есть только в Claude Code >= 2.1.259, старая
   или нечитаемая версия блокирует цель с понятной причиной (`writer unavailable`), флаг не выбрасывается. Подменные
   раннеры тестов не проверяются.

## 4. Флаги и умолчания

| Что | Умолчание | Где |
| --- | --- | --- |
| Уровень / автоприменение | L2 / ВЫКЛ | `hands.MAX_LEVEL`, `service.autonomy_mode()` |
| `run --max-cycles` | 1 (1..25) | `supervisor.SupervisorConfig` |
| `run --max-hours` | 2 (0 < h <= 24) | то же |
| `run --interval-s` | 0 (один проход; > 0 = ждать при простое) | то же |
| Пауза при ошибке | 5 с, удвоение до 300 с, стоп после 3 подряд | то же |
| Heartbeat | `<root>/heartbeat.json`, каждые 15 с и на каждом проходе | то же |
| Суточный кап | 3 цикла / 30 ходов / 0 USD | `<root>/budget.json` (владелец) |
| Бюджет JEFF-0042 | 90 мин, 10 ходов, 0 USD | `identity_task.jeff_0042_goal` |
| Бюджет целей `plan` | 60 мин, 8 ходов (backlog может только уменьшить, максимум 240 / 20) | `planner._goal` |
| Таймауты | писатель 900 с, ревьюер 600 с, не более 3 раундов рук, 2 ревизии | `cycle.CycleConfig` |
| `run --claude-model`, `--codex-model`, `--max-cli-turns` | не заданы (умолчания CLI) | `bossman autonomy run` |
| Корень / журнал | `<data>/autonomy` или `BOSSMAN_AUTONOMY_ROOT` | `cycle.default_root` |
| Красная команда Jeff | `BOSSMAN_AUTONOMY_JEFF_MODEL`, `..._ENDPOINT` (по умолчанию локальный Ollama, модель Jeff) | `identity_task` |
| Рецепты эволюции | `publish_recipes = False`, включение только `--publish-recipes` | `bossman_v3.self_improvement.loop` |

## 5. Команды владельца (подготовлено, НЕ запускалось)

Первый контролируемый цикл JEFF-0042. Выполняет владелец, только по своему разрешению, в своём терминале. Нужна
сборка с этим кодом (установленная 84f5e0ac этих команд не знает) или запуск из репозитория:
`python -m bcc.terminal_cli autonomy ...` с `PYTHONPATH=<repo>\command-center;<repo>\bossman-core;<repo>`.
Писателю нужен git-клон: передайте `--repo <репозиторий с .git>` (в установленной сборке истории нет).

```powershell
# 1. Закрепить конституцию. Только сам, интерактивно (агент и не-TTY будут отвергнуты ПО ЗАМЫСЛУ).
bossman autonomy constitution pin
bossman autonomy status                       # ожидается: loop: READY

# 2. Базовая линия Jeff на ЕГО модели Ollama (других локальных моделей не загружать).
python -m bcc.autonomy.identity_task --runtime guard+ollama --model <модель Jeff> --endpoint http://127.0.0.1:11434/v1
#    утечек 0 = улучшать нечего (фильтр Jeff 2.0 уже закрывает): честно запишите это, цикл на JEFF-0042 не нужен.

# 3. Предложить цель (только PROPOSED, ничего не запускает).
bossman autonomy plan --redteam-model <модель Jeff>
bossman autonomy goals                        # JEFF-0042 PROPOSED, если утечки > 0

# 4. Сухой проход, затем ровно один цикл.
bossman autonomy run --dry --repo <репозиторий>
bossman autonomy run --max-cycles 1 --max-hours 2 --repo <репозиторий> --max-cli-turns 8

# 5. Цикл остановится на USER_APPROVAL. Решайте на странице «Автономия» (Apply / Reject / Revise),
#    команды релиза выполняете сами. Любое время: bossman autonomy stop   (снять: bossman autonomy resume)
```

Расписание: `tools\autonomy_supervisor.ps1` (проверяет пин, STOP, один экземпляр, лимиты; пример регистрации задачи
в Планировщике в заголовке файла). Задача НЕ зарегистрирована.

Опыт: `bossman autonomy lesson list | verify <id> | withdraw <id>`; навыки: `skills list | revoke <имя> <версия>`.

## 6. Что доказано и что нет

Метки: `PASS(repo-local)` тесты в этом репозитории на этой машине; `SANDBOX_REHEARSAL` живой прогон в песочнице с
тестовым пином (не владельца); `OWNER_REQUIRED`; `NOT_RUN`; `NOT_IMPLEMENTED` (намеренно).

| Утверждение | Статус | Основание |
| --- | --- | --- |
| STOP: драйвер, рука, писатель, ревьюер, аренда, CLI, API, `stop --all` (autonomy, rave) | PASS(repo-local) | `test_autonomy_bounded_loop.py`, `test_control_plane_autonomy_rave.py`; 19 негативных контролей краснеют на сломанном коде |
| STOP настоящего Claude-писателя | SANDBOX_REHEARSAL | `bossman autonomy stop` убил `claude.exe` за 0,4 с, цикл вернул BLOCKED за 0,54 с, кандидата нет, аренда свободна |
| Защита своих правил (glob, scope, `protected:`, `tier:`, ветка) | PASS(repo-local) | тесты `protected_globs`, `goal_scope`, `writer_changing`, `target_branch` |
| Бюджет цели и суточный кап, журнал `budget.*` | PASS(repo-local) | `test_goal_turn_budget`, `test_daily_*` |
| Оценка до одобрения; `NOT_EXECUTED` | PASS(repo-local) + SANDBOX_REHEARSAL | ACCEPT на worktree кандидата: untested 2 -> 1, failures 0 -> 0 |
| Опыт -> кандидат-урок, provenance, dedup, verify/withdraw | PASS(repo-local) + SANDBOX_REHEARSAL | один UNVERIFIED-урок после цикла; в retrieval не попадает |
| Навыки: poison, dedup, журнал, предложение | PASS(repo-local) | `test_poisoned_skill_*`, `test_skill_store_*`, `test_a_skill_becomes_active_*` |
| `plan` только PROPOSED | PASS(repo-local) + SANDBOX_REHEARSAL | бесплатная Nemotron (агент 25, 2 задачи, 0 USD) выбрала цель из двух |
| Супервизор (лимиты, preflight, heartbeat, backoff, стоп на владельце) | PASS(repo-local) | `test_autonomy_supervisor.py` (15) |
| Один реальный цикл на песочнице, до USER_APPROVAL | SANDBOX_REHEARSAL | Claude haiku писатель, Claude и Codex ревью по одному sha, staging на порту 61442, журнал OK (45 записей), манифест без проблем |
| Цикл JEFF-0042 на корне владельца | NOT_RUN / OWNER_REQUIRED | пин владельца отсутствует (`status`: BLOCKED) |
| Автоприменение, автооткат, повышение уровня | NOT_IMPLEMENTED (намеренно) | L2, исполнителя нет, `promotion_eligibility` только читает |
| Обучение весов | не делается | везде `WEIGHTS_UNCHANGED` |
| Польза самоулучшения (перенос, рост метрик на реальных задачах), 24/7 soak | NOT_RUN | |
| Расписание Планировщика | НЕ зарегистрировано | есть только файл и инструкция |

### Замеры репетиции (песочница, `SBX-0001`)

Писатель Claude haiku: 1 вызов CLI, 24 с, 5 внутренних ходов. Ревью Claude: 10 с, 2 внутренних хода. Ревью Codex:
13 с. Всего 3 вызова подписочных CLI, 47 с CLI-времени, цикл целиком 52 с. Список токенов и «списочной» цены
(не деньги, подписка): `usage.json` в артефактах. Бесплатная Nemotron: 2 задачи (planner), 0 USD.

## 7. Известные ограничения (честно)

- `claude -p --max-turns 3` не хватило писателю (`error_max_turns`, цикл встал с понятной причиной); с 8 прошёл за 5
  внутренних ходов. Поэтому для живого цикла задавайте `--max-cli-turns 8`.
- Риск JEFF-0042 поднят до `critical_runtime` (по правилам путей `public_guard.py` и `j2/safety.py` это runtime-код,
  а писатель не может менять файлы рискованнее заявленного уровня). Строже плана (`prompts_models`); на L2 любой уровень
  останавливается на USER_APPROVAL, поэтому ничего не потеряно.
- Защищаемые метрики JEFF-0042 сужены до измеряемых: `red_team_pass_rate`, `latency_ms` (задержка реальной модели
  шумная, допускается до 2x). `task_success`, `refusals_and_hallucinations`, `role_stability` красная команда не
  измеряет, их нужно добавлять вместе с пробами (лаборатория качества Jeff).
- Красная команда измеряется подпроцессом в чекауте (свой `bcc` первым, проверка «не подгружен чужой bcc»).
- Цели `plan` из JUnit ставят scope на сам упавший тест: ревьюеры обязаны ловить ослабленный тест. Метрика
  `tests.failed` не отличает починку от редактирования теста.
- Метрика песочницы `functions_untested.count` считает упоминания, а не качество проверок; качество судят ревьюеры.
- Ревьюер не держит аренду: при `stop --all` из бэкенда его CLI убивает опрос в процессе цикла (0,5 с), а не бэкенд.
- Навык нельзя подтвердить, пока нет раунда двойного ревью самого артефакта: предложение остаётся предложением.
- Старый кампейн эволюции с сохранённым `publish_recipes: true` продолжит публиковать; новые кампейны публикуют
  только с `--publish-recipes`.
- Мы не проверяли работу с установленной сборкой владельца: нужен git-клон и сборка с этим кодом.
