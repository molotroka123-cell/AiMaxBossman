"""Run actual production save logic in Node with isolated API/DOM ports."""
from pathlib import Path
import shutil
import subprocess


def test_web_save_race_contracts():
    node = shutil.which('node')
    assert node, 'Node is required for Web Designer save acceptance'
    result = subprocess.run([node, '--experimental-vm-modules', '--test',
                             str(Path(__file__).with_name('test_web_designer_save_races.mjs'))],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
