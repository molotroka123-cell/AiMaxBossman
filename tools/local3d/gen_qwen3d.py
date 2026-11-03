"""Qwen3.5-4B-3D-Coder (Ollama: qwen35-3d-coder) -> Three.js builder -> GLB via js/export_glb.mjs. Retries with the error text."""
import json, re, subprocess, sys, time, urllib.request
from pathlib import Path
ROOT = Path("D:/Bossman/models3d")  # node_modules/three + js/export_glb.mjs live here
SYS = ("You write JavaScript for Three.js (r16x, global THREE passed as argument). No DOM, no import, no external assets. "
       "Output ONLY one ```js code block defining `function createModel(THREE)` that returns a THREE.Group. "
       "Use only BoxGeometry, SphereGeometry, CylinderGeometry, ConeGeometry, CapsuleGeometry, TorusGeometry, with MeshStandardMaterial flat colors. "
       "Model must stand on y=0, face -Z, be about 6 units tall, built from 30-120 meshes, stylized low-poly cartoon (Roblox-like), "
       "with clear head, body, arms, legs, face details (eyes, nose), ears/tail where relevant. Keep segments low (8-16).")
def ask(prompt, think=False):
    body = json.dumps({"model": "qwen35-3d-coder", "system": SYS, "prompt": prompt, "stream": False, "think": think,
                       "options": {"temperature": 0.6, "num_predict": 6000}}).encode()
    r = urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:11434/api/generate", body, {"Content-Type": "application/json"}), timeout=1800)
    return json.loads(r.read())
def extract(text):
    m = re.findall(r"```(?:js|javascript)?\n(.*?)```", text, re.S)
    return max(m, key=len) if m else text
def run(name, prompt, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    last = ""; t0 = time.time()
    for attempt in range(1, 4):
        p = prompt if attempt == 1 else f"{prompt}\n\nYour previous code failed with: {last}\nFix it and output the full corrected code block."
        res = ask(p); code = extract(res.get("response", ""))
        (out_dir / f"{name}.js").write_text(code, encoding="utf-8")
        r = subprocess.run(["node", str(ROOT / "js" / "export_glb.mjs")  # ESM resolves `three` next to the script, str(out_dir / f"{name}.js"), str(out_dir / f"{name}.glb")],
                           capture_output=True, text=True, cwd=ROOT / "js")
        if r.returncode == 0:
            info = json.loads(r.stdout.strip().splitlines()[-1]); info.update(name=name, attempts=attempt, seconds=round(time.time() - t0), tokens=res.get("eval_count")); return info
        last = (r.stderr or r.stdout).strip()[-300:]
    return {"name": name, "failed": last, "attempts": 3, "seconds": round(time.time() - t0)}
if __name__ == "__main__":
    out = Path(sys.argv[1]).resolve(); jobs = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    for name, prompt in jobs.items():
        print(json.dumps(run(name, prompt, out), ensure_ascii=False), flush=True)
