# Bossman 3D objects for games (voxel3d) — CANDIDATE, branch `rc19/h-3d`

**Status (2026-09-28): CANDIDATE, not part of the 1.9 freeze.** Lead integrates after review.

## Кратко (для владельца)
- `tools/voxel3d`: фраза («дубовое дерево») → локальная Qwen пишет компактный JSON-чертёж вокселей → детерминированный сборщик делает `.vox` (MagicaVoxel), `.glb` (жадное мешевание, цвета по материалам) и структуру BossBlocks (тот же формат записей, что и сохранение игры).
- Всё проверяется без модели: границы, «нет висящих кусков», чтение файлов независимыми OSS-библиотеками, превью PNG. Vision-модель описывает превью — это сигнал, не доказательство.
- В игру объект попадает только флагом `--apply` (это изменение файлов проекта → обычное одобрение Bossman).
- Meshy-подобное «картинка → 3D»: см. таблицу ниже; реально запущено здесь только то, что отмечено «ran here».

## Results on the owner PC (2026-09-28)

Model `bossman-fast-qwen36-35b-a3b-q5` (native Ollama, `think:false`), shared machine (other workstreams were using Ollama, so the times include queueing).
Evidence: `C:\Users\asd\Bossman\evidence\rc19\h\objects-run1\`, `objects-run2\` (spec, transcript, .vox, .glb, structure, preview, report per object; `summary.md`, `contact_sheet.png`).

**Run 1** (first prompt): 7/8 valid. The house FAILED after 3 tries: the model enumerated long voxel lists (two replies were not complete JSON) and then put the roof above the declared height. The bridge came out as a solid 11×4×5 block. Fixes: grid auto-fit, `roof` op, `voxels` cap of 24, cut-off feedback, open-space rule, a second few-shot example (stone well, not a test object), one lint turn for solid blocks.

**Run 2** (after fixes, fresh generation, same prompts): **8/8 valid, all deterministic checks PASS.**

| Object | Voxels | Size (X×Y×Z) | GLB verts/faces | Checks | Tries | Vision label / description | Match | In game |
|---|---|---|---|---|---|---|---|---|
| oak_tree | 254 | 9×14×9 | 336/168 | PASS | 1 | tree / "A Minecraft tree." | Y | prop |
| small_house | 1243 | 11×13×11 (auto-fit from 11×10×11) | 208/104 | PASS | 1 | house / "A house with a red roof and gray walls." | Y | prop |
| torch | 12 | 3×8×3 | 120/60 | PASS | 1 | torch / "A torch." | Y | prop |
| chest | 60 | 5×4×3 | 124/62 | PASS | 1 | chest / "Gold ore block" | Y | prop + structure |
| creature (pig) | 78 | 5×4×7 | 188/94 | PASS | 1 | chest / "A pink Minecraft pig." | N (description right) | prop + structure |
| crystal | 70 | 7×10×7 | 156/78 | PASS | 3 | tower / "Purple voxel tower…" | N | prop |
| bridge | 170 | 11×4×5 | 104/52 | PASS | 1 | bridge / "A stone and wood bridge." | Y | prop + structure |
| lamp_post | 27 | 3×12×3 | 72/36 | PASS | 1 | lamp post / "A torch." | Y | prop |
| triposr_chair (image→3D→voxel) | 465 | 10×17×9 | 1368/684 | PASS | – | – | – | prop |

Checks = non-empty, within bounds, grounded, one connected part, `.vox` re-read voxel-for-voxel by py-vox-io, GLB re-read by pygltflib + trimesh (bounds = voxel bbox, area = exposed faces, winding correct).
Determinism: rebuilding all 8 from the saved specs (`run_batch.py --reuse-specs`, no model) gave 24/24 byte-identical `.vox`/`.glb`/structure files.
Vision forced choice: 6/8 (run 1: 6/7). Honest quality notes: the house is solid inside (a prop, not enterable); the crystal is a single pillar rather than a cluster; the pig's legs are barely visible.

**Game sandbox** (`C:\Users\asd\Bossman\rc19-game-sandbox\bossblocks`, a copy; the original is untouched; custom user dir so the owner's save is never touched):
- `godot --headless --import`: exit 0 (4 s). `tests/voxel_import.gd`: `VOXEL_IMPORT_PASS props=9 structures=3`. All 9 GLBs load as scenes with exact block bounds and one surface per colour. The chest, pig and bridge are stamped through `main.gd::_add_block`, saved by the game and restored by a second game instance. The other 6 are taller than the game's world limit (y ≤ 5), so they are props only.
- pytest in the sandbox: **7/7 PASS** (the 6 original tests plus `test_voxel_import.py`).
- Real-renderer screenshot (Vulkan, Radeon 8060S): `evidence\rc19\h\godot\voxel-props.png`.

**Text → image → 3D, fully local** (one object): Studio's z-image-turbo via sd.cpp Vulkan (768², 8 steps) 98 s → white-background cut-out → TripoSR CPU ≈ 45 s (3.6 GiB) → `from_mesh.py` 1 s. The chain runs end to end, but the chest came out lumpy with a shadow skirt: clearly worse than the voxel-spec route for game props. The TripoSR example chair voxelizes well.

## How it works (same split as Motion Studio)

| Part | File | Role |
|---|---|---|
| Local model | `generate.py` | Writes the spec only: `{"name","size":[X,Y,Z],"palette":{name:"#rrggbb"},"ops":[...]}`. Native Ollama `/api/chat`, `think:false`, `format:json`, one few-shot example, up to 3 tries with the validator's errors sent back. Honours the shared-machine PAUSE file before every call. |
| Validator / repair | `spec.py` | Bounds (each dim 1..32, Y up), ≤16 colours, ≤80 ops, known ops/fields/materials. Safe logged repairs (clamp size, `#rgb`→`#rrggbb`, fuzzy material names, `type`→`op`, clip out-of-grid voxels, ground the object, drop floating bits ≤5 %). Larger disconnected parts are an error the model must fix. |
| Builder | `spec.build` | Ops: box (hollow), sphere/ellipsoid, cylinder (x/y/z), cone (up/down), line, voxels, layer (char grid), mirror, `air` carving. Pure stdlib, deterministic. |
| `.vox` | `vox.py` | MagicaVoxel v150 (SIZE/XYZI/RGBA); Y-up → Z-up with handedness preserved. |
| `.glb` | `mesh.py` | Greedy meshing (0fps algorithm), one primitive per colour, flat normals, PBR metallic 0 / roughness 1, 1 voxel = 1 m = 1 game block, origin at base centre. Pure stdlib. |
| BossBlocks | `bossblocks.py` | Structure JSON = the game's save records `{x,y,z,kind}` (+`color`), kind = nearest of Grass/Sand/Stone in CIELAB, `fits_world_at_origin` flag. `install()` copies GLB + structure + Godot helper/test scripts. |
| Checks | `verify.py` | py-vox-io (BSD) re-reads `.vox` voxel-for-voxel; pygltflib + trimesh (MIT) re-read the GLB: bounds = voxel bbox, surface area = exposed unit faces (exact), every triangle's winding matches its normal, sane counts. |
| Preview | `render.py` | Pillow isometric painter's render, two opposite views, no GPU. |
| Vision check | `verify.vision_check` | `bossman-fast-qwen36-vision`: free description + forced choice among labels. Advisory only. |
| Godot side | `godot/*.gd` | `voxel_structures.gd` (load/stamp through the game's own `_add_block`, spawn GLB prop), `voxel_import.gd` (headless test), `voxel_capture.gd` (windowed screenshot), `test_voxel_import.py`. |

OSS decision: `.vox` and GLB writers are ~60 lines each over simple, public formats, so the product
path stays dependency-free (no numpy / unsigned `.pyd` under Smart App Control); established OSS
readers (py-vox-io, trimesh, pygltflib) are used as independent oracles in verification rather than
as writers. Greedy meshing follows the public 0fps description.

## BossBlocks format findings
- `scripts/main.gd` keeps `blocks: Dictionary[Vector3i → StaticBody3D]`, 1 m cubes, 3 kinds (1 Grass `#69aa56`, 2 Sand `#d8bc79`, 3 Stone `#858f9e`), solid colours, no texture atlas, no prefab format.
- The only persistent format is the save: `{"version":1,"blocks":[{"x","y","z","kind"}]}`; loading keeps y∈[-1,5], |x|,|z|≤8.
- Therefore two outputs: a **structure** (placeable blocks, lossy 3-colour mapping, must fit the small world) and a **GLB prop** (full colours, any size ≤32, Godot imports it natively as a scene).

## Meshy-like image/text → 3D (general objects)

Owner is in the Czech Republic (EU); machine is Windows 11 + AMD Radeon 8060S (no CUDA), Smart App Control ON.
"Ran here" = executed on this PC on 2026-09-28; everything else is from upstream docs (not verified here).

| Model | License / commercial use (EU) | Windows + AMD feasibility | Output / quality | Size | Status here |
|---|---|---|---|---|---|
| **TripoSR** (VAST + Stability, 2024) | MIT code + weights; commercial OK, no region limits | **CPU works** after two fixes: `torchmcubes` (needs a C++/CUDA build, no compiler here) → PyMCubes fallback patch; `transformers<5` (v5 renamed ViT keys). torch-directml also runs on the 8060S but is not faster (`grid_sampler_2d` falls back to CPU) and peaks at 14 GiB | Watertight vertex-coloured mesh; good silhouette on clean single-object images, soft/blobby details; poster-style images give a 2.5-D relief | ~1.7 GB weights | **Ran here**: CPU 8 threads ≈ 8.3 s network + 18–20 s marching cubes ≈ 28 s/object (+7 s model load), peak RAM 3.5 GiB; chair 41,864 v / 83,732 f, robot 80,644 v / 161,280 f, both watertight. Voxelized into the game pipeline (`from_mesh.py`) |
| Stable Fast 3D / SPAR3D (Stability, 2024–25) | Stability AI Community License: free incl. commercial below US$1M annual revenue, enterprise licence above; gated weights | Needs compiled texture-baker / UV-unwrapper extensions (CUDA or Metal); CPU path experimental; no MSVC toolchain on this PC | UV-textured PBR mesh, better than TripoSR | ~1 GB | Not run (toolchain + CUDA) |
| InstantMesh (TencentARC, 2024) | Apache-2.0 code/weights (check the Zero123++ weights licence and nvdiffrast's NVIDIA non-commercial licence) | Multi-view diffusion + FlexiCubes; CUDA ≥12.1, ~8 GB VRAM, nvdiffrast | Good textured meshes | several GB | Not feasible without CUDA |
| TRELLIS / **TRELLIS.2** (Microsoft, 2024 / 2025) | MIT (TRELLIS.2 4B); TRELLIS 1 pulls in nvdiffrast / Gaussian rasterizer with NVIDIA terms | Linux + CUDA 12.4, custom CUDA extensions (spconv/xformers; o-voxel, flex_gemm, cumesh for .2), 24 GB NVIDIA GPU recommended | State of the art open quality, PBR | 4B params | Not feasible on AMD/Windows today; backlog: watch ROCm-on-Windows ports |
| Hunyuan3D-2.0 / 2.1 / 2mini (Tencent) | **Tencent Hunyuan 3D Community License does NOT apply in the EU, UK and South Korea** → not usable by an owner in CZ | Community ports run it on Radeon 8060S via ROCm 7.2 on Windows | Very good shape + PBR texture | 3–10 GB | **Excluded (licence territory)**, regardless of hardware |
| TripoSG (VAST, 2025) | MIT | CUDA recommended, 1.5B; geometry only | High-fidelity shape, no texture | ~6 GB | Not run; next CPU candidate |
| Cloud: Higgsfield `generate_3d`, Meshy API, Tripo API | Paid / credits | n/a | Best quality, textured | n/a | **Not called.** Only as an explicit per-job owner-approved option |

Practical Meshy-like route on this PC today: **text → local image (Studio, prompt "single object, centred, plain background") → TripoSR on CPU (~30 s) → GLB**, and for BossBlocks **→ `from_mesh.py` voxelization (≤32) → .vox/.glb/structure**. The Studio image step was not re-run here (no suitable single-object Studio render existed; TripoSR's own example images were used, labelled as such). Evidence: `C:\Users\asd\Bossman\evidence\rc19\h\image3d\TRIPOSR_RESULT.md`.

## Invocation (proposal)

```
python tools/voxel3d/make_object.py "oak tree" --out <dir> [--vision] [--label tree]
python tools/voxel3d/make_object.py "oak tree" --out <dir> --game <BossBlocks dir>            # dry run: prints install plan
python tools/voxel3d/make_object.py "oak tree" --out <dir> --game <BossBlocks dir> --apply    # writes into the game
python tools/voxel3d/run_batch.py tools/voxel3d/examples/bossblocks_set.json --out <dir> --vision [--reuse-specs]
```

Proposed product integration (not implemented here, `terminal_cli/cli.py` is edited by other workstreams):
- **CMD:** `bossman 3d "oak tree" --voxel --game bossblocks` → the client posts a normal task (`kind: studio.3d`) to the backend; the backend runs `make_object.make()` into the task's artifact dir and returns the preview + checks. Writing into the game is a separate step: the task result offers "Install into BossBlocks", which is a file-mutation action and goes through the existing approval gate (same as coding-path diffs). `--game` resolves a registered project alias.
- **Studio:** a "3D object" card next to image/video: prompt, mode (Voxel / Image→3D), size preset (S ≤12, M ≤20, L ≤32), result panel with the two-view preview, checks, downloads (.vox/.glb/.json) and the approval button for "Install into game".
- **Cloud routes** (Higgsfield `generate_3d`, Meshy, Tripo API) appear only as disabled options that require explicit owner approval per job (they spend credits). Never automatic.

## Backlog (big or risky — not done here)

| # | Problem | Example | Benefit | Cost | Risk | Test |
|---|---|---|---|---|---|---|
| 1 | BossBlocks has only 3 block kinds, so stamped structures lose colour | the pink pig becomes Sand/Stone blocks | faithful placeable structures | main.gd: material per `color` from the structure palette + `color` in save records | changes the owner's game and save format | existing owner_* tests + a save/load round trip with colours |
| 2 | No textured faces (Minecraft look) | stone walls are flat grey | recognisable materials | 16×16 tile atlas per material + UVs in the GLB | more files; atlas style choices | GLB UV bounds, Godot import, screenshot |
| 3 | Spec quality depends on prompt engineering | run 1 bridge was a solid block | fewer retries, better shapes | collect prompt→valid spec pairs from runs (like Motion Studio `brief_to_spec.jsonl`) → LoRA | overfitting to own examples | held-out prompt set, vision + human review |
| 4 | No product entry point | owner must use Python CLI | `bossman 3d ...` and a Studio card | backend task kind + approval action + UI | touches shared CLI/backend | CLI contract tests, approval-gate test |
| 5 | TripoSR on CPU only | ~28 s/object | faster image→3D | PyTorch ROCm for gfx1151 on Windows, re-measure | driver/SAC issues | same two images, time + Chamfer vs CPU mesh |
| 6 | Better open image→3D models are CUDA-only today | TRELLIS.2, TripoSG | textured, sharper assets | watch AMD/ROCm ports; Hunyuan3D stays excluded (licence excludes EU) | licence/territory, big downloads | licence review first, then the same harness |
| 7 | rembg's default model (bria-rmbg-2.0) is non-commercial | background removal before TripoSR | licence-clean pipeline | switch to a permissive matting model (u2net/BiRefNet MIT) | quality differences | same inputs, mask IoU |
| 8 | Vision check is noisy | torch labelled "tree" | stronger automatic QA | multi-view renders, label sets per category; keep advisory | false confidence | agreement with owner ratings |
| 9 | In-game placement UX | structure only placeable by script | owner places generated objects while playing | hotbar entry + ghost preview | game design choices are the owner's | owner_controls-style Godot test |
