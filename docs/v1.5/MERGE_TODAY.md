# Порядок сведения 23.09 в release/bossman-owner

**Задача: сохранить всю полезную согласованную работу, не сливать все старые PR механически.** Этот пакет не является runtime merge. Сначала свежие refs, потом semantic convergence, потом gates и только затем продвижение продукта.

TARGET = `release/bossman-owner`.
STAGING = существующая `fix/owner-run-20260923-p1`, если она по свежему ancestry остаётся лучшей owner-line. Не создавать очередную final-ветку. `main` не менять.

## 0. Freeze источников, не уничтожение текущей работы

В локальном repo: `git status --short`, `git worktree list`, `git fetch --all --prune`. Записать all relevant remote HEADs и точный START_TARGET_SHA после fetch. [contracts/source-refs.json](contracts/source-refs.json) — отправная точка, не запрет на новые коммиты.

Проверить dirty/shared worktree, активные model/media/coding jobs и аренды. Не checkout/reset чужую рабочую папку; использовать existing dedicated integration worktree или isolated detached candidate. Не удалять/авто-stash чужие изменения. Сохранить rollback ref только по существующей repo policy.

## 1. Карта уникальных delta

Для каждого источника получить точные commit dates, parents, `merge-base --is-ancestor`, `log --left-right --cherry-pick`, при необходимости patch-id/range-diff. Не считать commit новым только потому, что search возвратил его первым; индекс/даты могут быть устаревшими. `today` определяется diff с known baseline и происхождением, не только датой в названии ветки.

Таблица `TODAY_MERGE_LEDGER`: source/ref/SHA → capability/fix → existing equivalent → tests/evidence → INCLUDE / ALREADY_INCLUDED / DEFERRED / REJECTED(reason). Для каждой полезной неперенесённой функции есть решение. Открытый PR не означает, что его код отсутствует в кандидате.

## 2. Сначала сохранить пакет и owner-line

Документация 1.5 опубликована непосредственно на TARGET, поверх прежнего `e0bf948d`. Поэтому прежде certification включить свежий TARGET с документацией в STAGING обычным merge. Это сохраняет future fast-forward ancestry. Не брать старый target SHA из этого документа для реальной команды.

На старте owner-line наблюдалась на `1a29d85a`; источник: evidence `010d1907`. Не cherry-pick из неё по одному уже унаследованные SHA2/SHA3/SHA4 fixes и не откатывать к `12612c81`. Сначала сохранить полную актуальную линию, затем включать только недостающие delta.

## 3. Owner/security слой

Проверить сохранность memory/current-intent, CRLF evidence, tool-output paging, no-progress, CLI parity, PDF verified outcomes, computer.* tool contract, streaming full-repo snapshots и controlled apply. EOD перечисляет `aa6bee3d`, `c0a7e039`, `f72af6a8`, `14392574`, `cb13c2fd`, `6f9d1497`, `1a29d85a` — это lookup hints, проверять полные SHA и ancestry перед действием.

S1–S4 / `c5daa5e3` из прежнего обсуждения не объявлять новым сегодняшним fix автоматически: установить где он уже предок, где был перенесён эквивалентный patch. C1/C2/C3 re-attack на новом candidate обязателен. Security fix должен сохранять функцию, а не только удалять доступ к ней.

После слоя: targeted red-before/green-after и независимый adversarial retest. При conflict сначала invariant/reproducer, не `ours/theirs` вслепую. Если изменение смешивает нужный fix и эксперимент — минимальный port с исходным attribution и ledger; не переписывать чужую историю.

## 4. Существующие Evolution/CLI/skills delta

Сравнить `integrate/owner-final-20260922`, cloud-closure (#74), `codex/bossman-v1.1-evolution`, `claude/self-learning-orchestrator`, CLI/skills/memory/Telegram ветки. Унаследованное не мержить повторно. Нельзя переносом старой ветки восстановить второй engine/memory/router или регрессировать сегодняшние security fixes.

Полезный новый delta → тест → merge/cherry-pick в STAGING. Непроверенный эксперимент оставить disabled и clearly staged, либо DEFERRED с точным source SHA. Не удалять источники и не закрывать PR с несохранённой работой.

## 5. Сегодняшнее media/Jev/Higgsfield

Проверить `fix/studio-seedance-durations-20260923`, существующие Studio/AI Streamer/Higgsfield и Jev delta. Название источника не доказывает иной provider или меньшую цену. Опубликованные model/provider contracts и raw evidence важнее сообщений чата. Не использовать этот merge для новой платной генерации; уже созданные безопасные artifacts индексировать с hashes.

Переносить рабочие adapters, duration/accounting fixes и согласованные тексты, не демо-проект вместо Bossman. Новый provider остаётся optional/off без credentials. Нельзя выполнить live call/purchase/voice enrollment по факту наличия спецификации 1.5.

## 6. CI слой — проверять содержимое, не названия

Свежий EOD пишет, что Windows-100 на другой линии — fake print-loop, настоящий runner только спроектирован. Проверить source этого workflow. Не добавлять его в required список как работающие 100 tests, не переносить косметические PASS-выводы.

Нужен реально исполняющий assertions harness с test IDs, isolated state, negative controls, проверяемыми exit codes и итогами. NOT_RUN не замещается print PASS. При необходимости минимально реализовать его по существующему EOD-плану и проверить сам harness заведомо плохим input.

Required workflows брать из действующего exact_sha_certify и repo policy; учесть branch filters, push/PR/dispatch, все страницы runs и реальные checkout/build SHA. Нельзя менять default/main или плодить final refs ради CI trigger. Использовать поддерживаемый dispatch или исправить branch coverage CI отдельным reviewed change; если auth 403 — точный OWNER_ACTION_REQUIRED, не зелёный отчёт.

## 7. Evidence и документация

Не merge evidence-ветку как произвольный runtime source. Перенести безопасные MD/индекс/нужные fixtures адресно или сослаться immutable URL, сохраняя tested SHA. Не создавать задним числом raw logs/использованные prompts. Оригиналы приватных screenshots/voice/PII не публиковать. Новые prompts помечаются authored-now, не historically used.

Обновить CONTINUE, KNOWN_LIMITATIONS и статус каждой возможности. Этот пакет должен остаться в candidate. Пустые credentials и отсутствие fixtures не исправлять загрузкой чужих секретов.

## 8. Freeze candidate и выполнить тесты

Приостановить новые merge, зафиксировать RC_SHA, actual model/config/grants. Независимо прогнать P0/P1 suite, full regression, genuine Windows-100, Terminal TR/HW обязательные live cases, standard user, lifecycle/STOP/approvals, file parity, full-repo coding+apply. Не повторять все исторические тяжёлые тесты без причины в triage, но обязательная итоговая матрица должна относиться к final SHA.

Любой product/test/CI fix → новый RC_SHA и новый соответствующий evidence. Исправление не даёт автоматически право переносить PASS предка. Независимый auditor проверяет критерии и конфликтующие summary.

## 9. Exact-SHA и ZIP

Когда нет известных открытых critical blockers и обязательные тесты доступны, запустить штатную сертификацию для одного CANDIDATE_SHA. Windows ZIP собирается штатным build из того же SHA, с embedded source SHA; inner ZIP SHA-256 не путать с digest GitHub artifact wrapper. После распаковки — smoke установленного продукта, не подстановка .py/runtime из checkout.

Required BLOCKED/NOT_RUN/red означает NO_GO. Optional hardware/provider limits документируются, но полную 1.5 phone/commerce readiness без live проверки не объявлять. Certificate attached отдельно от frozen source: не пытаться записать собственный commit SHA внутрь создающего его commit.

## 10. Единственное продвижение в TARGET

Перед продвижением снова fetch. Сравнить START_TARGET_SHA с актуальным remote target и worker heads. Если target изменился — сохранить новые commits, включить delta в staging, обновить кандидат и повторить затронутые/required gates. Никакого overwrite.

Предпочтение: fast-forward TARGET на уже сертифицированный candidate, только если remote TARGET его предок и branch policy разрешает. Тогда `RELEASE_SHA == CANDIDATE_SHA`, архивная identity сохраняется точно. Обычный non-force push без `+`, force-with-lease запрещён вместе с force-push.

Если repo требует PR merge/squash и создаётся новый RELEASE_SHA: не выдавать сертификат родителя за exact-SHA нового commit. Проверить полное tree equality и ancestry; выполнить required CI/certification и build/identity для фактического RELEASE_SHA до объявления готового релиза. Старый архив можно сохранить только с его настоящим source SHA, не переименовывая его в новую сборку. Условия защиты веток не обходить.

## 11. После merge

Проверить remote SHA, tree, пакет docs/v1.5, capability inventory, отсутствие потерянных fixes и зелёную required matrix. `main` HEAD должен остаться прежним. Сохранить `MERGE_RESULT.md`: source decisions, merged/skipped/deferred, candidate/release/ZIP hashes, CI run IDs, P0/P1/P2, genuinely available capabilities и первую команду следующей сессии.

Если merge blocked, push безопасный STAGING/evidence и конкретный blocker. Не обещать completed merge. Слияние сегодняшнего кода не означает, что вся спроектированная 1.5 уже реализована. После handoff остановить ненужные workers/paid jobs, сохранить STOP и список намеренно оставленных процессов.

---

## SAME-RUN HANDOFF: 1.5 → 1.6

После закрытия 1.5 **или** достижения `V1_5_NEAR_COMPLETE` этот же owner-run сразу продолжает работу в отдельной ветке 1.6:

[feat/bossman-1.6-secret-intake-20260925](https://github.com/molotroka123-cell/AiMaxBossman/tree/feat/bossman-1.6-secret-intake-20260925)

`V1_5_NEAR_COMPLETE` = нет software P0 и release-blocking P1, основные targeted/regression/hidden tests зелёные, canonical SHA зафиксирован, а остаток относится только к owner-live/soak/внешней среде.

Не ждать отдельного следующего дня. Цель одного прогона:
`BOSSMAN_1_5_CLOSED -> BOSSMAN_1_6_CLOSED`.

Evidence и финальные статусы 1.5 и 1.6 сохраняются раздельно по своим SHA.
