# Единый кандидат владельческой приёмки — 2026-09-09 (PR60 + PR61)

Один код, одна регрессия, один SHA. Этот документ заменяет для приёмки два
предыдущих: `docs/final/OWNER_TEST_20260908_FILE_INTELLIGENCE.md` (PR60, код
`a1e633c`) и `docs/final_closure/FINAL_CLOSURE_REPORT.md` (PR61, код `cbb2991`).
Оба остаются как история; числа ниже сняты только на едином дереве.

## Кандидат

| | |
| --- | --- |
| **UNIFIED_FINAL_CODE_SHA** | `92e89e0140cd8682c2a0d9c9fcf35f0435506715` |
| Что это | merge-коммит: линия PR60 (`e5ccd54`, код `a1e633c`) + PR61 по `c7e75cc` (код `cbb2991`, цепочка `69df482 → 4001b7a → cbb2991 → c7e75cc`) |
| База обеих линий | `45027d3` (PR58 head) — merge-base проверен, не сдвигался |
| Ветка / PR | `claude/bossman-final-integration-pass-04nffa`, PR #60 |
| Выше `92e89e0` | только документация (этот файл и два указателя). Ни одного файла кода |
| Default branch | не вливался. PR59, Token Shunt, Visual V3 — не трогались |

Порядок сборки — как было предписано: взята текущая интеграционная линия
PR60 и в неё перенесено закрытие PR61 по `c7e75cc` целиком (`git merge --no-ff`).
Тестовые правки `c7e75cc` (восстановление `tempfile.tempdir` через
`monkeypatch`; закреплённый родитель песочницы в тесте контейнмента уборки)
перенесены как правки тестов, не как поведение. `4001b7a`
(`hashlib.sha1(usedforsecurity=False)` — идентичность git-объекта, не
криптография) и `cbb2991` (уборка read-only-родителей без root на POSIX и
read-only-записей на Windows — ограниченная, в пределах песочницы) — дословно.

### Единственное пересечение: `command-center/bcc/engine.py`

Git свёл файл без конфликта; вручную доказано, что выжили ОБЕ стороны:

* `diff(e5ccd54 → 92e89e0)` по этому файлу содержит только правки PR61:
  `_park_for_owner` (парковка на CAPTCHA/логин-стене вместо `completed`),
  ветка `target_status == "paused"` в финализации, `failure_status == "paused"`
  из `finalize_task`, `max_retries` в выборе ступени восстановления и правило
  «вмешательство владельца сильнее восстановления» (`stopped`/`cancelled` не
  перепостановятся, `paused` остаётся `paused`).
* `diff(c7e75cc → 92e89e0)` по этому файлу содержит только правки PR60:
  `from . import run_provenance` и `_capture_provenance(...)` сразу после
  `_start()`.
* Оба набора символов присутствуют в одном файле (строки 19/954/2224 —
  провенанс; 1131/1235/1261/2119/2148 — PR61).

Второй конфликт — `docs/testing/SKIPS_REGISTRY.md` — решён не слиянием, а
перегенерацией (`tools/skips_registry.py`, 162 записи, `--check` PASS).

## Что вошло и осталось целым

**PR60:** File Intelligence как внешний AGPL-сайдкар (выключен по умолчанию,
типизированный argv, `--review-only` без параметра, четыре написания
auto-apply запрещены в собранном argv, защищённые пути, стейл-план и
привязка хешей, перепроверка дайджеста бинаря перед apply, приватность
fail-closed, review-only/apply-контракт, проверка пост-состояния); идентичность
серий Trader; неизменяемый провенанс прогона; исправление утечки маршрутов.

**PR61:** MF-001 правда о пустом завершении; MF-002 парковка на CAPTCHA +
Resume; MF-003 независимость улик OpenHands; ограниченное восстановление;
ограниченный стриминг и правда о завершении потока; валидация памяти Reality
(NaN/inf/отрицательные → 422); честность уборки; отрицание в контракте
действий; идемпотентность второго Apply в Web Designer; выбор модели Web
Designer; путь Coding → OpenHands; SHA1 без bandit-шума; уборка
read-only-родителей без root.

## Прогоны на `92e89e0` — один за другим, HEAD неподвижен

Каждый набор запущен отдельно, `-p no:cacheprovider`, в том же виде, что и в
соответствующем workflow CI. Ни одно число не взято с другого SHA.

### Целевые (приоритеты 3–5 задания)

| набор | результат |
| --- | --- |
| PR61 breaker-негативы, Command Center (12 файлов: completion truth, streaming contract/honesty, recovery bounded, reality + route validation, action contract negation, action router, apps owner path, coding tasks, web designer apply/model choice) | 219 passed, 35 s |
| PR61 breaker-негативы, bossman-core (OpenHands evidence independence, worktree cleanup honesty) | 32 passed |
| PR60 File Intelligence (contract / hostile / recovery / feature) | 129 passed, 1 skipped (Windows junction) |
| PR60 провенанс прогона | 14 passed |
| PR60 Trader — идентичность серий | 27 passed |
| PR60 STOP_GRACE + AP-001 | 18 passed, 2 skipped (docker-gated: `NOT_TESTED_ON_THIS_HOST`) |

### Полная регрессия

| набор (как в CI) | результат | время |
| --- | --- | --- |
| Bossman Core — полный набор (`--timeout=300`) | 3044 passed, 41 skipped | 5:29 |
| Command Center — py3.12 (`--timeout=180`) | 3013 passed, 19 skipped | 18:28 |
| root — py3.11 (отдельный venv на `/usr/bin/python3.11`) | 1182 passed, 10 skipped | 2:19 |
| root — py3.12 | 1192 passed | 0:53 |
| Editors (настоящий Chromium, `BCC_REQUIRE_BROWSER=1`) | 5 passed; baseline-проверка: 0 failures / 0 errors / 0 skipped | 0:44 |
| Fable media preflight (настоящий ffmpeg, full_decode) / Fleet / Video Studio + Web / Fable | 3 / 88 / 335 passed, 11 skipped / 48 passed | 3:07 |
| ASTRA core-step (remediation + evidence signing + organization e2e) / Command Center | 98 / 24 passed | — |
| ASTRA acceptance `--profile runner` / `--profile sandbox` | PASS (1 тест) / PASS (11 тестов) | — |
| Solana safety py3.12 / py3.11 | 30 / 30 passed | — |
| internal benchmark `smoke` / `pr` | 6 / 13 кейсов PASS, `release_gate: READY`; режимы MOCK / SIMULATED / REAL_SANDBOX, **LIVE = 0**, `LiveCapabilityScore: INSUFFICIENT_EVIDENCE` | — |
| compileall · secret scan · whitespace (область root-ci) · skips registry · scorecard | PASS | — |

Наблюдение, не являющееся регрессией: в хвосте Command Center одно
`PytestUnraisableExceptionWarning: RuntimeError('Event loop is closed')` из
`__del__` транспорта subprocess при сборке мусора — предупреждение, не падение
(3013 passed, 0 failed).

### root py3.11 и `test_objective_cas_under_10ms_and_stale_write_is_denied`

Тест был красным на PR61 `c7e75cc` (py3.11) и зелёным на py3.12. На едином SHA
он запущен **один раз, обычным образом, в составе набора** — прошёл. Порог
10 мс не тронут; повторов «до зелёного», skip/xfail не было. Сырые
p50/p95/p100/floor тест печатает только при падении, поэтому здесь их нет.
Если он снова покраснеет в CI на этом SHA — сравнивать с py3.12-двойником на
том же SHA и с полем `floor_over_limit`, как записано в комментариях к PR #60.

## Что владелец делает дальше (B1–B10)

Порядок и ожидаемые состояния — в `docs/final_closure/OWNER_BREAKER_SETUP.md`
(PR61), он действует без изменений для `92e89e0`. К нему добавляется шаг
File Intelligence из `docs/final/OWNER_TEST_20260908_FILE_INTELLIGENCE.md`
(§07): фича выключена, `GET /api/file-intelligence/status` работает и при
выключенной, включать только на одноразовом корпусе.

## Честно НЕ проверено

`WINDOWS_OWNER`, `LOCAL_MODEL`, `AIFS_REAL_BINARY`, `AIFS_REAL_LOCAL_MODEL`,
настоящие OpenRouter/GLM/OpenHands, Higgsfield с авторизацией, N4/N5/N6/N8,
canary/rollback, soak — `NOT_RUN`, нужна машина владельца.

`INTELLIGENCE_RETENTION = INSUFFICIENT_EVIDENCE`. `measured intelligence
retention` красный на базе и должен остаться красным: файл
`docs/benchmark/intelligence-preservation-current.json` не создавался,
`evaluated_sha` не менялся, workflow не трогался.

## Открытое: PR61 ушёл вперёд после `c7e75cc`

Задание фиксировало PR61 как `head c7e75cc / код cbb2991`; единое дерево
собрано ровно так. Но ветка PR61 (`claude/bossman-final-audit-closure-aucx9x`)
после этого продолжила двигаться: на момент заморозки её head —
`5c19eea58691599cf06764b020a4ea8ca51c0285` (2026-09-09 02:13 UTC), **11
коммитов после `c7e75cc`**, 183 файла, +18281/−853. Из них 3 коммита —
владельца (`2da38b2`, `83c1a02`, `4264fd6`, «Release closure checkpoint…»),
8 — сессии PR61.

Что в них, по `git diff c7e75cc..5c19eea` и по их
`docs/astra/PR60_REQUIRED_SEMANTIC_INTEGRATION_20260910.md`:

* **повторная, независимая реализация части PR60** — идентичность серий
  Trader, провенанс прогона (`command-center/bcc/run_provenance.py` заново,
  +267), STOP_GRACE- и AP-001-тесты; File Intelligence **не** импортирован
  («Deferred»);
* **новый код вне обеих заморозок** — `gateway/anthropic_protocol.py` (+424),
  локальный бандл и `verify_installed_product.py`, `browser_runtime.py`,
  `health.py`, `owner_acceptance.py`, `task_admission.py`, безопасность
  file-commander-mini, правки в 16 workflow-файлах (в т.ч. новые
  `local-bundle.yml`, `postgres-contracts.yml`, `shipped-apps.yml`).

Сухой прогон `git merge-tree 92e89e0 5c19eea`: **9 конфликтов**, все в файлах,
которыми владеет PR60 (`db.py`, `engine.py`, `run_provenance.py` add/add,
`test_run_provenance.py` add/add, `test_secrem_f009_terminal.py`,
`test_stop_grace_lifecycle.py` add/add, `SKIPS_REGISTRY.md`,
`learning/trader_apprentice.py`, `tests/test_trader_apprentice.py`). Это не
сливается автоматически и не является «портом смысла» — это две разные
реализации одних и тех же P1.

Ничего из этого в `92e89e0` не внесено — сознательно: задание запрещает новые
фичи и фиксирует head PR61. Решение владельца, какая линия каноническая:

1. **`92e89e0` каноничен** → работу PR61 после `c7e75cc` переносить на него
   по смыслу (новее + строже побеждает), отдельным проходом, с воспроизведением
   каждой находки. Их замечания к `9fcf9ab` стоит проверить первыми:
   UPDATE провенанса без fence-условия (стейл-воркер после takeover), и
   «сбой снятия провенанса логируется, прогон идёт дальше» — у PR60 это
   осознанный выбор (провенанс — улика, не разрешение), у них — fail-closed.
   Ни то, ни другое здесь не воспроизводилось и не менялось.
2. **`5c19eea` каноничен** → на него переносится File Intelligence целиком
   (включая исправление утечки маршрутов `a1e633c`, которого там нет), и
   регрессия снимается заново на новом SHA.

До этого решения владельческую приёмку B1–B10 имеет смысл проводить на
`92e89e0`: это единственный SHA, на котором обе заморозки прогнаны вместе.

## Дополнение 2026-09-09 ~07:50 UTC — PR #62 и один воспроизведённый дефект установки

**PR #62** (`claude/bossman-final-completion-kymr05`, head `56e2597`, открыт
06:21 UTC) — третья линия на той же базе `45027d3`. Она выбирает путь 2 из
раздела выше: база — заморозка PR61 `910ca90` (через `5c19eea`), на неё
перенесён File Intelligence целиком (включая `9bea81a` и `a1e633c`), PR60 в неё
не вливался. Решение о канонической линии остаётся за владельцем; здесь это
только зафиксировано.

**Дефект, найденный PR #62 и воспроизведённый на `92e89e0`.** В чистом venv
после НЕ-редактируемой установки (`pip install . ./command-center`), при импорте
из каталога вне чекаута:

```
bcc.file_intelligence.discovery.pinned_sha()  ->  ''
bcc.file_intelligence.discovery.manifest()    ->  {}
```

Причина: `_MANIFEST_PATH = Path(__file__).resolve().parents[3] / "integrations" /
...` — в чекауте это корень репозитория, в `site-packages` — каталог
интерпретатора; манифест не находится, `manifest()` мягко возвращает `{}`.
Следствие в установленном продукте: `GET /api/file-intelligence/status` отдаёт
пустой `pinned_upstream_sha`, и каждый review-конверт пишет пустой
`upstream_sha`. **Что НЕ затронуто** (проверено по коду): `FORBIDDEN_FLAGS` /
`ALLOWED_FLAGS` захардкожены в `protocol.py`, `expected_binary` и
`protocol_version` совпадают с умолчаниями манифеста — граница безопасности не
ослабляется, теряется только заявленная идентичность интеграции. Из чекаута
(режим `-e`, в котором сняты все числа этого документа) значение верное:
`4dc374df69b5e63d5354e121097d92e25bbd32da`.

Классификация: **OPEN_REPO_P1** для `92e89e0` в режиме установки колесом. Код
по правилам заморозки не менялся; исправление существует на линии PR #62
(манифест кладётся в колесо рядом с UI, discovery читает упакованную копию
первой, отсутствие манифеста валит сборку). Если каноническим станет `92e89e0` —
перенести именно это, с двумя тестами: установленная копия читает пин, и
отсутствие манифеста не превращается молча в пустую строку. Для приёмки B1–B10
из чекаута дефект не проявляется; при запуске установленного колеса владелец
увидит пустой `pinned_upstream_sha` — это известное состояние, не сюрприз.
