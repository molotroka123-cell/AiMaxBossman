"""OpenTimelineIO interchange, with explicit preservation/loss boundaries.

OTIO is an interchange library, not a renderer. Foreign external references are
never opened automatically; all media still needs host import/relink validation.
"""
from copy import deepcopy
from collections.abc import Mapping, Sequence
import json

from .model import TICKS, clip_duration, identity, new_project, new_track, sequence, uid, validate_project


def _plain(value):
    """OTIO's nested AnyDictionary/AnyVector are not JSON encoder primitives."""
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_plain(item) for item in value]
    return deepcopy(value)


def available():
    try:
        import opentimelineio
        return {"available":True,"version":opentimelineio.__version__,"formats":["otio"],
                "render_backend":False}
    except ImportError:
        return {"available":False,"reason":"Install the video-interchange extra (OpenTimelineIO)"}


def export_otio(project):
    import opentimelineio as otio
    validate_project(project)
    seq=sequence(project)
    timeline=otio.schema.Timeline(name=project["name"])
    timeline.metadata["bossman"]={"schema_version":1,"sequence":{k:deepcopy(v) for k,v in seq.items() if k!="tracks"},
                                  "project_id":project["id"],"markers":project["markers"],"captions":project["captions"]}
    warnings=[]
    for tr in seq["tracks"]:
        # One OTIO layer per clip preserves overlap exactly. Original track ID
        # groups these layers again on import; no flattening loses simultaneous clips.
        for c in sorted(tr["clips"],key=lambda x:(x["start"],x["id"])):
            track=otio.schema.Track(name=tr["name"],kind=otio.schema.TrackKind.Audio if tr["kind"]=="audio" else otio.schema.TrackKind.Video)
            track.metadata["bossman_track"]={k:deepcopy(v) for k,v in tr.items() if k!="clips"}
            if c["start"]:
                track.append(otio.schema.Gap(source_range=otio.opentime.TimeRange(
                    otio.opentime.RationalTime(0,TICKS),otio.opentime.RationalTime(c["start"],TICKS))))
            media=project["media"].get(c.get("media_id"))
            if media:
                reference=otio.schema.ExternalReference(target_url=media["relative_path"],
                    available_range=otio.opentime.TimeRange(otio.opentime.RationalTime(0,TICKS),
                                                           otio.opentime.RationalTime(media["duration_ticks"],TICKS)))
                reference.metadata["bossman_media"]=deepcopy(media)
            else:
                reference=otio.schema.MissingReference()
                warnings.append(f"{c['id']}: title/nested/adjustment semantics require Bossman metadata")
            item=otio.schema.Clip(name=media["name"] if media else c.get("title",{}).get("text",c["id"]),
                                  media_reference=reference,source_range=otio.opentime.TimeRange(
                    otio.opentime.RationalTime(c.get("source_in",0),TICKS),otio.opentime.RationalTime(clip_duration(c),TICKS)))
            item.metadata["bossman_clip"]=deepcopy(c)
            if c.get("effects") or c.get("keyframes") or c.get("speed_ramp") or c.get("reverse"):
                warnings.append(f"{c['id']}: effects/keyframes/retiming preserved as metadata; foreign editors may ignore them")
            track.append(item)
            timeline.tracks.append(track)
    timeline.metadata["bossman"]["all_sequences"]=deepcopy(project["sequences"])
    timeline.metadata["bossman"]["all_media"]=deepcopy(project["media"])
    text=otio.adapters.write_to_string(timeline,adapter_name="otio_json")
    return {"data":text.encode("utf-8"),"filename":project["id"]+".otio","warnings":list(dict.fromkeys(warnings)),
            "interchange":"OpenTimelineIO", "parity_claim":False}


def import_otio(data, project_id, name=None):
    import opentimelineio as otio
    if not isinstance(data,(str,bytes)) or len(data)>32*1024*1024:
        raise ValueError("OTIO document exceeds the 32 MiB metadata limit")
    # Native JSON only: do not invoke third-party adapter plugins from input.
    timeline=otio.adapters.read_from_string(data.decode("utf-8") if isinstance(data,bytes) else data,adapter_name="otio_json")
    if not isinstance(timeline,otio.schema.Timeline):
        raise ValueError("Expected an OTIO Timeline")
    p=new_project(project_id,name or timeline.name or "OTIO import")
    metadata=_plain(timeline.metadata)
    boss=metadata.get("bossman",{})
    if not boss:
        raise ValueError("Foreign OTIO media references require explicit mapping to imported media IDs")
    seq=p["sequences"][0]
    seq.update(deepcopy(boss["sequence"]));seq["tracks"]=[]
    p["active_sequence_id"]=seq["id"]
    if boss.get("all_sequences"):
        p["sequences"]=[seq]+[s for s in deepcopy(boss["all_sequences"]) if s["id"]!=seq["id"]]
    p["media"]=deepcopy(boss.get("all_media",{}))
    tracks={}
    for layer in timeline.tracks:
        trmeta=_plain(layer.metadata).get("bossman_track")
        if not trmeta:
            raise ValueError("Foreign tracks need explicit media mapping")
        tid=identity(trmeta["id"])
        if tid not in tracks:
            tr=deepcopy(trmeta);tr["clips"]=[];tracks[tid]=tr;seq["tracks"].append(tr)
        for item in layer:
            if isinstance(item,otio.schema.Gap):continue
            if not isinstance(item,otio.schema.Clip):raise ValueError("Unsupported OTIO composition type")
            cm=_plain(item.metadata).get("bossman_clip")
            if not cm:raise ValueError("Clip identity metadata is missing")
            c=deepcopy(cm)
            parent_range=layer.range_of_child(item)
            c["start"]=round(parent_range.start_time.to_seconds()*TICKS)
            # Canonical retiming metadata takes precedence for nontrivial speed;
            # ordinary external trim edits are faithfully imported as new in/out.
            if not (c.get("speed_ramp") or c.get("freeze") or c.get("reverse") or c.get("title") or c.get("adjustment")) and c.get("speed",{})=={"num":1,"den":1}:
                c["source_in"]=round(item.source_range.start_time.to_seconds()*TICKS)
                c["source_out"]=c["source_in"]+round(item.source_range.duration.to_seconds()*TICKS)
            rm=_plain(item.media_reference.metadata).get("bossman_media")
            if rm:
                p["media"][identity(rm["id"])]=rm
            tracks[tid]["clips"].append(c)
    # Preserve empty tracks, and original track order, from our round-trip metadata.
    original=next((s for s in boss.get("all_sequences",[]) if s["id"]==seq["id"]),{})
    seq["tracks"]=[tracks.pop(tr["id"],{**deepcopy(tr),"clips":[]}) for tr in original.get("tracks",[])] + list(tracks.values())
    p["markers"]=boss.get("markers",[]);p["captions"]=boss.get("captions",[])
    validate_project(p)
    return {"project":p,"warnings":["Media references have not been opened; host must validate/import/relink them before applying this project"],
            "requires_media_validation":True}
