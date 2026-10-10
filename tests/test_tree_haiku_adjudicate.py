"""Haiku tree judge: strict verdicts only, metered spend, no network in tests."""
from __future__ import annotations

import io
import json

from tools.tree_proof import haiku_adjudicate as judge


def _reply(monkeypatch, content, usage=None):
    body = json.dumps({"choices": [{"message": {"content": content}}],
                       "usage": usage or {"prompt_tokens": 1000, "completion_tokens": 100}}).encode()
    seen = {}

    def fake_urlopen(req, timeout=0):
        seen["model"] = json.loads(req.data)["model"]
        return io.BytesIO(body)

    monkeypatch.setattr(judge, "key", lambda: "test-key")
    monkeypatch.setattr(judge.urllib.request, "urlopen", fake_urlopen)
    return seen


def test_a_valid_verdict_is_parsed_and_spend_is_metered_from_usage(monkeypatch):
    seen = _reply(monkeypatch, 'Sure: {"verdict": "ACCEPT_NEW", "reason": "tests ran and import the module"}')
    verdict, cost = judge.ask("prompt")
    assert verdict == {"verdict": "ACCEPT_NEW", "reason": "tests ran and import the module"}
    assert seen["model"] == "anthropic/claude-haiku-5.5"
    assert abs(cost - (1000 * judge.PRICE_IN + 100 * judge.PRICE_OUT)) < 1e-12


def test_anything_but_a_strict_verdict_keeps_the_old_record(monkeypatch):
    _reply(monkeypatch, "I think it is fine")
    assert judge.ask("p")[0]["verdict"] == "KEEP_OLD"
    _reply(monkeypatch, '{"verdict": "PROMOTE", "reason": "x"}')
    assert judge.ask("p")[0]["verdict"] == "KEEP_OLD"
