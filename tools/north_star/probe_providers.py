"""Probe the reviewer providers once (tiny prompt) and print OpenRouter usage counters. No key is printed."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import providers as p  # noqa: E402

ledger = Path(sys.argv[1]) / "spend.jsonl"
print("openrouter_usage", json.dumps(p.openrouter_usage()))
pairs = [tuple(x.split(":", 1)) for x in sys.argv[2:]] or [
    ("gemini", "gemini-3.5-flash"), ("gemini", "models/gemini-2.5-flash"), ("nvidia", "moonshotai/kimi-k3"),
    ("nvidia", "deepseek-ai/deepseek-v4.1-flash"), ("nvidia", "z-ai/glm-5.3")]
for prov, model in pairs:
    try:
        r = p.chat(prov, model, 'Return {"ok": true} as JSON.', ledger=ledger, purpose="probe", max_tokens=200)
        print(prov, model, "OK", r["text"][:80].replace("\n", " "), r["seconds"])
    except Exception as exc:  # noqa: BLE001
        print(prov, model, "FAIL", str(exc)[:200])
