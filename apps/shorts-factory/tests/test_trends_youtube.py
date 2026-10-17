"""Offline tests for shortsfactory.trends and shortsfactory.youtube (fakes only, no network)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shortsfactory import trends, youtube  # noqa: E402

KEY = "AIzaFAKEKEY_0123456789abcdef"


def _raw(vid, title, dur, views="1000", pub="2026-10-10T10:00:00Z", likes="10"):
    st = {"viewCount": views}
    if likes is not None:
        st["likeCount"] = likes
    return {"id": vid, "snippet": {"title": title, "channelTitle": "Ch", "publishedAt": pub, "tags": ["a", "b"],
                                   "categoryId": "24"},
            "statistics": st, "contentDetails": {"duration": dur}}


# ------------------------------------------------------------------ fetch_trends

def test_fetch_without_key_is_not_connected_with_steps_and_no_network():
    calls = []
    res = trends.fetch_trends(lambda u, p: calls.append(1), api_key=None)
    assert res["status"] == "NOT_CONNECTED" and res["items"] == [] and not calls
    text = " ".join(res["steps"])
    assert "youtube.googleapis.com" in text and "apis/credentials" in text and "YOUTUBE_API_KEY" in text
    assert res["fetched_at"] and res["source"]


def test_fetch_parses_items_and_sends_correct_params():
    seen = {}

    def get(url, params):
        seen.update(params, url=url)
        return {"items": [_raw("v1", "Short one", "PT45S"), _raw("v2", "Long", "PT12M3S", likes=None),
                          _raw("v3", "Three-min short", "PT2M59S")]}

    res = trends.fetch_trends(get, api_key=KEY, region="cz", max_results=3)
    assert res["status"] == "OK" and len(res["items"]) == 3
    assert seen["url"] == trends.API_URL and seen["chart"] == "mostPopular" and seen["regionCode"] == "CZ"
    assert seen["part"] == "snippet,statistics,contentDetails" and seen["maxResults"] == 3
    a, b, c = res["items"]
    assert set(a) == {"video_id", "title", "channel", "views", "likes", "published_at", "duration_s", "tags",
                      "category_id", "is_short"}
    assert (a["duration_s"], a["is_short"], a["views"]) == (45, True, 1000)
    assert (b["duration_s"], b["is_short"], b["likes"]) == (723, False, None)
    assert c["is_short"] is True
    assert KEY not in json.dumps(res)


def test_fetch_paginates_up_to_max_results():
    pages = iter([{"items": [_raw(f"a{i}", "t", "PT30S") for i in range(50)], "nextPageToken": "N"},
                  {"items": [_raw(f"b{i}", "t", "PT30S") for i in range(50)]}])
    tokens = []
    res = trends.fetch_trends(lambda u, p: (tokens.append(p.get("pageToken")), next(pages))[1],
                              api_key=KEY, max_results=70)
    assert res["status"] == "OK" and len(res["items"]) == 70 and tokens == [None, "N"]
    assert res["quota_units"] == 2


def test_fetch_api_error_and_exception_are_error_without_key_leak():
    err = {"error": {"code": 403, "message": f"bad key={KEY}", "errors": [{"reason": "quotaExceeded"}]}}
    res = trends.fetch_trends(lambda u, p: err, api_key=KEY)
    assert res["status"] == "ERROR" and "quotaExceeded" in res["reason"] and KEY not in json.dumps(res)

    def boom(u, p):
        raise ConnectionError(f"failed GET ...?key={KEY}")
    res = trends.fetch_trends(boom, api_key=KEY)
    assert res["status"] == "ERROR" and KEY not in json.dumps(res)


def test_fetch_rejects_bad_region():
    assert trends.fetch_trends(lambda u, p: {}, api_key=KEY, region="Czechia")["status"] == "ERROR"


@pytest.mark.parametrize("value,expected", [
    ("PT45S", 45), ("PT1M", 60), ("PT1M5S", 65), ("PT3M", 180), ("PT1H2M3S", 3723), ("P1DT1S", 86401),
    ("P0D", 0), ("PT0.6S", 1), ("pt10s", 10), ("", None), (None, None), ("garbage", None), ("P", None),
    ("PT", None), ("1:05", None)])
def test_parse_duration(value, expected):
    assert trends.parse_duration(value) == expected


def test_short_rule_three_minutes_since_2024_10_15():
    assert trends.is_short(180, "2026-01-01T00:00:00Z") is True
    assert trends.is_short(181, "2026-01-01T00:00:00Z") is False
    assert trends.is_short(90, "2024-10-14T00:00:00Z") is False      # old 60 s rule before the change
    assert trends.is_short(60, "2023-05-01T00:00:00Z") is True
    assert trends.is_short(0, None) is False and trends.is_short(None, None) is False


# ------------------------------------------------------------------ analyze_trends

ITEMS = [{"title": "Call me +420 777 123 456 or a@b.cz", "tags": ["x"] * 20, "duration_s": 30, "views": 5,
          "likes": 1, "channel": "SecretChannel", "video_id": "v1", "is_short": True}]


def test_analyze_not_connected_and_empty():
    assert trends.analyze_trends(ITEMS, None)["status"] == "NOT_CONNECTED"
    assert trends.analyze_trends([], lambda m: "{}")["status"] == "ERROR"


def test_analyze_sends_only_redacted_compact_summary_and_parses_json():
    sent = []

    def chat(messages):
        sent.append(messages)
        return "```json\n" + json.dumps({"topics": ["food hacks"], "hooks": ["Did you know..."],
                                         "formats": ["list of 3"], "notes": "ok"}) + "\n```"

    res = trends.analyze_trends(ITEMS, chat)
    assert res["status"] == "OK" and res["topics"] == ["food hacks"] and res["formats"] == ["list of 3"]
    payload = json.loads(sent[0][1]["content"])
    assert set(payload[0]) == {"title", "tags", "duration_s", "views", "is_short"}
    blob = sent[0][1]["content"]
    assert "777" not in blob and "a@b.cz" not in blob and "SecretChannel" not in blob and "v1" not in blob
    assert len(payload[0]["tags"]) == 8
    assert "never predict" in sent[0][0]["content"].lower()


@pytest.mark.parametrize("reply", ["not json at all", "{broken", "[1,2]", json.dumps({"topics": [], "hooks": []}), ""])
def test_analyze_garbage_is_error(reply):
    res = trends.analyze_trends(ITEMS, lambda m: reply)
    assert res["status"] == "ERROR" and res["topics"] == []


def test_analyze_salvages_truncated_reply():
    # real failure seen LIVE 17.10: a free model stopped mid-JSON (no closing brace)
    reply = '{"topics":["food \\"cheap vs expensive\\"","pets"],"hooks":["POV: first day"],"formats":["Ochutnávací srov'
    res = trends.analyze_trends(ITEMS, lambda m: reply)
    assert res["status"] == "OK" and res["topics"] == ['food "cheap vs expensive"', "pets"]
    assert res["hooks"] == ["POV: first day"] and res["formats"] == [] and "обрезан" in res["notes"]


def test_analyze_budget_or_guard_errors_are_error():
    class BudgetExceeded(Exception):
        pass

    def chat(m):
        raise BudgetExceeded("daily free budget used")
    res = trends.analyze_trends(ITEMS, chat)
    assert res["status"] == "ERROR" and "BudgetExceeded" in res["notes"]
    res = trends.analyze_trends(ITEMS, lambda m: {"status": "BUDGET_EXCEEDED", "reason": "cap"})
    assert res["status"] == "ERROR" and "BUDGET_EXCEEDED" in res["notes"]


def test_analyze_drops_view_predictions():
    reply = json.dumps({"topics": ["This will get 1M views guaranteed", "street food"], "hooks": ["POV:"],
                        "formats": ["прогноз: 500 тыс просмотров", "duet"], "notes": "we predict 2M views"})
    res = trends.analyze_trends(ITEMS, lambda m: reply)
    assert res["status"] == "OK" and res["topics"] == ["street food"] and res["formats"] == ["duet"]
    assert "2M" not in res["notes"] and "не прогноз" in res["notes"]


# ------------------------------------------------------------------ youtube

def test_status_and_stats_not_connected_without_token(tmp_path):
    st = youtube.status(tmp_path)
    assert st["connected"] is False and "connect" in st["reason"]
    assert youtube.channel_stats(tmp_path)["status"] == "NOT_CONNECTED"


def test_upload_not_connected_without_token(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"\0" * 10)
    res = youtube.upload(tmp_path, f, title="t", description="d", tags=["x"])
    assert res["status"] == "NOT_CONNECTED" and res["request"]["body"]["status"]["privacyStatus"] == "private"


def test_connect_missing_client_secrets(tmp_path):
    assert youtube.connect(tmp_path, tmp_path / "nope.json")["connected"] is False


def test_upload_dry_run_body_fields(tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\0" * 1234)
    res = youtube.upload(tmp_path, f, title="Prague in 30s", description="desc", tags=["prague", "#shorts"],
                         privacy="private", publish_at="2099-01-01T10:00:00Z", contains_synthetic_media=True,
                         dry_run=True)
    assert res["status"] == "DRY_RUN" and "video_id" not in res
    req = res["request"]
    st = req["body"]["status"]
    assert st == {"privacyStatus": "private", "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True,
                  "publishAt": "2099-01-01T10:00:00Z"}
    assert req["body"]["snippet"]["title"] == "Prague in 30s" and req["body"]["snippet"]["tags"] == ["prague", "#shorts"]
    assert "uploadType=resumable" in req["url"] and req["media"]["resumable"] is True
    assert req["media"]["size_bytes"] == 1234 and req["media"]["mime"] == "video/mp4"


def test_upload_dry_run_defaults(tmp_path):
    res = youtube.upload(tmp_path, tmp_path / "x.mp4", title="t", description="", tags=[], dry_run=True)
    st = res["request"]["body"]["status"]
    assert st["privacyStatus"] == "private" and st["selfDeclaredMadeForKids"] is False
    assert st["containsSyntheticMedia"] is False and "publishAt" not in st


@pytest.mark.parametrize("kw,needle", [
    ({"privacy": "public", "publish_at": "2099-01-01T10:00:00Z"}, "requires privacy='private'"),
    ({"privacy": "friends"}, "privacy must be"),
    ({"publish_at": "2001-01-01T10:00:00Z"}, "future"),
    ({"publish_at": "tomorrow"}, "ISO 8601"),
    ({"title": "x" * 101}, "100"),
    ({"title": "<b>"}, "< or >"),
])
def test_upload_validation(tmp_path, kw, needle):
    args = {"title": "t", "description": "d", "tags": [], "dry_run": True, **kw}
    res = youtube.upload(tmp_path, tmp_path / "x.mp4", **args)
    assert res["status"] == "ERROR" and needle in res["reason"]


class _Exec:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return self.data


class _FakeService:
    def __init__(self):
        self.inserted = None

    def channels(self):
        svc = self

        class C:
            def list(self, **kw):
                assert kw.get("mine") is True
                return _Exec({"items": [{"id": "UC1", "snippet": {"title": "My"},
                                         "statistics": {"subscriberCount": "12", "viewCount": "3400", "videoCount": "3"},
                                         "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]})
        return C()

    def playlistItems(self):
        class P:
            def list(self, **kw):
                assert kw["playlistId"] == "UU1"
                return _Exec({"items": [{"contentDetails": {"videoId": "s1"}}, {"contentDetails": {"videoId": "l1"}}]})
        return P()

    def videos(self):
        svc = self

        class V:
            def list(self, **kw):
                return _Exec({"items": [
                    {"id": "s1", "snippet": {"title": "S"}, "contentDetails": {"duration": "PT40S"},
                     "statistics": {"viewCount": "77", "likeCount": "5"}, "status": {"privacyStatus": "public"}},
                    {"id": "l1", "snippet": {"title": "L"}, "contentDetails": {"duration": "PT10M"},
                     "statistics": {"viewCount": "9"}, "status": {"privacyStatus": "public"}}]})

            def insert(self, **kw):
                svc.inserted = kw

                class I:
                    def next_chunk(self):
                        return None, {"id": "NEWID", "status": {"privacyStatus": "private", "uploadStatus": "uploaded"}}
                return I()
        return V()


def test_channel_stats_values_from_api(tmp_path, monkeypatch):
    monkeypatch.setattr(youtube, "_build_service", lambda cd: _FakeService())
    res = youtube.channel_stats(tmp_path)
    assert res["status"] == "OK" and res["subscribers"] == 12 and res["total_views"] == 3400
    assert [v["video_id"] for v in res["recent_shorts"]] == ["s1"] and res["recent_shorts"][0]["views"] == 77
    st = youtube.status(tmp_path)
    assert st["connected"] is True and st["channel"] == {"id": "UC1", "title": "My", "subscribers": 12,
                                                         "views": 3400, "videos": 3}


def test_upload_real_path_with_fake_service(tmp_path, monkeypatch):
    svc = _FakeService()
    monkeypatch.setattr(youtube, "_build_service", lambda cd: svc)
    f = tmp_path / "c.mp4"
    f.write_bytes(b"\0" * 100)
    res = youtube.upload(tmp_path, f, title="t", description="d", tags=["a"], privacy="public",
                         contains_synthetic_media=True)
    assert res["status"] == "OK" and res["video_id"] == "NEWID" and res["url"].endswith("/shorts/NEWID")
    assert svc.inserted["part"] == "snippet,status" and svc.inserted["body"]["status"]["containsSyntheticMedia"] is True
    assert "warning" in res and "private" in res["warning"]       # YouTube stored private != requested public


SECRET_REFRESH = "1//0gFAKE-REFRESH-TOKEN-should-never-appear"
SECRET_ACCESS = "ya29.FAKE-ACCESS-TOKEN-should-never-appear"


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows-only")
def test_connect_stores_token_dpapi_and_never_prints_it(tmp_path, monkeypatch, capsys):
    class Creds:
        refresh_token = SECRET_REFRESH

        def to_json(self):
            return json.dumps({"refresh_token": SECRET_REFRESH, "token": SECRET_ACCESS, "client_id": "cid",
                               "client_secret": "GOCSPX-fake"})

    class Flow:
        def run_local_server(self, **kw):
            assert kw["access_type"] == "offline"
            return Creds()

    secrets = tmp_path / "client_secret.json"
    secrets.write_text("{}")
    monkeypatch.setattr(youtube, "_flow", lambda p: Flow())
    monkeypatch.setattr(youtube, "_build_service", lambda cd: _FakeService())
    res = youtube.connect(tmp_path, secrets)
    out = capsys.readouterr()
    assert res["connected"] is True
    raw = (tmp_path / youtube.TOKEN_NAME).read_bytes()
    for secret in (SECRET_REFRESH, SECRET_ACCESS, "GOCSPX-fake"):
        assert secret not in json.dumps(res) and secret not in out.out and secret not in out.err
        assert secret.encode() not in raw
    assert youtube.load_token(tmp_path)["refresh_token"] == SECRET_REFRESH       # round-trips via DPAPI


def test_errors_are_redacted(tmp_path, monkeypatch):
    def boom(cd):
        raise RuntimeError(f'invalid_grant refresh_token="{SECRET_REFRESH}" access {SECRET_ACCESS}')
    monkeypatch.setattr(youtube, "_build_service", boom)
    for res in (youtube.status(tmp_path), youtube.channel_stats(tmp_path)):
        assert SECRET_REFRESH not in json.dumps(res) and SECRET_ACCESS not in json.dumps(res)
