"""Verify opt-in routing without invoking a model or processing personal media."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from tools.motion_studio import genjutsu_composite_e, genjutsu_e_video, local_genjutsu


def test_quality_e_routes_768_head_and_aligned_full_mask(tmp_path, monkeypatch):
    calls = {}
    def crop(*args, **kwargs):
        calls["crop"] = (args, kwargs)
        return {"guide": "guide.mp4", "mask": "head-mask.mp4", "frames": 16, "boxes": "boxes.json"}
    monkeypatch.setitem(sys.modules, "head_zoom", SimpleNamespace(crop_heads=crop))
    def settings(*args, **kwargs):
        calls["settings"] = kwargs
        return tmp_path / "settings.json"
    monkeypatch.setattr(local_genjutsu, "wgp_settings", settings)
    monkeypatch.setattr(local_genjutsu, "run_wgp", lambda *a, **k: tmp_path / "head.mp4")
    monkeypatch.setattr(local_genjutsu, "sh", lambda *a, **k: None)
    matte = object()
    monkeypatch.setattr(genjutsu_composite_e, "RVMForeground", lambda model: matte)
    monkeypatch.setattr(genjutsu_e_video, "render_composite_e_video", lambda **kw: calls.update(render=kw))
    result = local_genjutsu.head_zoom_pass(tmp_path / "generated.mp4", tmp_path / "source.mp4",
        tmp_path / "full-mask.mp4", [Path("ref.jpg")], 4.0, 16, 1, tmp_path, False, quality_e=True)
    assert result == tmp_path / "head_pasted.mp4"
    assert calls["crop"][1]["size"] == 768
    assert calls["settings"]["resolution"] == "768x768"
    assert calls["render"]["mask"] == tmp_path / "full-mask.mp4"
    assert calls["render"]["start"] == 4.0
    assert calls["render"]["matte"] is matte


def test_quality_e_checks_prerequisites_before_expensive_prep(tmp_path, monkeypatch):
    monkeypatch.setattr(local_genjutsu, "RVM", str(tmp_path / "missing.onnx"))
    monkeypatch.setattr(local_genjutsu, "prep", lambda *a, **k: pytest.fail("prep must not start"))
    with pytest.raises(FileNotFoundError, match="RVM"):
        local_genjutsu.main(["--video", "test.mp4", "--refs", "test.jpg", "--prompt", "test",
                             "--out", str(tmp_path / "out.mp4"), "--quality-e"])
