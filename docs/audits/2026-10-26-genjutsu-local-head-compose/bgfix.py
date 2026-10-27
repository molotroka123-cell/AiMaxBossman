import cv2, subprocess, os, numpy as np
W = r"D:\Bossman\work\genjutsu-local-20261026"
src = cv2.VideoCapture(r"D:\Bossman\work\genjutsu-20261026\source.mp4"); sfps = src.get(5)
cur = cv2.VideoCapture(W + r"\out\v720\anim720_h768_alpha.mp4")
msk = cv2.VideoCapture(W + r"\src720\mask720.mp4")
raw = W + r"\out\v720\anim720_final_raw.avi"
wr = cv2.VideoWriter(raw, cv2.VideoWriter_fourcc(*"MJPG"), 16.0, (720, 1280)); n = 0
k = 0
while True:
    ok, fc = cur.read(); ok2, fm = msk.read()
    if not (ok and ok2): break
    src.set(cv2.CAP_PROP_POS_FRAMES, int(round((4.0 + k / 16.0) * sfps))); ok3, fs = src.read()
    if not ok3: break
    fs = cv2.resize(fs, (720, 1280), interpolation=cv2.INTER_AREA)
    m = (cv2.cvtColor(fm, cv2.COLOR_BGR2GRAY) > 127).astype(np.uint8)
    m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31)))
    a = cv2.GaussianBlur(m.astype(np.float32), (0, 0), 6)[..., None]
    out = fc.astype(np.float32) * a + fs.astype(np.float32) * (1 - a)
    wr.write(np.clip(out, 0, 255).astype(np.uint8)); n += 1; k += 1
wr.release()
dst = W + r"\out\v720\anim720_final.mp4"
subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", raw, "-c:v", "libx264", "-crf", "14", "-pix_fmt", "yuv420p", dst], check=True); os.remove(raw)
print("final frames", n)
