"""One deterministic filtergraph compiler for preview and export.

Project ticks are converted only at the FFmpeg boundary. All filter expressions
come from bounded numeric data; user text lives in generated subtitle files.
"""
from __future__ import annotations

import asyncio
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile

from .media import MediaLibrary, TICKS, binary, blocking, digest_file, input_args, probe, process

EFFECT_PARAMETERS = {
    "eq":{"brightness","contrast","saturation","gamma"}, "color":{"brightness","contrast","saturation","gamma"},
    "colorbalance":{"rs","gs","bs","rm","gm","bm","rh","gh","bh"}, "curves":{"master","red","green","blue"},
    "lut3d":{"cube_text"}, "chroma":{"color","similarity","blend"}, "chromakey":{"color","similarity","blend"},
    "mask":{"shape","x","y","radius","width","height"}, "fade_in":{"duration"}, "fade_out":{"duration"},
    "transition":{"kind","duration"}, "stabilize":set(), "blend":{"mode"}, "volume":{"gain"},
    "audio_fade_in":{"duration"}, "audio_fade_out":{"duration"}, "equalizer":{"frequency","width","gain"},
    "compressor":{"threshold","ratio","attack","release"}, "limiter":{"limit"}, "denoise":{"reduction"},
    "loudnorm":{"integrated","true_peak","range"}, "ducking":{"sidechain_track_id"},
}


def validate_effect(effect):
    kind=effect.get("type")
    if kind not in EFFECT_PARAMETERS:raise ValueError(f"unsupported video effect: {kind}")
    params=effect.get("params",{})
    if not isinstance(params,dict) or set(params)-EFFECT_PARAMETERS[kind]:raise ValueError("unsupported effect parameters")
    if type(effect.get("enabled",True)) is not bool:raise ValueError("effect enabled must be boolean")
    return effect


def number(value, *, minimum=-1e9, maximum=1e9):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError("invalid finite numeric media parameter")
    return float(value)


def seconds(ticks):
    return number(ticks, minimum=0, maximum=86_400 * TICKS) / TICKS


def fmt(value):
    return format(float(value), ".12g")


def rate(value):
    if not isinstance(value, dict) or set(value) != {"num", "den"}:
        raise ValueError("rate must contain num and den")
    n, d = value["num"], value["den"]
    if type(n) is not int or type(d) is not int or not 0 < n <= 1_000_000 or not 0 < d <= 1_000_000:
        raise ValueError("rate must use positive integer numerator and denominator")
    result = Fraction(n, d)
    if not Fraction(1, 100) <= result <= 240:
        raise ValueError("rate outside supported range")
    return result


def clip_duration(clip):
    if clip.get("freeze") or clip.get("title") or clip.get("adjustment"):
        return seconds(clip.get("freeze_duration", 0))
    if clip.get("speed_ramp"):
        return sum((seconds(p["source_out"]) - seconds(p["source_in"])) / float(rate(p["speed"])) for p in clip["speed_ramp"])
    return (seconds(clip["source_out"]) - seconds(clip["source_in"])) / float(rate(clip.get("speed", {"num": 1, "den": 1})))


def sequence_duration(sequence):
    return max((seconds(c["start"]) + clip_duration(c) for t in sequence["tracks"] for c in t["clips"]), default=0)


def expression(frames, default, variable="t", *, minimum=-1e6, maximum=1e6):
    if not frames:
        return fmt(number(default, minimum=minimum, maximum=maximum))
    if not isinstance(frames, list) or len(frames) > 500:
        raise ValueError("keyframes must be a bounded list")
    points = []
    for frame in frames:
        time = seconds(frame["t"])
        value = number(frame["value"], minimum=minimum, maximum=maximum)
        ease = frame.get("easing", "linear")
        if ease not in {"linear", "hold", "ease_in", "ease_out", "ease_in_out"}:
            raise ValueError("unsupported keyframe easing")
        if points and time <= points[-1][0]:
            raise ValueError("keyframe times must increase strictly")
        points.append((time, value, ease))
    result = fmt(points[-1][1])
    for left, right in reversed(list(zip(points, points[1:]))):
        a, value, ease = left
        b, target, _ = right
        u = f"(({variable}-{fmt(a)})/{fmt(b-a)})"
        u = {"linear": u, "hold": "0", "ease_in": f"pow({u},2)",
            "ease_out": f"(1-pow(1-{u},2))", "ease_in_out": f"({u}*{u}*(3-2*{u}))"}[ease]
        interpolation = f"({fmt(value)}+({fmt(target-value)})*{u})"
        result = f"if(lt({variable},{fmt(b)}),{interpolation},{result})"
    return f"if(lt({variable},{fmt(points[0][0])}),{fmt(points[0][1])},{result})"


def tempo(speed):
    filters = []
    while speed > 2:
        filters.append("atempo=2")
        speed /= 2
    while speed < .5:
        filters.append("atempo=0.5")
        speed *= 2
    return ",".join([*filters, "atempo=" + fmt(speed)])


def filter_path(path):
    # Host-generated absolute paths only; the filter grammar still needs escaping.
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def ass_text(value):
    return str(value).replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\r", "").replace("\n", "\\N")


def subtitle_time(value):
    centis = max(0, round(value * 100))
    return f"{centis//360000}:{centis//6000%60:02}:{centis//100%60:02}.{centis%100:02}"


def subtitles_file(path, width, height, rows, style=None):
    header = f"[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\nWrapStyle: 0\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Default,Arial,36,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,0,2,20,20,25,1\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    if style:
        font=str(style.get("font","Arial"))
        if not re.fullmatch(r"[\w .-]{1,80}",font):raise ValueError("invalid title font family")
        size=fmt(number(style.get("size",36),minimum=6,maximum=512))
        color=style.get("color","FFFFFF")
        if not isinstance(color,str) or not re.fullmatch(r"#?[0-9a-fA-F]{6}",color):raise ValueError("invalid title color")
        color=color.lstrip("#");color="&H00"+color[4:6]+color[2:4]+color[:2]
        align={"left":1,"center":2,"right":3,"middle":5,"top":8}.get(style.get("align","center"))
        if align is None:raise ValueError("invalid title alignment")
        border=3 if style.get("background") else 1
        header=header.replace("Style: Default,Arial,36,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,0,2,20,20,25,1",
            f"Style: Default,{font},{size},{color},&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,{border},2,0,{align},20,20,25,1")
    for start, end, text in rows:
        header += f"Dialogue: 0,{subtitle_time(start)},{subtitle_time(end)},Default,,0,0,0,,{ass_text(text)}\n"
    path.write_text(header, encoding="utf-8")


class Compiler:
    def __init__(self, project, library, temporary):
        self.project, self.library, self.temporary = project, library, temporary
        self.inputs, self.graph, self.counter = [], [], 0
        self.sequences = {s["id"]: s for s in project["sequences"]}

    def label(self):
        self.counter += 1
        return f"n{self.counter}"

    def node(self, inputs, filters, outputs=1):
        labels = [self.label() for _ in range(outputs)]
        self.graph.append("".join(f"[{x}]" for x in inputs) + filters + "".join(f"[{x}]" for x in labels))
        return labels[0] if outputs == 1 else labels

    def source(self, media):
        if media.get("metadata",{}).get("color_transfer") in {"smpte2084","arib-std-b67"}:
            raise ValueError("HDR input needs an explicit validated tone-mapping profile; SDR tagging alone is not conversion")
        path = self.library.resolve(media)
        index = sum(1 for x in self.inputs if x == "-i")
        self.inputs += input_args(path)
        return f"{index}:v:0" if media["has_video"] else None, f"{index}:a:0" if media["has_audio"] else None

    def temporal(self, source, clip, audio=False):
        duration = clip_duration(clip)
        if duration <= 0:
            raise ValueError("clip duration must be positive")
        if clip.get("freeze") and audio:
            return None
        if clip.get("reverse") and seconds(clip["source_out"])-seconds(clip["source_in"]) > 30:
            raise ValueError("reverse clips above 30 seconds require a bounded reverse proxy; split first")
        prefix = "a" if audio else ""
        parts = clip.get("speed_ramp") or [{"source_in": clip["source_in"], "source_out": clip["source_out"], "speed": clip.get("speed", {"num":1,"den":1})}]
        sources = self.node([source], ("asplit" if audio else "split") + f"={len(parts)}", len(parts)) if len(parts)>1 else [source]
        if isinstance(sources, str): sources = [sources]
        chunks=[]
        for src, part in zip(sources, parts):
            start, end = seconds(part["source_in"]), seconds(part["source_out"])
            if end <= start: raise ValueError("invalid source range")
            speed = float(rate(part["speed"]))
            filters = f"{prefix}trim=start={fmt(start)}:end={fmt(end)},{prefix}setpts=PTS-STARTPTS"
            if clip.get("freeze"):
                filters += f",select='eq(n,0)',tpad=stop_mode=clone:stop_duration={fmt(duration)},trim=duration={fmt(duration)},setpts=PTS-STARTPTS"
            else:
                if clip.get("reverse"): filters += f",{prefix}reverse,{prefix}setpts=PTS-STARTPTS"
                filters += "," + (tempo(speed) if audio else f"setpts=PTS/{fmt(speed)}")
            chunks.append(self.node([src], filters))
        if len(chunks)>1:
            if clip.get("reverse"): chunks.reverse()
            return self.node(chunks, f"concat=n={len(chunks)}:v={0 if audio else 1}:a={1 if audio else 0}")
        return chunks[0]

    def video_effects(self, clip, width, height, duration):
        tr = clip.get("transform", {})
        keys = clip.get("keyframes", {})
        allowed = {"x", "y", "scale", "rotation", "opacity", "volume", "transform.x", "transform.y", "transform.scale", "transform.rotation", "transform.opacity", "audio.volume"}
        if set(keys)-allowed: raise ValueError("unsupported keyframe parameter")
        def key(name): return keys.get("transform."+name, keys.get(name))
        scale = expression(key("scale"), tr.get("scale",1), minimum=.01, maximum=32)
        rotation = expression(key("rotation"), tr.get("rotation",0), minimum=-3600, maximum=3600)
        opacity = expression(key("opacity"), tr.get("opacity",1), "T", minimum=0, maximum=1)
        crop=tr.get("crop", [0,0,0,0])
        if not isinstance(crop,list) or len(crop)!=4: raise ValueError("crop requires left,top,right,bottom fractions")
        l,t,r,b=[number(x, minimum=0, maximum=.99) for x in crop]
        if l+r>=1 or t+b>=1:raise ValueError("crop removes complete image")
        filters=[f"crop=iw*(1-{fmt(l+r)}):ih*(1-{fmt(t+b)}):iw*{fmt(l)}:ih*{fmt(t)}", "scale=trunc(iw*sar/2)*2:ih", "setsar=1",
            f"scale=w='max(2,trunc(iw*min({width}/iw,{height}/ih)*({scale})/2)*2)':h='max(2,trunc(ih*min({width}/iw,{height}/ih)*({scale})/2)*2)':eval=frame",
            "format=rgba", f"rotate=angle='({rotation})*PI/180':fillcolor=none"]
        if key("opacity") or tr.get("opacity",1)!=1:
            filters.append(f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='alpha(X,Y)*({opacity})'")
        for effect in clip.get("effects", []):
            if not effect.get("enabled",True): continue
            kind,p = effect["type"], effect.get("params",{})
            val=lambda k,d,lo=-1e6,hi=1e6:fmt(number(p.get(k,d),minimum=lo,maximum=hi))
            if kind in {"eq","color"}:
                filters.append(f"eq=brightness={val('brightness',0,-1,1)}:contrast={val('contrast',1,-1000,1000)}:saturation={val('saturation',1,0,3)}:gamma={val('gamma',1,.1,10)}")
            elif kind=="colorbalance":
                filters.append("colorbalance="+":".join(k+"="+val(k,0,-1,1) for k in ["rs","gs","bs","rm","gm","bm","rh","gh","bh"]))
            elif kind=="curves":
                channels=[]
                for key_,points in p.items():
                    if key_ not in {"master","red","green","blue"} or not isinstance(points,list) or not 2<=len(points)<=32: raise ValueError("invalid curves channel")
                    pairs=[];last=-1
                    for pair in points:
                        x,y=[number(v,minimum=0,maximum=1) for v in pair]
                        if x<=last: raise ValueError("curve x values must increase")
                        last=x;pairs.append(f"{fmt(x)}/{fmt(y)}")
                    channels.append(key_+"='"+" ".join(pairs)+"'")
                filters.append("curves="+":".join(channels))
            elif kind=="lut3d":
                text=p.get("cube_text", "")
                if not isinstance(text,str) or len(text)>2_000_000: raise ValueError("invalid LUT data")
                size=None; rows=0
                for line in text.splitlines():
                    line=line.strip()
                    if not line or line.startswith("#"):continue
                    items=line.split()
                    if items[0]=="LUT_3D_SIZE" and len(items)==2:
                        size=int(items[1])
                        if not 2<=size<=33:raise ValueError("LUT size must be 2..33")
                    elif items[0] in {"DOMAIN_MIN","DOMAIN_MAX"} and len(items)==4:
                        for item in items[1:]: number(float(item),minimum=-16,maximum=16)
                    elif len(items)==3:
                        for item in items:number(float(item),minimum=-16,maximum=16)
                        rows+=1
                    else:raise ValueError("unsupported LUT directive")
                if size is None or rows!=size**3:raise ValueError("incomplete LUT cube")
                path=self.temporary/(hashlib.sha256(text.encode()).hexdigest()+".cube")
                path.write_text(text,encoding="ascii")
                filters.append("lut3d=file='"+filter_path(path)+"'")
            elif kind in {"chroma","chromakey"}:
                color=p.get("color","00FF00")
                if not re.fullmatch(r"#?[0-9A-Fa-f]{6}",color):raise ValueError("invalid chromakey color")
                filters.append(f"chromakey=0x{color.lstrip('#')}:{val('similarity',.1,.01,1)}:{val('blend',.05,0,1)}")
            elif kind=="mask":
                shape=p.get("shape","rectangle")
                if shape=="circle":
                    x,y,radius=val("x",.5,0,1),val("y",.5,0,1),val("radius",.5,0,2)
                    mask=f"lte(pow(X-W*{x},2)+pow(Y-H*{y},2),pow(min(W,H)*{radius},2))"
                elif shape=="rectangle":
                    x,y,w,h=val("x",0,0,1),val("y",0,0,1),val("width",1,0,1),val("height",1,0,1)
                    mask=f"between(X,W*{x},W*({x}+{w}))*between(Y,H*{y},H*({y}+{h}))"
                else:raise ValueError("unsupported mask shape")
                filters.append(f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='alpha(X,Y)*({mask})'")
            elif kind in {"fade_in","fade_out","transition"}:
                length=number(p.get("duration", .5),minimum=.001,maximum=duration)
                if kind=="transition" and p.get("kind","fade")!="fade": raise ValueError("only alpha-fade overlap transitions are implemented")
                direction="out" if kind=="fade_out" else "in"
                start=duration-length if direction=="out" else 0
                filters.append(f"fade=t={direction}:st={fmt(start)}:d={fmt(length)}:alpha=1")
            elif kind=="stabilize":
                filters.append("deshake")
            elif kind in AUDIO_EFFECTS or kind=="blend": pass
            else: raise ValueError(f"unsupported video effect: {kind}")
        return ",".join(filters)

    def audio_effects(self, clip, track):
        keys=clip.get("keyframes",{})
        volume=expression(keys.get("audio.volume",keys.get("volume")), clip.get("volume",1), minimum=0,maximum=16)
        gain=number(track.get("volume",1),minimum=0,maximum=16)
        pan=max(-1,min(1,number(track.get("pan",0),minimum=-1,maximum=1)+number(clip.get("pan",0),minimum=-1,maximum=1)))
        filters=["aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo",f"volume='({volume})*{fmt(gain)}':eval=frame",f"pan=stereo|c0={fmt(min(1,1-pan))}*c0|c1={fmt(min(1,1+pan))}*c1"]
        for effect in clip.get("effects",[]):
            if not effect.get("enabled",True):continue
            kind,p=effect["type"],effect.get("params",{})
            val=lambda k,d,lo=-1e6,hi=1e6:fmt(number(p.get(k,d),minimum=lo,maximum=hi))
            if kind=="volume":filters.append("volume="+val("gain",1,0,16))
            elif kind in {"audio_fade_in","audio_fade_out"}:
                duration=clip_duration(clip);length=number(p.get("duration",.5),minimum=.001,maximum=duration)
                direction="out" if kind.endswith("out") else "in"
                filters.append(f"afade=t={direction}:st={fmt(duration-length if direction=='out' else 0)}:d={fmt(length)}")
            elif kind=="equalizer":filters.append(f"equalizer=f={val('frequency',1000,20,20000)}:t=q:w={val('width',1,.01,100)}:g={val('gain',0,-30,30)}")
            elif kind=="compressor":filters.append(f"acompressor=threshold={val('threshold',.125,.00097563,1)}:ratio={val('ratio',4,1,20)}:attack={val('attack',20,.01,2000)}:release={val('release',250,.01,9000)}")
            elif kind=="limiter":filters.append("alimiter=limit="+val("limit",.95,.0625,1))
            elif kind=="denoise":filters.append("afftdn=nr="+val("reduction",12,.01,97))
            elif kind=="loudnorm":filters.append(f"loudnorm=I={val('integrated',-16,-70,-5)}:TP={val('true_peak',-1,-9,0)}:LRA={val('range',11,1,50)}")
            elif kind=="ducking":pass
        return ",".join(filters)

    def sequence(self, sequence_id, visiting=()):
        if sequence_id in visiting:raise ValueError("nested sequence cycle")
        seq=self.sequences[sequence_id]
        width,height=int(seq["width"]),int(seq["height"])
        if not 16<=width<=8192 or not 16<=height<=8192:raise ValueError("sequence dimensions unsupported")
        fps=rate(seq["fps"]);duration=sequence_duration(seq)
        if not 0<duration<=86400:raise ValueError("sequence must have a positive duration up to 24 hours")
        video=self.node([],f"color=c=black:s={width}x{height}:r={fps.numerator}/{fps.denominator}:d={fmt(duration)},format=rgba")
        audio_tracks={}; ducking={}
        solos={t["kind"] for t in seq["tracks"] if t.get("solo")}
        for track in seq["tracks"]:
            if track.get("mute") or (track["kind"] in solos and not track.get("solo")):continue
            for effect in track.get("effects",[]):validate_effect(effect)
            clips=[]
            for clip in track["clips"]:
                for effect in clip.get("effects",[]):validate_effect(effect)
                start=seconds(clip["start"]);length=clip_duration(clip)
                if clip.get("adjustment"):
                    if track["kind"]!="adjustment":raise ValueError("adjustment clip requires adjustment track")
                    effects=[*track.get("effects",[]),*clip.get("effects",[])]
                    if any(e["type"] in AUDIO_EFFECTS or e["type"]=="blend" for e in effects):raise ValueError("adjustment layer supports image filters only")
                    original,adjusted=self.node([video],"split=2",2)
                    adjusted=self.node([adjusted],f"trim=start={fmt(start)}:end={fmt(start+length)},setpts=PTS-STARTPTS")
                    adjusted=self.node([adjusted],self.video_effects({**clip,"effects":effects},width,height,length)+f",setpts=PTS+{fmt(start)}/TB")
                    video=self.node([original,adjusted],f"overlay=eof_action=pass:repeatlast=0:enable='gte(t,{fmt(start)})*lt(t,{fmt(start+length)})'")
                    continue
                if clip.get("nested_sequence_id"):
                    v,a,_=self.sequence(clip["nested_sequence_id"],(*visiting,sequence_id))
                elif clip.get("title"):
                    title=clip["title"]
                    if len(str(title.get("text","")))>10000:raise ValueError("title too long")
                    v=self.node([],f"color=c=black@0:s={width}x{height}:r={fps}:d={fmt(length)},format=rgba")
                    textfile=self.temporary/(self.label()+".ass")
                    subtitles_file(textfile,width,height,[(0,length,title.get("text",""))],title)
                    v=self.node([v],"subtitles=filename='"+filter_path(textfile)+"':alpha=1")
                    a=None
                else:
                    media=self.project["media"][clip["media_id"]]
                    v,a=self.source(media)
                    if media["duration_ticks"]==0 and v:
                        v=self.node([v],f"loop=loop=-1:size=1:start=0,trim=duration={fmt(length)},setpts=PTS-STARTPTS")
                if not clip.get("title"):
                    v=self.temporal(v,clip) if v else None
                    a=self.temporal(a,clip,True) if a else None
                if a and clip.get("audio_disabled"):
                    self.graph.append(f"[{a}]anullsink");a=None
                if v and track["kind"]!="audio" and not clip.get("audio_only"):
                    visual_clip={**clip,"effects":[*clip.get("effects",[]),*(e for e in track.get("effects",[]) if e["type"] not in AUDIO_EFFECTS)]}
                    v=self.node([v],self.video_effects(visual_clip,width,height,length)+f",fps={fps},setpts=PTS+{fmt(start)}/TB")
                    tr=clip.get("transform",{});keys=clip.get("keyframes",{})
                    x=expression(keys.get("transform.x",keys.get("x")),tr.get("x",0),f"(t-{fmt(start)})")
                    y=expression(keys.get("transform.y",keys.get("y")),tr.get("y",0),f"(t-{fmt(start)})")
                    blend=next((e for e in clip.get("effects",[]) if e["type"]=="blend" and e.get("enabled",True)),None)
                    if blend:
                        mode=blend.get("params",{}).get("mode","normal")
                        if mode not in {"normal","multiply","screen","addition","overlay","difference"}:raise ValueError("unsupported blend mode")
                        if mode!="normal":
                            v=self.node([v],f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black@0,format=rgba")
                            video=self.node([video,v],f"blend=all_mode={mode}:enable='between(t,{fmt(start)},{fmt(start+length)})':shortest=0:repeatlast=0")
                            v=None
                    if v:
                        video=self.node([video,v],f"overlay=x='(W-w)/2+({x})':y='(H-h)/2+({y})':eof_action=pass:repeatlast=0:enable='gte(t,{fmt(start)})*lt(t,{fmt(start+length)})'")
                elif v:
                    self.graph.append(f"[{v}]nullsink")
                if a:
                    a=self.node([a],self.audio_effects(clip,track)+f",adelay={round(start*48000)}S:all=1")
                    clips.append(a)
                for effect in clip.get("effects",[]):
                    if effect.get("enabled",True) and effect["type"]=="ducking":
                        ducking[track["id"]]=effect.get("params",{}).get("sidechain_track_id")
            if clips:
                bus=self.node(clips,f"amix=inputs={len(clips)}:duration=longest:normalize=0")
                if track.get("effects"):
                    bus=self.node([bus],self.audio_effects({"source_in":0,"source_out":round(duration*TICKS),"effects":track["effects"]},{}))
                    for effect in track["effects"]:
                        if effect.get("enabled",True) and effect["type"]=="ducking":ducking[track["id"]]=effect.get("params",{}).get("sidechain_track_id")
                audio_tracks[track["id"]]=bus
        for target,side in ducking.items():
            if target not in audio_tracks or side not in audio_tracks or target==side:raise ValueError("ducking requires a distinct audible sidechain track")
            keep,signal=self.node([audio_tracks[side]],"asplit=2",2)
            audio_tracks[side]=keep
            audio_tracks[target]=self.node([audio_tracks[target],signal],"sidechaincompress=threshold=0.05:ratio=8:attack=20:release=300")
        silence=self.node([],f"anullsrc=r=48000:cl=stereo,atrim=duration={fmt(duration)}")
        audio=self.node([silence,*audio_tracks.values()],f"amix=inputs={len(audio_tracks)+1}:duration=first:normalize=0,alimiter=limit=0.98:level=0:latency=1,aresample=48000")
        captions=[c for c in self.project.get("captions",[]) if c.get("sequence_id",self.project["active_sequence_id"])==sequence_id]
        if captions:
            path=self.temporary/(self.label()+".ass")
            subtitles_file(path,width,height,[(seconds(c["start"]),seconds(c["end"]),c["text"]) for c in captions])
            video=self.node([video],"subtitles=filename='"+filter_path(path)+"'")
        return video,audio,duration


AUDIO_EFFECTS={"volume","audio_fade_in","audio_fade_out","equalizer","compressor","limiter","denoise","loudnorm","ducking"}


async def verify_output(path, expected=None):
    """Independent metadata and full decode verification; no success from file existence."""
    path=Path(path);expected=expected or {}
    info=await probe(path)
    failures=[]
    for key in ("width","height","has_video","has_audio"):
        if key in expected and info[key]!=expected[key]:failures.append(f"{key} mismatch")
    if "duration_ticks" in expected:
        target=number(expected["duration_ticks"],minimum=1,maximum=86_400*TICKS)
        tolerance=max(150_000, 2*TICKS/float(rate(expected.get("fps",{"num":25,"den":1}))))
        if abs(info["duration_ticks"]-target)>tolerance:failures.append("duration mismatch")
    if "fps" in expected and info["has_video"] and rate(info["fps"])!=rate(expected["fps"]):failures.append("frame rate mismatch")
    try:
        await process([binary("ffmpeg"),"-hide_banner","-loglevel","error","-xerror","-nostdin",*input_args(path),"-map","0:v?","-map","0:a?","-f","null","-"],timeout=3600)
        decoded=True
    except ValueError:
        decoded=False;failures.append("full decode failed")
    sampled=[]
    if decoded and info["has_video"]:
        duration=info["duration_ticks"]/TICKS
        for when in sorted(set([0, duration*.5, max(0,duration-.12)])):
            raw,_=await process([binary("ffmpeg"),"-hide_banner","-loglevel","error","-nostdin","-ss",fmt(when),
                *input_args(path),"-frames:v","1","-vf","scale=16:16","-pix_fmt","rgb24","-f","rawvideo","pipe:1"],timeout=60)
            if len(raw)!=16*16*3:
                failures.append("sample frame missing");continue
            sampled.append({"time_ticks":round(when*TICKS),"sha256":hashlib.sha256(raw).hexdigest(),
                "mean_rgb":[round(sum(raw[i::3])/256,2) for i in range(3)]})
    starts=info["metadata"]
    offset=None
    if starts.get("video_start") is not None and starts.get("audio_start") is not None:
        offset=round((float(starts["audio_start"])-float(starts["video_start"]))*TICKS)
        if abs(offset)>150_000:failures.append("audio/video stream start mismatch")
    content_digest=await blocking(digest_file,path)
    if expected.get("sha256") and expected["sha256"]!=content_digest:failures.append("artifact content hash mismatch")
    return {**info,"bytes":path.stat().st_size,"sha256":content_digest,"decoded":decoded,"sample_frames":sampled,
        "av_start_offset_ticks":offset,"passed":not failures,"failures":failures}


def publish(partial, output_path):
    with partial.open("rb+") as source:
        os.fsync(source.fileno())
    os.link(partial,output_path)


async def stream_copy(project, root, output_path, options, progress):
    """Exact full-clip concatenation only; incompatible streams fail explicitly.

    The concat manifest is generated from previously hash-verified owned files;
    uploaded concat manifests and client-provided file paths remain forbidden.
    """
    if set(options)-{"mode","sequence_id"}:raise ValueError("stream copy cannot resize, trim a range, or apply encoding options")
    seq=next(s for s in project["sequences"] if s["id"]==options.get("sequence_id",project["active_sequence_id"]))
    tracks=[t for t in seq["tracks"] if t["clips"] and not t.get("mute")]
    if len(tracks)!=1 or tracks[0]["kind"]!="video" or project.get("captions"):raise ValueError("stream copy requires one video track and no captions")
    track=tracks[0]
    if track.get("effects") or track.get("volume",1)!=1 or track.get("pan",0)!=0:raise ValueError("stream copy cannot apply track processing")
    library=MediaLibrary(root);paths=[];signature=None;end=0
    for clip in sorted(track["clips"],key=lambda c:c["start"]):
        media=project["media"].get(clip.get("media_id"))
        if media is None or not media["has_video"] or not media["has_audio"]:raise ValueError("stream copy currently requires complete audio/video media clips")
        defaults={"x":0,"y":0,"scale":1,"rotation":0,"opacity":1,"crop":[0,0,0,0]}
        if (clip.get("effects") or clip.get("keyframes") or clip.get("reverse") or clip.get("freeze") or clip.get("speed_ramp")
            or clip.get("audio_disabled") or clip.get("volume",1)!=1 or clip.get("pan",0)!=0
            or any(v!=defaults.get(k) for k,v in clip.get("transform",{}).items())
            or rate(clip.get("speed",{"num":1,"den":1}))!=1 or clip["source_in"]!=0
            or clip["source_out"]!=media["duration_ticks"] or abs(clip["start"]-end)>1):
            raise ValueError("stream copy requires contiguous, unmodified full source clips")
        if (media["width"],media["height"],media["fps"])!=(seq["width"],seq["height"],seq["fps"]):raise ValueError("stream copy requires matching sequence dimensions and frame rate")
        path=await blocking(library.resolve,media)
        if media["metadata"].get("format","").split(",")[0]!="mov":raise ValueError("stream copy supports verified MOV/MP4 sources only")
        out,_=await process([binary("ffprobe"),"-v","error",*input_args(path),"-show_streams","-show_data_hash","sha256","-of","json"],timeout=60)
        streams=json.loads(out)["streams"]
        fields={"codec_type","codec_name","profile","level","width","height","pix_fmt","sample_rate","channels","channel_layout","extradata_hash","time_base"}
        current=[{k:v for k,v in s.items() if k in fields} for s in streams if s["codec_type"] in {"video","audio"}]
        if signature is not None and signature!=current:raise ValueError("stream copy source codec parameters differ; normalize through regular export")
        signature=current;paths.append(path);end+=media["duration_ticks"]
    if not paths:raise ValueError("no stream-copy clips")
    with tempfile.TemporaryDirectory(prefix="copy-",dir=root) as td:
        manifest=Path(td)/"sources.ffconcat"
        rows=["ffconcat version 1.0"]
        for path in paths:
            escaped=str(path).replace("\\","/").replace("'","'\\''")
            rows.extend([f"file '{escaped}'","option enable_drefs 0","option use_absolute_path 0"])
        manifest.write_text("\n".join(rows)+"\n",encoding="utf-8")
        partial=Path(td)/("output"+output_path.suffix)
        await process([binary("ffmpeg"),"-hide_banner","-loglevel","warning","-nostdin","-y","-protocol_whitelist","file","-f","concat","-safe","0","-i",str(manifest),
            "-map","0:v:0","-map","0:a:0","-c","copy","-movflags","+faststart","-progress","pipe:1",str(partial)],progress=progress)
        expected={"width":seq["width"],"height":seq["height"],"fps":seq["fps"],"duration_ticks":end,"has_audio":True,"has_video":True}
        verification=await verify_output(partial,expected)
        if not verification["passed"]:raise ValueError("stream copy failed verification; use normalized export")
        publish(partial,output_path)
        return {"path":str(output_path),"verification":verification,"profile":{**expected,"mode":"stream_copy","video_codec":"copy","audio_codec":"copy"}}


async def render_project(project, root, output_path, options=None, progress=None):
    options=dict(options or {})
    allowed={"width","height","fps","video_codec","audio_codec","crf","bitrate","audio_bitrate","range","sequence_id","preset","profile","mode"}
    if set(options)-allowed:raise ValueError("unsupported export option: "+",".join(sorted(set(options)-allowed)))
    seq=next(s for s in project["sequences"] if s["id"]==options.get("sequence_id",project["active_sequence_id"]))
    profiles={"source":(seq["width"],seq["height"]),"youtube":(1920,1080),"reels":(1080,1920),"square":(1080,1080)}
    if options.get("profile","source") not in profiles:raise ValueError("unknown export profile")
    dw,dh=profiles[options.get("profile","source")]
    width=int(number(options.get("width",dw),minimum=16,maximum=8192))
    height=int(number(options.get("height",dh),minimum=16,maximum=8192))
    if width%2 or height%2:raise ValueError("export dimensions must be even")
    fps=rate(options.get("fps",seq["fps"]))
    codec=options.get("video_codec","libx264");audio_codec=options.get("audio_codec","aac")
    if codec not in {"libx264","libx265","h264_nvenc","hevc_nvenc","av1_nvenc","libvpx-vp9","prores_ks"}:raise ValueError("unsupported video codec")
    if audio_codec not in {"aac","libopus","pcm_s16le"}:raise ValueError("unsupported audio codec")
    output_path=Path(output_path).resolve();root=Path(root).resolve()
    if not output_path.is_relative_to(root) or output_path.suffix.lower() not in {".mp4",".mov",".mkv",".webm"}:raise ValueError("export must use an owned output path and supported container")
    if output_path.exists():raise ValueError("export path already exists; use a new immutable artifact")
    output_path.parent.mkdir(parents=True,exist_ok=True)
    if options.get("mode") == "stream_copy":
        return await stream_copy(project,root,output_path,options,progress)
    if options.get("mode","render")!="render":raise ValueError("unknown export mode")
    if progress:await progress("compiling",{"revision":project["revision"]})
    with tempfile.TemporaryDirectory(prefix="render-",dir=root) as td:
        compiler=Compiler(project,MediaLibrary(root),Path(td))
        v,a,duration=await blocking(compiler.sequence,seq["id"])
        start,end=0,duration
        if options.get("range"):
            start,end=seconds(options["range"]["start"]),seconds(options["range"]["end"])
            if not 0<=start<end<=duration:raise ValueError("export range outside sequence")
        v=compiler.node([v],f"trim=start={fmt(start)}:end={fmt(end)},setpts=PTS-STARTPTS,scale={width}:{height}:flags=lanczos,setsar=1,fps={fps},format=yuv420p")
        a=compiler.node([a],f"atrim=start={fmt(start)}:end={fmt(end)},asetpts=PTS-STARTPTS")
        graph=Path(td)/"graph.txt";graph.write_text(";\n".join(compiler.graph),encoding="utf-8")
        partial=Path(td)/("output"+output_path.suffix)
        argv=[binary("ffmpeg"),"-hide_banner","-loglevel","warning","-nostdin","-y",*compiler.inputs,
            "-filter_complex_script",str(graph),"-filter_complex_threads","2","-map",f"[{v}]","-map",f"[{a}]",
            "-t",fmt(end-start),"-c:v",codec,"-c:a",audio_codec,"-ar","48000","-ac","2","-color_primaries","bt709","-color_trc","bt709","-colorspace","bt709"]
        if codec in {"libx264","libx265"}:
            preset=options.get("preset","veryfast")
            if preset not in {"ultrafast","superfast","veryfast","faster","fast","medium","slow"}:raise ValueError("invalid encoder preset")
            argv += ["-preset",preset,"-crf",fmt(number(options.get("crf",20),minimum=0,maximum=51))]
        elif codec.endswith("nvenc"):
            argv += ["-preset","p4","-cq",fmt(number(options.get("crf",20),minimum=0,maximum=51))]
        for key,flag in [("bitrate","-b:v"),("audio_bitrate","-b:a")]:
            if key in options:
                value=options[key]
                if not isinstance(value,str) or not re.fullmatch(r"[1-9][0-9]{0,7}[kM]?",value):raise ValueError("invalid bitrate")
                argv += [flag,value]
        if output_path.suffix in {".mp4",".mov"}:argv += ["-movflags","+faststart"]
        argv += ["-progress","pipe:1","-nostats",str(partial)]
        await process(argv,progress=progress,stage="rendering",timeout=24*3600)
        if progress:await progress("verifying",{})
        verification=await verify_output(partial,{"width":width,"height":height,"has_video":True,"has_audio":True,
            "duration_ticks":round((end-start)*TICKS),"fps":{"num":fps.numerator,"den":fps.denominator}})
        if not verification["passed"]:raise ValueError("export failed independent verification: "+", ".join(verification["failures"]))
        # Exclusive publication prevents a concurrent export from replacing an artifact.
        publish(partial,output_path)
        profile={"width":width,"height":height,"fps":{"num":fps.numerator,"den":fps.denominator},"video_codec":codec,"audio_codec":audio_codec,"sample_rate":48000,"channels":2,"colorspace":"bt709","range":{"start":round(start*TICKS),"end":round(end*TICKS)}}
        if progress:await progress("complete",{"path":str(output_path),"verification":verification})
        return {"path":str(output_path),"verification":verification,"profile":profile}
