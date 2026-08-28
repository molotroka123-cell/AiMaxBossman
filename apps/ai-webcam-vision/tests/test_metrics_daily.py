"""Daily metrics: the numbers the owner actually looks at.

Every defect here is silent. A wrong day boundary, a double-counted
observation or a utilisation figure divided by the wrong denominator all
produce a plausible number that is simply not true.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ai_webcam_vision.config import Settings
from ai_webcam_vision.crm.base import CrmContext
from ai_webcam_vision.errors import ConfigError
from ai_webcam_vision.pipeline.analysis import Evidence
from ai_webcam_vision.pipeline.classifier import Classification, State
from ai_webcam_vision.storage import Store

PRAGUE = timezone(timedelta(hours=2))  # Europe/Prague in summer


def observation(store: Store, ts: datetime, state: State, room: str = "r1") -> int:
    return store.add_observation(
        room_id=room,
        evidence=Evidence(ts=ts, room_change=0.1, chair_change=0.2, work_motion=0.03,
                          motion_gate=True, frame_seq=1),
        crm=CrmContext(available=True, source="mock", is_mock=True, appointment_id="a1"),
        classification=Classification(state=state, confidence=0.9, reasons=["r"]),
        debounced_state=state,
        source_kind="synthetic",
        source_is_mock=True,
    )


# ------------------------------------------------------------ day boundary
def test_day_boundary_follows_the_configured_timezone(tmp_path):
    """A clinic day ends at local midnight, not at UTC midnight."""
    store = Store(tmp_path / "db.sqlite3", timezone_name="Europe/Prague")
    # 23:30 local on the 27th is 21:30 UTC on the 27th.
    late_yesterday = datetime(2026, 8, 27, 21, 30, tzinfo=timezone.utc)
    # 00:30 local on the 28th is 22:30 UTC on the 27th — still the 27th in UTC.
    early_today = datetime(2026, 8, 27, 22, 30, tzinfo=timezone.utc)

    observation(store, late_yesterday, State.CLINICAL_WORK)
    observation(store, early_today, State.CLINICAL_WORK)

    at = datetime(2026, 8, 28, 9, 0, tzinfo=PRAGUE)
    start, end = store.today_bounds(at)
    assert start.astimezone(PRAGUE).hour == 0
    assert end - start == timedelta(days=1)
    metrics = store.metrics("r1", start, end)
    assert metrics["samples"] == 1, "the 23:30 local sample belongs to the previous day"
    assert metrics["timezone"] == "Europe/Prague"


def test_day_boundary_defaults_to_utc(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    start, end = store.today_bounds(datetime(2026, 8, 28, 13, 0, tzinfo=timezone.utc))
    assert start == datetime(2026, 8, 28, tzinfo=timezone.utc)
    assert end == datetime(2026, 8, 29, tzinfo=timezone.utc)


def test_naive_timestamps_are_never_mixed_with_aware_ones(tmp_path):
    """Comparing naive and aware ISO strings silently drops rows."""
    store = Store(tmp_path / "db.sqlite3", timezone_name="Europe/Prague")
    start, end = store.today_bounds(datetime(2026, 8, 28, 13, 0))  # naive input
    assert start.tzinfo is not None and end.tzinfo is not None


def test_unknown_timezone_is_a_config_error(base_env):
    with pytest.raises(ConfigError):
        Settings.from_env(dict(base_env, AWV_TIMEZONE="Mars/Olympus_Mons"))


# ---------------------------------------------------------- double counting
def test_duplicate_observations_are_not_counted_twice(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    ts = datetime(2026, 8, 28, 9, 0, tzinfo=timezone.utc)
    first = observation(store, ts, State.CLINICAL_WORK)
    second = observation(store, ts, State.CLINICAL_WORK)
    assert second == first, "the same instant must not produce a second row"
    assert store.count_observations("r1") == 1


def test_a_worker_restart_does_not_double_count(tmp_path):
    path = tmp_path / "db.sqlite3"
    start = datetime(2026, 8, 28, 9, 0, tzinfo=timezone.utc)
    store = Store(path)
    for step in range(4):
        observation(store, start + timedelta(seconds=10 * step), State.CLINICAL_WORK)
    before = store.metrics("r1", start - timedelta(hours=1), start + timedelta(hours=1))

    # A restart: a brand new Store over the same file, replaying what it has.
    restarted = Store(path)
    for step in range(4):
        observation(restarted, start + timedelta(seconds=10 * step), State.CLINICAL_WORK)
    after = restarted.metrics("r1", start - timedelta(hours=1), start + timedelta(hours=1))

    assert after["samples"] == before["samples"] == 4
    assert after["clinical_seconds"] == before["clinical_seconds"] == 30.0


def test_a_restart_gap_is_an_unavailability_window_not_occupancy(tmp_path):
    """The service was down for ten minutes. That is not ten minutes of work."""
    store = Store(tmp_path / "db.sqlite3")
    start = datetime(2026, 8, 28, 9, 0, tzinfo=timezone.utc)
    observation(store, start, State.CLINICAL_WORK)
    observation(store, start + timedelta(seconds=20), State.CLINICAL_WORK)
    observation(store, start + timedelta(minutes=10), State.CLINICAL_WORK)
    observation(store, start + timedelta(minutes=10, seconds=20), State.CLINICAL_WORK)

    metrics = store.metrics("r1", start - timedelta(hours=1), start + timedelta(hours=1))
    assert metrics["clinical_seconds"] == 40.0
    assert metrics["skipped_gaps"] == 1
    assert metrics["unavailable_seconds"] == pytest.approx(580.0)


# ------------------------------------------------------ weighted aggregation
def test_monitored_seconds_and_both_utilisation_denominators(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    start = datetime(2026, 8, 28, 0, 0, tzinfo=timezone.utc)
    observation(store, start, State.CLINICAL_WORK)
    observation(store, start + timedelta(seconds=60), State.EMPTY)
    observation(store, start + timedelta(seconds=120), State.EMPTY)

    metrics = store.metrics("r1", start, start + timedelta(days=1))
    assert metrics["monitored_seconds"] == 120.0
    assert metrics["clinical_seconds"] == 60.0
    # Half of what was actually observed, a rounding blip of the whole day.
    assert metrics["utilisation_of_monitored"] == pytest.approx(0.5)
    assert metrics["utilisation_of_window"] == pytest.approx(60.0 / 86400.0, abs=1e-4)


def test_missed_frames_do_not_inflate_the_previous_state(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    start = datetime(2026, 8, 28, 9, 0, tzinfo=timezone.utc)
    observation(store, start, State.CLINICAL_WORK)
    # A three minute hole (over MAX_GAP_SECONDS) then a new sample.
    observation(store, start + timedelta(minutes=3), State.EMPTY)
    metrics = store.metrics("r1", start - timedelta(hours=1), start + timedelta(hours=1))
    assert metrics["clinical_seconds"] == 0.0
    assert metrics["monitored_seconds"] == 0.0
    assert metrics["skipped_gaps"] == 1


def test_out_of_order_timestamps_never_produce_negative_time(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    start = datetime(2026, 8, 28, 9, 0, tzinfo=timezone.utc)
    observation(store, start + timedelta(seconds=30), State.CLINICAL_WORK)
    observation(store, start, State.EMPTY)
    metrics = store.metrics("r1", start - timedelta(hours=1), start + timedelta(hours=1))
    assert all(value >= 0 for value in metrics["seconds_by_state"].values())
    assert metrics["monitored_seconds"] >= 0


async def test_service_metrics_expose_monitored_today(settings):
    from ai_webcam_vision.runtime.service import VisionService
    from ai_webcam_vision.transport.mock import SyntheticFrameSource, SyntheticScene

    source = SyntheticFrameSource(scene=SyntheticScene(room_activity=True))
    service = VisionService(settings, source=source)
    try:
        service.baseline.save(await source.grab())
        await service.sample_once()
        metrics = service.metrics()
        assert "monitored_today" in metrics
        assert metrics["today"]["room_id"] == settings.room_id
        assert metrics["today"]["timezone"] == "UTC"
    finally:
        await service.aclose()
