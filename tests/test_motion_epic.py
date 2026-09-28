"""Epic preset: reusable, deterministic rendering and unsupported-scene rejection."""
import sys
from pathlib import Path
import pytest
import numpy as np

ROOT = Path(__file__).resolve().parents[1] / 'tools' / 'motion_studio'
sys.path.insert(0, str(ROOT))
import spec
import epic

def test_epic_example_renders_each_scene_and_replays_identically():
    scene_spec = spec.load(ROOT / 'examples' / 'bossman_epic_22s.json')
    renderer = epic.Renderer(scene_spec)
    for scene in scene_spec['scenes']:
        t = (scene['start'] + scene['end']) / 2
        pixels = np.asarray(renderer.frame(t))
        assert pixels.shape == (720, 1280, 3)
        assert pixels.std() > 10
    assert renderer.frame(14).tobytes() == renderer.frame(14).tobytes()
    assert renderer.frame(14).tobytes() != renderer.frame(14.5).tobytes()

def test_epic_rejects_unsupported_scene_before_render():
    with pytest.raises(ValueError, match='does not support'):
        epic.Renderer({'scenes': [{'type': 'sticker'}]})

def test_epic_preview_produces_actual_image_without_audio(tmp_path):
    scene_spec = spec.load(ROOT / 'examples' / 'bossman_epic_22s.json')
    epic.render(scene_spec, tmp_path, None, previews=[1.5])
    assert (tmp_path / 'epic-preview-1.5.png').stat().st_size > 1000
    assert not (tmp_path / 'video.mp4').exists()
