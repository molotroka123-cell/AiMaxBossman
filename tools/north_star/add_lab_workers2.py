"""LAB-ONLY (second route): Gemini through the owner's second free-tier key (separate quota). Same rules as
add_lab_workers.py: edits only the lab clone's uncommitted coding_tasks.py; diff appended to the evidence folder."""
import difflib
import sys
from pathlib import Path

target = Path(r"C:\Users\asd\Bossman\ns-lab-20261010\repo\command-center\bcc\features\coding_tasks.py")
text = target.read_text(encoding="utf-8")
if "gemini-flash-2" in text:
    print("already patched")
    sys.exit(0)
anchor = '    "mistral-large": {'
new = ('    "gemini-flash-2": {"endpoint": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "gemini-3.5-flash",\n'
       '                       "key": "GEMINI_API_KEY_2", "label": "Google AI Studio · Gemini 3.5 Flash (free tier, key 2)"},\n') + anchor
assert text.count(anchor) == 1
patched = text.replace(anchor, new)
target.write_text(patched, encoding="utf-8", newline="")
Path(sys.argv[1]).write_text("".join(difflib.unified_diff(text.splitlines(True), patched.splitlines(True),
                                                          "a/coding_tasks.py", "b/coding_tasks.py")), encoding="utf-8")
print("patched")
