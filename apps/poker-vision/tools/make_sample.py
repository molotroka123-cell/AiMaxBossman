"""Cut a small, self-describing replay sample (frames + expected values taken from DOM truth, never from the pipeline)."""
import json, shutil, sys
from pathlib import Path

src, session, dst = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
n = int(sys.argv[4]) if len(sys.argv) > 4 else 24
rows = [json.loads(l) for l in (src / session / "labels.jsonl").read_text(encoding="utf-8").splitlines()]
end = None
for i in range(n, len(rows)):
    t = rows[i]["truth"]
    if (rows[i]["stable"] and not t["animating"] and rows[i]["tag"] == "settled" and len(t["board"]) >= 3 and t.get("pot_text")
            and t.get("pot_vis", 0) >= 0.99 and all(c.get("vis_corner", 0) >= 0.99 for c in t["hero_cards"]) and len(t["hero_cards"]) == 2
            and rows[i - 1]["truth"].get("pot_text") == t["pot_text"] and rows[i - 2]["truth"].get("pot_text") == t["pot_text"]):
        end = i; break
assert end is not None, "no suitable settled frame"
dst.mkdir(parents=True, exist_ok=True)
out = []
for k, r in enumerate(rows[end - n + 1:end + 1]):
    name = f"{k:04d}.png"
    shutil.copyfile(src / session / r["frame"], dst / name)
    out.append({"frame": name, "t_ms": r["t_ms"], "hand_idx": r["hand_idx"], "tag": r["tag"]})
(dst / "labels.jsonl").write_text("\n".join(json.dumps(x) for x in out) + "\n", encoding="utf-8")
t = rows[end]["truth"]
(dst / "expected_last_frame.json").write_text(json.dumps({
    "source": f"{session} frame {rows[end]['frame']} (DOM truth)", "hero_cards": [c["card"] for c in t["hero_cards"]],
    "board": [c["card"] for c in t["board"]], "pot": t["pot_text"], "hero_stack": t.get("hero_stack_text")}, indent=1), encoding="utf-8")
print(dst, len(out), "frames; last:", rows[end]["frame"])
