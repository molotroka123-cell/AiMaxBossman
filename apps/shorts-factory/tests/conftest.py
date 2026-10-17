import json
import pathlib
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from shortsfactory.api import create_app  # noqa: E402

TOK = {"viewer": "tv", "editor": "te", "owner": "to"}


def H(role):
    return {"Authorization": f"Bearer {TOK[role]}"}


def make_video(path: pathlib.Path, w: int, h: int, dur: float, freq: int = 440) -> pathlib.Path:
    """Tiny synthetic test video (ffmpeg lavfi): moving test pattern + tone with a loud middle part."""
    if path.exists():
        return path
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate=25:duration={dur}",
                    "-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=48000:duration={dur}",
                    "-af", f"volume='if(between(t,{dur/3:.1f},{dur/2:.1f}),1.0,0.25)':eval=frame",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
                   check=True, capture_output=True)
    return path


def fake_transcript(duration: float, lang: str = "en") -> dict:
    """Phrases of ~4 s with word timestamps; some phrases are hooks (questions)."""
    phrases = ["Why does nobody talk about this?", "Here is the simple idea behind it.", "We tested it on a home computer.",
               "The result surprised the whole team.", "Imagine running it every night for free.", "That changes the math completely.",
               "But there is one mistake people make.", "They skip the human check at the end.", "So keep a person in the loop.",
               "That is the whole trick, honestly."]
    segs, t, n = [], 0.5, 0
    while t + 3.6 < duration:
        text = phrases[n % len(phrases)]
        words, wt = [], t
        step = 3.4 / len(text.split())
        for w in text.split():
            words.append({"word": " " + w, "start": round(wt, 3), "end": round(wt + step * 0.9, 3), "p": 0.9})
            wt += step
        segs.append({"start": round(t, 3), "end": round(t + 3.4, 3), "text": text, "words": words})
        t += 4.0
        n += 1
    return {"language": lang, "language_probability": 0.99, "duration": duration, "model": "fake", "segments": segs, "engine": "fake"}


def fake_transcriber(path, model="small", cache_dir=None, cache_key=None, **kw):
    from shortsfactory.media import probe
    return fake_transcript(probe(path)["duration"])


class FakeChat:
    def __init__(self, script=None, fail_rate=False):
        self.calls = []
        self.script = script
        self.fail_rate = fail_rate
        self.model = "fake-qwen"

    def __call__(self, messages):
        sys_msg = messages[0]["content"]
        self.calls.append(sys_msg[:40])
        if "rate moments" in sys_msg:
            if self.fail_rate:
                raise RuntimeError("model offline")
            ids = [int(x.split()[0]) for x in messages[1]["content"].split("id=")[1:]]
            return json.dumps({"items": [{"id": i, "hook_score": 9 - (i % 5), "reason": "clear hook"} for i in ids]})
        if "metadata" in sys_msg:
            return json.dumps({"title": "Home PC runs the night shift", "description": "A short look at the idea.",
                               "hashtags": ["#AI", "#automation", "tech"]})
        if "редактор новостных" in sys_msg:
            return json.dumps(self.script or {})
        return "{}"


class FakeYouTube:
    def __init__(self, connected=True, upload_status="OK", stats=None):
        self.uploads = []
        self.connected = connected
        self.upload_status = upload_status
        self.stats = stats

    def status(self, config_dir):
        return {"connected": self.connected, "reason": "OK" if self.connected else "not connected", "channel": {"title": "T"} if self.connected else None}

    def upload(self, config_dir, path, *, title, description, tags, privacy, publish_at=None, contains_synthetic_media=False, dry_run=False):
        call = dict(path=str(path), title=title, description=description, tags=tags, privacy=privacy, publish_at=publish_at,
                    contains_synthetic_media=contains_synthetic_media, dry_run=dry_run)
        if dry_run:
            return {"status": "DRY_RUN", "request": call}
        self.uploads.append(call)
        if not self.connected:
            return {"status": "NOT_CONNECTED", "reason": "not connected"}
        return {"status": self.upload_status, "video_id": "vid123", "url": "https://www.youtube.com/shorts/vid123"}

    def channel_stats(self, config_dir):
        return self.stats or {"status": "NOT_CONNECTED"}


class Env:
    def __init__(self, tmp_path, *, chat=None, youtube=None, trends=None, image_fn=None, tts=None, gdelt=None):
        self.chat = chat or FakeChat()
        self.yt = youtube or FakeYouTube()
        self.app = create_app(tmp_path / "data", tokens=TOK, chat=self.chat, youtube_mod=self.yt, trends_mod=trends or False,
                              transcriber=fake_transcriber, image_fn=image_fn, tts=tts, gdelt_get_text=gdelt)
        self.c = TestClient(self.app)
        self.store = self.app.state.store
        self.tmp = tmp_path

    def req(self, method, path, role="owner", **kw):
        return self.c.request(method, "/api/v1" + path, headers=H(role) if role else {}, **kw)

    def register(self, path=None, kind="own", ai=False, **extra):
        body = {"title": "test source", "kind": kind, "path": str(path) if path else None, "rights_note": "собственная запись владельца",
                "ai_generated": ai, **extra}
        r = self.req("POST", "/sources", "editor", json=body)
        assert r.status_code == 200, r.text
        return r.json()["source"]

    def approve_rights(self, sid):
        r = self.req("POST", f"/sources/{sid}/rights", "owner", json={"approve": True, "confirm": "ДА", "note": "моя запись"})
        assert r.status_code == 200, r.text
        return r.json()["source"]

    def wait(self, jid, timeout=240):
        t0 = time.time()
        while time.time() - t0 < timeout:
            j = self.req("GET", f"/jobs/{jid}", "viewer").json()
            if j["status"] not in ("queued", "running"):
                return j
            time.sleep(0.3)
        raise TimeoutError(jid)


@pytest.fixture(scope="session")
def videos(tmp_path_factory):
    d = tmp_path_factory.mktemp("vids")
    return {"land": make_video(d / "land_1920x1080.mp4", 1920, 1080, 40),
            "small": make_video(d / "small_640x360.mp4", 640, 360, 20, 330),
            "vert": make_video(d / "vert_1080x1920.mp4", 1080, 1920, 20, 550)}


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)
