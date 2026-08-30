"""Hierarchical Context Manager — global → project → task → step.

Каждый слой возвращает ContextLayer {text, tokens_est, hash}. Кэш привязан
к hash содержимого, а не ко времени (требование п.9).
"""
from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import dataclass


def _estimate_tokens(text: str) -> int:
    # грубая оценка 1 токен ≈ 4 символа, минимум 1
    return max(1, len(text) // 4) if text else 0


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ContextLayer:
    name: str          # global | project | task | step
    text: str
    tokens_est: int
    hash: str
    stable: bool       # stable → prompt cache кандидат


class TokenBudgeter:
    """Жёсткий рез по приоритету: objective/invariants никогда не режутся."""

    @staticmethod
    def budget(layers: list[ContextLayer], max_tokens: int) -> list[ContextLayer]:
        """Возвращает слои, обрезанные по max_tokens (приоритет — порядок списка)."""
        if max_tokens <= 0:
            return []
        out: list[ContextLayer] = []
        used = 0
        for layer in layers:
            if used + layer.tokens_est <= max_tokens:
                out.append(layer)
                used += layer.tokens_est
            else:
                remain = max_tokens - used
                if remain > 32:
                    cut = layer.text[: remain * 4]
                    out.append(ContextLayer(
                        name=layer.name,
                        text=cut + "\n…[truncated by TokenBudgeter]",
                        tokens_est=remain,
                        hash=_hash_text(cut),
                        stable=layer.stable,
                    ))
                break
        return out


class HierarchicalContextManager:
    """Собирает 4 слоя. Источники инжектятся, чтобы не тянуть БД в unit-тестах."""

    def __init__(self, *,
                 global_text: str = "",
                 project_loader=None,  # async (project_id) -> str
                 task_loader=None,     # async (task_id) -> str
                 step_loader=None):    # async (run_id, step) -> str
        self._global_text = global_text
        self._project_loader = project_loader
        self._task_loader = task_loader
        self._step_loader = step_loader
        self._cache: dict[str, ContextLayer] = {}

    def _layer(self, name: str, text: str, stable: bool) -> ContextLayer:
        h = _hash_text(text)
        key = f"{name}:{h}"
        if key in self._cache:
            return self._cache[key]
        layer = ContextLayer(name=name, text=text, tokens_est=_estimate_tokens(text),
                             hash=h, stable=stable)
        self._cache[key] = layer
        return layer

    def get_global(self) -> ContextLayer:
        return self._layer("global", self._global_text or "BOSSMAN invariants: follow policy, be deterministic.", stable=True)

    async def get_project(self, project_id: str | int | None) -> ContextLayer:
        if project_id is None or self._project_loader is None:
            return self._layer("project", "", stable=True)
        res = self._project_loader(project_id)
        text = await res if inspect.isawaitable(res) else res
        return self._layer("project", text or "", stable=True)

    async def get_task(self, task_id: int | None) -> ContextLayer:
        if task_id is None or self._task_loader is None:
            return self._layer("task", "", stable=False)
        res = self._task_loader(task_id)
        text = await res if inspect.isawaitable(res) else res
        return self._layer("task", text or "", stable=False)

    async def get_step(self, run_id: int | None, step: int | None) -> ContextLayer:
        if run_id is None or self._step_loader is None:
            return self._layer("step", "", stable=False)
        res = self._step_loader(run_id, step or 0)
        text = await res if inspect.isawaitable(res) else res
        return self._layer("step", text or "", stable=False)

    async def assemble(self, *, project_id=None, task_id=None, run_id=None, step=None,
                       max_tokens: int = 8000) -> list[ContextLayer]:
        layers = [
            self.get_global(),
            await self.get_project(project_id),
            await self.get_task(task_id),
            await self.get_step(run_id, step),
        ]
        return TokenBudgeter.budget(layers, max_tokens)
