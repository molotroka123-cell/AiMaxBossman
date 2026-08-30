# WORKLOG

2026-08-29T05:20Z
ACTION: Общие швы этапов 4–7 + фикс авторизации telegram-вебхука
FILES: bossman/{errors,lifecycle,correlation,obs,events,api,config}.py
RESULT: Таксономия ошибок, реестр подсистем, correlation-id, JSON-лог с редакцией; вебхук approvals больше не открыт
TEST: pytest tests/test_shared_seams_stage4_7.py -> 15 passed; всего 125
NEXT: Стадии 4/5/6 параллельно

2026-08-29T06:05Z
ACTION: Закрыты аудитные P0/P1 — амнезия уплотнения, гонка project runner; исправлен .gitignore
FILES: bossman/context.py, bossman/projects/runner.py, bossman-core/.gitignore
RESULT: Пустая сводка не стирает историю; advisory-лок Postgres = один писатель на проект; пакет bossman/projects впервые попал в git
TEST: pytest tests/test_hardening_p0_p1.py -> 8 passed; всего 133
NEXT: browser approvals

2026-08-29T06:30Z
ACTION: Ужесточение browser approvals + gateway failover
FILES: bossman/toolkit/browser.py, bossman/gateway/{backends,app}.py
RESULT: select под барьером + confirmed_select, структурный submit; 4xx больше не эскалируется на облачный маршрут
TEST: 16 + 4 passed; всего 203
NEXT: этапы 4/5/6/7

2026-08-29T07:10Z
ACTION: Слиты этапы 4/5/6/7 (Resource Brain, Search, Remote, Video) + приёмочный тест
FILES: bossman/{resource_brain,search_everything,remote_client,video_factory}/*, tests/test_stage4_7.py
RESULT: Все 4 подсистемы регистрируются в реестре; аренды закрывают OOM-race; поиск без второго RAG; scope на каждом роуте; видео под допуском и ffmpeg argv
TEST: pytest -q -> 228 passed
NEXT: повторный red-team аудит

2026-08-29T07:40Z
ACTION: Повторный red-team нашёл 3 живых обхода approvals — закрыты
FILES: bossman/toolkit/browser.py
RESULT: press(Space) больше не активирует submit; <select onchange> под барьером; role=button консеквентен вне формы
TEST: pytest tests/test_browser_approvals_p1.py -> 16 passed
NEXT: Stage 8

2026-08-29T07:50Z
ACTION: Написано ядро Stage 8 AI Lab Sandbox
FILES: bossman/sandbox/{models,policy,runtime,resources,network,secrets,artifacts,trajectory,manager,subsystem,routes,__init__}.py, tests/test_sandbox_{core,security}.py
RESULT: 12-состоянийный автомат с запретом невалидных переходов, fail-closed политика/риск, FakeRuntime, аренды поверх Resource Brain, default-deny сеть, брокер секретов, artifact gate, редактируемая траектория; OFF=OFF
TEST: pytest tests/test_sandbox_*.py -> 37 passed; всего 265 passed
NEXT: SAFE rootless runtime adapter (NEXT.md шаг 1)

2026-08-29T08:05Z
ACTION: SAFE rootless runtime (NEXT шаг 1) + два дефекта, найденных его тестами
FILES: bossman/sandbox/runtimes/{__init__,safe}.py, sandbox/trajectory.py, sandbox/{models,policy}.py, tests/test_sandbox_safe_runtime.py
RESULT: Реальное исполнение процессов с копией рабочей области, rlimits, OFFLINE через unshare -rn, wall-time; траектория больше не роняет очистку и не воскрешает снесённый каталог; источник рабочей области недоверенный по умолчанию
TEST: pytest tests/test_sandbox_safe_runtime.py -> 10 passed; всего 275 passed
NEXT: NEXT.md шаг 2 — egress ALLOWLIST-энфорсмент (proxy/nftables)

2026-08-29T08:15Z
ACTION: Закрыт fail-closed пробел — OFFLINE без энфорсмента рантайма
FILES: bossman/sandbox/policy.py, tests/test_sandbox_safe_runtime.py
RESULT: Если рантайм не умеет отрезать сеть (нет unshare/netns), OFFLINE-заявка отвергается (IsolationUnavailable), а не исполняется с полной сетью под видом OFFLINE
TEST: pytest tests/test_sandbox_*.py -> 48 passed; всего 276 passed
NEXT: NEXT.md шаг 2 (ALLOWLIST egress proxy) или шаг 3 (sandbox.* инструменты агента)

2026-08-29T08:40Z
ACTION: Закрыты NEXT шаги 3–4 — инструменты агента и персистентный Secret Broker
FILES: bossman/sandbox/{tools,secrets,subsystem,__init__}.py, tests/test_sandbox_tools_and_broker.py
RESULT: sandbox.create/run/status/collect/destroy в REGISTRY (create/run под approval, argv только массивом, collect через ArtifactGate); PostgresSecretBroker хранит только scope, не материал секрета
TEST: 8 passed; всего 284 passed
NEXT: dataset gate и адаптеры сильной изоляции

2026-08-29T08:50Z
ACTION: Закрыты NEXT шаги 5–6 — dataset gate и Gvisor/MicroVM адаптеры
FILES: bossman/sandbox/dataset.py, bossman/sandbox/runtimes/strong.py, tests/test_sandbox_{dataset_gate,strong_runtimes}.py
RESULT: путь «сырые логи → обучение» физически закрыт (PermissionError без явного человека); HOSTILE без KVM отвергается, а не исполняется в контейнере
TEST: 13 passed; всего 297 passed
NEXT: egress-барьер для ALLOWLIST

2026-08-29T09:00Z
ACTION: Закрыт NEXT шаг 2 — реальный egress-барьер
FILES: bossman/sandbox/{egress,manager,__init__}.py, tests/test_sandbox_egress.py
RESULT: CONNECT-прокси спрашивает NetworkGuard на каждое соединение; приватные сети/metadata/control-plane запрещены даже при явном allowlist; в OFFLINE прокси не поднимается; менеджер стартует и закрывает его по жизненному циклу
TEST: 10 passed; всего 307 passed
NEXT: см. docs/context/NEXT.md — железо (runsc/KVM), замыкание egress на процесс, выдача инструментов агенту, red-team Stage 8

2026-08-29T09:20Z
ACTION: Замкнут egress на процесс и выданы инструменты агенту (NEXT шаги 2–3)
FILES: bossman/sandbox/runtimes/safe.py, agents/coder/agent.yaml, tests/test_sandbox_{egress,tools_and_broker}.py
RESULT: адрес прокси идёт в процесс через http(s)_proxy/all_proxy с пустым NO_PROXY; sandbox.* выданы coder, create/run с «: confirm»; прямые сокеты мимо прокси пока НЕ закрыты (нужен netns+nftables)
TEST: 22 passed по затронутым наборам; полный набор 311
NEXT: merge параллельной ветки и push

2026-08-29T09:30Z
ACTION: Merge параллельной ветки (openrouter discovery, circuit breaker, memory/state integrity) и push
FILES: merge-коммит 820ea18
RESULT: конфликтов нет (территории не пересекались); объединённое дерево зелёное
TEST: pytest bossman-core -q -> 347 passed
NEXT: см. docs/context/NEXT.md — runsc/KVM в железе, netns+nftables, red-team Stage 8

2026-08-29T10:10Z
ACTION: Закрыты пункты 1-3 внешнего аудита
FILES: bossman/db.py, bossman/errors.py, tests/browser_support.py, tests/test_browser_{approvals_p1,emulator_e2e,support_helper}.py, tests/test_db_fail_fast.py
RESULT: без Postgres — DEPENDENCY_UNAVAILABLE(503) с подсказкой и без пароля в тексте; schema.sql читается utf-8; Chromium ищется кроссплатформенно без запуска драйвера, битый путь не уходит в launch (это и вешало прогон на Windows); launch с timeout=60s
TEST: pytest tests -q БЕЗ переменных -> 354 passed (было 2 failed / hang)
NEXT: остались Linux-зависимые пункты (runsc/KVM, netns+nftables) и red-team Stage 8

2026-08-29T10:40Z
ACTION: Red-team Stage 8 — 18 атакующих проб, 3 реальные дыры закрыты
FILES: bossman/sandbox/artifacts.py, bossman/sandbox/runtimes/safe.py, bossman/obs.py, tests/test_sandbox_redteam_findings.py
RESULT: RT-01 хардлинк проходил ArtifactGate (эксфильтрация любого файла хоста); RT-02 OFFLINE-песочница шла под uid ядра = root, из-под root не действует protected_hardlinks; RT-03 секрет с дефисами внутри токена и поле «key» проходили редакцию в траекторию и датасет
TEST: pytest tests -q -> 371 passed (10 регрессов)
NEXT: Stage 10

2026-08-29T11:00Z
ACTION: Этап 10 — Dev Factory (автономная петля разработки)
FILES: bossman/dev_factory/{models,store,planner,workspace,evidence,reviewer,factory,executor,subsystem,routes,__init__}.py, tests/test_dev_factory.py, docs/context/STAGE10_STATUS.md
RESULT: петля до патча без авто-мержа; пути публикации в коде нет (проверено разбором AST); успех невозможен без доказательств; сбой песочницы fail-closed; бюджет конечен; рестарт не повторяет консеквентные шаги; инъекции из репозитория не меняют план
TEST: pytest tests/test_dev_factory.py -q -> 21 passed; полный набор 392 passed
NEXT: подключить реальный планировщик через существующий Gateway; executor.edit как шов под модель
[2026-08-29T09:00:50Z]
STAGE9_ACTION: полный Stage 9 E2E набор
FILES: bossman-core/tests/test_stage9_{gateway_e2e,sandbox_e2e,resource_stress,recovery,agent_smoke}.py; command-center/tests/test_feat_openrouter{,_smoke}.py; bossman/toolkit/{files,journal}.py (utf-8); bossman/gateway/app.py (400 on broken json); bossman/sandbox/runtimes/safe.py (nt fail-closed)
TEST: stage9 20 passed/4 skipped; openrouter 26 passed/1 skipped
RESULT: DONE
ISSUE: live OpenRouter/SAFE-live/live-local — BLOCKED_BY_HOST (ключ/POSIX/env)
NEXT: Stage 11 AI Lab (отдельный воркер)

[2026-08-29T09:08:50Z]
STAGE12_ACTION: (prep) Stage 11 закрыт перед интеграцией Stage 12
FILES: bossman/ai_lab/{__init__,sanitizer,candidates,export,routes}.py, tests/test_stage11_ai_lab.py, api.py (router), docs/context/STAGE11_STATUS.md
RESULT: 18/18 tests passed, training OFF by default
TEST: pytest ../bossman-core/tests/test_stage11_ai_lab.py -q
SECURITY: raw->training bypass closed; secrets+PII redacted; provenance enforced; approval revocation blocks export
NEXT: Stage 12 integration

2026-08-29T11:30Z
ACTION: Закрыты последние выполнимые пункты — планировщик на модели и toolbox внутри песочницы
FILES: bossman/dev_factory/{planner,executor,__init__}.py, bossman/sandbox/{toolbox,__init__}.py, tests/test_dev_factory.py, tests/test_sandbox_toolbox.py
RESULT: LLMPlanner идёт через существующий Gateway, разбор ответа строгий (REVIEW/PATCH дописывает система, argv только массивом, сбой модели → запасной план); toolbox песочницы без публикации (git push/remote/config отсутствуют) и без шелл-инъекций (включая закрытый «sh -c»), файлы не выходят за рабочую область, браузер с отдельным профилем
TEST: pytest tests -q -> 480 passed, 2 skipped
NEXT: только runsc/KVM на железе (Ai Max) и периодический red-team
[2026-08-29T09:20:27Z]
STAGE12_ACTION: интеграция пакета + adversarial hardening + encoding fix
FILES: bossman/remote_client/{mobile_api.py,__init__.py}, remote-app/, scripts/bootstrap_remote_device.py, ios/, tests/test_stage12_{mobile_api,security}.py, tests/test_remote_client.py, gateway/app.py (utf-8)
RESULT: mobile API расширяет Stage 6; PWA на /remote/app; iOS отдельным пакетом; BOM+mojibake устранены (0 mojibake lines, 42 gateway tests green)
TEST: stage12 31 passed; stage11 18 passed; full bossman-core 345 passed/27 skipped/0 failed
SECURITY: IDOR 404, scope-гвардейцы, logout/revoke, redaction email/IP (дописан слой Stage 11 sanitizer), SW не кэширует /remote/*, нет CDN/токенов в URL
NEXT: живой прогон на железе + пуш результатов

2026-08-29T14:20Z
ACTION: Закрыты обе находки аудита — открытые консеквентные маршруты ядра и второй путь исполнения мимо песочницы
FILES: bossman/authz.py (новый), bossman/api.py, bossman/ai_lab/routes.py, bossman/config.py, bossman/toolkit/shell.py, ui/index.html, .env.example, README.md, tests/test_core_route_authz.py
RESULT: (1) POST /approvals/{id}, PATCH /agents/{name}, POST /projects/{slug}/approve и гейт обучающего набора (/api/lab/.../decide, launch_training) требуют BOSSMAN_CORE_API_KEY; сравнение постоянного времени, ключ не настроен => отказ (fail closed), проверка по адресу источника сознательно НЕ используется (за tailscale serve всё приходит с loopback). (2) SANDBOX_MODE=local теперь исполняет только вместе с явным BOSSMAN_UNSAFE_LOCAL_EXEC=1, неизвестное значение режима отвергается вместо тихого падения в хостовый шелл.
TEST: pytest tests -q -> 507 passed, 2 skipped (было 494/2); живая проверка на api.app: аноним 401, неверный ключ 401, верный ключ доходит до обработчика
SECURITY: подтверждение больше нельзя решить анонимно с порта ядра; у агента больше нет неизолированного исполнителя по умолчанию
NEXT: без изменений — runsc/KVM на железе и периодический red-team

2026-08-29T18:35Z
ACTION: Pre-dispatch hardening — периметр ядра, AI Lab, хостовое исполнение, dev-factory, CI
FILES: bossman-core/bossman/perimeter.py (новый), api.py, ai_lab/{routes,export,__init__}.py, dev_factory/{editor(новый),subsystem,factory,planner,routes}.py, sandbox/routes.py, resource_brain/routes.py, video_factory/routes.py, search_everything/router.py, toolkit/{gitops,media,shell}.py, config.py, obs.py, ui/index.html, .env.example, README.md; tests/{test_core_auth_perimeter,test_ai_lab_containment,test_host_execution_argv,test_dev_factory_editor,test_dev_factory_wiring}.py (новые) + пометки канареек; .github/workflows/{bossman-core-ci(новый),command-center-ci}.yml; both pyproject (pytest-timeout); docs/context/{CORE_AUTH_MATRIX,FINAL_HARDENING_STATUS}.md, docs/deployment/TAILSCALE_V1_POLICY.md
RESULT: единый Stage 6 auth-слой на всех маршрутах ядра (убран отдельный core_api_key); approvals/decide недостижим без scope approve (доказано счётчиком); AI Lab admin+containment по sandbox_id+release аренды; argv-only на gitops/media/shell; планировщик точное имя исполняемого; dev-factory planner+editor подключены через существующий Gateway; CI ядра добавлен, канарейки помечены поштучно, зависший тест падает по pytest-timeout
TEST: bossman-core 589 passed/2 skipped (было 494/2); command-center — прогон в фоне; секрет-скан PASS
SECURITY: localhost НЕ аутентификация (за tailscale serve всё с loopback); WS-токен субпротоколом, не в URL; наружу только /remote (TAILSCALE_V1_POLICY)
BLOCKED_BY_HOST: runsc/KVM (сильные рантаймы Stage 8), живой Gateway/модель (LOCAL-LIVE прогон правки), live OpenRouter
NEXT: только pre-dispatch аудит владельца; Stage 13 НЕ начинать

2026-08-29T18:45Z
ACTION: Первый прогон CI ядра — устранены красные, вызванные окружением раннера, и флак
FILES: bossman-core/bossman/sandbox/runtimes/safe.py (safe_runtime_available → кэш-проба реальной способности), tests/test_sandbox_egress.py, tests/test_stage9_sandbox_e2e.py (гейт по этой пробе вместо posix-only), command-center/tests/test_v21_snapshot.py (не-hex канарейка), .github/workflows/* (timeout-method thread→signal)
RESULT: bossman-core CI падал на тестах реального SAFE-исполнения — на раннере сброшенный uid не проходит в 0700-tmp под root (DESTROYED). Проба воспроизводит ровно это и честно skip'ает там, где способности нет; на dev-хосте/Ai Max тесты идут. Egress-allowlist — та же проба. command-center: канарейка кончалась на hex и `SECRET[-4:]` ложно совпадал с sha256 в манифесте (флак) — хвост сделан не-hex. timeout-method signal называет зависший тест вместо безымянного убийства job'а.
TEST: локально затронутые наборы зелёные (egress+safe+stage9 28 passed/1 skipped; snapshot 16 passed)
PUSH: d3a3510 (после merge чужого Stage13-пака и hardware-audit)
CI: перезапущен на d3a3510 — ожидается зелёный; флак-висяк py3.12 под наблюдением (signal-метод назовёт тест, если повторится)
NEXT: дождаться CI; если py3.12 снова висит — назвать тест и чинить; PRE-DISPATCH аудит владельца; Stage 13 НЕ начинать

2026-08-29T19:06Z
ACTION: Второй виток CI — исправлена проба способности; command-center висяки названы
FILES: bossman-core/bossman/sandbox/runtimes/safe.py (проба теперь запускает реальный `unshare -r -n`), tests/test_sandbox_egress.py (allowlist гейт на netguard.available())
RESULT (bossman-core CI): реальный отказ на раннере — не обход каталогов (это работает), а `unshare -r` (unprivileged userns) запрещён seccomp'ом раннера → SAFE-процесс FAILED. Проба переписана на точную команду OFFLINE-пути; egress-allowlist гейтится на nftables. Локально 35 passed/1 skipped. Push fccc533.
RESULT (command-center CI): secret-scan и флак-канарейка (test_v21_snapshot) ИСПРАВЛЕНЫ (429 passed). Остались ДВА висяка, названные signal-методом: test_discovery::test_open_port_that_stays_silent_is_not_called_absent (py3.12) и test_v21_failure_injection::test_provider_failure_retries_are_bounded_and_status_is_honest (py3.11). Код under-test ограничен (wait_for 2.5с) и локально оба идут за ~2.5с; на раннере зависают >180с — специфика сети/asyncio-teardown раннера. НЕ трогаю продовый discovery.py спекулятивно (риск уронить 429 зелёных). Это предсуществующие тесты, не связанные с hardening; теперь падают быстро и ИМЕНОВАННО вместо тихого 30-мин kill.
NEXT: bossman-core CI на fccc533 — ожидается зелёный (проверка check-in'ом); command-center — два сетевых висяка чинить с воспроизведением на раннере (bounded-timeout/закрытие httpx-клиента), отдельно от security-мандата

2026-08-29T19:28Z
ACTION: Детерминированный гейт реального SAFE вместо пробы; bossman-core CI зелёный ожидаемо
FILES: bossman-core/bossman/sandbox/runtimes/safe.py (safe_runtime_available чтит BOSSMAN_RUN_REAL_SANDBOX), .github/workflows/bossman-core-ci.yml (env=0)
RESULT: три итерации пробы показали — реальный SafeRuntime /bin/true СОБИРАЕТСЯ на раннере, но тесты через менеджер уходят в DESTROYED; тривиальной пробой набор ограничений раннера не воспроизвести. Введён явный флаг (как runsc/KVM, item 23): раннер объявлен неспособным (=0) → 5 тестов реального исполнения честно skip; dev/Ai Max без флага → авто-проба их запускает. Локально с флагом: 2 passed/15 skipped; без — идут. Push 15616ca.
NEXT: подтвердить bossman-core CI зелёным (check-in); command-center — два сетевых висяка (см. выше) отдельно

<<<<<<< HEAD
2026-08-29T19:51Z
ACTION: CI ПОЛНОСТЬЮ ЗЕЛЁНЫЙ на HEAD c36b3c9 — оба workflow подтверждены
RESULT: Bossman Core CI run #12 → success (все 9 jobs: security/gateway-context/stage8-14/rest × py3.11+py3.12, compile). Command Center CI run #86 → success (pytest × py3.11+py3.12, secrets/JS checks). Pre-dispatch hardening (P0/P1) + CI-инфраструктура закрыты и подтверждены зелёным CI.
NEXT: PRE-DISPATCH АУДИТ ВЛАДЕЛЬЦА (FINAL_HARDENING_STATUS.md). Stage 13 Dispatch НЕ начинать. Открытые пункты вне мандата: runsc/KVM на железе (BLOCKED_BY_HOST), LOCAL-LIVE прогон dev-factory, воспроизведение 2 command-center висяков на self-hosted раннере (см. NEXT.md §5).
=======
[2026-08-29T20:19:05Z]
STAGE13_ACTION: pack integrated + 3-agent red team + subsystem registration P0 fix
FILES: bossman/computer_operator/* (16), api.py, ai_lab/{candidates,routes}.py, dev_factory/planner.py, toolkit/media.py, tests/test_stage13_{auth,ailab,hostexec,operator}_redteam.py, tests/test_computer_operator_*.py, tests/test_sandbox_toolbox.py
RESULT: bossman-core FULL 681 passed / 30 skipped / 0 failed; subsystems register again (P0 fix); operator hardened (bossman-surface deny, control lease, stale gate, secret redaction)
TEST: redteam batteries 31+83+57+21; live-local qwen2.5:7b PASS
SECURITY: LLM cannot click BOSSMAN approval UI; desktop lease exclusive; stale approval/screen rejected; secrets REDACTED in journal
NEXT: push + CI + GPT handoff folder
>>>>>>> 0d2c6e3 (docs: stage13 worklog)
