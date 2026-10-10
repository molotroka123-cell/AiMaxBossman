"""Token accounting for the code-mode facade (measured, tokenizer-agnostic).

Two proxies are reported: chars/4 (always) and tiktoken cl100k_base (only when the
package AND its vocabulary are available offline; otherwise `None`, never a guess).

Baseline = what the engine puts in `tools=[...]` of EVERY model call today: one JSON schema
per granted tool. Facade = the two facade schemas on every call, plus - once per task - the
search listing the model asked for and the snippet it wrote.
"""
from __future__ import annotations

import json
from typing import Any, Iterable

from .catalog import ToolCatalog, estimate_tokens
from .facade import facade_specs


def schema_text(spec: Any) -> str:
    return json.dumps(spec.schema(), ensure_ascii=False)


def tiktoken_count(texts: Iterable[str]) -> int | None:
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
    except Exception:  # noqa: BLE001 - missing package or no offline vocabulary
        return None
    return sum(len(enc.encode(t)) for t in texts)


def count(texts: list[str]) -> dict:
    chars = sum(len(t) for t in texts)
    return {"chars": chars, "tokens_chars4": estimate_tokens("x" * chars) if chars else 0,
            "tokens_cl100k": tiktoken_count(texts)}


def compare(specs: list[Any], scenarios: list[tuple[str, str]], *, steps_per_turn: int = 4,
            k: int = 5) -> dict:
    """`scenarios` = [(search query, snippet the model would write)]."""
    base_texts = [schema_text(s) for s in specs]
    facade_texts = [schema_text(s) for s in facade_specs()]
    catalog = ToolCatalog(specs)
    listings = [catalog.render_search(q, k) for q, _ in scenarios]
    snippets = [code for _, code in scenarios]
    per_call_base = count(base_texts)
    per_call_facade = count(facade_texts)
    turn_base = count(base_texts * steps_per_turn)
    turn_facade = count(facade_texts * steps_per_turn + listings + snippets)
    return {
        "tools": len(specs),
        "steps_per_turn": steps_per_turn,
        "per_call": {"baseline": per_call_base, "facade": per_call_facade},
        "typical_turn": {"baseline": turn_base, "facade": turn_facade,
                         "scenarios": len(scenarios),
                         "includes": "tool schemas on every model call; facade also counts the search "
                                     "listing and the snippet. Tool RESULTS are not counted on either side "
                                     "(the facade keeps them in the sandbox, which only adds savings)."},
        "ratio_per_call": round(per_call_facade["chars"] / max(1, per_call_base["chars"]), 4),
        "ratio_typical_turn": round(turn_facade["chars"] / max(1, turn_base["chars"]), 4),
    }


SCENARIOS: list[tuple[str, str]] = [
    ("run a shell command and read its output",
     "r = tools.terminal_run(command='git status --short')\nprint(r['content'][:500])"),
    ("search memory for a stored fact",
     "r = tools.memory_search(query='project deadline')\nprint(r['content'][:800])"),
    ("open a web page and read its text",
     "tools.browser_open(url='https://example.com')\nr = tools.browser_read_dom()\nprint(r['content'][:1000])"),
]


def report(specs: list[Any], scenarios: list[tuple[str, str]] | None = None, *, steps_per_turn: int = 4,
           k: int = 5) -> dict:
    """One `compare` per scenario (one search + one snippet per turn) and their mean."""
    scenarios = scenarios or SCENARIOS
    rows = [compare(specs, [s], steps_per_turn=steps_per_turn, k=k) for s in scenarios]
    base = sum(r["typical_turn"]["baseline"]["chars"] for r in rows) / len(rows)
    fac = sum(r["typical_turn"]["facade"]["chars"] for r in rows) / len(rows)
    first = rows[0]
    return {
        "tools": first["tools"], "steps_per_turn": steps_per_turn,
        "per_call": first["per_call"],
        "typical_turn_mean": {"baseline_chars": round(base), "facade_chars": round(fac),
                              "baseline_tokens_chars4": round(base / 4),
                              "facade_tokens_chars4": round(fac / 4),
                              "ratio": round(fac / max(1.0, base), 4)},
        "per_scenario_ratio": [r["ratio_typical_turn"] for r in rows],
        "ratio_per_call": first["ratio_per_call"],
    }
