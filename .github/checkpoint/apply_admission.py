"""Deterministic six-file delivery; all input/output hashes checked before writes."""
from pathlib import Path
import base64
import gzip
import hashlib
import io
import json

ALLOWED = {
    "bossman_shared/objective_admission.py",
    "bossman_shared/objective_mission.py",
    "bossman_shared/objective_store.py",
    "tests/test_v5_admission.py",
    "tests/test_v5_golden_missions.py",
    "tests/test_v5_admission_binding_regressions.py",
}
ROOT = Path.cwd().resolve()
packet = b"".join((ROOT / f".github/checkpoint/admission_{n}.b64").read_bytes() for n in range(4))
compressed = base64.b64decode(packet, validate=True)
assert hashlib.sha256(compressed).hexdigest() == "6181f1def58952ffc0dd57a048f7aa92acacb7e88f1b294809f715217aef8560"
with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
    raw = stream.read(131073)
assert len(raw) <= 131072
entries = json.loads(raw)
assert len(entries) == len(ALLOWED) and {e["path"] for e in entries} == ALLOWED
updates = {}
for entry in entries:
    path = ROOT / entry["path"]
    assert not path.is_symlink() and path.resolve().is_relative_to(ROOT)
    if entry["before"] is None:
        assert not path.exists(), f"Refuse existing destination: {entry['path']}"
        old = b""
    else:
        old = path.read_bytes()
        assert hashlib.sha256(old).hexdigest() == entry["before"], f"Refuse changed input: {entry['path']}"
    lines = old.decode("utf-8").splitlines(keepends=True)
    previous_end = 0
    for change in entry["changes"]:
        assert previous_end <= change["start"] <= change["end"] <= len(lines)
        previous_end = change["end"]
    for change in reversed(entry["changes"]):
        lines[change["start"]:change["end"]] = [change["text"]]
    new = "".join(lines).encode("utf-8")
    assert hashlib.sha256(new).hexdigest() == entry["after"], f"Output mismatch: {entry['path']}"
    compile(new, entry["path"], "exec")
    updates[path] = new
for path, new in updates.items():
    path.write_bytes(new)
print("Six source/test files materialized; all before/after hashes verified.")
