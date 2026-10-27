import cv2, json, subprocess, numpy as np, os
W = r"D:\Bossman\work\genjutsu-local-20261026"
B = json.load(open(W + r"\src\head768\boxes.json"))
sx = 720 / B["src_w"]; sy = 1280 / B["src_h"]
cb = cv2.VideoCapture(W + r"\out\anim720.mp4"); ch = cv2.VideoCapture(W + r"\out\head768\head768_gen_ff.mp4"); cm = cv2.VideoCapture(W + r"\src\head768\head_mask.mp4")
fps = cb.get(5); raw = W + r"\out\v720\anim720_h768_seam_raw.avi"
wr = cv2.VideoWriter(raw, cv2.VideoWriter_fourcc(*"MJPG"), fps, (720, 1280)); n = 0
for b in B["boxes"]:
    ok, fb = cb.read(); ok2, fh = ch.read(); ok3, fm = cm.read()
    if not (ok and ok2 and ok3): break
    x, y, s = int(b["x"] * sx), int(b["y"] * sy), max(16, int(b["s"] * min(sx, sy)))
    x, y = min(max(0, x), 720 - s), min(max(0, y), 1280 - s)
    head = cv2.resize(fh, (s, s), interpolation=cv2.INTER_AREA)
    pm = cv2.resize(cv2.cvtColor(fm, cv2.COLOR_BGR2GRAY), (s, s), interpolation=cv2.INTER_AREA)
    ell = np.zeros((s, s), np.uint8); cv2.ellipse(ell, (s // 2, int(s * 0.46)), (int(s * 0.34), int(s * 0.40)), 0, 0, 360, 255, -1)
    mask = cv2.bitwise_and((pm > 127).astype(np.uint8) * 255, ell)
    mask = cv2.erode(mask, np.ones((max(3, s // 40),) * 2, np.uint8))
    mask[0, :] = mask[-1, :] = mask[:, 0] = mask[:, -1] = 0
    if mask.sum() > 255 * 50:
        try:
            fb = cv2.seamlessClone(head, fb, mask, (x + s // 2, y + s // 2), cv2.NORMAL_CLONE)
        except cv2.error:
            pass
    wr.write(fb); n += 1
wr.release()
out = W + r"\out\v720\anim720_h768_seam.mp4"
subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", raw, "-c:v", "libx264", "-crf", "14", "-pix_fmt", "yuv420p", out], check=True); os.remove(raw)
print("seam frames", n)
