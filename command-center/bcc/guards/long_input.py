"""G2 — long-input / empty-result recovery for cloud models.

Incident 03.11.2026: Jev (GLM 5.3 Flash) returned an EMPTY answer on 40k characters of game code and answered the
same question on a 13k excerpt. An empty answer on a long input is a size problem, not a dead model.

Rule: if the answer is empty (no text, no tool call) and the input is long, retry ONCE with
  * the long message reduced to the most relevant excerpts (<= ~12k chars), chosen by keyword overlap with the
    question over line windows, in original order, with `...` gaps and line numbers kept, and
  * a strict short output format appended.
Which variant answered is recorded (`variant: "full" | "reduced"`), so a model that needs excerpts is visible.
Pure functions; the engine hook is `Engine._call_model` (opt-in per agent: permissions.empty_recovery or the
harness ladder flag free_pool_first)."""
from __future__ import annotations

import re
from typing import Any

#: target size of the reduced input (the 13k excerpt answered, 40k did not)
MAX_EXCERPT_CHARS = 12_000
#: below this the input is not «long»: an empty answer there is not a size problem
LONG_INPUT_CHARS = 16_000
WINDOW_LINES = 40
STEP_LINES = 20
STRICT_FORMAT = ("\n\n[ФОРМАТ ОТВЕТА] Отвечай кратко: не более 15 пунктов списком, каждый — файл:строка + одна "
                 "фраза. Без вступления и повторения кода. Если данных недостаточно — так и напиши одной строкой.")
_WORD = re.compile(r"[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё_0-9]{2,}")
_STOP = frozenset("the and for with that this from are was you not have has але как что это для при или все его "
                  "она они так где только если чтобы файл file line local end then return function".split())


def is_empty_answer(result: Any) -> bool:
    text = str(getattr(result, "text", "") or "")
    return not text.strip() and not (getattr(result, "tool_calls", None) or [])


def _terms(text: str) -> set[str]:
    return {w.lower()[:6] for w in _WORD.findall(text or "") if w.lower() not in _STOP}


def relevant_excerpt(text: str, query: str, *, max_chars: int = MAX_EXCERPT_CHARS,
                     window: int = WINDOW_LINES, step: int = STEP_LINES) -> str:
    """The most query-relevant line windows of `text`, in original order, total <= max_chars.

    Score = distinct query terms (6-char stems) in the window, weighted by rarity across windows; ties keep the
    earlier window. Line numbers are prefixed so «file:line» answers stay correct."""
    if len(text) <= max_chars:
        return text
    lines = text.splitlines()
    q = _terms(query)
    starts = list(range(0, max(1, len(lines)), step))
    wins = [(s, min(len(lines), s + window)) for s in starts]
    terms_per = [_terms("\n".join(lines[a:b])) for a, b in wins]
    df: dict[str, int] = {}
    for ts in terms_per:
        for t in ts & q:
            df[t] = df.get(t, 0) + 1
    n = len(wins)
    scores = [sum(1.0 + (n / (1 + df[t])) ** 0.5 for t in (ts & q)) for ts in terms_per]
    order = sorted(range(n), key=lambda i: (-scores[i], i))
    chosen: set[int] = set()
    budget = 0
    for i in order:
        if scores[i] <= 0 and chosen:
            break
        a, b = wins[i]
        new = [ln for ln in range(a, b) if ln not in chosen]
        cost = sum(len(lines[ln]) + 8 for ln in new)
        if budget + cost > max_chars:
            continue
        chosen.update(new)
        budget += cost
    out: list[str] = []
    prev = -2
    for ln in sorted(chosen):
        if ln != prev + 1:
            out.append("...")
        out.append(f"{ln + 1}: {lines[ln]}")
        prev = ln
    return "\n".join(out)


def _content_text(msg: dict) -> str | None:
    c = msg.get("content")
    return c if isinstance(c, str) else None


def reduce_messages(messages: list[dict], *, max_chars: int = MAX_EXCERPT_CHARS,
                    long_chars: int = LONG_INPUT_CHARS) -> tuple[list[dict], dict] | None:
    """Messages with the LONGEST string content (user/tool) cut to relevant excerpts + strict format on the last
    user turn, or None when nothing is long enough to be the cause. Returns (messages, info)."""
    idx = [i for i, m in enumerate(messages)
           if isinstance(m, dict) and m.get("role") in ("user", "tool") and _content_text(m) is not None]
    if not idx:
        return None
    longest = max(idx, key=lambda i: len(messages[i]["content"]))
    big = messages[longest]["content"]
    if len(big) < long_chars:
        return None
    users = [i for i in idx if messages[i].get("role") == "user"]
    last_user = users[-1] if users else longest
    # the question = last user turn without the bulk (its first and last 1500 chars carry the ask)
    q_src = messages[last_user]["content"]
    query = q_src if len(q_src) < 3000 else q_src[:1500] + "\n" + q_src[-1500:]
    excerpt = relevant_excerpt(big, query, max_chars=max_chars)
    out = [dict(m) if isinstance(m, dict) else m for m in messages]
    out[longest] = {**out[longest], "content": "[СОКРАЩЕНО harness G2: релевантные фрагменты; N: — номер строки исходного сообщения]\n"
                    + excerpt}
    out[last_user] = {**out[last_user], "content": out[last_user]["content"] + STRICT_FORMAT}
    return out, {"variant": "reduced", "chars_before": len(big), "chars_after": len(excerpt),
                 "message_index": longest}


def enabled_for(agent: dict) -> bool:
    perms = agent.get("permissions") if isinstance(agent.get("permissions"), dict) else {}
    flag = perms.get("empty_recovery")
    return bool(flag) if flag is not None else bool(perms.get("free_pool_first"))
