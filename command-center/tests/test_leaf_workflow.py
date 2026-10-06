"""authored_by_lane (opsplug): bcc.features.workflow - the read-only mission canvas.

Pure helpers + the real HTTP endpoints on an empty database (the populated-graph behaviour is covered by the
existing tests/test_feat_workflow.py, which the probe runs together with this file).
"""
from datetime import datetime, timedelta

from bcc.features import workflow


def test_ms_is_a_non_negative_millisecond_difference_or_none():
    a = datetime(2026, 10, 6, 12, 0, 0)
    assert workflow._ms(a, a + timedelta(seconds=1.5)) == 1500
    assert workflow._ms(a + timedelta(seconds=5), a) == 0            # a clock going backwards never gives a negative span
    assert workflow._ms(None, a) is None and workflow._ms(a, None) is None


def test_short_collapses_whitespace_and_truncates_with_an_ellipsis():
    assert workflow._short("  a \n b\t c  ") == "a b c"
    out = workflow._short("x" * 100, 10)
    assert len(out) == 10 and out.endswith("…")
    assert workflow._short(None) == ""


def test_status_vocabularies_cover_the_engine_states():
    assert workflow._TASK_STATUS["completed"] == "success" and workflow._TASK_STATUS["waiting_approval"] == "waiting"
    assert workflow._MISSION_STATUS["cancelled"] == "stopped" and workflow._MISSION_STATUS["planning"] == "pending"


def test_only_read_verbs_are_exposed():
    methods = {m for r in workflow.router.routes for m in r.methods}
    assert methods <= {"GET", "HEAD"}
    assert {r.path for r in workflow.router.routes} == {
        "/workflow/missions", "/workflow/missions/{mission_id}", "/workflow/missions/{mission_id}/log"}


async def test_empty_database_gives_an_empty_list_and_404_for_an_unknown_mission(env):
    assert (await env.client.get("/api/workflow/missions")).json() == []
    assert (await env.client.get("/api/workflow/missions/12345")).status_code == 404
    assert (await env.client.get("/api/workflow/missions/12345/log")).json() == []
