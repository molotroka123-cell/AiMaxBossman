from copy import deepcopy
import shutil
import pytest
from bcc.video_studio.model import new_project
from bcc.video_studio.media import MediaLibrary,binary,process
from bcc.video_studio.retrieval import search_project,suggest_broll,find_duplicates

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                                  reason="real FFmpeg binaries required")


def test_search_returns_exact_caption_time_without_visual_claim():
    p=new_project("retrieval","Search")
    p["captions"]=[{"id":"c1","sequence_id":p["active_sequence_id"],"start":2000000,"end":5000000,"text":"Свежие фрукты и яблоки"},
        {"id":"c2","sequence_id":p["active_sequence_id"],"start":5000000,"end":6000000,"text":"Открываем магазин"}]
    result=search_project(p,"Найди момент где яблоки")
    assert result["matches"][0]["caption_id"]=="c1" and result["matches"][0]["start"]==2000000
    assert not result["visual_understanding"] and result["egress"]=="none"
    assert search_project(p,"нет совпадений")["matches"]==[]


async def assets(tmp_path):
    p=new_project("retrieval","Search")
    for n,(color,crf) in enumerate((("red",18),("red",24),("blue",18))):
        source=tmp_path/f"source{n}.mp4"
        await process([binary("ffmpeg"),"-hide_banner","-loglevel","error","-nostdin","-y",
            "-f","lavfi","-i",f"color={color}:s=160x90:r=25:d=1","-c:v","libx264","-crf",crf,source])
        media=await MediaLibrary(tmp_path).import_file(source,name=f"{color}-{n}.mp4")
        media["tags"]=["Fresh","Vibes","fruit"] if color=="red" else ["ocean"]
        p["media"][media["id"]]=media
    return p


@needs_ffmpeg
async def test_broll_only_returns_existing_matching_imported_ids(tmp_path):
    p=await assets(tmp_path)
    out=suggest_broll(p,"Fresh Vibes fruit advertisement")
    assert len(out["matches"])==2 and all(x["media_id"] in p["media"] for x in out["matches"])
    assert suggest_broll(p,"spaceship")["next_action"]


@needs_ffmpeg
async def test_real_duplicate_sampler_distinguishes_reencoded_red_from_blue(tmp_path):
    p=await assets(tmp_path);before=deepcopy(p)
    out=await find_duplicates(p,tmp_path)
    assert len(out["matches"])==1 and out["matches"][0]["kind"]=="sampled_visual_candidate"
    assert all(p["media"][mid]["name"].startswith("red") for mid in out["matches"][0]["media_ids"])
    assert p==before and (await find_duplicates(p,tmp_path))["matches"]==out["matches"]
