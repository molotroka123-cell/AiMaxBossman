from types import SimpleNamespace
import asyncio
import json
import pytest
from bcc.features import tools_cloud
from bcc.v2 import terminal_control

@pytest.mark.parametrize('status', ['empty', 'error'])
def test_cloud_budget_includes_all_billed_statuses(tmp_path, monkeypatch, status):
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(tools_cloud, '_today', lambda: '2026-10-28')
    path = tmp_path / 'cloud_ask' / 'audit.jsonl'
    path.parent.mkdir()
    path.write_text(json.dumps({'ts':'2026-10-28T00:00:00Z','status':status,'cost_usd':1.25})+'\n', encoding='utf-8')
    assert asyncio.run(tools_cloud.budget_state(svc, None))['spent_usd'] == 1.25

def test_cloud_budget_not_truncated_by_2000_later_events(tmp_path, monkeypatch):
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(tools_cloud, '_today', lambda: '2026-10-28')
    path = tmp_path / 'cloud_ask' / 'audit.jsonl'
    path.parent.mkdir()
    first = json.dumps({'ts':'2026-10-28T00:00:00Z','status':'ok','cost_usd':1.25})+'\n'
    filler = json.dumps({'ts':'2026-10-28T01:00:00Z','status':'empty','cost_usd':0})+'\n'
    path.write_text(first+filler*2000, encoding='utf-8')
    assert asyncio.run(tools_cloud.budget_state(svc, None))['spent_usd'] == 1.25

def test_failed_job_attach_does_not_resume_process(monkeypatch):
    events=[]
    proc=SimpleNamespace(pid=123, _transport=object(), kill=lambda: events.append('kill'))
    monkeypatch.setattr(terminal_control, '_win_job_attach', lambda p: None)
    import psutil
    monkeypatch.setattr(psutil, 'Process', lambda pid: SimpleNamespace(resume=lambda: events.append('resume')))
    with pytest.raises(OSError):
        terminal_control._win_bind_and_resume(proc)
    assert events == ['kill']

@pytest.mark.asyncio
@pytest.mark.parametrize('role,path,allowed', [
 ('agent','/api/pc-agent/tasks/1/approve',False),
 ('agent','/api/pc-agent/resume',False),
 ('agent','/api/pc-agent/trust',False),
 ('agent','/api/pc-agent/self-modify',False),
 ('agent','/api/pc-agent/stop',True),
 ('owner','/api/pc-agent/tasks/1/approve',True),
])
async def test_pc_agent_owner_only_actions(role,path,allowed):
    from unittest.mock import AsyncMock
    from starlette.requests import Request
    from bcc.auth import AuthPrincipal
    from bcc.api import require_token
    principal=AuthPrincipal(role=role,agent_id='x' if role=='agent' else None,scopes=frozenset({'read'}))
    svc=SimpleNamespace(sessions=SimpleNamespace(get=AsyncMock(return_value=None)), settings=SimpleNamespace(legacy_token_auth=True), auth=SimpleNamespace(resolve_principal=lambda t:principal))
    request=Request({'type':'http','http_version':'1.1','method':'POST','scheme':'http','path':path,'raw_path':path.encode(),'query_string':b'','headers':[],'app':SimpleNamespace(state=SimpleNamespace(svc=svc)),'server':('test',80),'client':('127.0.0.1',1234)})
    if allowed:
        await require_token(request,x_bcc_token='synthetic')
    else:
        with pytest.raises(Exception) as exc:
            await require_token(request,x_bcc_token='synthetic')
        assert getattr(exc.value,'status',getattr(exc.value,'status_code',None)) == 403

def test_unknown_cloud_charge_blocks_further_daily_spend(tmp_path, monkeypatch):
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(tools_cloud, '_today', lambda: '2026-10-28')
    path = tmp_path / 'cloud_ask' / 'audit.jsonl'
    path.parent.mkdir()
    path.write_text(json.dumps({'ts':'2026-10-28T00:00:00Z','status':'billing_unknown','cost_usd':0})+'\n', encoding='utf-8')
    assert asyncio.run(tools_cloud.budget_state(svc, None))['remaining_usd'] == 0

@pytest.mark.parametrize('bad_row', ['{broken', '[]', '{"ts":"2026-10-28T00:00:00Z","cost_usd":-1}', '{"ts":"2026-10-28T00:00:00Z","cost_usd":NaN}'])
def test_cloud_budget_corrupt_audit_fails_closed(tmp_path, monkeypatch, bad_row):
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path))
    path = tmp_path / 'cloud_ask' / 'audit.jsonl'
    path.parent.mkdir()
    path.write_text(bad_row + '\n', encoding='utf-8')
    monkeypatch.setattr(tools_cloud, '_today', lambda: '2026-10-28')
    assert asyncio.run(tools_cloud.budget_state(svc, None))['remaining_usd'] == 0

def test_cloud_budget_unreadable_audit_fails_closed(tmp_path, monkeypatch):
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path))
    path = tmp_path / 'cloud_ask' / 'audit.jsonl'
    path.mkdir(parents=True)
    assert asyncio.run(tools_cloud.budget_state(svc, None))['remaining_usd'] == 0

def test_cloud_budget_missing_audit_allows_first_use(tmp_path):
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path))
    assert asyncio.run(tools_cloud.budget_state(svc, None))['remaining_usd'] > 0

def test_paid_cloud_missing_usage_is_not_a_zero_or_estimated_actual_cost():
    result = SimpleNamespace()
    assert tools_cloud._actual_cost({'paid': True, 'est_cost_usd': 0.3, 'price_in_1m': 1, 'price_out_1m': 2}, result) is None

@pytest.mark.parametrize('rollback_ok', [False, True])
def test_self_modify_health_reports_actual_rollback_result(tmp_path, monkeypatch, rollback_ok):
    import time
    from bcc.features import pc_agent
    root=tmp_path / 'selfmods'
    mod=root / 'sm-test'
    mod.mkdir(parents=True)
    (mod / 'proposal.json').write_text(json.dumps({'status':'applied', 'applied_at':time.time()}), encoding='utf-8')
    rt=SimpleNamespace(self_mod_dir=root, is_stopped=lambda svc:False)
    original=pc_agent._execute_self_modify_step
    async def fake_nested(*args, **kwargs):
        assert kwargs['args']['action'] == 'rollback'
        return {'ok':rollback_ok, 'verified':rollback_ok}
    monkeypatch.setattr(pc_agent, '_execute_self_modify_step', fake_nested)
    result=asyncio.run(original(None,rt,task_id=1,run_id=1,args={'action':'health_check','mod_id':'sm-test','simulate_unhealthy':True},actor='watchdog',approved=True,t0=time.perf_counter()))
    assert result['ok'] is rollback_ok
    assert result['verified'] is rollback_ok
    assert result['auto_rolled_back'] is rollback_ok
