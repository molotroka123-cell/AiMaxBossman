"""Protocol boundary controls; these are not live provider acceptance."""
import importlib
import json
from types import SimpleNamespace

import pytest

probe = importlib.import_module('social_farm.media.probe')


@pytest.mark.parametrize('duration', ['NaN', 'inf', '-inf', '1e999', '1e306', '-1', None, 'nonsense'])
def test_nonfinite_or_invalid_duration_stays_unmeasured(duration):
    assert probe._duration_ms(duration) is None


def test_finite_duration_positive_control():
    assert probe._duration_ms('1.25') == 1250


@pytest.mark.parametrize('width,height', [(-1, 480), (640, -1), (0, 480)])
def test_invalid_dimensions_cannot_be_measured_media(tmp_path, monkeypatch, width, height):
    target = tmp_path / 'controlled.mp4'
    target.write_bytes(b'local controlled input')
    monkeypatch.setattr(probe, 'ffprobe_path', lambda: 'controlled-probe')
    data = {'streams': [{'codec_type': 'video', 'codec_name': 'h264',
                          'width': width, 'height': height}],
            'format': {'format_name': 'mp4', 'duration': '1', 'size': str(target.stat().st_size)}}
    monkeypatch.setattr(probe.subprocess, 'run', lambda *a, **kw: SimpleNamespace(
        returncode=0, stdout=json.dumps(data).encode(), stderr=b''))
    with pytest.raises(probe.CorruptMedia):
        probe.probe_with_ffprobe(target)
