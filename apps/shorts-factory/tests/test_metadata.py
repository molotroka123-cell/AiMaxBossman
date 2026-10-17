"""Metadata: limits, #shorts, no clickbait, YouTube synthetic-media disclosure flag."""
from shortsfactory import metadata


def test_ai_generated_source_sets_disclosure_flag_and_text():
    m = metadata.generate("Some text.", chat=lambda msgs: '{"title":"T","description":"D","hashtags":["#ai"]}', lang="ru",
                          synthetic=metadata.needs_disclosure({"ai_generated": True}))
    assert m["contains_synthetic_media"] is True and metadata.DISCLOSURE["ru"] in m["description"]


def test_own_real_recording_is_not_flagged():
    assert metadata.needs_disclosure({"ai_generated": False}) is False
    assert metadata.needs_disclosure({"ai_generated": False}, {"realistic_synthetic": True}) is True
    m = metadata.normalize({"title": "x"}, lang="en", synthetic=False, fallback_text="x")
    assert m["contains_synthetic_media"] is False and metadata.DISCLOSURE["en"] not in m["description"]


def test_title_limits_shorts_tag_and_clickbait():
    raw = {"title": "<b>" + "word " * 40 + "</b> 100% guaranteed", "hashtags": ["AI", "#ai", "#Tech!", "x" * 50, "a", "b2", "c3", "d4", "e5"]}
    m = metadata.normalize(raw, lang="en", synthetic=False, fallback_text="fallback")
    assert len(m["title"]) <= 100 and "<" not in m["title"] and "100%" not in m["title"]
    assert m["hashtags"][0] == "#shorts" and len(m["hashtags"]) <= metadata.MAX_TAGS
    assert len({h.lower() for h in m["hashtags"]}) == len(m["hashtags"])
    assert all(len(h) <= 31 for h in m["hashtags"]) and "shorts" in m["tags"]


def test_llm_failure_falls_back_and_is_reported():
    def boom(msgs):
        raise RuntimeError("offline")
    m = metadata.generate("First sentence here. Second.", chat=boom, lang="en", synthetic=False)
    assert m["llm_status"] == "ERROR" and m["title"] == "First sentence here"


def test_edit_cannot_switch_disclosure_off(env, videos):
    src = env.register(videos["land"], ai=True)
    env.approve_rights(src["id"])
    j = env.wait(env.req("POST", "/jobs/process", "editor", json={"source_id": src["id"], "k": 1}).json()["id"])
    assert j["status"] == "completed", j.get("error")
    it = env.store.shorts()[0]
    assert it["metadata"]["contains_synthetic_media"] is True
    r = env.req("PATCH", f"/queue/{it['id']}/metadata", "editor", json={"title": "New title", "description": "plain"})
    assert r.status_code == 200
    m = r.json()["metadata"]
    assert m["title"] == "New title" and m["contains_synthetic_media"] is True and metadata.DISCLOSURE["en"] in m["description"]
