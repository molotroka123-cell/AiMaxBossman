import os,re,sys,json,subprocess
sys.path.insert(0,os.path.dirname(__file__))
from jeffb_scan import L,ROOT
# collect test files
tests=[]
for dp,dn,fn in os.walk(ROOT):
    dn[:]=[x for x in dn if x not in('.git','node_modules','__pycache__','.venv','venv')]
    for f in fn:
        if f.endswith('.py') and (f.startswith('test_') or f.endswith('_test.py')): tests.append(os.path.join(dp,f))
txt={t:open(t,encoding='utf-8',errors='ignore').read() for t in tests}
def info(n):
    p=n['sources'][0]['path']
    rel=p
    full=f'{ROOT}/{p}'
    base=os.path.basename(p)[:-3]
    mod=None
    if p.startswith('command-center/'): mod=p[len('command-center/'):-3].replace('/','.')
    elif p.startswith('bossman-core/'): mod=p[len('bossman-core/'):-3].replace('/','.')
    if mod and mod.endswith('.__init__'): mod=mod[:-9]
    short=mod.split('.')[-1] if mod else base
    hits=[]
    for t,s in txt.items():
        tb=os.path.basename(t)
        ok=False
        if mod and re.search(r'(?<![\w.])'+re.escape(mod)+r'(?![\w])',s): ok=True
        if mod:
            pkg,_,last=mod.rpartition('.')
            if pkg and re.search(r'from\s+'+re.escape(pkg)+r'\s+import[^\n]*\b'+re.escape(last)+r'\b',s): ok=True
        if tb.startswith('test_'+base) or tb.startswith('test_leaf_'+base): ok=True
        if ok: hits.append(os.path.relpath(t,ROOT).replace(chr(92),'/'))
    return mod,os.path.exists(full),hits
if __name__=='__main__':
    for n in L:
        m,e,h=info(n)
        print(n['id'],n['status'],m,e,len(h),h[:3])
