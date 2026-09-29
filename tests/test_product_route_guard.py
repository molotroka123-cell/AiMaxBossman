"""Owner rule: product runtime may use only local models or OpenRouter ':free' models.

No hidden paid fallback and no direct Claude/OpenAI call from the YouTube/K1m6a
ingest path or Motion Studio spec generation. A remote endpoint (including one
set through env/CLI) is refused BEFORE any network I/O and before a key is sent.
"""
from __future__ import annotations

import re
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.route_guard import RouteViolation, assert_free_or_local  # noqa: E402

PAID = ["https://api.openai.com/v1", "https://api.anthropic.com", "https://openrouter.ai/api/v1",
        "https://example.com/v1"]


@pytest.fixture()
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network call attempted before the route guard refused")
    monkeypatch.setattr(urllib.request, "urlopen", boom)


def test_guard_allows_local_and_free_only():
    for ok in ("http://127.0.0.1:11434", "http://localhost:8000/v1", "http://[::1]:11434/api/chat"):
        assert_free_or_local(ok, "qwen")
    assert_free_or_local("https://openrouter.ai/api/v1", "meta-llama/x:free")
    with pytest.raises(RouteViolation):
        assert_free_or_local("https://openrouter.ai/api/v1", "anthropic/claude-x")
    with pytest.raises(RouteViolation):
        assert_free_or_local("https://openrouter.ai/api/v1", "")
    for bad in PAID[:2] + PAID[3:]:
        with pytest.raises(RouteViolation):
            assert_free_or_local(bad, "gpt-x:free")
    with pytest.raises(RouteViolation):
        assert_free_or_local("http://127.0.0.1.evil.example/v1", "qwen")


def test_youtube_vision_and_discovery_refuse_remote(no_network):
    from tools import youtube_trader_ingest as y
    for bad in PAID:
        with pytest.raises(RouteViolation):
            y._api_json("GET", bad.rstrip("/") + "/models")


def test_youtube_asr_refuses_remote(no_network, tmp_path):
    from tools import youtube_trader_ingest_auto as a
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    for bad in PAID:
        with pytest.raises(RouteViolation):
            a._transcribe(wav, api_base=bad)


def test_youtube_claims_refuse_remote(no_network, monkeypatch):
    from tools import youtube_trader_ingest_claims as c
    monkeypatch.setattr(c, "OLLAMA", "https://api.openai.com")
    monkeypatch.setattr(c, "wait_if_paused", lambda **k: None)
    with pytest.raises(RouteViolation):
        c.ollama_chat("qwen", [{"role": "user", "content": "x"}])


def test_motion_studio_spec_generation_refuses_remote(no_network):
    sys.path.insert(0, str(ROOT / "tools" / "motion_studio"))
    try:
        import generate_spec as g
    finally:
        sys.path.pop(0)
    for bad in PAID:
        with pytest.raises(RouteViolation):
            g._chat(bad, "gpt-x", [{"role": "user", "content": "x"}])
        with pytest.raises(RouteViolation):
            g._chat_ollama(bad, "gpt-x", [{"role": "user", "content": "x"}])


def test_no_hardcoded_paid_provider_hosts_in_these_paths():
    files = [*ROOT.glob("tools/youtube_trader_ingest*.py"), ROOT / "tools/k1m6a_youtube_batch.py",
             *(ROOT / "tools" / "motion_studio").glob("*.py")]
    rx = re.compile(r"api\.openai\.com|api\.anthropic\.com|generativelanguage\.googleapis|import anthropic|import openai")
    assert files
    for f in files:
        assert not rx.search(f.read_text(encoding="utf-8")), f
