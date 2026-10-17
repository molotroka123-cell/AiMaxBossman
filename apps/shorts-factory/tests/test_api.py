"""API surface: health, auth, channel goal progress only from channel_stats, trends wiring, UI."""
from conftest import Env, FakeYouTube


class FakeTrends:
    def __init__(self):
        self.calls = []

    def fetch_trends(self, get, *, api_key, region, max_results):
        self.calls.append((api_key, region, max_results))
        if not api_key:
            return {"status": "NOT_CONNECTED", "items": [], "steps": ["set YOUTUBE_API_KEY"]}
        return {"status": "OK", "items": [{"video_id": "a", "title": "t", "channel": "c", "views": 5, "likes": 1, "published_at": "2026-10-16T00:00:00Z",
                                           "duration_s": 30, "tags": [], "category_id": "24"}]}

    def analyze_trends(self, items, chat):
        return {"status": "OK", "topics": ["x"], "hooks": [], "formats": [], "notes": ""}


def test_health_open_everything_else_needs_token(env):
    assert env.c.get("/api/v1/health").json()["status"] == "ok"
    assert env.c.get("/api/v1/sources").status_code == 401
    assert env.req("GET", "/sources", "viewer").status_code == 200
    assert env.req("POST", "/sources", "viewer", json={}).status_code in (403, 422)


def test_goal_shows_no_data_without_channel(env):
    g = env.req("GET", "/channel", "viewer").json()
    assert g["goal"]["subscribers"] == "нет данных" and g["goal"]["watch_hours_365d"] == "нет данных"


def test_goal_uses_only_channel_stats_numbers(tmp_path):
    stats = {"status": "OK", "subscribers": 250, "total_views": 99999,
             "recent_shorts": [{"published_at": "2026-10-10T00:00:00Z", "views": 1200}, {"published_at": "2020-01-01T00:00:00Z", "views": 9}]}
    env = Env(tmp_path, youtube=FakeYouTube(stats=stats))
    g = env.req("GET", "/channel", "viewer").json()["goal"]
    assert g["subscribers"] == 250 and g["subscribers_pct"] == 25.0
    assert g["shorts_views_90d"] == 1200 and "нижняя оценка" in g["shorts_views_90d_note"]
    assert g["watch_hours_365d"] == "нет данных"


def test_trends_not_connected_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    t = FakeTrends()
    env = Env(tmp_path, trends=t)
    r = env.req("POST", "/trends/fetch", "editor", json={"region": "CZ"}).json()
    assert r["status"] == "NOT_CONNECTED" and t.calls == [(None, "CZ", 50)]
    monkeypatch.setenv("YOUTUBE_API_KEY", "k")
    r = env.req("POST", "/trends/fetch", "editor", json={"region": "RU", "max_results": 10}).json()
    assert r["status"] == "OK" and r["analysis"]["status"] == "OK"
    assert env.req("GET", "/trends", "viewer").json()["region"] == "RU"


def test_trends_module_absent_is_not_connected(env):
    assert env.req("POST", "/trends/fetch", "editor", json={}).json()["status"] == "NOT_CONNECTED"


def test_ui_served(env):
    r = env.c.get("/ui/index.html")
    assert r.status_code == 200 and "Shorts Factory" in r.text


def test_schedule_owner_only(env):
    assert env.req("PUT", "/schedule", "editor", json={"per_day": 2, "times": ["10:00", "18:00"]}).status_code == 403
    assert env.req("PUT", "/schedule", "owner", json={"per_day": 2, "times": ["25:00"]}).status_code == 422
    assert env.req("PUT", "/schedule", "owner", json={"per_day": 2, "times": ["10:00", "18:00"]}).status_code == 200
    assert env.req("GET", "/schedule", "viewer").json()["config"]["per_day"] == 2
