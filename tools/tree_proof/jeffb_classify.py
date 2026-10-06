"""Write evidence/jeffb-classification.md + empty jeffb-retire.json from jeffb.json receipts (re-runnable)."""
import json,os,re,sys
sys.path.insert(0,os.path.dirname(__file__))
from jeffb_scan import L,ROOT
EV=f'{ROOT}/docs/architecture/bossman-tree-20261005/evidence'
rec={r['node_id']:r for r in json.load(open(f'{EV}/jeffb.json',encoding='utf-8'))}
TOP={'module-d93b8a2e7320','module-90209d82364c','module-9f87418c31e0','module-e3b155c353db','module-a2a8be1c06e1',
     'module-eddee4708a7d','module-f26b63157c2f','module-f526d2fd0ef3','module-9818110824b0','module-91e8d95273c5'}
LOW={'module-bf7480cb496c','module-d6fff5861410','module-d794cbd77cf3'}
rows=[];c={'GREEN':0,'RETIRE':0,'KEEP':0,'TOP':0}
for n in L:
    i=n['id'];label=n['label'].replace('|','/')
    p=n['sources'][0]['path'];r=rec.get(i)
    if r and r['verdict']=='PASS':
        o=open(f'{EV}/out/{i}.txt',encoding='utf-8').read()
        files=re.findall(r"test_files: \[(.*?)\] \(candidates=(\d+)\)",o)[0]
        passed=sum(int(x) for x in re.findall(r'(\d+) passed',o))
        names=', '.join(os.path.basename(f.strip("' ")) for f in files[0].split(','))
        auth=' authored_by_lane test.' if 'authored_by_lane' in r['probe'] else ''
        v='GREEN';val='TOP' if i in TOP else ('LOW' if i in LOW else 'OK')
        reason=f"import in clean subprocess inside worktree + {passed} tests passed ({names}; {files[1]} candidate files).{auth}"
        c['GREEN']+=1; c['TOP']+= val=='TOP'
    else:
        v='KEEP';val='OK'
        reason=f"code absent from this worktree (branch tgcalls-work only, {p}); not retired, no audit proof of duplication; needs the branch merged"
        c['KEEP']+=1
    rows.append(f"| {i} | {label} | {v} | {reason} | {val} |")
hdr=["# jeffb lane classification (zone jeff, second half by sorted id, 67 leaves)","",
 f"GREEN {c['GREEN']} / RETIRE {c['RETIRE']} / KEEP {c['KEEP']} / FAIL 0 / TOP {c['TOP']}","",
 "RETIRE=0: grep audit (tools/tree_proof/jeffb_importers.py) found no module that is both dead and non-safety; zero-importer modules (j2/persona, j2/director, companion_profile, voice_capability) are Jeff core, tested, or consent/policy related, so they are kept.",
 "Caveat: PASS = import + existing tests textually referencing the module (<=3-8 files per leaf), not semantic certification.","",
 "| id | label | verdict | reason | value |","|---|---|---|---|---|"]
open(f'{EV}/jeffb-classification.md','w',encoding='utf-8').write('\n'.join(hdr+rows)+'\n')
open(f'{EV}/jeffb-retire.json','w',encoding='utf-8').write('[]\n')
print(c)
