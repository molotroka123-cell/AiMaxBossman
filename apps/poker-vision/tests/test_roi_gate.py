"""Unknown layouts must pass calibration AND held-out verification; otherwise every field stays UNKNOWN."""
import random

import cv2
import numpy as np
import pytest

from pokervision.adapters.base import Frame
from pokervision.adapters.roi import RoiAdapter

W, H = 800, 450
ROIS = {"pot": [0.40, 0.30, 0.20, 0.08]}


def frame(pot: str, font=cv2.FONT_HERSHEY_SIMPLEX, scale=1.0, size=(W, H), noise=0, seed=0) -> Frame:
    img = np.full((size[1], size[0], 3), (30, 90, 50), np.uint8)
    x0, y0, w, h = int(ROIS["pot"][0] * size[0]), int(ROIS["pot"][1] * size[1]), int(ROIS["pot"][2] * size[0]), int(ROIS["pot"][3] * size[1])
    cv2.putText(img, pot, (x0 + 4, y0 + h - 6), font, scale, (255, 255, 255), 2, cv2.LINE_AA)
    if noise:
        rng = np.random.default_rng(seed)
        img = np.clip(img.astype(int) + rng.integers(-noise, noise, img.shape), 0, 255).astype(np.uint8)
    return Frame(img, 0, "synthetic", f"s{pot}")


def labelled(values, **kw):
    return [(frame(v, **kw), {"pot": v}) for v in values]


def values(n, seed):
    rng = random.Random(seed)
    return [str(rng.randint(10, 99999)) for _ in range(n)]


def make_calibrated():
    ad = RoiAdapter()
    cal = labelled(["1234567890", "9081726354", "5566778899", "1020304050"] + values(30, 1))
    ad.calibrate(cal, ROIS)
    return ad


def test_uncalibrated_and_unverified_refuse():
    ad = RoiAdapter()
    assert "layout_not_calibrated" in ad.read(frame("123")).pot.reason
    ad = make_calibrated()
    assert ad.profile.data["verified"] is False
    st = ad.read(frame("123"))
    assert not st.pot.known and "not_verified" in st.pot.reason


def test_verification_opens_the_gate_on_heldout_frames():
    ad = make_calibrated()
    rep = ad.verify(labelled(values(20, 99)))
    assert rep.passed, rep
    st = ad.read(frame("4821"))
    assert st.pot.known and st.pot.value.amount == 4821


def test_negative_control_poisoned_calibration_does_not_verify():
    """Calibration labels with 1 and 7 swapped teach wrong glyphs: the held-out check must catch it and keep the gate shut."""
    swap = str.maketrans("17", "71")
    ad = RoiAdapter()
    ad.calibrate([(f, {"pot": t["pot"].translate(swap)}) for f, t in labelled(["1234567890", "9081726354", "5566778899", "1020304050"] + values(30, 1))], ROIS)
    rep = ad.verify(labelled(values(30, 99)))
    assert not rep.passed and rep.money_acc is not None and rep.money_acc < 0.98
    assert not ad.read(frame("4821")).pot.known                       # gate closed


def test_a_different_look_never_yields_confident_errors():
    """Legitimate case: another font/noise may raise UNKNOWN but must not raise wrong answers."""
    ad = make_calibrated()
    ad.profile.data["verified"] = True
    wrong = 0
    for v in values(25, 7):
        f = ad.read(frame(v, font=cv2.FONT_HERSHEY_COMPLEX, scale=1.4, noise=40, seed=3)).pot
        wrong += int(f.known and f.value.amount != float(v))
    assert wrong == 0


def test_too_few_heldout_frames_do_not_verify():
    ad = make_calibrated()
    assert not ad.verify(labelled(values(3, 5))).passed


def test_changed_aspect_ratio_refused_even_when_verified():
    ad = make_calibrated(); assert ad.verify(labelled(values(20, 99))).passed
    st = ad.read(frame("4821", size=(450, 800)))
    assert not st.pot.known and "aspect_ratio" in st.pot.reason


def test_window_scaling_with_same_aspect_still_reads():
    ad = make_calibrated(); assert ad.verify(labelled(values(20, 99))).passed
    big = frame("4821", scale=1.0, size=(W, H))
    big_img = cv2.resize(big.bgr, (int(W * 1.25), int(H * 1.25)), interpolation=cv2.INTER_CUBIC)
    st = ad.read(Frame(big_img, 0, "s", "big"))
    assert not st.pot.known or st.pot.value.amount == 4821           # right or UNKNOWN, never a confident wrong value
