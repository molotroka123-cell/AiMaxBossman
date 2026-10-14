"""Render what a gcode will actually print: model = orange, supports = green; iso + side view, 5% of moves."""
import re, sys
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

src, out = Path(sys.argv[1]), Path(sys.argv[2])
x = y = z = 0.0
typ, pts = "", {"model": [], "support": []}
mv = re.compile(r"^G[123] (?=.*E[\d.])(.*)")
for line in src.open(encoding="utf-8", errors="replace"):
    if line.startswith(";TYPE:"):
        typ = line[6:].strip()
        continue
    if line.startswith("G1") or line.startswith("G0") or line.startswith("G2") or line.startswith("G3"):
        for k, v in re.findall(r"([XYZ])(-?[\d.]+)", line):
            if k == "X": x = float(v)
            elif k == "Y": y = float(v)
            else: z = float(v)
        if mv.match(line) and typ and typ not in ("Custom", "Skirt", "Brim", "Prime tower"):
            pts["support" if "Support" in typ else "model"].append((x, y, z))
fig = plt.figure(figsize=(14, 6), facecolor="#16161c")
for i, (elev, azim, title) in enumerate(((25, -60, "3D"), (0, -90, "сбоку"))):
    ax = fig.add_subplot(1, 2, i + 1, projection="3d", facecolor="#16161c")
    for key, col in (("support", "#39e66b"), ("model", "#ff7a3d")):
        a = np.array(pts[key][::6] or [(0, 0, 0)])
        ax.scatter(a[:, 0], a[:, 1], a[:, 2], s=0.5, c=col, alpha=0.85, linewidths=0)
    allp = np.array(pts["model"][::6] + pts["support"][::6])
    c, r = (allp.max(0) + allp.min(0)) / 2, (allp.max(0) - allp.min(0)).max() / 2
    ax.set_xlim(c[0] - r, c[0] + r); ax.set_ylim(c[1] - r, c[1] + r); ax.set_zlim(0, 2 * r)
    ax.view_init(elev, azim); ax.set_axis_off(); ax.set_title(title, color="white")
fig.suptitle(f"{src.stem}   оранжевый = модель, зелёный = поддержки", color="white", fontsize=12)
fig.savefig(out, dpi=90, facecolor=fig.get_facecolor())
print("ok", out.name, len(pts["model"]), len(pts["support"]))
