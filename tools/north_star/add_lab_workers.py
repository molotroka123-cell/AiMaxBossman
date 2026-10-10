"""LAB-ONLY: add two cloud-worker routes to the LAB CLONE's coding_tasks.WORKERS allowlist (not to this branch).

The allowlist is fixed in code by design (endpoint/model/credential never come from the request), so a new model
route needs a code line. This touches only C:\\Users\\asd\\Bossman\\ns-lab-20261010\\repo (an uncommitted edit, outside
every task's sandbox, which is built from the committed base). The diff is written to the evidence folder.
"""
import difflib
import sys
from pathlib import Path

target = Path(r"C:\Users\asd\Bossman\ns-lab-20261010\repo\command-center\bcc\features\coding_tasks.py")
text = target.read_text(encoding="utf-8")
if "gemini-flash" in text:
    print("already patched")
    sys.exit(0)
anchor = '''    "haiku-5.5": {"endpoint": "https://openrouter.ai/api/v1", "model": "anthropic/claude-haiku-5.5",
                  "key": "OPENROUTER_API_KEY", "label": "OpenRouter · Claude Haiku 5.5 (paid, cheap)"},
'''
new = anchor + '''    # LAB-ONLY (north star 10.10): Gemini free tier through its OpenAI-compatible endpoint, Mistral Large 4 via OpenRouter
    "gemini-flash": {"endpoint": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "gemini-3.5-flash",
                     "key": "GEMINI_API_KEY", "label": "Google AI Studio · Gemini 3.5 Flash (free tier)"},
    "mistral-large": {"endpoint": "https://openrouter.ai/api/v1", "model": "mistralai/mistral-large-4-0",
                      "key": "OPENROUTER_API_KEY", "label": "OpenRouter · Mistral Large 4 (paid)"},
'''
if anchor not in text:
    sys.exit("anchor not found (line endings?)")
patched = text.replace(anchor, new)
target.write_text(patched, encoding="utf-8", newline="")
out = Path(sys.argv[1])
out.write_text("".join(difflib.unified_diff(text.splitlines(True), patched.splitlines(True), "a/coding_tasks.py",
                                            "b/coding_tasks.py")), encoding="utf-8")
print("patched; diff ->", out)
