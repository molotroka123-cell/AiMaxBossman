"""Local face detect (SCRFD 2.5g) + ArcFace (w600k_r50) via onnxruntime CPU, models from the local FaceFusion install. No network. Copy of the proven helper used by head_zoom.py."""
import cv2, numpy as np, onnxruntime as ort
M = r"C:\Users\asd\Bossman\apps-local\facefusion\.assets\models"
_so = ort.SessionOptions(); _so.log_severity_level = 3
_det = ort.InferenceSession(M + r"\scrfd_2.5g.onnx", _so, providers=["CPUExecutionProvider"])
_rec = ort.InferenceSession(M + r"\arcface_w600k_r50.onnx", _so, providers=["CPUExecutionProvider"])
ARC = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366], [41.5493, 92.3655], [70.7299, 92.2041]], np.float32)

def _dist2bbox(pts, d): return np.stack([pts[:, 0]-d[:, 0], pts[:, 1]-d[:, 1], pts[:, 0]+d[:, 2], pts[:, 1]+d[:, 3]], 1)
def _dist2kps(pts, d): return np.stack([pts[:, i % 2] + d[:, i] for i in range(10)], 1).reshape(-1, 5, 2) if False else np.stack([pts[:, 0:1].repeat(5, 1)*0 + pts[:, 0:1] + d[:, 0::2], pts[:, 1:2] + d[:, 1::2]], 2)

def detect(img, thr=0.5, size=640):
    h, w = img.shape[:2]; s = size / max(h, w)
    nh, nw = int(h*s), int(w*s)
    canvas = np.zeros((size, size, 3), np.uint8); canvas[:nh, :nw] = cv2.resize(img, (nw, nh))
    blob = cv2.dnn.blobFromImage(canvas, 1/128, (size, size), (127.5, 127.5, 127.5), swapRB=True)
    out = _det.run(None, {_det.get_inputs()[0].name: blob})
    res = []
    for i, st in enumerate((8, 16, 32)):
        sc, bb, kp = out[i][:, 0], out[i+3]*st, out[i+6]*st
        g = size // st
        ys, xs = np.mgrid[0:g, 0:g]; pts = np.stack([xs, ys], -1).reshape(-1, 2).astype(np.float32)*st
        pts = np.repeat(pts, 2, 0)
        m = sc > thr
        if not m.any(): continue
        bx = _dist2bbox(pts[m], bb[m]); kx = _dist2kps(pts[m], kp[m])
        for b, k, c in zip(bx, kx, sc[m]): res.append((b/s, k/s, float(c)))
    # nms
    res.sort(key=lambda r: -r[2]); keep = []
    for r in res:
        ok = True
        for q in keep:
            a, b = r[0], q[0]
            iw = max(0, min(a[2], b[2]) - max(a[0], b[0])); ih = max(0, min(a[3], b[3]) - max(a[1], b[1]))
            inter = iw*ih; u = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter
            if inter/u > 0.4: ok = False; break
        if ok: keep.append(r)
    return keep  # list of (bbox, kps5, score)

def embed(img, kps):
    M_, _ = cv2.estimateAffinePartial2D(kps.astype(np.float32), ARC, method=cv2.LMEDS)
    al = cv2.warpAffine(img, M_, (112, 112), borderValue=0)
    blob = cv2.dnn.blobFromImage(al, 1/127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=True)
    e = _rec.run(None, {_rec.get_inputs()[0].name: blob})[0][0]
    return e / np.linalg.norm(e), al

def frontality(k):  # nose offset from eye midline relative to eye distance (0 = frontal)
    el, er, n = k[0], k[1], k[2]
    mid = (el+er)/2; d = np.linalg.norm(er-el) + 1e-6
    return abs(n[0]-mid[0])/d

def face_sharp(img, bb):
    x0, y0, x1, y1 = [int(max(0, v)) for v in bb]
    c = img[y0:y1, x0:x1]
    if c.size == 0: return 0.0
    c = cv2.resize(c, (128, 128))
    return float(cv2.Laplacian(cv2.cvtColor(c, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())
