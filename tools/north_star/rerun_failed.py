"""Re-run the failing test ids of a scan serially (no xdist) on the lab clone, with and without the machine's
local model endpoints hidden, and record which still fail. Environment-dependent failures drop out here.

    python tools/north_star/rerun_failed.py <junit.xml> <out.json> [--offline]
--offline sets OLLAMA_HOST/SEARX URL to a dead loopback port so tests that assume 'no local model' see none.
"""
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

LAB = Path(r"C:\Users\asd\Bossman\ns-lab-20261010")
junit, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
offline = "--offline" in sys.argv
ids = []
for c in ET.parse(junit).getroot().iter("testcase"):
    if c.find("failure") is None and c.find("error") is None:
        continue
    parts = c.get("classname").split(".")
    i = next(k for k, p in enumerate(parts) if p.startswith("test_"))
    ids.append("/".join(parts[: i + 1]) + ".py::" + c.get("name"))
env = dict(os.environ, PYTHONUTF8="1", LOCAL_ONLY="1", TEMP=str(LAB / "scan" / "tmp"), TMP=str(LAB / "scan" / "tmp"),
           BCC_DATA_DIR=str(LAB / "scan" / "data2"))
if offline:
    env.update(OLLAMA_HOST="127.0.0.1:9", OLLAMA_BASE_URL="http://127.0.0.1:9", BOSSMAN_SEARXNG_URL="http://127.0.0.1:9")
xml = out.with_suffix(".junit.xml")
res = subprocess.run([str(LAB / "venv" / "Scripts" / "python.exe"), "-m", "pytest", *ids, "-q", "-p", "no:cacheprovider",
                      "--tb=short", f"--junitxml={xml}"], cwd=str(LAB / "repo"), env=env, capture_output=True,
                     text=True, encoding="utf-8", errors="replace", timeout=1500)
still = []
for c in ET.parse(xml).getroot().iter("testcase"):
    if c.find("failure") is not None or c.find("error") is not None:
        still.append(c.get("classname") + "::" + c.get("name"))
out.write_text(json.dumps({"offline": offline, "input_failed": len(ids), "still_failing": len(still),
                           "still": still, "tail": res.stdout[-1500:]}, indent=1), encoding="utf-8")
print("input", len(ids), "still failing", len(still))
