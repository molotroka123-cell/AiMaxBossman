"""End-to-end (offline) for tools/youtube_learning_fetch.py: fake yt-dlp runner + fake SponsorBlock/RYD."""
from __future__ import annotations

import hashlib
import json

from bcc.learning_video.fetch_control import Breaker, FetchController
from tools import youtube_learning_fetch as tool

GOOD, POOR, NEW = "AAAAAAAAAA1", "BBBBBBBBBB2", "CCCCCCCCCC3"
LINES = [
    "Резервная копия это отдельная копия данных на другом носителе.",
    "Промокод BOSS это скидка 20 процентов, сначала перейдите по ссылке в описании.",
    "Правило три два один означает три копии на двух носителях и одну копию в другом месте.",
    "Никогда не храните пароль от архива рядом с самим архивом это грубая ошибка.",
    "Шифрование нужно включать до отправки копии в облако а не после, это важно.",
]


def vtt():
    out = ["WEBVTT", ""]
    for i, s in enumerate(LINES):
        out += ["00:00:%02d.000 --> 00:00:%02d.000" % (i * 5, i * 5 + 5), s, ""]
    return "\n".join(out)


def http_get(url, timeout):
    if "votes" in url:
        likes = {GOOD: (950, 50), POOR: (300, 700)}.get(url.rsplit("=", 1)[1])
        if not likes:
            return 404, b""
        vid = url.rsplit("=", 1)[1]
        return 200, json.dumps({"id": vid, "likes": likes[0], "dislikes": likes[1]}).encode()
    prefix = hashlib.sha256(GOOD.encode()).hexdigest()[:4]
    if "/skipSegments/" + prefix in url:
        return 200, json.dumps([{"videoID": GOOD, "segments": [
            {"segment": [5, 10], "category": "sponsor", "actionType": "skip", "votes": 2}]}]).encode()
    return 404, b""


def make_runner(order):
    def run(argv, timeout):
        url = argv[-1]
        order.append(url[-11:])
        if url.endswith(POOR):
            return 1, "ERROR: [youtube] x: Private video. Sign in if you've been granted access"
        out = argv[argv.index("-o") + 1].replace("%(id)s", url[-11:]).replace("%(language)s", "ru").replace("%(ext)s", "vtt")
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(vtt())
        return 0, ""
    return run


def test_ranked_fetch_strip_and_honest_verdicts(tmp_path):
    order = []
    ctrl = FetchController(ytdlp="yt-dlp", runner=make_runner(order), breaker=Breaker(path=tmp_path / "b.json"))
    urls = ["https://youtu.be/" + v for v in (NEW, POOR, GOOD)]
    rep = tool.run(urls, tmp_path, lang="ru", signals=True, controller=ctrl, http_get=http_get)
    assert order == [GOOD, POOR, NEW]  # best prior first, unknown prior last
    by = {r["video_id"]: r for r in rep["results"]}
    assert by[POOR]["fetch"]["verdict"] == "UNAVAILABLE" and "pipeline" not in by[POOR]
    assert by[GOOD]["pipeline"]["sponsor_strip"]["status"] == "STRIPPED"
    assert by[NEW]["pipeline"]["sponsor_strip"]["status"] == "NOT_REQUESTED"
    assert by[NEW]["prior"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert rep["verdict"] == "PARTIAL" and rep["with_transcript"] == 2
    stored = [json.loads(x) for x in (tmp_path / "claims.jsonl").read_text(encoding="utf-8").splitlines()]
    good_claims = [c for c in stored if c["source_id"].startswith(GOOD)]
    assert good_claims and not any("промокод" in c["statement"].lower() for c in good_claims)
    assert all(c["status"] == "UNVERIFIED" and c["source_kind"] == "auto_subs" for c in stored)


def test_no_signals_mode_makes_no_http(tmp_path):
    calls = []
    ctrl = FetchController(ytdlp="yt-dlp", runner=make_runner([]), breaker=Breaker())
    rep = tool.run(["https://youtu.be/" + GOOD], tmp_path, lang="ru", signals=False, controller=ctrl,
                   http_get=lambda u, t: calls.append(u) or (500, b""))
    assert calls == [] and rep["verdict"] == "OK"


def test_bad_url_is_refused_before_binary_lookup(capsys, monkeypatch):
    monkeypatch.setattr(tool.shutil, "which", lambda name: "/bin/yt-dlp")
    assert tool.main(["https://evil.example/watch?v=" + GOOD, "--out", "x"]) == 3
    assert "only public youtube" in capsys.readouterr().out


def test_missing_ytdlp_is_blocked_not_crash(capsys, monkeypatch):
    monkeypatch.setattr(tool.shutil, "which", lambda name: None)
    assert tool.main(["https://youtu.be/" + GOOD, "--out", "x"]) == 3
    assert "yt-dlp not found" in capsys.readouterr().out
