# План переписывания истории git — НЕ ВЫПОЛНЯТЬ АВТОМАТИЧЕСКИ

Этот документ — инструкция для владельца. Ни один шаг ниже не выполнялся и не
будет выполнен без явного решения человека.

## 1. Зачем это может понадобиться

`git rm` в `docs/REPOSITORY_ARTIFACT_CLEANUP.md` убрал 25 МБ из **рабочего
дерева**, но исторические blob'ы остались. Клон по-прежнему тянет всю историю.
Убрать вес по-настоящему может только переписывание истории.

**Сначала честно взвесьте, стоит ли.** Репозиторий сейчас ~40 МБ. Это не та
величина, ради которой стоит ломать всем чекауты. Переписывание оправдано, если:
репозиторий вырастет до сотен мегабайт, либо в историю попал настоящий секрет.
Ради 25 МБ архивов — скорее нет.

## 2. Что сломается — прочитайте до начала

Переписывание меняет **каждый SHA после первого затронутого коммита**.

| Последствие | Что именно |
|---|---|
| Все ветки | `claude/bossman-control-v03-43igbk`, `stable/v2.2-phase-closed` и любые другие получат новые SHA |
| Все теги | придётся пересоздать; старые указывают на исчезнувшие коммиты |
| Открытые PR | сравнение сломается, PR придётся пересоздать |
| Чужие клоны | у всех, кто клонировал, при `git pull` будет расхождение. Правильный путь для них — свежий клон, а не merge |
| **Ссылки в документах** | `docs/*` ссылаются на SHA: `b0bac7c`, `e520c68`, `c7f26e6` и другие. После переписывания **все они станут недействительными** и документы придётся править |
| Прогоны CI | привязаны к старым SHA; история прогонов не сойдётся с историей коммитов |
| `stable/v2.2-phase-closed` | это точка релиза; её SHA изменится, то есть «неизменяемая точка» перестанет быть той же |

Последние два пункта — главный аргумент **против** в нашем случае: вся
доказательная база V2.2 (прогон Actions #7 на `b0bac7c`, 323 passed) привязана
к текущим SHA. Переписав историю, вы обесцените ссылки, ради честности которых
эта фаза и делалась.

## 3. Точная процедура, если решение всё же принято

### 3.1 Резервная копия — обязательно, до всего остального

```bash
cd /path/to/parent
git clone --mirror https://github.com/molotroka123-cell/AiMaxBossman.git \
    AiMaxBossman-backup-$(date +%Y%m%d).git
tar -czf AiMaxBossman-backup-$(date +%Y%m%d).tar.gz \
    AiMaxBossman-backup-$(date +%Y%m%d).git
# Проверить, что копия читается:
git -C AiMaxBossman-backup-$(date +%Y%m%d).git log --oneline -5
git -C AiMaxBossman-backup-$(date +%Y%m%d).git rev-list --all --count
```

Запишите вывод последней команды: после переписывания сверите, что потеряны
ровно blob'ы архивов, а не коммиты.

### 3.2 Установка инструмента

`git filter-branch` использовать **не надо** — он медленный и его сам git
отговаривает.

```bash
pip install git-filter-repo    # либо: brew install git-filter-repo
git filter-repo --version
```

### 3.3 Работа на СВЕЖЕМ клоне

`git-filter-repo` отказывается работать в клоне с настроенным `origin` —
это защита, а не помеха. Клонируйте отдельно:

```bash
git clone https://github.com/molotroka123-cell/AiMaxBossman.git AiMaxBossman-rewrite
cd AiMaxBossman-rewrite
```

### 3.4 Что именно вырезать

```bash
cat > /tmp/paths-to-drop.txt <<'PATHS'
BOSSMAN_CLAUDE_V2_ALL_IN_ONE.zip
BOSSMAN_CLAUDE_V2_ALL_IN_ONE_PLUS.zip
BOSSMAN_IMAGES_EXACT_GITHUB_PATCH.zip
BOSSMAN_SKILLS_V1_OPENROUTER_MCP_TERMINAL.zip
BOSSMAN_MEMORY_RAG_OBSIDIAN_ADDON.zip
BOSSMAN_SELF_LEARNING_SKILLS_CORE.zip
BOSSMAN_CURRENT_PHASE_COMPLETION_PACK.zip
BOSSMAN_BROWSER_CONTROL_HANDOFF.zip
AI_WEBCAM_VISION_TAPO_C200_APP1.zip
AI_3D_MAKER_NEPTUNE3PLUS_APP2.zip
PATHS

git filter-repo --invert-paths --paths-from-file /tmp/paths-to-drop.txt
```

Скриншоты в список **не входят**: они не удалялись, а переехали в
`docs/reference-images/owner-screenshots/` и остаются нужны.

### 3.5 Проверка ДО отправки

```bash
# 1. Архивов в истории больше нет
git log --all --oneline -- '*.zip' | head      # должно быть пусто

# 2. Коммиты на месте (сверьте с числом из §3.1; уменьшиться не должно)
git rev-list --all --count

# 3. Дерево цело: код и документы читаются
git ls-files | wc -l
ls command-center/bcc/engine.py docs/V2_2_CURRENT_PHASE_FINAL_REPORT.md

# 4. Тесты проходят на переписанной истории
cd command-center && timeout 900 python -u -m pytest -q > /tmp/rewrite.log 2>&1
tail -3 /tmp/rewrite.log     # ожидается тот же счёт, что до переписывания

# 5. Размер действительно упал
du -sh .git
```

Если пункт 2 показал меньше коммитов — **остановитесь и разбирайтесь**. Значит
вырезано лишнее.

### 3.6 Отправка

```bash
git remote add origin https://github.com/molotroka123-cell/AiMaxBossman.git
git push --force --all origin
git push --force --tags origin
```

`--force` здесь неизбежен по природе операции. Именно поэтому §3.1 не
опциональна.

### 3.7 После отправки — обязательные шаги

1. **Снять защиту** с `stable/v2.2-phase-closed`, отправить, вернуть защиту
   (см. `BRANCH_PROTECTION_RECOMMENDATION.md`).
2. **Пересоздать теги** на новых SHA.
3. **Переписать ссылки на SHA в документах**: `README.md`,
   `docs/V2_2_CURRENT_PHASE_FINAL_REPORT.md`, `docs/NIGHT_HANDOFF.md`,
   `docs/CORE_SKILLS_AUDIT.md`, `docs/research/_MASTER_PLAN.md`,
   `docs/REPOSITORY_ARTIFACT_CLEANUP.md`. Найти всё разом:
   `grep -rnE "\b[0-9a-f]{7,40}\b" README.md docs/*.md docs/research/*.md`
4. **Предупредить всех, у кого есть клон**: не `git pull`, а свежий клон.
5. Сообщить GitHub Support о необходимости сборки мусора, если старые объекты
   продолжают отдаваться по прямым ссылкам.

## 4. Рекомендация

**Не переписывать сейчас.** Вес репозитория не критичен, секретов в истории не
обнаружено (сканер проходит), а вся доказательная база фазы V2.2 держится на
текущих SHA. Уборки рабочего дерева достаточно: новые клоны получают чистый
корень, новые архивы туда не вернутся благодаря `.gitignore`.

Вернуться к этому плану стоит, если репозиторий перевалит за несколько сотен
мегабайт или если в историю попадёт настоящий секрет — во втором случае
переписывание становится обязательным, и ротация самого секрета важнее.
