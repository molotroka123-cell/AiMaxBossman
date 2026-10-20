"""Music Studio contract tests. No real model is claimed in CI."""
from __future__ import annotations

import pytest
from bcc.features import music_studio as music


def test_phonk_presets_are_generic_genres_not_artist_clones():
    import asyncio
    data=asyncio.run(music.presets())
    ids={x["id"] for x in data["items"]}
    assert {"phonk","drift-phonk","ultrafunk","nightcore","electro"} <= ids
    assert all("instrumental" in x["prompt"] or x["id"] in {"ultrafunk","nightcore","electro"}
               for x in data["items"])


def test_provider_is_local_loopback_only(monkeypatch):
    monkeypatch.setattr(music.socket,"getaddrinfo",lambda *a,**k:[
        (music.socket.AF_INET,music.socket.SOCK_STREAM,6,"",("127.0.0.1",8001))])
    music._loopback_only("http://localhost:8001")
    monkeypatch.setattr(music.socket,"getaddrinfo",lambda *a,**k:[
        (music.socket.AF_INET,music.socket.SOCK_STREAM,6,"",("8.8.8.8",8001))])
    with pytest.raises(ValueError,match="loopback"):
        music._loopback_only("http://evil.example:8001")


@pytest.mark.asyncio
async def test_generate_maps_to_official_acestep_release_task(monkeypatch):
    monkeypatch.setattr(music,"_loopback_only",lambda _url:None)
    seen={}
    async def fake_json(client,method,path,**kwargs):
        seen.update(method=method,path=path,payload=kwargs["json"])
        return {"task_id":"fixture-1","status":"queued"}
    monkeypatch.setattr(music,"_json",fake_json)
    class Bus:
        async def emit(self,*a,**k): pass
    class Svc: bus=Bus()
    class App: state=type("State",(),{"svc":Svc()})()
    class Request: app=App()
    body=music.MusicIn(prompt="dark phonk instrumental",bpm=140,duration=60)
    out=await music.generate(body,Request())
    assert out["task_id"]=="fixture-1"
    assert seen["path"]=="/release_task"
    assert seen["payload"]["bpm"]==140
    assert seen["payload"]["audio_duration"]==60
    assert seen["payload"]["model"]=="acestep-v15-turbo"


def test_studio_master_filters_and_vocal_cover_chain():
    assert "studio_veo" in music.STUDIO_MASTER_FILTERS
    assert "vocal_cover" in music.STUDIO_MASTER_FILTERS
    assert "loudnorm=I=-14:TP=-1.0:LRA=11" in music.STUDIO_MASTER_FILTERS["studio_veo"]
    assert "loudnorm=I=-14:TP=-1.0:LRA=11[out]" in music.STUDIO_MASTER_FILTERS["vocal_cover"]
    assert "equalizer=f=6800" in music.STUDIO_MASTER_FILTERS["vocal_cover"]
    assert "equalizer=f=2800" in music.STUDIO_MASTER_FILTERS["vocal_cover"]
