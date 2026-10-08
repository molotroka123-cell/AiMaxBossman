import json,csv,collections,re
S=json.load(open('command-center/bcc/capability_tree_seed.json',encoding='utf-8'))
N={x['id']:x for x in S['nodes']}
R={l['id']:l for l in json.load(open('docs/architecture/tree-registry.json',encoding='utf-8'))['leaves']}
site={n['id']:n for n in json.load(open('C:/Users/asd/Bossman/handoff/tree-audit-20261008/site-tree.json',encoding='utf-8'))['nodes']}
kids=collections.Counter(x['parent'] for x in N.values())
def zone(i):
    seen=0
    while N[i]['parent'] and N[i]['parent']!='bossman' and N[i]['parent'] in N and seen<20: i=N[i]['parent']; seen+=1
    return i
srcmap=collections.defaultdict(list)
for i,n in N.items():
    for s in n.get('sources') or []:
        if s.get('path'): srcmap[s['path']].append(i)
def first(t,n=160):
    t=re.sub(r'\s+',' ',t or '').strip(); return t[:n]
rows=[]
for i,n in N.items():
    r=R.get(i); st=n['status']; srcs=[s['path'] for s in (n.get('sources') or []) if s.get('path')]
    proven=r['proven_through'] if r else 'не в registry'
    t=r['levels']['tests'] if r else {}
    dups=sorted({o for p in srcs for o in srcmap[p] if o!=i and kids[o]==0})[:6]
    group = kids[i]>0
    if st=='retired': dec,why='отклонить','retired в seed'
    elif group: dec,why='группа','узел-контейнер, решение по дочерним'
    elif st=='working' and proven=='tests': dec,why='внедрено','есть код и тесты (CI/ПК не проверены)'
    elif st in('working','reported','recorded','code','mixed') and srcs: dec,why='объединить/проверить','код есть, доказательство ниже tests'
    elif st in('branch','prepared'): dec,why='объединить','код в отдельной ветке или подготовлен'
    elif st=='blocked': dec,why='в план','заблокировано'
    elif st=='idea': dec,why='в план','идея без кода'
    else: dec,why='проверить','нет источников'
    crit={'внедрено':'pytest по файлам источника + реальный вход UX/CMD, затем STOP и restart на ПК',
          'объединить/проверить':'добавить/запустить тест источника, затем вход UX/CMD→backend→результат→STOP→restart',
          'объединить':'влить ветку в линию, тест источника, вход UX/CMD',
          'в план':'снять блокер/написать код, затем тест',
          'группа':'все дочерние листья закрыты','отклонить':'не требуется','проверить':'найти источник или пометить idea'}[dec]
    rows.append([i,n['label'],zone(i),n['parent'],first(site.get(i,{}).get('short') or n.get('detail')),st,proven,
      t.get('passed',''),t.get('failed',''),';'.join(srcs[:3]),';'.join(dups),dec,why,crit,
      'да' if i in site else 'нет на сайте','да' if i in R else 'нет в registry'])
with open('C:/Users/asd/Bossman/handoff/tree-audit-20261008/stage1-table.csv','w',encoding='utf-8-sig',newline='') as f:
    w=csv.writer(f); w.writerow(['id','название','зона','родитель','что нужно (short)','статус seed','доказано до','tests_ok','tests_fail','источники','дубли по источнику','решение (предв.)','основание','критерий результата','на сайте','в registry']); w.writerows(rows)
print(len(rows)); print(collections.Counter(r[11] for r in rows))
z=collections.defaultdict(collections.Counter)
for r in rows: z[r[2]][r[11]]+=1
for k,v in z.items(): print(N[k]['label'][:28].encode('ascii','replace').decode(),k,sum(v.values()),dict(v))
