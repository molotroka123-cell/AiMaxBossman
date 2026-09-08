from pathlib import Path

from social_farm.generation.higgsfield_browser_contracts import BrowserGenerationState
from social_farm.generation.job_store import GenerationJobStore


def test_store_create_is_idempotent_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "jobs.json"
    store = GenerationJobStore(path)

    first = store.create(job_id="job-1", mission_id="mission-1", provider="higgsfield")
    second = store.create(job_id="job-1", mission_id="other", provider="other")

    assert first.job_id == second.job_id == "job-1"
    assert second.mission_id == "mission-1"
    assert second.provider == "higgsfield"
    assert path.exists()

    reloaded = GenerationJobStore(path).get("job-1")
    assert reloaded is not None
    assert reloaded.state is BrowserGenerationState.CREATED


def test_store_resumable_excludes_terminal_jobs(tmp_path: Path) -> None:
    store = GenerationJobStore(tmp_path / "jobs.json")
    active = store.create(job_id="active", mission_id="m", provider="h")
    complete = store.create(job_id="done", mission_id="m", provider="h")
    complete.state = BrowserGenerationState.COMPLETE
    store.save(complete)

    assert [record.job_id for record in store.resumable()] == [active.job_id]


def test_store_fails_closed_on_corrupt_state(tmp_path: Path) -> None:
    path = tmp_path / "jobs.json"
    path.write_text("not-json", encoding="utf-8")

    store = GenerationJobStore(path)
    try:
        store.list()
    except RuntimeError as exc:
        assert "unreadable" in str(exc)
    else:
        raise AssertionError("corrupt durable state must not be silently ignored")
