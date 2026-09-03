from __future__ import annotations

import pytest

from ai_webcam_vision.pipeline.lowres_detection import (
    BoundingBox,
    Detection,
    ImageSize,
    LetterboxTransform,
    should_sample_for_detection,
)


def test_16_by_9_box_round_trips_between_lowres_and_highres():
    transform = LetterboxTransform.fit(ImageSize(1920, 1080), ImageSize(320, 180))
    assert transform.pad_left == 0
    assert transform.pad_top == 0
    source_box = BoundingBox(600, 300, 1200, 900)
    detector_box = transform.source_to_inference(source_box)
    assert detector_box == BoundingBox(100, 50, 200, 150)
    assert transform.inference_to_source(detector_box) == source_box


def test_4_by_3_box_round_trips_with_letterbox_padding():
    transform = LetterboxTransform.fit(ImageSize(640, 480), ImageSize(320, 180))
    assert transform.scaled == ImageSize(240, 180)
    assert transform.pad_left == 40
    source_box = BoundingBox(100, 120, 300, 360)
    detector_box = transform.source_to_inference(source_box)
    assert detector_box == BoundingBox(77.5, 45, 152.5, 135)
    assert transform.inference_to_source(detector_box) == source_box


def test_box_wholly_in_padding_is_not_reported_as_an_object():
    transform = LetterboxTransform.fit(ImageSize(640, 480), ImageSize(320, 180))
    padding_only = Detection("person", 0.9, BoundingBox(0, 10, 30, 80))
    assert transform.map_detection_to_source(padding_only) is None


def test_partial_box_is_clipped_to_source_bounds():
    transform = LetterboxTransform.fit(ImageSize(1920, 1080), ImageSize(320, 180))
    mapped = transform.inference_to_source(BoundingBox(-10, -5, 20, 20))
    assert mapped == BoundingBox(0, 0, 120, 120)


def test_detection_sampling_is_off_by_default_and_rate_limited():
    assert should_sample_for_detection(sequence=1, enabled=False, every_n_frames=3) is False
    assert [
        should_sample_for_detection(sequence=sequence, enabled=True, every_n_frames=3)
        for sequence in range(1, 8)
    ] == [True, False, False, True, False, False, True]


@pytest.mark.parametrize("width,height", [(0, 10), (10, 0), (-1, 10)])
def test_invalid_image_sizes_are_rejected(width, height):
    with pytest.raises(ValueError):
        ImageSize(width, height)


def test_invalid_box_and_sampling_configuration_are_rejected():
    with pytest.raises(ValueError):
        BoundingBox(1, 1, 1, 2)
    with pytest.raises(ValueError):
        should_sample_for_detection(sequence=0, enabled=True, every_n_frames=1)
    with pytest.raises(ValueError):
        should_sample_for_detection(sequence=1, enabled=True, every_n_frames=0)


@pytest.mark.parametrize(
    "width,height",
    [(True, 10), (10, False), (320.0, 180), (320, 180.0), (float("nan"), 180), (320, float("inf"))],
)
def test_non_integer_image_sizes_are_rejected(width, height):
    """Размер кадра — целое число пикселей.

    bool проходит как int, а float обычно означает недоделённое деление; и то и
    другое молча искажает масштаб, поэтому отвергаем на входе, а не в геометрии.
    """
    with pytest.raises(TypeError):
        ImageSize(width, height)


def test_odd_letterbox_keeps_box_within_source_and_round_trips():
    """Нечётные размеры: паддинг получается дробным, а обратное отображение
    обязано остаться внутри исходного кадра и вернуть ту же рамку."""
    source, inference = ImageSize(1920, 1079), ImageSize(641, 385)
    transform = LetterboxTransform.fit(source, inference)
    assert transform.pad_left >= 0 and transform.pad_top >= 0
    assert transform.pad_top % 1 != 0, f"нужен дробный паддинг, получен {transform.pad_top}"

    original = BoundingBox(3, 5, 1917, 1074)
    detector_box = transform.source_to_inference(original)
    assert 0 <= detector_box.x1 and detector_box.x2 <= inference.width
    assert 0 <= detector_box.y1 and detector_box.y2 <= inference.height

    back = transform.inference_to_source(detector_box)
    assert back is not None
    for got, want in zip((back.x1, back.y1, back.x2, back.y2),
                         (original.x1, original.y1, original.x2, original.y2)):
        assert abs(got - want) < 1.0, (got, want)
    assert 0 <= back.x1 < back.x2 <= source.width
    assert 0 <= back.y1 < back.y2 <= source.height


def test_box_covering_only_padding_is_not_reported_as_an_object():
    """Рамка целиком в чёрных полях — не объект, а артефакт детектора."""
    transform = LetterboxTransform.fit(ImageSize(640, 480), ImageSize(320, 180))
    assert transform.pad_left > 0, "у этой пары поля слева и справа, а не сверху"
    assert transform.inference_to_source(BoundingBox(0, 10, transform.pad_left, 100)) is None
