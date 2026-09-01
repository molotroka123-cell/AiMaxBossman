#!/usr/bin/env bash
python3 - <<'PY'
import json
d = json.load(open("/workspace/benchmarks/ab_medium_qwen25_14b.log"))
print("DIRECT ", d["direct"]["verified_success"], d["direct"]["total_samples"])
print("BOSSMAN", d["bossman"]["verified_success"], d["bossman"]["total_samples"])
print("direct classes ", {k: v["verified_success"] for k, v in d["direct"]["task_classes"].items()})
print("bossman classes", {k: v["verified_success"] for k, v in d["bossman"]["task_classes"].items()})
print("retention", d["intelligence_retention"], "cloud", d["cloud_calls"], "vram_mib", d["resources"]["peak_vram_mib"])
for name in ("reasoning", "long_context"):
    dc = d["direct"]["task_classes"][name]
    print(name, "direct in/out tok:", dc["input_tokens"], dc["output_tokens"], "p50", round(dc["p50_latency_s"], 2))
PY