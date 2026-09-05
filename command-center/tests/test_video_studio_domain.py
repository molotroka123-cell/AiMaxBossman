"""Editing mathematics and real transactional collaboration/history invariants."""
from copy import deepcopy
from fractions import Fraction
import asyncio

import pytest

from bcc.db import Database
from bcc.video_studio.commands import apply_command, interpolate
from bcc.video_studio.model import (Conflict, EditLocked, MissingObject, StudioError, TICKS,
    clip, clip_duration, migrate, new_project, sequence_duration, validate_project)
from bcc.video_studio.store import ProjectStore


def document():
    p = new_project("project", "Тест")
    p["media"]["media"] = {"id":"media", "name":"fixture.mp4", "relative_path":"media/fixture.mp4",
        "sha256":"0"*64, "bytes":200, "duration_ticks":10*TICKS, "has_video":True,"has_audio":True,
        "width":320,"height":180,"fps":{"num":25,"den":1},"sample_rate":48000,"channels":2}
    tr = p["sequences"][0]["tracks"][0]
    p, _, _ = apply_command(p, {"type":"clip.add","track_id":tr["id"],"clip":{
        "id":"first","media_id":"media","start":0,"source_in":0,"source_out":4*TICKS}})
    return p


def apply(p, command_type, **kw):
    return apply_command(p, {"type":command_type, **kw})[0]


def test_precise_ticks_and_speed_have_no_accumulating_float_drift():
    c = clip(document(),"first")[2]
    c.update(source_in=0, source_out=1001000, speed={"num":1001,"den":1000})
    assert clip_duration(c) == 1000000
    assert sum(clip_duration(c) for _ in range(10000)) == 10000000000


def test_split_preserves_source_and_timeline_coverage_without_mutation():
    p = document()
    before = deepcopy(p)
    q = apply(p,"clip.split",clip_id="first",at=TICKS)
    clips = q["sequences"][0]["tracks"][0]["clips"]
    assert p == before
    assert [(c["start"], c["source_in"],c["source_out"]) for c in clips] == [(0,0,TICKS),(TICKS,TICKS,4*TICKS)]
    assert sequence_duration(q["sequences"][0]) == 4*TICKS
    assert len({c["id"] for c in clips}) == 2


def test_reverse_split_preserves_reversed_playback_order():
    p = apply(document(),"clip.reverse",clip_id="first")
    q = apply(p,"clip.split",clip_id="first",at=TICKS)
    clips=q["sequences"][0]["tracks"][0]["clips"]
    assert [(c["source_in"],c["source_out"]) for c in clips]==[(3*TICKS,4*TICKS),(0,3*TICKS)]


@pytest.mark.parametrize("bad",[-1,True,1.5,10**18])
def test_invalid_timeline_values_rejected(bad):
    with pytest.raises(StudioError):
        apply(document(),"clip.move",clip_id="first",start=bad)


@pytest.mark.parametrize("at",[0,4*TICKS,5*TICKS])
def test_split_outside_interior_rejected(at):
    with pytest.raises(StudioError):
        apply(document(),"clip.split",clip_id="first",at=at)


def two_clips():
    p=document(); tr=p["sequences"][0]["tracks"][0]
    return apply(p,"clip.add",track_id=tr["id"],clip={"id":"second","media_id":"media","start":4*TICKS,
                                                       "source_in":4*TICKS,"source_out":8*TICKS})


def test_ripple_trim_and_delete_preserve_downstream_order():
    p=apply(two_clips(),"clip.trim",clip_id="first",source_out=3*TICKS,ripple=True)
    assert clip(p,"second")[2]["start"]==3*TICKS
    p=apply(p,"clip.remove",clip_id="first",ripple=True)
    assert clip(p,"second")[2]["start"]==0
    assert sequence_duration(p["sequences"][0])==4*TICKS


def test_roll_preserves_total_duration_and_adjacency():
    p=apply(two_clips(),"clip.roll",clip_id="first",right_clip_id="second",delta=TICKS)
    a,b=clip(p,"first")[2],clip(p,"second")[2]
    assert a["source_out"]==b["source_in"]==5*TICKS
    assert b["start"]==5*TICKS
    assert sequence_duration(p["sequences"][0])==8*TICKS


def test_slip_preserves_start_duration_and_shifts_content():
    p=apply(document(),"clip.slip",clip_id="first",delta=TICKS)
    c=clip(p,"first")[2]
    assert (c["start"],clip_duration(c),c["source_in"])==(0,4*TICKS,TICKS)


def test_slide_changes_neighbors_without_changing_program_duration():
    p=document();tr=p["sequences"][0]["tracks"][0]
    p=apply(p,"clip.trim",clip_id="first",source_out=2*TICKS)
    for key,start,end in [("middle",2,4),("last",4,8)]:
        p=apply(p,"clip.add",track_id=tr["id"],clip={"id":key,"media_id":"media","start":start*TICKS,
            "source_in":start*TICKS,"source_out":end*TICKS})
    p=apply(p,"clip.slide",clip_id="middle",delta=TICKS)
    assert clip(p,"middle")[2]["start"]==3*TICKS
    assert clip(p,"first")[2]["source_out"]==3*TICKS
    assert clip(p,"last")[2]["source_in"]==5*TICKS
    assert sequence_duration(p["sequences"][0])==8*TICKS


def test_locked_track_and_unknown_clip_fail_without_partial_batch():
    p=document();before=deepcopy(p)
    with pytest.raises(MissingObject):
        apply(p,"timeline.apply",operations=[{"type":"project.rename","name":"changed"},
            {"type":"clip.trim","clip_id":"absent","source_out":TICKS}])
    assert p==before
    p=apply(p,"track.update",track_id=p["sequences"][0]["tracks"][0]["id"],patch={"locked":True})
    with pytest.raises(EditLocked):
        apply(p,"clip.trim",clip_id="first",source_out=TICKS)


def test_group_move_and_detached_audio_share_motion():
    p=document();audio=p["sequences"][0]["tracks"][1]
    p=apply(p,"clip.detach_audio",clip_id="first",track_id=audio["id"])
    p=apply(p,"clip.move",clip_id="first",start=2*TICKS)
    assert p["sequences"][0]["tracks"][1]["clips"][0]["start"]==2*TICKS
    assert clip(p,"first")[2]["audio_disabled"] is True


def test_speed_ramp_has_exact_segment_duration_and_rejects_gaps():
    p=apply(document(),"clip.speed_ramp",clip_id="first",segments=[
        {"source_in":0,"source_out":2*TICKS,"speed":{"num":1,"den":1}},
        {"source_in":2*TICKS,"source_out":4*TICKS,"speed":{"num":2,"den":1}}])
    assert clip_duration(clip(p,"first")[2])==3*TICKS
    with pytest.raises(StudioError):
        apply(document(),"clip.speed_ramp",clip_id="first",segments=[
            {"source_in":1,"source_out":4*TICKS,"speed":{"num":1,"den":1}}])


@pytest.mark.parametrize("easing,expected",[("linear",.5),("hold",0),("ease_in",.25),("ease_out",.75),("ease_in_out",.5)])
def test_numeric_easing(easing,expected):
    assert interpolate([{"t":0,"value":0,"easing":easing},{"t":100,"value":1}],50)==expected


def test_split_keyframes_preserves_value_at_cut():
    p=apply(document(),"keyframe.set",clip_id="first",param="x",t=0,value=0)
    p=apply(p,"keyframe.set",clip_id="first",param="x",t=4*TICKS,value=100)
    p=apply(p,"clip.split",clip_id="first",at=2*TICKS)
    a,b=p["sequences"][0]["tracks"][0]["clips"]
    assert a["keyframes"]["x"][-1]["value"]==b["keyframes"]["x"][0]["value"]==50


def test_nested_sequence_cycle_and_future_schema_rejected():
    p=document();seq=p["sequences"][0]
    with pytest.raises(StudioError):
        apply(p,"clip.add",track_id=seq["tracks"][0]["id"],clip={"nested_sequence_id":seq["id"],
            "start":4*TICKS,"source_in":0,"source_out":TICKS})
    p["schema_version"]=999
    with pytest.raises(StudioError): migrate(p)


def test_transcript_cut_only_keeps_requested_source_ranges():
    p=apply(document(),"transcript.cut",clip_id="first",ranges=[{"start":0,"end":TICKS},{"start":3*TICKS,"end":4*TICKS}])
    values=p["sequences"][0]["tracks"][0]["clips"]
    assert [(c["start"],c["source_in"],c["source_out"]) for c in values]==[(0,0,TICKS),(TICKS,3*TICKS,4*TICKS)]


@pytest.fixture
async def store(tmp_path):
    db=Database(f"sqlite+aiosqlite:///{tmp_path/'studio.sqlite'}")
    await db.create_all()
    result=ProjectStore(db)
    await result.create("project","Initial","create")
    yield result
    await db.close()


async def test_cas_replay_and_operation_identity(store):
    a=await store.apply("project",0,"rename",{"type":"project.rename","name":"Renamed"})
    assert a["revision"]==1
    assert await store.apply("project",0,"rename",{"type":"project.rename","name":"Renamed"})==a
    with pytest.raises(Conflict):
        await store.apply("project",0,"other",{"type":"project.rename","name":"Old"})
    with pytest.raises(Conflict):
        await store.apply("project",0,"rename",{"type":"project.rename","name":"Different"})
    assert (await store.get("project"))["name"]=="Renamed"


async def test_dryrun_preserves_revision_and_operation_id(store):
    result=await store.apply("project",0,"op",{"type":"project.rename","name":"Preview"},dry_run=True)
    assert result["project"]["name"]=="Preview" and result["revision"]==0
    assert (await store.get("project"))["name"]=="Initial"
    assert len(await store.history("project"))==1
    assert (await store.apply("project",0,"op",{"type":"project.rename","name":"Preview"}))["revision"]==1


async def test_undo_redo_agent_edit_and_branch_invalidation(store):
    await store.apply("project",0,"agent_op",{"type":"project.rename","name":"Agent changed"},actor="agent:1")
    undo=await store.apply("project",1,"undo",{"type":"history.undo"})
    assert undo["project"]["name"]=="Initial" and undo["revision"]==2
    redo=await store.apply("project",2,"redo",{"type":"history.redo"})
    assert redo["project"]["name"]=="Agent changed" and redo["revision"]==3
    await store.apply("project",3,"undo2",{"type":"history.undo"})
    await store.apply("project",4,"newbranch",{"type":"project.rename","name":"New branch"})
    with pytest.raises(Conflict): await store.apply("project",5,"redo2",{"type":"history.redo"})


async def test_human_lease_blocks_agent_without_consuming_revision(store):
    await store.lease("project",["project"],actor="human")
    with pytest.raises(EditLocked):
        await store.apply("project",0,"agent_op",{"type":"project.rename","name":"Agent"},actor="agent:1")
    assert (await store.get("project"))["revision"]==0
    await store.release_lease("project","human")
    assert (await store.apply("project",0,"agent_op",{"type":"project.rename","name":"Agent"},actor="agent:1"))["revision"]==1


async def test_reopen_database_retains_history_and_idempotency(tmp_path):
    url=f"sqlite+aiosqlite:///{tmp_path/'restart.sqlite'}"
    first=Database(url);await first.create_all();st=ProjectStore(first)
    await st.create("p","Original","create")
    result=await st.apply("p",0,"op",{"type":"project.rename","name":"Durable"})
    await first.close()
    second=Database(url);await second.create_all();st=ProjectStore(second)
    assert await st.apply("p",0,"op",{"type":"project.rename","name":"Durable"})==result
    assert len(await st.history("p"))==2
    await st.apply("p",1,"undo",{"type":"history.undo"})
    assert (await st.get("p"))["name"]=="Original"
    await second.close()


async def test_concurrent_writers_cannot_lose_an_update(store):
    results=await asyncio.gather(*(store.apply("project",0,"op"+str(i),{"type":"project.rename","name":str(i)})
                                   for i in range(2)),return_exceptions=True)
    assert sum(isinstance(x,dict) for x in results)==1
    assert sum(isinstance(x,Conflict) for x in results)==1
    assert (await store.get("project"))["revision"]==1


async def test_project_lease_covers_child_objects_and_track_lease_covers_clips(store):
    p=await store.get("project")
    track_id=p["sequences"][0]["tracks"][0]["id"]
    await store.lease("project",["project"],actor="human")
    with pytest.raises(EditLocked):
        await store.apply("project",0,"agent",{"type":"track.update","track_id":track_id,"patch":{"mute":True}},actor="agent:1")
    await store.release_lease("project","human")
    await store.apply("project",0,"import",{"type":"project.import","project":document()})
    p=await store.get("project");track_id=p["sequences"][0]["tracks"][0]["id"]
    await store.lease("project",[track_id],actor="human")
    with pytest.raises(EditLocked):
        await store.apply("project",1,"move",{"type":"clip.move","clip_id":"first","start":TICKS},actor="agent:1")


async def test_duplicate_is_complete_atomic_and_idempotent(store):
    await store.apply("project",0,"import",{"type":"project.import","project":document()})
    dup=await store.duplicate("project","copy","Copy","copy_op",expected_revision=1)
    assert dup["project"]["id"]=="copy" and clip(dup["project"],"first")[2]["media_id"]=="media"
    assert len(await store.history("copy"))==1
    assert await store.duplicate("project","copy","Copy","copy_op",expected_revision=1)==dup
    with pytest.raises(Conflict):
        await store.duplicate("project","different","Old","other_op",expected_revision=0)
    with pytest.raises(MissingObject):await store.get("different")


def test_adjustment_is_timed_without_fake_media():
    p=apply(document(),"track.add",kind="adjustment",id="fx")
    p=apply(p,"clip.add",track_id="fx",clip={"id":"layer","adjustment":True,"start":TICKS,"freeze_duration":2*TICKS})
    assert clip_duration(clip(p,"layer")[2])==2*TICKS


def test_training_and_holdout_gold_commands_are_valid_real_domain_operations():
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location("video_training_recipe",Path(__file__).resolve().parents[2]/"tools/video_studio/train_adapter.py")
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    training,holdout=module.corpus(),module.corpus(True)
    assert not {x["input"] for x in training}&{x["input"] for x in holdout}
    for row in training+holdout:
        p=document()
        p["sequences"][0]["tracks"][0]["clips"][0]["id"]=row["command"]["clip_id"]
        q,changed,_=apply_command(p,row["command"])
        validate_project(q)
        assert row["command"]["clip_id"] in changed


async def test_concurrent_edit_leases_have_one_owner(store):
    results=await asyncio.gather(*(store.lease("project",["project"],actor=f"editor:{n}")
                                 for n in range(2)),return_exceptions=True)
    assert sum(isinstance(x,dict) for x in results)==1
    assert sum(isinstance(x,EditLocked) for x in results)==1


async def test_child_lease_blocks_parent_change_and_parent_lease(store):
    await store.apply("project",0,"import",{"type":"project.import","project":document()})
    tr=(await store.get("project"))["sequences"][0]["tracks"][0]
    await store.lease("project",["first"],actor="human")
    with pytest.raises(EditLocked):
        await store.lease("project",[tr["id"]],actor="agent:1")
    with pytest.raises(EditLocked):
        await store.apply("project",1,"mute",{"type":"track.update","track_id":tr["id"],"patch":{"mute":True}},actor="agent:1")
    assert (await store.get("project"))["revision"]==1


def test_output_fps_rejects_unrenderable_project_at_admission():
    p=document()
    with pytest.raises(StudioError,match="240"):
        apply(p,"sequence.update",sequence_id=p["active_sequence_id"],patch={"fps":{"num":1000,"den":1}})
