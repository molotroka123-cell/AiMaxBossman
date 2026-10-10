"""Stage 4 background swap: CPU-only checks of the pure parts of tools/genjutsu/background_swap.py and
tools/video_gate/background_gate.py (no GPU, no network, no model files, no personal media)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy", reason="root-ci installs no media deps")
pytest.importorskip("PIL", reason="root-ci installs no media deps")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "genjutsu"))
sys.path.insert(0, str(ROOT / "tools" / "video_gate"))
import background_swap as bs  # noqa: E402
import background_gate as bgate  # noqa: E402


def _disc(h=120, w=160, r=30, cx=80, cy=60):
    y, x = np.mgrid[0:h, 0:w]
    return ((x - cx) ** 2 + (y - cy) ** 2) <= r * r


# ------------------------------------------------------------------ background_swap
def test_composite_keeps_source_in_core_and_background_where_alpha_is_zero():
    rng = np.random.default_rng(1)
    src = rng.integers(0, 256, (40, 50, 3), dtype=np.uint8)
    fgr = rng.integers(0, 256, (40, 50, 3)).astype(np.float32)
    bg = bs.studio_background(50, 40)
    alpha = np.zeros((40, 50), np.uint8)
    alpha[10:30, 10:30] = 255
    alpha[10:30, 30:35] = 128
    alpha[10:30, 35:37] = bs.CORE_U8          # exactly the core threshold keeps the source too
    out = bs.composite(src, fgr, alpha, bg)
    core = alpha >= bs.CORE_U8
    assert np.array_equal(out[core], src[core])
    assert np.array_equal(out[alpha == 0], bg[alpha == 0])
    a = 128 / 255
    expect = np.clip(np.round(a * fgr[15, 32] + (1 - a) * bg[15, 32].astype(np.float32)), 0, 255)
    assert np.array_equal(out[15, 32], expect.astype(np.uint8))


def test_composite_below_core_uses_rvm_foreground_not_the_old_background():
    src = np.full((4, 4, 3), 200, np.uint8)       # old green screen-like colour baked into the source
    fgr = np.full((4, 4, 3), 50.0, np.float32)    # clean foreground estimate
    bg = np.zeros((4, 4, 3), np.uint8)
    alpha = np.full((4, 4), 249, np.uint8)        # just below the core
    out = bs.composite(src, fgr, alpha, bg)
    assert int(out[0, 0, 0]) == round(249 / 255 * 50)


def test_quantize_alpha_rounds_and_clips():
    q = bs.quantize_alpha(np.array([-0.2, 0.0, 0.5, 0.98, 1.0, 1.3]))
    assert q.tolist() == [0, 0, 128, 250, 255, 255]
    assert q.dtype == np.uint8


def test_studio_background_is_deterministic_neutral_and_sized():
    a, b = bs.studio_background(320, 180), bs.studio_background(320, 180)
    assert a.shape == (180, 320, 3) and a.dtype == np.uint8
    assert np.array_equal(a, b)
    spread = a.astype(int).max(axis=2) - a.astype(int).min(axis=2)
    assert spread.max() <= 30                      # grey-blue, not a saturated colour
    assert a[5, 160].mean() > a[175, 160].mean()   # floor darker than the lit wall


def test_auto_downsample_targets_long_side_512():
    assert bs.auto_downsample(720, 1280) == pytest.approx(0.4)
    assert bs.auto_downsample(1080, 1920) == pytest.approx(512 / 1920)
    assert bs.auto_downsample(300, 400) == 1.0


def test_subject_gate_covers_the_dilated_mask_and_stays_in_unit_range():
    m = np.zeros((60, 60), bool)
    m[25:35, 25:35] = True
    g = bs.subject_gate(m, dilate_px=5, feather_px=2)
    assert g.min() >= 0.0 and g.max() <= 1.0
    assert (g[20:40, 20:40] == 1.0).all()          # mask + 5 px
    assert g[0, 0] == 0.0


def test_rate_seconds_handles_ntsc():
    assert bs.rate_seconds("30000/1001") == pytest.approx(29.97, abs=1e-3)
    assert bs.rate_seconds("30") == 30.0


# ------------------------------------------------------------------ background_gate (needs opencv)
cv2 = pytest.importorskip("cv2", reason="gate measures need opencv")


def test_px_scales_with_height():
    assert bgate.px(0.01, 720) == 7 and bgate.px(0.015, 720) == 11 and bgate.px(0.0001, 720) == 1


def test_core_mask_erodes_the_high_alpha_region():
    a = (_disc() * 255).astype(np.uint8)
    core = bgate.core_mask(a, 4)
    assert core.sum() < (a >= 250).sum()
    assert not (core & ~(a >= 250)).any()
    assert core[60, 80] and not core[60, 80 + 28]


def test_edge_band_and_outer_ring_sit_where_expected():
    a = (_disc(r=30) * 255).astype(np.uint8)
    band = bgate.edge_band(a, 5)
    assert band[60, 80 + 30] and band[60, 80 + 34] and not band[60, 80] and not band[60, 80 + 40]
    ring = bgate.outer_ring(a, 3, 8)
    assert ring[60, 80 + 31 + 4] and not ring[60, 80 + 31] and not ring[60, 80 + 31 + 12] and not ring[60, 80]


def test_warping_error_zero_for_static_matte_and_detects_a_jump():
    a = (_disc() * 255).astype(np.uint8)
    zero = np.zeros((120, 160, 2), np.float32)
    assert bgate.warping_error(a, a, zero, zero, 7) == pytest.approx(0.0)
    jumped = (_disc(cx=83) * 255).astype(np.uint8)          # 3 px jump not explained by (zero) motion
    assert bgate.warping_error(a, jumped, zero, zero, 7) > 0.08


def test_warping_error_compensates_true_motion():
    a = (_disc(cx=80) * 255).astype(np.uint8)
    moved = (_disc(cx=83) * 255).astype(np.uint8)
    fw = np.zeros((120, 160, 2), np.float32)
    fw[..., 0] = 3.0                                        # t-1 -> t moves right by 3
    bw = -fw                                                # t -> t-1
    assert bgate.warping_error(a, moved, fw, bw, 7) < 0.02


def test_occlusion_check_flags_inconsistent_flow():
    fw = np.zeros((20, 20, 2), np.float32)
    bw = np.zeros((20, 20, 2), np.float32)
    assert not bgate.occluded(fw, bw).any()
    bw[..., 0] = 2.0                                        # backward says +2, forward says 0 -> inconsistent
    assert bgate.occluded(fw, bw).all()


def test_leak_zone_excludes_the_margin_near_people():
    p = _disc(r=20)
    z = bgate.leak_zone(p, 5, 15)
    assert not (z & p).any()
    assert z[60, 80 + 20 + 10] and not z[60, 80 + 20 + 3] and not z[60, 80 + 20 + 25]


def test_halo_measure_separates_clean_edge_from_old_background_rim():
    """Negative control: the same ring measure must see a rim of old background colour."""
    h, w = 120, 160
    bg = np.full((h, w, 3), (140, 128, 118), np.uint8)       # BGR studio grey
    old = np.full((h, w, 3), (20, 200, 30), np.uint8)        # old green background
    person = _disc()
    alpha = (person * 255).astype(np.uint8)
    clean = np.where(person[..., None], np.uint8(90), bg)
    rim = clean.copy()
    ring = bgate.outer_ring(alpha, 3, 8)
    rim[ring] = (0.4 * old[ring] + 0.6 * bg[ring]).astype(np.uint8)
    assert bgate.delta_e(clean, bg)[ring].mean() == pytest.approx(0.0, abs=1e-3)
    assert bgate.delta_e(rim, bg)[ring].mean() > bgate.T["halo_de_max"]


def test_tercile_means_split_by_motion():
    t = bgate.tercile_means([0.1, 0.2, 0.3, 1, 2, 3, 10, 20, 30], [0, 0, 0, 1, 1, 1, 2, 2, 2])
    assert [x["value_mean"] for x in t] == [0.0, 1.0, 2.0]
    assert bgate.tercile_means([1, None], [1, 2]) == []


def _metrics(**kw):
    m = {"frames": [90, 90], "fps_ok": True, "duration_ok": True, "aligned_share": 1.0, "max_abs_core": 0,
         "min_psnr_core_db": 99.0, "warp_error_mean": 0.05, "warp_error_p95": 0.1, "halo_de_mean": 5.0,
         "leak_share_mean": 0.001, "body_nme_mean": 0.01, "hole_share_mean": 0.0}
    m.update(kw)
    return m


def test_checks_pass_and_each_threshold_can_fail():
    assert all(bgate.checks_from(_metrics(), lossless=True).values())
    for key, bad in [("S4_T1_frame_count", {"frames": [90, 89]}), ("S4_T1_alignment", {"aligned_share": 0.99}),
                     ("S4_T2_core_pixels", {"max_abs_core": 1}), ("S4_T3_alpha_mean", {"warp_error_mean": 0.081}),
                     ("S4_T3_alpha_p95", {"warp_error_p95": 0.16}), ("S4_T4_halo", {"halo_de_mean": 8.01}),
                     ("S4_T5_leakage", {"leak_share_mean": 0.011}), ("S4_T6_body", {"body_nme_mean": 0.021}),
                     ("S4_T7_holes", {"hole_share_mean": 0.0051}), ("S4_T6_body", {"body_nme_mean": None})]:
        c = bgate.checks_from(_metrics(**bad), lossless=True)
        assert c[key] is False, key
    assert bgate.checks_from(_metrics(min_psnr_core_db=39.9), lossless=False)["S4_T2_core_pixels"] is False
    assert bgate.checks_from(_metrics(max_abs_core=3, min_psnr_core_db=45.0), lossless=False)["S4_T2_core_pixels"] is True


def test_thresholds_match_the_committed_stage_doc():
    """The doc (committed before the tool, f6e8ddc7) and the gate constants must agree."""
    doc = (ROOT / "docs" / "owner" / "VIDEO_PIPELINE_STAGES_20261010.md").read_text(encoding="utf-8")
    for needle in ("среднее E ≤ 0.08 и 95-й перцентиль E_t ≤ 0.15", "| ≤ 8.0 |", "ΔE(C, B) > 10", "| ≤ 0.01 |",
                   "| ≤ 0.005 |", "0.005·H (4 px)", "0.004·H … 0.011·H", "0.015·H"):
        assert needle in doc, needle
    assert bgate.T["warp_mean_max"] == 0.08 and bgate.T["warp_p95_max"] == 0.15 and bgate.T["halo_de_max"] == 8.0
    assert bgate.T["leak_de"] == 10.0 and bgate.T["leak_share_max"] == 0.01 and bgate.T["hole_share_max"] == 0.005
    assert bgate.T["body_max"] == 0.02 and bgate.CORE_U8 == bs.CORE_U8 == 250
