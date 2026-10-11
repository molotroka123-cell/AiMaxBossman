import sys, numpy as np, cv2
sys.path.insert(0, r'C:\Users\asd\Bossman\wt-ads-20261010\tools\trend_edit')
import gt_gate as g, hair_v2 as hv
out=g.VID/'ads-20261011'/'prep'/'gt'
refs={c:g.load_webp(g.REFS/f'{c}.webp') for c in g.COLORS}
h,w=refs['pink'].shape[:2]
orig=cv2.resize(cv2.imdecode(np.fromfile(str(g.ORIG),np.uint8),1),(w,h),interpolation=cv2.INTER_CUBIC)
gt=cv2.imread(str(out/'gt_mask.png'),0)/255.0
lo=hv.to_lab(orig)
chrom=[]
for c in ['pink','blue','red']:
    r=hv.to_lab(refs[c]); chrom.append(np.hypot(r[:,:,1]-lo[:,:,1], r[:,:,2]-lo[:,:,2]))
ch=np.stack(chrom); n_ok=(ch>9).sum(0)
add=(cv2.GaussianBlur(n_ok.astype(np.float32),(0,0),1.0)>=2.5).astype(np.uint8)
add=cv2.morphologyEx(add,cv2.MORPH_OPEN,np.ones((3,3),np.uint8))
# only components touching the existing GT (no new islands)
n,lab,st,_=cv2.connectedComponentsWithStats(np.maximum(add,(gt>0.5).astype(np.uint8)),8)
head=lab[(gt>0.5)][0] if (gt>0.5).any() else 1
ids=np.unique(lab[gt>0.5]); keep=np.isin(lab,ids)
new=(keep&(gt<=0.5)&(add>0))
print('added px',int(new.sum()),'GT px',int((gt>0.5).sum()))
gt2=((gt>0.5)|new).astype(np.uint8)
gt2=cv2.morphologyEx(gt2,cv2.MORPH_CLOSE,np.ones((7,7),np.uint8))
cv2.imwrite(str(out/'gt_mask_v3.png'),gt2*255)
ov=orig.copy(); ov[new]=(0.4*ov[new]+0.6*np.array([0,255,255])).astype(np.uint8)
cv2.imwrite(str(out/'gt_added.jpg'),cv2.resize(ov[200:1500],None,fx=0.7,fy=0.7))
fz=(np.load(out/'fused_best.npz')['m']>127)
for nm,G in [('GT v2',gt),('GT v3 (chroma repair)',gt2)]:
    print(nm,'IoU %.3f F1@3 %.3f F1@9 %.3f'%(g.iou(fz,G),g.boundary_f1(fz,G,3),g.boundary_f1(fz,G,9)))
