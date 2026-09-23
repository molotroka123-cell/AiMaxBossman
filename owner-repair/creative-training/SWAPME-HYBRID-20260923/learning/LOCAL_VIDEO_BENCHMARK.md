# LOCAL_VIDEO_BENCHMARK — Wan2.2 TI2V-5B on Radeon 8060S (2026-09-23)

MODEL: Wan2.2 TI2V-5B GGUF (QuantStack/Wan2.2-TI2V-5B-GGUF@57437632, see models/media/MANIFEST.json)
RUNTIME: stable-diffusion.cpp Vulkan (`media-runtime/sdcpp/vulkan/sd-cli.exe`) via Bossman Studio provider `sdcpp`, test Bossman :8810 (build 6a2b5907)
Smart App Control: On (unchanged). Other candidates: Wan A14B / LTX-2.3 / HunyuanVideo-1.5 — not installed / no signed Windows-AMD runtime → NOT_RUN (no new downloads needed for this run).

Same source frame (`bench/bench_source_640x1152.png`, sha256 9bf281adf97ca093ef4f34bd98d286ebb7b54006ebf40dfe003c5906b029f78d), same prompt, seed 2309, 640x1152, 17 frames @24 fps, cfg 5.0:

| Variant | Steps | Render time | Output sha256 | Verdict |
|---|---|---|---|---|
| bench_steps16 | 16 | 459 s | b9c7ed9474a84f67… | identity OK; motion drifts from the prompt (arm wave), paw artifacts |
| bench_steps24 | 24 | 510 s | 68dcbde9d2086dbf… | identity OK; motion follows the prompt (reaches for the glasses), fewer artifacts — SELECTED |

Finding: at this size the fixed overhead (model load, text encoder, VAE decode) is ~357 s; one step costs ~6.4 s at 14 400 latent tokens.
+50 % steps = +11 % wall time → quality steps are cheap; frames/resolution are what is expensive.
Selected production preset: 640x1152, 65 frames @24 fps (2.7 s), 24 steps, cfg 5.0, seed 2309, I2V from the adjacent cloud shot's last frame.
Width < 640 is refused by the Studio catalog (portrait 9:16 therefore starts at 640x1152).
