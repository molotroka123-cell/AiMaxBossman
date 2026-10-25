"""Person mask video via Robust Video Matting (onnx, CPU). white = person."""
import sys, cv2, numpy as np, onnxruntime as ort
src, dst = sys.argv[1], sys.argv[2]
thr = float(sys.argv[3]) if len(sys.argv) > 3 else 0.5
sess = ort.InferenceSession(r"D:\Bossman\models\media\rvm\rvm_mobilenetv3_fp32.onnx", providers=["CPUExecutionProvider"])
cap = cv2.VideoCapture(src); fps = cap.get(cv2.CAP_PROP_FPS)
w, h = int(cap.get(3)), int(cap.get(4))
out = cv2.VideoWriter(dst + ".tmp.avi", cv2.VideoWriter_fourcc(*"FFV1"), fps, (w, h), isColor=True)
rec = [np.zeros([1, 1, 1, 1], np.float32)] * 4
dr = np.array([0.375], np.float32)
n = 0
while True:
    ok, f = cap.read()
    if not ok: break
    x = cv2.cvtColor(f, cv2.COLOR_BGR2RGB).astype(np.float32).transpose(2, 0, 1)[None] / 255.
    fgr, pha, *rec = sess.run([], {"src": x, "r1i": rec[0], "r2i": rec[1], "r3i": rec[2], "r4i": rec[3], "downsample_ratio": dr})
    m = (pha[0, 0] > thr).astype(np.uint8) * 255
    out.write(cv2.merge([m, m, m])); n += 1
out.release()
import subprocess
subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", dst + ".tmp.avi", "-c:v", "libx264", "-crf", "0", "-pix_fmt", "yuv420p", dst], check=True)
import os; os.remove(dst + ".tmp.avi")
print("frames", n, "fps", fps, dst)
