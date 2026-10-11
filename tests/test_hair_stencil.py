"""tools/trend_edit/stencil: pure parts of the hair-stencil workflow.

numpy + OpenCV only (no models, no GPU, no network). The HTML editor is checked statically (required element ids, export
suffix) because no browser / Node is assumed.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy", reason="media extra not installed (pip install -e \".[media]\"; root-ci installs it)")
cv2 = pytest.importorskip("cv2", reason="media extra not installed (pip install -e \".[media]\"; root-ci installs it)")

ROOT = Path(__file__).resolve().parents[1]
STENCIL = ROOT / "tools" / "trend_edit" / "stencil"
sys.path.insert(0, str(STENCIL))
import stencil_core as sc  # noqa: E402
import stencil_propagate as sp  # noqa: E402


# ------------------------------------------------------------------ keyframe picker
def _features(n=40, seed=5):
    rng = np.random.default_rng(seed)
    t = np.linspace(0, 1, n)
    yaw = np.sin(t * 6.0)
    pitch = np.cos(t * 4.0) * 0.5
    area = 0.5 + 0.2 * np.sin(t * 9.0)
    return np.stack([yaw, pitch, area], 1) + rng.normal(0, 0.01, (n, 3))


def test_picker_is_deterministic_and_returns_k_sorted_unique():
    F = _features()
    a = sc.pick_keyframes(F, k=9, seed=0)
    b = sc.pick_keyframes(F.copy(), k=9, seed=0)
    assert a == b
    assert a == sorted(set(a))
    assert len(a) == 9
    assert 0 in a and len(F) - 1 in a                      # ends are always keyframes


def test_picker_covers_pose_change_better_than_uniform_spacing():
    F = _features(60)
    picks = sc.pick_keyframes(F, k=8, seed=0)
    Z = sc.standardise(F)

    def worst_gap(idx):                                     # farthest frame from its nearest keyframe in feature space
        d = ((Z[:, None, :] - Z[idx][None]) ** 2).sum(-1).min(1)
        return float(d.max())

    uniform = list(np.linspace(0, len(F) - 1, 8).round().astype(int))
    assert worst_gap(picks) <= worst_gap(uniform) * 1.05


def test_picker_respects_min_gap_and_small_inputs():
    F = _features(12)
    p = sc.pick_keyframes(F, k=6, min_gap=2)
    assert all(b - a >= 2 for a, b in zip(p, p[1:]))
    assert sc.pick_keyframes(F[:4], k=9) == [0, 1, 2, 3]


def test_kmeans_separates_two_blobs():
    rng = np.random.default_rng(0)
    X = np.vstack([rng.normal(0, 0.1, (20, 2)), rng.normal(5, 0.1, (20, 2))])
    lab, _ = sc.kmeans(X, 2, seed=1)
    assert len(set(lab[:20])) == 1 and len(set(lab[20:])) == 1 and lab[0] != lab[-1]


# ------------------------------------------------------------------ metrics
def _disc(h, w, cx, cy, r):
    yy, xx = np.mgrid[:h, :w]
    return (((xx - cx) ** 2 + (yy - cy) ** 2) <= r * r).astype(np.float32)


def test_iou_and_boundary_f1_basic():
    a = _disc(100, 100, 50, 50, 20)
    assert sc.iou(a, a) == 1.0
    assert sc.iou(a, np.zeros_like(a)) == 0.0
    b = _disc(100, 100, 52, 50, 20)
    assert 0.7 < sc.iou(a, b) < 0.95
    assert sc.boundary_f1(a, b, 3) > 0.9
    assert sc.boundary_f1(a, _disc(100, 100, 70, 50, 20), 3) < 0.4


def test_separability_prefers_light_background_over_dark_hoodie():
    h, w = 160, 160
    hair = _disc(h, w, 80, 70, 30)
    skin = _disc(h, w, 80, 95, 14)
    light = np.full((h, w), 70.0, np.float32)
    dark = light.copy()
    dark[96:, :] = 5.0                                     # black hoodie under / beside the hair
    dark[:, :52] = 5.0
    s_light = sc.separability(hair, skin, light)
    s_dark = sc.separability(hair, skin, dark)
    assert s_light["score"] > 0.95
    assert s_dark["score"] < s_light["score"] - 0.15


def test_pose_proxy_moves_with_hair_offset():
    skin = _disc(120, 120, 60, 70, 16)
    left = sc.mask_pose_features(_disc(120, 120, 48, 55, 26), skin)
    right = sc.mask_pose_features(_disc(120, 120, 72, 55, 26), skin)
    assert left["yaw"] < 0 < right["yaw"]


# ------------------------------------------------------------------ propagation on synthetic masks
def _moving_scene(n=13):
    """Textured background + a textured disc 'head' translating and growing; GT = the disc. Deterministic."""
    h, w = 160, 220
    rng = np.random.default_rng(3)
    tex = cv2.GaussianBlur(rng.integers(0, 255, (h, w, 3), dtype=np.uint8), (0, 0), 2.0)
    tex = cv2.normalize(tex, None, 0, 255, cv2.NORM_MINMAX)
    head_tex = cv2.GaussianBlur(rng.integers(0, 255, (h, w, 3), dtype=np.uint8), (0, 0), 1.5)
    frames, masks = [], []
    for t in range(n):
        cx, cy, r = 70 + 3 * t, 80 + 1.0 * t, 28 + 0.3 * t
        m = _disc(h, w, cx, cy, r)
        # head content moves with the disc (shift of the texture), background static
        M = np.float32([[1, 0, 3 * t], [0, 1, 1.0 * t]])
        shifted = cv2.warpAffine(head_tex, M, (w, h), borderMode=cv2.BORDER_REFLECT)
        fr = np.where(m[..., None] > 0, shifted, tex).astype(np.uint8)
        frames.append(fr)
        masks.append(m)
    return frames, masks


def test_flow_propagation_recovers_synthetic_motion_far_better_than_copying():
    frames, masks = _moving_scene()
    keys = {0: masks[0], 6: masks[6], 12: masks[12]}
    pr = sp.propagate_flow(frames, keys, "dis")
    mid = [3, 9]
    got = float(np.mean([sc.iou(pr[t], masks[t]) for t in mid]))
    copy = float(np.mean([sc.iou(masks[0 if t < 6 else 6], masks[t]) for t in mid]))
    assert got > 0.9
    assert got > copy + 0.1
    assert all(t in pr for t in range(len(frames)))
    assert np.array_equal(pr[6], masks[6])                  # keyframes pass through unchanged


def test_holdout_eval_reports_iou_and_baseline():
    frames, masks = _moving_scene()
    keys = {0: masks[0], 4: masks[4], 8: masks[8], 12: masks[12]}
    rows = sp.holdout_eval(frames, keys, [4, 8])
    assert [r["frame"] for r in rows] == [4, 8]
    assert all(r["iou"] > 0.88 for r in rows)
    assert all(r["iou"] > r["baseline_copy_iou"] for r in rows)
    s = sp.summarise(rows)
    assert s["n"] == 2 and s["iou_min"] > 0.88


def test_band_refine_does_not_grow_far_from_the_boundary():
    frame = np.full((120, 120, 3), 20, np.uint8)            # uniformly dark: a guided filter has nothing to snap to
    m = _disc(120, 120, 60, 60, 20)
    out = sp.band_refine(frame, m)
    far = (_disc(120, 120, 60, 60, 20 + sp.BAND_PX + 2) == 0)
    assert float(out[far].max()) == 0.0
    assert sc.iou(out, m) > 0.9


def test_frame_number_parsing_from_pack_names():
    assert sp.frame_no("adb-promo6_clip1_f00123_edited.png") == 123
    with pytest.raises(ValueError):
        sp.frame_no("nothing.png")


# ------------------------------------------------------------------ editor html (static check)
HTML = (STENCIL / "hair_stencil_editor.html").read_text(encoding="utf8")
REQUIRED_IDS = ["frame-file", "mask-file", "view", "brush-size", "brush-soft", "overlay-toggle", "overlay-opacity",
                "undo", "redo", "export", "export-all", "zoom-fit", "zoom-in", "zoom-out", "tool-add", "tool-erase",
                "tool-pan", "item-select", "help"]


@pytest.mark.parametrize("ident", REQUIRED_IDS)
def test_editor_has_required_element_ids(ident):
    assert re.search(r'id="%s"' % re.escape(ident), HTML), ident


def test_editor_is_self_contained_and_phone_friendly():
    assert not re.search(r'(src|href)\s*=\s*"https?://', HTML)            # no CDN / remote asset
    assert "<script src" not in HTML and "<link rel=\"stylesheet\"" not in HTML
    assert "width=device-width" in HTML
    assert "@media (max-width" in HTML
    assert "Инструкция" in HTML                                           # RU instruction block inside the page


def test_editor_export_name_rule_present():
    # exportName(): stem + "_edited" (once) + ".png"
    m = re.search(r"function exportName\(srcName\)\{(.*?)\n\}", HTML, re.S)
    assert m, "exportName() missing"
    body = m.group(1)
    assert '"_edited"' in body and '".png"' in body
    assert re.search(r"/_edited\$/", body)                                 # no double suffix
    # the python twin of the rule, kept equal to the JS by this test
    def export_name(name: str) -> str:
        stem = re.sub(r"\.[^./\\]+$", "", name)
        if not stem.endswith("_edited"):
            stem += "_edited"
        return stem + ".png"
    assert export_name("clip_f00090.png") == "clip_f00090_edited.png"
    assert export_name("clip_f00090_edited.png") == "clip_f00090_edited.png"
