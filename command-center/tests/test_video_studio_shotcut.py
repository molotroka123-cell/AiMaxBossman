"""Actual MLT render and checked interchange boundaries, never arbitrary XML execution."""
from copy import deepcopy
import os
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import pytest
from bcc.video_studio.commands import apply_command
from bcc.video_studio.model import TICKS, StudioError, new_project
from bcc.video_studio.media import MediaLibrary, binary, process
from bcc.video_studio.render import verify_output
from bcc.video_studio.shotcut import export_shotcut

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                                  reason="real FFmpeg binaries required")


async def fixture_project(tmp_path):
    p=new_project("shotcut","Два кадра & music")
    seq=p["sequences"][0];seq.update(width=320,height=180)
    for n,color in enumerate(("red","blue")):
        source=tmp_path/(color+".mp4")
        await process([binary("ffmpeg"),"-hide_banner","-loglevel","error","-nostdin","-y",
            "-f","lavfi","-i",f"color={color}:s=320x180:r=25:d=1",
            "-f","lavfi","-i",f"sine=frequency={440+n*220}:sample_rate=48000:duration=1",
            "-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac","-shortest",source])
        media=await MediaLibrary(tmp_path).import_file(source);p["media"][media["id"]]=media
        p=apply_command(p,{"type":"clip.add","track_id":seq["tracks"][0]["id"],"clip":{
            "id":color,"media_id":media["id"],"start":n*TICKS,"source_in":0,"source_out":TICKS}})[0]
    return p


@needs_ffmpeg
async def test_native_xml_references_only_verified_owned_media(tmp_path):
    p=await fixture_project(tmp_path);before=deepcopy(p)
    output=export_shotcut(p,tmp_path)
    tree=ET.fromstring(output["data"])
    assert output["frames"]==50 and not output["parity_claim"]
    assert len(tree.findall(".//chain"))==2
    assert len(tree.findall(".//playlist/entry"))==3
    assert p==before
    media=next(iter(p["media"].values()));MediaLibrary(tmp_path).resolve(media).write_bytes(b"tampered")
    with pytest.raises(ValueError,match="hash mismatch"):export_shotcut(p,tmp_path)


@needs_ffmpeg
async def test_effect_is_rejected_instead_of_silently_lost(tmp_path):
    p=await fixture_project(tmp_path)
    p=apply_command(p,{"type":"clip.reverse","clip_id":"red"})[0]
    with pytest.raises(StudioError,match="rendered master"):export_shotcut(p,tmp_path)


@needs_ffmpeg
async def test_actual_shotcut_mlt_render_has_both_scenes_and_audio(tmp_path):
    melt=os.environ.get("BOSSMAN_VIDEO_MELT") or shutil.which("melt")
    if not melt or not Path(melt).is_file():pytest.skip("install Shotcut portable and configure BOSSMAN_VIDEO_MELT")
    p=await fixture_project(tmp_path)
    out=export_shotcut(p,tmp_path);mlt=tmp_path/"timeline.mlt";mlt.write_bytes(out["data"])
    rendered=tmp_path/"shotcut-render.mp4"
    await process([melt,mlt,"-consumer",f"avformat:{rendered}","vcodec=libx264","acodec=aac",
                   "crf=18","pix_fmt=yuv420p","ar=48000","ac=2","real_time=-2","threads=2"],timeout=60)
    proof=await verify_output(rendered,{"width":320,"height":180,"duration":2,"has_audio":True})
    assert proof["passed"] and proof["decoded"] and proof["has_audio"]
    for at,dominant in ((.25,0),(1.25,2)):
        raw,_=await process([binary("ffmpeg"),"-hide_banner","-loglevel","error","-ss",at,"-i",rendered,
                            "-frames:v","1","-vf","scale=1:1","-pix_fmt","rgb24","-f","rawvideo","pipe:1"],binary_output=True)
        assert raw[dominant]>180 and raw[1]<50, (at,list(raw))


@needs_ffmpeg
async def test_actual_shotcut_multitrack_compositing_and_static_gain(tmp_path):
    melt=os.environ.get("BOSSMAN_VIDEO_MELT") or shutil.which("melt")
    if not melt or not Path(melt).is_file():pytest.skip("Shotcut/MLT executable required")
    p=await fixture_project(tmp_path)
    p=apply_command(p,{"type":"track.add","kind":"video","id":"upper"})[0]
    blue=next(c for c in p["sequences"][0]["tracks"][0]["clips"] if c["id"]=="blue")
    p=apply_command(p,{"type":"clip.add","track_id":"upper","clip":{
        "id":"overlay","media_id":blue["media_id"],"start":250000,"source_in":0,"source_out":500000,
        "volume":.5}})[0]
    mlt=tmp_path/"layered.mlt";mlt.write_bytes(export_shotcut(p,tmp_path)["data"])
    target=tmp_path/"layered.mp4"
    await process([melt,mlt,"-consumer",f"avformat:{target}","vcodec=libx264","acodec=aac",
                   "pix_fmt=yuv420p","ar=48000","ac=2","real_time=-2","threads=2"],timeout=60)
    assert (await verify_output(target,{"duration":2,"has_audio":True}))["passed"]
    raw,_=await process([binary("ffmpeg"),"-hide_banner","-loglevel","error","-ss",.5,"-i",target,
                         "-frames:v","1","-vf","scale=1:1","-pix_fmt","rgb24","-f","rawvideo","pipe:1"],binary_output=True)
    assert raw[2]>180 and raw[0]<50, list(raw)
