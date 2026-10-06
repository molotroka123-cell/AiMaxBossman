---
name: bossman-motion-concert
description: Turn one owner track into a full-length local concert video with one command — singer shots lip-driven by the track (Wan2.2 S2V-14B in ComfyUI ROCm) plus band/shamisen/crowd/LED inserts (Wan2.2-TI2V-5B via sd.cpp Vulkan), cut on the beat, original audio copied bit-exact, QC with visible gaps. Resumable, STOP-able, one GPU job at a time.
compatibility: BOSSMAN (Windows owner machine, Radeon 8060S, ComfyUI ROCm venv, sd-cli Vulkan, ffmpeg in PATH)
metadata:
  owner: bossman
  version: "1.0"
  learned_from: owner task 2026-10-06 (Faint shamisen cover, 168.83 s, 45-shot plan)
---

# Motion concert (local, one command)

Code: `tools/motion_concert/concert.py` (stdlib only), owner copy in
`C:\Users\asd\Bossman\Bossman_Motion_Concert_Faint\pipeline\concert.py`. Russian guide: `tools/motion_concert/README.md`.

## Chat command -> what to run

Owner says "сделай концерт" / "запусти концертный пайплайн" / "/concert":

1. Check that the GPU is free. Nothing else may be generating: no other `sd-cli.exe`, ComfyUI, or a large ollama or llama-server model.
   If something is running, ask the owner first. Never kill a job you did not start.
2. Look before spending GPU (seconds, no GPU):
   ```
   C:\Users\asd\Bossman\media-runtime\comfyui-venv\Scripts\python.exe pipeline\concert.py plan
   C:\Users\asd\Bossman\media-runtime\comfyui-venv\Scripts\python.exe pipeline\concert.py run --dry-run --limit 3
   ```
   (cwd `C:\Users\asd\Bossman\Bossman_Motion_Concert_Faint`). Report the plan summary: shot counts by kind,
   covered seconds = track duration, s2v_fraction ~0.70.
3. Full run, same cwd. It is resumable, so re-running it is always safe:
   ```
   C:\Users\asd\Bossman\media-runtime\comfyui-venv\Scripts\python.exe pipeline\concert.py all
   ```
   For a short trial run use `run --limit 2` first, then `assemble` and `qc`.
4. Stop on request: create `pipeline\STOP`. The script checks it between shots and exits with code 3. To resume, delete
   STOP and run the same command; finished shots are skipped.
5. Send the owner `pipeline\out\faint_concert_854x480.mp4` and the `qc.json` verdict and gaps through the пульт bot.

## Honesty rules (must)

- Report the `qc.json` verdict exactly as written: `PASS`, `FAIL_GAPS` or `FAIL`. Do not call the video "готово" if there are gaps.
  List each gap with its id, time range and reason.
- QC proves timing and audio integrity only: duration within 1 frame, 4052 frames, audio packets md5 equal to the
  source, and a full decode with no errors. QC does NOT prove lip-sync, correct hands or instruments, the same face across
  shots, or the absence of colour noise. Say so, and give the owner the video to judge.
- Never re-encode, trim or move the original mp3. The final mux is `-c:a copy` with no `-shortest`.
- Never fill a failed shot by looping or stretching a neighbouring clip. The dark-red gap slate is intentional.
- State the real wall time from `manifest.json` (`runs[].wall_s`). Do not estimate the time of a full run as if it were measured.
- Vocal on/off is not detected. Singer shots are placed by section energy, so do not claim the singer sings exactly on the vocals.

## Known traps

- Run one heavy job at a time. S2V runs first in one ComfyUI session, then ComfyUI is stopped, then sd-cli inserts run.
  Two generators on the single 8060S cause "device lost" or OOM.
- sd-cli: pass the prompt with `--prompt-file`, because Start-Process splits spaces. Do not use `--diffusion-fa` or
  `--vae-tiling`, which produced colour noise. The output may get an extra `.avi` suffix, and the script accepts both names.
- `analysis.json` stores only the beat count. Cuts snap to a grid built from `tempo_bpm` and `first_beat_s`.
- Do not depend on sklearn or ml_dtypes on this PC, because Smart App Control blocks their DLLs.
