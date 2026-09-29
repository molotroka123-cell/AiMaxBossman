# Motion Studio: Epic trailer recipe

Owner request, 2026-09-29: recreate the supplied `bossman-32-days.mp4` style,
make it more epic, exactly 22 seconds with music, and make it reusable by Bossman.

## Implemented candidate

`make_video.py --style epic` extends the existing Motion Studio pipeline. It uses
the same validated scene schema, existing original score generator, optional
Kokoro voice-over, and a new deterministic Pillow renderer. No new backend,
memory database, model weights, subscription or external media service.

Output is 1280x720, 30 fps, H.264/AAC. Animated depth particles, neon cards,
3D particle sphere, typed hierarchy, scene subtitles, transitions and original
music share one scene clock. Text is fitted to available space. The `grid` layout
also handles small counts, rather than leaving nearly all of its canvas empty.
Classic rendering stays the default. Epic supports title, cards, bars, grid,
voice, roadmap, logo, end_card. Lottie/sticker scenes explicitly require classic.

Dependencies: Pillow, numpy, scipy, ffmpeg; a readable TTF font. Linux DejaVu and
Windows Arial are detected, or set `BOSSMAN_MOTION_FONT`. No browser is required
for Epic. Optional voice retains the existing Kokoro/soundfile requirements.

## Reproduce the owner trailer

From the repository root:

```sh
python tools/motion_studio/make_video.py tools/motion_studio/examples/bossman_epic_22s.json --style epic --no-voice --work work/epic
```

This produces `work/epic/video.mp4` and `soundtrack.wav`. `--no-voice` means
music plus burned-in subtitles; it does NOT mute the soundtrack. For local
voice-over, replace it with the existing `--tts-models <Kokoro directory>`.
Preview before rendering with `--preview 1.5 8.8 11.5 14 17 20`.

## Teach the local scenario writer

Reuse the existing few-shot mechanism, rather than claiming trained weights:

```sh
python tools/motion_studio/generate_spec.py "22-second epic neon product trailer. Use supported Epic scenes only. Include short subtitle lines as vo; mark future work as targets." --model LOCAL_MODEL --endpoint http://127.0.0.1:11434/v1 --facts facts.json --example tools/motion_studio/examples/bossman_epic_22s.json --out work/new-trailer.json
python tools/motion_studio/make_video.py work/new-trailer.json --style epic --no-voice --work work/new-trailer
```

The caller supplies the actual selected local model and current verified facts.
Do not copy the example's CI numbers into other trailers. Its 26/27 count is a
2026-09-28 snapshot at `84f5e0acce6a32c348330af39ff4a5874ade98a1`, including
multiple workflow triggers, not 27 unique tests. Learning evidence remained open.
The roadmap is direction, not a version release certificate. The reference
video's commit/test totals were not re-certified for this render.

After owner visual approval, the example can be added to the existing approved
dataset. Until then it is a TEACHER_PATCH / candidate example, not student
learning, an approved training sample, or proof of measurable transfer.
For transfer evaluation use unseen briefs, first-attempt validity, no fabricated
facts, full video decode, readable frames, exact duration, audio presence and
owner quality scoring. Compare baseline vs example-guided local generation.
No local model or owner Windows session was available for this candidate run.

## Integration boundary for Claude

Validation in the cloud candidate: `python -m pytest tests/test_motion_studio.py
tests/test_motion_epic.py -q` — 16 passed. The full example was rendered through
`make_video.py --style epic --no-voice`; ffprobe reports exactly 22.000 seconds,
1280x720, 30 fps, H.264 video and AAC audio. Full ffmpeg decode returned exit 0.
Scene preview frames were visually reviewed. Owner Windows and real local-model
transfer remain NOT_RUN; this is an engineering-delivered example.

This candidate is wired into Motion Studio's existing `make_video.py` entrypoint.
The reviewed Motion branch still lists Command Center job wiring as future work.
Do not claim an installed UX button or Bossman CMD end-to-end acceptance.
Use the existing Studio job runner, project artifact storage and STOP/approval
path when exposing the preset in the app; never launch a second service or
silently execute model-written code. Show `Epic` as a style and retain validated
scene JSON. Package the new module, Pillow and the example with the same product.

North Star: verified continuous self-improvement remains the goal; this patch
does not advance its ladder or claim TRANSFER_MEASURED_GAIN. CMD, UX and Telegram
must remain control surfaces for one configured Bossman backend.
