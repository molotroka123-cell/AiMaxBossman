import cv2, json, subprocess, os, numpy as np, onnxruntime as ort
W = r"D:\Bossman\work\genjutsu-local-20261026"
sess = ort.InferenceSession(r"D:\Bossman\models\media\rvm\rvm_mobilenetv3_fp32.onnx", providers=["CPUExecutionProvider"])
cap = cv2.VideoCapture(W + r"\out\head768\head768_gen_ff.mp4"); fg, al = [], []
rec = [np.zeros([1, 1, 1, 1], np.float32)] * 4; dr = np.array([0.5], np.float32)
while True:                                   # matte the GENERATED head: foreground colours free of the head video's own background (no veil)
    ok, f = cap.read()
    if not ok: break
    x = cv2.cvtColor(f, cv2.COLOR_BGR2RGB).astype(np.float32).transpose(2, 0, 1)[None] / 255.
    fgr, pha, *rec = sess.run([], {"src": x, "r1i": rec[0], "r2i": rec[1], "r3i": rec[2], "r4i": rec[3], "downsample_ratio": dr})
    fg.append((fgr[0].transpose(1, 2, 0)[..., ::-1] * 255).clip(0, 255).astype(np.float32)); al.append(pha[0, 0].astype(np.float32))
B = json.load(open(W + r"\src\head768\boxes.json")); sx = 720 / B["src_w"]; sy = 1280 / B["src_h"]
cb = cv2.VideoCapture(W + r"\out\anim720.mp4"); fps = cb.get(5)
raw = W + r"\out\v720\anim720_h768_E_raw.avi"
wr = cv2.VideoWriter(raw, cv2.VideoWriter_fourcc(*"MJPG"), fps, (720, 1280)); n = 0
for k, b in enumerate(B["boxes"]):
    ok, fb = cb.read()
    if not (ok and k < len(al)): break
    x, y, s = int(b["x"] * sx), int(b["y"] * sy), max(16, int(b["s"] * min(sx, sy)))
    x, y = min(max(0, x), 720 - s), min(max(0, y), 1280 - s)
    head = cv2.resize(fg[k], (s, s), interpolation=cv2.INTER_AREA)
    a = cv2.resize(al[k], (s, s), interpolation=cv2.INTER_AREA)
    yy, xx = np.mgrid[0:s, 0:s].astype(np.float32) / s
    a = a * np.clip(np.minimum.reduce([xx, 1 - xx, yy, 1 - yy]) / 0.12, 0, 1) * np.clip((0.84 - yy) / 0.10, 0, 1)
    roi = fb[y:y + s, x:x + s].astype(np.float32)
    m = a > 0.6
    if m.sum() > 200:                          # match to the base person's own pixels in the same place (not to the background)
        for c in range(3):
            hm, hs = head[..., c][m].mean(), head[..., c][m].std() + 1e-6
            rm, rs = roi[..., c][m].mean(), roi[..., c][m].std() + 1e-6
            head[..., c] = (head[..., c] - hm) * min(1.3, max(0.77, rs / hs)) + 0.6 * rm + 0.4 * hm
    out = roi * (1 - a[..., None]) + np.clip(head, 0, 255) * a[..., None]
    fb[y:y + s, x:x + s] = np.clip(out, 0, 255).astype(np.uint8)
    wr.write(fb); n += 1
wr.release()
dst = W + r"\out\v720\anim720_h768_E.mp4"
subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", raw, "-c:v", "libx264", "-crf", "14", "-pix_fmt", "yuv420p", dst], check=True); os.remove(raw)
print("composited E", n)
