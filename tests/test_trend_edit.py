"""tools/trend_edit: pure parts — beat snapping, grid fit, ramp timing, punch curve, wipes, drawtext escaping, plan.

No audio, no GPU, no network. numpy only (OpenCV is imported lazily by the pixel helpers that need it).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy", reason="root-ci installs no media deps")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "trend_edit"))
import beat_grid as bg  # noqa: E402
import fx  # noqa: E402


# ------------------------------------------------------------------ beat grid
def test_fit_grid_recovers_period_and_phase_despite_a_missed_beat():
    true = bg.BeatGrid(period=0.4644, phase=0.02)
    beats = [true.time(k) for k in range(0, 32) if k != 7]            # tracker missed beat 7
    beats = [b + (0.004 if i % 2 else -0.004) for i, b in enumerate(beats)]   # +-4 ms jitter
    g = bg.fit_grid(beats)
    assert g.period == pytest.approx(0.4644, abs=0.002)
    assert g.phase == pytest.approx(0.02, abs=0.01)
    assert g.bpm == pytest.approx(129.2, abs=0.6)


def test_comb_fit_finds_period_and_phase_from_an_onset_envelope():
    times = np.arange(0, 15.0, 1 / 172)
    env = np.zeros_like(times)
    for k in range(32):
        env[int(round((0.02 + k * 0.4644) * 172))] = 1.0
    env += 0.05 * np.random.default_rng(0).random(len(env))
    g = bg.comb_fit(env, times, period_hint=60 / 130.5, rel_range=0.03)
    assert g.period == pytest.approx(0.4644, abs=0.0015)
    d = (g.phase - 0.02 + g.period / 2) % g.period - g.period / 2
    assert abs(d) < 0.012


def test_find_drop_picks_the_biggest_energy_jump():
    e = [0.1, 0.2, 0.15, 0.1, 0.12, 0.1, 0.11, 0.3, 0.9, 0.95, 0.9, 0.92]
    assert bg.find_drop(e) == 8
    flat = [0.3] * 10
    assert 4 <= bg.find_drop(flat) < 10


def test_fit_grid_rejects_too_few_beats():
    with pytest.raises(ValueError):
        bg.fit_grid([0.1, 0.5, 0.9])


def test_nearest_snaps_to_beat_and_half_beat():
    g = bg.BeatGrid(period=0.5, phase=0.0)
    assert g.nearest(1.18) == pytest.approx(1.0)
    assert g.nearest(1.30) == pytest.approx(1.5)
    assert g.nearest(1.30, division=2) == pytest.approx(1.25)
    assert g.nearest(0.0) == 0.0


def test_snap_cuts_drops_cuts_that_collapse_onto_one_beat_and_keeps_order():
    g = bg.BeatGrid(period=0.5, phase=0.0)
    cuts = [4.5, 4.7, 5.03, 5.67, 6.43]       # the reference edit's quick cut cluster
    snapped = bg.snap_cuts(cuts, g)
    assert snapped == sorted(set(snapped))
    assert all(abs(s / 0.5 - round(s / 0.5)) < 1e-6 for s in snapped)
    assert len(snapped) < len(cuts)             # 4.5 and 4.7 land on the same beat
    assert bg.cut_on_beat_share(snapped, g) == 1.0
    assert bg.cut_on_beat_share([0.13, 0.62], g) == 0.0


def test_beats_between_and_bars():
    g = bg.BeatGrid(period=0.5, phase=0.1)
    assert g.beats_between(0.0, 2.0) == [0.1, 0.6, 1.1, 1.6]
    assert g.bar(2) == pytest.approx(0.1 + 8 * 0.5)


def test_section_edges_are_bar_starts_and_strictly_increasing():
    g = bg.BeatGrid(period=0.4644, phase=0.0)
    edges = bg.section_edges(g, [2.0, 2.1, 5.0, 8.0, 12.0, 30.0])
    bar = 4 * 0.4644
    assert all(abs(e / bar - round(e / bar)) < 1e-4 for e in edges)
    assert edges == sorted(edges) and len(set(edges)) == len(edges)


def test_audio_loop_plan_fills_total_with_beat_aligned_seams():
    g = bg.BeatGrid(period=0.5, phase=0.02)
    segs = bg.audio_loop_plan(g, total=30.0, intro_beats=20, loop_beats=8)
    assert segs[0] == (pytest.approx(0.02), pytest.approx(10.0))
    assert sum(d for _s, d in segs) == pytest.approx(30.0)
    for s, d in segs[1:-1]:
        assert s == pytest.approx(0.02 + 20 * 0.5) and d == pytest.approx(4.0)
    # every segment starts on a beat of the track grid
    for s, _d in segs:
        assert (s - 0.02) / 0.5 == pytest.approx(round((s - 0.02) / 0.5))


def test_audio_loop_plan_shorter_than_intro():
    g = bg.BeatGrid(period=0.5)
    segs = bg.audio_loop_plan(g, total=3.0, intro_beats=20, loop_beats=8)
    assert segs == [(0.0, 3.0)]


# ------------------------------------------------------------------ ramps / punch
def test_ramp_with_constant_speed_is_linear():
    assert fx.ramp_source_time(2.0, [(0.0, 1.0)]) == pytest.approx(2.0)
    assert fx.ramp_source_time(2.0, [(0.0, 0.5)], src_start=10.0) == pytest.approx(11.0)
    assert fx.ramp_source_time(3.0, []) == pytest.approx(3.0)


def test_ramp_trapezoid_is_exact_and_continuous():
    knots = [(0.0, 1.0), (1.0, 3.0)]            # speed rises linearly 1 -> 3 over 1 s: integral = 2.0
    assert fx.ramp_source_time(1.0, knots) == pytest.approx(2.0)
    assert fx.ramp_source_time(2.0, knots) == pytest.approx(2.0 + 3.0)       # then held at 3x
    a = fx.ramp_source_time(0.999999, knots)
    b = fx.ramp_source_time(1.000001, knots)
    assert abs(a - b) < 1e-4
    assert fx.ramp_source_time(0.5, knots) == pytest.approx(0.5 * (1.0 + 2.0) / 2)


def test_ramp_is_monotone_for_velocity_curve_and_slows_before_the_hit():
    k = fx.velocity_knots(t_hit=2.0, lead=0.3, hold=0.1, tail=0.25, slow=0.35, fast=2.4)
    ts = [i / 100 for i in range(0, 400)]
    src = [fx.ramp_source_time(t, k) for t in ts]
    assert all(b >= a for a, b in zip(src, src[1:]))
    pre = fx.ramp_source_time(2.0, k) - fx.ramp_source_time(1.7, k)           # 0.3 s of output before the hit
    assert pre < 0.3                                                        # slowed down
    post = fx.ramp_source_time(2.2, k) - fx.ramp_source_time(2.0, k)
    assert post > 0.2                                                       # snapped faster than 1x
    assert fx.ramp_source_time(4.0, k) - fx.ramp_source_time(3.0, k) == pytest.approx(1.0, abs=1e-6)


def test_balanced_velocity_curve_returns_to_sync_after_the_hit():
    k = fx.velocity_knots_balanced(t_hit=2.0, lead=0.3, hold=0.1, tail=0.3, slow=0.35)
    # before the ramp and well after it, source time == output time (footage stays locked to the music)
    assert fx.ramp_source_time(1.0, k) == pytest.approx(1.0)
    assert fx.ramp_source_time(2.3, k) == pytest.approx(2.3, abs=1e-3)
    assert fx.ramp_source_time(5.0, k) == pytest.approx(5.0, abs=1e-3)
    assert fx.ramp_source_time(2.0, k) < 2.0 - 0.05          # it did slow down before the hit
    src = [fx.ramp_source_time(i / 100, k) for i in range(0, 500)]
    assert all(b >= a for a, b in zip(src, src[1:]))


def test_ramp_out_duration_inverts_the_source_time():
    k = fx.velocity_knots(1.0)
    d = fx.ramp_out_duration_for(3.0, k)
    assert fx.ramp_source_time(d, k) == pytest.approx(3.0, abs=0.02)


def test_punch_scale_peaks_at_the_cut_and_decays():
    assert fx.punch_scale(0.9, 1.0) == 1.0
    assert fx.punch_scale(1.0, 1.0, peak=0.14) == pytest.approx(1.14)
    assert fx.punch_scale(1.16, 1.0, peak=0.14, decay=0.16) == pytest.approx(1 + 0.14 / math.e)
    assert fx.punch_scale(3.0, 1.0) == pytest.approx(1.0, abs=1e-6)


def test_frame_pick_blends_between_frames():
    assert fx.frame_pick(0.0, 24, 10) == (0, 1, 0.0)
    i0, i1, w = fx.frame_pick(1.5 / 24, 24, 10)
    assert (i0, i1) == (1, 2) and w == pytest.approx(0.5)
    assert fx.frame_pick(99.0, 24, 10)[:2] == (9, 9)


def test_alternating_cuts_cover_the_range_and_toggle():
    cuts = fx.alternating_cuts(2.0, 5.0, 0.5)
    assert cuts[0][0] == 2.0 and cuts[-1][1] == 5.0
    assert [c[2] for c in cuts] == [0, 1, 0, 1, 0, 1]
    assert all(b[0] == pytest.approx(a[1]) for a, b in zip(cuts, cuts[1:]))


# ------------------------------------------------------------------ pixels
def test_rgb_split_moves_red_and_blue_opposite_and_keeps_green():
    f = np.zeros((4, 8, 3), np.uint8)
    f[:, 4, :] = 200
    o = fx.rgb_split(f, 2)
    assert o[0, 6, 2] == 200 and o[0, 4, 2] == 0            # red moved right
    assert o[0, 2, 0] == 200 and o[0, 4, 0] == 0            # blue moved left
    assert (o[:, :, 1] == f[:, :, 1]).all()
    assert fx.rgb_split(f, 0) is f


def test_flash_blends_toward_white_and_zero_is_identity():
    f = np.full((2, 2, 3), 100, np.uint8)
    assert fx.flash(f, 0.0) is f
    assert int(fx.flash(f, 0.5)[0, 0, 0]) == 177
    assert int(fx.flash(f, 1.0)[0, 0, 0]) == 255


def test_wipe_endpoints_and_monotone_coverage():
    a = np.zeros((20, 40, 3), np.uint8)
    b = np.full((20, 40, 3), 255, np.uint8)
    assert fx.wipe(a, b, 0.0) is a and fx.wipe(a, b, 1.0) is b
    cover = [float(fx.wipe_mask(20, 40, p, "h").mean()) for p in (0.1, 0.3, 0.5, 0.7, 0.9)]
    assert cover == sorted(cover) and 0.0 < cover[0] and cover[-1] < 1.0
    for kind in ("v", "diag", "circle"):
        m = fx.wipe_mask(20, 40, 0.5, kind)
        assert 0.2 < float(m.mean()) < 0.8
    with pytest.raises(ValueError):
        fx.wipe_mask(4, 4, 0.5, "spiral")


def test_split_before_after_puts_after_on_the_right_of_the_divider():
    a = np.zeros((6, 10, 3), np.uint8)
    b = np.full((6, 10, 3), 90, np.uint8)
    o = fx.split_before_after(a, b, 0.5, line=0)
    assert int(o[0, 1, 0]) == 0 and int(o[0, 8, 0]) == 90


# ------------------------------------------------------------------ text
def test_drawtext_escapes_colons_percent_and_font_drive_colon():
    s = fx.drawtext("Motion Studio: Bossman 2, 100% local", 1.0, 3.0)
    assert "C\\:/Windows/Fonts/" in s
    assert "Studio\\: Bossman" in s
    assert "2\\, 100" in s
    assert "100\\\\% local" in s
    assert "enable='between(t,1.0000,3.0000)'" in s
    assert "'" not in fx.escape_drawtext("it's")


def test_drawtext_box_and_rise_options():
    s = fx.drawtext("X", 0.0, 1.0, box=True, rise=40)
    assert "box=1" in s and "pow(1-" in s


# ------------------------------------------------------------------ promo plan
def test_promo_plan_has_the_brief_sections_in_order_on_whole_beats():
    sys.path.insert(0, str(ROOT / "tools" / "trend_edit"))
    import build_promo as bp
    plan = bp.make_plan()
    names = list(plan.sections)
    assert names == ["original", "swap", "hair", "outfit", "background", "endcard"]
    starts = [plan.sections[n][0] for n in names]
    assert starts == sorted(starts)
    assert all(s % 4 == 0 for s in starts)                       # every section opens on a bar
    period = 0.4644
    t = [b * period for b in starts]
    assert 1.5 < t[1] < 2.2 and 4.8 < t[2] < 6.0 and 7.0 < t[3] < 9.5 and 12.0 < t[4] < 13.5   # the brief's 2/5/8/12 s
    assert all(h[0] <= plan.sections["endcard"][0] for h in plan.hits)
    texts = bp.text_plan(period)
    assert all(0.0 <= x.t0 < x.t1 <= 66 * period + 1e-6 for x in texts)
    assert any("GENJUTSU" in x.text for x in texts) and any("MOTION STUDIO" in x.text for x in texts)
    assert any("trial" in x.text for x in texts)                 # the outfit stage is labelled honestly
