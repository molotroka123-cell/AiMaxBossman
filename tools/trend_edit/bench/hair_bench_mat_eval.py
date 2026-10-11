import sys, json, time, numpy as np, cv2
sys.path.insert(0, r'C:\Users\asd\Bossman\wt-ads-20261010\tools\trend_edit')
sys.path.insert(0, r'C:\Users\asd\Bossman\wt-ads-20261010\tools\genjutsu')
import gt_gate as g, hair_v2 as hv, parsing_worker as pw
import onnxruntime as ort
V = g.VID; out = V / 'ads-20261011' / 'prep' / 'gt'
refs = {c: g.load_webp(g.REFS / f'{c}.webp') for c in g.COLORS}
h, w = refs['pink'].shape[:2]
orig = cv2.resize(cv2.imdecode(np.fromfile(str(g.ORIG), np.uint8), 1), (w, h), interpolation=cv2.INTER_CUBIC)
gt, _ = g.gt_mask(orig, list(refs.values()))
parser = pw.Parser(hv.SEG_MODEL, 'directml')
m, _ = pw.parse(orig, parser)
hair = m['hair'].astype(np.float32) / 255
skin = m['skin'].astype(np.float32) / 255
clothes = m['clothes'].astype(np.float32) / 255
person = m['person'].astype(np.float32) / 255
ys, xs = np.where(hair > 0.5)
cx, cy = int(xs.mean()), int(ys.mean())
S = 1040
x0 = 0; y0 = int(np.clip(cy - S // 2, 0, h - S))
crop = orig[y0:y0 + S, x0:x0 + S]
RES = {}
def sess(path):
    for prov in (['DmlExecutionProvider', 'CPUExecutionProvider'],):
        try:
            return ort.InferenceSession(path, providers=prov)
        except Exception as e:
            print('prov fail', e)
    return ort.InferenceSession(path, providers=['CPUExecutionProvider'])
def run_birefnet(path, name, size=1024):
    t0 = time.time()
    s = sess(path)
    im = cv2.cvtColor(cv2.resize(crop, (size, size), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB).astype(np.float32) / 255
    im = (im - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
    x = im.transpose(2, 0, 1)[None]
    o = s.run(None, {s.get_inputs()[0].name: x})[0]
    a = 1 / (1 + np.exp(-o[0, 0]))
    a = cv2.resize(a, (S, S), interpolation=cv2.INTER_CUBIC)
    full = np.zeros((h, w), np.float32); full[y0:y0 + S, x0:x0 + S] = np.clip(a, 0, 1)
    RES[name] = full
    print(name, 'time', round(time.time() - t0, 1), 'providers', s.get_providers()[0], flush=True)
B = r'C:\Users\asd\Bossman\models\genjutsu\birefnet'
run_birefnet(B + r'\BiRefNet-portrait-ONNX.onnx', 'birefnet_portrait')
run_birefnet(B + r'\BiRefNet-ONNX.onnx', 'birefnet_general')
# MODNet (512 ref size, person), on the head crop
def run_modnet():
    s = sess(r'C:\Users\asd\Bossman\models\genjutsu\modnet\model.onnx')
    inp = s.get_inputs()[0]
    sz = 1024
    im = cv2.cvtColor(cv2.resize(crop, (sz, sz), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB).astype(np.float32)
    im = (im - 127.5) / 127.5
    o = s.run(None, {inp.name: im.transpose(2, 0, 1)[None]})[0]
    a = cv2.resize(np.clip(o[0, 0], 0, 1), (S, S), interpolation=cv2.INTER_CUBIC)
    full = np.zeros((h, w), np.float32); full[y0:y0 + S, x0:x0 + S] = a
    RES['modnet'] = full
try:
    run_modnet()
except Exception as e:
    print('modnet fail', e)
from background_swap import Matting
rvm = Matting(device='dml')
for _ in range(3):
    _, pha = rvm(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB), 0.5)
full = np.zeros((h, w), np.float32); full[y0:y0 + S, x0:x0 + S] = pha; RES['rvm'] = full
np.savez(out / 'mattes.npz', **{k: (v * 255).astype(np.uint8) for k, v in RES.items()})
tol = 9
def rep(n, a):
    r = (n, round(g.iou(a, gt), 3), round(g.boundary_f1(a, gt, 3), 3), round(g.boundary_f1(a, gt, tol), 3), round(float((a > 0.5).sum() / (gt > 0.5).sum()), 2))
    print('%-46s IoU %.3f F1@3 %.3f F1@9 %.3f area/GT %.2f' % r, flush=True)
    return r
rows = []
skin_s = cv2.GaussianBlur(skin, (0, 0), 1.2)
cl_s = cv2.GaussianBlur(clothes, (0, 0), 1.2)
for k in [1, 2, 3]:
    pass
base_excl = (1 - hv.smoothstep(0.08, 0.45, skin_s)) * (1 - hv.smoothstep(0.3, 0.7, cl_s))
rows.append(rep('segformer hair (skin+clothes excluded)', hair * base_excl))
for name, mat in RES.items():
    rows.append(rep(f'{name} raw (person incl. face)', mat))
    for d in [15, 31]:
        region = cv2.dilate(hair, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (d, d)))
        a = mat * np.clip(region, 0, 1) * base_excl
        rows.append(rep(f'{name} x hair-region({d}px) x excl', a))
    # matte minus face/clothes only (no hair-region prior), restricted to above the neck line = rows above hair bottom
    ybot = int(np.where(hair > 0.5)[0].max())
    a = mat * base_excl; a[ybot + 20:] = 0
    rows.append(rep(f'{name} x excl, cut below hair bottom', a))
json.dump(rows, open(out / 'mat_eval.json', 'w'), indent=1)
