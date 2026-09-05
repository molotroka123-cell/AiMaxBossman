"""Real synthetic TEST fixtures: actual decode/render, no model-generated media."""
import asyncio
from copy import deepcopy
from pathlib import Path
import shutil

import pytest

from bcc.video_studio.media import MediaLibrary, binary, process
from bcc.video_studio.render import render_project, verify_output
from bcc.video_studio.analysis import analyse_media, parse_captions, export_captions, transcribe, track_object

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),reason="real FFmpeg binaries required")


async def fixture_media(tmp_path, color="red", duration=1):
    path=tmp_path/(color+".mp4")
    await process([binary("ffmpeg"),"-hide_banner","-loglevel","error","-nostdin","-y",
        "-f","lavfi","-i",f"color=c={color}:size=320x180:rate=25:duration={duration}",
        "-f","lavfi","-i",f"sine=frequency=440:sample_rate=48000:duration={duration}",
        "-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac","-shortest",str(path)])
    return await MediaLibrary(tmp_path).import_file(path)


def document(media):
    return {"id":"p1","revision":1,"schema_version":1,"timebase":1_000_000,"active_sequence_id":"s1",
        "media":{media["id"]:media},"captions":[],"sequences":[{"id":"s1","width":320,"height":180,
            "fps":{"num":25,"den":1},"sample_rate":48000,"tracks":[{"id":"v1","kind":"video","clips":[
                {"id":"c1","media_id":media["id"],"start":0,"source_in":0,"source_out":1_000_000,
                 "speed":{"num":1,"den":1},"transform":{},"effects":[],"keyframes":{}}]}]}]}


@pytest.mark.asyncio
async def test_real_render_preview_export_and_probe(tmp_path):
    media=await fixture_media(tmp_path)
    project=document(media)
    progress=[]
    async def update(stage,details):progress.append(stage)
    result=await render_project(project,tmp_path,tmp_path/"export.mp4",progress=update)
    assert result["verification"]["passed"] and result["verification"]["decoded"]
    assert result["verification"]["has_audio"]
    preview=await render_project(project,tmp_path,tmp_path/"preview.mp4",{"width":160,"height":90})
    assert preview["verification"]["width"]==160
    assert "rendering" in progress and progress[-1]=="complete"
    assert not (await verify_output(tmp_path/"export.mp4",{"width":999}))["passed"]
    assert not (await verify_output(tmp_path/"export.mp4",{"sha256":"0"*64}))["passed"]


@pytest.mark.asyncio
async def test_prepare_real_cache_and_content_integrity(tmp_path):
    media=await fixture_media(tmp_path)
    library=MediaLibrary(tmp_path)
    artifacts=await library.prepare(media)
    assert set(artifacts)=={"thumbnail","proxy","waveform"}
    assert all((tmp_path/p).stat().st_size for p in artifacts.values())
    before={k:(tmp_path/p).stat().st_mtime_ns for k,p in artifacts.items()}
    assert await library.prepare(media)==artifacts
    assert before=={k:(tmp_path/p).stat().st_mtime_ns for k,p in artifacts.items()}
    library.resolve(media).write_bytes(b"tampered")
    with pytest.raises(ValueError,match="hash mismatch"):library.resolve(media)


@pytest.mark.asyncio
async def test_playlist_rejected_before_demux(tmp_path):
    source=tmp_path/"evil.mp4"
    source.write_text("#EXTM3U\nhttp://127.0.0.1/private\n")
    with pytest.raises(ValueError,match="unsafe media container"):
        await MediaLibrary(tmp_path).import_file(source)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode",["speed","reverse","freeze","ramp","nested","title","captions","keyframes","mix"])
async def test_temporal_multitrack_and_text_real(mode,tmp_path):
    media=await fixture_media(tmp_path)
    project=document(media);seq=project["sequences"][0];clip=seq["tracks"][0]["clips"][0]
    if mode=="speed":clip["speed"]={"num":2,"den":1}
    if mode=="reverse":clip["reverse"]=True
    if mode=="freeze":clip.update(freeze=True,freeze_duration=700_000)
    if mode=="ramp":clip["speed_ramp"]=[{"source_in":0,"source_out":500_000,"speed":{"num":2,"den":1}},{"source_in":500_000,"source_out":1_000_000,"speed":{"num":1,"den":1}}]
    if mode=="nested":
        child=deepcopy(seq);child["id"]="child";project["sequences"].append(child)
        clip.pop("media_id");clip["nested_sequence_id"]="child"
    if mode=="title":clip.update(title={"text":"Тест {safe} \\N caption"},freeze_duration=1_000_000)
    if mode=="captions":project["captions"]=[{"start":0,"end":900_000,"text":"Привет, мир","sequence_id":"s1"}]
    if mode=="keyframes":clip["keyframes"]={"x":[{"t":0,"value":-100},{"t":900_000,"value":100,"easing":"ease_out"}],"opacity":[{"t":0,"value":.1},{"t":900_000,"value":1}]}
    if mode=="mix":
        other=deepcopy(clip);other.update(id="c2",start=300_000);other["transform"]={"scale":.5,"x":20}
        seq["tracks"].append({"id":"v2","kind":"video","volume":.2,"clips":[other]})
    result=await render_project(project,tmp_path,tmp_path/(mode+"-result.mp4"))
    assert result["verification"]["passed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("effect",[
    {"type":"color","params":{"brightness":.1,"saturation":.5}},
    {"type":"mask","params":{"shape":"circle","radius":.3}},
    {"type":"chroma","params":{"color":"FF0000"}},
    {"type":"curves","params":{"master":[[0,0],[.5,.7],[1,1]]}},
    {"type":"fade_in","params":{"duration":.3}},
    {"type":"stabilize","params":{}},
    {"type":"denoise","params":{}},
    {"type":"equalizer","params":{"frequency":1000,"gain":3}},
    {"type":"compressor","params":{}},
    {"type":"loudnorm","params":{}},
])
async def test_effects_real_decode(effect,tmp_path):
    media=await fixture_media(tmp_path);project=document(media)
    project["sequences"][0]["tracks"][0]["clips"][0]["effects"]=[effect]
    assert (await render_project(project,tmp_path,tmp_path/"effect.mp4"))["verification"]["passed"]


@pytest.mark.asyncio
async def test_unknown_effect_and_output_escape_fail(tmp_path):
    project=document(await fixture_media(tmp_path))
    with pytest.raises(ValueError,match="owned output"):
        await render_project(project,tmp_path,tmp_path.parent/"escape.mp4")
    project["sequences"][0]["tracks"][0]["clips"][0]["effects"]=[{"type":"movie=http://bad"}]
    with pytest.raises(ValueError,match="unsupported video effect"):
        await render_project(project,tmp_path,tmp_path/"no.mp4")
    assert not (tmp_path/"no.mp4").exists()


@pytest.mark.asyncio
async def test_process_cancellation_reaps_child():
    event=asyncio.Event()
    async def progress(stage,details):event.set()
    task=asyncio.create_task(process([binary("ffmpeg"),"-hide_banner","-loglevel","error","-re","-f","lavfi","-i","testsrc2=size=320x180:rate=25","-progress","pipe:1","-f","null","-"],progress=progress))
    await asyncio.wait_for(event.wait(),10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):await asyncio.wait_for(task,5)


async def rgb(path,time=.3):
    data,_=await process([binary("ffmpeg"),"-hide_banner","-loglevel","error","-nostdin","-ss",str(time),"-i",str(path),"-frames:v","1","-vf","scale=1:1","-pix_fmt","rgb24","-f","rawvideo","pipe:1"])
    assert len(data)==3
    return tuple(data)


@pytest.mark.asyncio
async def test_pixel_evidence_concat_range_preview_equivalent(tmp_path):
    red=await fixture_media(tmp_path,"red");blue=await fixture_media(tmp_path,"blue")
    project=document(red);project["media"][blue["id"]]=blue
    second=deepcopy(project["sequences"][0]["tracks"][0]["clips"][0]);second.update(id="blue",start=1_000_000,media_id=blue["id"])
    project["sequences"][0]["tracks"][0]["clips"].append(second)
    await render_project(project,tmp_path,tmp_path/"concat.mp4")
    first=await rgb(tmp_path/"concat.mp4",.3);last=await rgb(tmp_path/"concat.mp4",1.3)
    assert first[0]>200 and first[2]<20
    assert last[2]>200 and last[0]<20
    await render_project(project,tmp_path,tmp_path/"range.mp4",{"range":{"start":1_100_000,"end":1_800_000},"width":160,"height":90})
    ranged=await rgb(tmp_path/"range.mp4")
    assert all(abs(a-b)<5 for a,b in zip(last,ranged))


@pytest.mark.asyncio
async def test_actual_silence_black_diagnostics_and_ducking(tmp_path):
    media=await fixture_media(tmp_path,"black");project=document(media)
    track=project["sequences"][0]["tracks"][0]
    track["volume"]=0
    await render_project(project,tmp_path,tmp_path/"silent.mp4")
    result=await analyse_media(tmp_path/"silent.mp4")
    assert result["intervals_complete"]
    assert result["black_intervals"] and result["silence_intervals"]
    track["volume"]=.8
    other=deepcopy(track);other["id"]="music";other["kind"]="audio";other["clips"][0]["id"]="musicclip"
    other["effects"]=[{"type":"ducking","params":{"sidechain_track_id":track["id"]}}]
    project["sequences"][0]["tracks"].append(other)
    assert (await render_project(project,tmp_path,tmp_path/"ducked.mp4"))["verification"]["passed"]


@pytest.mark.parametrize("format",["srt","vtt"])
def test_caption_roundtrip_and_bad_time(format):
    original=[{"start":120_000,"end":999_000,"text":"Привет\nline"}]
    parsed=parse_captions(export_captions(original,format),format)
    assert {k:parsed[0][k] for k in original[0]}==original[0]
    with pytest.raises(ValueError):parse_captions("1\n00:00:70,000 --> 00:00:71,000\nno",format)


@pytest.mark.asyncio
async def test_asr_no_model_does_not_invent_captions(tmp_path):
    with pytest.raises(ValueError,match="ASR BLOCKED"):
        await transcribe(tmp_path/"any.wav")


@pytest.mark.asyncio
async def test_adjustment_interval_changes_lower_composite_only_inside_range(tmp_path):
    media=await fixture_media(tmp_path,"red");project=document(media)
    project["sequences"][0]["tracks"].append({"id":"adjust","kind":"adjustment","clips":[
        {"id":"adjust_clip","adjustment":True,"start":500_000,"freeze_duration":500_000,
         "effects":[{"type":"color","params":{"brightness":.8}}]}]})
    await render_project(project,tmp_path,tmp_path/"adjust.mp4")
    before=await rgb(tmp_path/"adjust.mp4",.2);after=await rgb(tmp_path/"adjust.mp4",.7)
    assert before[1]<20 and after[1]>100


@pytest.mark.asyncio
async def test_detach_audio_is_not_doubled(tmp_path):
    media=await fixture_media(tmp_path);project=document(media)
    original=project["sequences"][0]["tracks"][0]["clips"][0]
    await render_project(project,tmp_path,tmp_path/"before.mp4")
    detached=deepcopy(original);detached.update(id="a1",audio_only=True)
    original["audio_disabled"]=True
    project["sequences"][0]["tracks"].append({"id":"audio","kind":"audio","clips":[detached]})
    await render_project(project,tmp_path,tmp_path/"detached.mp4")
    before=await analyse_media(tmp_path/"before.mp4");after=await analyse_media(tmp_path/"detached.mp4")
    assert abs(before["peak_db"]-after["peak_db"])<.2


@pytest.mark.asyncio
async def test_lut_and_blend_produce_actual_pixels(tmp_path):
    media=await fixture_media(tmp_path,"red");project=document(media)
    cube="LUT_3D_SIZE 2\n"+"\n".join(f"{1-r} {1-g} {1-b}" for b in [0,1] for g in [0,1] for r in [0,1])+"\n"
    project["sequences"][0]["tracks"][0]["clips"][0]["effects"]=[{"type":"lut3d","params":{"cube_text":cube}}]
    await render_project(project,tmp_path,tmp_path/"lut.mp4")
    pixel=await rgb(tmp_path/"lut.mp4")
    assert pixel[0]<30 and pixel[1]>200 and pixel[2]>200
    project["sequences"][0]["tracks"][0]["clips"][0]["effects"]=[{"type":"blend","params":{"mode":"screen"}}]
    await render_project(project,tmp_path,tmp_path/"blend.mp4")
    assert (await rgb(tmp_path/"blend.mp4"))[0]>200


@pytest.mark.asyncio
async def test_stream_copy_compatible_actual_and_reject_processing(tmp_path):
    red=await fixture_media(tmp_path,"red");blue=await fixture_media(tmp_path,"blue")
    project=document(red);project["media"][blue["id"]]=blue
    first=project["sequences"][0]["tracks"][0]["clips"][0]
    first["source_out"]=red["duration_ticks"]
    second=deepcopy(first);second.update(id="second",media_id=blue["id"],start=red["duration_ticks"],source_out=blue["duration_ticks"])
    project["sequences"][0]["tracks"][0]["clips"].append(second)
    result=await render_project(project,tmp_path,tmp_path/"copy.mp4",{"mode":"stream_copy"})
    assert result["profile"]["video_codec"]=="copy"
    assert (await rgb(tmp_path/"copy.mp4",.3))[0]>200
    assert (await rgb(tmp_path/"copy.mp4",1.3))[2]>200
    first["transform"]={"scale":.5}
    with pytest.raises(ValueError,match="unmodified"):
        await render_project(project,tmp_path,tmp_path/"invalid-copy.mp4",{"mode":"stream_copy"})


@pytest.mark.asyncio
@pytest.mark.skipif(not Path('C:/Python314/python.exe').is_file(),reason="optional host CV interpreter unavailable")
async def test_local_optical_flow_actual_moving_region(tmp_path):
    path=tmp_path/"tracked.mp4"
    script="""import cv2,numpy as np,sys
out=cv2.VideoWriter(sys.argv[1],cv2.VideoWriter_fourcc(*'mp4v'),25,(320,180))
for i in range(40):
 frame=np.zeros((180,320,3),np.uint8)
 x=20+i*2
 for a in range(4):
  for b in range(4):
   frame[60+a*10:70+a*10,x+b*10:x+10+b*10]=255 if (a+b)%2 else 70
 out.write(frame)
out.release()
"""
    await process(['C:/Python314/python.exe','-c',script,str(path)])
    result=await track_object(path,[18,58,44,44],python_executable='C:/Python314/python.exe')
    assert result["cloud_used"] is False
    assert len(result["points"])>10
    assert result["points"][-1]["x"]-result["points"][0]["x"]>65
    assert abs(result["points"][-1]["y"]-58)<2


@pytest.mark.asyncio
@pytest.mark.skipif(not (Path(__file__).resolve().parents[2]/'.audit-work/models/ggml-base.bin').is_file(),reason="optional downloaded Whisper model not installed")
async def test_installed_local_whisper_real_speech():
    root=Path(__file__).resolve().parents[2]
    voice=root/'.audit-work/asr/english-test.wav'
    if not voice.is_file():pytest.skip("local SAPI speech TEST fixture not generated")
    captions=await transcribe(voice,model_path=root/'.audit-work/models/ggml-base.bin',language='en')
    text=' '.join(c['text'] for c in captions).lower()
    assert 'local video editing test' in text and 'subtitles' in text
    assert all(c['end']>c['start'] for c in captions)
