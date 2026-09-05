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
    blacks=[];silences=[];scenes=[];peaks=[];rms=[];begin=None;complete=True
    async def inspect(text):
        nonlocal begin,complete
        if len(blacks)+len(silences)+len(scenes)>=100_000:
            complete=False;return
        for a,b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)",text):
            blacks.append({"start":round(float(a)*TICKS),"end":round(float(b)*TICKS)})
        for kind,value in re.findall(r"silence_(start|end):\s*([\d.]+)",text):
            if kind=="start":begin=round(float(value)*TICKS)
            elif begin is not None:
                observed_end=round(float(value)*TICKS)
                # Decoded AAC can contain trailing codec padding beyond the
                # container's edit duration. Keep that observation, but only
                # propose cuts inside the actual source timeline.
                end=min(observed_end,metadata["duration_ticks"])
                if begin<end:silences.append({"start":begin,"end":end,"observed_end":observed_end})
                begin=None
        scenes.extend(round(float(t)*TICKS) for t in re.findall(r"lavfi.scd.time:\s*([\d.]+)",text))
        found=re.findall(r"Peak level dB:\s*(-?[\d.]+|-inf)",text)
        if found:peaks[:]=found[-1:]
        found=re.findall(r"RMS level dB:\s*(-?[\d.]+|-inf)",text)
        if found:rms[:]=found[-1:]
    await process(argv, progress=progress, diagnostic=inspect, timeout=24*3600)
    return {"metadata":metadata,"black_intervals":blacks,"silence_intervals":silences,
        "scene_times":scenes,
        "peak_db":float(peaks[-1]) if peaks and peaks[-1]!="-inf" else None,
        "rms_db":float(rms[-1]) if rms and rms[-1]!="-inf" else None,
        "near_full_scale_warning":bool(peaks and peaks[-1]!="-inf" and float(peaks[-1])>=-.1),
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


def silence_keep_ranges(analysis, source_in=0, source_out=None, padding=100_000):
    """Compute source ranges from COMPLETE measured silence, never invented cuts."""
    duration=analysis["metadata"]["duration_ticks"]
    source_out=duration if source_out is None else source_out
    if analysis.get("intervals_complete") is not True:raise ValueError("incomplete silence evidence cannot drive automatic cuts")
    if any(type(v) is not int for v in [source_in,source_out,padding]) or not 0<=source_in<source_out<=duration or not 0<=padding<=2*TICKS:
        raise ValueError("invalid silence-cut source range/padding")
    removals=[]
    for item in analysis.get("silence_intervals",[]):
        a,b=item["start"],item["end"]
        if type(a) is not int or type(b) is not int or not 0<=a<b<=duration:raise ValueError("invalid measured silence interval")
        a=max(source_in,a+padding);b=min(source_out,b-padding)
        if b>a:removals.append((a,b))
    cursor=source_in;keep=[]
    for a,b in sorted(removals):
        if a>cursor:keep.append({"start":cursor,"end":a})
        cursor=max(cursor,b)
    if cursor<source_out:keep.append({"start":cursor,"end":source_out})
    return keep


def scene_ranges(analysis, min_duration=500_000):
    if analysis.get("intervals_complete") is not True:raise ValueError("incomplete scene evidence cannot drive segmentation")
    duration=analysis["metadata"]["duration_ticks"]
    if type(min_duration) is not int or not 1<=min_duration<=duration:raise ValueError("invalid minimum scene duration")
    boundaries=[0]
    for time_ in sorted(set(analysis.get("scene_times",[]))):
        if type(time_) is not int or not 0<=time_<=duration:raise ValueError("invalid measured scene time")
        if time_-boundaries[-1]>=min_duration and duration-time_>=min_duration:boundaries.append(time_)
    boundaries.append(duration)
    return [{"start":a,"end":b} for a,b in zip(boundaries,boundaries[1:])]


def _sync_audio_numpy(reference_path,candidate_path,max_offset=10*TICKS,window=60*TICKS):
    """Two bounded mono amplitude envelopes; reject weak or ambiguous matches."""
    import numpy as np
    if type(max_offset) is not int or not 0<max_offset<=30*TICKS or type(window) is not int or not TICKS<=window<=120*TICKS:
        raise ValueError("audio sync window/offset outside bounded limits")
    def envelope(path):
        if Path(path).stat().st_size>120*4000*4:raise ValueError("audio sync capture exceeded bound")
        samples=np.frombuffer(Path(path).read_bytes(),dtype='<f4')
        count=len(samples)//40
        if count<100:raise ValueError("audio sync needs at least one second of audio")
        values=np.sqrt(np.mean(samples[:count*40].reshape(-1,40)**2,axis=1))
        if not np.isfinite(values).all() or float(np.max(values))<.001 or float(np.std(values))<.0001:raise ValueError("audio sync source is silent or lacks distinctive amplitude variation")
        return values-values.mean()
    reference=envelope(reference_path);candidate=envelope(candidate_path)
    limit=max_offset//10_000;scores=[]
    for lag in range(-limit,limit+1):
        if lag>=0:
            n=min(len(reference),len(candidate)-lag);a=reference[:n];b=candidate[lag:lag+n]
        else:
            n=min(len(reference)+lag,len(candidate));a=reference[-lag:-lag+n];b=candidate[:n]
        if n<100:continue
        denominator=float(np.linalg.norm(a)*np.linalg.norm(b))
        if denominator>1e-12:scores.append((float(np.dot(a,b)/denominator),lag,n))
    if not scores:raise ValueError("audio sync has no usable overlap")
    best=max(scores)
    alternate=max((score for score,lag,n in scores if abs(lag-best[1])>=5),default=-1)
    if best[0]<.65 or best[0]-alternate<.04:raise ValueError("audio synchronization is ambiguous; supply timecode or manual offsets")
    offset=best[1]*10_000
    return {"offset_ticks":offset,"confidence":round(best[0],6),"method":"audio_correlation",
        "placements":{"reference_start":max(0,offset),"candidate_start":max(0,-offset)},
        "evidence":{"sample_rate":4000,"envelope_hz":100,"overlap_ticks":best[2]*10_000,"runner_up_score":round(alternate,6),"window_ticks":window},
        "meaning":"Positive offset means candidate audio content occurs later; place reference later by that offset to align nonnegative timelines.","cloud_used":False}


async def synchronize_audio(reference_path,candidate_path,max_offset=10*TICKS,window=60*TICKS,python_executable=None):
    executable=python_executable or os.getenv("BOSSMAN_VIDEO_CV_PYTHON") or sys.executable
    if not Path(executable).is_file():raise ValueError("audio analysis Python runtime is unavailable")
    if type(max_offset) is not int or not 0<max_offset<=30*TICKS or type(window) is not int or not TICKS<=window<=120*TICKS:
        raise ValueError("audio sync window/offset outside bounded limits")
    root=str(Path(__file__).resolve().parents[2])
    code="import sys,json;sys.path.insert(0,sys.argv[1]);from bcc.video_studio.analysis import _sync_audio_numpy;print(json.dumps(_sync_audio_numpy(sys.argv[2],sys.argv[3],**json.loads(sys.argv[4]))))"
    with tempfile.TemporaryDirectory(prefix="bossman-audio-sync-") as td:
        files=[]
        for index,path in enumerate([reference_path,candidate_path]):
            raw,_=await process([binary("ffmpeg"),"-hide_banner","-loglevel","error","-nostdin",*input_args(Path(path)),
                "-t",str(window/TICKS),"-vn","-ac","1","-ar","4000","-f","f32le","pipe:1"],timeout=180,max_output=120*4000*4,binary_output=True)
            file=Path(td)/f"audio-{index}.f32";file.write_bytes(raw);files.append(file)
        out,_=await process([executable,"-c",code,root,*map(str,files),json.dumps({"max_offset":max_offset,"window":window})],timeout=240)
        return json.loads(out)


def autoframe(track_result,target_width,target_height):
    """Fill a target canvas and follow measured centers; simplify within 2 pixels.

    Caller maps source timestamps to its selected clip when speed/trim differs.
    The returned clip transform assumes sequence dimensions match the target.
    """
    import math
    if any(type(v) is not int or not 16<=v<=8192 for v in [target_width,target_height]):raise ValueError("invalid auto-frame target dimensions")
    width,height=track_result["width"],track_result["height"]
    if width<=0 or height<=0:raise ValueError("invalid tracked source dimensions")
    cover=max(target_width/width,target_height/height);contain=min(target_width/width,target_height/height)
    scale=cover/contain
    if scale>32:raise ValueError("auto-frame scale exceeds renderer support")
    limit_x=max(0,(width*cover-target_width)/2);limit_y=max(0,(height*cover-target_height)/2)
    points=[]
    for point in track_result["points"]:
        t=point["t"]
        values=[point[k] for k in ["x","y","width","height"]]
        if type(t) is not int or t<0 or any(type(v) not in (int,float) or not math.isfinite(v) for v in values):raise ValueError("invalid tracking measurements")
        if points and t<=points[-1][0]:raise ValueError("tracking times must increase")
        x=(width/2-point["x"]-point["width"]/2)*cover;y=(height/2-point["y"]-point["height"]/2)*cover
        points.append((t,max(-limit_x,min(limit_x,x)),max(-limit_y,min(limit_y,y))))
    if not points:raise ValueError("auto-frame requires measured tracking points")
    keep={0,len(points)-1};pending=[(0,len(points)-1)]
    while pending:
        a,b=pending.pop()
        if b-a<=1:continue
        largest=0;index=None
        for i in range(a+1,b):
            u=(points[i][0]-points[a][0])/(points[b][0]-points[a][0])
            error=max(abs(points[i][k]-(points[a][k]+u*(points[b][k]-points[a][k]))) for k in [1,2])
            if error>largest:largest=error;index=i
        if largest>2:
            keep.add(index);pending.extend([(a,index),(index,b)])
    if len(keep)>500:raise ValueError("tracking motion needs more than 500 keyframes at two-pixel accuracy; split the clip")
    chosen=[points[i] for i in sorted(keep)]
    return {"transform":{"scale":scale,"x":chosen[0][1],"y":chosen[0][2]},
        "keyframes":{key:[{"t":p[0],"value":round(p[index],4),"easing":"linear"} for p in chosen] for key,index in [("x",1),("y",2)]},
        "evidence":{"method":track_result.get("method"),"input_points":len(points),"retained_points":len(chosen),"max_interpolation_error_pixels":2},"cloud_used":False}


async def scope_media(path,root,output_path,kind="waveform",time=0):
    from .media import digest_file,blocking
    from .render import seconds,publish
    filters={"waveform":"waveform=mode=column:display=overlay:components=7",
        "histogram":"histogram=display_mode=overlay:levels_mode=linear",
        "vectorscope":"vectorscope=mode=color2"}
    if kind not in filters:raise ValueError("unknown video scope")
    when=seconds(time);root=Path(root).resolve();output_path=Path(output_path).resolve()
    if not output_path.is_relative_to(root) or output_path.suffix!=".png" or output_path.exists():raise ValueError("scope output must be a new owned PNG artifact")
    output_path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="scope-",dir=root) as td:
        partial=Path(td)/"scope.png"
        await process([binary("ffmpeg"),"-hide_banner","-loglevel","error","-nostdin","-ss",str(when),*input_args(Path(path)),
            "-an","-vf",filters[kind]+",scale=512:288","-frames:v","1",str(partial)],timeout=60)
        if not partial.exists() or not partial.stat().st_size:raise ValueError("scope has no decoded frame at requested time")
        await probe(partial);publish(partial,output_path)
    return {"path":str(output_path),"kind":kind,"time_ticks":time,"width":512,"height":288,
        "source_sha256":await blocking(digest_file,Path(path)),"snapshot":True,"live":False}


async def hardware_probe(width,height,codec):
    if codec not in {"h264_nvenc","hevc_nvenc","av1_nvenc","libx264"}:raise ValueError("unknown hardware probe codec")
    if any(type(v) is not int or not 16<=v<=8192 or v%2 for v in [width,height]):raise ValueError("invalid hardware probe dimensions")
    try:
        await process([binary("ffmpeg"),"-hide_banner","-loglevel","error","-nostdin","-f","lavfi","-i",f"testsrc2=size={width}x{height}:rate=25", "-frames:v","3","-c:v",codec,"-f","null","-"],timeout=30)
    except ValueError as exc:return {"available":False,"codec":codec,"width":width,"height":height,"reason":str(exc)[-500:]}
    return {"available":True,"codec":codec,"width":width,"height":height,"evidence":"three actual frames encoded to null muxer","reason":None}
