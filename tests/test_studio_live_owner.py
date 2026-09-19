import importlib.util
from pathlib import Path
import pytest

PATH=Path(__file__).parents[1]/'tools/studio_live_owner.py'
def load():
    spec=importlib.util.spec_from_file_location('studio_live_owner',PATH)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod

def test_free_policy_requires_explicit_zero_and_two_surfaces():
    m=load()
    ids=['openrouter:google/gemini-3.1-flash-image','openrouter:minimax/hailuo-3-max']
    p={'enabled':True,'free_only':True,'prices':dict.fromkeys(ids,0),'cloud_budget_usd':0,'per_job_usd':0}
    assert m.validate_free_policy(p,ids)==p
    for bad in ({**p,'free_only':False},{**p,'prices':{}},{**p,'prices':dict.fromkeys(ids,0.1)}):
        with pytest.raises(ValueError):m.validate_free_policy(bad,ids)

def test_missing_key_owner_required_writes_binding(tmp_path,monkeypatch):
    m=load();monkeypatch.delenv('BOSSMAN_OPENROUTER_API_KEY',raising=False);monkeypatch.delenv('OPENROUTER_API_KEY',raising=False)
    out=tmp_path/'studio.json'
    assert m.main(['--output',str(out),'--expected-sha','a'*40])==2
    import json
    report=json.loads(out.read_text())
    assert report['status']=='OWNER_REQUIRED'
    assert report['binding']['source_sha']=='a'*40
    assert report['installed'] is False and not report['live'][0]['outputs']
