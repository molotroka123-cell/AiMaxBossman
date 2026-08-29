# NEXT — исполняемые шаги

## 1. runsc / MicroVM на живом хосте  (БЛОКЕР: нет бинарей и /dev/kvm)
- Установить gVisor (`runsc`) и/или дать `/dev/kvm` + лаунчер MicroVM.
- Проверить: `GvisorRuntime().capabilities().tiers` содержит CONTAINER,
  `MicroVMRuntime()` — MICROVM; прогнать реальную задачу под каждым.
- Сейчас протестирован только путь ОТКАЗА (fail closed).
- `python -m pytest tests/test_sandbox_strong_runtimes.py -q`

## 2. Toolbox ВНУТРИ песочницы
- shell/git/files/browser как инструменты самой песочницы (не агента снаружи).
- Браузер там обязан использовать ОТДЕЛЬНЫЙ профиль (non-negotiable #9).

## 3. Dev Factory: реальный планировщик
- Подключить `Planner` к модели ЧЕРЕЗ существующий Gateway (Этап 3), второго не
  заводить. `FakePlanner` остаётся для детерминированных тестов.
- Реализовать `executor.edit()` как шов под модель/агента. Инвариант: пустой
  прогон не должен выдавать себя за работу — доказательства даёт только шаг TEST.

## 4. Периодический red-team
- Повторять атаки после каждого крупного изменения, а не один раз.
- Пробы лежат в `tests/test_sandbox_redteam_findings.py` и
  `tests/test_dev_factory.py`; расширять их, а не заменять.

## Команды проверки
```
cd bossman-core && python -m pytest -q            # 432 passed, 2 skipped
python -m pytest tests/test_sandbox_*.py -q
python -m pytest tests/test_dev_factory.py -q
```
