"""Installed Studio acceptance. No fixture provider, paid fallback or guessed tariffs.

Cloud execution requires --execute --policy FILE --model IMAGE --model VIDEO.
The policy must explicitly declare zero upper bounds for both models. Without
those owner inputs this writes OWNER_REQUIRED (exit 2), never synthetic PASS.
Local ComfyUI uses --provider comfyui --execute and existing BCC_COMFYUI_* env.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time


def validate_free_policy(policy,models):
    if (policy.get('enabled') is not True or policy.get('free_only') is not True
            or len(models)!=2 or len(set(models))!=2
            or any(type(policy.get('prices',{}).get(m)) not in (int,float)
                   or policy['prices'][m]!=0 for m in models)
            or policy.get('cloud_budget_usd')!=0 or policy.get('per_job_usd')!=0):
        raise ValueError('Two explicitly free routes and zero spending caps required')
    return policy


def exercise(args,report):
    from bcc import owner_acceptance as owner
    report['identity']=owner.installed_identity()
    if report['identity']['source_sha']!=args.expected_sha:raise ValueError('installed source mismatch')
    report['installed']=True
    if not args.execute:raise owner.OwnerRequired('Explicit --execute is required for live generation')
    from bcc.studio.runtime import model_specs
    catalog={m['id']:m for m in model_specs()}
    if args.provider=='openrouter':
        if not args.policy or not args.model:raise owner.OwnerRequired('Supply confirmed free image/video routes and policy')
        policy=validate_free_policy(json.loads(args.policy.read_text()),args.model)
        models=args.model
        if {catalog[m]['surface'] for m in models}!={'image','video'}:raise ValueError('image and video required')
        if any(catalog[m]['provider']!='openrouter' for m in models):raise ValueError('provider mismatch')
    else:
        from bcc.oss.comfyui import image_configuration
        if image_configuration() is None:raise owner.OwnerRequired('Configure local ComfyUI checkpoint')
        models=[m for m in catalog if catalog[m]['provider']=='comfyui'][:1]
    with tempfile.TemporaryDirectory(prefix='bossman-studio-live-') as folder:
        data=Path(folder);os.chmod(data,0o700);owner._restrict_to_owner(data)
        with (data/'private-server.log').open('wb') as log:
            port=owner.free_port();process=owner.launch(data,port,log);client=None
            try:
                client,_=owner.connect(process,data,port)
                if args.provider=='openrouter':
                    response=client.put('/api/studio/policy',json=policy);response.raise_for_status()
                for model in models:
                    response=client.post('/api/studio/jobs',json={'model':model,'prompt':'A single blue ceramic cube on a neutral background','settings':{},'count':1});response.raise_for_status()
                    jid=response.json()['id'];deadline=time.monotonic()+args.timeout
                    while time.monotonic()<deadline:
                        response=client.get('/api/studio/jobs/'+str(jid));response.raise_for_status();job=response.json()
                        if job['status'] in ('completed','failed','cancelled'):break
                        time.sleep(0.5)
                    else:
                        client.post('/api/studio/jobs/'+str(jid)+'/cancel')
                        raise TimeoutError('live generation deadline')
                    if job['status']!='completed':
                        reason=job['studio']['reason']
                        if job['studio']['verdict']=='OWNER_REQUIRED':raise owner.OwnerRequired(reason)
                        raise ValueError(reason)
                    response=client.get('/api/studio/runs');response.raise_for_status()
                    outputs=[r for r in response.json()['items'] if r['job_id']==jid]
                    if not outputs:raise ValueError('no verified output')
                    for row in outputs:
                        response=client.get(row['file_url']);response.raise_for_status();raw=response.content
                        if hashlib.sha256(raw).hexdigest()!=row['sha256'] or len(raw)!=row['file_bytes'] or row['provenance']['mock']:raise ValueError('output proof mismatch')
                        target=args.output.parent/'studio-artifacts';target.mkdir(exist_ok=True)
                        suffix=Path(row['file_path']).suffix if 'file_path' in row else ('.png' if row['surface']=='image' else '.mp4')
                        path=target/(row['id']+suffix);path.write_bytes(raw)
                        import asyncio
                        from bcc.studio.runtime import verify_file
                        checked=asyncio.run(verify_file(path,row['surface']))
                        (target/(row['id']+'.json')).write_text(json.dumps(row['provenance'],ensure_ascii=False,indent=2))
                        report['live'][0]['outputs'].append({'surface':row['surface'],'sha256':checked['sha256'],'bytes':checked['bytes'],'decoded':True,'mock':False,'provenance':True,'model':model,'run_id':row['id'],'cost_usd':row['provenance']['cost_usd']})
                response=client.get('/api/studio/budget');response.raise_for_status();report['budget']=response.json()
                report['live'][0]['status']='PASS';report['status']='PASS';report['acceptance']='PASS'
            finally:
                if client:client.close()
                if process:owner.stop(process)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--expected-sha',required=True)
    parser.add_argument('--provider',choices=['openrouter','comfyui'],default='openrouter')
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--policy',type=Path)
    parser.add_argument('--model',action='append')
    parser.add_argument('--timeout',type=int,default=600)
    args=parser.parse_args(argv)
    if not re.fullmatch('[0-9a-f]{40}',args.expected_sha) or not 1<=args.timeout<=600:parser.error('full SHA and timeout 1..600 required')
    report={'source_sha':args.expected_sha,'status':'OWNER_REQUIRED','acceptance':'OWNER_REQUIRED','installed':False,'identity':None,
            'binding':{'source_sha':args.expected_sha,'archive_sha256':os.environ.get('BOSSMAN_ARCHIVE_SHA256'),'run_id':os.environ.get('GITHUB_RUN_ID'),'harness_sha':os.environ.get('BOSSMAN_HARNESS_SHA')},
            'live':[{'provider':args.provider,'status':'OWNER_REQUIRED','outputs':[]}],
            'budget':'NOT_CAPTURED:no_live_call','egress':{'confirmed':0,'attempted':0},'paid_fallback':False}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    secret=os.environ.pop('BOSSMAN_OPENROUTER_API_KEY','')
    old=os.environ.get('OPENROUTER_API_KEY')
    if secret:os.environ['OPENROUTER_API_KEY']=secret
    try:
        if args.provider=='openrouter' and not os.environ.get('OPENROUTER_API_KEY'):
            report['reason']='unauthorized: owner key missing'
        else:exercise(args,report)
    except Exception as exc:
        # No provider text, request payload or credential reaches the report.
        if type(exc).__name__=='OwnerRequired':report['reason']='OWNER_REQUIRED: configuration or installed artifact required'
        else:report.update(status='FAIL',acceptance='FAIL',reason=type(exc).__name__);report['live'][0]['status']='FAIL'
    finally:
        if secret:
            if old is None:os.environ.pop('OPENROUTER_API_KEY',None)
            else:os.environ['OPENROUTER_API_KEY']=old
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('BOSSMAN_STUDIO_LIVE='+args.provider+':'+report['status'])
    print('BOSSMAN_STUDIO_ACCEPTANCE='+report['acceptance'])
    costs=[o['cost_usd'] for o in report['live'][0]['outputs']]
    spent=sum(costs) if costs and all(type(c) in (int,float) for c in costs) else None
    print('BOSSMAN_STUDIO_BUDGET='+ (str(spent)+'/0' if spent is not None else 'NOT_CAPTURED:no_reported_live_charge'))
    print('BOSSMAN_STUDIO_EGRESS=0/0')
    return {'PASS':0,'FAIL':1,'OWNER_REQUIRED':2}[report['status']]

if __name__=='__main__':raise SystemExit(main())
