# Аргон: встроить Agents в уже существующий Bossman

Прямое поручение владельца дополняет текущую работу. Документационный фундамент подготовлен Кодексом; ты остаёшься единственным интегратором продукта. Сначала прочитай [README пакета](README_RU.md), [source map](SOURCE_MAP_RU.md), [архитектуру](ARCHITECTURE_RU.md), [UX](UX_RU.md) и [приёмку](ACCEPTANCE_RU.md).

## Цель

Сохранить понравившиеся Tasks/Agents и сделать из них рабочее управление помощниками: локальная Gemma принимает цель, выбирает совместимого local/free-cloud/paid-cloud специалиста, создаёт ограниченное поручение, наблюдает его и независимо проверяет результат. Карточка открывает inspector/настройку/черновик агента, а не запускает платный запрос одним нажатием без preview.

Не заводи новый backend, отдельную память, очередь, registry или модельный fleet. Переиспользуй существующие API и workbench. Названия/цены моделей бери из действующего каталога с provenance; недоступные/неизмеренные данные показывай N/A.

## Первый законченный шаг

1. Сверь origin/main, integration HEAD, dirty, занятые процессы и runtime identity. Сохрани текущие workspace-kit изменения. Документация приехала отдельным коммитом: fetch и безопасный fast-forward при отсутствии конфликтов; не делать reset и не терять work-in-progress.
2. Перепроверь source findings по актуальному коду: WS STOP, idempotency CODE path, DAG dependencies и shared cloud budgets. Если уже исправлено, добавь evidence вместо дубликата.
3. Доведи один существующий typed delegation path от Gemma до local read-only child на synthetic fixture. Если его пока нет, добавь тонкий adapter к existing TaskEngine/coding worker с scoped child envelope и durable parent link.
4. Покажи один child/task/receipt в existing desktop и mobile интерфейсах. Проверь independent postcondition, повтор intent и canonical STOP. Ошибки/unknown outcome видны, не превращаются в зелёный статус.
5. Зафиксируй CP-AG-00..02: exact source/runtime SHA, tests, tool calls, task IDs, sanitized evidence и результат. Только затем расширяй UX, free/paid routing и обучение.

## Дальнейшая работа

Cloud choices — через общий policy/privacy/price/atomic budget reserve/settle. Для первой приёмки настоящие платные запросы не нужны; используйте fake transport. Никаких новых расходов из старого лимита на PDF. Настоящий cloud dispatch возможен только по действующему точному owner grant.

Координатор даёт bounded brief; независимые read-only задачи могут идти параллельно; общий продукт меняет один интегратор. STOP каскадный, depth/число детей/total budget ограничены. Возобновление после restart сначала восстанавливает состояние, а не повторяет внешние эффекты.

Урок [BOSSMAN_LESSON_RU.md](BOSSMAN_LESSON_RU.md) записать/использовать через существующую memory, не выдавая его за VERIFIED skill или fine-tune. Сначала зафиксировать baseline и hidden evaluator inputs, затем измерить применение и transfer. Public `eval_tasks.json` — smoke-only, не heldout.

Обновлять capability tree только через существующие evidence tools. Сохранить все 15 направлений, 39 требований, North Star/2x performance gates, Jeff hold, STOP и owner approvals. Подготовить узкие коммиты и push в текущую integration-ветку; main merge/release оставить действующему owner gate.

Финал: что стало работать, exact code/runtime SHA, PASS/FAIL/BLOCKED/NOT_RUN по A01..A23, что реально усвоено моделью, измеренное время/стоимость, PR и следующий один шаг. При отсутствии живого tool→child→verification→receipt писать «Agents foundation implemented/partial; live acceptance не доказана».
