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
