1. **`tools/resource_governor/leases.py:793` — recovery падает на живой корректной аренде.**  
   `self._set(c, lease, epopoch, ...)`: переменной `epopoch` нет, будет `NameError`; вся транзакция `recover()` откатится, включая обработку остальных аренд.  
   **Фикс:** `self._set(c, lease, epoch=epoch, heartbeat_at=..., ...)`.

2. **`leases.py:594, 608–622` (также `tick`:707) — гонка из-за устаревшего `available_gb`.**  
   Два запроса читают одинаковые 50 ГБ свободными; первый получает аренду и начинает выделять память, второй ждёт write-lock, но затем проверяет всё те же 50 ГБ. При достаточном бюджете обе аренды выдаются, хотя live-проверка вторую должна отклонить.  
   **Минимальный фикс:** повторно вызывать `_read_mem()` уже внутри `_tx()`, непосредственно перед `plan()`/`_promote()`, а не использовать значение, прочитанное до ожидания блокировки.

3. **`leases.py:597–605` — unload использует устаревший `keep`.**  
   Пока запрос A выполняет pre-plan и выгрузку, запрос B может получить аренду на Gemma. Если модель не попала в ранее вычисленный `keep` A, выгрузчик удалит уже арендованную модель.  
   **Минимальный фикс:** сериализовать через SQLite write-lock весь участок «вычислить keep → unload → перечитать память → решение»; либо ввести общий DB-механизм unload-in-progress, который `request()` обязан учитывать.

Тест для №1 — падает сейчас и проходит после исправления:

```python
from tools.resource_governor.leases import LeaseLedger, lease_env_token


def test_recover_readopts_verified_live_process(tmp_path):
    ledger = LeaseLedger(
        tmp_path / "resource-leases.db",
        clock=lambda: 1_000.0,
    )
    try:
        granted = ledger.request(
            "gemma_chat",
            "owner_interactive",
            holder="owner",
            pid=4242,
            proc_create_time=123.25,
        )
        assert granted["state"] == "granted"

        def probe(pid):
            assert pid == 4242
            return {
                "create_time": 123.25,
                "env_token": lease_env_token(granted["id"], granted["token"]),
                "env_readable": True,
            }

        result = ledger.recover(probe)

        assert result["readopted"] == [granted["id"]]
        recovered = ledger.get(granted["id"])
        assert recovered["state"] == "granted"
        assert recovered["epoch"] == 1
        assert recovered["pid"] == 4242
    finally:
        ledger.close()
```