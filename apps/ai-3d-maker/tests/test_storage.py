"""Job history, cancellation and disk accounting."""

from __future__ import annotations

import time

import pytest

from ai_3d_maker.errors import DiskQuotaError, JobNotFoundError, UnsafePathError
from ai_3d_maker.storage import CANCELLED, SUCCEEDED, JobStore, StageRecord


@pytest.fixture
def store(tmp_path) -> JobStore:
    return JobStore(tmp_path / "jobs", job_quota_bytes=1024, total_quota_bytes=8192, max_retained=5)


def test_create_and_get(store):
    record = store.create("job1", "design", {"a": 1})
    assert record.id == "job1"
    assert store.get("job1").status == "queued"


def test_unsafe_job_id_is_refused(store):
    with pytest.raises(UnsafePathError):
        store.create("../escape", "design", {})


def test_job_dir_stays_inside_the_jobs_root(store):
    record = store.create("job1", "design", {})
    assert store.jobs_dir.resolve() in store.job_dir(record.id).parents


def test_missing_job_raises(store):
    with pytest.raises(JobNotFoundError):
        store.get("nope")


def test_history_is_ordered_newest_first(store):
    store.create("a", "design", {})
    time.sleep(0.01)
    store.create("b", "design", {})
    ids = [j.id for j in store.list()]
    assert ids[0] == "b"


def test_history_can_be_filtered_by_status(store):
    store.create("a", "design", {})
    store.create("b", "design", {})
    store.update("b", status=SUCCEEDED)
    assert [j.id for j in store.list(status=SUCCEEDED)] == ["b"]


def test_history_survives_a_reload(tmp_path):
    first = JobStore(tmp_path / "jobs", job_quota_bytes=1024, total_quota_bytes=8192)
    first.create("persisted", "design", {"x": 1})
    first.update("persisted", status=SUCCEEDED, result={"printable": True})

    second = JobStore(tmp_path / "jobs", job_quota_bytes=1024, total_quota_bytes=8192)
    record = second.get("persisted")
    assert record.status == SUCCEEDED
    assert record.result == {"printable": True}


def test_stages_are_recorded(store):
    store.create("job1", "design", {})
    store.add_stage("job1", StageRecord("generate", "ok", time.time(), time.time(), {"n": 1}))
    stages = store.get("job1").stages
    assert stages[0]["name"] == "generate"
    assert stages[0]["duration_s"] is not None


# --------------------------------------------------------------- cancelling
def test_cancel_sets_the_event_and_the_flag(store):
    store.create("job1", "design", {})
    store.request_cancel("job1")
    assert store.is_cancelled("job1")
    assert store.get("job1").cancel_requested


def test_cancelling_a_finished_job_is_a_no_op(store):
    store.create("job1", "design", {})
    store.update("job1", status=CANCELLED)
    record = store.request_cancel("job1")
    assert record.status == CANCELLED


# ------------------------------------------------------------------- quota
def test_per_job_quota_is_enforced(store):
    record = store.create("job1", "design", {})
    from pathlib import Path

    (Path(record.directory) / "big.bin").write_bytes(b"x" * 5000)
    with pytest.raises(DiskQuotaError, match="per-job quota"):
        store.check_job_quota("job1")


def test_under_quota_passes(store):
    store.create("job1", "design", {})
    assert store.check_job_quota("job1") < 1024


def test_total_quota_blocks_new_jobs(tmp_path):
    small = JobStore(tmp_path / "jobs", job_quota_bytes=10_000, total_quota_bytes=100)
    record = small.create("job1", "design", {})
    from pathlib import Path

    (Path(record.directory) / "fill.bin").write_bytes(b"x" * 5000)
    with pytest.raises(DiskQuotaError, match="total quota"):
        small.create("job2", "design", {})


def test_metrics_report_usage_and_limits(store):
    store.create("job1", "design", {})
    metrics = store.metrics()
    assert metrics["jobs_total"] == 1
    assert metrics["jobs_by_status"]["queued"] == 1
    assert metrics["disk_quota_bytes"] == 8192


def test_retention_prunes_old_terminal_jobs(store):
    for i in range(8):
        store.create(f"job{i}", "design", {})
        store.update(f"job{i}", status=SUCCEEDED)
    assert len(store.list(limit=100)) <= 5
