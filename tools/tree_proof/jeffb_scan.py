import json,os,re,subprocess,sys
ROOT=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
d=json.load(open(f'{ROOT}/command-center/capability_tree_seed.json' if 0 else f'{ROOT}/command-center/bcc/capability_tree_seed.json',encoding='utf-8'))
nodes=d['nodes'];ids={n['id']:n for n in nodes}
def zone(n):
    c=n;ch=[]
    while c and c['id'] not in ch: ch.append(c['id']); c=ids.get(c.get('parent'))
    return ch[-2] if len(ch)>=2 else n['id']
parents={n['parent'] for n in nodes if n.get('parent')}
L=sorted([n for n in nodes if zone(n)=='jeff' and n['id'] not in parents and n['status'] in('code','branch')],key=lambda n:n['id'])
L=L[len(L)-67:]
def lane_leaves(): return L
if __name__=='__main__':
    for n in L:
        p=n['sources'][0]['path'] if n.get('sources') and isinstance(n['sources'][0],dict) else None
        print(n['id'],n['status'],p,os.path.exists(f'{ROOT}/{p}') if p else None)
