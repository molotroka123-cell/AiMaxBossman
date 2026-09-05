"""TZ-02 §2.1 — секрет-скан 2.0: корпус синтетических секретов (все ловятся без
маркера, все снимаются маркером), энтропийные позитивы/негативы, ZIP с секретом,
запрещённые файлы. Значения заведомо фальшивые."""
from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("ci_secret_scan", ROOT / "tools" / "ci_secret_scan.py")
scan = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(scan)  # type: ignore[union-attr]

# ci-secret-scan: allow — весь файл состоит из фальшивых канареек для теста сканера
CANARIES = [  # ci-secret-scan: allow
    ("openai key", "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"),  # ci-secret-scan: allow
    ("anthropic key", "sk-ant-" + "api03-Zz9Yy8Xx7Ww6Vv5Uu4Tt3Ss2Rr1Qq0"),  # ci-secret-scan: allow
    ("openrouter key", "sk-or-v1-" + "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0"),  # ci-secret-scan: allow
    ("google api key", "AIza" + "SyD9x8W7v6U5t4S3r2Q1p0O9n8M7l6K5j4I"),  # ci-secret-scan: allow
    ("slack token", "xoxb-" + "1234567890-ABCDEFGHIJKLMNOP"),  # ci-secret-scan: allow
    ("telegram bot token", "123456789:" + "AAFakeTokenForScannerTest0123456789"),  # ci-secret-scan: allow
    ("jwt", "eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjM0In0" + ".Q2FuYXJ5U2lnbmF0dXJlMTIz"),  # ci-secret-scan: allow
    ("github token", "ghp_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),  # ci-secret-scan: allow
    ("github token", "github_pat_" + "11ABCDEFG0abcdefghijklmnopqrstuvwxyz"),  # ci-secret-scan: allow
    ("aws access key", "AKIA" + "ABCDEFGHIJKLMNOP"),  # ci-secret-scan: allow
    ("aws secret", "aws_secret_access_key = " + "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"),  # ci-secret-scan: allow
    ("private key", "-----BEGIN " + "RSA PRIVATE KEY-----"),  # ci-secret-scan: allow
    ("private key", "-----BEGIN " + "OPENSSH PRIVATE KEY-----"),  # ci-secret-scan: allow
    ("private key", "-----BEGIN " + "PGP PRIVATE KEY BLOCK-----"),  # ci-secret-scan: allow
    ("wallet seed label", "seed phrase: " + "apple banana cherry"),  # ci-secret-scan: allow
    ("obvious password", 'password = "' + 'Sup3rSecretValue!"'),  # ci-secret-scan: allow
]
ENTROPY_POSITIVES = [  # ci-secret-scan: allow — случайные base64-подобные строки без словарных подстрок
    "qZ8vB2nL0pR7wX4tY9mK3sJ6hF1dG5cV",  # ci-secret-scan: allow
    "Mx7Pq2Rz9Lw4Kt8Vn3Bj6Hf1Gd5Cs0Yu",  # ci-secret-scan: allow
    "T4r8Yw2Uq6Io0Pl3Kj7Hg1Fd5Sa9Zx3Cv",  # ci-secret-scan: allow
    "b7N2m9K4j1H6g3F8d5S0a2P9o4I7u1Y6t",  # ci-secret-scan: allow
    "Zq9Xw8Cv7Bn6Ml5Kj4Hg3Fd2Sa1Po0Iu9Y",  # ci-secret-scan: allow
]
ENTROPY_NEGATIVES = [
    "AI3D_OPENSCAD_BIN=openscad",                              # env-переменная, не секрет
    "sha256=9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    "commit 3b18e512dba79e4c8300dd08aeb37f8e728b8dad",
    "https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/33965177036",
    "this_is_a_test_placeholder_value_for_config_examples",
    "BOSSMAN_EVIDENCE_KEY_FILE",
]


def _scan(tmp_path: Path, name: str, text: str) -> list[str]:
    f = tmp_path / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    return scan.scan_paths([f], tmp_path)


def test_every_pattern_canary_is_caught_and_allow_marker_silences_it(tmp_path):
    assert len(CANARIES) >= 15
    for label, value in CANARIES:
        found = _scan(tmp_path, "cfg/settings.py", f'X = "{value}"\n')
        assert any(label in f for f in found), (label, found)
        assert _scan(tmp_path, "cfg/settings.py", f'X = "{value}"  # {scan.ALLOW_MARK}\n') == []


def test_entropy_positives_and_negatives(tmp_path):
    for tok in ENTROPY_POSITIVES:
        found = _scan(tmp_path, "svc/config.py", f'TOKEN = "{tok}"\n')
        assert any("high-entropy" in f for f in found), (tok, scan.shannon_entropy(tok))
    for line in ENTROPY_NEGATIVES:
        assert _scan(tmp_path, "svc/config.py", line + "\n") == [], line
    # документация/манифесты — только паттерны, без энтропии
    assert _scan(tmp_path, "docs/notes.md", f'id: {ENTROPY_POSITIVES[0]}\n') == []


def test_zip_content_is_scanned_and_forbidden_files_flagged(tmp_path):
    z = tmp_path / "drop.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("pack/config.py", f'KEY = "{CANARIES[2][1]}"\n')
        zf.writestr("pack/image.png", b"\x89PNG not scanned")
    found = scan.scan_paths([z], tmp_path)
    assert any("drop.zip!pack/config.py" in f and "openrouter key" in f for f in found)
    for name in (".env", "deploy/.env.production", "certs/server.pem", "ssh/id_rsa", "keys/evidence.key"):
        found = _scan(tmp_path, name, "anything\n")
        assert any("forbidden file" in f for f in found), name
    assert _scan(tmp_path, "apps/x/.env.example", "AI3D_OPENSCAD_BIN=openscad\n") == []


def test_repository_itself_is_clean():
    assert scan.main() == 0
