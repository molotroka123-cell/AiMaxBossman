"""`bossman chat` /models: модель, которую Ollama отдаёт, но которая ещё не
заведена в Bossman, имеет в каталоге id = None (так задумано в list_items).
Чат форматировал её как `{id:<4}` → TypeError; цикл чата ловит только
BossmanError, поэтому беседа закрывалась целиком.
"""
from __future__ import annotations

import io
import sys

import pytest

pytest.importorskip("rich", reason="rich is a runtime dependency of the terminal client")

from bcc.terminal_cli.chat import EXIT_OK  # noqa: E402

from .test_terminal_chat_claude_parity import FakeClient, make_chat  # noqa: E402


class OllamaClient(FakeClient):
    """Ollama на адресе провайдера #1 перечисляет ещё одну, не заведённую модель."""

    def post(self, path, body=None):
        if path == "/api/models/discover":
            return {"endpoints": [{"label": "Ollama (локально)", "base_url": "http://127.0.0.1:8080",
                                   "ok": True, "models": ["qwen3", "gemma4:12b"]}]}
        return super().post(path, body)


def _run_lines(chat, monkeypatch, *lines: str) -> int:
    monkeypatch.setattr(sys, "stdin", io.StringIO("".join(ln + "\n" for ln in lines)))
    return chat.run()


def test_models_lists_an_unregistered_ollama_model_instead_of_crashing(tmp_path, monkeypatch):
    chat, _client, buf = make_chat(tmp_path, OllamaClient(tmp_path / "data"))
    assert _run_lines(chat, monkeypatch, "/models", "/exit") == EXIT_OK
    out = buf.getvalue()
    assert "gemma4:12b" in out and "qwen-local" in out
