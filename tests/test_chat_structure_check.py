import pytest
import json
import subprocess
import sys
from pathlib import Path

def test_all(tmp_path):
    # 1. Valid file with canary
    chat_dir = tmp_path / "chats"
    chat_dir.mkdir()
    canary = "KANAREYKA-9911"
    chat_data = {
        "thread_id": "t1",
        "turns": [
            {"task_id": 1, "text": f"h {canary}", "status": "done"},
            {"task_id": 2, "text": "w", "status": "pending"},
            {"task_id": 3, "text": "b"}
        ],
        "summary": f"S {canary}",
        "title": canary
    }
    (chat_dir / "t1.json").write_text(json.dumps(chat_data))
    (chat_dir / "t1.json.bak").write_text("bak")

    # Test JSON output
    cmd = [sys.executable, "tools/chat_structure_check.py", "--dir", str(chat_dir), "--json"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0
    assert canary not in res.stdout
    data = json.loads(res.stdout)
    assert len(data) == 1
    assert data[0]["turn_count"] == 3
    assert set(data[0]["statuses"]) == {"done", "pending"}
    assert data[0]["bak_exists"] is True

    # Test Table output
    cmd = [sys.executable, "tools/chat_structure_check.py", "--dir", str(chat_dir)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0
    assert canary not in res.stdout
    assert "t1.json" in res.stdout

    # 2. Invalid JSON
    (chat_dir / "bad.json").write_text("{")
    res = subprocess.run([sys.executable, "tools/chat_structure_check.py", "--dir", str(chat_dir), "--json"], capture_output=True, text=True)
    assert res.returncode == 1
    data = json.loads(res.stdout)
    assert any(r["json_valid"] is False and r["error"] == "JSONDecodeError" for r in data)

    # 3. Missing dir
    res = subprocess.run([sys.executable, "tools/chat_structure_check.py", "--dir", "no_dir"], capture_output=True, text=True)
    assert res.returncode == 2
    assert "is not a directory" in res.stderr

    # 4. Empty dir
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    res = subprocess.run([sys.executable, "tools/chat_structure_check.py", "--dir", str(empty_dir)], capture_output=True, text=True)
    assert res.returncode == 0
    assert res.stdout == ""
