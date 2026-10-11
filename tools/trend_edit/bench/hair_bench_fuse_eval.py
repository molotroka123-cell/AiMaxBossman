import sys, numpy as np, cv2
sys.path.insert(0, r'C:\Users\asd\Bossman\wt-ads-20261010\tools\trend_edit')
import gt_gate as g, hair_v2 as hv, parsing_worker as pw
out=g.VID/'ads-20261011'/'prep'/'gt'
gt=cv2.imread(str(out/'gt_mask.png'),0)/255.0; h,w=gt.shape
orig=cv2.resize(cv2.imdecode(np.fromfile(str(g.ORIG),np.uint8),1),(w,h),interpolation=cv2.INTER_CUBIC)
parser=pw.Parser(hv.SEG_MODEL,'directml'); m,_=pw.parse(orig,parser)
hair=m['hair']/255.0; skin=m['skin']/255.0; clothes=m['clothes']/255.0
sam=np.load(out/'sam_best.npz')['m'].astype(np.float32)
def rep(n,a):
    print('%-52s IoU %.3f F1@3 %.3f F1@9 %.3f area %.2f'%(n,g.iou(a,gt),g.boundary_f1(a,gt,3),g.boundary_f1(a,gt,9),float((a>0.5).sum()/(gt>0.5).sum())),flush=True)
excl=(1-hv.smoothstep(0.08,0.45,cv2.GaussianBlur(skin,(0,0),1.2)))
base=hair*excl
rep('seg x excl',base); rep('sam',sam)
inter=base*sam; rep('seg x sam',inter)
sam_d=cv2.dilate(sam,np.ones((9,9),np.uint8)); rep('seg x sam(dil9)',base*sam_d)
for r in [2,3,4]:
    k=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(2*r+1,2*r+1))
    a=cv2.dilate((inter>0.5).astype(np.uint8),k).astype(np.float32)
    # grow only onto pixels that are not skin and that are darker than local background (dark hair fringe)
    L=hv.to_lab(orig)[:,:,0]; Lb=cv2.GaussianBlur(L,(0,0),12)
    fr=(a>0.5)&(excl>0.5)
    rep(f'seg x sam, grow {r}px',fr.astype(np.float32))
    # guided-filter edge snap on colour luma
    guide=cv2.cvtColor(orig,cv2.COLOR_BGR2GRAY).astype(np.float32)/255
    for rad,eps in [(4,1e-3),(8,1e-3)]:
        sn=pw.guided_filter(guide,a*excl,rad,eps); rep(f'   + guided r{rad} thr.4',(sn>0.4).astype(np.float32))
        rep(f'   + guided r{rad} thr.5',(sn>0.5).astype(np.float32))
print('--- sweep')
guide=cv2.cvtColor(orig,cv2.COLOR_BGR2GRAY).astype(np.float32)/255
best=None
for r in [4,5,6,8]:
    k=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(2*r+1,2*r+1))
    a=cv2.dilate((inter>0.5).astype(np.uint8),k).astype(np.float32)
    for rad in [8,12,16]:
        for eps in [1e-3,1e-4]:
            for thr in [0.3,0.4]:
                sn=pw.guided_filter(guide,a*excl,rad,eps); b=(sn>thr).astype(np.float32)
                sc=(g.iou(b,gt),g.boundary_f1(b,gt,3))
                if best is None or sc[0]+sc[1]>best[0][0]+best[0][1]: best=(sc,(r,rad,eps,thr),b)
print('best',best[0],best[1])
np.savez(out/'fused_best.npz',m=(best[2]*255).astype(np.uint8))
fp=(best[2]>0.5)&(gt<0.5); fn=(best[2]<=0.5)&(gt>0.5)
dg=cv2.distanceTransform((gt<=0.5).astype(np.uint8),cv2.DIST_L2,3); dn=cv2.distanceTransform((gt>0.5).astype(np.uint8),cv2.DIST_L2,3)
print('FP',int(fp.sum()),'far(>9)',int((fp&(dg>9)).sum()),'FN',int(fn.sum()),'deep(>9)',int((fn&(dn>9)).sum()))
ov=orig.copy(); ov[fp]=(0.3*ov[fp]+0.7*np.array([0,0,255])).astype(np.uint8); ov[fn]=(0.3*ov[fn]+0.7*np.array([255,0,0])).astype(np.uint8)
cv2.imwrite(str(out/'err_fused.jpg'),cv2.resize(ov[200:1500,:],None,fx=0.7,fy=0.7))
