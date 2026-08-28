from __future__ import annotations

import gc
import sqlite3
from datetime import datetime, timedelta, timezone

from ai_webcam_vision.crm.base import CrmContext
from ai_webcam_vision.pipeline.analysis import Evidence
from ai_webcam_vision.pipeline.classifier import Classification, State
from ai_webcam_vision.storage import Store


def observation(store: Store, ts: datetime, state: State, room: str = "r1") -> None:
    store.add_observation(
        room_id=room,
        evidence=Evidence(ts=ts, room_change=0.1, chair_change=0.2, work_motion=0.03,
                          motion_gate=True, frame_seq=1),
        crm=CrmContext(available=True, source="mock", is_mock=True, appointment_id="a1"),
        classification=Classification(state=state, confidence=0.9, reasons=["r"]),
        debounced_state=state,
        source_kind="synthetic",
        source_is_mock=True,
    )


def test_schema_is_created_with_version(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    with store.connect() as conn:
        row = conn.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()
    assert row["value"] == "1"


def test_database_and_state_dir_are_owner_only(tmp_path):
    state = tmp_path / "state"
    store = Store(state / "db.sqlite3")
    assert oct(store.path.stat().st_mode)[-3:] == "600"
    assert oct(state.stat().st_mode)[-3:] == "700"


def test_connections_are_closed(tmp_path):
    """The legacy pack leaked one sqlite connection per call."""
    store = Store(tmp_path / "db.sqlite3")
    for _ in range(50):
        store.count_observations()
    gc.collect()
    live = [obj for obj in gc.get_objects() if isinstance(obj, sqlite3.Connection)]
    assert len(live) < 5, f"connections are leaking: {len(live)} alive"


def test_metrics_sum_time_by_state_and_skip_gaps(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    start = datetime(2026, 8, 28, 9, 0, tzinfo=timezone.utc)
    observation(store, start, State.CLINICAL_WORK)
    observation(store, start + timedelta(seconds=30), State.CLINICAL_WORK)
    observation(store, start + timedelta(seconds=60), State.TURNOVER)
    # A five minute hole: not counted as continuous occupancy.
    observation(store, start + timedelta(seconds=360), State.EMPTY)

    metrics = store.metrics("r1", start - timedelta(hours=1), start + timedelta(hours=1))
    assert metrics["samples"] == 4
    assert metrics["skipped_gaps"] == 1
    assert metrics["seconds_by_state"]["CLINICAL_WORK"] == 60.0
    assert metrics["clinical_seconds"] == 60.0
    assert metrics["occupied_seconds"] == 60.0
    assert metrics["counted_intervals"] == 2


def test_latest_observation_records_mock_flags(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    observation(store, datetime.now(timezone.utc), State.PREP)
    latest = store.latest_observation("r1")
    assert latest["source_is_mock"] == 1
    assert latest["crm_is_mock"] == 1
    assert latest["crm_available"] == 1
    assert latest["state"] == "PREP"


def test_jobs_and_artifacts_round_trip(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    created = datetime.now(timezone.utc)
    store.upsert_job({
        "id": "job-1", "type": "sample", "status": "running",
        "created_at": created.isoformat(), "params": {"a": 1},
    })
    store.upsert_job({
        "id": "job-1", "type": "sample", "status": "succeeded",
        "created_at": created.isoformat(), "finished_at": created.isoformat(),
        "params": {"a": 1}, "result": {"ok": True},
    })
    jobs = store.list_jobs()
    assert len(jobs) == 1 and jobs[0]["status"] == "succeeded"

    store.add_artifact(artifact_id="art-1", job_id="job-1", kind="baseline",
                       path="/tmp/x.npy", size_bytes=12, created_at=created, meta={"k": "v"})
    artifacts = store.list_artifacts(job_id="job-1")
    assert artifacts[0]["kind"] == "baseline"
    assert artifacts[0]["meta"] == {"k": "v"}
    assert store.list_artifacts(job_id="other") == []


def test_job_error_text_is_scrubbed(tmp_path):
    from ai_webcam_vision.secretstore import register_secret_value

    register_secret_value("StoredJobErrorProbe")
    store = Store(tmp_path / "db.sqlite3")
    store.upsert_job({
        "id": "job-2", "type": "sample", "status": "failed",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "params": {}, "error": "failed for rtsp://u:StoredJobErrorProbe@h/s",
    })
    assert "StoredJobErrorProbe" not in store.list_jobs()[0]["error"]
