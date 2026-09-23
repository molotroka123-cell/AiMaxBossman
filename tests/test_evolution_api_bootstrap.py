"""Evolution API worker must import the one loop with embedded Python rules."""
from __future__ import annotations

import pytest
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_worker_command_imports_real_loop_in_isolated_python(tmp_path):
    pytest.importorskip("pydantic", reason="bcc.features.evolution needs pydantic (root-ci installs no Command Center deps)")
    # This cloud image has no FastAPI installation; stub only the route
    # registration layer while exercising the real API's bootstrap function
    # and the real bossman_v3 loop inside an independent -I child.
    code = f'''import json, sys, types
sys.path[:0] = {[str(ROOT / 'command-center'), str(ROOT / 'bossman-core'), str(ROOT)]!r}
class Router:
    def __init__(self, *a, **kw): pass
    def get(self, *a, **kw): return lambda fn: fn
    post = get
class HttpError(Exception):
    def __init__(self, status_code, detail): self.status_code, self.detail = status_code, detail
sys.modules["fastapi"] = types.SimpleNamespace(APIRouter=Router, HTTPException=HttpError, Request=object)
sys.modules["bcc.features.tools_code"] = types.SimpleNamespace(_within=lambda *a: True, allowed_roots=lambda *a: [])
from bcc.features import evolution
from bossman_v3.self_improvement import loop
script = evolution._bootstrap(loop)
import subprocess
child = subprocess.run([sys.executable, "-I", "-c", script, "status", "--work", {str(tmp_path / 'campaign')!r}],
                       capture_output=True, text=True, timeout=30)
print(json.dumps({{"exit": child.returncode, "body": json.loads(child.stdout) if child.stdout else None,
                  "error": child.stderr[-300:]}}))
'''
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["exit"] == 0, payload["error"]
    assert payload["body"]["status"] == "NO_CAMPAIGN"
