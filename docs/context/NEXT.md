# NEXT — исполняемые шаги (Stage 8 continuation)

Порядок по приоритету. Каждый шаг — конкретный, с файлом и командой проверки.

## 1. SAFE rootless runtime adapter — ✅ СДЕЛАНО (dd44df0)
<!-- Реализован bossman/sandbox/runtimes/safe.py; 10 тестов в tests/test_sandbox_safe_runtime.py.
     Следующий незакрытый шаг — №2. Исходное задание шага 1 ниже для истории. -->
- Реализуй `bossman/sandbox/runtimes/safe.py` → класс `SafeRuntime` (тот же
  Protocol `SandboxRuntime`, что и `FakeRuntime`).
- `capabilities()` → `RuntimeCapabilities(name="safe", tiers={ROOTLESS},
  supports_offline=True, supports_allowlist=False, ...)`.
- `prepare()` — создать одноразовую рабочую копию `workspace_root/<id>/work`
  (копия/worktree источника `spec.workspace_source`, НЕ mount оригинала).
- `start()`/`poll()` — запуск задачи через `asyncio.create_subprocess_exec`
  (НИКОГДА shell), под `bwrap`/`unshare` если доступно (rootless namespaces),
  с rlimits (pids/mem/wall) из `spec.resources`.
- `destroy()` — снести рабочую копию.
- Тест: `tests/test_sandbox_safe_runtime.py` — реальный запуск `echo`, проверка
  изоляции workspace, cleanup, OFFLINE (нет сети). Команда:
  `python -m pytest tests/test_sandbox_safe_runtime.py -q`.
- Ожидание: SAFE-задача выполняется и не видит host-ФС вне своей копии.

## 2. Egress enforcement в рантайме
- `NetworkGuard.decide()` уже даёт вердикт. Нужен реальный барьер: в SafeRuntime
  запускать процесс в network namespace без интерфейсов (OFFLINE) или через
  proxy, который зовёт `manager.check_network(session, host)` перед соединением.
- Тест: попытка соединения на 127.0.0.1/169.254.169.254 из песочницы → отказ.

## 3. Wire sandbox как инструмент агента
- Добавь `sandbox.*` tools в `bossman/toolkit` через публичный `register()`
  (как это делает `search_everything/tools.py`; НЕ править `toolkit/__init__.py`).
- Минимум: `sandbox.create`, `sandbox.run`, `sandbox.status`, `sandbox.collect`
  (последний — через `manager.artifact_gate(session).inspect(...)`).
- Approvals: создание CONNECTED/HOSTILE песочницы и любой egress — через
  существующий approvals-путь (Этап 1/6), не в обход.

## 4. Persistent Secret Broker backend
- Реализуй `PostgresSecretBroker` (тот же контракт `SecretBrokerBackend`):
  таблицы grants (id, sandbox_id, scope, issued_at, ttl, revoked). Материал
  секрета резолвится из существующего vault/.env НА control-plane, не хранится
  в grant-строке. `CREATE TABLE IF NOT EXISTS` в subsystem.validate().
- Тест: grant→redeem→revoke против in-memory фейка store (без живого PG).

## 5. gVisor / MicroVM адаптеры (fail-closed уже готов)
- `runtimes/gvisor.py`, `runtimes/microvm.py`. Если бинаря/KVM нет —
  `capabilities().tiers` не включает нужный tier, и `PolicyEngine.resolve`
  сам отдаст `IsolationUnavailable` (проверено `test_risk_escalation_requires_microvm`).
- CubeSandbox: см. `_staging/s8/stage8/RUNTIME_SELECTION.md` перед интеграцией.

## 6. Dataset gate из траекторий
- `TrajectoryRecorder` пишет `workspace/_sandbox/<id>/trajectory.jsonl`.
- Пайплайн: raw → sanitize (secrets уже вычищены) → validate/eval → candidate →
  human gate. Sandbox-обучения входят durable-память ТОЛЬКО как candidate
  (non-negotiable #12).

## Команды проверки на каждом шаге
```
cd bossman-core && python -m pytest tests/test_sandbox_*.py -q
BOSSMAN_TEST_CHROMIUM=$(ls -d /opt/pw-browsers/chromium-*/chrome-linux/chrome|head -1) python -m pytest -q
```
