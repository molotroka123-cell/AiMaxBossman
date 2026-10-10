"""Cheap probe (<$0.01): how does Mistral Large 4 on OpenRouter behave with/without response_format and reasoning opts."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import providers as p  # noqa: E402

key = p._key("OPENROUTER_API_KEY")


def call(label, extra, rf):
    body = {"model": "mistralai/mistral-large-4-0", "temperature": 0, "max_tokens": 600, "usage": {"include": True},
            "messages": [{"role": "user", "content": 'Return JSON {"edits": [{"path":"a.py","old":"","new":"x=1"}]} and nothing else.'}]}
    if rf:
        body["response_format"] = {"type": "json_object"}
    body.update(extra)
    try:
        d = p._post(p.ENDPOINTS["openrouter"], key, body, 120)
    except Exception as exc:  # noqa: BLE001
        print(label, "ERR", str(exc)[:200])
        return
    m, u = d["choices"][0]["message"], d.get("usage", {})
    print(label, "| content:", repr((m.get("content") or "")[:100]), "| nonempty keys:", [k for k in m if m[k]],
          "| ct", u.get("completion_tokens"), "| reasoning_tokens",
          (u.get("completion_tokens_details") or {}).get("reasoning_tokens"), "| cost", u.get("cost"),
          "| finish", d["choices"][0].get("finish_reason"))


call("plain", {}, False)
call("json_mode", {}, True)
call("reasoning.enabled=False", {"reasoning": {"enabled": False}}, False)
call("reasoning.max_tokens=1", {"reasoning": {"max_tokens": 1}}, False)
