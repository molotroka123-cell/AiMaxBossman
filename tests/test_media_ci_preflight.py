"""The preflight must fail on missing tools, not turn absence into a green skip."""
import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('media_ci_preflight', Path(__file__).resolve().parents[1] / 'tools/media_ci_preflight.py')
media = importlib.util.module_from_spec(spec)
spec.loader.exec_module(media)

@pytest.mark.parametrize('missing', ['ffmpeg', 'ffprobe'])
def test_both_tools_are_mandatory(monkeypatch, missing):
    monkeypatch.setattr(media.shutil, 'which', lambda name: None if name == missing else '/not-executed')
    with pytest.raises(RuntimeError, match=missing):
        media.preflight()


def test_nonzero_media_process_is_not_success(monkeypatch):
    monkeypatch.setattr(media.shutil, 'which', lambda name: '/fake/' + name)
    def fail(*args, **kwargs):
        raise media.subprocess.CalledProcessError(1, 'media-fixture')
    monkeypatch.setattr(media.subprocess, 'run', fail)
    with pytest.raises(media.subprocess.CalledProcessError):
        media.preflight()
