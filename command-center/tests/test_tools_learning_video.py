"""`learning.youtube` through the real Bossman app (env fixture): registry, permission policy, STOP, store.
Synthetic subtitles; yt-dlp and SponsorBlock/RYD are replaced by recording fakes. Live proof: owner PC (docs/audits)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from bcc.features import tools_computer
from bcc.features import tools_learning_video as lv
from bcc.learning_video.fetch_control import Breaker, FetchController
from bcc.tools import REGISTRY, decide_effect

VID = "Lesson-0001"  # 11 chars, with a dash on purpose (store_status must not split it)
VTT = "\n".join(["WEBVTT", ""] + sum(([
    "00:00:%02d.000 --> 00:00:%02d.000" % (i * 5, i * 5 + 5), s, ""] for i, s in enumerate([
        "Резервная копия это отдельная копия данных на другом носителе.",
        "Промокод BOSS это скидка 20 процентов, сначала перейдите по ссылке в описании.",
        "Правило три два один означает три копии на двух носителях и одну копию в другом месте.",
        "Никогда не храните пароль от архива рядом с самим архивом это грубая ошибка.",
        "Шифрование нужно включать до отправки копии в облако а не после, это важно.",
    ])), []))


def _http(url, timeout):
    if "votes" in url:
        return 200, json.dumps({"id": VID, "likes": 900, "dislikes": 100}).encode()
    if "/skipSegments/" + hashlib.sha256(VID.encode()).hexdigest()[:4] in url:
        return 200, json.dumps([{"videoID": VID, "segments": [
            {"segment": [5, 10], "category": "sponsor", "actionType": "skip", "votes": 3}]}]).encode()
    return 404, b""


def _install(env, calls, outcome=(0, "")):
    def runner(argv, timeout):
        calls.append(argv)
        rc, err = outcome
        if rc == 0:
            o = argv[argv.index("-o") + 1]
            Path(o.replace("%(id)s", VID).replace("%(language)s", "ru").replace("%(ext)s", "vtt")).write_text(
                VTT, encoding="utf-8")
        return rc, err

    def factory(out):
        return FetchController(ytdlp="yt-dlp", runner=runner, breaker=Breaker(path=out / "fetch_breaker.json")), _http
    env.svc._learning_video_controller = factory


def _ctx(env):
    return SimpleNamespace(svc=env.svc, agent={"id": 41}, task={"id": 1})


async def test_tools_are_registered(env):
    assert lv.INGEST_TOOL in REGISTRY.names() and lv.STATUS_TOOL in REGISTRY.names()


def test_grant_makes_it_auto_otherwise_ask():
    spec = lv.SPECS[0]
    assert decide_effect(spec, {}, {"permissions": {}})[0] == "ask"
    assert decide_effect(spec, {}, {"permissions": {"browser.read": True}})[0] == "auto"
    assert decide_effect(lv.SPECS[1], {}, {"permissions": {}})[0] == "auto"  # read-only status


async def test_ingest_end_to_end_strips_ad_and_reports(env, monkeypatch):
    monkeypatch.setattr(lv.shutil, "which", lambda name: "/usr/bin/yt-dlp")
    calls = []
    _install(env, calls)
    res = await lv._t_ingest({"urls": ["https://youtu.be/%s?si=tracking" % VID]}, _ctx(env))
    assert not res.error and res.external and res.data["verdict"] == "OK"
    row = res.data["results"][0]
    assert row["fetch"] == "OK" and row["sponsor"] == "STRIPPED" and row["claims"] > 0
    assert calls[0][-1] == "https://www.youtube.com/watch?v=" + VID  # canonical URL, tracking param dropped
    assert "UNVERIFIED" in res.content
    stored = (lv.store_dir(env.svc) / "claims.jsonl").read_text(encoding="utf-8")
    assert "промокод" not in stored.lower()
    st = await lv._t_status({}, _ctx(env))
    assert st.data["claims_by_video"] == {VID: row["claims"]} and st.data["breaker"]["open"] is False


async def test_bot_wall_is_an_error_not_success(env, monkeypatch):
    monkeypatch.setattr(lv.shutil, "which", lambda name: "/usr/bin/yt-dlp")
    calls = []
    _install(env, calls, outcome=(1, "ERROR: Sign in to confirm you're not a bot"))
    res = await lv._t_ingest({"urls": ["https://www.youtube.com/watch?v=" + VID], "no_signals": True}, _ctx(env))
    assert res.error and res.data["results"][0]["fetch"] == "BLOCKED_BOT_CHECK"
    assert len(calls) == 5 and not any("cookies" in " ".join(a) for a in calls)


async def test_owner_stop_blocks_before_anything_runs(env, monkeypatch):
    monkeypatch.setattr(lv.shutil, "which", lambda name: "/usr/bin/yt-dlp")
    calls = []
    _install(env, calls)
    stop = Path(env.svc.settings.data_dir) / "computer" / tools_computer.STOP_FILE
    stop.parent.mkdir(parents=True, exist_ok=True)
    stop.write_text("stop", encoding="utf-8")
    try:
        res = await lv._t_ingest({"urls": ["https://youtu.be/" + VID]}, _ctx(env))
    finally:
        stop.unlink()
    assert res.error and "STOP" in res.content and calls == []


async def test_bad_input_refused_without_side_effects(env, monkeypatch):
    monkeypatch.setattr(lv.shutil, "which", lambda name: "/usr/bin/yt-dlp")
    calls = []
    _install(env, calls)
    for args in ({"urls": []}, {"urls": ["https://evil.example/watch?v=" + VID]}, {"urls": ["file:///etc/passwd"]},
                 {"urls": ["https://youtu.be/" + VID] * 11}, {"urls": ["https://youtu.be/" + VID], "lang": "de"}):
        assert (await lv._t_ingest(args, _ctx(env))).error
    assert calls == []


async def test_missing_ytdlp_is_blocked(env, monkeypatch):
    monkeypatch.setattr(lv.shutil, "which", lambda name: None)
    env.svc._learning_video_controller = None
    res = await lv._t_ingest({"urls": ["https://youtu.be/" + VID]}, _ctx(env))
    assert res.error and "yt-dlp" in res.content
