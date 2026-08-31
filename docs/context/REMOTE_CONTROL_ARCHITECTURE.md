# REMOTE CONTROL — AI Max Pro (мозг) ↔ ROG Strix (глаза/руки/экран)

Дизайн для двухмашинной схемы владельца. Строим как **две машины по сети**, а не
«подключить видеопамять одного к другому» — VRAM ноутбука (8 GB) не ограничивает
передачу: тяжёлые модели живут на AI Max Pro, ROG получает токены/типизированные
действия по API. Требования к сети смешные (текст/JSON/tool-calls/скриншоты —
мегабайты); 1 GbE достаточно, 2.5 GbE — комфортно для больших файлов.

```
AI MAX PRO (мозг)                          ROG STRIX (клиент)
  Gateway / Model Router                     UI (Command Center)
  Local LLMs / Embeddings / RAG              Browser
  Memory (Postgres)                          Windows control (UI Automation)
  Agents / Orchestration                     OpenCode / IDE
       │  secure API (HTTP/WS, mTLS)              │  лёгкие локальные модели
       ▼                                          ▼
  typed actions  ───────────────────────►  Windows Agent (исполнитель)
       ▲                                          │
       └──────── fresh observation ◄──────────────┘
                 (verifier на AI Max)
```

## Ключевой принцип: это НЕ новый транспорт, а два уже существующих контура

Bossman уже несёт большую часть «безопасного удалённого управления». Не строим
второй Gateway/Policy/Approval — переиспользуем:

| Нужное для remote control | Уже есть в репо | Файл |
|---|---|---|
| Аутентификация устройства | Remote Client device-tokens (hash at rest, single-use nonce) | `bossman/remote_client/` |
| Scopes на устройство | Remote Client scopes, fail-closed | `bossman/remote_client/` |
| Профильный доступ к управлению компом | profiles gate — **теперь fail-CLOSED для не-локальных источников** (эта сессия) | `bossman/profiles/`, `computer_operator/manager.py` |
| Типизированные действия на десктопе | Stage13 Computer Operator (ActionKind, ExpectedState) | `bossman/computer_operator/` |
| Approval на опасные действия | approvals (single-use callback), host-shell = ALWAYS ASK (эта сессия) | `bossman/approvals.py`, `toolkit/shell.py` |
| Свежее наблюдение + verify | Observer.observe(generation) + Verifier | `computer_operator/observer.py`, `verifier.py` |
| Защита от слепого повтора | LoopGuard (repeat/no-progress/oscillation) | `computer_operator/loop_guard.py` |
| Восстановление | recover_all / take_control / resume, Cyber Recovery план | `computer_operator/manager.py`, `cybersec/recovery.py` |
| Границы недоверенного in/out | канонические ingest_guard/egress_guard (эта сессия) | `cybersec/guards.py` |

## Поток «AI Max управляет ROG» (канонический цикл, сетевой)

```
AI Max: intent → typed ComputerAction
   → profiles gate (device_id, source="remote") ── fail-closed, эта сессия
   → policy.classify (scopes/mode)
   → approval (если опасно; host-exec/purchase/… = ALWAYS ASK)
   → [сеть, mTLS] → ROG Windows Agent исполняет argv-only действие
   → ROG снимает свежее наблюдение (foreground+ui_tree+screenshot)
   → [сеть] → AI Max Verifier проверяет постусловие (не «модель сказала успех»)
   → audit + loop-guard.record
```

Самая большая задача — **не скорость сети, а безопасность удалённого управления**.
Что для неё уже готово и что осталось:

### Готово (в т.ч. в этой сессии)
- device-token auth + scopes (Remote Client), fail-closed.
- profiles `computer.control` gate — **fail-CLOSED для source != "local"** и при
  недоступности сервиса; подсистема `critical=True` (не деградирует в permissive).
- host/local shell = **ALWAYS ASK**, не переотменяется грантом агента.
- фичи безопасности слоя: канонический ingest_guard (недоверенный вход) и
  egress_guard fail-closed (исходящее: Telegram-транспорт + runner), IDS→RiskSignal.

### Остаётся сделать под эту схему (по возрастанию усилий)
1. **mTLS/authenticated транспорт AI Max ↔ ROG** — device-token у нас есть; добавить
   взаимный TLS (клиентские сертификаты) для канала, чтобы ни одна сторона не
   доверяла сети. DESIGN → impl на реальном железе.
2. **ROG Windows Agent** — тонкий исполнитель: принимает ТОЛЬКО типизированные
   ComputerAction (не произвольный shell), исполняет через существующие
   Stage13-адаптеры (UI Automation / AppLaunch allowlist), возвращает наблюдение.
   На Linux сейчас честный SKIP_HOST; на ROG — первый live-прогон.
3. **IDS → Policy на контуре управления** — точную вставку описываю ниже:
   поведенческий IDS повышает требование approval, НЕ становясь вторым Policy.
4. **Recovery по сети** — take_control/resume уже есть; добавить сетевой heartbeat
   аренды (ControlLease TTL) поверх канала, чтобы разрыв сети возвращал контроль
   владельцу, а не оставлял задачу висеть.

## IDS → RiskSignal → Policy — точка вставки (готово к реализации)

Тип уже есть: `cybersec.guards.RiskSignal` + `policy_recommendation()`. Вставка —
в `computer_operator/manager.py::_run_loop`, СРАЗУ после `policy.classify(a,...)`
и до решения об approval:

```python
# после d = self.policy.classify(a, mode=t.mode, locked=...)
if gates.cybersec_enabled():
    sig = ids.BehaviorSignal(
        policy_denials=<счётчик отказов за задачу>,
        verifier_failures=<счётчик verify-провалов>,
        repeated_actions=<число срабатываний loop-guard>,
        scope_escalations=1 if a.kind in _AUTHORITY_KINDS else 0,
    )
    rec = guards.policy_recommendation(guards.ids_risk_signal(sig))
    if rec == "deny":
        # трактуем как policy-denial: тратим replan, не действие
        ...
    elif rec == "require_approval":
        d = replace(d, requires_approval=True)   # эскалация в СУЩЕСТВУЮЩИЙ approval
```

Инвариант сохранён: IDS не меняет права напрямую — он лишь **ужесточает** решение
существующей Policy (require_approval/deny), поэтому второго Policy-движка нет.
Счётчики (`policy_denials`, `verifier_failures`, loop-guard trips) уже наблюдаемы
в цикле — нужно только вынести их в per-task аккумулятор. Реализация — отдельным
аккуратным коммитом с тестом на security-критичном loop'е, а не «на бегу».

## Сеть (рекомендация владельцу)
- AI Max Pro и ROG — оба кабелем Ethernet; желательно **2.5 GbE**.
- Для текста/JSON/tool-calls/скриншотов хватит и 1 GbE (~80–110 MB/s).
- 10 GbE нужен только если гонять веса моделей/сырое видео/большие checkpoints —
  для Bossman это не требуется (модель на AI Max, ROG получает токены).
- VRAM ROG (8 GB) определяет лишь какие модели ноутбук запускает САМ; на удалённое
  управление и передачу не влияет.
