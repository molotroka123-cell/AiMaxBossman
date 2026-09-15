"""Model Registry: провайдеры и модели, health-check и мини-benchmark.

Ключи хранятся шифрованными; во все ответы уходит только маска (раздел 8).
"""
from __future__ import annotations

import time
from typing import Any

import sqlalchemy as sa

from .db import Database, fetch_one, models as models_t, providers as providers_t, rows_dicts, utcnow
from .events import EventBus
from .providers import ADAPTERS, ChatResult, ProviderAdapter, ProviderError, build_adapter
from .fable_cap import capped
from .secrets import Vault, mask

BENCH_PROMPT = "Ответь одним словом: работаешь?"


class Registry:
    """CRUD реестра + операции check/test над конкретной моделью."""

    def __init__(self, db: Database, vault: Vault, bus: EventBus,
                 adapter_factory: Any = None):
        self.db = db
        self.vault = vault
        self.bus = bus
        # фабрика адаптеров подменяется в тестах: (model_row, provider_row) -> ProviderAdapter
        self.adapter_factory = adapter_factory or self._default_adapter

    # ---------- провайдеры ----------

    def provider_public(self, row: dict) -> dict:
        out = {k: v for k, v in row.items() if k != "api_key_enc"}
        out["api_key_masked"] = mask(self.vault.decrypt(row.get("api_key_enc")))
        return out

    async def list_providers(self) -> list[dict]:
        async with self.db.session() as s:
            res = await s.execute(sa.select(providers_t).order_by(providers_t.c.id))
            return [self.provider_public(r) for r in rows_dicts(res.fetchall())]

    async def create_provider(self, name: str, kind: str, base_url: str = "",
                              api_key: str | None = None) -> dict:
        if kind not in ADAPTERS:
            raise ProviderError(f"неизвестный вид провайдера: {kind}",
                                hint=f"доступны: {', '.join(ADAPTERS)}")
        async with self.db.session() as s:
            res = await s.execute(sa.insert(providers_t).values(
                name=name, kind=kind, base_url=base_url or "",
                api_key_enc=self.vault.encrypt(api_key), created_at=utcnow()))
            pid = int(res.inserted_primary_key[0])
            await s.commit()
            row = await fetch_one(s, providers_t, pid)
        await self.bus.emit("provider.created", id=pid, name=name, provider_kind=kind)
        return self.provider_public(row or {})

    async def delete_provider(self, provider_id: int) -> bool:
        async with self.db.session() as s:
            res = await s.execute(sa.delete(providers_t).where(providers_t.c.id == provider_id))
            await s.commit()
        ok = bool(res.rowcount)
        if ok:
            await self.bus.emit("provider.deleted", id=provider_id)
        return ok

    # ---------- модели ----------

    async def list_models(self) -> list[dict]:
        async with self.db.session() as s:
            res = await s.execute(sa.select(models_t).order_by(models_t.c.id))
            return rows_dicts(res.fetchall())

    async def get_model(self, model_id: int) -> dict | None:
        async with self.db.session() as s:
            return await fetch_one(s, models_t, model_id)

    async def create_model(self, **values: Any) -> dict:
        values = {k: v for k, v in values.items() if v is not None or k in ("price_in", "price_out")}
        from .provider_governance import known_prices
        values.setdefault("price_in", None)
        values.setdefault("price_out", None)
        values["pricing_known"] = known_prices(values)
        values.setdefault("alias", values.get("name"))
        async with self.db.session() as s:
            provider = await fetch_one(s, providers_t, int(values["provider_id"]))
            if provider is None:
                raise LookupError("провайдер не найден")
            res = await s.execute(sa.insert(models_t).values(**values))
            mid = int(res.inserted_primary_key[0])
            await s.commit()
            row = await fetch_one(s, models_t, mid)
        await self.bus.emit("model.created", id=mid, alias=values.get("alias"))
        return row or {}

    async def update_model(self, model_id: int, **values: Any) -> dict | None:
        values = {k: v for k, v in values.items() if v is not None or k in ("price_in", "price_out")}
        async with self.db.session() as s:
            if "price_in" in values or "price_out" in values:
                from .provider_governance import known_prices
                prior = await fetch_one(s, models_t, model_id)
                values["pricing_known"] = known_prices({**(prior or {}), **values})
            if values:
                await s.execute(sa.update(models_t).where(models_t.c.id == model_id).values(**values))
                await s.commit()
            return await fetch_one(s, models_t, model_id)

    async def delete_model(self, model_id: int) -> bool:
        async with self.db.session() as s:
            res = await s.execute(sa.delete(models_t).where(models_t.c.id == model_id))
            await s.commit()
        return bool(res.rowcount)

    # ---------- адаптеры ----------

    def _default_adapter(self, model: dict, provider: dict) -> ProviderAdapter:
        return build_adapter(provider["kind"], provider.get("base_url") or "",
                             self.vault.decrypt(provider.get("api_key_enc")))

    async def adapter_for(self, model_id: int) -> tuple[ProviderAdapter, dict]:
        """Адаптер и строка модели; расшифрованный ключ дальше этой функции не уходит."""
        async with self.db.session() as s:
            model = await fetch_one(s, models_t, model_id)
            if model is None:
                raise LookupError(f"модель {model_id} не найдена")
            provider = await fetch_one(s, providers_t, model["provider_id"])
            if provider is None:
                raise LookupError(f"провайдер модели {model_id} не найден")
        # Платная граница Fable оборачивается ЗДЕСЬ, а не в движке: через
        # adapter_for ходят все — движок, benchlab, ревью-гейт, веб-поиск,
        # проверка и тест модели, — и обойти обёртку значит обойти потолок.
        # Обёртка ставится ПОСЛЕ фабрики, поэтому подменённая фабрика (тесты,
        # плагины) от потолка тоже не освобождает.
        from .provider_governance import GovernedAdapter
        return GovernedAdapter(capped(self.adapter_factory(model, provider), provider, model), provider, model), model

    # ---------- проверки ----------

    async def check_model(self, model_id: int) -> dict:
        """Health endpoint'а провайдера → status/status_detail/last_check модели.

        B5: это проверка ДОСТУПНОСТИ ПРОВАЙДЕРА, а не способности модели
        отвечать. Она больше не пишет измеренное здоровье: живой эндпоинт
        ничего не говорит про то, вернёт ли эта модель хоть слово (ровно так
        `cohere/north-mini-code:free` и считался пригодным). Умение отвечать
        измеряет `test_model`, и только оно ставит health=healthy."""
        adapter, model = await self.adapter_for(model_id)
        health = await adapter.health()
        status = {"ok": "online"}.get(health.status, health.status)
        await self._set_status(model_id, status, health.detail)
        if health.status != "ok":
            # Недоступный провайдер — это факт и про модель тоже: она сейчас не
            # ответит. Обратное неверно, поэтому «ok» здесь ничего не записывает.
            from . import model_health as mh
            kind = mh.UNAUTHORIZED if "401" in (health.detail or "") or "403" in (
                health.detail or "") else mh.PROVIDER_DOWN
            await self.record_model_health(model_id, kind, health.detail or status)
        await self.bus.emit("model.status", id=model_id, alias=model["alias"],
                            status=status, detail=health.detail)
        return {"id": model_id, "status": status, "detail": health.detail,
                "latency_ms": health.latency_ms, "last_check": utcnow(),
                "health": (await self.model_health(model_id)).to_dict()}

    # ---------- B5: измеренное здоровье модели ----------

    async def model_health(self, model_id: int):
        from . import model_health as mh
        async with self.db.session() as s:
            row = (await s.execute(sa.select(models_t.c.health).where(
                models_t.c.id == model_id))).first()
        return mh.HealthRecord.from_dict(row[0] if row is not None else None)

    async def record_model_health(self, model_id: int, status: str, detail: str = "",
                                  *, latency_ms: int | None = None):
        """Сложить одно измерение в запись здоровья и сохранить её."""
        from . import model_health as mh
        prior = await self.model_health(model_id)
        record = mh.record_observation(prior, status, detail, latency_ms=latency_ms)
        async with self.db.session() as s:
            await s.execute(sa.update(models_t).where(models_t.c.id == model_id).values(
                health=record.to_dict()))
            await s.commit()
        await self.bus.emit("model.health", id=model_id, health_status=status,
                            detail=detail[:300], confidence=record.confidence,
                            usable=record.usable())
        return record

    async def usable_models(self, *, kind: str | None = None) -> list[dict]:
        """Модели в порядке ИЗМЕРЕННОГО здоровья: доказанные, затем неизмеренные,
        затем сломанные. Неизмеренная НЕ считается здоровой (иначе молчащая
        модель обгоняет проверенную), но и не считается сломанной — иначе её
        никогда не выберут и здоровье никогда не измерится."""
        from . import model_health as mh
        stmt = sa.select(models_t)
        if kind:
            stmt = stmt.where(models_t.c.kind == kind)
        async with self.db.session() as s:
            rows = [dict(r._mapping) for r in (await s.execute(stmt)).fetchall()]
        pairs = [(row, mh.HealthRecord.from_dict(row.get("health"))) for row in rows]
        ordered = mh.rank(pairs)
        by_id = {row["id"]: rec for row, rec in pairs}
        return [{**row, "health": by_id[row["id"]].to_dict()} for row in ordered]

    async def test_model(self, model_id: int) -> dict:
        """Мини-benchmark: короткий prompt, замер latency и tok/s; результат — в bench."""
        adapter, model = await self.adapter_for(model_id)
        t0 = time.perf_counter()
        try:
            result: ChatResult = await adapter.chat(
                model["name"], [{"role": "user", "content": BENCH_PROMPT}], max_tokens=32)
        except ProviderError as exc:
            status = "offline" if exc.kind == "network" else "error"
            await self._set_status(model_id, status, str(exc))
            from . import model_health as mh
            kind, detail = mh.classify_exception(exc)
            await self.record_model_health(model_id, kind, detail)
            await self.bus.emit("model.status", id=model_id, alias=model["alias"],
                                status=status, detail=str(exc))
            raise
        elapsed = max(time.perf_counter() - t0, 1e-6)
        # B5: вызов, который не бросил исключение, ещё не ответ. HTTP 200 с
        # пустым телом не бросает ничего — так молчащая бесплатная модель и
        # записывалась как online. Классифицируем сам ОТВЕТ.
        from . import model_health as mh
        kind, detail = mh.classify_answer(result.text, tokens_out=result.tokens_out)
        record = await self.record_model_health(model_id, kind, detail,
                                                latency_ms=int(elapsed * 1000))
        if kind != mh.HEALTHY:
            await self._set_status(model_id, "error", detail)
            await self.bus.emit("model.status", id=model_id, alias=model["alias"],
                                status="error", detail=detail)
            raise ProviderError(f"модель {model['alias']}: {detail}", kind="empty_response")
        bench = {
            "prompt_tps": round(result.tokens_in / elapsed, 2) if result.tokens_in else None,
            "gen_tps": round(result.tokens_out / elapsed, 2) if result.tokens_out else None,
            "latency_ms": int(elapsed * 1000),
            "answer": result.text[:200],
            "tested_at": utcnow().isoformat(),
        }
        async with self.db.session() as s:
            await s.execute(sa.update(models_t).where(models_t.c.id == model_id).values(
                bench=bench, status="online", status_detail="", last_check=utcnow()))
            await s.commit()
        await self.bus.emit("model.status", id=model_id, alias=model["alias"],
                            status="online", detail="", bench=bench)
        return {"id": model_id, "bench": bench, "health": record.to_dict()}

    async def _set_status(self, model_id: int, status: str, detail: str) -> None:
        async with self.db.session() as s:
            await s.execute(sa.update(models_t).where(models_t.c.id == model_id).values(
                status=status, status_detail=detail or "", last_check=utcnow()))
            await s.commit()
