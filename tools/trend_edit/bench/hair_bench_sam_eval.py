import sys, numpy as np, cv2
sys.path.insert(0, r'C:\Users\asd\Bossman\wt-ads-20261010\tools\trend_edit')
sys.path.insert(0, r'C:\Users\asd\Bossman\wt-ads-20261010\tools\genjutsu')
import gt_gate as g, hair_v2 as hv, parsing_worker as pw
import genjutsu as gj
from PIL import Image
out=g.VID/'ads-20261011'/'prep'/'gt'
gt=cv2.imread(str(out/'gt_mask.png'),0)/255.0
h,w=gt.shape
orig=cv2.resize(cv2.imdecode(np.fromfile(str(g.ORIG),np.uint8),1),(w,h),interpolation=cv2.INTER_CUBIC)
parser=pw.Parser(hv.SEG_MODEL,'directml'); m,_=pw.parse(orig,parser)
hair=m['hair']/255.0; skin=m['skin']/255.0; clothes=m['clothes']/255.0; person=m['person']/255.0
seg=gj.Segmenter()
ys,xs=np.where(hair>0.5); S=1040
y0=int(np.clip((ys.min()+ys.max())//2-S//2,0,h-S)); x0=0
crop=orig[y0:y0+S,x0:x0+S]
def pts_from(mask, n, rng, erode=9):
    mk=cv2.erode((mask>0.9).astype(np.uint8),np.ones((erode,erode),np.uint8))
    yy,xx=np.where(mk[y0:y0+S,x0:x0+S]>0)
    if len(yy)==0: return []
    idx=rng.choice(len(yy),min(n,len(yy)),replace=False)
    return [(int(xx[i]),int(yy[i])) for i in idx]
rng=np.random.default_rng(3)
def rep(n,a):
    print('%-40s IoU %.3f F1@3 %.3f F1@9 %.3f area/GT %.2f'%(n,g.iou(a,gt),g.boundary_f1(a,gt,3),g.boundary_f1(a,gt,9),float((a>0.5).sum()/(gt>0.5).sum())),flush=True)
bx=[xs.min()-10-x0, ys.min()-10-y0, xs.max()+10-x0, ys.max()+10-y0]
bx=[max(0,bx[0]),max(0,bx[1]),min(S-1,bx[2]),min(S-1,bx[3])]
# restrict hair mask rows to the crop
results={}
for np_,nn in [(6,6),(10,10),(16,16)]:
    P=[(x,y,1) for x,y in pts_from(hair,np_,rng)]
    N=[(x,y,0) for x,y in pts_from(skin,nn//2+1,rng,5)+pts_from(clothes,nn//2+1,rng,9)]
    # background negatives: outside person, near the head
    bgm=(person<0.05).astype(np.float32); N+= [(x,y,0) for x,y in pts_from(bgm,3,rng,15)]
    # SAM2 wrapper returns 1 mask; call decoder directly for the 3 candidates
    im=Image.fromarray(cv2.cvtColor(crop,cv2.COLOR_BGR2RGB))
    xin=((np.asarray(im.resize((1024,1024),Image.BILINEAR),np.float32)/255-[0.485,0.456,0.406])/[0.229,0.224,0.225]).transpose(2,0,1)[None].astype(np.float32)
    e0,e1,e2=seg.enc.run(None,{"pixel_values":xin})
    sc=1024/S
    pts=np.array([[[[px*sc,py*sc] for px,py,_ in P+N]]],np.float32); lab=np.array([[[l for _,_,l in P+N]]],np.int64)
    b=np.array([[[bx[0]*sc,bx[1]*sc,bx[2]*sc,bx[3]*sc]]],np.float32)
    iou,masks,_=seg.dec.run(None,{"input_points":pts,"input_labels":lab,"input_boxes":b,"image_embeddings.0":e0,"image_embeddings.1":e1,"image_embeddings.2":e2})
    for k in range(masks.shape[2]):
        up=cv2.resize(masks[0,0,k].astype(np.float32),(S,S),interpolation=cv2.INTER_LINEAR)
        full=np.zeros((h,w),np.float32); full[y0:y0+S,x0:x0+S]=(up>0)
        rep(f'SAM2 pos{np_} cand{k} (pred iou {iou[0,0,k]:.2f})',full)
        results[(np_,k)]=full
best=max(results.items(),key=lambda kv:g.iou(kv[1],gt))
np.savez(out/'sam_best.npz',m=best[1].astype(np.uint8),key=str(best[0]))
print('best',best[0])
