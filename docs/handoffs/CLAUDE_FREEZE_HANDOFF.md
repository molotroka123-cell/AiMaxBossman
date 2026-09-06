# CLAUDE FREEZE HANDOFF — передача до исчерпания лимита

Написано на замороженном состоянии: новых блоков не начиналось, всё
проверенное закоммичено и запушено, непроверенное — НЕ слито.

## 1. Ветки, SHA, PR

```
BRANCH=claude/v5-closure-at-reconcile-xdh12f
HANDOFF_SHA=47d14511ae7be32dc4b9b1cfaea842112d3936fe
FINAL_REMOTE_SHA=47d14511ae7be32dc4b9b1cfaea842112d3936fe   # local == remote, проверено
PR=https://github.com/molotroka123-cell/AiMaxBossman/pull/37 (draft)
BASE_BRANCH=claude/bossman-control-v03-43igbk
BASE_SHA=1bb39bf8dc656cf21a9bd03f85f6751d87d725c7
WORKTREE=clean (git status пуст на момент записи)
```

Force-push не делался, ветки не удалялись.

## 2. Что в основной, а что только в этой ветке

**Уже в основной (`1bb39bf`), НЕ переделывать:** PR32 journal/telemetry/doctor,
PR34 Windows/security/UX, durable AT-04 evidence ledger, owner recovery parking,
Context byte-digest/cache migration (`927a353`), deny-before-approval +
owner-control diagnostics (`871de8c`), Web New project и editor user-path тесты
(`1bb39bf`).

**Только в этой ветке (37 коммитов над базой).** Слито из чужих веток:
`0b73e69` — merge живой приёмки владельца `acceptance/total-local-20260906`
(`6dfb1e9`); `437c26a` — merge аудитов ГЛМ (`20f70d4`).

Собственная работа, по коммитам:

| SHA | Что закрыто |
|---|---|
| `dd2fb78` | **AT-01 и AT-03** на текущей линии |
| `4e8f8ca` | **Video CFR**: допуск в один кадр на квантование длительности контейнера |
| `6cc9523` | OBSERVER-DEPS-001, GATEWAY-URL-V1, H-CLUSTER (три дефекта живого прогона) |
| `0d732c7` + `15f666d` | **V5 N4**: возврат ключа конфликта + справедливость + 8 враждебных случаев `settle` |
| `9af3dde` | Профайлер шага: отдельная стоимость boundary probe |
| `5abd4a6` | **V5 N5**: измеренное продвижение (holdout, одноразовая улика) |
| `63d8259` | **V5 N8**: канарейка + репетиция отката |
| `c673bea` | **V5 N6**: мастер целей, вкладки «Улики» и «Ревизии» |
| `b19f4fd` | Web Designer: четыре молчаливых способа уничтожить страницу |
| `51a536a` | Оператор: кириллица, работающий «Стоп», закрытый обход approval |
| `181643f` | **OpenRouter**: владелец может подключить ключ и дойти до GLM 5.3 |
| `9cb1fb4` | Измеритель удержания интеллекта (его не существовало) |
| `798e634` | Scorecard V5 честно |
| `98b9945` | 8 скилов в `.claude/skills/` для локальной модели |
| `506b2f2` | Speed-gate: нормировка по полу хоста, замер чередуясь |
| `f38ac74`, `47d1451` | Промежуточные checkpoint'ы параллельных аудит-лент |

## 3. Незавершённое и FILES_IN_PROGRESS

На момент записи рабочее дерево ЧИСТОЕ: всё, что успели ленты, закоммичено.

**Параллельный прогон десяти аудит-лент был ещё активен.** Ленты, чьи правки
УЖЕ вошли: cc-ux-p1, cc-ux-p2, video-studio. Ленты, чей результат в ветку НЕ
попал: operator-core (A1), auth-approvals (A2), adapters A3-04..A3-12,
evidence-journal (A4), v5-remainder (A6-02..A6-10), list-caps (A11a/A11b),
web-designer A9-05..A9-12. Их находки остаются открытыми — см. §5.

Adversarial-проверка лент НЕ завершилась. Поэтому вошедшие правки лент
(`f38ac74`, `47d1451`) — это checkpoint, а не утверждение, что каждая находка
закрыта.

```
FILES_IN_PROGRESS=(нет; дерево чистое на 47d1451)
LANES_NOT_LANDED=A1, A2, A3(04-12), A4, A6(02-10), A9(05-12), A11a, A11b
```

## 4. Аудиты ГЛМ

```
GLM_AUDIT_BRANCH=acceptance/total-local-20260906
GLM_AUDIT_SHA_REVIEWED=20f70d40f49e96f826e4f47f93f187700de49065   # слит в 437c26a
```
Прочитаны и учтены: `docs/testing/acceptance-run-20260906/audits/audit-01..11b`
(12 файлов) и `OPEN_FINDINGS.json`. Более новых аудитов, чем `20f70d4`, на
момент записи не появлялось.

## 5. Открытые подтверждённые P0/P1 с репродукциями

**P0: нет.**

**P1, подтверждённые и НЕ закрытые:**

* `A8-01` / `CC-VIDEO-READVERIFICATION-WINFILE` — `WinError 32` при
  unlink/replace медиа под живым `VerifiedRead`-дескриптором,
  `command-center/bcc/video_studio/read_verification.py:93,160-161,220`.
  Репродукция: 7 тестов кластера A на Windows. **Здесь непроверяемо** (Linux
  разрешает unlink открытого файла) → `INSUFFICIENT_EVIDENCE`, нужна Windows.
  Вектор из аудита: открывать хендл с `FILE_SHARE_DELETE`.
* `A7-01`, `A7-02` — правки вошли в `f38ac74`, но **adversarial-проверка не
  завершилась**: считать закрытыми нельзя.
* `A9-01..A9-04` — закрыты в `b19f4fd` с репродукцией и контролями.
  `A9-05..A9-12` открыты.
* `A3-01..A3-03` — закрыты в `51a536a`. `A3-04..A3-12` открыты, из них
  `A3-04` (виснущее приложение вешает шаг навсегда: нет `asyncio.wait_for`
  вокруг observe/execute) — самый острый.
* `A2-03`, `A2-04` — обход тумблера `computer_control` и fail-open downgrade
  источника. Открыты, малы, безопасность.
* `A6-03` — `set_stopped` не проверяет владельца: чужой снимает owner-stop.
  Открыто, мало, безопасность.

**Известные НЕ-регрессии (проверены на чистом `1bb39bf`, не чинить вслепую):**
`test_editors_user_acceptance.py` (2 теста), `test_web_designer_sandbox_ui.py`,
`test_web_designer_viewport.py` — Playwright-таймауты окружения контейнера.
Воспроизводятся идентично на базе.

## 6. Тесты, коды возврата, CI

Все команды из корня, `.venv/bin/python`, флаги
`-q --timeout=300 --timeout-method=thread -p no:cacheprovider`.

| Набор | Результат | На каком SHA |
|---|---|---|
| `bossman-core/tests` | **2670 passed, 31 skipped, 0 failed** (exit 0) | `51a536a` |
| `tests` (root) | 956 passed, 1 failed → см. ниже | `798e634` |
| `tests/test_v5_human_speed.py` | **26 passed** после `506b2f2` | `506b2f2` |
| V5-выборка (`-k "v5 or promotion or fairness or canary"`) | **379 passed** | `15f666d` |
| command-center `-k "openrouter or provider or model or plugin"` | **240 passed, 5 skipped** | `181643f` |
| bossman-core `-k "gateway or backend or cli"` | **170 passed, 1 skipped** | `181643f` |
| command-center `-k video` | 320 passed, 11 skipped, 1 failed (pre-existing Playwright) | `47d1451` |
| `test_video_studio_cfr_frames.py` (настоящий FFmpeg 6.1.1) | **22 passed** | `4e8f8ca` |
| `tools/ci_secret_scan.py` | **PASS** | `181643f` |

Единственный root-FAIL был `test_objective_cas_under_10ms_and_stale_write_is_denied`
— **не регрессия**: измерено, что на базе `1bb39bf` тот же гейт даёт здесь
p100=27.53 мс при пороге 10, а на этой ветке операция БЫСТРЕЕ (p50 1.68 против
2.79 мс). Исправлено в `506b2f2` нормировкой по полу хоста.

**CI (PR #37).** Красные проверки на всех SHA до `506b2f2`:
`root pytest + hygiene (py3.11/py3.12)` — это ровно CAS-гейт выше, значение на
раннере 309.97 мс; `measured intelligence retention` — падает из-за
ОТСУТСТВУЮЩЕГО `docs/benchmark/intelligence-preservation-current.json`, не из-за
модели и не из-за кода; `windows paths (py3.12)` — на `5abd4a6` и `63d8259`,
НЕ разобрано, см. §8.
Прогон CI на `47d1451` на момент записи не завершился — результат смотреть
на самом PR, чужие цифры не заимствовать.

```
CI_RUNS_ACTUAL_CHECKOUT_SHAS=4e8f8ca, 5abd4a6, 63d8259, 437c26a, 15f666d, 181643f, 9cb1fb4, 798e634
```

## 7. Работающий локальный прогон — НЕ менять

Приёмочный прогон владельца идёт на его машине из своей копии; эта ветка его
не трогала. Зафиксировано из `CHECKPOINT_1/2`:

```
RUN_ID=acceptance-20260906-01
ACCEPTANCE_WORKTREE=C:\Bossman-acceptance-20260906   # не трогать
ACCEPTANCE_JOURNAL=C:\bossman-acceptance\20260906T1945-d1e851b\   # вне git
TESTED_CODE_SHA=60efe77 (ветка acceptance/total-local-20260906)
HOST=Windows 11 26200, i9-14900HX, RTX 4060 8GB, RAM 15.6GB (НЕ 128GB)
FFmpeg=8.1, Python=3.13.1, Ollama=0.33.3 на 127.0.0.1:11435
МОДЕЛИ=qwen2.5:7b, qwen2.5-coder:14b, llama3.2:latest
```
Ветку `acceptance/total-local-20260906` не перезаписывать, процесс ГЛМ не
перезапускать, его рабочую копию не обновлять посреди сценария.

## 8. Точная следующая операция

Строго по порядку; ничего не начинать «широко».

1. `git fetch origin` и взять ФАКТИЧЕСКИЙ head, не эти цифры.
2. Открыть PR #37, посмотреть CI на `47d1451`. Ожидание: `root pytest + hygiene`
   зелёный после `506b2f2`. Если красный — читать лог, а не гадать.
3. **`windows paths (py3.12)`** — единственная красная проверка, которую здесь
   НЕ разбирали. Взять её лог из run на `5abd4a6` или `63d8259` и
   воспроизвести. Это первая настоящая работа.
4. `measured intelligence retention` останется красной, пока на машине
   владельца не снят настоящий прогон. Команда — в
   `docs/benchmark/INTELLIGENCE_MEASUREMENT.md`. Внимание: порог CI
   `--min-samples 20` — это НИЖНЯЯ ГРАНИЦА, а не достаточность. Измерено
   прогоном самого гейта: даже БЕЗУПРЕЧНЫЙ парный прогон получает PASS только
   со 189 задач на метрику; при 20 ответ будет INSUFFICIENT_EVIDENCE. В наборе
   сейчас 20 на метрику. Не выдавать INSUFFICIENT_EVIDENCE за PASS.
5. Только потом — открытые находки §5, начиная с `A3-04`, `A2-03/04`, `A6-03`.

## 9. Правила, которые здесь соблюдались

Секреты не публиковались: `tools/ci_secret_scan.py` PASS, настоящих ключей в
дереве нет. Тесты не ослаблялись ради зелёного: там, где контракт менялся
(AT-01, AT-03, speed-gate), тест переписан вместе с обоснованием, а не
смягчён, и к каждому смягчению строгости добавлен отрицательный контроль.
Автономность V5 не включалась. Force-push не делался.
