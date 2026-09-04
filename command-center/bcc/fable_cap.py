"""Жёсткий потолок платной работы Fable для Command Center.

Что здесь происходит. Раньше единственной защитой от расхода был Spend Meter:
он смотрел на уже потраченное перед стартом прогона и не давал стартовать,
если потолок УЖЕ выбран. Между стартом и концом прогона денег он не считал,
поэтому один прогон мог уйти сколь угодно далеко за потолок: шагов много,
токенов много, а проверка была одна и в начале. Кроме того, его потолки
владелец задаёт сам через POST /spend/limit — то есть «жёсткость» отменялась
одним запросом.

Здесь потолок другой: резерв под ХУДШИЙ случай снимается ДО сетевого вызова,
в том же самом журнале, что и у прямого транспорта bossman-core, а сам потолок
(3.00 USD) — константа общего модуля, которую не поднимает ни настройка, ни
ручка, ни переменная окружения, ни повтор, ни второй процесс, ни перезапуск.

Точка врезки — Registry.adapter_for(): через неё проходят все, кто вообще
может позвать модель (движок, benchlab, ревью-гейт, веб-поиск, проверка и
тест модели). Оборачивать движок было бы недостаточно: мимо него ходят ещё
пятеро.

Граница платного — провайдер вида `anthropic` с неместным адресом. Локальный
endpoint в формате Anthropic денег не стоит, и резервировать под него нечего;
всё остальное, что этот адаптер умеет, — это api.anthropic.com.

Цена берётся ТОЛЬКО из общего прайса в коде. Цены в таблице models владелец
редактирует сам, и потолок, который считают по цене, назначенной тратящей
стороной, — не потолок. Модели, которой в прайсе нет, отказываем ДО вызова
адаптера: неизвестную цену нельзя оценить консервативно.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from .providers import ChatResult, Health, ProviderAdapter, ProviderError, is_local_url

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path and (_ROOT / "bossman_shared").is_dir():
    sys.path.insert(0, str(_ROOT))

try:
    from bossman_shared.fable_budget import (
        BudgetExhausted,
        actual_usd,
        canonical_budget,
        estimate_worst_case_usd,
    )
    LEDGER_AVAILABLE = True
    LEDGER_PROBLEM = ""
except Exception as exc:  # noqa: BLE001
    # Недоступный журнал — это не «работаем без потолка». Денежная защита
    # обязана падать в закрытую сторону: платный вызов не состоится вовсе.
    LEDGER_AVAILABLE = False
    LEDGER_PROBLEM = f"{type(exc).__name__}: {exc}"

    class BudgetExhausted(RuntimeError):  # type: ignore[no-redef]
        code = "budget_exhausted"

# Столько токенов ответа просит AnthropicAdapter, когда max_tokens не задан.
# Значение обязано совпадать с ним: резерв считается под то, что реально уйдёт.
DEFAULT_MAX_OUTPUT_TOKENS = 2048
ANTHROPIC_KIND = "anthropic"
DEFAULT_ANTHROPIC_BASE = "https://api.anthropic.com"


class BudgetRefused(ProviderError):
    """Отказ по деньгам. Это ProviderError, поэтому движок обходится с ним как
    с недоступной моделью: пишет причину в прогон и уходит на fallback —
    который либо местная модель (бесплатная), либо снова отказ. Обойти потолок
    ни тем, ни другим нельзя."""

    def __init__(self, message: str):
        super().__init__(message, kind="http",
                         hint="жёсткий потолок платной работы Fable — 3.00 USD на всё")


def paid_fable_boundary(provider: dict) -> bool:
    """Уйдут ли отсюда деньги Anthropic."""
    if str(provider.get("kind") or "").strip() != ANTHROPIC_KIND:
        return False
    base = str(provider.get("base_url") or "").strip() or DEFAULT_ANTHROPIC_BASE
    return not is_local_url(base)


def prompt_chars(messages: list[dict], tools: Any = None) -> int:
    """Размер запроса в знаках — то, что уйдёт в сеть, а не только текст.

    Схемы инструментов тоже отправляются и тоже оплачиваются, поэтому считаются
    вместе с сообщениями: недооценка входа — это недобор резерва.
    """
    payload: list[Any] = [messages]
    if tools:
        payload.append(tools)
    return len(json.dumps(payload, ensure_ascii=False, default=str))


class CappedAdapter:
    """Адаптер платной границы: сначала резерв, потом сеть, потом сверка.

    Неопределённый исход (обрыв, таймаут, отмена, ответ без usage) оставляет
    резерв висеть в RECONCILING — провайдер мог списать деньги, и «наверное,
    не списал» тут не аргумент. Освободить такой резерв может только разбор с
    идентификатором запроса от провайдера.
    """

    def __init__(self, inner: ProviderAdapter, *, alias: str = "") -> None:
        self.inner = inner
        self.alias = alias

    async def chat(self, model: str, messages: list[dict], **kw: Any) -> ChatResult:
        if not LEDGER_AVAILABLE:
            raise BudgetRefused(
                "журнал жёсткого потолка недоступен, платный вызов не выполняется: "
                + LEDGER_PROBLEM)
        max_out = int(kw.get("max_tokens") or DEFAULT_MAX_OUTPUT_TOKENS)
        try:
            worst = estimate_worst_case_usd(
                model, prompt_chars(messages, kw.get("tools")), max_out)
        except BudgetExhausted as exc:
            # цена неизвестна — отказ ДО того, как адаптер был вызван
            raise BudgetRefused(str(exc)) from exc

        budget = await asyncio.to_thread(canonical_budget)
        try:
            rid = await asyncio.to_thread(
                budget.reserve, worst, purpose=f"bcc:{self.alias or model}"[:120])
        except BudgetExhausted as exc:
            raise BudgetRefused(str(exc)) from exc

        try:
            result = await self.inner.chat(model, messages, **kw)
        except BaseException:
            # в том числе CancelledError и таймаут: деньги не прощаем
            await asyncio.to_thread(_hold, budget, rid)
            raise

        spent = _reported_cost(model, result)
        if spent is None:
            await asyncio.to_thread(_hold, budget, rid)     # нет свидетельства расхода
        else:
            # min: если провайдер отчитался ДОРОЖЕ, чем мы удержали, значит наша
            # оценка худшего случая оказалась мала. Списываем весь резерв —
            # занизить расход до «сколько удержали» безопаснее, чем оставить
            # разницу неучтённой, а сам факт виден по исчерпанию потолка.
            await asyncio.to_thread(_settle, budget, rid, min(spent, worst),
                                    str((result.provider_meta or {}).get("id") or ""))
        return result

    # health/list_models у Anthropic бьют в /v1/models — токены там не тратятся,
    # поэтому резерв под них не берётся: он бы съедал потолок ни за что.
    async def health(self) -> Health:
        return await self.inner.health()

    async def list_models(self) -> list[str]:
        return await self.inner.list_models()

    def __getattr__(self, item: str) -> Any:
        return getattr(self.inner, item)


def _reported_cost(model: str, result: ChatResult) -> float | None:
    """Цена по отчёту провайдера и доверенному прайсу; None — отчёта нет."""
    if not (result.tokens_in or result.tokens_out):
        return None
    read = int(getattr(result, "cache_read_tokens", 0) or 0)
    write = int(getattr(result, "cache_write_tokens", 0) or 0)
    fresh = max(0, int(result.tokens_in) - read - write)
    try:
        return actual_usd(model, input_tokens=fresh, output_tokens=int(result.tokens_out),
                          cache_read_tokens=read, cache_write_tokens=write)
    except BudgetExhausted:
        return None


def _hold(budget, rid: str) -> None:
    try:
        budget.mark_reconciling(rid)
    except Exception:  # noqa: BLE001 — резерв уже висит, это и есть безопасный исход
        pass


def _settle(budget, rid: str, cost: float, request_id: str) -> None:
    try:
        budget.commit(rid, cost, request_id=request_id)
    except Exception:  # noqa: BLE001
        _hold(budget, rid)


def capped(adapter: ProviderAdapter, provider: dict, model: dict) -> ProviderAdapter:
    """Обернуть адаптер, если он ведёт на платную границу Fable."""
    if not paid_fable_boundary(provider):
        return adapter
    return CappedAdapter(adapter, alias=str(model.get("alias") or model.get("name") or ""))
