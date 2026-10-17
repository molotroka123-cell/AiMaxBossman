"""News/explainer pipeline offline: GDELT rate limit + cache, executable fact checks, end-to-end original short."""
import json
import subprocess

import pytest

from shortsfactory import media, news
from shortsfactory.store import Store
from conftest import Env, FakeChat

ARTS = {"articles": [
    {"url": "https://a.example/1", "title": "Центробанк сохранил ключевую ставку на уровне 16%", "domain": "a.example", "seendate": "20261017T080000Z"},
    {"url": "https://b.example/2", "title": "ЦБ сохранил ключевую ставку 16% годовых", "domain": "b.example", "seendate": "20261017T081000Z"},
    {"url": "https://c.example/3", "title": "В Праге открылась выставка роботов", "domain": "c.example", "seendate": "20261017T070000Z"},
]}
GOOD = {"headline": "ЦБ сохранил ставку", "outlets": ["a.example", "b.example"], "image_prompt": "abstract bank building illustration",
        "lines": ["Центробанк сохранил ключевую ставку на уровне 16 процентов годовых, сообщают a.example и b.example.",
                  "Решение означает, что стоимость кредитов в ближайшее время, по данным изданий, не изменится за счёт ставки.",
                  "Других подробностей в заголовках пока не сообщается, поэтому мы не делаем выводов о причинах решения.",
                  "Следите за официальными сообщениями регулятора и проверяйте первоисточники, ссылки на них есть в описании."]}


def text_ok(url, params):
    return 200, json.dumps(ARTS)


def test_gdelt_rate_limit_backoff_and_cache():
    calls, sleeps, clock = [], [], {"t": 1000.0}

    def get_text(url, params):
        calls.append(params)
        return (200, "Please limit requests to one every 5 seconds") if len(calls) == 1 else (200, json.dumps(ARTS))

    def sleep(s):
        sleeps.append(s)
        clock["t"] += s
    news._last_call["t"] = 0.0
    st = Store()
    r = news.gdelt_fetch("ставка sourcelang:russian", get_text=get_text, store=st, sleep=sleep, clock=lambda: clock["t"])
    assert r["status"] == "OK" and len(r["articles"]) == 3 and len(calls) == 2
    assert any(s >= news.MIN_INTERVAL_S for s in sleeps)
    r2 = news.gdelt_fetch("ставка sourcelang:russian", get_text=get_text, store=st, sleep=sleep, clock=lambda: clock["t"])
    assert r2.get("cached") and len(calls) == 2                                   # served from cache, no new request
    with pytest.raises(news.RateLimited):
        news.gdelt_fetch("другое", get_text=lambda u, p: (429, "slow down"), store=st, sleep=sleep, clock=lambda: clock["t"], retries=1)


def test_grouping_prefers_story_covered_by_more_outlets():
    g = news.group_stories([dict(a) for a in ARTS["articles"]])
    assert g[0]["outlets"] == 2 and set(g[0]["domains"]) == {"a.example", "b.example"}


def test_fact_check_flags_invented_numbers_outlets_and_quotes():
    story = news.group_stories([dict(a) for a in ARTS["articles"]])[0]
    assert news.fact_check(GOOD, story) == []
    bad = dict(GOOD, lines=GOOD["lines"][:-1] + ["По словам главы, «ставка вырастет до 25%» уже в 2027 году и это точно."],
               outlets=["unknown.example"])
    probs = news.fact_check(bad, story)
    assert any("25" in p for p in probs) and any("2027" in p for p in probs)
    assert any("издания" in p for p in probs) and any("цитата" in p for p in probs)


def test_model_that_keeps_inventing_is_refused():
    story = news.group_stories([dict(a) for a in ARTS["articles"]])[0]
    liar = dict(GOOD, lines=GOOD["lines"] + ["Ставка вырастет до 30% в марте."])
    with pytest.raises(news.FactCheckError):
        news.write_script(story, FakeChat(script=liar))


def fake_tts(lines, work):
    out = []
    for n, line in enumerate(lines):
        wav = work / f"line{n}.wav"
        d = round(0.065 * len(line), 2)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=300:duration={d}", "-ar", "22050", str(wav)], check=True)
        out.append({"text": line, "wav": str(wav), "dur": media.probe(wav)["duration"]})
    return out


def test_news_short_end_to_end_is_original_disclosed_and_queued_ready(tmp_path):
    news._last_call["t"] = 0.0
    env = Env(tmp_path, chat=FakeChat(script=GOOD), tts=fake_tts, image_fn=None, gdelt=text_ok)
    src = env.register(None, kind="generated", ai=True, title="news", rights_note="оригинальный текст и озвучка; факты из заголовков GDELT")
    r = env.req("POST", "/jobs/news", "editor", json={"source_id": src["id"], "query": "ставка sourcelang:russian", "max_images": 0})
    assert r.status_code == 403                                                    # negative control: rights first
    env.approve_rights(src["id"])
    r = env.req("POST", "/jobs/news", "editor", json={"source_id": src["id"], "query": "ставка sourcelang:russian", "max_images": 0})
    j = env.wait(r.json()["id"])
    assert j["status"] == "completed", j
    it = env.store.shorts()[0]
    assert it["state"] == "ready" and it["kind"] == "news"
    v = it["validation"]["ffprobe"]
    assert (v["width"], v["height"]) == (1080, 1920) and v["duration"] <= 60 and v["has_audio"]
    m = it["metadata"]
    assert m["contains_synthetic_media"] is True and "a.example" in m["description"] and "https://a.example/1" in m["description"]
    assert it["framing"]["mode"] == "text_cards" and it["story"]["domains"] == ["a.example", "b.example"]
    assert env.yt.uploads == []


def test_image_cap_is_two(tmp_path):
    calls = []

    def img(prompt, out):
        calls.append(prompt)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=864x1536", "-frames:v", "1", str(out)], check=True)
        return {"status": "OK", "file": str(out)}
    news._last_call["t"] = 0.0
    env = Env(tmp_path, chat=FakeChat(script=GOOD), tts=fake_tts, image_fn=img, gdelt=text_ok)
    src = env.register(None, kind="generated", ai=True, title="news", rights_note="оригинальный текст")
    env.approve_rights(src["id"])
    it = news.make_news_short(env.store, src["id"], env.app.state.data_dir, query="q", chat=env.chat, get_text=text_ok,
                              image_fn=img, max_images=9, tts=fake_tts)
    assert len(calls) == 2 and it["framing"]["mode"] == "images" and it["validation"]["ok"], it["validation"]
    assert it["validation"]["ffprobe"]["width"] == 1080


RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title>ЦБ сохранил ключевую ставку 16%</title><link>https://a.example/r1</link><description>&lt;p&gt;Решение принято в пятницу&lt;/p&gt;</description></item>
<item><title>Центробанк сохранил ключевую ставку на уровне 16%</title><link>https://www.b.example/r2</link><description>Регулятор</description></item>
<item><title>Футбол: итоги тура</title><link>https://c.example/r3</link><description>спорт</description></item>
</channel></rss>"""


def test_rss_fallback_when_gdelt_fails_is_used_and_reported(tmp_path):
    news._last_call["t"] = 0.0
    seen = []

    def rss_get(url, params):
        seen.append(url)
        return 200, RSS
    env = Env(tmp_path, chat=FakeChat(script=GOOD), tts=fake_tts, image_fn=None, gdelt=lambda u, p: (500, "down"))
    src = env.register(None, kind="generated", ai=True, title="news", rights_note="оригинальный текст")
    env.approve_rights(src["id"])
    it = news.make_news_short(env.store, src["id"], env.app.state.data_dir, query="q", chat=env.chat, get_text=lambda u, p: (500, "down"),
                              image_fn=None, tts=fake_tts, rss_keywords="ставк", rss_get_text=rss_get)
    assert seen and it["state"] == "ready"
    st = it["story"]
    assert st["feed"].startswith("rss:") and "GDELT HTTP 500" in st["fallback_reason"]
    assert st["domains"] == ["a.example", "b.example"] and all("ставк" in a["title"].lower() for a in st["articles"])
    assert "GDELT" not in it["metadata"]["description"] and "https://a.example/r1" in it["metadata"]["description"]


def test_gdelt_failure_without_fallback_is_an_honest_error(tmp_path):
    news._last_call["t"] = 0.0
    env = Env(tmp_path, chat=FakeChat(script=GOOD), tts=fake_tts, image_fn=None)
    src = env.register(None, kind="generated", ai=True, title="news", rights_note="оригинальный текст")
    env.approve_rights(src["id"])
    with pytest.raises(news.RateLimited):
        news.make_news_short(env.store, src["id"], env.app.state.data_dir, query="q", chat=env.chat, get_text=lambda u, p: (500, "down"),
                             image_fn=None, tts=fake_tts)
    assert env.store.shorts() == []
