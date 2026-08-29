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
