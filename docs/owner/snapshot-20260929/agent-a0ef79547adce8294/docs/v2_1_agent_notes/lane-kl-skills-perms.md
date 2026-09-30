# Lane K/L — Skills → реальные инструменты, NL-компилятор прав

Ветка `claude/bossman-control-v03-43igbk`. Коммитов не делал (по правилам лейна).

## Изменённые/созданные файлы

| Файл | Что |
|---|---|
| `command-center/bcc/v2/skill_library.py` | +`SkillContract`, `skill_contract()`, `skill_version()`, `build_skill_prompt()`, `NO_TOOLS_SENTINEL` |
| `command-center/bcc/features/skills.py` | `/skills/{id}/run` через канонический рантайм; `skill_versions`; хук `after_run`; Skill Forge (`observe/propose/apply`) |
| `command-center/bcc/features/nl_permissions.py` | новый: компилятор прав RU/EN + `/permissions/compile|apply|{agent_id}` |
| `command-center/tests/test_v21_skills_tools.py` | новый, 14 тестов |
| `command-center/tests/test_v21_nl_permissions.py` | новый, 13 тестов |

Чужих файлов не трогал (`engine.py`, `api.py`, `db.py`, `tools.py`, `approvals.py` — только чтение).

## K — что реально работает

1. **Вход по схеме.** `_validate_input` расширен на number/integer/boolean/array/object
   (раньше проверялся только `string`); `bool` не считается числом.
2. **Агент проверяется**: 404 если нет, 409 если выключен или без модели.
3. **Инструменты — РОВНО объявленные.** `tasks.meta.allowed_tools = required_tools`
   скилла. Тест `test_skill_run_exposes_only_declared_tools` смотрит не на БД, а на
   схемы, реально ушедшие провайдеру (`ToolAdapter.seen_tools`): у агента выданы
   два инструмента, модель видит один.
4. **Скилл без инструментов не наследует инструменты агента.** `allowed_tools_for`
   трактует пустой список как «нет записи» и падает на `agents.tools`, поэтому в
   meta кладётся `NO_TOOLS_SENTINEL` (`skill.__no_tools__`) — имя, которого нет в
   реестре, `REGISTRY.resolve` даёт `[]`, провайдер получает `tools=None`.
   **Хук для Лида:** чище было бы различать в `bcc/tools.py::allowed_tools_for`
   «ключа нет» и «ключ есть, но пустой» — тогда sentinel не нужен. Файл ваш.
5. **Prompt** = процесс скилла + вход + список выданных инструментов + схема
   ожидаемого выхода + HTML-коммент `skill/version/fingerprint`.
6. **Исполнение** — обычный `svc.engine.enqueue`. Второго пути нет.
7. **Версия/отпечаток на задаче.** `/skills/{id}/run` заводит (идемпотентно по
   fingerprint) строки `skills` + `skill_versions` и проставляет
   `tasks.skill_version_id`; в `tasks.meta` — `skill`, `skill_fp`, `skill_version`,
   `skill_version_id`, `skill_input`, `skill_output_schema`, `skill_missing_permissions`,
   `skill_unknown_tools`. Результат дописывается хуком `after_run`
   (`skill_result`, `skill_status`, `skill_error`, `skill_finished_at`) + событие
   `skill.finished`.
   *Оговорка:* meta пишется на пол-такта позже смены статуса задачи (хук зовётся
   из `_finish` после апдейта). Потребителю ждать событие `skill.finished`, а не
   статус. В тесте это учтено (`run_until_recorded`).

### Skill Forge — правило против «скилла из каждого чата»

- предложение делается **только явным** `POST /skills/forge/propose`;
- сигнатура процесса (нормализованный текст → sha256[:24]) должна быть
  зафиксирована `POST /skills/forge/observe` **не менее 3 раз** (`FORGE_MIN_RUNS`);
- повтор предложения по той же сигнатуре — не чаще раза в 24 ч
  (`FORGE_COOLDOWN_HOURS`);
- предложение НИЧЕГО не пишет на диск, только возвращает черновик.

`POST /skills/forge/apply`: считает дельту прав/инструментов относительно текущей
версии скилла (для нового — относительно пустого набора). Сужение применяется
сразу. **Расширение** заводит строку `approvals` (`kind="skill_permissions"`,
preview — JSON с `added_tools`/`added_permissions`/`was_*`), файл НЕ пишется;
запись возможна только повторным вызовом с `approval_id`, и только если строка
`approved` (иначе 409). Состояние — в `settings_kv["skills.forge"]` (шифровано).

## L — компилятор прав

`compile_policy(text)` — детерминированный, RU+EN, без вызова модели.
Возвращает `{policy, tool_rules, unrecognised, clauses}`.

- `tool_rules` — ровно формат `bcc.tools.decide_effect`
  (`[{tool, resource, effect, reason}]`); правила отсортированы по возрастанию
  строгости, чтобы `deny` всегда оказывался последним совпавшим (последнее
  правило побеждает).
- Тест доказывает влияние на рантайм: с правилами `pytest` → auto,
  `npm install` → ask, `git push` → ask, `wallet` → deny; без правил те же
  команды идут auto.
- Разбор: клауза (`, . ; \n`) → эффект (deny → ask → auto, с приоритетным
  «без подтверждения/without asking» = auto) → объекты (таблица `SUBJECTS`:
  tests/installs/git_push/git_read/lint/build/docker/sudo/destructive/browser/
  wallets/secrets/payments) + пути (`D:/Projects`, `/home/x`, `~/p`, `./src`).
  Клауза без эффекта наследует эффект предыдущей; без объектов — `unrecognised`
  с причиной.
- **Компилятор не выдаёт бланкетных прав** (`agents.permissions[x]=true`) —
  только правила по ресурсам. «Может править D:/Projects» не превращается в
  `filesystem.write` на всю машину.
- `/permissions/apply`: политика **всегда** перекомпилируется на сервере из
  текста (присланным правилам доверия нет). Сужение — сразу. Расширение —
  превью + `approvals(kind="permissions")`, применяется только повторным вызовом
  с `approval_id`, если он `approved` **и** отпечаток `args_hash(agent_id+text)`
  совпадает с preview (подменить текст под чужое одобрение нельзя).

### Отличия от примера в ТЗ (осознанные)

Пример выхода в задании содержит `"git status*": "auto"` для фразы «run tests
automatically». Такое правило из этой фразы не следует, и я его не выдумываю:
`git status*` попадает в политику только от явного «git status/log/diff».
Тест сверяет пример по существенным пунктам (filesystem D:/Projects/**, pytest
auto, npm install ask, git push ask, wallet deny), а не побуквенно.

## Тесты

```
timeout 300 python -u -m pytest tests/test_v21_skills_tools.py \
    tests/test_v21_nl_permissions.py -q        → 27 passed
timeout 300 python -u -m pytest tests/test_v21_skills_tools.py \
    tests/test_v21_nl_permissions.py tests/test_feat_skills.py -q → 34 passed
```

Полный прогон (`timeout 900 python -u -m pytest -q -p no:randomly`):
**265 passed, 1 skipped, 2 failed за 125 с**. Оба падения — в чужих файлах и
не связаны с K/L:

- `tests/test_v21_e2e_mission.py::test_autonomous_mission_with_ten_plus_tool_calls`
- `tests/test_v21_failure_injection.py::test_governor_does_not_pause_a_run_making_real_tool_calls`

Оба про одно: миссия уходит в `paused`. `bcc/features/governor.py::_on_step`
считает «нет прогресса», сравнивая отпечатки последних assistant-сообщений
(`_step_fingerprint` берёт `content[:160]`), а у сообщения с `tool_calls`
content пустой → несколько tool-шагов подряд выглядят одинаковыми и Governor
душит нормальный run. Чинить в отпечаток класть имена/аргументы `tool_calls`.
Файл принадлежит другому лейну (он же правит его прямо сейчас) — не трогал.

## Найденное попутно (не чинил — чужие файлы)

- **Изоляция библиотеки скиллов в тестах.** `repo_root = ui_dir.parent.parent`, а
  в фикстуре `ui_dir = tmp_path/"no-ui"` → канонический корень
  `/tmp/pytest-of-*/.agents/skills` **общий для всех тестов прогона**: тесты
  пишут скиллы друг другу. Мои тесты подменяют `svc.skills` на каталог внутри
  `data_dir` (`isolate_skills`). Общий фикс — в `tests/conftest.py` (не мой файл).
- `default_skill_roots` включает `~/.claude/skills` и `~/.agents/skills`, поэтому
  discovery в тестах может видеть скиллы разработчика. На мои проверки не влияет.

## Хуки для Интеграционного лида

- `allowed_tools_for` и пустой список (см. K.4) — единственная просьба к `tools.py`.
- Новые события шины: `skill.run`, `skill.finished`, `skill.proposed`,
  `skill.updated`, `permissions.compiled`, `permissions.applied`.
- Новые виды approvals: `skill_permissions`, `permissions`.
- UI-страницы не делал (не было в задании лейна).
