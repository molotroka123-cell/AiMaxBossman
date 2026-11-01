"""EXPLORATORY, NOT PREREGISTERED (written after the results). Does not change the verdict.

Splits the held-out failures into "rule not applied" vs "rule applied, arithmetic wrong":
* margin_applied: the answer is within 1 % of the truth AND closer to it than to the no-margin value;
* rounding: where the model wrote its own unrounded product and the rounded result ("X.xxx -> Y.yy"),
  was the direction the desk's (BUY up / SELL down)? This isolates the rounding rule from the multiplication.
"""
import json
import re
from decimal import ROUND_DOWN, ROUND_UP, Decimal as D
from pathlib import Path

import scorer as S

HERE = Path(__file__).resolve().parent
truth = json.loads((HERE / "truth.json").read_text(encoding="utf-8"))["truth"]
PAIR = re.compile(r"(\d+\.\d{3,})\s*(?:rightarrow|→|->|≈|=)\s*(\d+\.\d{2})(?!\d)")
out = {}
for phase in ("baseline", "after"):
    rows = [r for r in S.load(HERE / "raw" / f"raw_{phase}.jsonl") if r["task"].startswith("H")]
    margin = exact = pairs_seen = dir_ok = 0
    for r in rows:
        t = truth[r["task"]]
        value, _ = S.parse(r["answer"], "money")
        got = S._to_decimal(value) if value else None
        tv, nm = D(t["value"]), D(t["diag"]["no_margin_would_be"])
        if got is not None and abs(got - tv) < abs(got - nm) and abs(got - tv) / tv < D("0.01"):
            margin += 1
        exact += got == tv
        text = S._strip_md(r["answer"]).replace("\\", "")
        found = PAIR.findall(text)
        if found:
            raw, rounded = D(found[-1][0]), D(found[-1][1])
            pairs_seen += 1
            want = raw.quantize(D("0.01"), ROUND_UP if t["params"]["side"] == "BUY" else ROUND_DOWN)
            dir_ok += rounded == want
    out[phase] = {"heldout_runs": len(rows), "exact_correct": exact, "margin_applied_within_1pct": margin,
                  "own_rounding_steps_seen": pairs_seen, "own_rounding_in_desk_direction": dir_ok}
(HERE / "exploratory_rule_application.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
print(json.dumps(out, indent=1))
