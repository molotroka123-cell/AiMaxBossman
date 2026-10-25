"""Pixel QA: original | Genjutsu kitchen | Genjutsu Red Square | ours at matching timestamps.
usage: qa.py ours.mp4 t0_in_original tag [n_fast]"""
import sys, cv2, numpy as np, os
W = "D:/Bossman/work/genjutsu-local-20261025"
ours, t0, tag = sys.argv[1], float(sys.argv[2]), sys.argv[3]
nfast = int(sys.argv[4]) if len(sys.argv) > 4 else 4
SRC = {"original": f"{W}/src/original.mp4", "genjutsu_kitchen": f"{W}/src/genjutsu_kitchen.mp4",
       "genjutsu_redsquare": f"{W}/src/genjutsu_redsquare.mp4", "ours": ours}
def grab(path, t):
    c = cv2.VideoCapture(path); fps = c.get(cv2.CAP_PROP_FPS); n = int(c.get(cv2.CAP_PROP_FRAME_COUNT))
    i = int(round(t * fps))
    if i >= n: return None
    c.set(cv2.CAP_PROP_POS_FRAMES, i); ok, f = c.read(); return f if ok else None
def dur(path):
    c = cv2.VideoCapture(path); return c.get(cv2.CAP_PROP_FRAME_COUNT) / c.get(cv2.CAP_PROP_FPS)
D = dur(ours)
def tile(t_rel, h):
    row = []
    for k, p in SRC.items():
        f = grab(p, t_rel if k == "ours" else t0 + t_rel)
        if f is None: f = np.zeros((854, 480, 3), np.uint8)
        f = cv2.resize(f, (int(h * 480 / 854), h), interpolation=cv2.INTER_AREA)
        cv2.putText(f, f"{k} {t0+t_rel:.1f}s", (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
        row.append(f)
    return np.hstack(row)
os.makedirs(f"{W}/qa", exist_ok=True)
times = np.arange(0, D - 0.05, 0.5)
rows = [tile(t, 320) for t in times]
for i in range(0, len(rows), 4):
    cv2.imwrite(f"{W}/qa/{tag}_sheet2fps_{i//4}.jpg", np.vstack(rows[i:i+4]), [cv2.IMWRITE_JPEG_QUALITY, 88])
# fastest moments in original within window
c = cv2.VideoCapture(SRC["original"]); fps = c.get(cv2.CAP_PROP_FPS); c.set(cv2.CAP_PROP_POS_FRAMES, int(t0 * fps))
prev = None; e = []
for j in range(int(D * fps)):
    ok, f = c.read()
    if not ok: break
    g = cv2.cvtColor(cv2.resize(f, (180, 320)), cv2.COLOR_BGR2GRAY).astype(np.float32)
    e.append(0 if prev is None else np.abs(g - prev).mean()); prev = g
e = np.array(e); picks = []
for j in np.argsort(-e):
    if all(abs(j - p) > fps * 0.6 for p in picks): picks.append(j)
    if len(picks) == nfast: break
for p in sorted(picks):
    t = p / fps
    cv2.imwrite(f"{W}/qa/{tag}_fast_{t0+t:.2f}s.png", tile(t, 854))
# sharpness (Laplacian var) per source over the window
for k, p in SRC.items():
    v = []
    for t in times:
        f = grab(p, t if k == "ours" else t0 + t)
        if f is not None: v.append(cv2.Laplacian(cv2.cvtColor(cv2.resize(f, (480, 854)), cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())
    print(k, "lapvar_median", round(float(np.median(v)), 1) if v else None)
print("fast moments", [round(t0 + p / fps, 2) for p in sorted(picks)])
