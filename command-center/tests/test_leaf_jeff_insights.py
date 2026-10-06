"""authored_by_lane (opsplug): bcc.features.jeff_insights - owner-only Jeff overview API.

Route contract + input validation on the real app; the data collection itself is covered by the existing
tests/test_jeff_2_insights.py (run together with this file by the probe).
"""
from bcc.features import jeff_insights


def test_the_documented_endpoints_exist_and_only_the_digest_send_mutates():
    routes = {(r.path, m) for r in jeff_insights.router.routes for m in r.methods if m not in ("HEAD",)}
    assert routes == {
        ("/jeff-insights/overview", "GET"), ("/jeff-insights/participants", "GET"),
        ("/jeff-insights/narratives", "GET"), ("/jeff-insights/narratives/{person_key}", "GET"),
        ("/jeff-insights/trends", "GET"), ("/jeff-insights/digest", "GET"), ("/jeff-insights/digest/send", "POST"),
    }
    assert jeff_insights.FEATURE.name == "jeff_insights"


async def test_a_narrative_is_addressed_by_the_64_hex_person_key_never_a_telegram_id(env):
    for bad in ("123456789", "short", "g" * 64, "A" * 63):
        r = await env.client.get(f"/api/jeff-insights/narratives/{bad}")
        assert r.status_code == 422, (bad, r.status_code)
        assert "64 hex" in r.text
    unknown = await env.client.get("/api/jeff-insights/narratives/" + "a" * 64)
    assert unknown.status_code == 404                       # valid key, no narrative yet: honest 404, not an empty 200


async def test_overview_reports_whether_jeff_is_configured_and_never_leaks_paths_or_ids(env):
    r = await env.client.get("/api/jeff-insights/overview")
    assert r.status_code == 200
    body = r.json()
    assert "configured" in body and "digest_week" in body
    assert "telegram_id" not in r.text and ":\\" not in r.text
