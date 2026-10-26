"""Check actual provider locality and price on EVERY inference, including fallback."""
import math
import os
import re
import time
from contextvars import ContextVar
from urllib.parse import urlsplit

from bossman_shared.privacy import assert_provider_egress
from .v2.model_router import derive_local
from .providers import ProviderError, is_local_url
from .fable_cap import CappedAdapter


# Owner memory (vault notes, lessons, facts) recalled at TASK_START is LOCAL data.
# It reaches a model only on this machine / the local network unless the task
# explicitly opts in (task.meta.memory_to_cloud). Enforced HERE, on every
# inference, because the Smart Router, the recovery ladder and fallback pick the
# model after recall — a check at recall time would miss them (P1 2026-09-24: a
# task on a free cloud endpoint that may retain prompts received 5 vault notes).
MEMORY_CONTEXT_MARKER = "[MEMORY CONTEXT"
memory_to_cloud_allowed: ContextVar[bool] = ContextVar("bossman_memory_to_cloud", default=False)
memory_withheld: ContextVar[list | None] = ContextVar("bossman_memory_withheld", default=None)


def _is_memory_context(message) -> bool:
    return (isinstance(message, dict) and message.get("role") == "system"
            and isinstance(message.get("content"), str)
            and message["content"].lstrip().startswith(MEMORY_CONTEXT_MARKER))


def withhold_local_memory(args: tuple, kwargs: dict) -> tuple[tuple, dict, int]:
    """(args, kwargs) of adapter.chat(model, messages, ...) without recalled memory."""
    if "messages" in kwargs:
        msgs = kwargs["messages"]
        kept = [m for m in msgs or [] if not _is_memory_context(m)]
        return args, {**kwargs, "messages": kept}, len(msgs or []) - len(kept)
    if len(args) >= 2 and isinstance(args[1], list):
        kept = [m for m in args[1] if not _is_memory_context(m)]
        return (args[0], kept, *args[2:]), kwargs, len(args[1]) - len(kept)
    return args, kwargs, 0


def known_prices(model):
    return all(isinstance(v, (int, float)) and not isinstance(v, bool)
               and math.isfinite(v) and v >= 0 for v in (model.get("price_in"), model.get("price_out")))


#: Why a cloud price is unknown, as far as the provider's synchronized catalog
#: can tell (Registry.adapter_for). The gate is the same in every case; the
#: owner's next step is not — "refresh the catalog" cannot fix a model the
#: catalog no longer lists (RC19 audit: a removed OpenRouter model).
_UNKNOWN_PRICE_WHY = {
    "stale": ("Цена облачной модели неизвестна: модели {name} больше нет в каталоге провайдера "
              "(провайдер её убрал?). Выберите для агента другую модель."),
    "absent": ("Цена облачной модели неизвестна: модели {name} нет в синхронизированном каталоге "
               "провайдера. Проверьте имя модели или выберите другую."),
}


#: Ollama's own marker for a model it does NOT run here: `gpt-oss:120b-cloud`,
#: `minimax-m3.1-flash:cloud`. Same pattern as the ollama.chat plugin (features.plugins).
_OLLAMA_CLOUD_TAG = re.compile(r"(?i)[:-]cloud\b")
#: Where such a request really goes; used for the privacy egress check instead of 127.0.0.1.
OLLAMA_CLOUD_EGRESS_URL = "https://ollama.com"


def is_cloud_offload(provider: dict, model: dict) -> bool:
    """A local Ollama endpoint serving a `:cloud`/`-cloud` model forwards the prompt to ollama.com.

    The base_url is 127.0.0.1, but the data leaves the machine: for memory, privacy, price and
    the free-only rule such a model is a CLOUD model. Erring this way is the safe direction."""
    if not is_local_url(provider.get("base_url") or ""):
        return False
    return any(n and _OLLAMA_CLOUD_TAG.search(str(n)) for n in (model.get("name"), model.get("alias")))


#: Owner-granted, time-limited FREE Ollama-cloud routes: exact model name -> end of the window
#: (UTC, inclusive minute). Only on loopback Ollama (/v1 on :11434), only text, never owner
#: memory, never a private/local_only task. After the window the model is refused like any other
#: unproven cloud model; nothing has to be switched off by hand.
#: 2026-10-20: MiniMax M3.1 Flash for code review / audit, free until 2026-10-22T23:59Z.
FREE_OLLAMA_CLOUD_WINDOWS: dict[str, str] = {
    "minimax-m3.1-flash:cloud": "2026-10-22T23:59:00Z",
}
_clock = time.time        # tests move it


def _window_end(model_name: str) -> float | None:
    raw = FREE_OLLAMA_CLOUD_WINDOWS.get(str(model_name or "").strip().lower())
    if not raw:
        return None
    from datetime import datetime
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()


def free_ollama_cloud_window(provider: dict, model: dict) -> str:
    """'open' | 'expired' | '' (not a windowed model) for a FREE_OLLAMA_CLOUD_WINDOWS route."""
    from .providers import is_ollama_v1_url
    end = _window_end(str(model.get("name") or ""))
    if end is None or not is_ollama_v1_url(provider.get("base_url") or ""):
        return ""
    return "open" if _clock() <= end else "expired"


#: Content parts that carry media (images, audio, files): never sent on a free review route.
_MEDIA_PARTS = frozenset({"image_url", "image", "input_image", "input_audio", "audio", "file", "video"})


def _has_media(messages) -> bool:
    for m in messages or []:
        content = m.get("content") if isinstance(m, dict) else None
        if isinstance(content, list) and any(isinstance(p, dict) and p.get("type") in _MEDIA_PARTS
                                             for p in content):
            return True
    return False


def is_governed_local(provider: dict, model: dict) -> bool:
    """Local for the price gate: this machine / the local network."""
    if is_cloud_offload(provider, model):
        return False
    base = provider.get("base_url") or ""
    local, _ = derive_local(model.get("kind", ""), provider["kind"], base)
    return local or (provider["kind"] == "anthropic" and is_local_url(base))


def priced(model: dict) -> bool:
    return bool(model.get("pricing_known", False)) and known_prices(model)


def refuses_unknown_price(provider: dict, model: dict) -> bool:
    """Would GovernedAdapter refuse this model before any provider is asked?

    Same rule as `GovernedAdapter.chat`, for callers that must not queue or
    pick a model that is certain to be refused (admission, recovery)."""
    from .fable_cap import paid_fable_boundary
    if free_ollama_cloud_window(provider, model) == "open":
        return False                       # declared free for its window (FREE_OLLAMA_CLOUD_WINDOWS)
    return (not is_governed_local(provider, model) and not paid_fable_boundary(provider)
            and not priced(model))


def window_closed_message(model: dict) -> str:
    name = str(model.get("name") or "?")
    return (f"Бесплатное окно облачной модели {name} закрыто "
            f"({FREE_OLLAMA_CLOUD_WINDOWS.get(name.strip().lower(), '?')}): модель отключена автоматически.")


def unknown_price_message(model: dict, catalog_state: str | None = None) -> str:
    name = model.get("name") or model.get("alias") or "?"
    template = _UNKNOWN_PRICE_WHY.get(catalog_state or "")
    return (template.format(name=name) if template
            else "Цена облачной модели неизвестна: обновите каталог провайдера перед запуском.")


ALLOW_PAID_CLOUD_ENV = "BOSSMAN_ALLOW_PAID_CLOUD"


def free_only_policy_active() -> bool:
    """Owner rule: product runtime cloud = OpenRouter ':free' or a free-tier preset, else local.

    Same switch as the core gateway (bossman.gateway.router); it is an explicit
    operator/test opt-out and is never implied by a registered key or price."""
    return os.getenv(ALLOW_PAID_CLOUD_ENV, "").strip().lower() not in {"1", "true", "yes"}


def _free_preset_hosts() -> frozenset:
    from .features.free_providers import PRESETS
    return frozenset((urlsplit(p.base_url).hostname or "").lower() for p in PRESETS.values())


def is_chat_model(model_id: str) -> bool:
    from .features.free_providers import is_chat_model as _is_chat_model
    return _is_chat_model(model_id)


def banned_model_refusal(model: dict) -> str:
    """'' unless the model is a banned family (Liquid/LFM, bcc.pit.model_policy)."""
    from .pit.model_policy import banned_refusal
    for name in (model.get("name"), model.get("alias")):
        refusal = banned_refusal(name) if name else ""
        if refusal:
            return refusal
    return ""


def free_only_refusal(provider: dict, model: dict) -> str:
    """"" when the free-only rule lets this inference through, else why not.

    Local endpoints and the owner-capped Fable boundary (fable_cap, a separate
    owner-set hard limit) are not judged here. Any other cloud model must be
    provably free: OpenRouter with a ':free' id, or a model connected through a
    free-tier preset (caps.free_tier) on that preset's own host. A positive price
    is refused outright; a 0/0 an owner typed for an arbitrary host is not proof."""
    from .fable_cap import paid_fable_boundary
    banned = banned_model_refusal(model)
    if banned:
        return banned                  # owner ban: local or cloud, paid opt-out or not
    offload = is_cloud_offload(provider, model)
    if (not free_only_policy_active() or is_governed_local(provider, model)
            or paid_fable_boundary(provider) or (is_local_url(provider.get("base_url") or "") and not offload)):
        return ""
    # Owner-enabled long-run profile (bcc.long_run): exactly Mistral Large 4,
    # GLM 5.3 Flash and Haiku 5.5 on their own hosts pass THIS gate; the daily
    # EUR caps and STOP are enforced per call in GovernedAdapter.chat.
    from . import long_run
    if long_run.allows_paid(str(model.get("name") or ""), provider.get("base_url") or ""):
        return ""
    name = str(model.get("name") or model.get("alias") or "?")
    prices = [v for v in (model.get("price_in"), model.get("price_out"))
              if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if any(v > 0 for v in prices):
        return (f"Облачная модель {name} платная, а включён режим «только бесплатное»: "
                f"разрешены локальные модели, модели OpenRouter с суффиксом ':free' и бесплатные тарифы провайдеров.")
    if offload:
        window = free_ollama_cloud_window(provider, model)
        if window == "open":
            return ""
        if window == "expired":
            return window_closed_message(model)
    host = (urlsplit(provider.get("base_url") or "").hostname or "").lower()
    if host.endswith("openrouter.ai") and name.endswith(":free"):
        return ""
    if (model.get("caps") or {}).get("free_tier") and host in _free_preset_hosts():
        return ""
    return (f"Облачная модель {name} ({host or 'адрес по умолчанию'}) не доказанно бесплатная, а включён режим "
            f"«только бесплатное»: нужна локальная модель, модель OpenRouter ':free' или бесплатный тариф провайдера.")


class FreeOnlyAdapter:
    """Refuses inference (never health/catalog reads) for a non-free cloud model."""

    def __init__(self, adapter, reason: str):
        self.adapter, self.reason = adapter, reason

    def __getattr__(self, name):
        return getattr(self.adapter, name)

    async def chat(self, *args, **kwargs):
        raise ProviderError(self.reason, kind="budget")


class GovernedAdapter:
    def __init__(self, adapter, provider, model, *, catalog_state: str | None = None):
        self.adapter, self.provider, self.model = adapter, dict(provider), dict(model)
        self.catalog_state = catalog_state

    def __getattr__(self, name):
        return getattr(self.adapter, name)

    async def chat(self, *args, **kwargs):
        p, m = self.provider, self.model
        banned = banned_model_refusal(m)
        if banned:
            raise ProviderError(banned, kind="budget")
        offload = is_cloud_offload(p, m)
        # judge egress by where the prompt really goes: a `:cloud` model on local Ollama is ollama.com
        assert_provider_egress(p["kind"], OLLAMA_CLOUD_EGRESS_URL if offload else (p.get("base_url") or ""))
        local = is_governed_local(p, m)
        window = free_ollama_cloud_window(p, m) if offload else ""
        if window == "expired":
            raise ProviderError(window_closed_message(m), kind="budget")
        if window == "open":
            # free review/audit route: text only, never personal media
            messages = kwargs.get("messages") if "messages" in kwargs else (args[1] if len(args) >= 2 else [])
            if _has_media(messages):
                raise ProviderError(f"{m.get('name')}: бесплатный облачный маршрут для ревью кода принимает "
                                    "только текст; изображения, аудио и файлы сюда не отправляются.",
                                    kind="policy")
        # CappedAdapter has its own canonical tariff and rejects unknown models before dispatch.
        elif not local and not isinstance(self.adapter, CappedAdapter) and not priced(m):
            raise ProviderError(unknown_price_message(m, self.catalog_state), kind="budget")
        # memory follows the actual destination: this machine / the local network
        memory_local = local or (is_local_url(p.get("base_url") or "") and not offload)
        # a windowed free route never gets owner memory, even for a task that opted in
        if not memory_local and (window == "open" or not memory_to_cloud_allowed.get()):
            args, kwargs, removed = withhold_local_memory(args, kwargs)
            sink = memory_withheld.get()
            if removed and sink is not None:
                sink.append({"provider": p.get("name") or p["kind"], "model": m.get("alias") or m.get("name"),
                             "messages": removed})
        family = None
        if not local:
            from . import long_run
            family = long_run.classify(str(m.get("name") or ""), p.get("base_url") or "")
            if family is not None and not long_run.enabled():
                family = None              # profile off: the free-only gate already decided
        if family is None:
            return await self.adapter.chat(*args, **kwargs)
        return await self._chat_long_run(family, args, kwargs)

    async def _chat_long_run(self, family: str, args: tuple, kwargs: dict):
        """Paid long-run call: STOP, per-provider daily cap, reserve -> call -> settle."""
        from . import long_run
        m = self.model
        messages = kwargs.get("messages") if "messages" in kwargs else (args[1] if len(args) >= 2 else [])
        max_out = int(kwargs.get("max_tokens") or 2048)
        # The reservation assumes at most `max_out` output tokens; the provider must be held to
        # the same number. Without it the OpenAI-compatible adapter sent no max_tokens at all,
        # the model could answer far past the booked worst case and one call overshot the cap.
        kwargs = {**kwargs, "max_tokens": max_out}
        ledger = long_run.LongRunLedger()
        problem = long_run.paid_price_problem(m, family)
        if problem:
            ledger._refuse(family, str(m.get("name") or ""), 0.0, problem)
            raise ProviderError(problem, kind="budget")
        worst = long_run.estimate_worst_case_eur(m, messages or [], max_out, kwargs.get("tools"))
        try:
            rid = ledger.reserve(family, worst, model=str(m.get("name") or ""),
                                 purpose=str(m.get("alias") or m.get("name") or "")[:120])
        except long_run.LongRunRefused as exc:
            raise ProviderError(str(exc), kind="budget") from None
        try:
            result = await self.adapter.chat(*args, **kwargs)
        except BaseException:
            # network failure / timeout / cancel: the provider may have billed
            # -> the worst-case reservation stays booked (no release).
            raise
        spent = long_run.actual_eur(m, result)
        ledger.commit(rid, worst if spent is None else spent,
                      tokens_in=getattr(result, "tokens_in", 0), tokens_out=getattr(result, "tokens_out", 0))
        return result
