---

### 1. 5 самых серьёзных пробелов / рисков — с исправлением

| # | Риск / Пробел | Почему это убьёт сроки или деньги | Конкретное исправление (внеси в ТЗ v2) |
|---|---------------|-----------------------------------|----------------------------------------|
| 1 | **M1 Governor не имеет авторитетного источника истины о памяти** — «оценка пика из замеров» — это гадание. 120 ГБ shared VRAM/DRAM на AMD 8060S фрагментируется, драйвер может не отдать блок, OOM-киллер убьёт процесс до срабатывания твоего сторожа. | Jeff + LTX = детерминированный краш. Ты не узнаешь *реальный* working set до запуска. | **Внедри «dry-run» аренды**: перед выдачей lease Governor запускает потребителя с `--dry-run --mem-probe` (или `mmap(MAP_POPULATE)` на нужный объём) и меряет `RSS`/`VRAM` через `rocm-smi`/`nvidia-smi` (amdgpu-top). Только после успешного пробы — выдаёт lease. Добавь в API `POST /lease` поле `probe: true`. |
| 2 | **Ollama `keep_alive=0` не гарантирует мгновенное освобождение VRAM** — модель остаётся в GPU-кэше до следующего запроса или таймаута драйвера. Governor считает память свободной, запускает LTX → OOM. | «Jeff не перезагружает Gemma» — ложь, если VRAM не вернулась. | **Явный `POST /api/unload` к Ollama + `wait_for_vram_free(model_id, timeout=30s)`** в Governor. Если за 30с VRAM не упала до базовой — Governor помечает lease как `BLOCKED`, шлёт алерт в пульт, не запускает следующую задачу. Добавь в реестр потребителей поле `unload_hook: "ollama_unload"`. |
| 3 | **M2 Studio Pipeline: нет контракта качества (QC) для `pose_dance`** — «эвристики размытия/двоения» — субъективно. Клиент заплатит, получит артефакты, вернёт деньги, репутация = 0. | Первый платный заказ — единственная метрика успеха 2.5. Без измеримого QC — нет повторных заказов. | **Зафиксируй числовые пороги в ТЗ v2**: `blur_score < 0.15 (Laplacian var)`, `temporal_consistency > 0.92 (LPIPS между соседними кадрами)`, `pose_deviation < 5° (DWPose keypoints vs driver)`. Авто-QC падает → задача в `NEEDS_HUMAN_REVIEW`, владелец видит метрики, а не просто «плохо/хорошо». |
| 4 | **M3 Coding Swarm: нет изоляции контекста и секретов** — «скан на секреты» вручную не сработает при 50+ вызовах в день. Kimi/Qwen увидят `.env`, ключи tailnet, личные фото в репо. | Утечка приватности = конец проекта. Правило «личное не уходит в облако» нарушается архитектурно. | **Введи `ContextSanitizer` как обязательный мидлварь перед любым облачным вызовом**: (1) `git ls-files` → белый список путей (`src/`, `tests/`, `docs/tech/`), (2) `ripgrep` по паттернам секретов (`sk-`, `tailnet`, `api_key`, `password`), (3) стриппинг комментариев с TODO/FIXME содержащими данные. Только санитизированный патч уходит в LLM. Добавь в M3 как `tools/context_sanitizer.py` + unit-тесты на утечки. |
| 5 | **Порядок работ: M3 (Coding Swarm) до M2 (Pipeline) — инверсия приоритетов**. Ты тратишь недели на инфраструктуру кодинга, пока нет ни одного платного заказа, прошедшего M2. | Бизнес-метрика «≥1 оплаченный пилот» блокируется M3. | **Переставь: M2 → M1 → M4 → M5 → M3**. M3 делай *после* первого платного заказа (dogfooding: Bossman пишет фиксы для M2). В ТЗ v2 перенеси M3 в «Phase 2 (post-revenue)». |

---

### 2. Что убрать / отложить — чтобы быстрее к первому платному заказу

| Убираем / Откладываем | Обоснование | Что делаем вместо этого (минимум) |
|----------------------|-------------|-----------------------------------|
| **M3 Coding Swarm (полностью)** | Не приносит денег напрямую. Сложность интеграции 4+ LLM провайдеров, бюджетов, квитанций — недели работы. | Оставь **локальный ревьюер** (Gemma/Qwen через Ollama) для генерации тестов и линтинга. Облачных кодеров подключи *после* первого чека. |
| **M4 Obliterated local brain (bench, выбор модели)** | «Лучшая uncensored модель» — субъективно. Текущая Gemma/Qwen уже работают. Бенчмарки съедают VRAM и время. | **Заморозь текущую модель чата** (та, что сейчас в проде). Смени только если владелец явно скажет «тупой». |
| **M5 Паритет поверхностей (Telegram `/niche`, `/mem`, `/calls`)** | Не нужно для Studio заказа. Пульт владельца уже есть (мобильный + TG базовый). | **Только `/gov` (M1) и `/studio` (M2) в TG**. Остальное — бэклог. |
| **M6 Безопасность (F6, F7, F10, F5)** | Хвосты аудита. Не блокируют генерацию видео и приём денег. | **Только «живое нажатие STOP на свободном ПК» (hardware kill-switch / `pkill -9` по хоткею)**. Остальное — технический долг с тикетами. |
| **LTX-2.5 коммерческая лицензия — «проверить до первого клиента»** | Риск: лицензия запретит коммерцию. | **Проверь прямо сейчас (до кода)**. Если нельзя — выкинь `product_spin`/`hand_reveal`/`lifestyle_pushin`, оставь только `pose_dance` (Wan VACE — Apache 2.0). Не пиши код под модель, которую не можешь продать. |
| **RIFE апскейл 576→720** | Доп. VRAM, время, артефакты. | **Генерируй сразу 720×1280 на LTX** (если влезает после M1 probe) или дропни пресет. RIFE — отдельный таск после ревеню. |

---

### 3. M1 Resource Governor — конкретный дизайн

#### 3.1 Структуры данных (Pydantic + SQLite/WAL для персистентности)

```python
# src/governor/models.py
from enum import IntEnum
from pydantic import BaseModel, Field
from typing import Literal, Optional
import time

class Priority(IntEnum):
    OWNER_INTERACTIVE = 100   # чат/фото по запросу
    JEFF_RESPONSE     = 80    # ответ Jeff
    STUDIO_ORDER      = 60    # платный заказ
    BACKGROUND_GEN    = 40    # фоновые генерации
    RESEARCH          = 20    # самоулучшение

class ConsumerKind(str, Enum):
    OLLAMA_CHAT       = "ollama_chat"       # Jeff/Gemma
    OLLAMA_EMBED      = "ollama_embed"
    SD_CLI            = "sd_cli"            # фото
    LTX_VIDEO         = "ltx_video"
    WAN_VACE          = "wan_vace"
    TRAINING          = "training"

class ConsumerSpec(BaseModel):
    kind: ConsumerKind
    peak_vram_gb: float          # из dry-run пробы
    peak_ram_gb: float
    unload_hook: Optional[str]   # "ollama_unload", "sd_cli_exit", null
    min_interval_sec: int = 300  # защита от флаппинга

class LeaseState(str, Enum):
    PENDING      = "pending"      # ждёт ресурсов
    PROBING      = "probing"      # dry-run
    ACTIVE       = "active"       # держит ресурсы
    RELEASING    = "releasing"    # выгружает модель
    EXPIRED      = "expired"      # TTL вышло
    REVOKED      = "revoked"      # вытеснен вышеприоритетной задачей

class Lease(BaseModel):
    id: str                      # uuid
    consumer: ConsumerKind
    priority: Priority
    owner: Literal["owner", "jeff", "studio", "background", "research"]
    requested_at: float = Field(default_factory=time.time)
    granted_at: Optional[float] = None
    expires_at: Optional[float] = None   # TTL от потребителя
    state: LeaseState = LeaseState.PENDING
    probe_passed: bool = False
    vram_allocated_gb: float = 0
    ram_allocated_gb: float = 0
```

**Персистентность**: SQLite `governor.db` (WAL mode) — таблицы `consumers`, `leases`, `events`. При рестарте Governor читает `leases WHERE state IN ('ACTIVE','PROBING','RELEASING')` → вызывает `unload_hook` для каждого → помечает `REVOKED` → стартует чистый.

#### 3.2 Алгоритм выдачи аренды (lease grant)

```python
# src/governor/core.py
async def try_grant(lease: Lease) -> bool:
    # 1. Считаем текущие коммиты
    committed_vram = sum(l.vram_allocated_gb for l in active_leases)
    committed_ram  = sum(l.ram_allocated_gb  for l in active_leases)

    # 2. Лимиты (константы из конфига)
    VRAM_LIMIT = TOTAL_VRAM_GB - 8.0   # запас 8 ГБ
    RAM_LIMIT  = TOTAL_RAM_GB  - 4.0

    # 3. Проверка пика + коммитов
    if (committed_vram + lease.consumer_spec.peak_vram_gb > VRAM_LIMIT or
        committed_ram  + lease.consumer_spec.peak_ram_gb  > RAM_LIMIT):
        return False  # не влезает

    # 4. Правило «одна тяжёлая GPU-задача»
    heavy_kinds = {ConsumerKind.LTX_VIDEO, ConsumerKind.WAN_VACE, ConsumerKind.SD_CLI, ConsumerKind.TRAINING}
    active_heavy = [l for l in active_leases if l.consumer in heavy_kinds]
    if lease.consumer in heavy_kinds and active_heavy:
        # Есть ли Jeff/Gemma?
        jeff_lease = next((l for l in active_leases if l.consumer == ConsumerKind.OLLAMA_CHAT), None)
        if jeff_lease and (committed_vram + lease.consumer_spec.peak_vram_gb > VRAM_LIMIT):
            return False  # Jeff + новая тяжёлая > лимит
        # Иначе вытесняем фоновую тяжёлую (есть ниже по приоритету)
        for victim in sorted(active_heavy, key=lambda x: x.priority):
            if victim.priority < lease.priority:
                await revoke(victim, reason="preempted_by_higher_priority")
            else:
                return False  # нет жертв с ниже приоритетом

    # 5. Всё ок — переходим в PROBING
    lease.state = LeaseState.PROBING
    await persist(lease)
    return True
```

#### 3.3 Dry-run проба (обязательная для тяжёлых)

```python
async def run_probe(lease: Lease) -> bool:
    spec = lease.consumer_spec
    if spec.kind not in {ConsumerKind.LTX_VIDEO, ConsumerKind.WAN_VACE, ConsumerKind.SD_CLI, ConsumerKind.TRAINING}:
        lease.probe_passed = True
        return True

    # Запускаем потребителя в probe-режиме (подкласс должен поддерживать --mem-probe)
    proc = await asyncio.create_subprocess_exec(
        spec.probe_cmd, "--mem-probe", f"--alloc-vram={spec.peak_vram_gb}G",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        await asyncio.wait_for(proc.wait(), timeout=60)
    except asyncio.TimeoutError:
        proc.kill()
        return False

    # Читаем реальный пик из stdout (JSON: {"peak_vram_gb": 14.2, "peak_ram_gb": 6.1})
    real = json.loads((await proc.stdout.read()).decode())
    # Обновляем спеку (kalman/ewma сглаживание)
    spec.peak_vram_gb = max(spec.peak_vram_gb, real["peak_vram_gb"] * 1.1)  # 10% запас
    spec.peak_ram_gb  = max(spec.peak_ram_gb,  real["peak_ram_gb"]  * 1.1)
    await persist(spec)

    lease.probe_passed = True
    lease.vram_allocated_gb = spec.peak_vram_gb
    lease.ram_allocated_gb  = spec.peak_ram_gb
    return True
```

#### 3.4 Работа с Ollama `keep_alive`

```python
# src/governor/ollama_hook.py
import httpx

OLLAMA_HOST = "http://127.0.0.1:11434"

async def unload_model(model_name: str, timeout: float = 30.0) -> bool:
    # 1. Явный unload
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(f"{OLLAMA_HOST}/api/generate",
                              json={"model": model_name, "keep_alive": 0, "prompt": ""})
        if r.status_code != 200:
            return False

    # 2. Ждём реального освобождения VRAM (amdgpu-top / rocm-smi)
    deadline = time.time() + timeout
    baseline_vram = get_baseline_vram()  # замерено при старте Governor
    while time.time() < deadline:
        current = get_current_vram_usage()
        if current - baseline_vram < 0.5:  # ГБ
            return True
        await asyncio.sleep(1.0)
    return False  # таймаут — VRAM не вернулась

async def ensure_vram_for(lease: Lease) -> bool:
    if lease.consumer == ConsumerKind.OLLAMA_CHAT:
        return True  # чат не выгружает себя
    # Находим активный чат-lease
    chat_lease = next((l for l in active_leases if l.consumer == ConsumerKind.OLLAMA_CHAT), None)
    if not chat_lease:
        return True
    # Выгружаем модель чата
    model = chat_lease.metadata.get("model_name")  # сохраняем при выдаче lease
    ok = await unload_model(model)
    if ok:
        chat_lease.state = LeaseState.RELEASING
        chat_lease.vram_allocated_gb = 0
        await persist(chat_lease)
    return ok
```

#### 3.5 Рестарт Governor (crash / update / reboot)

1. **Старт**: читает `governor.db` → `leases WHERE state IN ('ACTIVE','PROBING','RELEASING')`.
2. Для каждого:
   - Если `unload_hook` есть → вызывает хук (Ollama unload, `pkill -9` для sd-cli/LTX).
   - Логирует `event: "recovery_revoke", lease_id, reason: "governor_restart"`.
   - Помечает `state = REVOKED`.
3. Сбрасывает `committed_vram/ram = 0`.
4. Запускает фоновый таск `reconcile_loop()` каждые 10с: сверяет `active_leases` с реальными процессами (`psutil`, `rocm-smi`). Если процесс умер, а lease `ACTIVE` → `REVOKED`, ресурсы возвращены.
5. **API `/api/governor/state`** отдаёт `recovery_mode: true` до первого успешного reconcile.

---

### 4. Порядок первых 5 задач для кодинга (с DoD)

| # | Задача (файл/модуль) | DoD (Definition of Done) — чек-лист |
|---|----------------------|--------------------------------------|
| **1** | `src/governor/models.py` + `src/governor/store.py` (SQLite WAL, миграции) | ✅ Unit-тесты: CRUD Lease/ConsumerSpec, персистентность после рестарта процесса, конкурентные записи (10 параллельных тасков) без локов. ✅ `pytest -xvs tests/governor/test_store.py` зелёный. |
| **2** | `src/governor/core.py` — `try_grant`, `revoke`, `reconcile_loop` + dry-run probe для LTX/SD-CLI | ✅ Property-based тест (hypothesis): «сумма пиков активных аренд ≤ лимит» на 1000 случайных последовательностей. ✅ Интеграционный тест: запуск Governor + 2 параллельных `ltx_video --mem-probe` → только один получает ACTIVE, второй PENDING с причиной `VRAM_LIMIT`. ✅ Лог в `governor.db.events` содержит `grant`, `revoke`, `probe_passed`. |
| **3** | `src/governor/ollama_hook.py` + интеграция в `core.ensure_vram_for` | ✅ E2E тест: (1) загружаем Gemma в Ollama (`keep_alive=-1`), (2) просим lease на LTX → Governor вызывает unload, ждёт VRAM < baseline+0.5ГБ, выдаёт lease. ✅ Если unload таймаут >30с → lease `BLOCKED`, алерт в пульт (мок пульта). ✅ После релиза LTX — Gemma не перезагружается автоматически (проверка логов Ollama). |
| **4** | `src/api/governor.py` — FastAPI роутеры `GET /state`, `POST /lease`, `DELETE /lease/{id}` + авторизация (owner-only) | ✅ OpenAPI схема валидна. ✅ Тест: мобильный пульт (мок) запрашивает `/state` каждые 2с — latency < 50мс, нет утечек памяти за 10к запросов. ✅ `POST /lease` с `priority=OWNER_INTERACTIVE` вытесняет `BACKGROUND_GEN` за < 2с (включая unload). |
| **5** | `src/studio/pipeline.py` — пресеты `product_spin`, `hand_reveal`, `lifestyle_pushin`, `pose_dance` + авто-QC (blur, LPIPS, pose_dev) | ✅ Каждый пресет: конфиг (разрешение, модели, параметры) + `run(order_id) -> Artifact[]`. ✅ Авто-QC: функция `qc_check(frames: list[np.ndarray]) -> QCReport` с порогами из ТЗ (blur<0.15, LPIPS>0.92, pose_dev<5°). ✅ Интеграционный тест: прогон `pose_dance` на 8-кадровом драйвере → артефакты сохраняются в `studio/orders/{id}/qc/`, отчёт JSON. ✅ Владелец в пульте видит карточку заказа с превью, метриками QC, кнопками «Принять / В переработку». |

---

**Итог**: ТЗ v2 должен зафиксировать — **M3 в Phase 2, LTX лицензия проверена до кода, Governor с dry-run и Ollama unload-хуком, QC с цифрами**. Начинай кодить с задачи 1.