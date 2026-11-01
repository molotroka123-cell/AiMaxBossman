"""Start a THROW-AWAY Bossman backend from this checkout for the lesson-transfer experiment.

    python launch_temp_backend.py <port> <temp root>

<temp root>/data = BCC_DATA_DIR, <temp root>/lad = LOCALAPPDATA, <temp root>/exkeys = BCC_EXERCISE_KEYS_DIR (outside the
data dir and the repo). Refuses the owner's ports 8800/8801 and any root that is the owner's real data dir.
"""
import os
import sys
from pathlib import Path

port = int(sys.argv[1])
root = Path(sys.argv[2]).resolve()
if port in (8800, 8801):
    raise SystemExit("refusing owner port")
owner = Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman"
if owner.exists() and str(root).lower().startswith(str(owner.resolve()).lower()):
    raise SystemExit("refusing: temp root inside the owner's Bossman data")
for sub in ("data", "lad", "exkeys"):
    (root / sub).mkdir(parents=True, exist_ok=True)
os.environ["BCC_DATA_DIR"] = str(root / "data")
os.environ["LOCALAPPDATA"] = str(root / "lad")
os.environ["BCC_EXERCISE_KEYS_DIR"] = str(root / "exkeys")
os.environ["BCC_PORT"] = str(port)
repo = Path(__file__).resolve().parents[3]
for p in (repo, repo / "bossman-core", repo / "command-center"):
    sys.path.insert(0, str(p))
from bcc.app import main  # noqa: E402

main(["--port", str(port)])
