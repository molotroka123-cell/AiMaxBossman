"""Bounded negative controls for PREP-08, not Windows/GPU acceptance."""
import importlib.util
import json
import struct
import sys
import types
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def probe():
    spec = importlib.util.spec_from_file_location('measurement_regression_probe', ROOT / 'tools/responsiveness_probe.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Page:
    def __init__(self):
        self.clock = 10.0
        self.events = []

    def evaluate(self, expression, *args):
        if 'location.hash.replace' in expression:
            return 'old'
        self.events.append('navigate')
        self.clock += 0.2

    def wait_for_function(self, *args, **kwargs):
        self.events.append('fresh-view')
        self.clock += 0.02

    def wait_for_selector(self, *args, **kwargs):
        self.events.append('selector')


def test_latency_includes_navigation_dispatch(probe, monkeypatch):
    page = Page()
    monkeypatch.setattr(probe.time, 'perf_counter', lambda: page.clock)
    measured = probe.Walker(page, ['old', 'new']).goto('new')
    assert measured >= 219, 'dispatch consumed 200ms but the timer started afterwards'
    assert measured <= 221


def test_gallery_selector_cannot_certify_a_stale_view(probe):
    page = Page()
    probe.Walker(page, ['old', 'new']).goto('new', selector='article.studio-card')
    assert page.events == ['navigate', 'fresh-view', 'selector']


@pytest.mark.parametrize('value', [-1.0, float('nan'), float('inf'), float('-inf'), True])
def test_invalid_observation_never_becomes_a_budget_result(probe, value):
    line = probe.Line('cpu', {'limit': 2, 'means': 'idle cpu', 'where': 'tree'})
    line.observe(value, reference=False)
    assert line.verdict == probe.INSUFFICIENT
    assert line.measured is None
    json.dumps(line.as_dict(), allow_nan=False)


def test_valid_observations_still_distinguish_pass_from_failure(probe):
    line = probe.Line('cpu', {'limit': 2, 'means': 'idle cpu', 'where': 'tree'})
    line.observe(1.0, reference=False)
    assert line.verdict == 'PASS'
    line.observe(3.0, reference=False)
    assert line.verdict == 'FAIL'


@pytest.mark.parametrize('states', [[], ['UNKNOWN'], ['PASS', 'NOT_RUN']])
def test_empty_or_unknown_verdict_cannot_be_pass(probe, states):
    assert probe.overall_verdict(states) == probe.INSUFFICIENT


@pytest.mark.parametrize('seconds', [-1, float('nan'), float('inf'), 7201])
def test_invalid_duration_refused_before_loading_runtime(probe, monkeypatch, seconds):
    def forbidden(*args, **kwargs):
        raise AssertionError('runtime loading must not happen')
    monkeypatch.setattr(probe, '_load', forbidden)
    with pytest.raises(ValueError, match='soak'):
        probe.measure('reference', seconds, probe.load_budget())


def test_seed_fixture_is_decodable_png_not_only_a_magic_header(probe, monkeypatch, tmp_path):
    # Schema transport is deliberately synthetic; the produced PNG bytes are real.
    class Engine:
        def begin(self):
            return self
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def execute(self, *args):
            pass
        def dispose(self):
            pass
    monkeypatch.setitem(sys.modules, 'sqlalchemy', types.SimpleNamespace(create_engine=lambda *args: Engine()))
    monkeypatch.setitem(sys.modules, 'bcc.studio.tables', types.SimpleNamespace(runs=types.SimpleNamespace(insert=lambda: None)))
    probe._seed_gallery(tmp_path, 'sqlite://test', 1)
    data = (tmp_path / 'seeded/seed.png').read_bytes()
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    offset, chunks, compressed = 8, [], bytearray()
    while offset < len(data):
        length = struct.unpack('>I', data[offset:offset + 4])[0]
        name = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + length]
        crc = data[offset + 8 + length:offset + 12 + length]
        assert len(crc) == 4, 'PNG has an invalid/truncated chunk, not image data'
        assert struct.unpack('>I', crc)[0] == zlib.crc32(name + payload) & 0xffffffff
        chunks.append(name)
        if name == b'IHDR':
            width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', payload)
            assert (width, height, depth, color, compression, filtering, interlace) == (64, 64, 8, 2, 0, 0, 0)
        if name == b'IDAT':
            compressed.extend(payload)
        offset += 12 + length
    assert chunks == [b'IHDR', b'IDAT', b'IEND']
    assert len(zlib.decompress(compressed)) == 64 * (1 + 64 * 3)


def test_explicit_installed_cli_refuses_partial_binding(probe, tmp_path):
    with pytest.raises(SystemExit) as error:
        probe.main(['--mode', 'installed', '--installed-root', str(tmp_path)])
    assert error.value.code == 2


def test_explicit_installed_cli_uses_loaded_probe_without_module_registration(probe, monkeypatch, tmp_path):
    def refuse(module, **kwargs):
        assert module.Line is probe.Line
        raise ValueError('synthetic runtime is intentionally not launched')
    monkeypatch.setattr(probe, '_load', lambda *args: types.SimpleNamespace(run_installed=refuse))
    output = tmp_path / 'never-created.json'
    with pytest.raises(SystemExit) as error:
        probe.main(['--mode', 'installed', '--installed-root', str(tmp_path / 'app'),
                    '--archive', str(tmp_path / 'app.zip'), '--expected-sha', 'a' * 40,
                    '--expected-archive-sha256', 'b' * 64, '--json', str(output)])
    assert error.value.code == 2
    assert not output.exists()
