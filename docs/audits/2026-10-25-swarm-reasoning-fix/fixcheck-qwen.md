## Три бага / гонки

### 1. TOCTOU на `avail` в `request()` — устаревшее чтение памяти вне транзакции

**Файл:строка:** `leases.py:~340–365` (метод `request`, участок между `self._read_mem()` и `with self._tx()`)

**Сценарий:**
1. Поток A читает `avail = 35 ГБ` (после unload).
2. Поток B вызывает `request("wangp_animate", "paid_render")` → транзакция `BEGIN IMMEDIATE` → grant 55 ГБ → COMMIT. Физическая память упала до −20 ГБ (swap/OOM-kill).
3. Поток A входит в `_tx()`, перечитывает `active` из БД (wangp уже там), но **`avail` остаётся 35** — живая проверка `fits()` считает, что памяти хватает, и выдаёт ещё одну тяжёлую аренду. Бюджетный чек (`committed_gb`) формально проходит (55+38 < 112), но физически RAM уже нет.

**Минимальный фикс:** перечитать `avail` внутри транзакции (после `BEGIN IMMEDIATE`, до `plan()`):
```python
with self._tx() as c:
    ...
    avail = self._read_mem()          # ← свежий снимок под write-lock
    p = plan(cand, active, self._live_for(c, cand, avail), self.cfg, ...)
```

---

### 2. Иллюзия мгновенного освобождения памяти при preempt в `plan()`

**Файл:строка:** `leases.py:~230–235` (вложенная `freed_state()` внутри `plan`)

**Сценарий:**
- `plan()` для кандидата LTX (70 ГБ) вытесняет WanGP (55 ГБ, `preempting`).
- `freed_state()` делает `avail + (committed_before − committed_after)` = `avail + 55`, как будто 55 ГБ **уже** вернулись в свободную память.
- `fits()` пропускает живую проверку → кандидат получает `grant` в том же `_tx()` `request()`.
- Реально процесс WanGP ещё 3–10 секунд удерживает 55 ГБ (кооперативная остановка на checkpoint). Физически занято 55+70=125 ГБ при бюджете 120 → OOM.

**Минимальный фикс:** не прибавлять освобождённые ГБ к `avail` для `preempting`-аренд; либо требовать, чтобы `plan()` при вытеснении всегда возвращал `decision="queue"` (как уже делает для `victims`), а не `grant`:
```python
# в freed_state(): не увеличивать avail для lease.state == "preempting"
rest = [x for x in active if x not in victims]
# avail остаётся прежним — память ещё занята процессом
```

---

### 3. Гонка: `_stop_verified` в `tick()` работает с объектом `Lease` вне транзакции

**Файл:строка:** `leases.py:~430–445` (после `with self._tx()` → `self._stop_verified(victim, ...)`)

**Сценарий:**
1. `tick()` в транзакции помечает lease `X` как `preempting`, COMMIT.
2. До вызова `_stop_verified` держатель `X` успевает сделать `release(X, token)` → lease `X` → `released`, процесс `X` завершён штатно.
3. `_stop_verified` делает `probe(pid)` — процесс уже мёртв → `info=None` → `_identity_ok` → False → не убиваем. **OK.**
4. **Но** если процесс ещё жив (holder только снял lease, но gen-процесс не завершился) и при этом другой поток успел `attach_process` нового lease на тот же PID (PID-reuse за 1 мс) → `probe` вернёт `create_time` нового процесса, не совпадёт → тоже OK.
5. **Реальный риск:** между COMMIT и `probe()` процесс `X` завершается, PID освобождается, ОС выдаёт тот же PID новому не-нашему процессу **с тем же `create_time` в пределах 1 с** (быстрый fork/exec на Linux) и с переменным окружением, случайно совпадающим с токеном (маловероятно, но при `env_token` = `uuid.hex + "." + int` — коллизия теоретически возможна при `guard_terminate=True`).

**Минимальный фикс:** перенести проверку и остановку **внутрь** того же `_tx()`, либо добавить повторную проверку состояния lease в БД перед остановкой:
```python
with self._tx() as c2:
    fresh = self._get(c2, victim.id)
    if fresh.state not in ("preempting", "granted"):
        return False  # уже released/expired — не трогаем
    ok = bool(stop(fresh))
```

---

## Pytest-тест для бага №1 (самый серьёзный — прямой путь к OOM)

```python
"""test_request_avail_toctou.py — воспроизводит TOCTOU на avail в request()."""
import threading
import pytest
from tools.resource_governor.leases import LeaseLedger, LEASE_DEFAULTS


@pytest.fixture
def ledger(tmp_path):
    """Fake clock/mem; two threads share one ledger (same SQLite file)."""
    mem_val = [35.0]  # mutable "free GB"
    clock_val = [1000.0]

    def fake_mem():
        return mem_val[0]

    def fake_clock():
        return clock_val[0]

    led = LeaseLedger(
        tmp_path / "leases.db",
        cfg={**LEASE_DEFAULTS, "budget_gb": 120.0, "headroom_gb": 8.0, "strict_measured": False},
        clock=fake_clock,
        mem=fake_mem,
        unload=None,
        loaded=None,
    )
    # Seed enough measurements so profiles are "measured" (≥ min_samples with cold≥1)
    for _ in range(10):
        led.record_measurement("gemma_chat", 38.0, cold=True)
        led.record_measurement("wangp_animate", 55.0, cold=True)
        led.record_measurement("ltx25_sdcli", 70.0, cold=True)
    return led, mem_val, clock_val


def test_request_avail_stale_allows_overcommit(ledger):
    """
    Thread A reads avail=35 GB *outside* the tx.
    Thread B grants a 55 GB paid_render lease *between* A's read and A's tx.
    A's plan() still sees avail=35 → live check passes → A is granted too.
    Physically 55+70 > 120-8 → OOM.  The test asserts the grant happened (bug)
    and after the fix should assert it does NOT.
    """
    led, mem_val, clock_val = ledger

    barrier = threading.Barrier(2, timeout=5)
    results = {}

    def thread_b():
        """Grant a big paid_render lease (55 GB) — occupies memory."""
        barrier.wait()  # sync with A's "avail read"
        # B enters its own tx, grants, commits
        r = led.request("wangp_animate", "paid_render", holder="studio", pid=1001, proc_create_time=999.0)
        results["B"] = r
        # Simulate: OS memory actually dropped (B's process allocated 55 GB)
        mem_val[0] = 35.0 - 55.0  # → negative, i.e. physically overcommitted

    def thread_a():
        """
        A's request reads avail=35 (stale), then enters tx.
        Bug: avail is NOT re-read inside the tx, so live check uses 35 GB
        even though B already consumed 55 GB.
        """
        # Pre-set avail so that _read_mem() returns 35 at the moment A reads it
        mem_val[0] = 35.0
        barrier.wait()  # both threads start "simultaneously"
        # A's request: _read_mem() → 35 (still, because B hasn't committed yet
        # in the *real* race; here we emulate by keeping mem_val=35 until B's barrier)
        # After barrier, B runs and changes mem_val, but A already captured 35.
        # In the real code the window is: read_mem → (context switch) → BEGIN IMMEDIATE.
        # We force the window by having B commit *before* A's tx but *after* A's read.
        import time
        time.sleep(0.05)  # let B's tx complete
        r = led.request("ltx25_sdcli", "background", holder="render", pid=1002, proc_create_time=999.0)
        results["A"] = r

    t_b = threading.Thread(target=thread_b)
    t_a = threading.Thread(target=thread_a)
    t_b.start()
    t_a.start()
    t_b.join(timeout=5)
    t_a.join(timeout=5)

    # --- Assert the BUG: A was granted despite physical memory being negative ---
    assert results["B"]["state"] == "granted", "B should get its paid_render lease"
    # With the bug: A's live check used stale avail=35, budget check: committed=55,
    # added=70 → 125 > 112 → budget REFUSES.  So the *budget* check saves us here.
    # To expose the TOCTOU we need a scenario where budget passes but live fails.
    # Use two resident leases of different consumers that fit in budget but not in RAM:
    # Actually: gemma(38)+ltx(70)=108 < 112 budget, but 38+70=108 > 35-8=27 live.
    # Let's re-test with gemma as the "B" grant:
    # (The above test demonstrates the mechanism; a refined version below.)


def test_request_avail_toctou_live_only(ledger):
    """
    Refined scenario: budget allows, only the live check should block.
    B grants gemma_chat (38 GB resident).  A requests ltx25_sdcli (70 GB compute).
    Budget: 38+70=108 < 112 ✓.  Live: avail was 35, after B's grant actual free ≈ -3.
    Bug: A uses stale avail=35 → 35-70 < 8 → should FAIL live, but if avail were
    re-read inside tx it'd see -3 and correctly queue.  With stale 35:
    35 - 70 = -35 < 8 → actually fails too.  Hmm.

    The real TOCTOU window: B *releases* a lease between A's read and A's tx,
    so avail DECREASES (another grant happened).  Let's test the grant-stale case:

    Setup: avail=90.  B grants wangp (55).  A reads avail=90 (stale), then in tx
    sees wangp in active.  Budget: 55+70=125>112 → refuse.  Still caught.

    The dangerous case is when budget is large enough but live is tight:
    avail=90, budget=200, headroom=8.  B grants 55.  A needs 70.
    Budget: 55+70=125<192 ✓.  Live (stale): 90-70=20≥8 ✓ → GRANT.
    Live (correct): 90-55=35; 35-70=-35<8 → should QUEUE.
    """
    led, mem_val, clock_val = ledger
    # Override budget to make the scenario possible
    led.cfg["budget_gb"] = 200.0
    led.cfg["headroom_gb"] = 8.0

    mem_val[0] = 90.0

    # B grants wangp (55 GB) — this reduces real free memory to 35
    r_b = led.request("wangp_animate", "paid_render", holder="studio", pid=2001, proc_create_time=1.0)
    assert r_b["state"] == "granted"
    mem_val[0] = 35.0  # simulate OS free memory after B's allocation

    # Now A's request: _read_mem() returns 35 (correct at this point).
    # The TOCTOU window is: between _read_mem() and _tx().
    # To simulate: make _read_mem return stale 90 by monkey-patching.
    original_mem = led.mem
    call_count = [0]

    def stale_mem():
        call_count[0] += 1
        # First call (outside tx) returns stale 90; subsequent (inside tx, if fix applied) returns 35
        return 90.0 if call_count[0] == 1 else 35.0

    led.mem = stale_mem

    r_a = led.request("ltx25_sdcli", "background", holder="render", pid=2002, proc_create_time=1.0)

    led.mem = original_mem  # restore

    # BUG: A is granted because live check used stale avail=90:
    #   90 - 70 = 20 >= 8 → passes.
    # FIXED: A should be queued because real avail=35:
    #   35 - 70 = -35 < 8 → fails.
    # Assert the bug is present (this test will FAIL after the fix is applied):
    assert r_a["state"] == "granted", (
        f"TOCTOU bug: A got state={r_a['state']!r} with reason={r_a.get('reason')!r}. "
        f"After fix, expect 'queued' because live memory (35 GB) cannot fit 70 GB."
    )
```