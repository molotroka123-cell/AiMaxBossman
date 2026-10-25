"""WanGP task list for Wan2.2-Animate-14B character replacement (replace / animate modes)."""
import argparse, json
W = "D:/Bossman/work/genjutsu-local-20261025"
REFS = {
    "A": (f"D:/Bossman/work/tiktok-trend-20261024/look-casual.png",
          "A young woman with a long high black ponytail, oversized white t-shirt, loose black cargo pants and chunky white sneakers dances energetically."),
    "B": (f"{W}/refs/man.png",
          "A young man with short curly dark brown hair, a plain dark green hoodie, light grey jogger pants and white sneakers dances energetically."),
}
MODES = {"replace": "PVBAIH#1", "animate": "PVBXAKI"}
NEG = ("blurry, ghosting, smeared limbs, extra limbs, extra fingers, deformed hands, fused fingers, distorted face, "
       "bad anatomy, duplicate person, flicker, low quality")
ap = argparse.ArgumentParser()
ap.add_argument("--jobs", default="B:replace,B:animate,A:replace,A:animate")
ap.add_argument("--guide", default=f"{W}/src/drive_s2.mp4")
ap.add_argument("--mask", default=f"{W}/src/drive_s2_mask.mp4")
ap.add_argument("--res", default="480x832")
ap.add_argument("--frames", type=int, default=65)
ap.add_argument("--window", type=int, default=81)
ap.add_argument("--overlap", type=int, default=5)
ap.add_argument("--fps", default="16")
ap.add_argument("--steps", type=int, default=6)
ap.add_argument("--seed", type=int, default=2510)
ap.add_argument("--out", required=True)
a = ap.parse_args()
tasks = []
for i, job in enumerate(a.jobs.split(",")):
    ref, mode = job.split(":")
    img, prompt = REFS[ref]
    tasks.append({
        "model_type": "animate_14B_local_lightx2v",
        "prompt": prompt, "negative_prompt": NEG,
        "resolution": a.res, "video_length": a.frames,
        "sliding_window_size": a.window, "sliding_window_overlap": a.overlap,
        "num_inference_steps": a.steps, "seed": a.seed,
        "video_prompt_type": MODES[mode],
        "video_guide": a.guide, "video_mask": a.mask, "image_refs": [img],
        "mask_expand": 20, "remove_background_images_ref": 0,
        "guidance_phases": 1, "guidance_scale": 1, "flow_shift": 5, "sample_solver": "euler",
        "force_fps": a.fps, "audio_prompt_type": "R",
    })
json.dump(tasks, open(a.out, "w"), indent=1)
print(a.out, len(tasks))
