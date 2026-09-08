---
name: parallel-agent-file-ownership
description: Применять, когда над ОДНИМ рабочим каталогом одновременно работают несколько агентов или лент задач — параллельные аудиты, несколько исправлений сразу, координатор с подагентами. Разграничить файлы ЗАРАНЕЕ, стадировать только свои пути явным списком, никогда не делать `git add -A` и `git add .`.
compatibility: BOSSMAN, OpenCode, Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
  category: coordination
---

# Parallel Agent File Ownership

Один checkout, десять агентов. Единственное, что делает это безопасным, —
непересекающиеся файлы, объявленные ДО начала работы.

## Правила

1. **Владение объявляется заранее.** У каждого агента свой список путей. Если два
   агента могут тронуть один файл — это не два агента, а один.
2. **Аудит не меняет код.** Полоса аудита пишет ТОЛЬКО свой отчёт. Правки делает
   тот, кому отчёт адресован. Так весь конфликт сводится к одному файлу на агента.
3. **Стадировать только явные пути:**
   `git add .claude/skills/parallel-agent-file-ownership/SKILL.md`.
   `git add -A` и `git add .` запрещены: они забирают чужую незаконченную работу.
4. **`git status` показывает чужие изменения — не трогать их.** Это не мусор, это
   чей-то незавершённый файл.
5. **Общие сгенерированные файлы** (сводный scorecard, индекс, `OPEN_FINDINGS.json`)
   принадлежат координатору, а не полосам. Иначе они переписывают друг друга.
6. **Перед push — `git pull --rebase`, затем push. Никогда `--force`.** Ветка
   двигается под тобой, пока ты пишешь.
7. **Прогон тестов только по своей выборке.** Полный прогон на чужих
   недописанных файлах даст падения, которые ты примешь за свои.

## Как это выглядело здесь

Десять полос аудита в этой ветке — по одному файлу на полосу, ни одной правки кода:

```
docs/testing/acceptance-run-20260906/audits/audit-01-operator-core.md
docs/testing/acceptance-run-20260906/audits/audit-02-auth-approvals.md
docs/testing/acceptance-run-20260906/audits/audit-03-adapters.md
...
docs/testing/acceptance-run-20260906/audits/audit-10-doctor-ci.md
```

Каждая полоса — свой коммит, ровно один изменённый файл (`06d3488`, `d4e6db5`,
`e5a6b39`, `1099478`, `90d57d7`, `4ec2535`, `683be83`, `bfa93e5`, `e3e6cf8`).
Коммит `ad1d02c` прямо назван «audit-10-doctor-ci — findings for main coder,
**no code changes**». Одиннадцатая полоса пришла отдельной веткой и была влита
merge-коммитом `437c26a`:
`docs/testing/acceptance-run-20260906/audits/audit-11-openrouter-deadend.md`
и два однотипных прогона `audit-11a-env-bootstrap-deadends.md`,
`audit-11b-list-caps-deadends.md` в том же каталоге.

Дальше правки делал координатор, каждая — по своей области и своим файлам:
`51a536a` (адаптеры оператора), `b19f4fd` (веб-дизайнер), `181643f` (OpenRouter).
Сводка полос — `docs/testing/acceptance-run-20260906/OPEN_FINDINGS.json` и
`docs/testing/acceptance-run-20260906/CHECKPOINT_2.md` — велась в одном месте
координатором, а не полосами.

## Проверка перед коммитом

```bash
git status --short          # смотри, что изменено чужими руками
git add <явные пути>        # только свои
git status --short          # убедись, что в индексе ТОЛЬКО твои файлы
git diff --cached --name-only
```

Если в `git diff --cached --name-only` есть путь вне твоего владения — `git restore
--staged <путь>`, а не коммит «заодно».
