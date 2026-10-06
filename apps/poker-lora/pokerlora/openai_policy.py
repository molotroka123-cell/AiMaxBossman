"""Policy backed by a LOCAL OpenAI-compatible chat endpoint (llama.cpp server, vLLM, LM Studio...). Loopback only. The answer goes through the same
validator as every other policy, so a model that rambles, names an illegal action or changes the amount is simply an invalid answer."""
from __future__ import annotations

import json
import urllib.request
from urllib.parse import urlparse

from .policies import Policy

SYSTEM = ("You choose ONE action for the hero in a heads-up no-limit hold'em river spot, using ONLY the JSON state you are given "
          "(your cards, the board, pot, stacks, street history, the ranges that are GIVEN, and the legal actions). The villain's cards are unknown. "
          'Answer with a single JSON object: {"action": <one legal action>, "size": <that action\'s amount>, '
          '"probs": {<legal action>: <probability>, ...}, "explanation": <one short sentence>}. If a mixed strategy is right, give probabilities.')


class NotLoopback(ValueError):
    pass


class OpenAICompatPolicy(Policy):
    def __init__(self, base_url: str, model: str, timeout: float = 60.0, name: str | None = None):
        u = urlparse(base_url)
        if u.scheme not in ("http", "https") or (u.hostname or "") not in ("127.0.0.1", "localhost", "::1") or u.username:
            raise NotLoopback(f"{base_url!r}: the policy server must run on this machine (loopback)")
        self.url = base_url.rstrip("/") + "/v1/chat/completions"
        self.model, self.timeout = model, timeout
        self.name = name or f"openai_compat:{model}"

    def act(self, inp: dict):
        body = json.dumps({"model": self.model, "temperature": 0, "max_tokens": 300,
                           "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": json.dumps(inp, ensure_ascii=False)}]}).encode()
        req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))          # never through a proxy: loopback only
        with opener.open(req, timeout=self.timeout) as r:
            data = json.loads(r.read().decode())
        return data["choices"][0]["message"]["content"]
