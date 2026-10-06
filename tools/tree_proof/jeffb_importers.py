"""Deterministic grep audit: who references each jeffb-lane module outside its own file/tests."""
import os,re,sys,json
sys.path.insert(0,os.path.dirname(__file__))
from jeffb_scan import L,ROOT
files=[]
for dp,dn,fn in os.walk(ROOT):
    dn[:]=[x for x in dn if x not in('.git','node_modules','__pycache__','.venv','venv','evidence')]
    for f in fn:
        if f.endswith(('.py','.json','.md','.toml','.ps1','.yml','.yaml','.js','.html')):
            fp=os.path.join(dp,f)
            if os.path.getsize(fp)<3_000_000: files.append(fp)
txt={}
for f in files:
    try: txt[f]=open(f,encoding='utf-8',errors='ignore').read()
    except OSError: pass
res={}
for n in L:
    p=n['sources'][0]['path']
    full=os.path.join(ROOT,p).replace('/',os.sep)
    mod=p.split('command-center/')[-1][:-3].replace('/','.') if 'command-center/' in p else p.split('bossman-core/')[-1][:-3].replace('/','.')
    last=mod.split('.')[-1]; pkg=mod.rpartition('.')[0]
    pats=[re.compile(r'(?<![\w.])'+re.escape(mod)+r'(?![\w])'),
          re.compile(r'from\s+'+re.escape(pkg)+r'\s+import[^\n]*\b'+re.escape(last)+r'\b'),
          re.compile(r'from\s+\.+\s*import[^\n]*\b'+re.escape(last)+r'\b'),
          re.compile(r'from\s+\.+[\w.]*\b'+re.escape(last)+r'\s+import')]
    hits=[]
    for f,s in txt.items():
        if os.path.normcase(f)==os.path.normcase(full): continue
        rel=os.path.relpath(f,ROOT).replace(os.sep,'/')
        if '/tests/' in rel or os.path.basename(rel).startswith('test_') or 'capability_tree' in rel or rel.startswith('tools/tree_proof') or rel.startswith('docs/'): continue
        if any(pt.search(s) for pt in pats): hits.append(rel)
    res[n['id']]=(p,hits)
    print(n['id'],p.split('bcc/')[-1],len(hits),hits[:2])
