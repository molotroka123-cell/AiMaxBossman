from copy import deepcopy
from pathlib import Path
import math
import shutil
import random
import struct
import wave

import pytest

from bcc.video_studio.media import MediaLibrary,binary,process
from bcc.video_studio.render import render_project
from bcc.video_studio.analysis import (analyse_media,silence_keep_ranges,scene_ranges,synchronize_audio,
    autoframe,scope_media,hardware_probe,track_object)
from .test_video_studio_render import fixture_media,document,rgb,tracked_fixture

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                                  reason="real FFmpeg binaries required")


@needs_ffmpeg
@pytest.mark.asyncio
@pytest.mark.parametrize('nested',[False,True])
async def test_long_reverse_disk_proxy_actual_pixels_audio_cache_and_pure_project(tmp_path,nested):
    source=tmp_path/'long-source.mp4'
    await process([binary('ffmpeg'),'-hide_banner','-loglevel','error','-nostdin','-y',
        '-f','lavfi','-i','color=red:s=320x180:r=25:d=16','-f','lavfi','-i','color=blue:s=320x180:r=25:d=17',
        '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=16',
        '-f','lavfi','-i','sine=frequency=880:sample_rate=48000:duration=17',
        '-filter_complex','[0:v][2:a][1:v][3:a]concat=n=2:v=1:a=1[v][a]',
        '-map','[v]','-map','[a]','-c:v','libx264','-c:a','aac',str(source)])
    media=await MediaLibrary(tmp_path).import_file(source);project=document(media)
    clip=project['sequences'][0]['tracks'][0]['clips'][0]
    clip.update(source_out=33_000_000,reverse=True)
    if nested:
        child=deepcopy(project['sequences'][0]);child['id']='nested';child['tracks'][0]['clips'][0]['reverse']=False
        project['sequences'].append(child)
        clip.pop('media_id');clip['nested_sequence_id']='nested'
    original=deepcopy(project);steps=[]
    async def progress(stage,details):
        if stage=='preparing_reverse':steps.append(details)
    result=await render_project(project,tmp_path,tmp_path/'long-reverse.mp4',progress=progress)
    assert project==original and result['verification']['passed']
    assert len(steps)>=7
    assert (await rgb(tmp_path/'long-reverse.mp4',1))[2]>200
    assert (await rgb(tmp_path/'long-reverse.mp4',31))[0]>200
    async def crossings(path):
        raw,_=await process([binary('ffmpeg'),'-hide_banner','-loglevel','error','-nostdin','-ss','1','-i',str(path),'-t','0.3','-vn','-ac','1','-ar','8000','-f','f32le','pipe:1'],binary_output=True)
        values=struct.unpack('<'+'f'*(len(raw)//4),raw)
        return sum((a<0)!=(b<0) for a,b in zip(values,values[1:]))
    assert await crossings(tmp_path/'long-reverse.mp4')>1.8*await crossings(source)
    cache=list((tmp_path/'cache/reverse').glob('*.json'));assert len(cache)==1
    before=cache[0].stat().st_mtime_ns
    if not nested:
        proxy=await MediaLibrary(tmp_path).reverse_proxy(media,0,33_000_000,{'num':25,'den':1})
        assert proxy['metadata']['reverse_recipe']['chunks']>=7 and cache[0].stat().st_mtime_ns==before


@needs_ffmpeg
@pytest.mark.asyncio
async def test_measured_scene_silence_drive_only_evidenced_ranges(tmp_path):
    red=await fixture_media(tmp_path,'red');blue=await fixture_media(tmp_path,'blue')
    p=document(red);p['media'][blue['id']]=blue
    second=deepcopy(p['sequences'][0]['tracks'][0]['clips'][0]);second.update(id='two',media_id=blue['id'],start=1_000_000)
    p['sequences'][0]['tracks'][0]['clips'].append(second)
    await render_project(p,tmp_path,tmp_path/'scenes.mp4')
    analysis=await analyse_media(tmp_path/'scenes.mp4')
    assert any(abs(t-1_000_000)<100_000 for t in analysis['scene_times'])
    assert len(scene_ranges(analysis))==2
    p['sequences'][0]['tracks'][0]['volume']=0
    await render_project(p,tmp_path,tmp_path/'silence.mp4')
    measured=await analyse_media(tmp_path/'silence.mp4')
    assert silence_keep_ranges(measured,padding=0)==[]
    measured['intervals_complete']=False
    with pytest.raises(ValueError,match='incomplete'):silence_keep_ranges(measured)
    with pytest.raises(ValueError,match='incomplete'):scene_ranges(measured)


def audio_fixture(path,silence_prefix=0,constant=False):
    sr=8000;rng=random.Random(53)
    levels=[.3]*80 if constant else [rng.uniform(.03,.8) for _ in range(80)]
    samples=[0]*round(silence_prefix*sr)
    samples.extend(round(20000*levels[min(79,i//800)]*math.sin(2*math.pi*430*i/sr)) for i in range(8*sr))
    with wave.open(str(path),'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(sr)
        stream.writeframes(struct.pack('<'+'h'*len(samples),*samples))


@needs_ffmpeg
@pytest.mark.asyncio
@pytest.mark.skipif(not Path('C:/Python314/python.exe').is_file(),reason='optional local NumPy runtime absent')
async def test_audio_multicam_correlation_actual_offset_and_ambiguous_refusal(tmp_path):
    reference=tmp_path/'reference.wav';candidate=tmp_path/'candidate.wav'
    audio_fixture(reference);audio_fixture(candidate,.7)
    result=await synchronize_audio(reference,candidate,max_offset=2_000_000,window=10_000_000,python_executable='C:/Python314/python.exe')
    assert abs(result['offset_ticks']-700_000)<=20_000
    assert result['placements']['reference_start']==result['offset_ticks'] and result['placements']['candidate_start']==0
    assert result['confidence']>.9 and result['cloud_used'] is False
    audio_fixture(reference,constant=True);audio_fixture(candidate,.7,constant=True)
    with pytest.raises(ValueError,match='silent|distinctive|ambiguous'):
        await synchronize_audio(reference,candidate,python_executable='C:/Python314/python.exe')


def test_autoframe_measured_coordinates_and_interpolation_bounds():
    tracked={'width':320,'height':180,'method':'measured_test_motion','points':[
        {'t':i*100_000,'x':80+i,'y':60,'width':40,'height':40} for i in range(20)]}
    result=autoframe(tracked,160,240)
    assert result['transform']['scale']>2
    assert len(result['keyframes']['x'])==2
    assert result['keyframes']['x'][0]['value']>result['keyframes']['x'][-1]['value']
    assert result['evidence']['max_interpolation_error_pixels']==2


@needs_ffmpeg
@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['waveform','histogram','vectorscope'])
async def test_actual_scope_artifact(kind,tmp_path):
    media=await fixture_media(tmp_path)
    result=await scope_media(MediaLibrary(tmp_path).resolve(media),tmp_path,tmp_path/(kind+'.png'),kind,time=200_000)
    assert result['snapshot'] and not result['live']
    assert Path(result['path']).read_bytes().startswith(b'\x89PNG')
    assert result['source_sha256']==media['sha256']


@needs_ffmpeg
@pytest.mark.asyncio
async def test_actual_encoder_probe_and_bad_parameters():
    assert (await hardware_probe(320,180,'libx264'))['available']
    with pytest.raises(ValueError):await hardware_probe(321,180,'libx264')


@needs_ffmpeg
@pytest.mark.asyncio
async def test_long_audio_only_reverse_proxy(tmp_path):
    source=tmp_path/'audio.wav'
    await process([binary('ffmpeg'),'-hide_banner','-loglevel','error','-nostdin','-f','lavfi','-i','sine=frequency=440:duration=31','-c:a','pcm_s16le',str(source)])
    library=MediaLibrary(tmp_path);media=await library.import_file(source)
    proxy=await library.reverse_proxy(media,0,31_000_000,{'num':25,'den':1})
    assert proxy['has_audio'] and not proxy['has_video']
    assert abs(proxy['duration_ticks']-31_000_000)<50_000
    assert proxy['metadata']['reverse_recipe']['chunks']==2


@needs_ffmpeg
@pytest.mark.asyncio
@pytest.mark.skipif(not Path('C:/Python314/python.exe').is_file(),reason='optional local CV runtime absent')
async def test_actual_tracking_autoframe_render_keeps_moving_subject_centered(tmp_path):
    source=await tracked_fixture(tmp_path)
    tracking=await track_object(source,[18,58,44,44],python_executable='C:/Python314/python.exe')
    framing=autoframe(tracking,160,240)
    media=await MediaLibrary(tmp_path).import_file(source);p=document(media)
    seq=p['sequences'][0];seq.update(width=160,height=240)
    clip=seq['tracks'][0]['clips'][0];clip['source_out']=media['duration_ticks']
    await render_project(p,tmp_path,tmp_path/'before-frame.mp4')
    clip['transform']=framing['transform'];clip['keyframes']=framing['keyframes']
    await render_project(p,tmp_path,tmp_path/'auto-frame.mp4')
    async def center(path):
        out,_=await process([binary('ffmpeg'),'-hide_banner','-loglevel','error','-nostdin','-ss','1.2','-i',str(path),
            '-frames:v','1','-vf','format=rgb24,crop=20:20:70:110,scale=1:1','-pix_fmt','rgb24','-f','rawvideo','pipe:1'])
        return sum(out)/3
    assert await center(tmp_path/'before-frame.mp4')<10
    assert await center(tmp_path/'auto-frame.mp4')>40
