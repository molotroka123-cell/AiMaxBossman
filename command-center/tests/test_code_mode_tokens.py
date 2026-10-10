"""Measured token cost of the facade vs the baseline on the REAL registry (no network).

The numbers are printed (`pytest -s`) and asserted with margins; the docs quote them.
Proxy: chars/4 (tokenizer-agnostic); cl100k only when tiktoken works offline.
"""
from __future__ import annotations

import copy

from bcc.code_mode.catalog import ToolCatalog
from bcc.code_mode.measure import SCENARIOS, report
from bcc.tools import REGISTRY


def _grow(specs, target):
    out = list(specs)
    i = 0
    while len(out) < target:
        clone = copy.copy(specs[i % len(specs)])
        clone.name = f"{clone.name}.v{i // len(specs) + 2}"
        out.append(clone)
        i += 1
    return out


async def test_facade_cost_is_flat_while_the_baseline_grows_with_the_tool_count(env):
    everything = REGISTRY.all()
    assert len(everything) >= 50, "the real registry must be loaded"
    small = report(everything)
    large = report(_grow(everything, 1000))

    print("\nall registered tools:", small["tools"], small["per_call"]["baseline"],
          "facade", small["per_call"]["facade"], small["typical_turn_mean"])
    print("1000 tools:", large["per_call"]["baseline"], "facade", large["per_call"]["facade"],
          large["typical_turn_mean"])

    # per call: the facade is a small constant
    assert small["per_call"]["facade"]["chars"] == large["per_call"]["facade"]["chars"]
    assert small["per_call"]["facade"]["chars"] < 1800
    assert small["ratio_per_call"] < 0.06
    # per typical turn (4 model calls + one search listing + one snippet)
    assert small["typical_turn_mean"]["ratio"] < 0.35
    assert large["typical_turn_mean"]["ratio"] < 0.06
    # the baseline is linear in the tool count, the facade turn is not
    assert large["per_call"]["baseline"]["chars"] > 8 * small["per_call"]["baseline"]["chars"]
    assert large["typical_turn_mean"]["facade_chars"] < 2 * small["typical_turn_mean"]["facade_chars"]


async def test_search_listing_stays_small_and_finds_the_tools_of_the_scenarios(env):
    catalog = ToolCatalog(REGISTRY.all())
    expected = {"terminal.run", "memory.search", "browser.open"}
    for (query, _code), want in zip(SCENARIOS, ["terminal.run", "memory.search", "browser.open"]):
        hits = [e.spec.name for e in catalog.search(query, 5)]
        assert want in hits, (query, hits)
        assert len(catalog.render_search(query, 5)) < 3500
    assert expected
