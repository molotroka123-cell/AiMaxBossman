import sys, numpy as np, cv2
sys.path.insert(0, r'C:\Users\asd\Bossman\apps-local\facefusion'); sys.path.insert(0, r'C:\Users\asd\Bossman\wt-ads-20261010\tools\trend_edit')
from facefusion import state_manager
for k,v in {"execution_providers":["directml"],"execution_device_ids":["0"],"download_providers":["github","huggingface"],"face_detector_model":"yolo_face","face_detector_size":"640x640","face_detector_angles":[0],"face_detector_margin":[0,0,0,0],"face_detector_score":0.5,"face_landmarker_model":"2dfan4","face_landmarker_score":0.5,"log_level":"error"}.items(): state_manager.init_item(k,v)
from facefusion.face_creator import get_many_faces
import gt_gate as g, hair_v2 as hv
out=g.VID/'ads-20261011'/'prep'/'gt'
gt=cv2.imread(str(out/'gt_mask.png'),0)/255.0; h,w=gt.shape
orig=cv2.resize(cv2.imdecode(np.fromfile(str(g.ORIG),np.uint8),1),(w,h),interpolation=cv2.INTER_CUBIC)
fz=(np.load(out/'fused_best.npz')['m']>127).astype(np.float32)
fs=get_many_faces([orig]); f=max(fs,key=lambda f:f.bounding_box[2]-f.bounding_box[0]); l=f.landmark_set['68']
jaw=l[0:17]; chin=l[8]; c=l.mean(0)
# neck region: from the jaw polyline downward along the face's down direction (chin - brow-mid), width = jaw width
down=chin-(l[19]+l[24])/2; down/=np.linalg.norm(down)+1e-6; L=np.linalg.norm(l[0]-l[16])
poly=np.array([jaw[3],jaw[5],jaw[8],jaw[11],jaw[13]],np.float32)
nk=np.vstack([jaw[3]+down*0.0,jaw[13]+down*0.0,jaw[13]+down*0.9*L,jaw[3]+down*0.9*L]).astype(np.int32)
mask=np.zeros((h,w),np.uint8); cv2.fillConvexPoly(mask,nk,1)
for shrink in [1.0,0.8]:
    pass
b=fz*(1-mask); print('neck-box exclusion: IoU %.3f F1@3 %.3f F1@9 %.3f'%(g.iou(b,gt),g.boundary_f1(b,gt,3),g.boundary_f1(b,gt,9)))
ov=orig.copy(); cv2.polylines(ov,[nk],True,(0,255,0),4); cv2.imwrite(str(out/'neck_box.jpg'),cv2.resize(ov[200:1500],None,fx=0.5,fy=0.5))
np.savez(out/'neck.npz',m=mask)
