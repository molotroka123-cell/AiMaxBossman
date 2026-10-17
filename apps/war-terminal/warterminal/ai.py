"""Local-model adapter: analysis and drafts ONLY. A model never decides compliance, never changes a gate and never sees company secrets.

Sends the model only public tender facts (redacted), enforces a daily call budget, parses a strict JSON answer and reports disagreement
with the deterministic tier. Provider: any OpenAI-compatible endpoint (Bossman local Ollama/llama.cpp, or an OpenRouter free model).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import httpx

REDACT = [(re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"), (re.compile(r"\+?\d[\d\s().-]{8,}\d"), "[phone]"),
          (re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"), "[iban]")]
TIERS = ("EASY", "MEDIUM", "HARD", "REVIEW_REQUIRED")


def redact(text: str) -> str:
    for rx, sub in REDACT:
        text = rx.sub(sub, text)
    return text


def build_messages(tender: dict) -> list[dict]:
    facts = {k: tender.get(k) for k in ("title", "category", "country", "estimated_value", "currency", "deadline", "cpv", "status")}
    facts = json.loads(redact(json.dumps(facts, ensure_ascii=False)))
    system = ("You assist a small NEW company in the EU with public-procurement triage. You are NOT the compliance authority. "
              "Classify practical difficulty for a company with limited working capital: EASY, MEDIUM, HARD, or REVIEW_REQUIRED when facts are missing. "
              "Reply with ONE JSON object: {\"tier\":..., \"rationale\":\"<=300 chars\", \"risks\":[\"...\"]}. Never claim a probability of winning.")
    return [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(facts, ensure_ascii=False)}]


def parse_reply(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("model reply has no JSON")
    data = json.loads(m.group(0))
    tier = str(data.get("tier", "")).upper()
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}")
    return {"tier": tier, "rationale": str(data.get("rationale", ""))[:600], "risks": [str(r)[:200] for r in (data.get("risks") or [])][:5]}


def make_chat(base_url: str, model: str, api_key: str | None = None, timeout: int = 180):
    """OpenAI-compatible chat. base_url like http://127.0.0.1:11435/v1 (Bossman local) or https://openrouter.ai/api/v1."""
    def chat(messages: list[dict]) -> str:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        r = httpx.post(base_url.rstrip("/") + "/chat/completions", headers=headers, timeout=timeout,
                       json={"model": model, "messages": messages, "temperature": 0.1, "max_tokens": 700})
        r.raise_for_status()
        return r.json()["choices"][0]["message"].get("content") or ""
    return chat


def classify(tender: dict, chat, store, *, daily_limit: int = 50) -> dict:
    """Returns {status: OK|NOT_CONNECTED|BUDGET_EXCEEDED|ERROR, ...}. Deterministic rules remain the authority."""
    if chat is None:
        return {"status": "NOT_CONNECTED", "reason": "локальная модель не настроена (WAR_TERMINAL_AI_URL / WAR_TERMINAL_AI_MODEL)"}
    day = datetime.now(timezone.utc).date().isoformat()
    used = store.kv_get(f"ai_calls:{day}", 0)
    if used >= daily_limit:
        return {"status": "BUDGET_EXCEEDED", "reason": f"дневной лимит вызовов модели {daily_limit} исчерпан"}
    store.kv_set(f"ai_calls:{day}", used + 1)
    try:
        return {"status": "OK", **parse_reply(chat(build_messages(tender)))}
    except Exception as exc:                                    # noqa: BLE001 - a bad model reply must not break assessment
        return {"status": "ERROR", "reason": f"{type(exc).__name__}: {str(exc)[:160]}"}


def disagreement(ai_tier: str | None, det_tier: str | None) -> bool:
    order = {"EASY": 0, "MEDIUM": 1, "HARD": 2}
    return bool(ai_tier in order and det_tier in order and abs(order[ai_tier] - order[det_tier]) >= 1)
