"""One-shot deterministic delivery of an already tested, hash-bound patch."""
from hashlib import sha256
from pathlib import Path

EXPECTED = {
    "bossman_shared/objective_store.py": "57e862862be23d9d304616ab5c08fbb97fcde61dd10572f9501d6d80fcbd2c2b",
    "bossman-core/bossman_v3/fleet/store.py": "1541f74d03e58549aead9aa428354af47bfd4bcd3d1ca40a973b8d42238b8efa",
    "bossman-core/bossman_v3/organization/store.py": "20297a32ce2710ccac0af2a734d7696c48b7d80c44de9bf7a50beb1843b79c7b",
}

updates = {}
for name, expected in EXPECTED.items():
    path = Path(name)
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != expected:
        raise SystemExit(f"Refuse changed source: {name}")
    text = raw.decode("utf-8")
    text = text.replace("import sqlite3\n", "import sqlite3\n\nfrom bossman_shared.sqlite_connection import OwnedConnection\n", 1)
    for old in (
        'sqlite3.connect(self.path, timeout=30, isolation_level="IMMEDIATE")',
        'sqlite3.connect(self.path, timeout=30, isolation_level=None)',
        'sqlite3.connect(self.path, timeout=30)',
    ):
        text = text.replace(old, old[:-1] + ', factory=OwnedConnection)', 1)
    method = text.index('def connect(') if 'def connect(' in text else text.index('def _connect(')
    start = text.index('        con.row_factory = sqlite3.Row', method)
    end = text.index('        return con', start)
    block = text[start:end]
    text = (text[:start] + '        try:\n' +
            ''.join('    ' + line + '\n' for line in block.splitlines()) +
            '        except BaseException:\n            con.close()\n            raise\n' + text[end:])
    compile(text, name, "exec")
    updates[path] = text
# Validate every input before changing any file.
for path, text in updates.items():
    path.write_text(text, encoding="utf-8")
