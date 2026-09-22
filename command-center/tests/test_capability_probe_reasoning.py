"""Проба способностей не записывает reasoning-модель в «не умеет» из-за бюджета.

Тот же дефект, что закрыл d6e25fb4 в `Registry.test_model` (32 токена ушли на
рассуждение, finish=length, текста нет), жил и в `v2/capability_probe.py`:
chat — 8 токенов, tools — 64, structured_output — 32, vision — 16. GPT-OSS и
другие reasoning-модели тратят такой бюджет на мысли, и проба писала в
`model_capability_checks` verified=False: роутер (`model_intelligence`: verified
fail → NO) переставал отдавать им задачи с инструментами, которые они умеют.

Заглушка ведёт себя как reasoning-модель: при бюджете меньше 200 токенов она
обрезается на рассуждении (content пуст, finish_reason=length), с запасом —
отвечает. Негативный контроль: модель, честно вернувшая пустоту с
finish_reason=stop, по-прежнему не умеет, и повторного вызова нет.
"""
from __future__ import annotations

import json
from typing import Any

from bcc.v2 import capability_probe as cp

THINKING = 200


class ReasoningModel:
    def __init__(self, *, silent: bool = False) -> None:
        self.silent = silent
        self.budgets: list[int] = []

    async def chat_raw(self, model: str, messages: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
        budget = int(kw.get("max_tokens") or 0)
        self.budgets.append(budget)
        if self.silent:
            return _reply("", "stop", budget)
        if budget < THINKING:
            return _reply("", "length", budget, reasoning="Пользователь просит…")
        prompt = str(messages[-1]["content"])
        if kw.get("tools"):
            return {"choices": [{"message": {"role": "assistant", "content": "",
                                             "tool_calls": [{"id": "c1", "type": "function",
                                                             "function": {"name": "bossman_probe",
                                                                          "arguments": '{"value": 7}'}}]},
                                 "finish_reason": "tool_calls"}],
                    "usage": {"completion_tokens": THINKING + 10}}
        if kw.get("response_format"):
            return _reply(json.dumps({"ok": True}), "stop", THINKING + 5)
        if isinstance(messages[-1]["content"], list):
            return _reply("red", "stop", THINKING + 1)
        assert "OK" in prompt
        return _reply("OK", "stop", THINKING + 1)


def _reply(text: str, finish: str, tokens: int, reasoning: str | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if reasoning:
        message["reasoning"] = reasoning
    return {"choices": [{"message": message, "finish_reason": finish}],
            "usage": {"completion_tokens": tokens}}


async def test_reasoning_model_passes_chat_tools_structured_and_vision_probes():
    client = ReasoningModel()
    results = {r.capability: r for r in await cp.probe_model(
        client, "openai/gpt-oss-120b:free",
        {"tools": True, "structured_output": True, "vision": True})}
    for cap in ("chat", "tools", "structured_output", "vision"):
        assert results[cap].verified is True, (cap, results[cap].detail)


async def test_negative_control_an_empty_answer_that_stopped_is_still_a_failure():
    client = ReasoningModel(silent=True)
    result = await cp.probe_chat(client, "silent/model")
    assert result.verified is False
    assert client.budgets == [8], "пустота с finish=stop не повод звать модель ещё раз"


async def test_negative_control_a_model_cut_off_even_with_room_is_still_a_failure():
    class Endless(ReasoningModel):
        async def chat_raw(self, model, messages, **kw):
            self.budgets.append(int(kw.get("max_tokens") or 0))
            return _reply("", "length", int(kw.get("max_tokens") or 0))

    client = Endless()
    result = await cp.probe_chat(client, "endless/model")
    assert result.verified is False
    assert len(client.budgets) == 2, "ровно один повтор, не цикл"
