# NEXT — исполняемые шаги (Stage 8)

Всё запланированное по Stage 8 сделано. Осталось то, что упирается в железо или
требует отдельного захода.

## 1. Проверить сильные рантаймы на живом хосте  (БЛОКЕР: нет бинарей)
- Установить gVisor (`runsc`) и/или дать `/dev/kvm` + лаунчер MicroVM.
- Проверить: `GvisorRuntime().capabilities().tiers` содержит CONTAINER,
  `MicroVMRuntime()` — MICROVM; прогнать реальную задачу под каждым.
- Сейчас на этом хосте ни runsc, ни KVM нет, поэтому протестирован только путь
  ОТКАЗА (fail closed). Ожидание после установки: DEVELOPER/HOSTILE реально
  исполняются вместо IsolationUnavailable.
- Команда: `python -m pytest tests/test_sandbox_strong_runtimes.py -q`

## 2. Закрыть прямые сокеты мимо egress-прокси
- Сделано: прокси поднимается менеджером, адрес идёт в процесс через
  `http_proxy/https_proxy/all_proxy`, `NO_PROXY` пуст.
- НЕ сделано: процесс всё ещё может открыть сокет напрямую, игнорируя переменные.
  Нужен netns + nftables redirect на прокси (или контейнерный рантайм, где сеть
  ходит только через него).
- После этого `SafeRuntime.supports_allowlist` можно поставить True.
- Тест: процесс в ALLOWLIST-песочнице не может соединиться в обход прокси.

## 3. Повторный red-team всего Stage 8
- Stage 8 ещё не проходил адверсариальный аудит (в прошлом заходе агенты упали
  по лимиту сессии).
- Цели атаки: обход approvals через `sandbox.*`; побег из рабочей копии; обход
  ArtifactGate (symlink/архив/размер); утечка секрета в траекторию или датасет;
  обход egress-прокси; зависшая аренда при падении рантайма; гонки в автомате
  состояний; выход за `workspace_root` через id песочницы.

## 4. Toolbox ВНУТРИ песочницы
- shell/git/files/browser как инструменты самой песочницы (не агента снаружи).
- Браузер в песочнице обязан использовать ОТДЕЛЬНЫЙ профиль (non-negotiable #9).

## Команды проверки
```
cd bossman-core && python -m pytest tests/test_sandbox_*.py -q      # 85
BOSSMAN_TEST_CHROMIUM=$(ls -d /opt/pw-browsers/chromium-*/chrome-linux/chrome|head -1) python -m pytest -q   # 347
```
