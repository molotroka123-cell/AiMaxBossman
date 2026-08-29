# NEXT — исполняемые шаги (Stage 8)

Все шесть исходных пунктов закрыты (см. WORKLOG). Ниже — то, что осталось.

## 1. Проверить сильные рантаймы на живом хосте
- Установить gVisor (`runsc`) и/или дать доступ к `/dev/kvm` + лаунчер MicroVM.
- Убедиться, что `GvisorRuntime().capabilities().tiers` содержит CONTAINER, а
  `MicroVMRuntime()` — MICROVM, и прогнать реальную задачу под каждым.
- Ожидание: DEVELOPER/HOSTILE перестают отвергаться и реально исполняются.
- Команда: `python -m pytest tests/test_sandbox_strong_runtimes.py -q`
  (сейчас проверяется только fail-closed путь, потому что бинарей нет).

## 2. Замкнуть egress на процесс песочницы
- `EgressProxy` уже поднимается менеджером и кладёт адрес в
  `session.spec.labels['egress_proxy']`.
- Осталось: в `SafeRuntime._env()` пробросить `http_proxy`/`https_proxy` на этот
  адрес, а прямые сокеты в обход прокси закрыть (netns + nftables redirect или
  контейнерный рантайм с сетью только через прокси).
- Тест: процесс в ALLOWLIST-песочнице не может открыть сокет мимо прокси.

## 3. Выдать sandbox-инструменты агенту
- Инструменты `sandbox.*` в REGISTRY, но ни в одном `agent.yaml` не выданы.
- Добавить нужному агенту в его `agent.yaml` с `confirm: true` на create/run.

## 4. Повторный red-team всего Stage 8
- Прошлый аудит не покрыл Stage 8 (агенты упали по лимиту сессии).
- Цели атаки: обход approvals через `sandbox.*`, побег из рабочей копии,
  обход ArtifactGate, утечка секрета в траекторию/датасет, обход egress-прокси,
  зависшая аренда при падении, гонки в автомате состояний.

## 5. Toolbox внутри песочницы
- shell/git/files/browser как инструменты САМОЙ песочницы (не агента).
- Браузер в песочнице обязан использовать отдельный профиль (non-negotiable #9).

## Команды проверки
```
cd bossman-core && python -m pytest tests/test_sandbox_*.py -q
BOSSMAN_TEST_CHROMIUM=$(ls -d /opt/pw-browsers/chromium-*/chrome-linux/chrome|head -1) python -m pytest -q
```
