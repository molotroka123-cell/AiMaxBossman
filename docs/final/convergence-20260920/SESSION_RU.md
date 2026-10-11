# Финальная ветка: конвергенция, Mimik/open-news и фактические исправления

Дата владельца: **20.09.2026**. Контрольная сессия UTC: **21.09.2026**.
Единственная целевая ветка: `claude/bossman-final-convergence-hu2702`.
Статус: **INTEGRATED_CANDIDATE_PENDING_CI**, не FROZEN и не полный V8 Total.

## История и границы слияния

Remote до изменений и последнего чтения: `6cb62d92978baaf16207199d973821cdf09e0ae1`.
Сведены найденные актуальные дневные линии:

- canonical PREP/open-news на `6cb62d92978baaf16207199d973821cdf09e0ae1`;
- `release/bossman-owner` на `ce095d556fd9e65d7b7c84a35fb14dd3d0a240f6`;
- Dashboard Next на `7db0db970f92a2fac185acfc7b4492be5b96a823`;
- image candidate на `43bc5e4bd2f291ea576f52eff301215567d428b8`.

Первое слияние — `e75493d31ad13724dfb3420aa72d2e55bdbf6f53`, родители canonical
и Dashboard Next, который уже содержит release. Второе —
`81e2da74cd5cc42ac8394639334f6662e3dc51d0`, дополнительный родитель image candidate.
Последующий коммит содержит tested skills/UI/build-context/README изменения и
эту запись. Его собственный SHA не вписывается в содержимое: он ещё не существует
до создания Git commit. Перед публикацией ref обязательно перечитывается.

Native compare release/canonical: **148 ahead / 10 behind**, общий предок
`d4381e6c6f1640e0992942a864ed4e75ad78b21a`. Dashboard Next добавляет один документ;
image branch содержит шесть собственных коммитов. Использованы merge-родители,
а не squash или переписывание истории. Старые ветки не удаляются. Проверен список
106 веток, но полный аудит дат каждого коммита всех исторических веток не заявлен.

Особые решения изложены в `MERGE_DECISIONS_RU.md`. PREP-03, open-news, тесты
отмены telemetry, исправленные таймер/PNG/finite checks, внешний installed runner
и весь журнал PREPARATION_MEMORY_RU.md сохранены. Старый README canonical сохранён
как `README_PRE_CONVERGENCE_6cb.md`, расширенный README release —
`README_GUIDE_20260918.md`; визуальные assets не удалены.

**Image candidate не активирован.** Все пять конечных файлов сохранены побайтно
под `integrations/openai-image-candidate/source/`, а шесть коммитов — в родительской
истории. Его прямой платный POST ещё не связан с canonical approval/tariff/egress;
есть несовместимый размер и неподходящая граница проверки размера ответа.
Существование gpt-image-2.5-flare/sunburst проверено по официальному источнику,
оно не является ошибкой. README честно помечает эту линию PRESERVED_NOT_ACTIVATED.
Не выдавать сохранение кандидата за работающий cloud provider.

## Mimik — отдельный исполняемый скилл

Upstream `westpoint-io/mimik`, exact commit
`905098ac005a7caad68949189a81e43ac8c327a1`, MIT, оригинальные reference files/pins
и лицензия сохранены. `.agents/skills/mimik/SKILL.md`, tool `mimik.guide`, readonly
`/api/mimik/status`, штатный ToolRegistry/model-tool loop; нет второго исполнителя.

Это **text_export_adapter**, не полная установка браузерного расширения. Он
принимает предоставленный Markdown export или internal Snapshot, сохраняет
порядок stepIds, различает heading/callout/action и готовит текстовый чек-лист.
Все действия остаются NOT_RUN; слово success внутри callout не становится PASS.
Скриншоты не декодируются, inputValue/selectors не передаются в результат,
URL ограничиваются origin, распознаваемые секреты минимизируются. Это не гарантия
полной анонимизации; вход надо очистить ДО передачи модели и выбрать локального
агента. Встроенный tool не может отменить уже отправленный облачный prompt.

Нет установки расширения, записи вкладок, Guide Me replay, voice/cloud AI,
публикации, автоклика, чтения личных файлов или фонового мониторинга. Полномочия
не расширены. Штатный пользовательский импорт — Markdown; Snapshot не означает,
что у upstream есть придуманная кнопка JSON export. См. `integrations/mimik/NOTICE.md`.

## Исправленные пользовательские и сборочные дефекты

1. **Форма Skills.** Реальный обработчик `openRunSkill` отправлял все значения
   строками и добавлял пустые необязательные поля. Массив новостей и число limit
   не доходили до tool корректными типами. Теперь JSON array/object, numbers и
   booleans типизированы, enum — select, Markdown — textarea, пустое optional
   не отправляется, conditional/oneOf проверяются до POST. При ошибке форма
   не закрывается. Именованные средства выполнения скиллов не подменяются shell.
2. **Релевантность RSS.** В search path не применялись локальные query/query_mode:
   посторонняя RSS-новость могла попасть в успешную подборку. Теперь работают
   реальные upstream any/all/exact_phrase проверки; отдельный NO_MATCHING_RESULTS.
   Перед исправлением четыре новых различающих проверки падали.
3. **Чистая тестовая сборка.** `_pristine_component` копировал apps/integrations/tools,
   но не новый `.agents` build input. Теперь `.agents` включён, а реальный wheel
   test дополнительно требует оба скилла и их LICENSE/NOTICE/UPSTREAM assets.
   Контрфактическая старая раскладка воспроизвела missing SKILL.md, новая проходит
   обе реальные функции копирования assets на ограниченном тестовом контексте.
4. **Явная тестовая зависимость.** `setuptools>=68` добавлен только в dev extra:
   новые asset tests импортируют его, build-system isolation не устанавливает его
   автоматически в интерпретатор pytest. Runtime зависимости этим не расширены.

## Что выполнено до push

Среда: Linux, Python 3.13.5, Node 22.16, **частичная рабочая копия**. Исходные
вспомогательные файлы и изменённые full files сверены по Git blob; канонический
ToolRegistry объединённой линии сохранил blob `973aafa5fc2882cd224ff4e5ac7675b96888a7c1`.

- Оба скилла: **131 passed / 0 failed / 0 skipped**.
- После трёх build-context контролов совместный прогон: **134 passed**, 1.04 s.
- JS helper + реальный modal/submission handler: **20 passed**, 99.19 ms последнего
  прогона. DOM/API в wiring-контролях — явные двойники, не живой браузер.
- Настоящая pinned TypeScript функция Mimik Markdown export выполнена с fixture
  и тестовым i18n/date; результат побайтно совпал с `export.fixture.md`. Это не
  live recording. Node stripTypeScriptTypes выдал штатное experimental warning.
- Синтаксис изменённых runtime/setup/test/JS файлов проверен.
- HTTP news — MockTransport, не живой новостной запрос; модели не вызывались.

Команды:

```bash
PYTHONPATH=command-center python -m pytest -q command-center/tests/test_skill_build_context_regression.py command-center/tests/test_open_news_skill.py command-center/tests/test_open_news_query_regression.py command-center/tests/test_mimik_skill.py
node --test command-center/ui/tests/skill_inputs.test.mjs command-center/ui/tests/skills_run_wiring.test.mjs
node tools/test_mimik_export_contract.mjs
```

Assets-copy test НЕ равен полному wheel build. Все 148 переносимых коммитов не
запускались здесь полным стеком. Новый общий CI на финальном SHA обязателен.
Пороги CI/coverage, подтверждения и пропуски не ослаблялись.

## Красный родительский CI — не скрывать

Command Center run `35548359286` на **ebdf227d** завершился FAIL в Python matrix,
при этом Windows paths и security lane успешны. По прочитанным logs py3.12:
три сборочных отказа относятся к описанным выше `.agents` и setuptools; исправления
проверены ограниченно, их повторный полный CI ещё нужен. Также есть отказ
диагностического снимка `testing_period`: `_safe_payload` обрезает список features
до 50. **Этот четвёртый дефект в данной записи остаётся OPEN, runtime-patch не
вносился без достаточного полного воспроизведения.** Не писать «все ошибки закрыты».
Зелёный Windows installed profile не отменяет красный общий Command Center CI.

## Последний фактически принятый Windows RC (предыдущий source, не этот merge)

Run `35548359170`, source=harness=`ebdf227d1f5c6348369372d94c07e204eb6f9954`:

```text
BOSSMAN-Windows-x64-ebdf227d1f5c.zip
783183197 bytes
SHA-256 e66b9ca284f0f248039a838c91510d8d6274f17d792a1914a29c2cda38417e9c
```

Actions container `10617033841` — 775295857 bytes, НЕ application ZIP:
https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/35548359170/artifacts/10617033841

Installed profile **46/46 без ошибок/пропусков**, UI **31 page PASS**. CIM увидел
Intel Xeon/Hyper-V/16 GiB и hardware=different. Live model tasks=[], Studio outputs=[],
egress=0. `ASTRA6-FREEZE.json` имеет status=OWNER_REQUIRED,
release_state=WINDOWS_RC_READY_OWNER_REQUIRED, publish_gate.release_ready=false;
intelligence NOT_VERIFIED_BY_THIS_MANIFEST. Это не физический Ryzen и не FROZEN.

Малые доказательства действительно скачаны и проверены:
proof artifact10617128257, 681960 bytes,
SHA256708c305940b3094ad42fbed8b867cbd2147cfaa82a684a9eb11a25ef9815200b;
freeze artifact10617854576,1290 bytes,
SHA256e723462ea24eb75d78e7c1382ee14bc9640d84199a4fc529c2103eaed8c69675.
Хэши results.xml,ui-sweep.json,live-model.json,studio.json совпали с freeze manifest.
Большой application здесь не скачан и не перехеширован: его bytes/hash взяты из
привязанной Windows acceptance, а не нового локального измерения.

Этот старый RC содержит PREP-03/open-news1.0, но не Mimik, новый typed UI/RSS-fix
и release-конвергенцию. Он не изменён и не помечен новым SHA. Для текущего source
нового принятого application ZIP/прямого Release ещё нет.

## Кратчайший безопасный путь к рабочей приёмке

Проверить общий CI нового объявленного candidate, закрыть воспроизводимый
диагностический отказ, собрать НОВЫЙ exact ZIP и прогнать его installed/UI профиль.
Затем владельческий OpenCode+GUI-driver calibration, real model→tool→result→restart,
Studio→bytes→editors, native cold/warm и длительный process/model soak, two-ZIP
backup/rollback, Ryzen/shared-memory pressure/reclaim и independent intelligence pair.
При отсутствии улики сохранять pending, не переносить старые PASS.

Главный owner GUI путь: `docs/v8/owner-final-run/OPENCODE_LOCAL_SETUP_RU.md`, затем
`OPENCODE_OWNER_RUN_RU.md`. Память подготовки до этой сессии сохранена побайтно;
эта запись — новый checkpoint, на неё ссылается актуальный README. Ни платных
вызовов, секретов, внешней отправки личных данных, реальных сделок, новых
автоматизаций, скрытой записи экрана или изменений принятого ZIP не было.
