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
