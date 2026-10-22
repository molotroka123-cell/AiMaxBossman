"""STL -> scaled (longest side = L mm) -> OrcaSlicer, Neptune 4 Pro 0.4, Elegoo PLA at 218 C, supports on.

    python tools/print3d/slice_any.py <file.stl> <L_mm> <rot: none|faceup> <process preset or .json> <tag> <gentle 0|1>

Uses the app's own adapter (apps/ai-3d-maker, ai_3d_maker.orca.resolve_profile) and the OrcaSlicer CLI with a
private --datadir, so the owner's open Orca and its config are untouched. Skill: .agents/skills/print3d-orca.
Env: BOSSMAN_ORCA_EXE (orca-slicer.exe), BOSSMAN_PRINT_OUT (output root, default Desktop),
     BOSSMAN_PRINT_NOZZLE (default 218; owner 22.10 also uses 220), BOSSMAN_PRINT_BED (e.g. 60: sets EVERY plate type, the
     Cool Plate profile otherwise heats to 35), BOSSMAN_PRINT_RAFT (raft layers, default 0).
"""
import json, os, shutil, struct, subprocess, sys, tempfile, time
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "ai-3d-maker" / "src"))
from ai_3d_maker import orca  # noqa: E402

src_arg, L, rot, proc, tag, gentle = sys.argv[1], float(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6] == "1"
SRC = Path(src_arg)
name = SRC.stem
EXE = os.environ.get("BOSSMAN_ORCA_EXE", r"C:\Users\asd\Bossman\apps-local\OrcaSlicer\orca-slicer.exe")
OUT = Path(os.environ.get("BOSSMAN_PRINT_OUT", Path(os.environ.get("USERPROFILE", ".")) / "Desktop")) / f"{name.capitalize()}_Neptune4Pro"
RUN = Path(tempfile.gettempdir()) / "bossman-print3d" / f"run_{name}_{int(L)}"
PRINTER, FILAMENT = "Elegoo Neptune 4 Pro 0.4 nozzle", "Elegoo PLA @EN4 Series"
SUPPORT = {"enable_support": "1", "support_type": "tree(auto)", "support_style": "organic",
           "support_on_build_plate_only": "0", "brim_type": "auto_brim"}
if gentle:
    SUPPORT.update({"support_threshold_angle": "40", "support_top_z_distance": "0.24",
                    "support_bottom_z_distance": "0.24", "support_interface_top_layers": "2",
                    "support_interface_bottom_layers": "0", "support_interface_spacing": "0.4",
                    "support_object_xy_distance": "0.5"})
NOZZLE = os.environ.get("BOSSMAN_PRINT_NOZZLE", "218")
BED = os.environ.get("BOSSMAN_PRINT_BED", "")
RAFT = os.environ.get("BOSSMAN_PRINT_RAFT", "0")
PLA218 = {"nozzle_temperature": [NOZZLE], "nozzle_temperature_initial_layer": [NOZZLE]}
if BED:
    for _k in ("hot_plate_temp", "cool_plate_temp", "eng_plate_temp", "textured_plate_temp"):
        PLA218[_k] = [BED]; PLA218[_k + "_initial_layer"] = [BED]
if RAFT != "0":
    SUPPORT["raft_layers"] = RAFT

shutil.rmtree(RUN, ignore_errors=True)
(RUN / "out").mkdir(parents=True)
(RUN / "datadir").mkdir()
OUT.mkdir(exist_ok=True)
raw = SRC.read_bytes()
n = struct.unpack("<I", raw[80:84])[0]
tri = np.frombuffer(raw[84:84 + n * 50], dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])).copy()
v = tri["v"].reshape(-1, 3).astype(np.float64)
if rot == "faceup":                                   # face (-Y) -> +Z
    v = np.stack([v[:, 0], v[:, 2], -v[:, 1]], axis=1)
    tri["n"] = np.stack([tri["n"][:, 0], tri["n"][:, 2], -tri["n"][:, 1]], axis=1)
k = L / (v.max(0) - v.min(0)).max()
v = (v - [(v[:, 0].min() + v[:, 0].max()) / 2, (v[:, 1].min() + v[:, 1].max()) / 2, v[:, 2].min()]) * k
tri["v"] = v.reshape(-1, 3, 3).astype("<f4")
stl = RUN / f"{name}_{int(L)}mm.stl"
stl.write_bytes(raw[:80] + struct.pack("<I", n) + tri.tobytes())

files = {}
for kind, pname in (("machine", PRINTER), ("process", proc), ("filament", FILAMENT)):
    flat, _ = orca.resolve_profile(kind, pname, vendor="Elegoo", orca=EXE)
    flat.update(SUPPORT if kind == "process" else PLA218 if kind == "filament" else {})
    p = RUN / f"{kind}.json"
    p.write_text(json.dumps(flat, indent=1, ensure_ascii=False), encoding="utf-8")
    files[kind] = p
base = f"{name}_{int(L)}mm_PLA{NOZZLE}{'_bed' + BED if BED else ''}_{tag}"
cmd = [EXE, "--datadir", str(RUN / "datadir"), "--load-settings", f"{files['machine']};{files['process']}",
       "--load-filaments", str(files["filament"]), "--arrange", "1", "--orient", "0", "--slice", "0",
       "--outputdir", str(RUN / "out"), "--export-3mf", f"{base}.3mf", str(stl)]
t = time.time()
p = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=3000,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
res = {"size_mm": np.round(v.max(0) - v.min(0), 1).tolist(), "rc": p.returncode, "wall_s": round(time.time() - t, 1),
       "stderr_tail": p.stderr.decode(errors="replace")[-300:]}
g = sorted((RUN / "out").glob("plate_*.gcode"))
if g:
    dst = OUT / f"{base}.gcode"
    shutil.copyfile(g[0], dst)
    res["gcode"] = str(dst)
    s = orca.parse_gcode_summary(dst.read_text(encoding="utf-8", errors="replace"))
    res["time"], res["g"], res["layers"] = s.get("estimated_time"), s.get("filament_used", {}).get("g"), s.get("layers")
for m in list((RUN / "out").glob("*.3mf")) + list(RUN.glob("*.3mf")):
    shutil.copyfile(m, OUT / m.name)
    res["3mf"] = str(OUT / m.name)
print(json.dumps(res, ensure_ascii=False))
