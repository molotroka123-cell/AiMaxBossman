"""Metrics per plan T3_VIDEO_4K_EVAL_PLAN_20261011 (G2 sharpness, G3 flicker). Usage: metrics.py RUN_DIR [CONTROL_DIR]"""
import sys, os, json
import numpy as np, cv2, imageio
ROI = {'P1': (0.00, 0.00, 0.25, 0.12), 'P2': (0.00, 0.00, 0.20, 0.20), 'P3': (0.00, 0.00, 0.25, 0.10)}
IDX = (0, 20, 40, 60, 80)
def frames(path):
    r = imageio.get_reader(path); out = [f for f in r]; r.close(); return out
def lapvar(rgb):
    g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    return float(cv2.Laplacian(g, cv2.CV_32F, ksize=1).var())
def flicker(fr, roi):
    h, w = fr[0].shape[:2]; x0, y0, x1, y1 = roi
    sl = (slice(int(y0 * h), int(y1 * h)), slice(int(x0 * w), int(x1 * w)))
    lum = [cv2.cvtColor(f, cv2.COLOR_RGB2GRAY).astype(np.float32)[sl] for f in fr]
    d = [float(np.abs(lum[i + 1] - lum[i]).mean()) for i in range(len(lum) - 1)]
    full = [float(np.abs(cv2.cvtColor(fr[i + 1], cv2.COLOR_RGB2GRAY).astype(np.float32) - cv2.cvtColor(fr[i], cv2.COLOR_RGB2GRAY).astype(np.float32)).mean()) for i in range(len(fr) - 1)]
    return float(np.mean(d)), float(np.mean(full))
run = sys.argv[1]; ctrl = sys.argv[2] if len(sys.argv) > 2 else None
res = {}
for pid in ('P1', 'P2', 'P3'):
    p = os.path.join(run, pid, 'clip.mp4')
    if not os.path.exists(p): continue
    fr = frames(p); h, w = fr[0].shape[:2]
    r = {'n_frames': len(fr), 'size': [w, h], 'lapvar_native': float(np.mean([lapvar(fr[i]) for i in IDX]))}
    r['flicker_roi'], r['flicker_full'] = flicker(fr, ROI[pid])
    if ctrl and os.path.exists(os.path.join(ctrl, pid, 'clip.mp4')):
        cf = frames(os.path.join(ctrl, pid, 'clip.mp4'))
        r['ctrl_flicker_roi'], r['ctrl_flicker_full'] = flicker(cf, ROI[pid])
        up = [cv2.resize(cf[i], (w, h), interpolation=cv2.INTER_LANCZOS4) for i in IDX]
        r['ctrl_lapvar_lanczos_up'] = float(np.mean([lapvar(u) for u in up]))
        r['ctrl_lapvar_native'] = float(np.mean([lapvar(cf[i]) for i in IDX]))
        r['sharp_ratio'] = r['lapvar_native'] / max(r['ctrl_lapvar_lanczos_up'], 1e-6)
        r['G2'] = 'PASS' if (r['sharp_ratio'] >= 1.5 and r['lapvar_native'] >= 50) else 'FAIL'
        r['G3'] = 'PASS' if (r['flicker_roi'] <= 2.0 and r['flicker_roi'] <= 1.5 * r['ctrl_flicker_roi']) else 'FAIL'
    res[pid] = r
json.dump(res, open(os.path.join(run, 'metrics.json'), 'w'), indent=1)
print(json.dumps(res, indent=1))
