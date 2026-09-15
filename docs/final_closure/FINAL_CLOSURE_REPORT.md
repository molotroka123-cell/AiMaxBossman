# Final audit closure — report (2026-09-08, evening)

BASE_BRANCH=`night/v7-convergence-20260908`
BASE_SHA=`45027d3e9aef554407a0a9ff07fb8678d1b849f3`
FIX_BRANCH=`claude/bossman-final-audit-closure-aucx9x` (also pushed as `freeze/final-audit-closure-20260908`)
PR=#61 (draft, base `night/v7-convergence-20260908`)
FINAL_CODE_SHA=`910ca90` (2026-09-09, 01:35 UTC) — the head of both branches and of PR #61.
Closure code = `69df482` + the two CI-driven fixes named below (`4001b7a`, `cbb2991`) + the owner's three
release commits pushed onto the PR branch (`4264fd6`, `83c1a02`, `2da38b2`; 181 files: Windows jobs,
installed-bundle acceptance, byte-exact OpenHands patches, terminal container proof) + seven follow-up
commits that made the combined tree pass CI (§2a). The authoritative exact-SHA result is the CI run on
`910ca90` in PR #61, recorded in §4; the local numbers in §3 were measured on the trees named there.

The owner asked for the logs to be written down now, with little budget left. This report therefore
records what was MEASURED at the moment of writing and names what was still running. Numbers from
different trees are never added together.

## 1. What was reproduced before any code changed (all on BASE_SHA 45027d3)

| Finding | Reproducer | Result on BASE_SHA |
|---|---|---|
| Astra F1 protected-file mutation hidden by `assume-unchanged` | Astra `test_breaker.py` | 2/2 failed (defect present) |
| Astra F2 stream cap does not bound reads | same | 100 frames consumed for `max_chunks=2` |
| Astra F3 partial+malformed stream `ok=True` | same | reproduced |
| Astra F5 NaN/-1024 memory admitted by the route | Astra `test_final_boundaries.py` | 2/2 reproduced |
| Astra F6 alternating error classes never terminate | same | 2/2 reproduced (12 transitions `queued`) |
| Astra F7 negated tool request → TERMINAL_FILE_ACTION | direct `classify_all` probe (8 prompts EN/RU) | reproduced |
| Owner P0 empty result completed (tasks 22/44 shapes + plain prompt) | `tests/test_p0_completion_truth.py` | 7 of 10 tests failed on BASE (3 positive controls passed) |
| Owner P0 CAPTCHA excuse completed, `url_contains` verified on a challenge page | same | reproduced |
| Web Designer second Apply reverts the edit | `tests/test_web_designer_apply_idempotent_ui.py` in real Chromium against the live server | reproduced on the unfixed UI code (line 101: code reverted to SAVED) |
| Astra F4 Windows cleanup swallows errors | code inspection (`rmtree(ignore_errors=True)`) | not reproducible on Linux |

## 2. What was fixed (code commit `69df482`, one-line follow-up in FINAL_CODE_SHA)

See `MASTER_FINDINGS.md` (33 rows). Summary: P0 ×2 fixed and proven; P1 ×3 fixed and proven
(OpenHands evidence independence, Web Designer model choice, Coding → OpenHands owner path);
P2 ×7 fixed and proven (recovery budget, stream cap, stream completion classes, route validation,
sandbox cleanup honesty, action-contract negation, Web Designer apply idempotence). No gate,
threshold or test was weakened or skipped to obtain green.

### 2a. Follow-up commits after the owner's release checkpoint (all test/CI-wiring, no product logic)

Every red job on the combined tree was reproduced (log read; locally in real Chromium where the test is a
browser test) before the change. None widens a gate; the one product-adjacent change (`5e9e246`) makes
the evidence hash stricter about what counts as a change, not looser.

| Commit | Job that was red | Root cause → change |
|---|---|---|
| `5e9e246` | Windows workspace and PID contracts | `core.autocrlf=true` checkout made every text file an "evidence mismatch" → blob ids computed through git's clean filter (`git hash-object --stdin-paths`); symlinks raw; test with an autocrlf fixture |
| `942d13b` | покрытие (Core coverage) | job lacked Chromium while the partitions install it → same install step |
| `6124ec7` | Windows workspace and PID contracts | teacher fakes wrote through Windows text mode (LF→CRLF); the owner's byte-exact patch (`2da38b2`) preserved it correctly → fakes write exact bytes; CRLF positive control added |
| `e5884bd` | local bundle | owner test looked for the disabled Start by its launch title, but the UI swaps the title to «Управление приложениями выключено» when policy is off → assert that button disabled and no launch-titled Start left |
| `7634af5` | root pytest + hygiene, browser-user-paths | skips registry stale after a line shift in `test_openhands_release_evidence.py` → regenerated |
| `67fa440` | browser-user-paths | Web Designer test clicked the preview before the post-Ctrl+S reload; the fresh frame honestly reported the old selection `lost` → helper waits for the reloaded preview |
| `910ca90` | Command Center pytest (py3.14) | owner's terminal AP-001 container test (first completed run: docker exists on the runner) required `res.error is True` for a failed `cat` outside the roots; `terminal.run`'s contract makes a non-zero exit data, `error=True` only when the command could not run → assert `exit_code != 0` in a usable container + no token in output. Not run locally (no docker); built from the CI-observed ToolResult |

## 3. Suites run locally in this run (Linux, Python 3.11.15, real ffmpeg, real Chromium)

Targeted suites, run on the working tree that became `69df482` (pre-commit, identical code):

| Suite | Result |
|---|---|
| New closure tests: P0 completion truth (14), action-contract negation (45 incl. old suite), OpenHands evidence (25), cleanup honesty (7), streaming honesty (22), reality route (16), recovery bounded (18), Web Designer model choice (5), apply-idempotence UI (1), coding tasks (8), apps owner path (2) | all passed |
| Regression batch 1 (policy algebra, authorization at effect time, review deadlock, approval scope, mission budget, apps control ×3, trading lab, resources unmeasured, approval revocation, engine stop, sandbox user-run regressions, no-direct-completed-writes, lazy pages, apps probe cost, crash-after-effect, restart durability) | 229 passed, 1 skipped |
| Regression batch 2 (24 Video Studio files with real ffmpeg + 5 browser suites with Chromium) | 399 passed, 11 skipped |
| Finalize/review/verifier/action suites (finalize gate ×3, review deadlock, governor review, verifiers, action gate, action router) | 76 passed after updating one expectation (deeper `youtube.com/watch` goal) |
| Streaming contract + reality + openrouter router + model health | 109 passed (two SSE tests updated to the DONE-yielding contract, one ladder test updated to bounded semantics) |
| bossman-core `tests/apprentice/` | 102 passed, 10 skipped |
| Root suite (`tests/`) | 1177 passed, 2 skipped, 1 failed → the failure was the stale skips registry, regenerated in `69df482`; `SKIPS_REGISTRY_CURRENT=PASS`, `README_SCORECARD_CURRENT=PASS`, secret scan PASS, whitespace clean |

Full suites on the frozen code:

| Suite | Tree | Result |
|---|---|---|
| bossman-core full (`tests/`, `BOSSMAN_RUN_REAL_SANDBOX=0`) | started on `69df482`, docs commit `413d160` landed mid-run | 3021 passed, 57 skipped, **7 failed — all `ShaMismatch: requested 69df482 but the executing checkout is 413d160`** (the benchmark integrity check refusing a moved HEAD, i.e. the guard working); the same 7 tests re-run on `413d160`: 11/11 passed |
| bossman-core full re-run on `413d160` | in progress at the time of writing | not claimed |
| Command Center full (CI-equivalent: `--cov=bcc --cov-fail-under=72`, `BCC_REQUIRE_BROWSER=1`) | started on `69df482` | **2860 passed, 16 skipped, 1 failed**, coverage 78.85 % (gate 72 %); the failure was `test_ux2_desktop::test_tests_never_write_into_the_owner_data_dir` — test-order pollution from the NEW `test_coding_tasks` (it pinned `tempfile.tempdir`); fixed by restoring it via monkeypatch (also in the new cleanup tests, whose `never_deletes_outside` case had passed only through that pollution and now pins the sandbox parent explicitly) |
| bossman-core full re-run on `413d160` | HEAD moved again (`4001b7a`) mid-run | 3020 passed, 57 skipped, 8 failed — all `ShaMismatch`, same guard as above; CI on the final head is the authoritative Core result |
| Root full + hygiene re-run on `413d160` | stable | **1178 passed, 2 skipped**; `README_SCORECARD_CURRENT=PASS`, `SKIPS_REGISTRY_CURRENT=PASS` (157 entries, 0 without reason), secret scan PASS, whitespace hygiene exit 0 |

## 4. CI on FINAL_CODE_SHA `910ca90` (exact SHA; read 2026-09-09 02:12 UTC)

Every job below ran on commit `910ca901ddcd84c13d65f9246dfec394088c29bf`. Several workflows ran twice on
the same commit (push + pull_request event); a job is listed PASS only when every completed instance of
it is green and none failed.

| Workflow / job | Result on `910ca90` |
|---|---|
| root-ci: root pytest + hygiene (py3.11, py3.12), bossman-core container ships bossman-shared | PASS |
| Command Center CI: pytest (py3.11, py3.12, py3.14), windows paths (py3.12), секреты/JS/запрещённые файлы | PASS (py3.11/3.12/3.14 green in the completed run; two duplicate instances of the same jobs were still running at read time) |
| Bossman Core CI: compile + секреты, security, stage8-14, gateway-context, rest (py3.11, py3.12), Windows workspace and PID contracts (py3.11, py3.12), покрытие (неснижаемый порог) | PASS |
| PostgreSQL contracts (py3.11, py3.12, py3.14) | PASS |
| ASTRA acceptance: portable (ubuntu, windows), runner recovery; real sandbox skipped by design | PASS |
| Editors user safety: browser-user-paths | PASS |
| Existing Social Farm media and browser (py3.11, py3.12); File Commander (ubuntu, windows) | PASS |
| Real media, Web and Fleet (py3.11, py3.12) | PASS |
| Installed local bundle (ubuntu-latest, windows-latest) | PASS |
| Solana safety (3.11, 3.12); deterministic-benchmark; anti-dumbness gate contract | PASS |
| Human speed: component measurements (ubuntu-latest) | PASS (on `e5884bd` the same job failed on wall-clock host stalls — 3 samples over 10 ms with the host floor itself at 11 ms — in the owner's latency contract; this run is its one re-run) |
| Human speed: component measurements (windows-latest) | **FAIL — owner tooling, standing down.** `tests/test_v5_human_speed.py::test_objective_cas_under_10ms_and_stale_write_is_denied`: wall PASS, `thread_cpu` = INSUFFICIENT_EVIDENCE / `degenerate_measurement`. `time.thread_time_ns()` on the Windows runner advances in 15.625 ms quanta, so per-op thread CPU reads 0 or 15.625 ms and the contract's `p50 == 0 → degenerate` rule fires by construction. No finer thread-CPU clock exists in the stdlib on Windows; the contract (added in `2da38b2`) was not weakened. Proposed patch (PR comment): measure the clock quantum once; when quantum ≥ limit, return INSUFFICIENT_EVIDENCE with reason `thread_cpu_clock_quantum` and let the Windows job record a host limitation, wall clock keeping its own verdict |
| Intelligence Preservation: measured intelligence retention | **FAIL — EXTERNAL_EVIDENCE_REQUIRED.** `docs/benchmark/intelligence-preservation-current.json` missing → `INSUFFICIENT_EVIDENCE`. The gate was not weakened; a genuine current same-model measurement is the owner's step |

Earlier heads, for the record (not combined with the numbers above): the root-ci py3.11 latency
test flaked once on `cbb2991` and passed on every later head; the Core `rest (py3.11)` failure on a
non-root runner and bandit B324 were fixed in `cbb2991` / `4001b7a`; no re-run rights were available
in this session, so every "green" above is a fresh run on the commit named.

## 5. Freeze verdict

* Repository-fixable P0/P1 and confirmed P2 findings: fixed with negative and positive controls.
* Open in the repository: MF-012 (Web Designer one-click retry, P2 UX), MF-031 (agentless task creation
  UX, P2), MF-032 (liveness URL, feedback-less buttons, P2–P3), MF-033 (Video Studio 404/ValueError on the
  owner's runtime, not reproduced on the closure code).
* EXTERNAL_EVIDENCE_REQUIRED: Intelligence Preservation.
* OWNER_LIVE_REQUIRED: live OpenRouter/GLM, real OpenHands SDK run, Windows file locks, Higgsfield
  authenticated generation, and the breaker corpus B1–B10 in `OWNER_BREAKER_SETUP.md`.

Freeze status: **READY_FOR_OWNER_BREAKER on `910ca90`** — CI on FINAL_CODE_SHA is green apart from the
two items above that the repository cannot close: Intelligence Preservation (external evidence) and the
Windows thread-CPU measurement contract (owner's tooling, proposed patch on PR #61). Both branches
(`claude/bossman-final-audit-closure-aucx9x`, `freeze/final-audit-closure-20260908`) point at the same
commit; the freeze branch is not to be force-pushed.

---

## Последний проход перед владельческим прогоном на Windows (2026-09-09)

Ветка `claude/bossman-final-completion-kymr05` — прямой потомок `5c19eea`
(рантайм совпадает с объявленным `910ca90`). PR #62 в ту же базу, что и PR #61.

| Пункт | Итог |
|---|---|
| **BUILD_SHA** | добавлен. `bcc/build_identity.py`, отдаётся одинаково `/api/identity`, `/health`, `/healthz`, `/health/live`; короткий SHA виден в оболочке; строка доктора идёт первой. Недоказанный источник — `SOURCE_IDENTITY_UNKNOWN` с причиной, никогда не PASS |
| MF-031 | закрыт. `POST /api/tasks/preflight` — тот же `select_executor`, ничего не пишет; в поле поручения живая строка «Выполнит: агент · модель (AUTO)» или названная причина отказа |
| MF-032 | проверен. `/health`, `/healthz`, `/health/live` уже были; занятость кнопки уже рисовалась (`.bx-btn.is-loading` в `theme.css`). Добавлено `aria-busy` — занятость существовала только для глаз |
| MF-012 | уже закрыт на этом коде: панель восстановления смонтирована на обеих раскладках, всегда даёт «Обновить состояние», «Повторить безопасно» для временных отказов, и diff с явной заменой при конфликте сохранения. Правки элементов намеренно НЕ переигрываются — это правило бы правило чужой элемент |
| MF-033 | **не воспроизводится**. `POST /api/video-studio/commands` существует; `guarded()` переводит `ValueError` в 409/404/422. Фикс не выдуман, остаётся `NEEDS_LIVE` с точным сценарием повтора (B8) |

Ошибка этого прохода, записанная как есть: сначала был сделан вывод «правила
`.is-loading` нет вовсе» — искали только в `style.css`, а правило со спиннером
живёт в `theme.css`. Поймал негативный контроль: проверка осталась зелёной после
удаления «исправления». Лишнее правило снято до того, как оно подралось бы с
существующим спиннером.

Владельческий прогон: `.\start-bossman.ps1` (venv → зависимости → доктор →
окно), правок env-файлов нет, ключ в лаунчер не попадает.
`python scripts/owner_breaker.py plan | run | report` — двенадцать шагов в
заданном порядке; на каждом записываются id задачи и прогона, модель, статус,
результат, вызовы инструментов, улика ПОСТ-СОСТОЯНИЯ, подтверждения, повторы,
ошибки HTTP и консоли, мёртвые клики, токены/стоимость и телеметрия; каждая
запись несёт BUILD_SHA (с пометкой `-DIRTY`, если дерево грязное).

Не ослаблено и не сымитировано: Intelligence Preservation остаётся
`INSUFFICIENT_EVIDENCE`; живые Higgsfield, локальная модель, поведение файловых
блокировок Windows, настоящий OpenHands SDK и доступность провайдеров —
владельческие.

---

# RC владельца — вечер 2026-09-09 (PR #62, ветка `claude/bossman-final-completion-kymr05`)

Продолжение того же закрытия на КАНОННОЙ ветке PR #62. Новая ветка не
создавалась, force-push не делался, исторические ветки не мержились.
База прохода: `864805b9eb6bc0ff6fe0729d555373eb12ba6994`.

## A. OWNER-PREFLIGHT-WORKFLOW-ORDER-01 — воспроизведено и починено

Воспроизведено на `864805b` до правки: шаг «Record exact source» создавал
`safety-results/` внутри checkout, следующий шаг запускал exact-SHA преflight,
и тот честно отказывал — `?? safety-results/`, RC=2. Локальный повтор в том же
порядке дал ту же строку.

Починен ПОРЯДОК, не гейт:

* улики ушли из рабочего дерева в `RUNNER_TEMP/bossman-evening-safety/<SHA>`;
* шаг импорт-путей пишет только в `GITHUB_ENV`;
* преflight идёт по нетронутому дереву;
* после установки и наборов ТОТ ЖЕ гейт вызывается повторно и доказывает, что
  весь путь сертификации дерево не трогал.

Ни одного исключения в ignore-списках, ни одной ослабленной проверки.

Починенный порядок открыл ещё три настоящих отказа, которые до этого были
не видны, потому что джоб падал раньше:

| Отказ | Причина | Починка |
|---|---|---|
| `UnicodeEncodeError` в самопроверке харнессов (windows-latest) | весь вывод русский, консоль раннера в cp1252; лаунчеры экспортировали PYTHONUTF8 и маскировали дефект | три скрипта переводят свои потоки в UTF-8 с `errors=replace`; обёртка отдаёт детям UTF-8 через `setdefault`; в `owner_breaker.py` добавлен отсутствовавший `import sys` |
| `No module named pytest` (ubuntu-latest) | негативный контроль стоял ДО установки пакетов | шаг переехал после установки |
| «async def functions are not natively supported», 23 теста (обе ОС) | `asyncio_mode=auto` объявлен в `bossman-core/pyproject.toml`, а запуск шёл из корня | наборы ядра запускаются с `working-directory: bossman-core`, каждый со своим junitxml |

**Evening residual safety = PASS на ubuntu-latest и windows-latest**
(`07fbfb45bd6308089e56d85b337e54fdb02dc6b7`, run 34358639092). Оба джоба
доходят до затронутых регрессий: 11 тестов корня и 155 тестов ядра.

Контроли на сам гейт живут в `tests/test_evening_owner_run_preflight.py` и
работают на НАСТОЯЩИХ Git-деревьях, не на моках: чистый коммит принимается;
та самая untracked-папка результатов, изменённый отслеживаемый файл, чужой SHA
и чужая ветка — отвергаются; игнорируемое состояние прогона не мешает
запустить обёртку дважды.

## B. Вечерний вход владельца — обёртка по точному SHA

`start-bossman.ps1 -EveningTest` и `start-bossman.sh --evening-test` идут через
`scripts/evening_owner_run.py run`: канонная ветка, чистое дерево, совпадение с
живым `origin`, доктор, самопроверка обоих харнессов, каталог улик этого SHA.
Прямой запуск `evening_acceptance.py` собирал улики без привязки к коммиту —
их нельзя отличить от улик другого дерева. Тихого отката нет: без обёртки
лаунчер отказывает. `-DoctorOnly` и обычный запуск не тронуты (закреплено
тестами).

## C. Иконка приложения

На этой линии мастер 1024 был потерян, а производные ассеты заменены грубым
логотипом из пересекающихся эллипсов с жёсткой обводкой. Чистая вырезанная
плитка (PR #51, `40bb5e4`) перенесена как ЕДИНСТВЕННЫЙ растровый мастер
`command-center/ui/icons/icon-1024.png` — перенесён только артворк.

Из него выводится всё: PNG 512/256/192/128/64/48/32/16 и `bossman.ico` с шестью
размерами. `tools/app_icons.py` — чистый stdlib (проверка обязана идти на каждом
джобе и на машине владельца), масштабирование по премультиплицированной альфе,
иначе прозрачный чёрный из углов даёт тёмный ореол.

Фавикон больше не второй рисунок: `icon.svg` в обоих UI — обёртка над тем же
мастером (в `bossman-core/ui` там была зелёная буква «B»). Из манифеста убран
`purpose=maskable`: плитка уже скруглена и имеет прозрачные углы.

Две ловушки детерминизма, обе всплыли на windows-latest и обе закреплены
контролями: вывод `deflate` различается между сборками zlib (сверяем ПИКСЕЛИ,
а не байты), и git переписывал `icon.svg` в CRLF при чекауте (добавлен
`.gitattributes`; сверка разметки переживает CRLF-клон, но по-прежнему
отвергает другой рисунок и подменённую разметку).

## D. Один архив = всё приложение (Windows)

`tools/build_windows_bundle.py` собирает `BOSSMAN-Windows-x64-<short>/`:
`runtime/` (embeddable CPython той же версии, что собирала колёса, и все
зависимости), `browser/` (Chromium ставит сам shipped Playwright — версия не
угадывается), `media/` (ffmpeg+ffprobe), `icons/`, `app-support/`, `LICENSES/`,
`MANIFEST.json`, `SHA256SUMS`, `Start-Bossman.cmd`, `Evening-Test.cmd`.

`downloads_required_for_bossman_app` ВЫЧИСЛЯЕТСЯ как `1 + ` число компонентов,
которые не удалось вложить; каждый назван с причиной и тем, кто его докачает.
Не вложенный Chromium ломает сборку, а не переименовывается в «сборку без
браузера».

`Evening-Test.cmd` — приёмка ИМЕННО этого билда. Это не `evening_owner_run.py`:
та обёртка привязывает улики к Git-чекауту, а у архива чекаута нет, и выдумывать
ветку нельзя. Привязка здесь настоящая: `MANIFEST.json` называет точный SHA, и
установленный продукт обязан вернуть тот же SHA по HTTP.


### D.1. Что архив реально доказал на windows-latest

Приёмка `tools/verify_windows_bundle.py` распаковывает архив в каталог с
пробелами (`.../bossman bundle .../Owner Downloads/...`) и ведёт его только тем,
что лежит внутри. На `e2e02e708dcf44ac3d8a1c3e3376cfb14831b6ed`
(run 34365049629) — `BOSSMAN_BUNDLE_ACCEPTANCE=PASS`:

* MANIFEST.json и SHA256SUMS сошлись по каждому файлу, SHA источника совпал;
* иконки в архиве побайтово равны канонным из репозитория;
* продукт импортируется вложенным runtime при отсутствии репозитория в
  `sys.path`;
* вложенные `ffmpeg.exe` и `ffprobe.exe` реально запустились;
* вложенный Chromium стартовал и отрисовал страницу;
* вечерняя приёмка архива: доктор без BLOCKED, установленный продукт поднялся,
  ответил своим SHA по HTTP, отдал UI и ассеты, пережил рестарт с сохранением
  состояния и остановился штатно.

Два настоящих дефекта, найденных этой приёмкой (оба — «продукт не работал бы у
владельца», а не косметика):

| Дефект | Что было | Починка |
|---|---|---|
| нет console-точек входа | `pip install --target` не делает рабочих обёрток: свои он кладёт рядом с пакетами и зашивает путь интерпретатора СБОРОЧНОЙ машины. `runtime\Scripts\bossman.exe` в архиве не существовало | сборка пишет свои `.cmd`-обёртки в `runtime\Scripts`, они находят runtime рядом с собой; обёртки pip удаляются; `verify_installed_product` принимает обе формы и по-прежнему ЗАПУСКАЕТ каждую команду |
| доктор BLOCKED на computer-operator | pywinauto, pywin32, pyautogui, Pillow объявлены в extra `windows` у bossman-core, установка голого колеса их не берёт | extra ставится на сборке; ответ лежит внутри загрузки, а не в инструкции «запустите pip» |

`OWNER_REQUIRED` (код 2) и `FAIL` (код 1) теперь различаются: первое помечается
предупреждением, второе валит джоб. Зелёным обманом ничего не красится.

## E. Состояние CI на `e2e02e708dcf44ac3d8a1c3e3376cfb14831b6ed`

| Проверка | Состояние |
|---|---|
| One-download Windows application (windows-latest) | PASS, `BOSSMAN_BUNDLE_ACCEPTANCE=PASS` |
| Evening residual safety (ubuntu-latest, windows-latest) | PASS |
| Local bundle (ubuntu-latest, windows-latest) | PASS |
| Command Center CI: секреты/JS/запрещённые файлы, windows paths (py3.12) | PASS |
| Bossman Core CI: покрытие (неснижаемый порог), security, gateway-context, stage8-14, rest, Windows workspace/PID | PASS |
| PostgreSQL contracts (3.11/3.12/3.14) | PASS |
| Shipped apps, Editors user safety, Solana safety, ASTRA acceptance, Fable media/Fleet | PASS |
| root pytest + hygiene (py3.12) | КРАСНЫЙ в PR-прогоне, ЗЕЛЁНЫЙ в push-прогоне того же коммита — см. ниже |
| measured intelligence retention | КРАСНЫЙ намеренно: `INTELLIGENCE_PRESERVATION=INSUFFICIENT_EVIDENCE` |

### E.1. `test_a_real_4ms_cpu_regression_is_rejected_on_slow_storage` — калибровка, не регрессия

Один и тот же коммит: push-прогон (run 34365045527, py3.12) — зелёный,
PR-прогон (run 34365049736, py3.12) — красный. Тест ничего из изменённого в
этом проходе не трогает.

Причина арифметическая. Тест вносит фиксированные 4 мс процессорной работы и
ждёт отказа по основанию «непропорционально собственному полу хоста». Отказ
наступает при `(floor + 4) / floor > 8`, то есть только если пол хоста быстрее
≈0.571 мс. В красном прогоне пол был 0.62 мс, отношение вышло 7.956 при пороге
8.0 — контракт ЧЕСТНО разрешил операцию: 7.96× собственного пола укладывается в
объявленные 8×. Ошибочна не проверка контракта, а посылка теста: фиксированные
4 мс задают непропорциональность не на каждом хосте.

Предлагаемая правка (в этом проходе НЕ вносится: гейт латентности к задаче не
относится, а трогать его без отдельного воспроизведения запрещено): вычислять
вносимую нагрузку от измеренного пола хоста — `burden > floor * (multiple - 1)`
с запасом, вместо константы 4 мс, сохранив все утверждения теста, включая
минимальные 4 мс настоящей процессорной работы. Ни один порог при этом не
снижается. Права на перезапуск джоба у прогона нет (`403 Resource not
accessible by integration`), поэтому повтор не делался.

### E.2. Сохранение интеллекта остаётся закрытым

`INTELLIGENCE_PRESERVATION=INSUFFICIENT_EVIDENCE`, код выхода 2: файла
`docs/benchmark/intelligence-preservation-current.json` нет, потому что замера
на ТОЙ ЖЕ модели не существует. Это ожидаемое честное состояние: порог не
снижается, другая модель не подставляется, доказательство не выдумывается.
Снять его может только владельческий прогон.

### E.3. Что по-прежнему требует владельца

Живой Windows-запуск установленного архива на машине владельца, реальный
Windows-MCP, локальная модель, провайдеры OpenHands/OpenRouter/GLM, бинарь
AIFS, N4–N8 в бою, канареечный выкат с откатом, soak и замер удержания
интеллекта на той же модели. Ни одно из этого не подменяется CI-уликами.
