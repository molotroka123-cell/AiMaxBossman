### 1. 5 серьёзных пробелов/рисков

1. **Governor рассчитан на статические оценки памяти, но iGPU/Windows — динамическая shared memory.**  
   120 ГБ «общей памяти» не означают 120 ГБ стабильного GPU-резерва: есть page file, fragmentation, driver overhead, Ollama/sd.cpp/LTX/Wan могут аллоцировать по-разному. Оценка «пик из замеров» не гарантирует отсутствие OOM.  
   **Исправление:** ввести два уровня резерва: `resident_lease` (модель в памяти) и `compute_lease` (активная генерация). Максимум одна `compute_lease`. Сумма `resident + compute + safety_margin` должна быть ≤ реально измеренного `available_gpu_bytes`. Перед выдачей — preflight probe, во время — sampling every 2–5 sec, при превышении резерва на >1 ГБ — emergency preempt/stop.

2. **Нет лицензионно-юридического gate для продажи Studio-контента.**  
   LTX-2.5, Wan VACE, uncensored-модели, DWPose, клиентские лица/голоса, метка AI, водяные знаки, коммерческое использование — всё это может блокировать первый платный заказ. TZ говорит «проверить лицензии», но не делает это hard-stop.  
   **Исправление:** до любого `studio_order.status = ready_to_deliver` обязательный чек-лист: модель/версия, license_id, commercial_use=true, consent_artifact_id если есть человек/лицо/голос, AI_label=true, watermark=true, provenance hash. Без чек-листа заказ не может быть сдан.

3. **M2 — это не pipeline, а набор генераций без SLA, QC, retry и себестоимости.**  
   «Заявка → генерация → проверка → сдача» не описывает: кто отменяет, что при таймауте, сколько retries, как измеряется качество, как клиент получает результат, как фиксируется себестоимость, как owner review блокирует сдачу.  
   **Исправление:** state machine: `draft → accepted → preflight → generating → qc_failed/retry → qc_passed → owner_review → approved → delivered → invoiced`. Каждый переход — event log, таймауты, retry policy, cost receipt. Для MVP один тип заказа, без лиц и сложных движений.

4. **M3 Coding Swarm и M4 Obliterated local brain — риск безопасности, бюджета и юрисдикции.**  
   Облачные кодеры получают контекст репозитория, могут унести секреты/личные данные. Kimi с доступом к ПК — prompt injection и destructive actions. Uncensored локальная модель — риск запрещённого контента, особенно при коммерческих заказах.  
   **Исправление:** для первого платного заказа отложить M3/M4. Оставить только локальные модели и ручные правки. Если облако нужно — strict secret-scan, allowlist файлов, no personal data, no shell access, patches only, owner approval. Для uncensored — не использовать в клиентских заказах до legal/content-policy тестов.

5. **Нет проверки спроса: цель «≥1 оплаченный пилот» не связана с процессом продаж.**  
   TZ строит инфраструктуру, но не описывает, как найти клиента, оценить заявку, взять предоплату/плату, сдать результат. Можно сделать Governor идеально и снова получить ноль выручки.  
   **Исправление:** сузить MVP до одного продукта: «короткий вертикальный motion-ролик для товара/услуги, 5–10 сек, 720×1280, AI label, 1 правка, 48 ч». Ручной intake через Telegram/pulse, pre-sell 3 пилота, оплата до генерации или после owner approval. Никаких Netlify forms, pose_dance, lifestyle_pushin, ниш и самоулучшения, пока нет первого платежа.

---

### 2. Что убрать или отложить ради первого платного заказа

**Отложить полностью:**
- M3 Coding Swarm: Kimi/Qwen/бесплатные/Claude-автокод.  
- M4 Obliterated local brain: бенчи uncensored, удаление дублей, смена локальной модели по умолчанию.  
- M5 полный паритет: `/niche`, `/mem`, `/calls`, все мобильные карточки.  
- M2 `pose_dance`, `lifestyle_pushin`, Wan VACE, DWPose, любые заказы с лицами/голосами/танцами.  
- Netlify Forms и автоматический приём заявок.  
- Исследование ниш, самоаудиты, самоулучшение, AI Company Mode.

**Оставить минимально:**
- M1 Governor: state, lease, unload, watchdog, restart.  
- M2 один пресет: `product_spin` или `hand_reveal` без людей/лиц.  
- Telegram/pulse: `/gov`, `/studio` только просмотр заказов и owner approval.  
- M6 критичное: STOP, client token, secret scan, redirect/DNS, license gate, AI label.  
- Ручной pipeline: заявка → генерация → QC → owner approval → сдача → квитанция.

---

### 3. M1 Resource Governor: конкретный дизайн

#### Структуры данных

SQLite или append-only journal + in-memory cache.

```python
Pool:
  id: str
  capacity_bytes: int
  safety_margin_bytes: int
  hard_stop_free_bytes: int
  measured_at: datetime
  state: healthy | degraded | emergency

Consumer:
  id: str
  name: str
  kind: ollama | sd_cli | ltx | wan | rife | train
  priority: int
  unloadable: bool
  max_resident_bytes: int
  max_compute_bytes: int

Task:
  id: str
  consumer_id: str
  order_id: str | null
  type: str
  state: queued | running | cancelling | done | failed | orphaned
  lease_id: str | null
  started_at: datetime
  deadline: datetime
  cancel_token: str

Lease:
  id: str
  task_id: str
  consumer_id: str
  kind: resident | compute
  requested_bytes: int
  granted_bytes: int
  priority: int
  state: requested | granted | revoked | released | expired | orphaned
  token: str
  expires_at: datetime
  preemption_allowed: bool
  reason: str

EventLog:
  id: int
  ts: datetime
  type: str
  lease_id: str | null
  task_id: str | null
  payload: json
```

`resident_lease` — модель загружена, но не генерирует. `compute_lease` — активная тяжёлая GPU-работа. Governor разрешает максимум один `compute_lease`.

#### Алгоритм выдачи аренды

1. `POST /api/governor/lease` принимает:
   - `consumer_id`, `task_id`, `kind`, `estimated_peak_bytes`, `priority`, `timeout`, `preemption_allowed`.
2. Governor рассчитывает:
   - `estimate = p95_peak + 10% fragmentation + 1 GB driver overhead`.
   - `used = sum(active resident granted + active compute granted)`.
   - `available = pool.capacity - pool.safety_margin - used`.
3. Если `kind == compute` и есть активный compute — задача в queue, кроме случая preempt.
4. Если `estimate <= available` — grant lease, token, `expires_at`, event.
5. Если не хватает:
   - найти leases с `priority < request.priority`;
   - отправить revoke с grace 5–15 sec;
   - если grace истёк и `preemption_allowed=true` — kill process, release lease;
   - повторить проверку.
6. Если не хватает даже после preempt — queue с причиной:
   - `insufficient_memory`,
   - `gpu_busy`,
   - `owner_priority_locked`,
   - `license_block`,
   - `hard_stop`.
7. При grant:
   - для Ollama: установить/продлить `keep_alive`;
   - для sd-cli/LTX/Wan: запустить через adapter с `lease_token`;
   - начать memory sampling.
8. При runtime:
   - если actual > granted + 1 GB — request expansion или preempt;
   - если free < 3 GB — emergency stop lowest priority non-owner task;
   - если task done/cancel/timeout — release lease.
9. При release:
   - mark released;
   - если нет active resident lease для Ollama — unload.

Приоритеты:
- owner interactive: 100
- Jeff answer: 90
- paid Studio order: 80
- background generation: 50
- research/self-improvement: 20

#### Ollama keep_alive

- Governor owns keep_alive policy.
- Для Jeff/Gemma:
  - во время owner chat/Jeff task: `keep_alive = lease.duration + 5 min`.
  - после task: `keep_alive = 10 min`, но только если memory permits.
- Перед выдачей compute lease:
  - если resident Jeff/Gemma мешает, Governor вызывает unload.
  - Adapter: `POST /api/generate` или `POST /api/chat` с `keep_alive: 0` и минимальным запросом; либо `ollama stop <model>`, если CLI-путь надёжнее.
  - После unload — poll `/api/ps`, ждать отсутствия модели.
  - Если модель не выгрузилась за timeout — lease denied, не запускать sd-cli.
- Нельзя использовать `keep_alive: -1` или «keep forever».
- Если клиент сам перезагрузил модель вне lease — Governor видит через `/api/ps`, помечает `unauthorized_resident`, требует release/reload через lease.

#### Рестарт

- Все leases/tasks/events в journal.
- На startup:
  1. Load pool, consumers, tasks, leases.
  2. Active leases → `orphaned`.
  3. Scan processes: sd-cli, Ollama, LTX, RIFE, Wan.
  4. Если процесс жив и имеет валидный `lease_token` — reattach, state `running`.
  5. Если процесс жив без token — kill или mark `unknown_process`, owner approval.
  6. Если процесс мёртв — release lease, task → `failed/orphaned`.
  7. Ollama `/api/ps`: все загруженные модели без resident lease — unload или mark unauthorized.
  8. Pool state = `degraded` до preflight probe.
  9. Не запускать background tasks автоматически.
  10. Paid order tasks require owner action.

---

### 4. Порядок первых 5 задач для кодинга

1. **Governor MVP: pool, leases, events, API, SQLite.**  
   **Done:** `GET /api/governor/state`, `POST /api/governor/lease`, `DELETE /api/governor/lease/{id}` работают. Unit tests: сумма granted leases ≤ capacity - safety; queue при нехватке; preempt по приоритету; отказ без падения. Live test: Jeff loaded + sd-cli request → unload → grant → release.

2. **Ollama adapter + keep_alive/unload/reload detection.**  
   **Done:** adapter умеет `ps`, `extend_keep_alive`, `unload`, `wait_unloaded`, `detect_unauthorized_model`. Test: Gemma loaded → unload → `/api/ps` empty → no reload without lease. Restart: orphaned resident lease marked, unauthorized model unloaded or flagged.

3. **Task adapter contract: sd-cli/LTX/RIFE with lease token, cancel, timeout, memory sampling.**  
   **Done:** каждый adapter запускается только с `lease_token`; умеет start, poll, cancel, kill process tree, release lease, emit events. Test: generation killed by timeout/cancel/emergency stop, no orphan process, lease released, reason logged.

4. **Studio MVP: one preset `product_spin`, order state machine, QC, owner approval, delivery, cost receipt.**  
   **Done:** manual order create → preflight → generate → contact sheet/QC → owner approve → archive/link delivery → receipt with seconds, energy, margin, license check. Test: fake order passes full flow ≤48h; no faces/voices; AI label/watermark present; license checklist blocks if false.

5. **Security/legal gate + minimal pulse: `/gov`, `/studio`, STOP, client token, secret scan, redirect/DNS fixes.**  
   **Done:** client A cannot access client B; personal data not sent to cloud; secrets scanned before patch/context; STOP cancels active leases/tasks; site_audit redirect/DNS tests pass; license/consent gate blocks delivery. Test: security regression suite green; one pilot order can be processed manually with audit trail.