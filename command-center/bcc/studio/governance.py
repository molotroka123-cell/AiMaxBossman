"""Persistent per-feature reservations under the existing Governor/egress gate."""
import math
from datetime import datetime,timezone
import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import insert
from bcc.studio.tables import config,budget,jobs
from bcc.studio.runtime import one,digest,StudioError
from bcc.v2.governor import GovernorState,GovernorThresholds

DEFAULT={'enabled':False,'free_only':True,'cloud_budget_usd':0,'per_job_usd':0,'prices':{},'download_hosts':[]}
async def policy(svc):
    row=await one(svc,config,config.c.key,'openrouter')
    return {**DEFAULT,**(row['value'] if row else {})}

def number(x):return type(x) in (int,float) and math.isfinite(x) and x>=0

async def save_policy(svc,value):
    if not isinstance(value,dict) or set(value)-set(DEFAULT):raise ValueError('policy: unknown setting')
    data={**DEFAULT,**value}
    if type(data['enabled']) is not bool or type(data['free_only']) is not bool:raise ValueError('policy booleans required')
    if not number(data['cloud_budget_usd']) or not number(data['per_job_usd']):raise ValueError('budget: finite nonnegative value required')
    if not isinstance(data['prices'],dict) or any(not k.startswith('openrouter:') or not number(v) for k,v in data['prices'].items()):raise ValueError('price: invalid owner upper bound')
    if not isinstance(data['download_hosts'],list) or any(not isinstance(h,str) or not h or any(c in h for c in '/:@* ') for h in data['download_hosts']):raise ValueError('download_hosts: exact hostnames required')
    async with svc.db.session() as s:
        await s.execute(insert(config).values(key='openrouter',value=data).on_conflict_do_update(index_elements=['key'],set_={'value':data}));await s.commit()
    return data

async def confirm(svc,provider,inputs):
    if provider!='openrouter':raise ValueError('provider: unsupported')
    key='egress:'+digest({'provider':provider,'inputs':inputs})
    async with svc.db.session() as s:
        await s.execute(insert(config).values(key=key,value={'provider':provider,'inputs':inputs}).on_conflict_do_nothing());await s.commit()
    return key

async def reserve(svc,model,jid,count,inputs):
    p=await policy(svc)
    if not p['enabled']:raise StudioError('unauthorized','OWNER_REQUIRED: OpenRouter disabled','OWNER_REQUIRED')
    price=p['prices'].get(model)
    if price is None:raise StudioError('unknown_price','OWNER_REQUIRED: price upper bound missing','OWNER_REQUIRED')
    if p['free_only'] and price!=0:raise StudioError('budget','free_only refuses paid model','OWNER_REQUIRED')
    amount=price*count
    if amount>p['per_job_usd']:raise StudioError('budget','per-job budget exceeded','OWNER_REQUIRED')
    if inputs:
        key='egress:'+digest({'provider':'openrouter','inputs':inputs})
        if not await one(svc,config,config.c.key,key):raise StudioError('egress','egress requires confirmation for these exact bytes','OWNER_REQUIRED')
    day=datetime.now(timezone.utc).date().isoformat()
    async with svc.db.session() as s:
        await s.execute(insert(budget).values(day=day,committed_usd=0).on_conflict_do_nothing())
        # A reservation is a conservative committed expense, never silently refunded
        # after timeout/cancel/crash because the provider may still charge.
        result=await s.execute(sa.update(budget).where(budget.c.day==day,budget.c.committed_usd+amount<=p['cloud_budget_usd']).values(committed_usd=budget.c.committed_usd+amount))
        if not result.rowcount:raise StudioError('budget','daily budget exceeded','OWNER_REQUIRED')
        await s.execute(sa.update(jobs).where(jobs.c.job_id==jid).values(reserved_usd=amount,budget_day=day,policy_digest=digest(p)))
        await s.commit()
    state=GovernorState(thresholds=GovernorThresholds(cloud_budget_usd=p['cloud_budget_usd']))
    # SQL is the authoritative atomic reservation; Governor records the same cost.
    state.add_cloud_spend(amount)
    return {'upper_bound_usd':amount,'policy_digest':digest(p),'policy':p,'day':day,'inputs':inputs}

async def check_current(svc,reservation):
    if reservation.get('inputs'):
        key='egress:'+digest({'provider':'openrouter','inputs':reservation['inputs']})
        if not await one(svc,config,config.c.key,key):raise StudioError('egress','Reference consent revoked','OWNER_REQUIRED')
    p=await policy(svc)
    if not p['enabled'] or digest(p)!=reservation['policy_digest']:
        raise StudioError('policy_changed','Provider policy changed; dispatch stopped','OWNER_REQUIRED')

async def budget_status(svc):
    p=await policy(svc);day=datetime.now(timezone.utc).date().isoformat()
    row=await one(svc,budget,budget.c.day,day)
    return {'day':day,'committed_upper_bound_usd':row['committed_usd'] if row else 0,'cloud_budget_usd':p['cloud_budget_usd'],'free_only':p['free_only'],'enabled':p['enabled']}
