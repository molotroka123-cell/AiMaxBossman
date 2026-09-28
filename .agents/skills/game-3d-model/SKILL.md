---
name: game-3d-model
description: Make a game-ready 3D model (5k-10k triangles, coloured GLB) from a text prompt with LOCAL models only - local LLM writes a part spec, a deterministic builder and validator make the mesh, the local vision model critiques the preview and the LLM revises it. Optional neural route (local image -> TripoSR). Can install the model into the BossBlocks game after approval.
version: "1.0"
required_tools:
  - terminal.run
permissions:
  - filesystem.read
  - filesystem.write
input_schema:
  type: object
  properties:
    prompt: {type: string}
    out_dir: {type: string}
    route: {type: string, enum: [procedural, neural]}
    height_m: {type: number}
    install_into_game: {type: boolean}
    game_dir: {type: string}
  required: [prompt, out_dir]
output_schema:
  type: object
  properties:
    glb: {type: string}
    preview_png: {type: string}
    triangles: {type: integer}
    validator_ok: {type: boolean}
    critique: {type: object}
    installed: {type: boolean}
metadata:
  owner: bossman
  version: "1.0"
---

# Game 3D model (local)

Everything runs on this PC: Ollama `bossman-fast-qwen36-35b-a3b-q5` writes the spec,
`bossman-fast-qwen36-vision` reviews the picture. No cloud model, no paid 3D service.

## Steps

1. Pick the route. `procedural` (default) is best for props with clear parts: furniture, buildings,
   vehicles, lamps, trees, creatures made of simple shapes. `neural` (text -> Z-Image-Turbo image ->
   TripoSR) suits organic single objects (a mushroom, a rock, a fruit); it needs the shared GPU and
   waits while another `sd-cli.exe` runs.
2. Run from the Bossman repo root (one command, no shell pipes):
   - procedural: `python tools/voxel3d/hp_generate.py "<prompt>" --out "<out_dir>" --rounds 2`
   - neural: `python tools/voxel3d/hp_generate.py "<prompt>" --out "<out_dir>" --neural --height <height_m>`
3. Read the last output line (JSON): `ok`, `triangles`, `critique`, `glb`. Then read
   `<out_dir>/<name>/report.json` for every version's validator result and critique.
4. Report honestly: triangles, validator PASS/FAIL, what the vision critic said it looks like, and the
   preview path. A model that fails the validator is not delivered as done.
5. Only if `install_into_game` is true: first run the same command with `--game "<game_dir>"` (dry run,
   prints the files it WOULD write), show that plan, then run it again with `--apply`. Writing into the
   game is a file change and goes through the normal approval.

## Limits (say them, do not hide them)

- Shapes are built from simple parts (ellipsoid, box, lathe, tube, torus, prism); no textures,
  colours are per vertex. Thin cloth, text and faces are weak.
- The vision critic is a signal, not proof: it can praise a wrong model or miss a flaw.
- The validator checks the triangle budget, closed shells, pivot/scale and colours; it does not judge beauty.
