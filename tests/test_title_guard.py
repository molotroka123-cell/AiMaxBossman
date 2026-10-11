"""tools/trend_edit/title_guard.py: titles never sit over a face (owner defect: titles over face)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "trend_edit"))
import title_guard as tg  # noqa: E402

W, H = 1080, 1920
FACE = (380, 520, 700, 900)            # x0, y0, x1, y1


def test_overlap_is_detected_with_margin():
    assert tg.overlaps((400, 600, 800, 700), FACE)
    assert not tg.overlaps((0, 1500, 600, 1600), FACE)
    # just outside the face box but inside the safety margin
    assert tg.overlaps((380, 910, 700, 960), FACE, margin=20)
    assert not tg.overlaps((380, 910, 700, 960), FACE, margin=0)


def test_place_title_prefers_lower_third_and_clears_all_faces():
    rect = tg.place_title(W, H, 700, 120, faces=[FACE])
    assert rect is not None
    assert rect[1] >= H * 2 // 3 - 1                     # lower third
    assert not tg.overlaps(rect, FACE, margin=tg.DEFAULT_MARGIN)
    assert 0 <= rect[0] and rect[2] <= W and rect[3] <= H


def test_place_title_moves_away_when_face_is_in_the_lower_third():
    low_face = (300, 1300, 780, 1800)
    rect = tg.place_title(W, H, 700, 120, faces=[low_face])
    assert rect is not None and not tg.overlaps(rect, low_face, margin=tg.DEFAULT_MARGIN)


def test_place_title_reports_impossible_instead_of_overlapping():
    full = (0, 0, W, H)
    assert tg.place_title(W, H, 700, 120, faces=[full]) is None


def test_check_titles_flags_every_frame_violation():
    titles = [{"t0": 0.0, "t1": 2.0, "rect": (0, 1500, 600, 1600)},
              {"t0": 2.0, "t1": 4.0, "rect": (400, 600, 800, 700)}]
    bad = tg.check_titles(titles, lambda t: [FACE], step=0.5)
    assert [b["index"] for b in bad] == [1]
    assert bad[0]["t"] >= 2.0
