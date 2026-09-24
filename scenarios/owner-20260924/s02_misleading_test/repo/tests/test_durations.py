import pytest
from durations import parse_duration


def test_seconds_only():
    assert parse_duration("45s") == 45


def test_minutes_and_seconds():
    assert parse_duration("2m5s") == 125


def test_hours_and_minutes():
    assert parse_duration("1h30m") == 90


def test_rejects_garbage():
    with pytest.raises(ValueError):
        parse_duration("abc")
