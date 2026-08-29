# NEXT — исполняемые шаги

Периметр и хостовое исполнение закрыты (см. FINAL_HARDENING_STATUS.md). Осталось:

## 1. PRE-DISPATCH АУДИТ ВЛАДЕЛЬЦА (перед Stage 13)
Stage 13 Dispatch НЕ начат намеренно. Нужен отдельный аудит/одобрение владельца
по FINAL_HARDENING_STATUS.md. Проверить особенно: branch protection (required
checks) и политику Tailscale (наружу только /remote).

## 2. runsc / MicroVM на живом хосте  (БЛОКЕР: железо)
Раннер без runsc/KVM: сильные рантаймы Stage 8 протестированы только по пути
ОТКАЗА (fail closed). На Ai Max (Linux+KVM): установить gVisor / обеспечить
/dev/kvm, прогнать `tests/test_sandbox_strong_runtimes.py`, затем реальную
задачу в DEVELOPER/HOSTILE (должны ИСПОЛНЯТЬСЯ), проверить egress-барьер.

## 3. LOCAL-LIVE dev-factory
LLMPlanner+GatewayEditor подключены, но живьём (реальный Gateway+модель, реальная
правка+тест в песочнице) не гонялись. Прогнать одну задачу end-to-end на хосте с
живым Gateway; убедиться, что патч собирается и НЕ публикуется автоматически.

## 4. Периодический red-team (постоянная практика)
После каждого крупного изменения повторять атаки, а не доверять зелёным тестам
(см. FAIL-001 в FAILURES.md). Новые цели: обход scope-гейта, WS-подписка без
events, containment AI Lab (traversal/symlink), argv-only (нет shell-инъекции в
gitops/media/shell), editor (побег из рабочей копии).

## Команды проверки
```
cd bossman-core && python -m pytest -q --timeout=180 --timeout-method=thread   # 589 passed, 2 skipped
cd command-center && python -m pytest -q --timeout=180 --timeout-method=thread  # 430 passed, 2 skipped
python tools/ci_secret_scan.py                                                  # PASS
```
