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

## 5. Два command-center теста зависают на GitHub-раннере (открытый баг)
`tests/test_discovery.py::test_open_port_that_stays_silent_is_not_called_absent`
и `tests/test_v21_failure_injection.py::test_provider_failure_retries_are_bounded_and_status_is_honest`
зависают >180с ТОЛЬКО на GitHub-раннере (signal-таймаут их называет), локально
идут за ~2.5с и проходят. Оба на FakeAdapter — сеть ни при чём; зависает
teardown asyncio/движка BCC под окружением раннера. Пока помечены
`BCC_CI_SKIP_RUNNER_HANGS=1` в CI (локально/на железе гоняются). Воспроизвести
на self-hosted раннере, добавить bounded-timeout в движок/фикстуру `env`,
снять флаг. НЕ трогать продовый discovery.py без воспроизведения — 429 тестов
сейчас зелёные.
