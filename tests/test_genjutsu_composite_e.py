"""Synthetic checks for the opt-in Genjutsu E compositor; no models or user media."""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy", reason="Genjutsu compositor requires the optional media extra")
pytest.importorskip("cv2", reason="Genjutsu compositor requires the optional media extra")

from tools.motion_studio.genjutsu_composite_e import (
    RVMForeground,
    composite_head_frame,
    iter_composite_e,
    restore_background_frame,
)


def frame(value: int, height: int = 6, width: int = 6) -> np.ndarray:
    return np.full((height, width, 3), value, dtype=np.uint8)


def test_head_alpha_zero_keeps_base_and_does_not_mutate_input() -> None:
    base = frame(17)
    result = composite_head_frame(base, frame(220, 4, 4), np.zeros((4, 4)),
                                  {"x": 1, "y": 1, "s": 4},
                                  feather=0, bottom_fade=None, match_color=False)
    np.testing.assert_array_equal(result, base)
    np.testing.assert_array_equal(base, frame(17))


def test_opaque_head_changes_only_its_box() -> None:
    base = frame(17)
    result = composite_head_frame(base, frame(220, 4, 4), np.ones((4, 4)),
                                  {"x": 1, "y": 1, "s": 4},
                                  feather=0, bottom_fade=None, match_color=False)
    expected = base.copy()
    expected[1:5, 1:5] = 220
    np.testing.assert_array_equal(result, expected)


@pytest.mark.parametrize("box", [
    {"x": -1, "y": 0, "s": 4}, {"x": 3, "y": 0, "s": 4},
    {"x": 0, "y": 0, "s": 0}, {"x": 1.5, "y": 0, "s": 2},
    {"x": 0, "y": 0},
])
def test_invalid_boxes_are_rejected_instead_of_clipped(box: dict) -> None:
    with pytest.raises(ValueError, match="box"):
        composite_head_frame(frame(0), frame(100, 4, 4), np.ones((4, 4)), box)


@pytest.mark.parametrize("alpha", [np.full((4, 4), np.nan), np.full((4, 4), 1.1), np.ones((3, 4))])
def test_invalid_alpha_is_rejected(alpha: np.ndarray) -> None:
    with pytest.raises(ValueError, match="alpha"):
        composite_head_frame(frame(0), frame(100, 4, 4), alpha, {"x": 1, "y": 1, "s": 4})


def test_background_restore_zero_and_one_masks_keep_correct_pixels() -> None:
    original, generated = frame(30), frame(190)
    np.testing.assert_array_equal(
        restore_background_frame(generated, original, np.zeros((6, 6)), dilate_px=0, blur_sigma=0),
        original,
    )
    np.testing.assert_array_equal(
        restore_background_frame(generated, original, np.ones((6, 6)), dilate_px=0, blur_sigma=0),
        generated,
    )


def test_background_restore_rejects_unaligned_geometry() -> None:
    with pytest.raises(ValueError, match="geometry"):
        restore_background_frame(frame(1), frame(2, 7, 6), np.ones((6, 6)))
    with pytest.raises(ValueError, match="mask"):
        restore_background_frame(frame(1), frame(2), np.ones((5, 6)))


def test_stream_is_incremental_and_rejects_different_frame_counts() -> None:
    class Matte:
        def __init__(self):
            self.calls = 0

        def __call__(self, image):
            self.calls += 1
            return image.astype(np.float32), np.ones(image.shape[:2], dtype=np.float32)

    matte = Matte()
    boxes = [{"i": 0, "x": 1, "y": 1, "s": 4}, {"i": 1, "x": 1, "y": 1, "s": 4}]
    stream = iter_composite_e([frame(10), frame(10)], [frame(120, 4, 4)],
                              [frame(20), frame(20)], [np.ones((6, 6))] * 2, boxes,
                              matte=matte, dilate_px=0, blur_sigma=0)
    assert matte.calls == 0
    assert next(stream).shape == (6, 6, 3)
    assert matte.calls == 1
    with pytest.raises(ValueError, match="frame count"):
        next(stream)


def test_stream_rejects_out_of_order_boxes() -> None:
    with pytest.raises(ValueError, match="box index"):
        list(iter_composite_e([frame(1)], [frame(2, 4, 4)], [frame(3)], [np.ones((6, 6))],
                              [{"i": 1, "x": 1, "y": 1, "s": 4}],
                              matte=lambda h: (h.astype(np.float32), np.ones((4, 4)))))


def test_stream_rejects_a_change_in_head_geometry() -> None:
    stream = iter_composite_e([frame(1), frame(1)], [frame(2, 4, 4), frame(2, 3, 4)],
                              [frame(3), frame(3)], [np.ones((6, 6))] * 2,
                              [{"i": 0, "x": 1, "y": 1, "s": 4}, {"i": 1, "x": 1, "y": 1, "s": 4}],
                              matte=lambda h: (h.astype(np.float32), np.ones(h.shape[:2])))
    next(stream)
    with pytest.raises(ValueError, match="head.*geometry"):
        next(stream)


def test_rvm_without_local_model_fails_closed() -> None:
    with pytest.raises(FileNotFoundError, match="local RVM model"):
        RVMForeground()


def test_rvm_reuses_recurrent_state_and_returns_bgr_foreground() -> None:
    class FakeSession:
        def __init__(self):
            self.inputs = []

        def run(self, outputs, inputs):
            self.inputs.append(inputs)
            src = inputs["src"]
            alpha = np.ones((1, 1, src.shape[2], src.shape[3]), dtype=np.float32)
            states = [np.full((1, 1, 1, 1), len(self.inputs), dtype=np.float32)] * 4
            return [src, alpha, *states]

    session = FakeSession()
    rvm = RVMForeground(session=session)
    bgr = np.zeros((4, 5, 3), np.uint8)
    bgr[:, :, 0] = 50
    foreground, alpha = rvm(bgr)
    rvm(bgr)
    np.testing.assert_array_equal(foreground, bgr.astype(np.float32))
    assert alpha.shape == (4, 5)
    assert session.inputs[0]["src"][0, 2, 0, 0] == pytest.approx(50 / 255)
    assert session.inputs[1]["r1i"][0, 0, 0, 0] == 1
