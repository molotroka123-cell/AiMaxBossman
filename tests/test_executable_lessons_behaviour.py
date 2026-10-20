"""Branch 8 (Memory): executable operational lessons check BEHAVIOUR, not source text.

Each test builds a synthetic repo whose source text satisfies the old grep/AST
heuristics while the behaviour is wrong; the check must fail it. The real repo
must still pass.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("import_operational_lessons_b08", ROOT / "tools" / "import_operational_lessons.py")
imp = importlib.util.module_from_spec(_spec)
sys.modules["import_operational_lessons_b08"] = imp
_spec.loader.exec_module(imp)


def _repo(tmp_path: Path, rel: str, text: str) -> Path:
    root = tmp_path / "repo"
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return root


def test_real_repo_passes_behavioural_pult_and_launcher_checks():
    assert imp._check_pult_utf8(ROOT)[0], imp._check_pult_utf8(ROOT)
    assert imp._check_companion_launcher_ready(ROOT)[0], imp._check_companion_launcher_ready(ROOT)


def test_pult_check_rejects_cp1251_body_hidden_behind_utf8_keywords(tmp_path: Path):
    root = _repo(tmp_path, "tools/pult_note.py", (
        "import json\n"
        "def read(p):\n"
        "    return open(p, encoding='utf-8').read()\n"
        "def build_payload(chat, text):\n"
        "    json.dumps({}, ensure_ascii=False)\n"
        "    return json.dumps({'chat_id': chat, 'text': text}, ensure_ascii=False).encode('cp1251')\n"))
    ok, detail = imp._check_pult_utf8(root)
    assert not ok and "UTF-8" in detail


def test_pult_check_rejects_payload_that_drops_text(tmp_path: Path):
    root = _repo(tmp_path, "tools/pult_note.py", (
        "import json\n"
        "def build_payload(chat, text):\n"
        "    return json.dumps({'chat_id': chat, 'text': text.encode('ascii', 'ignore').decode()},\n"
        "                      ensure_ascii=False).encode(encoding='utf-8')\n"))
    assert not imp._check_pult_utf8(root)[0]


def test_launcher_check_rejects_companion_without_isolated_utf8(tmp_path: Path):
    root = _repo(tmp_path, "tools/owner_one_bossman.py", (
        "def _companion_locked(cfg, fp):\n"
        "    return False\n"
        "def command_for(kind, home, data_dir, port, companion_config, src=None, python=None):\n"
        "    py = 'python.exe'\n"
        "    if kind == 'companion':\n"
        "        return [py, '-m', 'bcc.telegram_companion', '--config', str(companion_config)]\n"
        "    return [py, \"-I\", \"-X\", \"utf8\", '-m', 'bcc']\n"))
    ok, detail = imp._check_companion_launcher_ready(root)
    assert not ok and "companion" in detail


def test_checks_fail_closed_when_code_raises(tmp_path: Path):
    root = _repo(tmp_path, "tools/pult_note.py", "def build_payload(chat, text):\n    raise RuntimeError('boom')\n")
    ok, detail = imp._check_pult_utf8(root)
    assert not ok and "RuntimeError" in detail
