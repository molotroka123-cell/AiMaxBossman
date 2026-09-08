# Branch Protection — Инструкция включения

## Как активировать через GitHub UI

1. Перейди: https://github.com/molotroka123-cell/AiMaxBossman/settings/branches
2. Нажми **"Add branch ruleset"**
3. Настройки:
   - **Branch name pattern**: `main`
   - ✅ Require a pull request before merging
   - ✅ Require approvals: 1
   - ✅ Require review from Code Owners (CODEOWNERS)
   - ✅ Restrict who can push to matching branches → добавь `@molotroka123-cell`
   - ✅ Block force pushes
   - ✅ Require status checks to pass (выбери CI workflows)
4. Сохрани

## Важно

- Для приватных репо на free plan — Branch Rules работают ограниченно
- Для полной защиты нужен **GitHub Pro** (~$4/мес) или сделать репо публичным
- CODEOWNERS работает автоматически — уже активен после этого коммита

## Разрешённые pushers

- `@molotroka123-cell` — владелец
- GitHub Apps с явным доступом (Perplexity MCP, Claude Code)

## Статус защиты

- CODEOWNERS: ✅ активен (этот коммит)
- Branch ruleset: ⚠️ требует ручного включения через UI выше
- Secret scanning: включить в Settings → Security → Secret scanning
