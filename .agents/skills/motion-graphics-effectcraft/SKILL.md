---
name: motion-graphics-effectcraft
description: Render titles, promo intros and motion-graphics layers locally with EffectCraft (open-source After Effects-style compositor in Rust) from the command line or its MCP server, then hand the clip to Motion Studio / the pult.
compatibility: BOSSMAN (Windows x64, CPU render, no FFmpeg needed)
metadata:
  owner: bossman
  version: "0.1"
  learned_from: owner request 2026-10-14 ("если поможет анимации тоже кинь в мейн"); local probe on the owner PC
---

# Motion graphics with EffectCraft (local, free)

Status: **tool installed and probed, not wired into the product.** No Bossman route calls it yet; use it from the
CLI or as an MCP server. Wiring it into Motion Studio is a separate, reviewed change.

## What it is

- Upstream: https://github.com/storytold/effectcraft — license MIT OR Apache-2.0 (README badge; GitHub API reports
  Apache-2.0). Young project (repo created 2026-10-01, releases daily) — pin a version, never auto-update.
- Pinned build: v0.9.2 `effectcraft-0.9.2-windows-x64-portable.zip`, SHA256
  `1f6fbb3a348f1b12b15faa3bc2c78a079414f07454cc1402208793486e79bee4`.
- Installed (outside git): `C:\Users\asd\Bossman\apps-local\effectcraft-0.9.2\effectcraft-0.9.2-windows-x64-portable\`
  (`effectcraft.exe` GUI, `effectcraft-cli.exe` headless). Binaries never go into the repository.
- Projects are versioned JSON (`.ecproj`); compositions, layers, keyframes, ~300 effects, expressions, 3D camera and
  lights, render queue. Encoders are pure Rust (H.264/HEVC/AV1/ProRes/WebM/PNG/EXR/GIF/WAV) — no FFmpeg inside.

## Measured on the owner PC (2026-10-14, v0.9.2)

- `effectcraft-cli render --out demo.mp4 --end 3` (built-in demo comp): 1920x1080, 90 frames, H.264 15 Mbps in 3.98 s.
- `effectcraft-cli render-frame --time 1 --out f1.png`: one 1080p frame in 99.7 ms.
- Not measured yet: long comps, 3D/GPU path (`bench --gpu`), alpha output for compositing over AI video.

## Recipes

```
EC=C:\Users\asd\Bossman\apps-local\effectcraft-0.9.2\effectcraft-0.9.2-windows-x64-portable\effectcraft-cli.exe
%EC% info main.ecproj --json                          # comps, layers, engine summary
%EC% set Main "#1" transform/position "[100,360]" --time 0 main.ecproj --save
%EC% render --comp Main --out title.mp4 main.ecproj  # headless render
%EC% render --comp Main --out title.mov --prores 4444 --channels rgba main.ecproj   # alpha for overlays
%EC% render --template lower-third.ectemplate --values v.json --out lt.mp4         # one template instance
%EC% script build.jsx main.ecproj                     # After Effects-style JS object model
```

MCP for agents (Qwen operator, Argon, Claude): `effectcraft-cli mcp --automation-read-root <in> --automation-write-root
<out>` — always confine file access to the job's own folders; never point the roots at the user profile, keys or the
repository.

## Rules

- Text/titles/promos only from the owner's brief. Personal photos or faces go in only under the same consent and
  never-leaves-the-PC rules as the face-swap skill.
- Captions in the pult state provenance: "титры/анимация: EffectCraft локально".
- Commercial use: MIT/Apache permits it; fonts and footage inside a project keep their own licenses — check them.
