"""Local editable Shotcut/MLT exchange with an explicit, checked subset.

MLT XML can itself invoke plugins and open resources. We only *write* a fixed
allowlist graph from an already validated Bossman project; arbitrary MLT is never
executed by this adapter. Effects outside the subset fail instead of vanishing.
"""
from fractions import Fraction
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from .media import MediaLibrary
from .model import StudioError, TICKS, clip_duration, sequence, sequence_duration, validate_project


def _prop(node, key, value):
    ET.SubElement(node,"property",{"name":key}).text=str(value)


def _frame(ticks, fps):
    value=Fraction(ticks,TICKS)*fps
    return (value.numerator+value.denominator//2)//value.denominator


def _check_subset(project):
    seq=sequence(project)
    if project.get("captions"):
        raise StudioError("Shotcut editable export cannot preserve burned captions; export a verified rendered master instead")
    for tr in seq["tracks"]:
        if tr.get("effects") or tr.get("pan",0):
            raise StudioError("Shotcut editable export does not translate track effects/pan; use a verified rendered master")
        previous_end=0
        for c in sorted(tr["clips"],key=lambda v:(v["start"],v["id"])):
            if c["start"]<previous_end:
                raise StudioError("Shotcut editable export needs non-overlapping clips per track; move overlaps onto separate tracks or use a rendered master")
            previous_end=c["start"]+clip_duration(c)
            if (any(c.get(k) for k in ("title","adjustment","nested_sequence_id","effects","keyframes","reverse","freeze","speed_ramp"))
                    or c.get("speed",{"num":1,"den":1})!={"num":1,"den":1} or c.get("pan",0)):
                raise StudioError("Shotcut editable export supports cuts and volume; retiming/effects/nesting require a verified rendered master")
            transform=c.get("transform",{})
            defaults={"x":0,"y":0,"scale":1,"rotation":0,"opacity":1,"crop":[0,0,0,0]}
            if any(transform.get(k,v)!=v for k,v in defaults.items()):
                raise StudioError("Shotcut editable export does not translate transforms; use a verified rendered master")


def export_shotcut(project, media_root):
    """Return an editable .mlt for cuts, AV tracks, mute/solo, gain and source trims.

    References are local to this installation. For another computer export the
    portable Bossman package first; absolute paths are visible in the XML.
    """
    validate_project(project)
    _check_subset(project)
    seq=sequence(project); fps=Fraction(seq["fps"]["num"],seq["fps"]["den"])
    length=_frame(sequence_duration(seq),fps)
    if length<1:
        raise StudioError("Shotcut exchange requires at least one frame")
    root_path=Path(media_root).resolve()
    library=MediaLibrary(root_path)
    paths={mid:library.resolve(media) for mid,media in project["media"].items()}
    root=ET.Element("mlt",{"LC_NUMERIC":"C","version":"7.40.0","producer":"tractor",
                           "root":root_path.as_posix()})
    ET.SubElement(root,"profile",{"description":"Bossman Video Studio","width":str(seq["width"]),
        "height":str(seq["height"]),"frame_rate_num":str(fps.numerator),"frame_rate_den":str(fps.denominator),
        "progressive":"1","sample_aspect_num":"1","sample_aspect_den":"1",
        "display_aspect_num":str(seq["width"]),"display_aspect_den":str(seq["height"]),"colorspace":"709"})
    background=ET.SubElement(root,"producer",{"id":"black","in":"0","out":str(length-1)})
    _prop(background,"resource","0");_prop(background,"mlt_service","color")
    _prop(background,"mlt_image_format","rgba");_prop(background,"set.test_audio",1)
    playlist=ET.SubElement(root,"playlist",{"id":"background"})
    ET.SubElement(playlist,"entry",{"producer":"black","in":"0","out":str(length-1)})
    warnings=["Local absolute media references; keep source storage available or relink in Shotcut.",
              "Editable subset: cuts, source trims, AV tracks, mute/solo and static volume. No VEGAS/Shotcut feature parity claim."]
    playlists=[]; any_solo=any(t["solo"] for t in seq["tracks"])
    # Shotcut places audio tracks first, then video layers bottom to top.
    ordered=sorted(enumerate(seq["tracks"]),key=lambda item:(item[1]["kind"]!="audio",item[0]))
    for index,tr in ordered:
        pid=f"track_{index}"; visible=not tr["mute"] and (not any_solo or tr["solo"])
        entries=[];position=0
        for ci,c in enumerate(sorted(tr["clips"],key=lambda v:(v["start"],v["id"]))):
            producer_id=f"clip_{index}_{ci}"
            producer=ET.SubElement(root,"chain",{"id":producer_id})
            _prop(producer,"resource",paths[c["media_id"]].as_posix())
            _prop(producer,"mlt_service","avformat")
            _prop(producer,"eof","pause")
            _prop(producer,"seekable",1)
            _prop(producer,"bossman:clip_id",c["id"])
            _prop(producer,"bossman:media_id",c["media_id"])
            _prop(producer,"bossman:source_sha256",project["media"][c["media_id"]]["sha256"])
            if tr["kind"]=="audio" or c.get("audio_only"):
                _prop(producer,"video_index",-1)
            if c.get("audio_disabled"):
                _prop(producer,"audio_index",-1)
            gain=c.get("volume",1)*tr.get("volume",1)
            if gain==0:
                _prop(producer,"audio_index",-1)
            elif gain!=1:
                filter=ET.SubElement(producer,"filter")
                _prop(filter,"mlt_service","volume");_prop(filter,"level",20*math.log10(gain))
                _prop(filter,"shotcut:filter","volume")
            start=_frame(c["start"],fps); source_in=_frame(c["source_in"],fps)
            frames=_frame(c["start"]+clip_duration(c),fps)-start
            if frames<1:
                raise StudioError("A clip is shorter than one output frame")
            if start>position:entries.append(("blank",{"length":str(start-position)}))
            entries.append(("entry",{"producer":producer_id,"in":str(source_in),"out":str(source_in+frames-1)}))
            position=start+frames
            if any(Fraction(v,TICKS)*fps % 1 for v in (c["start"],c["source_in"],c["source_out"])):
                warnings.append(f"{c['id']}: subframe time rounded to nearest sequence frame for MLT")
        playlist=ET.SubElement(root,"playlist",{"id":pid})
        _prop(playlist,"shotcut:name",tr["name"])
        _prop(playlist,"shotcut:trackHeight",64)
        _prop(playlist,"bossman:track_id",tr["id"])
        _prop(playlist,"shotcut:trackLock",1 if tr["locked"] else 0)
        if tr["kind"]=="audio":_prop(playlist,"shotcut:trackAudio",1)
        for tag,attrs in entries:ET.SubElement(playlist,tag,attrs)
        playlists.append((pid,tr,visible))
    tractor=ET.SubElement(root,"tractor",{"id":"tractor","shotcut":"1","in":"0","out":str(length-1)})
    _prop(tractor,"shotcut:name",project["name"])
    _prop(tractor,"shotcut:projectAudioChannels",2)
    _prop(tractor,"bossman:project_id",project["id"])
    _prop(tractor,"bossman:revision",project["revision"])
    ET.SubElement(tractor,"track",{"producer":"background"})
    for i,(pid,tr,visible) in enumerate(playlists,1):
        attrs={"producer":pid}
        if not visible:attrs["hide"]="both"
        elif tr["kind"]=="audio":attrs["hide"]="video"
        ET.SubElement(tractor,"track",attrs)
        mix=ET.SubElement(tractor,"transition",{"in":"0","out":str(length-1)})
        for key,value in (("mlt_service","mix"),("a_track",0),("b_track",i),("always_active",1),("sum",1)):
            _prop(mix,key,value)
        if tr["kind"]!="audio":
            comp=ET.SubElement(tractor,"transition",{"in":"0","out":str(length-1)})
            for key,value in (("mlt_service","qtblend"),("a_track",0),("b_track",i)):
                _prop(comp,key,value)
    ET.indent(root)
    return {"data":ET.tostring(root,encoding="utf-8",xml_declaration=True),
            "filename":project["id"]+".mlt","warnings":list(dict.fromkeys(warnings)),
            "interchange":"Shotcut/MLT","parity_claim":False,"frames":length}
