"""Local deterministic diagnostics, timed captions, and optional real local ASR.

Diagnostics are observations, never unconditional quality failures: deliberate
black scenes and intentional silence are valid editorial choices.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import tempfile
import os
import sys

from .media import TICKS, binary, input_args, probe, process


async def analyse_media(path, *, progress=None):
    path=Path(path)
    metadata=await probe(path)
    argv=[binary("ffmpeg"),"-hide_banner","-loglevel","info","-nostdin",*input_args(path)]
    if metadata["has_video"]:
        argv += ["-vf","blackdetect=d=0.1:pix_th=0.1,scdet=threshold=10", "-map","0:v:0"]
    if metadata["has_audio"]:
        argv += ["-af","silencedetect=noise=-40dB:d=0.3,astats=metadata=0:reset=0", "-map","0:a:0"]
    argv += ["-progress","pipe:1","-f","null","-"]
    blacks=[];silences=[];scenes=[];peaks=[];begin=None;complete=True
    async def inspect(text):
        nonlocal begin,complete
        if len(blacks)+len(silences)+len(scenes)>=100_000:
            complete=False;return
        for a,b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)",text):
            blacks.append({"start":round(float(a)*TICKS),"end":round(float(b)*TICKS)})
        for kind,value in re.findall(r"silence_(start|end):\s*([\d.]+)",text):
            if kind=="start":begin=round(float(value)*TICKS)
            elif begin is not None:
                silences.append({"start":begin,"end":round(float(value)*TICKS)});begin=None
        scenes.extend(round(float(t)*TICKS) for t in re.findall(r"lavfi.scd.time:\s*([\d.]+)",text))
        found=re.findall(r"Peak level dB:\s*(-?[\d.]+|-inf)",text)
        if found:peaks[:]=found[-1:]
    await process(argv, progress=progress, diagnostic=inspect, timeout=24*3600)
    return {"metadata":metadata,"black_intervals":blacks,"silence_intervals":silences,
        "scene_times":scenes,
        "peak_db":float(peaks[-1]) if peaks and peaks[-1]!="-inf" else None,
        "diagnostics_only":True,"intervals_complete":complete,
        "note":"Black/silence are observations requiring comparison with project intent. No lip-sync judgment."}


def parse_captions(text, format="srt"):
    if format not in {"srt","vtt"}:raise ValueError("only SRT and VTT captions are supported")
    if not isinstance(text,str) or len(text)>8_000_000:raise ValueError("caption input exceeds limit")
    def ticks(stamp):
        parts=stamp.replace(",",".").split(":")
        if len(parts)==2:parts.insert(0,"0")
        if len(parts)!=3:raise ValueError("invalid caption time")
        h,m,s=int(parts[0]),int(parts[1]),float(parts[2])
        if not 0<=h<=168 or not 0<=m<60 or not 0<=s<60:raise ValueError("invalid caption timestamp")
        return round((h*3600+m*60+s)*TICKS)
    result=[]
    for block in re.split(r"\n\s*\n",text.replace("\r","").strip()):
        lines=block.splitlines()
        index=next((i for i,line in enumerate(lines) if "-->" in line),None)
        if index is None:continue
        left,right=lines[index].split("-->",1)
        start,end=ticks(left.strip()),ticks(right.strip().split()[0])
        body="\n".join(lines[index+1:])
        if start>=end or not body or len(body)>10000:raise ValueError("invalid caption interval/text")
        # SRT/VTT markup is inert plain text in this API; render escaping prevents
        # ASS directives, include directives or arbitrary font paths.
        key=hashlib.sha256(f"{len(result)}:{start}:{end}:{body}".encode()).hexdigest()[:24]
        result.append({"id":"cap_"+key,"start":start,"end":end,"text":body})
    if not result:raise ValueError("no timed captions found")
    if len(result)>10000:raise ValueError("too many caption segments")
    return result


def export_captions(captions, format="srt"):
    if format not in {"srt","vtt"}:raise ValueError("unsupported caption format")
    separator="," if format=="srt" else "."
    def stamp(value):
        if type(value) is not int or value<0:raise ValueError("caption timestamps must be nonnegative ticks")
        ms=round(value/1000)
        return f"{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02}{separator}{ms%1000:03}"
    blocks=[]
    for i,item in enumerate(captions,1):
        if item["end"]<=item["start"]:raise ValueError("caption end must follow start")
        blocks.append(f"{i}\n{stamp(item['start'])} --> {stamp(item['end'])}\n{item['text']}\n")
    return ("WEBVTT\n\n" if format=="vtt" else "")+"\n".join(blocks)


async def transcribe(path, *, model_path=None, language="auto", progress=None):
    """Real whisper.cpp FFmpeg filter; caller owns model trust, lease and budget.

    No downloads, synthetic captions, cloud fallbacks or inferred model paths.
    A configured model must already exist and be approved by the host.
    """
    if model_path is None:raise ValueError("ASR BLOCKED: no host-approved local whisper.cpp model configured")
    model_path=Path(model_path).resolve()
    if not model_path.is_file() or model_path.suffix!=".bin":raise ValueError("ASR model must be an existing local whisper.cpp .bin file")
    if language!="auto" and not re.fullmatch(r"[a-z]{2,3}",language):raise ValueError("invalid ASR language")
    from .render import filter_path
    with tempfile.TemporaryDirectory(prefix="bossman-asr-") as temporary:
        output=Path(temporary)/"transcript.srt"
        graph="whisper=model='"+filter_path(model_path)+"':language="+language+":use_gpu=false:format=srt:destination='"+filter_path(output)+"'"
        await process([binary("ffmpeg"),"-hide_banner","-loglevel","error","-nostdin",*input_args(Path(path)),
            "-vn","-af",graph,"-progress","pipe:1","-f","null","-"],progress=progress,timeout=24*3600)
        if not output.exists():raise ValueError("ASR did not produce timed transcript")
        return parse_captions(output.read_text(encoding="utf-8"),"srt")


def generation_status():
    return {"status":"BLOCKED","reason":"No verified image/video generation provider is connected to Video Studio", "cloud_used":False}


def _track_cv(path, box, start=0, end=None):
    """Bounded frame streaming; feature loss is an error, never invented motion."""
    import cv2
    import numpy as np
    from .media import file_kind
    file_kind(Path(path))
    if type(start) is not int or start<0 or (end is not None and (type(end) is not int or end<=start)):
        raise ValueError("tracking timestamps require nonnegative integer ticks")
    if not isinstance(box,list) or len(box)!=4 or any(type(x) not in (int,float) for x in box):raise ValueError("tracking box requires x,y,width,height pixels")
    x,y,w,h=map(float,box)
    if not all(np.isfinite([x,y,w,h])) or x<0 or y<0 or w<4 or h<4:raise ValueError("invalid tracking box")
    capture=cv2.VideoCapture(str(path))
    try:
        fps=capture.get(cv2.CAP_PROP_FPS)
        if not 0<fps<=240:raise ValueError("tracking requires a valid bounded frame rate")
        duration=capture.get(cv2.CAP_PROP_FRAME_COUNT)/fps
        start=start/TICKS;end=duration if end is None else end/TICKS
        if not 0<=start<end<=duration+.05 or end-start>600:raise ValueError("tracking segment must be at most ten minutes")
        capture.set(cv2.CAP_PROP_POS_MSEC,start*1000)
        ok,frame=capture.read()
        if not ok:raise ValueError("tracking source could not decode")
        height,width=frame.shape[:2]
        if x+w>width or y+h>height:raise ValueError("tracking box outside source")
        mask=np.zeros((height,width),dtype=np.uint8);mask[int(y):int(y+h),int(x):int(x+w)]=255
        old=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
        points=cv2.goodFeaturesToTrack(old,mask=mask,maxCorners=80,qualityLevel=.01,minDistance=3)
        if points is None or len(points)<2:raise ValueError("tracking region lacks stable visual features")
        result=[{"t":round(start*TICKS),"x":x,"y":y,"width":w,"height":h,"confidence":1.0}]
        index=1;last_emit=0
        while start+index/fps<end:
            ok,frame=capture.read()
            if not ok:raise ValueError("tracking source ended before requested range")
            gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
            new,status,error=cv2.calcOpticalFlowPyrLK(old,gray,points,None,winSize=(21,21),maxLevel=3)
            if new is None or status is None:raise ValueError("tracking lost visual features")
            keep=(status.reshape(-1)==1)&np.isfinite(new.reshape(-1,2)).all(axis=1)
            good_new=new.reshape(-1,2)[keep];good_old=points.reshape(-1,2)[keep]
            if len(good_new)<2:raise ValueError("tracking lost visual features")
            delta=np.median(good_new-good_old,axis=0)
            x+=float(delta[0]);y+=float(delta[1]);x=max(0,min(width-w,x));y=max(0,min(height-h,y))
            if index/fps-last_emit>=.1:
                result.append({"t":round((start+index/fps)*TICKS),"x":round(x,3),"y":round(y,3),"width":w,"height":h,"confidence":round(len(good_new)/len(points),3)})
                last_emit=index/fps
            points=good_new.reshape(-1,1,2);old=gray;index+=1
        return {"method":"opencv-pyramidal-lucas-kanade-translation","points":result,
            "width":width,"height":height,"fps":fps,"cloud_used":False,
            "limits":"Translation tracking of textured regions; not identity recognition, segmentation or scale/occlusion recovery."}
    finally:
        capture.release()


async def track_object(path, box, *, start=0, end=None, python_executable=None):
    """Run an explicitly host-selected installed CV runtime; cancellation reaps it."""
    executable=python_executable or os.getenv("BOSSMAN_VIDEO_CV_PYTHON") or sys.executable
    if not Path(executable).is_file():raise ValueError("tracking Python runtime is unavailable")
    package_root=str(Path(__file__).resolve().parents[2])
    code="import sys,json;sys.path.insert(0,sys.argv[1]);from bcc.video_studio.analysis import _track_cv;print(json.dumps(_track_cv(sys.argv[2],**json.loads(sys.argv[3]))))"
    out,_=await process([executable,"-c",code,package_root,str(Path(path).resolve()),json.dumps({"box":box,"start":start,"end":end})],timeout=1800)
    return json.loads(out)
