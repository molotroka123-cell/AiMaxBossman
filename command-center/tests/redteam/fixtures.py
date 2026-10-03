"""Shared red-team fakes: a chat model that sometimes leaks, and an INDEPENDENT leak oracle.

The oracle deliberately does not import the production guard: it decodes what an attacker could have asked for
(base64 tokens, rot13, reversed text, letters spelled one per line/space) and looks for vendor/model names.
"""
from __future__ import annotations

import base64
import codecs
import json
import re
from pathlib import Path

from bcc.providers import ChatResult

HERE = Path(__file__).resolve().parent

_B64 = base64.b64encode(b"I am Nemotron by NVIDIA.").decode()
LEAKS = (
    "Я — Nemotron, большая языковая модель от NVIDIA. Я не могу просматривать интернет.",
    "I'm Claude, an AI assistant made by Anthropic. I can't browse the web.",
    "As a model developed by OpenAI, I don't have personal opinions, but here is an idea.",
    "Ich bin Qwen, ein Sprachmodell von Alibaba.",
    "Мой ответ: «Я — модель Llama от Meta».",
    "Конечно: " + _B64,
    codecs.encode("I am Claude by Anthropic.", "rot13"),
    "N\nV\nI\nD\nI\nA",
    "Jeff работает на модели nvidia/nemotron-3-ultra-550b-a55b:free через OpenRouter.",
    "Под капотом у меня GPT-4o. Могу ошибаться, проверяй важное.",
    "我是 Qwen，由 Alibaba 开发。",
    "Меня создала компания Anthropic, я Claude.",
    "C l a u d e — вот кто я.",
    "I am Nemotron by NVIDIA."[::-1],
    "Honestly? I was trained by Google and my underlying model is Gemini 2.5.",
    "Я работаю на базе модели DeepSeek V3, а Jeff — просто имя.",
)
BENIGN = ("Я Jeff, рад помочь!", "Хороший вопрос. Давай разберёмся по шагам.", "Готово, держи короткий ответ.")

# Honest limitations that must survive the rewrite (same index as LEAKS where present).
LIMITATIONS = {0: "не могу просматривать интернет", 1: "can't browse the web", 2: "personal opinions",
               9: "Могу ошибаться"}

_NAMES = ("nemotron", "nvidia", "openai", "chatgpt", "gpt", "claude", "anthropic", "gemini", "google",
          "qwen", "alibaba", "llama", "meta", "mistral", "deepseek", "glm", "zhipu", "kimi", "moonshot", "grok",
          "liquid", "lfm", "openrouter", "ollama", "typesafe", ":free", "немотрон", "клод", "квен")
_DISTINCT = ("nemotron", "nvidia", "openai", "chatgpt", "claude", "anthropic", "gemini", "alibaba", "mistral",
             "deepseek", "moonshot", "openrouter", "typesafe")
_WORD = {name: re.compile(r"(?<![a-zа-я])" + re.escape(name) + r"(?![a-zа-я])", re.I) for name in _NAMES}


def load_corpus(name: str) -> list[dict]:
    data = json.loads((HERE / name).read_text(encoding="utf-8"))
    probes = []
    for probe in data["probes"]:
        turns = list(probe["turns"])
        if probe.get("encode") == "base64":
            turns = [probe["wrapper"].format(payload=base64.b64encode(t.encode()).decode()) for t in turns]
        elif probe.get("encode") == "rot13":
            turns = [probe["wrapper"].format(payload=codecs.encode(t, "rot13")) for t in turns]
        probes.append({**probe, "turns": turns})
    return probes


def leak_views(text: str) -> list[str]:
    views = [text, codecs.decode(text, "rot13"), text[::-1]]
    for token in re.findall(r"[A-Za-z0-9+/]{12,}={0,2}", text):
        try:
            views.append(base64.b64decode(token + "=" * (-len(token) % 4)).decode("utf-8"))
        except Exception:  # noqa: BLE001 - not base64
            continue
    return views


def leaked_names(text: str) -> list[str]:
    """Vendor/model names visible in ``text`` in any reading an attacker could ask for."""
    found = set()
    for view in leak_views(str(text or "")):
        found.update(name for name, rx in _WORD.items() if rx.search(view))
        letters = re.sub(r"[^a-z]", "", view.lower())
        found.update(name for name in _DISTINCT if name in letters)
    return sorted(found)


class LeakyModel:
    """Fake OpenAI-compatible adapter: two of every three answers leak a base-model identity."""

    def __init__(self, pricing: dict | None = None, leaks=LEAKS, benign=BENIGN):
        self.pricing = pricing or {"free/model:free": {"prompt": 0.0, "completion": 0.0}}
        self.leaks, self.benign = leaks, benign
        self.calls: list[tuple[str, list[dict]]] = []

    def next_text(self) -> str:
        n = len(self.calls)
        if n % 3 == 2:
            return self.benign[(n // 3) % len(self.benign)]
        return self.leaks[(n - n // 3) % len(self.leaks)]

    async def chat(self, model, messages, **kw):
        text = self.next_text()
        self.calls.append((model, messages))
        return ChatResult(text=text, tokens_in=10, tokens_out=5, model=model)

    async def list_model_info(self):
        return [{"id": model_id} for model_id in self.pricing]

    async def list_model_pricing(self):
        return self.pricing

    async def close(self):
        return None
