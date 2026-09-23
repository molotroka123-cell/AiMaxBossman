# CLAUDE — СВЕСТИ СЕГОДНЯШНИЙ BOSSMAN И ПОДГОТОВИТЬ БАЗУ 1.5

Репозиторий: `molotroka123-cell/AiMaxBossman`.
ЕДИНСТВЕННАЯ ЦЕЛЕВАЯ ВЕТКА: `release/bossman-owner`.

Нужны фактическое сведение, тесты и push, не новый план. Документация 1.5 уже здесь: `BOSSMAN_1_5_START_HERE.md`, весь `docs/v1.5/`. Сохрани её. Сначала завершить integration сегодняшнего кода; не пытаться за одну сессию реализовать ещё несуществующие телефон/покупки.

## Сначала

`git status --short`, `git worktree list`, `git fetch --all --prune`.
Прочитай AGENTS, BOSSMAN_1_5_START_HERE, `docs/v1.5/MERGE_TODAY.md`, свежие `owner-repair/owner-run-20260923/FINAL-1.0/CONTINUE.md`, EOD/20MIN_CLOSURE и финальный аудит из evidence-ветки. Не выдавай отсутствие файла в своей старой ветке за отсутствие во всём repo.

Исходные указатели в `docs/v1.5/contracts/source-refs.json` — подсказки. По последнему чтению owner/fix=`1a29d85a`, evidence=`010d1907`, integration=`bd2fe23d`. Получи сегодняшние remote HEAD, не откатывай новые изменения. Release получил docs-only commit: включи его в staging, чтобы не потерять пакет и сохранить возможность fast-forward.

## Рой из 5 агентов

1. **OWNER + SECURITY:** перепроверить и сохранить memory/current-intent, PDF false PASS, computer.*, C1/C2/C3, path containment, approvals/STOP/sandbox. Reproducer → regression → минимальный fix → independent retest.
2. **CODING + EVOLUTION:** full-repo streaming evidence, controlled apply, no-progress/output paging, App/Skill foundation; сравнить Evolution/self-learning/skills ветки, не дублировать stores/engines. Сохранить attribution LOCAL_STUDENT/TEACHER.
3. **CLI + INTERNET + MEDIA:** сегодняшний CLI/Terminal, Jev, Seedance durations/cost, существующий Higgsfield/Studio, shared state и artefacts. Не запускать платные генерации или новые providers по этому merge prompt.
4. **WINDOWS + CI + PACKAGING:** реальный Windows-100, required regression, branch filters/actual checkout SHA, standard-user, clean installed ZIP. Print-loop, ноль jobs и skipped не PASS.
5. **INDEPENDENT AUDITOR:** read-only проверка результатов 1–4, source ledger, конфликтующих claims, secret/PII scan, code/build/evidence identities. Автор fix не сертифицирует сам себя.

Координатор — единственный writer shared integration worktree и TARGET. Worker работают в изолированных scopes/worktrees, возвращают commit + evidence; не меняют один desktop/data root/GPU/restart одновременно. Не создавать новую final-ветку. Использовать текущую owner/fix staging-линию; новые temporary worktrees не становятся альтернативным продуктом.

## Порядок сведения

1. Snapshot всех refs и unique delta/patch-equivalence. Ledger INCLUDE/ALREADY_INCLUDED/DEFERRED/REJECTED с причиной.
2. В existing staging сохранить нынешнюю owner-line и включить свежий TARGET с документацией.
3. Проверенные owner/security fixes; старые security commits не дублировать, если уже ancestors/equivalent.
4. Недостающий полезный Evolution/CLI/skills/Telegram delta, затем согласованные media/Jev/Higgsfield fixes. Без blind merge старых PR.
5. Настоящие CI/Windows fixes. Проверить, что Windows-100 реально исполняет assertions; свежий EOD предупреждает о print-loop.
6. Адресно safe evidence/MD/index, не runtime merge evidence-ветки. Сохранить все useful sources; непроверенное явно DEFERRED, а не потеряно.
7. Freeze RC_SHA → targeted + full required regressions → independent red-team → live installed/standard-user tests. Fix = новый SHA, соответствующий retest.
8. Один candidate → exact-SHA CI → Windows ZIP того же SHA → inner ZIP SHA256 → unpacked smoke → independent audit.
9. Fetch TARGET заново. Только если required gates зелёные и target не ушёл: non-force fast-forward на certified candidate, если разрешено repo policy. Иначе PR по правилам; новый merge SHA требует отдельной exact-SHA проверки/identity.
10. Verify remote release и сохранность docs/fixes; `main` НЕ МЕНЯТЬ.

## Правила

Сохранять нынешние функции, не переписывать архитектуру. Routine engineering внутри owner scope делать без лишних вопросов; не отключать SAC, policy, verifier/STOP/budgets. Новая спецификация LOCAL_UNRESTRICTED относится к поведению локальной модели, а не разрешению любому скрипту читать vault/платить/менять stable. Новых звонков, покупок, voice enrollment и auto-recharge не выполнять.

Нельзя форсировать зелёный статус удалением теста/required workflow или добавлением fake result JSON. На deadline — честный checkpoint и pushed fixes, не ложный freeze. BLOCKED required gate запрещает объявление release-ready; продолжать независимые безопасные работы.

Фактический historical learning verdict: single coached repair; transfer NO_MEASURED_GAIN, пока новые сравнимые данные не изменили его. Jev shadow/модельный agreement не независимое подтверждение всей системы.

## Итог

Создай `MERGE_RESULT.md` и актуальный CONTINUE с одним следующим действием. Выдай: TARGET, START_TARGET_SHA, INCLUDED/SKIPPED/DEFERRED SOURCES, CANDIDATE_SHA, RELEASE_SHA, MERGED_YES_NO, EXACT_SHA_CI, WINDOWS_100, ZIP_SHA256, P0/P1/P2, LIVE_RETESTS, 1.5_DOCS_PRESENT, MAIN_UNCHANGED, UNPUSHED_CHANGES и BLOCKERS.

Если gates не закрыты — сохранить candidate/evidence и точную команду продолжения. Если закрыты — выполнить разрешённое продвижение в ту же `release/bossman-owner`, не только предложить его. В обоих случаях не объявлять нереализованные функции 1.5 готовыми и не начинать следующий большой эксперимент после итогового handoff.
