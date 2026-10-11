import sys, numpy as np, cv2, json
sys.path.insert(0, r'C:\Users\asd\Bossman\wt-ads-20261010\tools\trend_edit')
import gt_gate as g, hair_v2 as hv, parsing_worker as pw
out = g.VID/'ads-20261011'/'prep'/'gt'
refs = {c: g.load_webp(g.REFS/f'{c}.webp') for c in g.COLORS}
h, w = refs['pink'].shape[:2]
orig = cv2.resize(cv2.imdecode(np.fromfile(str(g.ORIG), np.uint8), 1), (w, h), interpolation=cv2.INTER_CUBIC)
gt, _ = g.gt_mask(orig, list(refs.values()))
cv2.imwrite(str(out/'gt_mask.png'), (gt*255).astype(np.uint8))
parser = pw.Parser(hv.SEG_MODEL, 'directml'); m, _ = pw.parse(orig, parser)
hair = m['hair'].astype(np.float32)/255; skin = m['skin'].astype(np.float32)/255; clothes = m['clothes'].astype(np.float32)/255
mats = np.load(out/'mattes.npz')
sk = cv2.GaussianBlur(skin,(0,0),1.2); cl = cv2.GaussianBlur(clothes,(0,0),1.2)
excl = (1-hv.smoothstep(0.08,0.45,sk))*(1-hv.smoothstep(0.3,0.7,cl))
def rep(n,a):
    print('%-44s IoU %.3f F1@3 %.3f F1@9 %.3f area/GT %.2f'%(n,g.iou(a,gt),g.boundary_f1(a,gt,3),g.boundary_f1(a,gt,9),float((a>0.5).sum()/(gt>0.5).sum())))
rep('seg raw', hair); rep('seg x excl', hair*excl)
for k in ['rvm','birefnet_portrait','birefnet_general','modnet']:
    mm = mats[k].astype(np.float32)/255
    reg = cv2.dilate(hair, cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(15,15)))
    rep(k+' x region15 x excl', mm*np.clip(reg,0,1)*excl)
    # soft combination: strong seg core OR matte inside region
    a = np.maximum(hair*excl*(hair>0.85), mm*np.clip(reg,0,1)*excl)
    rep(k+' core OR matte', a)
