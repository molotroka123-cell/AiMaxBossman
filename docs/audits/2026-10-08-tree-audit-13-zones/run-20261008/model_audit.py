import json,csv,os,re,sys,time,urllib.request,collections
D='C:/Users/asd/Bossman/handoff/tree-audit-20261008/'
KEYS=dict(l.strip().split('=',1) for l in open(os.path.expandvars(r'%LOCALAPPDATA%/Bossman/keys/provider-keys.env'),encoding='utf-8') if '=' in l)
OR=KEYS['OPENROUTER_API_KEY']
rows=list(csv.DictReader(open(D+'stage1-table.csv',encoding='utf-8-sig')))
oss={r['node']:r for r in csv.DictReader(open(D+'oss-github-facts.csv',encoding='utf-8-sig'))}
miss={m[0] for m in json.load(open(D+'source-existence.json',encoding='utf-8'))['missing']}
zones=collections.defaultdict(list)
for r in rows: zones[r['зона']].append(r)
ROLES={
 'gaps':('anthropic/claude-haiku-5.5','Ты аудитор. Найди пробелы и противоречия между статусом узла дерева и доказательствами (источники, tests, miss_src=файла-источника нет в репозитории). Отмечай только то, что видно в данных; не выдумывай.'),
 'arch':('z-ai/glm-5.3-flash','Ты архитектор. Найди дублирование и пересечение узлов (одинаковые источники, схожие названия), предложи что объединить в один модуль Bossman, что оставить в плане, что отклонить. Только по данным.'),
}
def line(r):
    f=oss.get(r['id'])
    extra=f" lic={f['license']} stars={f['stars']} pushed={f['pushed_at'][:10]}" if f else ''
    return f"{r['id']}|{r['название'][:40]}|{r['статус seed']}|proof={r['доказано до']}|t={r['tests_ok']}/{r['tests_fail']}|src={r['источники'].split(';')[0][:70]}|dup={r['дубли по источнику'][:40]}|miss_src={'Y' if r['id'] in miss else 'N'}|dec={r['решение (предв.)']}{extra}"
def call(model,system,user,maxtok=6000):
    body=json.dumps({'model':model,'messages':[{'role':'system','content':system},{'role':'user','content':user}],'max_tokens':maxtok,'temperature':0}).encode()
    req=urllib.request.Request('https://openrouter.ai/api/v1/chat/completions',body,{'Authorization':'Bearer '+OR,'Content-Type':'application/json'})
    d=json.load(urllib.request.urlopen(req,timeout=240))
    ch=d['choices'][0]; m=ch['message']
    u=dict(d.get('usage',{})); u['finish']=ch.get('finish_reason'); u['refusal']=m.get('refusal')
    return m.get('content'),u
only=sys.argv[1:] 
spent=0.0; out={}
PRICE={'anthropic/claude-haiku-5.5':(0.1,0.5),'z-ai/glm-5.3-flash':(0.15,0.5)}
for z,rs in zones.items():
    if only and z not in only: continue
    if len(rs)<3: continue
    ex='\n'.join(line(r) for r in rs[:140])
    for role,(model,sysmsg) in ROLES.items():
        key=f'{z}:{role}'
        try:
            txt,u=call(model,sysmsg+' Ответ по-русски, максимум 12 пунктов, каждый с ID узла.',f'Зона {z}, узлов {len(rs)} (показано {min(len(rs),140)}):\n{ex}')
            c=float(u.get('cost') or 0); spent+=c
            if not txt: raise ValueError(f"empty content finish={u.get('finish')} refusal={u.get('refusal')} ct={u.get('completion_tokens')}")
            out[key]={'model':model,'text':txt,'usd':round(c,5),'finish':u.get('finish')}
        except Exception as e:
            out[key]={'model':model,'error':f'{type(e).__name__}: {str(e)[:160]}'}
        print(key,'ok' if 'text' in out[key] else out[key]['error'],flush=True)
fn=D+'model-audit-cloud.json'
old=json.load(open(fn,encoding='utf-8')) if os.path.exists(fn) else {}
old.update(out); out=old
json.dump(out,open(fn,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
print('est_usd',round(spent,4))
