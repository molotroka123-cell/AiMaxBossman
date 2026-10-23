"""Before/after sheets for a multi-part print set: STL renders (before) and real-gcode toolpaths (after).

    python tools/print3d/before_after_sheet.py <dir with *.stl> <out dir> <dir with */*.gcode>

Run with a Python that has matplotlib (apps-local/figurine/venv). Orange = model, green = supports; titles carry
size (before) and slicer time + grams (after). Proven 23.10 on the Source One V6 drone set (7 parts).
"""
import glob, re, struct, sys
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

SRC = Path(sys.argv[1]); OUT = Path(sys.argv[2]); VIEW = Path(sys.argv[3])
OUT.mkdir(parents=True, exist_ok=True)


def tris(path):
    b = path.read_bytes(); n = struct.unpack("<I", b[80:84])[0]
    a = np.frombuffer(b[84:84 + n * 50], dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]))
    return a["v"].astype(float)


stls = sorted(SRC.glob("*.stl"))
fig = plt.figure(figsize=(16, 9), dpi=110)
for i, p in enumerate(stls):
    v = tris(p)
    if len(v) > 30000:
        v = v[:: len(v) // 30000 + 1]
    ax = fig.add_subplot(2, 4, i + 1, projection="3d")
    n = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0]); n /= (np.linalg.norm(n, axis=1, keepdims=True) + 1e-9)
    shade = 0.35 + 0.65 * np.clip(n @ np.array([0.4, -0.5, 0.75]), 0, 1)
    ax.add_collection3d(Poly3DCollection(v, facecolors=plt.cm.Oranges(0.35 + 0.55 * shade), edgecolor="none"))
    lo, hi = v.reshape(-1, 3).min(0), v.reshape(-1, 3).max(0); c = (lo + hi) / 2; r = (hi - lo).max() / 2
    ax.set_xlim(c[0] - r, c[0] + r); ax.set_ylim(c[1] - r, c[1] + r); ax.set_zlim(c[2] - r, c[2] + r)
    ax.view_init(28, -55); ax.set_axis_off()
    sz = hi - lo
    ax.set_title(f"{p.stem}\n{sz[0]:.0f}x{sz[1]:.0f}x{sz[2]:.0f} мм", fontsize=9)
fig.suptitle(f"ДО нарезки: {len(stls)} деталей ({SRC.name})", fontsize=12)
fig.tight_layout(); before = OUT / "SourceOne_V6_BEFORE_stl.png"; fig.savefig(before); plt.close(fig)



def toolpaths(path):
    """Extrusion segments split into model / support, zoomed to the part (not the whole bed)."""
    x = y = z = 0.0; e_prev = 0.0; kind = "model"; segs = {"model": [], "support": []}
    for line in open(path, encoding="utf-8", errors="replace"):
        if line.startswith(";TYPE:"):
            kind = "support" if "Support" in line else "model"; continue
        if not line.startswith(("G1 ", "G0 ")):
            continue
        nx, ny, nz = x, y, z; e = None
        for tok in line.split(";")[0].split()[1:]:
            k, v = tok[0], tok[1:]
            try:
                if k == "X": nx = float(v)
                elif k == "Y": ny = float(v)
                elif k == "Z": nz = float(v)
                elif k == "E": e = float(v)
            except ValueError:
                pass
        if e is not None and e > 0 and line.startswith("G1") and (nx, ny) != (x, y):
            segs[kind].append(((x, y, nz), (nx, ny, nz)))
        x, y, z = nx, ny, nz
    return segs


fig = plt.figure(figsize=(16, 9), dpi=110)
gcodes = sorted(glob.glob(str(VIEW / "*" / "*.gcode")))
for i, g in enumerate(gcodes):
    segs = toolpaths(g)
    allp = np.array([p for s in segs.values() for seg in s for p in seg]) if any(segs.values()) else np.zeros((1, 3))
    ax = fig.add_subplot(2, 4, i + 1, projection="3d")
    from mpl_toolkits.mplot3d.art3d import Line3DCollection
    for kind, col in (("model", "#e8590c"), ("support", "#2f9e44")):
        s = segs[kind]
        if s:
            step = max(1, len(s) // 60000)
            ax.add_collection3d(Line3DCollection(s[::step], colors=col, linewidths=0.35, alpha=0.9))
    lo, hi = np.percentile(allp, 1, axis=0), np.percentile(allp, 99, axis=0); c = (lo + hi) / 2; r = max((hi - lo).max() / 2 * 1.15, 1)
    ax.set_xlim(c[0] - r, c[0] + r); ax.set_ylim(c[1] - r, c[1] + r); ax.set_zlim(lo[2], lo[2] + 2 * r)
    ax.view_init(28, -55); ax.set_axis_off()
    txt = open(g, encoding="utf-8", errors="replace").read()
    tm = re.search(r"total estimated time: ([^\n;]+)", txt) or re.search(r"estimated printing time[^=]*= ([^\n]+)", txt)
    gr = re.search(r"total filament weight \[g\] : ([\d.]+)", txt) or re.search(r"filament used \[g\] = ([\d.]+)", txt)
    ax.set_title(f"{Path(g).stem.split('_')[0]}\n{tm.group(1).strip() if tm else '?'} · {gr.group(1) if gr else '?'} г · "
                 f"поддержки {len(segs['support'])} сегм.", fontsize=9)
fig.suptitle("ПОСЛЕ нарезки (рендер из реального G-code): оранжевый = модель, зелёный = поддержки", fontsize=12)
fig.tight_layout(); after = OUT / "SourceOne_V6_AFTER_gcode.png"; fig.savefig(after); plt.close(fig)
print(before); print(after)
