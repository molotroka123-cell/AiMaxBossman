"""Real OpenTimelineIO serialization/foreign edit boundaries (optional extra)."""
from copy import deepcopy
import pytest

otio = pytest.importorskip("opentimelineio", reason="install video-interchange extra")
from bcc.video_studio.commands import apply_command
from bcc.video_studio.interchange import export_otio, import_otio
from bcc.video_studio.model import TICKS, new_project, clip, validate_project


def document():
    p=new_project("interchange","Кино & music")
    p["media"]["source"]={"id":"source","name":"clip.mp4","relative_path":"media/clip.mp4",
        "sha256":"0"*64,"bytes":200,"duration_ticks":10*TICKS,"has_video":True,"has_audio":True,
        "width":320,"height":180,"fps":{"num":25,"den":1},"sample_rate":48000,"channels":2}
    tr=p["sequences"][0]["tracks"][0]
    for ident,start in [("first",TICKS),("overlap",2*TICKS)]:
        p=apply_command(p,{"type":"clip.add","track_id":tr["id"],"clip":{
            "id":ident,"media_id":"source","start":start,"source_in":TICKS,"source_out":4*TICKS}})[0]
    return p


def test_real_otio_roundtrip_preserves_overlap_and_empty_audio_track():
    p=document(); before=deepcopy(p)
    out=export_otio(p)
    assert out["data"].startswith(b"{") and not out["parity_claim"]
    q=import_otio(out["data"],"imported")["project"]
    assert p==before
    assert q["sequences"]==p["sequences"] and q["media"]==p["media"]
    assert q["id"]=="imported"


def test_external_otio_trim_is_imported_at_exact_time():
    p=document(); timeline=otio.adapters.read_from_string(export_otio(p)["data"].decode(),"otio_json")
    first=timeline.tracks[0][1]
    first.source_range=otio.opentime.TimeRange(otio.opentime.RationalTime(2*TICKS,TICKS),
                                             otio.opentime.RationalTime(TICKS,TICKS))
    q=import_otio(otio.adapters.write_to_string(timeline,"otio_json"),"imported")["project"]
    c=clip(q,"first")[2]
    assert (c["start"],c["source_in"],c["source_out"])==(TICKS,2*TICKS,3*TICKS)


def test_foreign_media_is_never_opened_or_trusted():
    foreign=otio.schema.Timeline(name="unmapped")
    with pytest.raises(ValueError,match="explicit mapping"):
        import_otio(otio.adapters.write_to_string(foreign,"otio_json"),"imported")


def test_retiming_and_effects_are_explicitly_preserved_not_parity_claimed():
    p=document()
    p=apply_command(p,{"type":"clip.reverse","clip_id":"first"})[0]
    out=export_otio(p)
    assert out["warnings"] and not out["parity_claim"]
    q=import_otio(out["data"],"imported")["project"]
    assert q["sequences"]==p["sequences"]


def test_inactive_sequence_and_unused_media_are_preserved():
    p=document()
    p=apply_command(p,{"type":"sequence.duplicate","sequence_id":p["active_sequence_id"],"name":"Nested"})[0]
    p["media"]["unused"]={**p["media"]["source"],"id":"unused"}
    validate_project(p)
    q=import_otio(export_otio(p)["data"],"imported")["project"]
    assert q["sequences"]==p["sequences"] and q["media"]==p["media"]


def test_oversized_metadata_is_rejected_before_adapter():
    with pytest.raises(ValueError,match="32 MiB"):
        import_otio(b" "*(32*1024*1024+1),"imported")
