"""List model ids available to the owner's Gemini and NVIDIA keys (ids only; keys never printed)."""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import providers as p  # noqa: E402

flt = sys.argv[1] if len(sys.argv) > 1 else ""
try:
    url = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200&key=" + (p._key("GEMINI_API_KEY") or "")
    data = json.loads(urllib.request.urlopen(url, timeout=30).read())
    print("gemini:", [m["name"] for m in data.get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])])
except Exception as exc:  # noqa: BLE001
    print("gemini list FAIL", type(exc).__name__, str(exc)[:120])
try:
    req = urllib.request.Request("https://integrate.api.nvidia.com/v1/models",
                                 headers={"Authorization": "Bearer " + (p._key("NVIDIA_API_KEY") or "")})
    data = json.loads(urllib.request.urlopen(req, timeout=30).read())
    print("nvidia:", [m["id"] for m in data.get("data", []) if flt in m["id"]])
except Exception as exc:  # noqa: BLE001
    print("nvidia list FAIL", type(exc).__name__, str(exc)[:120])
