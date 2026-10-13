"""Centre-pip suit evidence (``vision.pipshape``) on synthetic silhouettes: each suit's big pip is recognised from its
shape at several card widths, a blob that is no suit stays UNKNOWN, and the colour family is decided by the caller."""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from pokervision.vision.pipshape import suit_from_centre_pip

RED, BLACK = (30, 30, 190), (25, 25, 25)


def _big_pip(img, suit, cx, cy, r, col):
    if suit == "h":
        cv2.circle(img, (cx - r // 2, cy - r // 3), int(.55 * r), col, -1, cv2.LINE_AA)
        cv2.circle(img, (cx + r // 2, cy - r // 3), int(.55 * r), col, -1, cv2.LINE_AA)
        cv2.fillConvexPoly(img, np.array([[cx - r, cy - r // 6], [cx + r, cy - r // 6], [cx, cy + r]]), col)
    elif suit == "d":
        cv2.fillConvexPoly(img, np.array([[cx, cy - r], [cx + int(.8 * r), cy], [cx, cy + r], [cx - int(.8 * r), cy]]), col)
    elif suit == "s":
        cv2.fillConvexPoly(img, np.array([[cx, cy - r], [cx + r, cy + r // 6], [cx - r, cy + r // 6]]), col)
        cv2.circle(img, (cx - r // 2, cy + r // 3), int(.55 * r), col, -1, cv2.LINE_AA)
        cv2.circle(img, (cx + r // 2, cy + r // 3), int(.55 * r), col, -1, cv2.LINE_AA)
        cv2.fillConvexPoly(img, np.array([[cx, cy + r // 2], [cx + r // 3, cy + int(1.05 * r)], [cx - r // 3, cy + int(1.05 * r)]]), col)
    elif suit == "c":
        cv2.circle(img, (cx, cy - int(.45 * r)), int(.6 * r), col, -1)
        for dx in (-int(.55 * r), int(.55 * r)):
            cv2.circle(img, (cx + dx, cy + int(.3 * r)), int(.45 * r), col, -1)
        cv2.fillConvexPoly(img, np.array([[cx, cy], [cx + r // 4, cy + r], [cx - r // 4, cy + r]]), col)
    else:                                                     # "square": not a suit
        cv2.rectangle(img, (cx - r, cy - r), (cx + r, cy + r), col, -1)


def _card(suit, w, col):
    h = int(w / 0.7)
    img = np.full((h + 20, w + 20, 3), (40, 110, 45), np.uint8)
    cv2.rectangle(img, (10, 10), (10 + w, 10 + h), (238, 238, 238), -1)
    _big_pip(img, suit, 10 + int(0.68 * w), 10 + int(0.72 * h), int(0.18 * w), col)
    return img


@pytest.mark.parametrize("w", [90, 120])
@pytest.mark.parametrize("suit", list("shdc"))
def test_each_suit_is_recognised_from_its_big_pip(suit, w):
    red = suit in "hd"
    got, why = suit_from_centre_pip(_card(suit, w, RED if red else BLACK), 10, 10, w, red)
    assert got == suit, why


@pytest.mark.parametrize("red", [False, True])
def test_a_blob_that_is_no_suit_stays_unknown(red):
    got, why = suit_from_centre_pip(_card("square", 120, RED if red else BLACK), 10, 10, 120, red)
    assert got is None and why.startswith("centre_pip_shape_ambiguous")


def test_wrong_colour_family_finds_no_pip_and_never_guesses():
    # a red heart looked at as BLACK ink: no black silhouette at all -> UNKNOWN, never "s" or "c"
    got, why = suit_from_centre_pip(_card("h", 120, RED), 10, 10, 120, red=False)
    assert got is None and why == "centre_pip_not_found"


def test_card_outside_the_frame_is_unknown():
    img = _card("s", 120, BLACK)
    got, why = suit_from_centre_pip(img, img.shape[1] - 4, img.shape[0] - 4, 120, red=False)
    assert got is None and why == "centre_pip_outside_frame"
