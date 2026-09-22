# Импортированные скиллы для локального ученика Bossman

Дата импорта: 2026-09-22 (облачная подготовка, CLOUD_PREPARE). На машине владельца ничего не запускалось и не проверялось.

## Что это

11 сторонних скиллов (методик в виде текста `SKILL.md`) лежат в
`command-center/bcc/skills_catalog/<источник>/<скилл>/`. Рядом с каждым лежит `provenance.json`
(откуда, какой коммит, sha256, лицензия, что исключено) и файл лицензии.

Они подключены к **существующей** библиотеке скиллов: поиск и разбор делает тот же
`SkillLibrary` (`bcc/v2/skill_library.py`), второго реестра нет. Сверху работает
`bcc/v2/skill_catalog.py`: происхождение, фильтр политики, подбор под задачу, отзыв.

Как ученик их получает: `skills_for_task(svc, instruction)` из `bcc/features/skills.py` подбирает
по тексту задачи 0–3 подходящих скилла и отдаёт только их текст, в пределах лимита
(не больше 4000 символов на скилл и 12000 на все вместе). Остальные скиллы в контекст не попадают.
Подключение к `context["skills"]` в `coding_tasks.py` делает координатор. Пока его нет,
скиллы до ученика **не доходят**.

## Правила безопасности

- **Статус при импорте — UNVERIFIED.** Такой скилл не даёт ни инструментов, ни прав
  (`grants` всегда пуст). Если в заголовке скилла объявлены `allowed-tools` или `permissions`,
  они записываются как проигнорированные.
- **Только текст.** Скрипты, хуки, примеры кода, справочные файлы и плагины upstream не
  импортируются и не запускаются. Телеметрии и автообновления нет. Чтобы обновить скилл, нужно
  заново вручную импортировать его с новым зафиксированным коммитом.
- **Перед текстом скилла ставится баннер.** В нём сказано, что это цитата-методика, а не разрешение:
  критерии успеха задачи она не меняет, тесты и проверки не отменяет.
- **Фильтр политики при загрузке:**
  - *удаляются строки*, где сказано отключить или пропустить тесты, пропустить проверку,
    использовать `--no-verify`, сделать push в main или force-push, подменить критерии успеха
    («считай готовым, даже если тесты падают», `pytest … || true`). Вместо строки ученик видит
    пометку «строка удалена политикой Bossman». Строки с отрицанием («Never skip the tests»),
    пункты под заголовками «Never:» и «Red Flags» и цитаты-отговорки в кавычках не удаляются:
    это методика *против* такого поведения.
  - *весь скилл попадает в карантин (QUARANTINED)*, если в нём есть `curl … | sh`, отправка
    секретов наружу, телеметрия, автообновление, «ignore previous instructions» или `rm -rf /`.
- **Проверка целостности.** Если байты `SKILL.md` не совпадают с sha256 из `provenance.json`
  (файл изменили после импорта) или `provenance.json` отсутствует, скилл уходит в QUARANTINED.
- **Совместимость инструментов.** У локального ученика есть только `list_dir, read_file, search,
  edit_file, write_file, run_tests, finish`. Если скилл предполагает shell, git, браузер,
  субагентов, инструменты Claude Code, сеть или HF CLI, в результате стоят `tool_compat: "flagged"`
  и список `unsupported_tools`, а в баннере написано «Недоступно в Bossman: …, такие шаги
  не выполнять». Молча такие шаги не используются.

## Статусы

| Статус | Значение | Попадает к ученику |
|---|---|---|
| UNVERIFIED | импортирован, владелец не проверял | да, как цитата с баннером |
| VERIFIED | зарезервирован для решения владельца, при импорте не ставится | да |
| QUARANTINED | нарушение политики, изменённый файл или нет provenance | **нет** |
| REVOKED | отозван владельцем (одна версия по sha256 или скилл целиком) | **нет** |

Сейчас все 11 скиллов имеют статус **UNVERIFIED**. Ни один не в карантине.

## Список

| Скилл | Репозиторий @ коммит | Лицензия | sha256 SKILL.md (начало) | Нет в Bossman |
|---|---|---|---|---|
| superpowers/systematic-debugging | obra/superpowers @ `5bf4e7801107` | MIT | `808fc5717aa88ad6` | git, shell, skill_tool |
| superpowers/test-driven-development | obra/superpowers @ `5bf4e7801107` | MIT | `64b03fce4aee5a97` | shell |
| superpowers/verification-before-completion | obra/superpowers @ `5bf4e7801107` | MIT | `2befe7fc55bcadaa` | — |
| superpowers/writing-plans | obra/superpowers @ `5bf4e7801107` | MIT | `0bc3d36590f7b2c3` | git, shell, skill_tool, subagents |
| superpowers/requesting-code-review | obra/superpowers @ `5bf4e7801107` | MIT | `cfcee1b06774e7c0` | git, shell, subagents |
| superpowers/receiving-code-review | obra/superpowers @ `5bf4e7801107` | MIT | `091df1629510af1b` | gh_cli |
| superpowers/using-git-worktrees | obra/superpowers @ `5bf4e7801107` | MIT | `8cfb86f121269e8f` | claude_code_tools, git, package_install, shell |
| anthropics/skill-creator | anthropics/skills @ `34040c9c5685` | Apache-2.0 | `dcd4803e61e913e6` | browser, claude_code_tools, shell, subagents |
| anthropics/webapp-testing | anthropics/skills @ `34040c9c5685` | Apache-2.0 | `51b7349e77ec63b7` | browser, shell |
| huggingface/hf-mem | huggingface/skills @ `abc20ae526d8` | Apache-2.0 | `ee99f9d97e084aa6` | hf_cli, package_install, shell |
| huggingface/huggingface-local-models | huggingface/skills @ `abc20ae526d8` | Apache-2.0 | `814640db1d5f2f27` | git, hf_cli, network, package_install, shell |

Полные коммиты:
- obra/superpowers `5bf4e78011075bcfc0dc295f0724994cd123ee71`;
- anthropics/skills `34040c9c568585f6929bedeaad110ad08f079624`;
- huggingface/skills `abc20ae526d8b4c0e4dff89f904adce28a4a0eb6`.

Полные sha256 записаны в `provenance.json`. Все `SKILL.md` скопированы без изменений. Фильтр
применяется при загрузке, файлы он не правит.

Лицензии сохранены: MIT лежит в `skills_catalog/superpowers/LICENSE`, Apache-2.0 — в
`skills_catalog/huggingface/LICENSE` и в `LICENSE.txt` рядом с каждым скиллом anthropics.

## Что исключено и почему

| Что | Почему |
|---|---|
| anthropics `docx`, `pdf`, `pptx`, `xlsx` | лицензия source-available, а не открытая; импорт запрещён |
| anthropics `mcp-builder` | в Bossman уже есть своя адаптация `.agents/skills/mcp-builder`; upstream требует WebFetch/сеть и скрипты оценки |
| obra/superpowers: хуки `hooks/*` (session-start и др.), плагины `.opencode/.pi/.hermes`, `index.js`, `package.json`, `scripts/bump-version.sh` | исполняемый код и автоматизация репозитория; к тому же в заметке исследования у superpowers отмечены хуки и телеметрия |
| superpowers `systematic-debugging/find-polluter.sh`, `condition-based-waiting-example.ts` | скрипты |
| superpowers: доп. тексты (`root-cause-tracing.md`, `defense-in-depth.md`, `writing-good-tests.md`, `code-reviewer.md`, `plan-document-reviewer-prompt.md`, тестовые сценарии давления и т. п.) | не импортированы, чтобы контекст оставался ограниченным; при необходимости — отдельный ревью и импорт |
| skill-creator: `scripts/*.py`, `eval-viewer/*`, `assets/*`, `agents/*.md`, `references/schemas.md` | скрипты и вспомогательные файлы; только текст методики |
| webapp-testing: `scripts/with_server.py`, `examples/*.py` | исполняемые примеры |
| huggingface-local-models: `references/*.md` | ограничение контекста |
| huggingface/skills: `.mcp.json`, `scripts/publish.sh`, `scripts/build_skill_distribution.py` | MCP-конфиг и скрипты публикации |
| остальные HF-скиллы (trainer, jobs, spaces, sagemaker, paper-publisher, datasets и др.) | требуют HF-токен, платные Jobs/облако (AWS/SageMaker) или публикацию; для локального ученика бесполезны или рискованны |
| остальные superpowers (brainstorming с локальным сервером, subagent-driven-development со скриптами, executing-plans со скриптами, using-superpowers, writing-skills, diagnosing-superpowers) | скрипты или сервер, зависимость от субагентов или специфика самого superpowers |

Полный список исключённых upstream-файлов каждого скилла лежит в его `provenance.json`
(`upstream_skill_files_excluded`, `upstream_repo_hooks_excluded`).

У `hf-mem` и `huggingface-local-models` помечены `hf_cli`, `package_install` и `shell`:
у ученика нет ни `uvx`, ни сети, поэтому эти скиллы работают только как справка.
HF-токен для них не нужен (он требуется лишь для закрытых моделей). Из облака huggingface.co
закрыт, так что сами команды там не проверялись.

## Подбор под задачу (примеры из тестов)

- «Fix the bug: … the test test_parse_tz fails» или «Исправь баг: … падает тест» →
  systematic-debugging + test-driven-development + verification-before-completion.
- «Сколько видеопамяти нужно, чтобы модель … GGUF влезла на GPU» → hf-mem (и huggingface-local-models).
- «Составь план миграции» → writing-plans.
- «Rename variable x to y» → ничего.

Слова-триггеры написаны в Bossman (`selection.triggers` в `provenance.json`), в upstream их нет.

## Управление (API, префикс `/api`)

- `GET /skill-catalog` — все скиллы со статусами, происхождением, нарушениями и совместимостью.
- `GET /skill-catalog/select?q=<текст задачи>` — что получил бы ученик.
- `GET /skill-catalog/<источник>/<скилл>` — один скилл: полный provenance и очищенный текст
  (у скилла в карантине текст не отдаётся).
- `POST /skill-catalog/<источник>/<скилл>/revoke` с `{"sha256": "...", "reason": "..."}` —
  отозвать версию. Без `sha256` отзывается скилл целиком.
- `POST /skill-catalog/<источник>/<скилл>/restore` — снять отзывы.

Отзывы хранятся в базе продукта (`settings_kv`, ключ `skills.catalog.revocations`,
зашифровано), поэтому переживают перезапуск. Если список отзывов не читается, ученик не получает
ни одного импортного скилла: отказ закрытым, отозванный скилл случайно не вернётся.

## Что проверено, а что нет

- Проверено в облаке тестами `command-center/tests/test_skill_catalog.py`:
  - происхождение и целостность;
  - отравленные скиллы уходят в карантин или вычищаются, чистый скилл проходит без изменений;
  - подбор и лимиты;
  - флаги несовместимых инструментов;
  - отзыв переживает перезапуск сервиса на тех же данных;
  - fail-closed при нечитаемом списке отзывов.
- **Не проверено:** помогают ли эти скиллы локальной модели решать задачи. Это покажет только
  прогон на машине владельца после подключения `context["skills"]`. До тех пор польза не доказана.
