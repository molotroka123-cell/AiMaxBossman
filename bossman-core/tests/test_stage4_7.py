from pathlib import Path
from bossman.resource_brain import *
from bossman.search_everything import *
from bossman.remote_client import DeviceRegistry
from bossman.video_factory import VideoFactory
def test_resource_admission():
    s=ResourceSnapshot(1000,500,10000,8000); b=ResourceBrain(max_ram_pressure=.8,disk_reserve=1000)
    assert b.admit(s,WorkloadRequest(estimated_ram=100,estimated_disk=10)).allowed
    assert not b.admit(s,WorkloadRequest(estimated_ram=400,estimated_disk=10)).allowed
def test_search_and_provenance():
    e=SearchEngine();e.upsert([SearchDocument("1","Bossman browser context memory","repo","p"),SearchDocument("2","unrelated","repo","p")])
    h=e.search("browser memory",project="p");assert h and h[0].document.source=="repo"
def test_device_revoke():
    r=DeviceRegistry();did,t=r.enroll("iphone",("chat",));assert r.verify(did,t,"chat");r.revoke(did);assert not r.verify(did,t)
def test_video_checkpoint(tmp_path):
    v=VideoFactory(tmp_path);j=v.create("x",["scene one"]);v.checkpoint_scene(j,"s001",status="complete",output="x.mp4")
    assert (tmp_path/j.id/"job.json").exists()
