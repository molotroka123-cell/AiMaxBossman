from __future__ import annotations

import sqlalchemy as sa

from ..db import Database, fetch_one, providers as providers_t, models as models_t, utcnow
from ..secrets import Vault
from .openrouter_ext import OpenRouterClient
from .tables import provider_catalog_models

class OpenRouterCatalogService:
    def __init__(self, db: Database, vault: Vault):
        self.db = db
        self.vault = vault

    async def _provider(self, provider_id: int) -> dict:
        async with self.db.session() as s:
            row = await fetch_one(s, providers_t, provider_id)
        if row is None:
            raise LookupError("provider not found")
        return row

    async def sync(self, provider_id: int) -> dict:
        provider = await self._provider(provider_id)
        key = self.vault.decrypt(provider.get("api_key_enc"))
        if not key:
            raise ValueError("OpenRouter api_key is required")
        base = provider.get("base_url") or "https://openrouter.ai/api/v1"
        client = OpenRouterClient(key, base_url=base)
        cards = await client.list_models()
        now = utcnow()

        remote_ids = [c.id for c in cards]
        async with self.db.session() as s:
            # Mark prior rows stale first; rows seen below are reactivated.
            await s.execute(
                sa.update(provider_catalog_models)
                .where(provider_catalog_models.c.provider_id == provider_id)
                .values(stale=True, last_synced_at=now)
            )
            for c in cards:
                values = dict(
                    display_name=c.name,
                    context_window=c.context_length,
                    price_in=c.price_in,
                    price_out=c.price_out,
                    input_modalities=c.input_modalities,
                    output_modalities=c.output_modalities,
                    supported_parameters=c.supported_parameters,
                    architecture=c.architecture,
                    advertised_caps=c.advertised_caps,
                    raw_metadata=c.raw,
                    stale=False,
                    last_synced_at=now,
                )
                existing = await s.execute(
                    sa.select(provider_catalog_models.c.id).where(
                        provider_catalog_models.c.provider_id == provider_id,
                        provider_catalog_models.c.remote_id == c.id,
                    )
                )
                row_id = existing.scalar_one_or_none()
                if row_id is None:
                    await s.execute(
                        sa.insert(provider_catalog_models).values(
                            provider_id=provider_id, remote_id=c.id, **values
                        )
                    )
                else:
                    await s.execute(
                        sa.update(provider_catalog_models)
                        .where(provider_catalog_models.c.id == row_id)
                        .values(**values)
                    )
            stale_left = await s.execute(
                sa.select(sa.func.count()).select_from(provider_catalog_models).where(
                    provider_catalog_models.c.provider_id == provider_id,
                    provider_catalog_models.c.stale.is_(True),
                )
            )
            stale_count = int(stale_left.scalar_one() or 0)
            await s.commit()
        # Модели, исчезнувшие из remote, остаются строками со stale=True: закреплённые
        # алиасы и история проб не удаляются, но каталог честно помечает их устаревшими.
        return {"provider_id": provider_id, "synced": len(cards),
                "remote_ids": len(remote_ids), "stale": stale_count}

    async def list_catalog(self, provider_id: int, *, search: str = "", stale: bool | None = False,
                           limit: int = 500) -> list[dict]:
        async with self.db.session() as s:
            stmt = (
                sa.select(provider_catalog_models)
                .where(provider_catalog_models.c.provider_id == provider_id)
                .order_by(provider_catalog_models.c.display_name)
                .limit(min(max(limit, 1), 2000))
            )
            if stale is not None:
                stmt = stmt.where(provider_catalog_models.c.stale == stale)
            if search:
                q = f"%{search.lower()}%"
                stmt = stmt.where(
                    sa.or_(
                        sa.func.lower(provider_catalog_models.c.remote_id).like(q),
                        sa.func.lower(provider_catalog_models.c.display_name).like(q),
                    )
                )
            res = await s.execute(stmt)
            return [dict(r._mapping) for r in res.fetchall()]

    async def pin(self, provider_id: int, remote_id: str, *, alias: str | None = None) -> dict:
        async with self.db.session() as s:
            res = await s.execute(
                sa.select(provider_catalog_models).where(
                    provider_catalog_models.c.provider_id == provider_id,
                    provider_catalog_models.c.remote_id == remote_id,
                )
            )
            cat = res.first()
            if cat is None:
                raise LookupError("remote model not found in synchronized catalog")
            c = dict(cat._mapping)
            existing = await s.execute(sa.select(models_t).where(models_t.c.alias == (alias or remote_id)))
            if existing.first() is not None:
                raise ValueError("model alias already exists")
            inserted = await s.execute(sa.insert(models_t).values(
                provider_id=provider_id,
                name=remote_id,
                alias=alias or remote_id,
                kind="cloud",
                context_window=c.get("context_window") or 8192,
                caps=c.get("advertised_caps") or {},
                price_in=c.get("price_in") or 0.0,
                price_out=c.get("price_out") or 0.0,
                status="unknown",
            ))
            mid = int(inserted.inserted_primary_key[0])
            await s.commit()
            row = await fetch_one(s, models_t, mid)
            return row or {}
