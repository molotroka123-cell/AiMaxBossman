# Terminal Run 1.2 — что брать из open source

Проверка источников: 2026-09-23. Это shortlist для интегратора, не отчёт об установке или полном security-аудите. Выбирать точные совместимые версии и фиксировать package hashes в существующем lock/build manifest перед включением в ZIP.

## Основной стек: максимум две новые UI-зависимости

### 1. prompt-toolkit/python-prompt-toolkit

Repo: https://github.com/prompt-toolkit/python-prompt-toolkit
Docs: https://python-prompt-toolkit.readthedocs.io/en/stable/
Source: README.rst; LICENSE. При просмотре blob README = `686fa254c941d53a8cc67011313966406c2ee080`, LICENSE = `e1720e0fb70684043842e94ede48622e6bffc62d` (это blob hashes, НЕ commit для checkout).

Лицензия: BSD-3-Clause. Описаны Windows, Unicode, completion, история, многострочный ввод, bracketed paste и async-подходы. Существуют fallback-пути для Windows-консоли без современных VT-возможностей.

Взять: редактирование ввода, autocomplete slash-команд и путей, историю, безопасную вставку, resize, async prompt. Предпочитать простой PromptSession, не строить полноэкранную ОС в терминале.

Проверить на нашем bundle: cmd/ConHost и Windows Terminal, Cyrillic, EOF/redirected stdin, copy/paste, Ctrl+C, потоковые события во время набора, восстановление режима консоли после exception. История ввода — с фильтрацией секретов и возможностью отключить сохранение. Не предполагать, что поддержка библиотеки означает PASS нашего приложения.

### 2. Textualize/rich

Repo: https://github.com/Textualize/rich
Docs: https://rich.readthedocs.io/en/stable/introduction.html
Source: README.md; LICENSE. Просмотренные blobs: README `a8475817e1d00628656acd0d7eab72df16f61371`; LICENSE `4415505566f261c802b671426be529a31f914137`.

Лицензия: MIT. Поддерживаются форматированный текст, Markdown, таблицы, подсветка кода/traceback и Windows. Старый console host и новый Windows Terminal имеют разные возможности цвета/символов.

Взять: компактный diff, таблицы моделей/skills/tools, статусы, readable errors и итог задачи. Не доверять markup из вывода модели и файлов: экранировать/отключать markup и управляющие последовательности. В JSONL не допускать ни одного ANSI-байта.

Prompt Toolkit и Rich не должны одновременно владеть курсором и перерисовывать экран независимо. Сделать один output coordinator, ограничить частоту repaint, сохранять scrollback. При отсутствии цвета/TTY — обычный текст. Не добавлять Textual full-screen, Electron и webview в критический путь ради картинок.

### 3. openai/codex — открытый референс UX, НЕ замена ядра

Repo: https://github.com/openai/codex
Docs: https://developers.openai.com/codex/noninteractive/
Source: README.md (просмотренный blob `06b02fced7db972084bf0188cb03f60375b51c1c`). README указывает Apache-2.0; перед переносом конкретного кода проверить его LICENSE/NOTICE и зависимости.

Взять идеи: chat в терминале, выбор сессии, non-interactive exec, структурированные события, diff/review и понятное завершение. Зафиксировать конкретный source commit, если переносится код, сохранить notices.

Не делать fork Codex с переименованием, не уводить задачи в другой оркестратор, не связывать обязательный локальный Bossman с аккаунтом OpenAI. Не копировать branding и не называть Bossman равным по интеллекту только из-за сходства интерфейса. Это референс удобства, а backend остаётся Bossman.

## Что уже есть и переиспользуется

На просмотренном cloud-closure SHA `bb2ef0287716a97a5daa2175188cfa7c2a9ce580` entry point уже есть в `bossman-core/pyproject.toml`, CLI — `bossman-core/bossman/cli.py`, parser — argparse. Поэтому не мигрировать весь CLI на Typer/Click ради нового chat. Существующий httpx, сериализацию/валидацию и streaming transport использовать там, где они уже решают задачу.

Coding API/local_sidecar, evolution, общие хранилища, Windows process-tree cleanup, OpenCode bridge, model downloader и Telegram companion уже создаются в соседних ветках. Сначала сравнить текущие реализации и их tests. Не добавлять дублирующий SSH daemon, второй shell-agent, новый model manager или второй Telegram bot.

## Навыки из согласованного предыдущего этапа

- https://github.com/huggingface/skills — model discovery, memory estimate, evals; repo Apache-2.0, внешние jobs не означают разрешённый расход.
- https://github.com/obra/superpowers — debugging/TDD/verification/review; выбирать нужные навыки, не включать глобальные hooks и телеметрию вслепую.
- https://github.com/anthropics/skills — skill-creator и инженерные примеры; документные подкаталоги имеют отдельные source-available условия.

Использовать уже согласованные/проверенные revisions и реестр навыков Bossman. CLI только показывает и включает те же навыки через тот же registry. Не устанавливать плагины автоматически только в Claude и не считать это обучением локального Bossman.

## Supplier/packaging checklist

Для каждой реально включённой зависимости: exact version, upstream, SPDX/условия, lock/hash, транзитивные зависимости, Python/Windows support, wheel availability, notices, тест импорта из чистого bundle. Использовать штатный dependency scanner, если он уже есть. Не запускать curl|sh/irm|iex из README стороннего проекта.

Не менять pins при каждом `bossman` и не устанавливать зависимости в фоне. В offline smoke bundle не должен пытаться получить пакет из сети. Встроенные PowerShell/Python launcher не должны требовать отключения защиты ОС.

## Решение интегратора

Рекомендуется: существующий argparse + существующий Bossman API client + prompt_toolkit + Rich. Codex — сравнительный UX-референс. Другой стек допустим только если он уже присутствует в проекте и объективно уменьшает время/риск интеграции; решение и регрессии фиксируются. README review не даёт права маркировать весь чужой код «проверен, без вирусов».
