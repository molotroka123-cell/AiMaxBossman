"""Model Registry: провайдеры и модели, health-check и мини-benchmark.

Ключи хранятся шифрованными; во все ответы уходит только маска (раздел 8).
"""
from __future__ import annotations

import time
from typing import Any

import sqlalchemy as sa

from .db import Database, fetch_one, models as models_t, providers as providers_t, rows_dicts, utcnow
from .events import EventBus
from .providers import (ADAPTERS, ChatResult, Health, ProviderAdapter, ProviderError,
                        build_adapter)
from .fable_cap import capped
from .secrets import Vault, mask

BENCH_PROMPT = "Ответь одним словом: работаешь?"
#: status_detail prefix of an `offline` set by a failed real call (not a probe).
RUNTIME_OFFLINE_PREFIX = "runtime: "
REASONING_PROBE_TOKENS = 1024


def _same_model(served: str, wanted: str) -> bool:
    if served == wanted:
        return True
    # Ollama: `qwen3` and `qwen3:latest` are one tag.
    return served.removesuffix(":latest") == wanted.removesuffix(":latest")


async def served_model_problem(adapter: Any, name: str) -> str:
    """"" when the endpoint lists `name`, else why it does not.

    Only OpenAI-compatible catalogs (`list_model_info`) are held to this: a
    cloud API that accepts aliases it does not list (Anthropic) is not.
    Lenient for a single-model llama.cpp server (or a single-model catalog that
    does not say who owns it): it answers under whatever id it was started
    with — often the GGUF path — and serves every request with that one model.
    A server that lists several models, or names another owner, must list this
    one. A catalog we cannot read here says nothing new: health() already did."""
    info = getattr(adapter, "list_model_info", None)
    if not callable(info):
        return ""
    try:
        served = await info()
    except ProviderError:
        return ""
    ids = [str(m.get("id") or "") for m in served if isinstance(m, dict)]
    if any(_same_model(i, name) for i in ids):
        return ""
    if len(served) == 1 and served[0].get("owned_by") in (None, "llamacpp"):
        return ""
    shown = ", ".join(ids[:5]) + (" …" if len(ids) > 5 else "")
    return (f"сервер отвечает, но модели {name} в его списке нет ({shown or 'пусто'}): "
            f"на этом порту другая модель или другой сервер")


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
        from .provider_governance import FreeOnlyAdapter, GovernedAdapter, free_only_refusal
        catalog_state = (None if model.get("pricing_known")
                         else await self._catalog_state(model))
        inner = capped(self.adapter_factory(model, provider), provider, model)
        # Free-only product runtime: a cloud model that is not provably free
        # (owner-registered direct provider, priced OpenRouter model) is refused
        # on chat; health and catalog reads still work.
        refusal = free_only_refusal(provider, model)
        if refusal:
            inner = FreeOnlyAdapter(inner, refusal)
        return GovernedAdapter(inner, provider, model, catalog_state=catalog_state), model

    async def _catalog_state(self, model: dict) -> str | None:
        """Why the price is unknown, from the provider's synchronized catalog.

        Only names the reason in the governance refusal; it never admits a
        model. None when there is nothing to say (no catalog table, local)."""
        try:
            from .v2.tables import provider_catalog_models as catalog_t
            async with self.db.session() as s:
                rows = (await s.execute(sa.select(catalog_t.c.remote_id, catalog_t.c.stale).where(
                    catalog_t.c.provider_id == model["provider_id"]))).fetchall()
        except Exception:  # noqa: BLE001 — a diagnostic, never a reason to fail the call
            return None
        if not rows:
            return None                     # no catalog for this provider: nothing to add
        mine = [r for r in rows if r[0] == model.get("name")]
        if not mine:
            return "absent"
        return "stale" if mine[0][1] else None

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
        if health.status == "ok":
            # A live catalog proves the SERVER, not that it serves THIS model:
            # an unrelated process on the port, or llama.cpp/Ollama holding a
            # different model, answered "online" (RC19 audit, :8081/:8082).
            problem = await served_model_problem(adapter, model["name"])
            if problem:
                health = Health(status="error", detail=problem, latency_ms=health.latency_ms)
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
            if not (result.text or "").strip() and result.finish == "length":
                # A reasoning model (GPT-OSS) spends a 32-token budget thinking and is cut
                # off before it answers: that is a small budget, not a silent model. One
                # retry with room; a real answer is still required.
                result = await adapter.chat(
                    model["name"], [{"role": "user", "content": BENCH_PROMPT}],
                    max_tokens=REASONING_PROBE_TOKENS)
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
        # TEL-001: скорость — только из собственных замеров сервера (llama.cpp
        # `timings`). «tokens_out / вся латентность» короткого ответа показывал
        # 1.6 ток/с у модели с настоящими ~10. Нет замеров сервера — null и
        # подсказка про Bench Lab (там дифференциальный замер), а не выдумка.
        from .model_speed import from_timings
        meta = result.provider_meta if isinstance(result.provider_meta, dict) else {}
        speed = (from_timings(meta["timings"], elapsed * 1000, result.tokens_out)
                 if isinstance(meta.get("timings"), dict) else None)
        bench = {
            "method": speed["method"] if speed else "unavailable",
            "ttft_ms": speed["ttft_ms"] if speed else None,
            "prompt_tps": speed["prompt_tps"] if speed else None,
            "gen_tps": speed["gen_tps"] if speed else None,
            "latency_ms": int(elapsed * 1000),
            "tokens_out": result.tokens_out,
            "answer": result.text[:200],
            "tested_at": utcnow().isoformat(),
        }
        if speed is None:
            bench["note"] = "сервер не отдал timings — точная скорость в Bench Lab"
        async with self.db.session() as s:
            await s.execute(sa.update(models_t).where(models_t.c.id == model_id).values(
                bench=bench, status="online", status_detail="", last_check=utcnow()))
            await s.commit()
        await self.bus.emit("model.status", id=model_id, alias=model["alias"],
                            status="online", detail="", bench=bench)
        return {"id": model_id, "bench": bench, "health": record.to_dict()}

    async def mark_runtime_offline(self, model_id: int, detail: str) -> None:
        """A real call could not reach this model's endpoint.

        The `runtime:` prefix is what features/healing re-checks, so the model
        returns to `online` by itself once the endpoint answers again."""
        await self._set_status(model_id, "offline", f"{RUNTIME_OFFLINE_PREFIX}{detail}"[:500])
        await self.bus.emit("model.status", id=model_id, status="offline", detail=detail[:300])

    async def _set_status(self, model_id: int, status: str, detail: str) -> None:
        async with self.db.session() as s:
            await s.execute(sa.update(models_t).where(models_t.c.id == model_id).values(
                status=status, status_detail=detail or "", last_check=utcnow()))
            await s.commit()
