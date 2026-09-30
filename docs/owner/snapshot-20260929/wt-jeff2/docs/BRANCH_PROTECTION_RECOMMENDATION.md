# Защита ветки `stable/v2.2-phase-closed` — рекомендация

## 1. Что есть сейчас

`stable/v2.2-phase-closed` → `e520c687` создана как точка релиза V2.2.
Внешний аудит зафиксировал: **она не защищена**. То есть прямо сейчас её можно
перезаписать force-push'ем или удалить одной командой, и «неизменяемая точка
отката» перестанет быть неизменяемой ровно в тот момент, когда понадобится.

В этом прогоне ветка **не менялась** — ни коммитов, ни force-push, ни удаления.

## 2. Почему это не косметика

Стабильная ветка — единственное, к чему можно вернуться, если следующая фаза
что-то сломает. Точка отката, которую можно случайно затереть, точкой отката не
является. Цена ошибки несимметрична: защита стоит двух минут, потеря — всей
доказательной базы фазы.

## 3. Рекомендуемая политика

Для `stable/v2.2-phase-closed`:

| Правило | Значение | Зачем |
|---|---|---|
| Allow force pushes | **выключено** | force-push — единственный способ незаметно подменить точку релиза |
| Allow deletions | **выключено** | удаление ветки безвозвратно теряет ссылку на состояние |
| Require a pull request before merging | **включено** | прямой пуш в релизную ветку не должен быть возможен |
| Require status checks to pass | **включено**, обязательные проверки: `pytest (py3.11)`, `pytest (py3.12)`, `секреты, JS, запрещённые файлы` | это ровно три job'а `Command Center CI`; красный набор не должен попадать в релизную точку |
| Require branches to be up to date | включено | иначе проверки пройдут на устаревшем базисе |
| Do not allow bypassing | **включено**, включая администраторов | иначе правило действует только на тех, кто и так не ошибается |

Для рабочей ветки `claude/bossman-control-v03-43igbk` **защиту ставить не
нужно**: это ветка активной разработки, и требование PR на каждый коммит
остановит работу. Достаточно того, что CI на ней прогоняется на каждый пуш.

## 4. Точные шаги (веб-интерфейс)

1. `https://github.com/molotroka123-cell/AiMaxBossman/settings/branches`
2. **Add branch ruleset** (современный путь) либо **Add classic branch
   protection rule**.
3. Target: `stable/v2.2-phase-closed` — точное имя, без шаблона, чтобы правило
   не задело рабочие ветки.
4. Отметить, согласно таблице §3:
   - Restrict deletions;
   - Block force pushes;
   - Require a pull request before merging;
   - Require status checks to pass → добавить три проверки по именам выше;
   - Require branches to be up to date before merging.
5. Enforcement status: **Active**.
6. Bypass list: **пустой**.
7. Save.

## 5. Через API (если удобнее)

```bash
gh api -X PUT repos/molotroka123-cell/AiMaxBossman/branches/stable%2Fv2.2-phase-closed/protection \
  --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["pytest (py3.11)", "pytest (py3.12)", "секреты, JS, запрещённые файлы"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {"required_approving_review_count": 1},
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
```

Имя ветки в URL кодируется: `stable/v2.2-phase-closed` → `stable%2Fv2.2-phase-closed`.

## 6. Почему я это не сделал сам

Настройка защиты веток — **административное изменение репозитория**. Правило
этого прогона: административные изменения молча не выполняются.

Отдельно: у учётных данных сессии этих прав, скорее всего, и нет — пуш
аннотированного тега уже был отклонён с `HTTP 403`, а `workflow_dispatch`
вернул `Resource not accessible by integration`. Токен сессии ограничен пушем в
рабочую ветку.

Проверить, применилось ли правило:

```bash
gh api repos/molotroka123-cell/AiMaxBossman/branches/stable%2Fv2.2-phase-closed/protection \
  --jq '{force: .allow_force_pushes.enabled, del: .allow_deletions.enabled,
         checks: .required_status_checks.contexts}'
```

Ожидается `force: false`, `del: false` и три проверки в списке.

## 7. Дальше

Каждая закрытая фаза получает свою `stable/vX.Y-phase-closed` с той же
защитой. Правило можно задать шаблоном `stable/**` — тогда следующая точка
релиза окажется защищённой сразу при создании, а не после того, как о ней
вспомнят.
