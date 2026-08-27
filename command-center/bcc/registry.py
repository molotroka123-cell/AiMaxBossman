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
        values = {k: v for k, v in values.items() if v is not None}
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
        values = {k: v for k, v in values.items() if v is not None}
        async with self.db.session() as s:
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
        return self.adapter_factory(model, provider), model

    # ---------- проверки ----------

    async def check_model(self, model_id: int) -> dict:
        """Health endpoint'а провайдера → status/status_detail/last_check модели."""
        adapter, model = await self.adapter_for(model_id)
        health = await adapter.health()
        status = {"ok": "online"}.get(health.status, health.status)
        await self._set_status(model_id, status, health.detail)
        await self.bus.emit("model.status", id=model_id, alias=model["alias"],
                            status=status, detail=health.detail)
        return {"id": model_id, "status": status, "detail": health.detail,
                "latency_ms": health.latency_ms, "last_check": utcnow()}

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
            await self.bus.emit("model.status", id=model_id, alias=model["alias"],
                                status=status, detail=str(exc))
            raise
        elapsed = max(time.perf_counter() - t0, 1e-6)
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
        return {"id": model_id, "bench": bench}

    async def _set_status(self, model_id: int, status: str, detail: str) -> None:
        async with self.db.session() as s:
            await s.execute(sa.update(models_t).where(models_t.c.id == model_id).values(
                status=status, status_detail=detail or "", last_check=utcnow()))
            await s.commit()
