#!/usr/bin/env python3
"""OpenAI-compatible streaming client for worker models, with measured timing,
free-only guard and distillation recording.

  * OpenRouter: only `...:free` ids are accepted; each answer's reported cost
    must be 0, otherwise the call is marked PAID_VIOLATION (no silent paid use).
  * TTFT = first streamed token of any kind (content, reasoning or tool call);
    tok/s = completion tokens / (end - first token). Numbers are measured,
    never inferred.
  * 429 / 5xx are retried with backoff and every attempt is counted.
  * The key is read from the environment or the owner's local secrets file and
    never written anywhere.
"""
from __future__ import annotations

import json
import os
import pathlib
import time
import urllib.error
import urllib.request
from typing import Any

OPENROUTER = "https://openrouter.ai/api/v1"
SECRETS = pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "secrets" / "openrouter-test.env"


def openrouter_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key and SECRETS.is_file():
        for line in SECRETS.read_text(encoding="utf-8").splitlines():
            if line.startswith("OPENROUTER_API_KEY="):
                key = line.split("=", 1)[1].strip()
    if not key:
        raise RuntimeError("no OpenRouter key (OPENROUTER_API_KEY or local secrets file)")
    return key


class PaidViolation(RuntimeError):
    pass


class Worker:
    def __init__(self, base: str, model: str, *, recorder=None, timeout: float = 900.0,
                 max_retries: int = 6, extra_body: dict | None = None,
                 allow_paid: bool = False, max_total_cost_usd: float = 0.0):
        self.base = base.rstrip("/")
        self.model = model
        self.remote = self.base.startswith("https://openrouter.ai")
        self.free_model = model.endswith(":free")
        self.allow_paid = bool(allow_paid)
        self.max_total_cost_usd = float(max_total_cost_usd or 0.0)
        self.total_cost_usd = 0.0
        self.cost_unknown = False
        if self.remote and not self.free_model:
            if not self.allow_paid:
                raise PaidViolation(f"refusing non-free OpenRouter model {model}")
            if self.max_total_cost_usd <= 0:
                raise PaidViolation("paid OpenRouter worker requires a positive max_total_cost_usd")
        self.key = openrouter_key() if self.remote else ""
        self.recorder = recorder
        self.timeout = timeout
        self.max_retries = max_retries
        self.extra_body = extra_body or {}
        self.events: list[dict] = []          # rate-limit / retry log (no secrets)
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if self.remote:
            h["Authorization"] = "Bearer " + self.key
            h["HTTP-Referer"] = "https://github.com/molotroka123-cell/AiMaxBossman"
            h["X-Title"] = "Bossman owner run"
        return h

    def chat(self, messages: list[dict], *, tools: list | None = None, max_tokens: int = 16000,
             temperature: float = 0.2, task_class: str = "adhoc", response_format: dict | None = None,
             record: bool = True, verdict: str = "UNVERIFIED", curator_note: str = "") -> dict:
        if self.remote and not self.free_model and (
            self.cost_unknown or self.total_cost_usd >= self.max_total_cost_usd
        ):
            return {"text": "", "reasoning": "", "tool_calls": [], "usage": {},
                    "error": "PAID_BUDGET_EXHAUSTED", "attempts": 0,
                    "latency_s": None, "ttft_ms": None, "tps": None,
                    "budget": self.budget()}
        body: dict[str, Any] = {"model": self.model, "messages": messages, "max_tokens": max_tokens,
                                "temperature": temperature, "stream": True,
                                "stream_options": {"include_usage": True}, **self.extra_body}
        if tools:
            body["tools"] = tools
        if response_format:
            body["response_format"] = response_format
        if self.remote:
            body["usage"] = {"include": True}
        attempt = 0
        while True:
            attempt += 1
            try:
                out = self._stream(body)
                out["attempts"] = attempt
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:400]
                self.events.append({"ts": time.time(), "status": exc.code, "attempt": attempt,
                                    "retry_after": exc.headers.get("Retry-After"), "detail": detail[:200]})
                if exc.code in (429, 500, 502, 503, 504) and attempt <= self.max_retries:
                    wait = float(exc.headers.get("Retry-After") or min(60, 5 * 2 ** (attempt - 1)))
                    time.sleep(wait)
                    continue
                out = {"text": "", "reasoning": "", "tool_calls": [], "usage": {}, "error":
                       f"HTTP {exc.code}: {detail}", "attempts": attempt, "latency_s": None, "ttft_ms": None,
                       "tps": None}
                break
            except (urllib.error.URLError, OSError, ValueError) as exc:
                self.events.append({"ts": time.time(), "status": "net", "attempt": attempt,
                                    "detail": f"{type(exc).__name__}: {str(exc)[:160]}"})
                if attempt <= self.max_retries:
                    time.sleep(min(60, 5 * 2 ** (attempt - 1)))
                    continue
                out = {"text": "", "reasoning": "", "tool_calls": [], "usage": {},
                       "error": f"{type(exc).__name__}: {exc}", "attempts": attempt,
                       "latency_s": None, "ttft_ms": None, "tps": None}
                break
        cost = (out.get("usage") or {}).get("cost")
        if self.remote and self.free_model and cost not in (None, 0, 0.0):
            out["error"] = f"PAID_VIOLATION cost={cost}"
        elif self.remote and not self.free_model:
            if not isinstance(cost, (int, float)) or isinstance(cost, bool) or cost < 0:
                self.cost_unknown = True
                out["error"] = "PAID_COST_UNKNOWN"
            else:
                self.total_cost_usd += float(cost)
                if self.total_cost_usd > self.max_total_cost_usd + 1e-12:
                    out["error"] = (
                        f"PAID_BUDGET_EXCEEDED total={self.total_cost_usd:.6f} "
                        f"cap={self.max_total_cost_usd:.6f}"
                    )
        if record and self.recorder is not None:
            out["record_id"] = self.recorder.record(
                model=self.model, task_class=task_class, messages=messages, tools=tools,
                response_text=out["text"], reasoning=out.get("reasoning"), tool_calls=out["tool_calls"],
                latency_s=out.get("latency_s"), ttft_ms=out.get("ttft_ms"), usage=out.get("usage"),
                verdict=verdict, curator_note=curator_note, error=out.get("error"),
                extra={"attempts": out.get("attempts"), "tps": out.get("tps"),
                       "served_model": out.get("served_model"), "finish_reason": out.get("finish_reason")})
        out["budget"] = self.budget()
        return out

    def budget(self) -> dict:
        return {"model": self.model, "free": self.free_model,
                "allow_paid": self.allow_paid, "spent_usd": round(self.total_cost_usd, 8),
                "max_total_cost_usd": self.max_total_cost_usd,
                "cost_unknown": self.cost_unknown}

    def _stream(self, body: dict) -> dict:
        req = urllib.request.Request(self.base + "/chat/completions", data=json.dumps(body).encode("utf-8"),
                                     headers=self._headers(), method="POST")
        started = time.monotonic()
        first = None
        text, reasoning = [], []
        calls: dict[int, dict] = {}
        usage: dict = {}
        served = None
        finish = None
        with self._opener.open(req, timeout=self.timeout) as resp:  # noqa: S310
            for raw in resp:
                line = raw.strip()
                if not line.startswith(b"data:"):
                    continue
                payload = line[5:].strip()
                if payload == b"[DONE]":
                    break
                obj = json.loads(payload)
                if obj.get("error"):
                    raise ValueError(f"stream error: {json.dumps(obj['error'])[:300]}")
                served = obj.get("model") or served
                if obj.get("usage"):
                    usage = obj["usage"]
                for ch in obj.get("choices") or []:
                    finish = ch.get("finish_reason") or finish
                    d = ch.get("delta") or {}
                    piece_r = d.get("reasoning") or d.get("reasoning_content") or ""
                    if (d.get("content") or piece_r or d.get("tool_calls")) and first is None:
                        first = time.monotonic()
                    if d.get("content"):
                        text.append(d["content"])
                    if piece_r:
                        reasoning.append(piece_r)
                    for tc in d.get("tool_calls") or []:
                        slot = calls.setdefault(tc.get("index", 0), {"id": None, "type": "function",
                                                                      "function": {"name": "", "arguments": ""}})
                        slot["id"] = tc.get("id") or slot["id"]
                        fn = tc.get("function") or {}
                        slot["function"]["name"] += fn.get("name") or ""
                        slot["function"]["arguments"] += fn.get("arguments") or ""
        end = time.monotonic()
        comp = int(usage.get("completion_tokens") or 0)
        gen = (end - first) if first else None
        return {"text": "".join(text), "reasoning": "".join(reasoning),
                "tool_calls": [calls[i] for i in sorted(calls)], "usage": usage,
                "latency_s": round(end - started, 3),
                "ttft_ms": round((first - started) * 1000, 1) if first else None,
                "tps": round(comp / gen, 2) if comp and gen and gen > 0 else None,
                "served_model": served, "finish_reason": finish, "error": None}
