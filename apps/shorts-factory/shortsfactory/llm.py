"""Local LLM (Ollama) as an injectable chat(messages) -> str. Nothing leaves the PC."""
from __future__ import annotations

import json
import os
import re
from typing import Callable

import httpx

OLLAMA_URL = os.environ.get("SHORTS_OLLAMA_URL", "http://127.0.0.1:11434")
PREFERRED = ("bossman-fast-qwen36-35b-a3b-q5", "bossman-main-qwen38-27b-q5", "qwen38", "qwen36", "qwen3", "qwen")


def pick_model(url: str = OLLAMA_URL, want: str | None = None) -> str | None:
    """An installed Qwen model; prefer one already loaded in memory (no extra VRAM/RAM load)."""
    want = want or os.environ.get("SHORTS_LLM_MODEL")
    try:
        tags = [m["name"] for m in httpx.get(f"{url}/api/tags", timeout=5).json().get("models", [])]
        loaded = [m["name"] for m in httpx.get(f"{url}/api/ps", timeout=5).json().get("models", [])]
    except Exception:  # noqa: BLE001 - reported as None => NOT_CONNECTED
        return None
    if want:
        return want if want in tags else None
    qwen_loaded = [m for m in loaded if "qwen" in m.lower() and "coder" not in m.lower() and "vision" not in m.lower()]
    if qwen_loaded:
        return qwen_loaded[0]
    for pref in PREFERRED:
        for m in tags:
            if m.startswith(pref) and "coder" not in m:
                return m
    return None


def make_chat(model: str, url: str = OLLAMA_URL, *, json_mode: bool = True, temperature: float = 0.3,
              num_ctx: int = 8192, timeout: float = 300) -> Callable[[list[dict]], str]:
    def chat(messages: list[dict]) -> str:
        body = {"model": model, "messages": messages, "stream": False, "think": False,
                "options": {"temperature": temperature, "num_ctx": num_ctx}}
        if json_mode:
            body["format"] = "json"
        r = httpx.post(f"{url}/api/chat", json=body, timeout=timeout)
        r.raise_for_status()
        return r.json()["message"]["content"]
    chat.model = model  # type: ignore[attr-defined]
    return chat


def parse_json(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        v = json.loads(text)
        return v if isinstance(v, dict) else {"items": v}
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise
