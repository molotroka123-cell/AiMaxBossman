# SESSION HANDOFF — Stage 8 (AI Lab Sandbox)

> Для следующей модели (Claude Opus 5). Этот файл самодостаточен: продолжай **без**
> исходного чата. Работай через точечный поиск / git diff / этот handoff, не
> перечитывай весь repo и весь ZIP.

## CURRENT OBJECTIVE
Достроить Stage 8 (AI Lab Sandbox) control plane. **Ядро уже написано и зелёное.**
Дальше — реальные рантаймы (SAFE rootless → gVisor → MicroVM), egress-энфорсмент,
persistent Secret Broker backend, интеграция инструментов песочницы в toolkit,
dataset-gate из траекторий.

## CURRENT HEAD
- ветка: `claude/bossman-control-v03-43igbk`
- HEAD: `dd44df0` (feat(sandbox): SAFE rootless runtime). Всё запушено в origin.
- baseline этой большой сессии: `ddf2259`.

## WHAT EXISTS (написано в этой сессии)
Стадии 1–7 уже были интегрированы ранее. **В этой сессии добавлено:**
- **Общие швы** (`bossman/errors.py`, `lifecycle.py`, `correlation.py`, `obs.py`) —
  таксономия ошибок, реестр подсистем, correlation-id, JSON-лог с вычисткой секретов.
- **Этапы 4–7** доведены и слиты: `resource_brain/`, `search_everything/`,
  `remote_client/`, `video_factory/` (+ `tests/test_stage4_7.py`).
- **Этап 8 sandbox** (`bossman/sandbox/`) — см. ARCHITECTURE ниже.
- Закрыты аудитные P0/P1 (см. `docs/context/DECISIONS.md`).

## WHAT WORKS (проверено тестами)
- `bossman/sandbox` — 37 адверсариальных тестов зелёные.
- Полный набор `bossman-core`: **265 passed** (2 браузерных требуют
  `BOSSMAN_TEST_CHROMIUM`, см. TEST COMMANDS).
- Все 5 подсистем этапов 4–8 регистрируются в реестре жизненного цикла:
  `resource_brain, remote_client, search_everything, video_factory, sandbox`.

## WHAT DOES NOT WORK / НЕ СДЕЛАНО (следующие шаги)
- **SAFE rootless рантайм ГОТОВ** (`sandbox/runtimes/safe.py`) — реальные процессы,
  копия рабочей области, rlimits, OFFLINE через `unshare -rn`, wall-time.
  **Нет** адаптеров gVisor-класса и MicroVM (CubeSandbox — кандидат, см.
  `_staging/s8/stage8/RUNTIME_SELECTION.md`). Fail-closed уже работает: адаптер
  объявляет `RuntimeCapabilities.tiers`, политика отвергает недостижимый tier
  (проверено `test_hostile_policy_rejected_by_safe_runtime`).
- **Egress-энфорсмент частично**: SAFE в режиме OFFLINE реально уходит в сетевой
  namespace без интерфейсов (`unshare -rn`). Для ALLOWLIST фильтра по хостам
  ещё нет — нужен proxy/nftables-плейн (SafeRuntime честно ставит
  `supports_allowlist=False`, поэтому ALLOWLIST через него отвергается).
- **Secret Broker** — только `InMemorySecretBroker`. Нужен persistent backend
  (тот же контракт: `grant/revoke/revoke_sandbox/redeem`).
- **Toolbox песочницы** (shell/git/files/browser внутри sandbox) — не начат.
- **Dataset-gate** (trajectory → sanitize → validate → candidate → human gate) —
  не начат. `TrajectoryRecorder` уже пишет события, это вход пайплайна.
- Sandbox **не подключён** к runner/агентам как инструмент — только подсистема +
  read-only `/sandbox/status`,`/sandbox/sessions`.

## FILES CHANGED (Stage 8)
`bossman/sandbox/{__init__,models,policy,runtime,resources,network,secrets,artifacts,trajectory,manager,subsystem,routes}.py`,
`bossman/sandbox/runtimes/{__init__,safe}.py`, `tests/test_sandbox_safe_runtime.py`,
`bossman/errors.py` (+6 кодов), `bossman/api.py` (регистрация подсистемы),
`tests/test_sandbox_core.py`, `tests/test_sandbox_security.py`.

## ARCHITECTURE (bossman/sandbox)
Control plane, всё через `SandboxManager`:
- `models.py` — `SandboxState` (12 состояний) + единственный граф переходов
  `_TRANSITIONS`; `SandboxSpec/Policy/Session`, `PolicyMode{SAFE,DEVELOPER,CONNECTED,
  HOSTILE}`, `NetworkMode{OFFLINE,ALLOWLIST,INTERNET}`, `RiskLevel{LOW,MEDIUM,HIGH,
  HOSTILE}`, `IsolationTier{ROOTLESS,CONTAINER,MICROVM}`, `RISK_MIN_ISOLATION`,
  `POLICY_MIN_ISOLATION`, `ResourceRequest`, `RuntimeCapabilities`, `SecretGrant`,
  `Artifact`.
- `policy.py` — `RiskEngine.assess(spec)`; `PolicyEngine.resolve(spec,risk,caps)` →
  `SandboxPolicy`, fail-closed (`IsolationUnavailable`/`PolicyDenied`).
- `runtime.py` — `SandboxRuntime` Protocol + `FakeRuntime` (сценарии в
  `spec.labels["fake_scenario"]`).
- `resources.py` — `ResourceLeaseAdapter` поверх `bossman.resource_brain.BRAIN`
  (reserve/release, double-release safe).
- `network.py` — `NetworkGuard.decide(host,policy,port)` → `NetDecision`.
- `secrets.py` — `InMemorySecretBroker` (+ `SecretBrokerBackend` Protocol).
- `artifacts.py` — `ArtifactGate.inspect(rel)` / `.safe_archive_members(path)`.
- `trajectory.py` — `TrajectoryRecorder.record(kind, **data)` (redacted).
- `manager.py` — `SandboxManager.create/start/poll/freeze/cancel/destroy/recover`,
  `check_network`, `grant_secret`, `artifact_gate`.
- `subsystem.py` — `SandboxSubsystem` (`MANAGER` синглтон, OFF=OFF на start()).
- `__init__.py` — `sandbox_enabled()` (env `BOSSMAN_SANDBOX_ENABLED`, дефолт OFF),
  `build_subsystem()`, `router`.

## ACTIVE DECISIONS
См. `docs/context/DECISIONS.md`. Кратко: переиспользуем Resource Brain (Этап 4),
Gateway (Этап 3), Context/Memory (Этап 2.222) — второго не заводим. Sandbox
memory входит durable-память только как candidate (ещё не реализовано).

## SECURITY BOUNDARIES (non-negotiable, НЕ ослаблять)
1. OFF значит OFF. 2. Сеть по умолчанию OFFLINE. 3. Никакого host docker.sock.
4. Никаких сырых прод-секретов в песочнице (только брокер). 5. Fail closed на
недостижимой изоляции. 6. Прод-ФС не монтируется как writable. 7. Лимиты ресурсов
через Resource Brain. 8. Approvals остаются над песочницей. 9. Прод браузер-профиль
не переиспользуется. 10. Прод-эндпоинты private-first. Полный список:
`_staging/s8/NON_NEGOTIABLES.md`.

## TEST COMMANDS
```
cd bossman-core
python -m pytest tests/test_sandbox_core.py tests/test_sandbox_security.py -q   # 37
CHROME=$(ls -d /opt/pw-browsers/chromium-*/chrome-linux/chrome | head -1)
BOSSMAN_TEST_CHROMIUM="$CHROME" python -m pytest -q                              # 265
```

## LATEST TEST RESULTS
`275 passed` (полный набор, с BOSSMAN_TEST_CHROMIUM). Sandbox: `47 passed`
(core 13 + security 24 + safe runtime 10).

## KNOWN FAILURES
Нет падающих тестов. Открытые долги — в разделе «WHAT DOES NOT WORK» и в
`docs/context/NEXT.md`. Незакрытый долг вне Stage 8: gateway request/run
correlation-logging (P2) и context_engine O(N) vector scan (P2, масштаб).

## NEXT EXACT ACTIONS
См. `docs/context/NEXT.md` — там пронумерованный исполняемый список.
