---
name: print3d-orca
description: STL from the owner's pult -> ready gcode for the Elegoo Neptune 4 Pro (PLA 218 C) with checked supports, previews and delivery to the pult. Use for any "сделай gcode / напечатай / подготовь к печати" request.
---

# 3D print: STL -> gcode (Neptune 4 Pro, PLA)

Proven on the owner's PC 14.10 on 7 models (bananza x3, spoon x2, chair x2, Liberty x2). Everything is local and free:
the app's own Orca adapter (`apps/ai-3d-maker`, `ai_3d_maker.orca`) + OrcaSlicer CLI with a private `--datadir`.

## Fixed owner settings
- Printer `Elegoo Neptune 4 Pro 0.4 nozzle`, bed 230x230, max height 265 mm (`printable_height`).
- Filament `Elegoo PLA @EN4 Series`, **nozzle 218 C and first layer 218 C** (owner 14.10), bed 60 C.
- Supports always on: tree(auto), organic, brim auto.
- "Мягкие / легко отдирать" = `gentle 1`: threshold 40°, top/bottom z-distance 0.24, 2 sparse interface layers (spacing 0.4), xy 0.5.
- Quality: fine detail -> `tools/print3d/presets/figurine_process_012.json` (0.12), big detail -> `figurine_process_016.json` (0.16),
  "стандартная" -> `0.20mm Standard @Elegoo N4Pro 0.4 nozzle`.

## Steps
1. **Get the file.** The pult does NOT download documents («Такие вложения … не поддерживаются»). Open Telegram Desktop, the pult chat,
   click the file's download arrow -> `%USERPROFILE%\Downloads\Telegram Desktop\<name>.stl`; wait until the size stops changing.
2. **Measure.** Many STLs come in metres (~1 unit tall) — always rescale; the tool scales the LONGEST side to L mm.
3. **Orientation.** Render 4 views (`yaw0 = -Y`) and look where the face/relief points.
   - "лицом вверх" (figure lying on its back / spoon relief up): `rot=faceup` (maps -Y to +Z).
   - statues on a base, chairs: `rot=none` (keep upright).
4. **Size.** "максимум плиты": longest side 215 mm for wide objects, height 260 mm for tall ones (keep 5 mm margin);
   "на 60% меньше" = 40% of the size; "в 2 раза меньше" = half; real spoons: teaspoon 140 mm, tablespoon 190 mm.
5. **Slice:** `python tools/print3d/slice_any.py <stl> <L> <none|faceup> <process> <tag> <0|1>` -> gcode + 3mf in `Desktop\<Name>_Neptune4Pro`.
6. **Verify before sending (mandatory):** in every gcode `;TYPE:Support` count > 0, `enable_support = 1`,
   `nozzle_temperature = 218`, `nozzle_temperature_initial_layer = 218`, X/Y inside 0..230, max Z <= 265, end-of-print present.
7. **Preview:** `python tools/print3d/gcode_view.py <gcode> <png>` — rendered from the real gcode (orange model, green supports).
   Orca GUI screenshots are optional: the Bambu-plugin dialog and slow 1.5M-triangle loads make them flaky, and re-slicing a 3mf
   in the GUI may drop the CLI support overrides — the gcode render is the truthful picture.
8. **Deliver to the pult:** previews first, one photo each with a caption, then the gcode files. Telegram bots send at most 50 MB:
   zip bigger gcode (117 MB -> 37 MB). Report per file: size, layer height, time, grams.

## Measured 14.10
bananza 5/10/15 cm: 27 min / 1 h 37 / 2 h 57; spoon 14/19 cm: 1 h 13 / 2 h 24; chair 215 mm: 28 h 12, 592 g; chair 86 mm: 4 h 11;
Liberty 260 mm (0.20): 13 h 18, 329 g; 130 mm (0.12): 7 h 34.

## Not done yet
The USB stick in the owner's PC was not detected by Windows on 14.10 (no mass-storage device) — copy gcode to `<drive>\models`
only once a drive letter appears. There is no pult command for this yet; the owner asks in chat and the operator runs the steps.

## Owner rules added 22.10 (Svoboda redo)
- Temperatures are parameters: `BOSSMAN_PRINT_NOZZLE=220 BOSSMAN_PRINT_BED=60 python tools/print3d/slice_any.py ...`. Name carries them
  (`..._PLA220_bed60_...`). When the owner says "220 иглы и 60 пола" always set BOTH; without `BOSSMAN_PRINT_BED` the Cool Plate profile heats
  the bed to 35 C (the 260 mm Svoboda came out with `M190 S35` — wrong). Verify `M190 S60` and `M109 S220` in the gcode before sending.
- "Подставка / не разъезжалось": `BOSSMAN_PRINT_RAFT=3` plus brim 8 mm. Do not use tall thin tree supports under a lying figure.
- Orientation: pick by looking at the 4 renders. A statue on a round plinth stays UPRIGHT (`rot=none`, plinth flat on the bed);
  lying on its back puts the plinth on edge and needs tall support columns (Svoboda horizontal 130 mm: 83 g, 4014 support passes — worse).
  The owner delegates the choice: "как лучше, лишь бы норм".
- Always report print TIME and grams per file (owner: «пиши скок делаться будет»), from the slicer summary, plus a preview rendered by
  `gcode_view.py` using a Python that has matplotlib (`apps-local/figurine/venv`).
