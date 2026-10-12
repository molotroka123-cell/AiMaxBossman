"""Tests for tools/photo_upscale.  All images are SYNTHETIC (generated here); no personal photos, no network.

Run one file at a time:  python -m pytest tests/photo_upscale -q

Most tests exercise the engine without a neural model (fake infer function / Lanczos fallback) so they run in any
Python with numpy + Pillow.  ``test_neural_*`` run the real DirectML model only when the FaceFusion venv and
the real_esrgan_x4 weights exist on this machine (they are skipped otherwise, never faked).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))

from photo_upscale import measure as M  # noqa: E402
from photo_upscale import upscale as U  # noqa: E402

FF_DIR = U.facefusion_dir()
FF_PY = U.facefusion_python(FF_DIR)
HAVE_NEURAL = bool(FF_PY and (FF_DIR / ".assets" / "models" / "real_esrgan_x4.onnx").exists())


# ----------------------------------------------------------------- helpers
def _sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _make_image(path: Path, w: int = 120, h: int = 80, name: str = "foliage", alpha: bool = False) -> Path:
    arr = M.synth_scene(name, w, h, seed=7)
    im = Image.fromarray(arr)
    if alpha:
        a = np.zeros((h, w), np.uint8)
        a[: h // 2, :] = 255
        a[h // 2:, : w // 2] = 128
        im.putalpha(Image.fromarray(a))
    im.save(path)
    return path


@pytest.fixture()
def no_models(monkeypatch, tmp_path):
    """FaceFusion dir that exists but has no weights: forces the honest Lanczos fallback."""
    ff = tmp_path / "no_facefusion"
    (ff / ".assets" / "models").mkdir(parents=True)
    monkeypatch.setenv("PHOTO_UPSCALE_FACEFUSION_DIR", str(ff))
    monkeypatch.setenv("PHOTO_UPSCALE_NO_REEXEC", "1")
    return ff


def _run(src, out, **kw):
    msgs = []
    res = U.upscale_file(Path(src), Path(out), log=msgs.append, **kw)
    return res, msgs


# ----------------------------------------------------------------- FaceFusion location (no machine-specific path)
def test_facefusion_dir_env_overrides_win_in_documented_order(monkeypatch, tmp_path):
    specific, shared = tmp_path / "specific", tmp_path / "shared"
    monkeypatch.delenv("PHOTO_UPSCALE_FACEFUSION_DIR", raising=False)
    monkeypatch.setenv("BOSSMAN_FACEFUSION_DIR", str(shared))
    assert U.facefusion_dir() == shared
    monkeypatch.setenv("PHOTO_UPSCALE_FACEFUSION_DIR", str(specific))
    assert U.facefusion_dir() == specific  # the tool-specific name stays first (unchanged behaviour)


def test_facefusion_dir_default_comes_from_home_and_from_the_checkout_location(monkeypatch, tmp_path):
    for name in U.FACEFUSION_DIR_ENV:
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    lonely = tmp_path / "lonely" / "repo" / "tools" / "photo_upscale" / "upscale.py"
    monkeypatch.setattr(U, "__file__", str(lonely))
    expected = home / "Bossman" / "apps-local" / "facefusion"
    assert U.facefusion_dir() == expected == U.default_facefusion_dir()

    # an apps-local/facefusion next to the checkout (or above a nested worktree) is found first
    root = tmp_path / "bossman_root"
    (root / "apps-local" / "facefusion").mkdir(parents=True)
    nested = root / "wt" / "agent" / "tools" / "photo_upscale" / "upscale.py"
    monkeypatch.setattr(U, "__file__", str(nested))
    assert U.facefusion_dir() == root / "apps-local" / "facefusion"


def test_no_machine_specific_user_path_in_the_tool_sources():
    import re
    for path in (TOOLS / "photo_upscale").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"[A-Za-z]:[\\\\/]+Users[\\\\/]", text), f"{path.name} hard-codes a user profile path"


# ----------------------------------------------------------------- geometry / identity
def test_aspect_ratio_preserved_and_exact_long_edge(no_models, tmp_path):
    src = _make_image(tmp_path / "in.png", 120, 80)
    res, _ = _run(src, tmp_path / "out.png", longest_edge=300)
    assert res.exit_code == 0
    with Image.open(res.out_path) as im:
        assert im.size == (300, 200)
    side = json.loads(res.sidecar_path.read_text(encoding="utf-8"))
    w, h = side["resolution"]["exported_px"]
    assert abs(w / h - 120 / 80) < 0.01


def test_portrait_orientation_aspect(no_models, tmp_path):
    src = _make_image(tmp_path / "in.png", 77, 130)
    res, _ = _run(src, tmp_path / "out.png", longest_edge=260)
    assert res.exit_code == 0
    with Image.open(res.out_path) as im:
        assert im.size[1] == 260
        assert abs(im.size[0] / im.size[1] - 77 / 130) < 0.01


def test_source_is_never_overwritten(no_models, tmp_path):
    src = _make_image(tmp_path / "in.png")
    before = _sha(src)
    # 1) explicit attempt to write over the source -> refused, source intact
    res, msgs = _run(src, src, longest_edge=240)
    assert res.exit_code == 2
    assert any("never overwritten" in m for m in msgs)
    assert _sha(src) == before
    # 2) normal run leaves the source byte-identical
    res, _ = _run(src, tmp_path / "out.png", longest_edge=240)
    assert res.exit_code == 0
    assert _sha(src) == before


def test_existing_output_requires_overwrite_flag(no_models, tmp_path):
    src = _make_image(tmp_path / "in.png")
    out = tmp_path / "out.png"
    assert _run(src, out, longest_edge=240)[0].exit_code == 0
    res, msgs = _run(src, out, longest_edge=240)
    assert res.exit_code == 2 and any("--overwrite" in m for m in msgs)
    assert _run(src, out, longest_edge=240, overwrite=True)[0].exit_code == 0


def test_non_png_output_name_gets_png_suffix(no_models, tmp_path):
    src = _make_image(tmp_path / "in.png")
    res, _ = _run(src, tmp_path / "result.jpg", longest_edge=240)
    assert res.exit_code == 0
    assert res.out_path.name == "result.jpg.png"
    assert Image.open(res.out_path).format == "PNG"


# ----------------------------------------------------------------- sidecar
def test_sidecar_has_all_required_fields(no_models, tmp_path):
    src = _make_image(tmp_path / "in.png")
    res, _ = _run(src, tmp_path / "out.png", longest_edge=360)
    side = json.loads(res.sidecar_path.read_text(encoding="utf-8"))
    assert res.sidecar_path.name == "out.png.json"
    assert side["source"]["sha256"] == _sha(src)
    assert side["output"]["sha256"] == _sha(res.out_path)
    assert side["output"]["decode_check"] == "ok"
    assert side["resolution"]["native_px"] == [120, 80]
    assert side["resolution"]["exported_px"] == [360, 240]
    assert side["resolution"]["upscale_factor"] == 3.0
    for k in ("request", "engine", "models", "passes", "faces", "measured", "fallback", "timings", "honesty", "warnings"):
        assert k in side
    sr = side["measured"]["sharpness_ratio_vs_lanczos"]
    assert set(sr) == {"laplacian_variance", "hf_energy"}
    assert side["timings"]["total_seconds"] >= 0
    # the explicit honesty sentence, EN + RU
    assert side["honesty"]["reconstructed_detail"] is True
    assert "reconstructed" in side["honesty"]["statement_en"]
    assert "not native detail" in side["honesty"]["statement_en"]
    assert side["honesty"]["statement_ru"]


# ----------------------------------------------------------------- refusal
def test_refusal_over_safe_limit_exit_2_and_no_files(no_models, tmp_path):
    src = _make_image(tmp_path / "in.png", 100, 60)
    out = tmp_path / "out.png"
    res, msgs = _run(src, out, longest_edge=int(100 * U.SAFE_MAX_FACTOR) + 1)
    assert res.exit_code == 2
    text = "\n".join(msgs)
    assert "REFUSED" in text and "safe" in text and "limit" in text
    assert not out.exists() and not Path(str(out) + ".json").exists()


def test_refusal_is_announced_before_running_via_dry_run(no_models, tmp_path):
    src = _make_image(tmp_path / "in.png", 100, 60)
    res, msgs = _run(src, tmp_path / "o.png", longest_edge=800, dry_run=True)
    assert res.exit_code == 2 and msgs[0].startswith("REFUSED")
    ok, msgs = _run(src, tmp_path / "o.png", longest_edge=300, dry_run=True)
    assert ok.exit_code == 0 and msgs[0].startswith("plan:")
    assert not (tmp_path / "o.png").exists()


def test_accept_unproven_factor_is_recorded(no_models, tmp_path):
    src = _make_image(tmp_path / "in.png", 64, 48)
    res, _ = _run(src, tmp_path / "o.png", longest_edge=64 * 6, accept_unproven=True)
    assert res.exit_code == 0
    side = json.loads(res.sidecar_path.read_text(encoding="utf-8"))
    assert side["request"]["accept_unproven_factor"] is True
    assert any("exceeds the safe limit" in w for w in side["warnings"])


def test_target_not_larger_than_source_is_refused(no_models, tmp_path):
    src = _make_image(tmp_path / "in.png", 100, 60)
    res, msgs = _run(src, tmp_path / "o.png", longest_edge=100)
    assert res.exit_code == 2 and "not larger than the source" in msgs[-1]


def test_plan_passes_semantics():
    p = U.plan_request((100, 50), 400, model_scale=4, passes=None)
    assert p.steps == [4] and p.factor == 4.0
    p = U.plan_request((100, 50), 400, model_scale=2, passes=None)
    assert p.steps == [2, 2]
    p = U.plan_request((100, 50), 200, model_scale=4, passes=1)
    assert p.steps == [2] and p.neural_reach == 2
    with pytest.raises(U.Refusal):
        U.plan_request((100, 50), 400, model_scale=2, passes=1)  # one 2x pass cannot reach 4x
    with pytest.raises(U.Refusal):
        U.plan_request((100, 50), 200, model_scale=2, passes=4)  # not an allowed pass count
    auto = U.plan_request((1000, 500), None, model_scale=4, passes=None)
    assert auto.longest_edge == 4000  # min(8192, safe factor * source)
    capped = U.plan_request((3000, 1000), None, model_scale=4, passes=None)
    assert capped.longest_edge == 8192 and capped.factor < U.SAFE_MAX_FACTOR


# ----------------------------------------------------------------- fallback honesty + determinism
def test_fallback_is_labelled_and_never_silent(no_models, tmp_path):
    src = _make_image(tmp_path / "in.png")
    res, msgs = _run(src, tmp_path / "out.png", longest_edge=240)
    assert res.exit_code == 0
    assert any("fallback: no neural model" in m for m in msgs)
    side = json.loads(res.sidecar_path.read_text(encoding="utf-8"))
    assert side["fallback"]["used"] is True
    assert side["fallback"]["label"] == "fallback: no neural model"
    assert side["models"] == []
    assert "fallback: no neural model" in side["warnings"]
    assert side["resolution"]["neural_reach_factor"] == 1


def test_lanczos_fallback_is_deterministic_and_equals_reference(no_models, tmp_path):
    src = _make_image(tmp_path / "in.png", 96, 64, "architecture")
    h = []
    for i in range(2):
        res, _ = _run(src, tmp_path / f"o{i}.png", longest_edge=288)
        h.append(_sha(res.out_path))
    assert h[0] == h[1]
    ref = Image.open(src).convert("RGB").resize((288, 192), Image.LANCZOS)
    got = Image.open(tmp_path / "o0.png").convert("RGB")
    assert np.array_equal(np.asarray(ref), np.asarray(got))


def test_require_neural_exit_3_when_only_lanczos(no_models, tmp_path):
    src = _make_image(tmp_path / "in.png")
    res, _ = _run(src, tmp_path / "o.png", longest_edge=240, require_neural=True)
    assert res.exit_code == 3


def test_explicit_lanczos_is_not_called_a_fallback(no_models, tmp_path):
    src = _make_image(tmp_path / "in.png")
    res, msgs = _run(src, tmp_path / "o.png", longest_edge=240, model="lanczos")
    assert res.exit_code == 0
    side = json.loads(res.sidecar_path.read_text(encoding="utf-8"))
    assert side["fallback"]["used"] is False
    assert not any("fallback: no neural model" in m for m in msgs)


def test_faces_without_engine_reports_reason(no_models, tmp_path):
    src = _make_image(tmp_path / "in.png")
    res, _ = _run(src, tmp_path / "o.png", longest_edge=240, faces=True)
    assert res.exit_code == 0
    side = json.loads(res.sidecar_path.read_text(encoding="utf-8"))
    assert side["faces"]["requested"] is True and side["faces"]["applied"] is False
    assert side["faces"]["reason"]
    assert side["honesty"]["faces_generative"] is False


def test_alpha_channel_is_preserved(no_models, tmp_path):
    src = _make_image(tmp_path / "in.png", 80, 60, alpha=True)
    res, _ = _run(src, tmp_path / "o.png", longest_edge=240)
    assert res.exit_code == 0
    with Image.open(res.out_path) as im:
        assert im.mode == "RGBA" and im.size == (240, 180)
        a = np.asarray(im.getchannel("A"))
    assert a[:60].min() == 255 and a[-5:, -10:].max() == 0 and a[-5:, :10].min() == 128


# ----------------------------------------------------------------- tiling / seams (fake translation-equivariant model)
def _fake_x2(chunk: np.ndarray) -> np.ndarray:
    """Bicubic x2 on a NCHW float tile: local support, shift-equivariant -> tiling must be (almost) lossless."""
    c = chunk[0].transpose(1, 2, 0)
    h, w = c.shape[:2]
    chans = [np.asarray(Image.fromarray(c[:, :, k]).resize((w * 2, h * 2), Image.BICUBIC)) for k in range(3)]
    return np.stack(chans, axis=0)[None].astype(np.float32)


def _photoish(w=200, h=150) -> np.ndarray:
    return M.synth_scene("foliage", w, h, seed=3)


def test_tiled_equals_untiled_for_local_model():
    img = _photoish()
    whole = U.tiled_upscale(img, _fake_x2, 2, tile=256, overlap=0, fixed_tile=False)  # single tile
    tiled = U.tiled_upscale(img, _fake_x2, 2, tile=64, overlap=16, fixed_tile=True)
    assert whole.shape == tiled.shape == (img.shape[0] * 2, img.shape[1] * 2, 3)
    diff = np.abs(whole.astype(int) - tiled.astype(int))
    assert diff.max() <= 6, f"max seam error {diff.max()}"
    assert diff.mean() < 0.35


def test_seam_metric_clean_for_feathered_blend_and_detects_hard_seams():
    img = _photoish(256, 192)
    st = U.TileStats()
    out = U.tiled_upscale(img, _fake_x2, 2, tile=64, overlap=16, fixed_tile=True, stats=st)
    assert st.tiles > 4 and st.seam_cols and st.seam_rows
    clean = M.seam_report(out, st.seam_cols, st.seam_rows)
    assert abs(clean["excess"]) < 0.10, f"feathered blend must not add seams, got {clean}"

    # negative control: hard-merge tiles that each carry a different brightness offset -> detectable seams
    hard = np.asarray(Image.fromarray(img).resize((img.shape[1] * 2, img.shape[0] * 2), Image.BICUBIC)).astype(np.int16)
    cols = [x for x in range(64, hard.shape[1], 64)]
    rows = [y for y in range(64, hard.shape[0], 64)]
    for ci, x0 in enumerate([0] + cols):
        for ri, y0 in enumerate([0] + rows):
            hard[y0:y0 + 64, x0:x0 + 64] += (-1) ** (ci + ri) * 6
    hard = np.clip(hard, 0, 255).astype(np.uint8)
    bad = M.seam_report(hard, cols, rows)
    assert bad["excess"] > 0.3 and bad["excess"] > clean["excess"] + 0.25, f"hard seams must be visible (clean={clean}, bad={bad})"


def test_ramp_is_a_partition_of_unity_and_tiles_cover_image():
    a = U._ramp(64, 16, True, False)[:16]
    b = U._ramp(64, 16, False, True)[-16:]
    assert np.allclose(a + b, 1.0, atol=1e-6)
    for n, t, s in [(100, 64, 48), (64, 64, 48), (33, 64, 48), (500, 128, 96), (129, 64, 48)]:
        starts = U.axis_starts(n, t, s)
        assert starts[0] == 0
        if n > t:
            assert starts[-1] + t == n  # last tile flush with the edge
            assert all(b2 - a2 <= s for a2, b2 in zip(starts, starts[1:]))


def test_tiny_image_smaller_than_tile_and_edge_padding():
    img = np.full((5, 7, 3), 90, np.uint8)
    out = U.tiled_upscale(img, _fake_x2, 2, tile=128, overlap=16)
    assert out.shape == (10, 14, 3)
    assert abs(float(out.mean()) - 90.0) < 1.5  # reflect padding keeps flat content flat (no dark borders)


# ----------------------------------------------------------------- metrics
def test_metrics_basic_properties():
    gt = M.synth_scene("architecture", 160, 120, seed=1)
    assert M.psnr(gt, gt) == 99.0
    assert abs(M.ssim_y(gt, gt) - 1.0) < 1e-6
    blur = np.asarray(Image.fromarray(gt).resize((80, 60), Image.BOX).resize((160, 120), Image.BICUBIC))
    rep = M.full_report(blur, gt)
    assert rep["psnr_rgb"] < 40 and rep["ssim_y"] < 1.0
    assert rep["lap_var_ratio"] < 1.0 and rep["hf_energy_ratio"] < 1.0   # blurrier than the truth
    assert 0.0 <= rep["hf_nmse"] <= 1.5
    # added noise on a smooth image raises HF energy above the truth ("sharper than GT") while HF NMSE explodes
    smooth = np.asarray(Image.fromarray(gt).resize((20, 15), Image.BOX).resize((160, 120), Image.BICUBIC))
    rng = np.random.default_rng(0)
    noisy = np.clip(smooth.astype(np.float32) + rng.normal(0, 12, smooth.shape), 0, 255).astype(np.uint8)
    rn = M.full_report(noisy, smooth)
    assert rn["hf_energy_ratio"] > 1.5 and rn["hf_nmse"] > 0.5 and abs(rn["hf_corr"]) < 0.5


def test_degrade_area_is_exact_box_average():
    gt = M.synth_scene("text", 64, 48, seed=2)
    lr = M.degrade_area(gt, 4)
    assert lr.shape == (12, 16, 3)
    expect = gt.reshape(12, 4, 16, 4, 3).astype(np.float64).mean(axis=(1, 3))
    assert np.abs(lr.astype(np.float64) - expect).max() <= 1.0  # PIL BOX is 8-bit fixed point (< 1 level)
    with pytest.raises(ValueError):
        M.degrade_area(gt[:47], 4)


def test_synth_scenes_are_deterministic():
    a = M.synth_scene("hair", 96, 64, seed=11)
    b = M.synth_scene("hair", 96, 64, seed=11)
    c = M.synth_scene("hair", 96, 64, seed=12)
    assert np.array_equal(a, b) and not np.array_equal(a, c)


def test_chain_plan():
    assert M.chain_plan(4, 4) == ([4], 1)
    assert M.chain_plan(4, 2) == ([2, 2], 1)
    assert M.chain_plan(4, 8) == ([8], 2)
    assert M.chain_plan(2, 4) == ([4], 2)


# ----------------------------------------------------------------- CLI (subprocess, like exchange_volume)
def test_cli_exit_code_2_on_refusal(tmp_path):
    src = _make_image(tmp_path / "in.png", 100, 60)
    env = dict(os.environ, PHOTO_UPSCALE_NO_REEXEC="1")
    cp = subprocess.run([sys.executable, "-m", "photo_upscale.upscale", "--input", str(src), "--out", str(tmp_path / "o.png"),
                         "--longest-edge", "5000"], cwd=str(TOOLS), env=env, capture_output=True, text=True)
    assert cp.returncode == 2
    assert "REFUSED" in cp.stdout and "safe limit" in cp.stdout
    assert not (tmp_path / "o.png").exists()


def test_cli_lanczos_ok_exit_0(tmp_path):
    src = _make_image(tmp_path / "in.png", 60, 40)
    env = dict(os.environ, PHOTO_UPSCALE_NO_REEXEC="1")
    cp = subprocess.run([sys.executable, "-m", "photo_upscale.upscale", "--input", str(src), "--out", str(tmp_path / "o.png"),
                         "--longest-edge", "120", "--model", "lanczos"], cwd=str(TOOLS), env=env, capture_output=True, text=True)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert (tmp_path / "o.png.json").exists()


# ----------------------------------------------------------------- real neural model (skipped when absent)
@pytest.mark.skipif(not HAVE_NEURAL, reason="FaceFusion venv / real_esrgan_x4.onnx not present")
def test_neural_real_model_end_to_end_cli(tmp_path):
    src = _make_image(tmp_path / "in.png", 96, 72, "architecture")
    env = dict(os.environ)
    env.pop("PHOTO_UPSCALE_NO_REEXEC", None)
    env.pop("PHOTO_UPSCALE_REEXEC", None)
    env.pop("PHOTO_UPSCALE_FACEFUSION_DIR", None)
    cp = subprocess.run([sys.executable, "-m", "photo_upscale.upscale", "--input", str(src), "--out", str(tmp_path / "o.png"),
                         "--longest-edge", "384", "--model", "real_esrgan_x4", "--tile", "64", "--overlap", "16"],
                        cwd=str(TOOLS), env=env, capture_output=True, text=True, timeout=900)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    side = json.loads((tmp_path / "o.png.json").read_text(encoding="utf-8"))
    assert side["fallback"]["used"] is False
    m = side["models"][0]
    assert m["name"] == "real_esrgan_x4" and len(m["sha256"]) == 64 and m["license"] == "BSD-3-Clause"
    assert side["engine"]["name"] == "onnxruntime"
    assert side["passes"][0]["check"] == "ok" and side["passes"][0]["tiles"] > 1
    with Image.open(tmp_path / "o.png") as im:
        out = np.asarray(im.convert("RGB"))
    assert out.shape == (288, 384, 3)
    lanczos = np.asarray(Image.open(src).convert("RGB").resize((384, 288), Image.LANCZOS))
    assert not np.array_equal(out, lanczos)  # a neural model really ran
    # tiling with 64 px tiles must not leave seams in the real output either
    seam = side["passes"][0]["seam_excess"]
    assert seam is not None and seam < 0.35  # few seams: content noise; see README seam notes
