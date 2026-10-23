"""Live proof on the owner's PC: a chat photo -> Gemma refines the prompt -> local uncensored Qwen edit (owner 23.10).

    python tools/prove_chat_photo_edit.py [--agent 41] [--gen-wait 3600] [--out docs/audits/2026-10-23-chat-attachments]

Synthetic picture only (white canvas, RED circle, a random code; no person). It is uploaded into the tool's own
thread «PROOF photo edit» and ONE owner-style message asks to turn the circle blue. Checks, in order:
  1. the chat turn completes and the answer does not claim it cannot see the photo;
  2. a NEW Direct Gen job exists: an edit model, ref_images_count >= 1, prompt mentions blue (Gemma's own prompt);
  3. (--gen-wait > 0) the job completes with verified bytes and the circle area is now blue-dominant.
Receipt JSON with ids, statuses, timings and the measured colours. Exit 0 only when every requested check passes.
"""
from __future__ import annotations

import argparse
import io
import json
import time
from pathlib import Path

from PIL import Image, ImageDraw

from prove_chat_attachments import _font, api, code

EDIT_MODELS = ("qwen-image-2.1-uc-edit", "qwen-image-edit-2509")
CANT_SEE = ("не вижу", "не могу увидеть", "не понимаю фот", "cannot see", "can't see")
CIRCLE = (60, 60, 360, 360)


def picture(c: str) -> bytes:
    img = Image.new("RGB", (768, 432), "white")
    d = ImageDraw.Draw(img)
    d.ellipse(CIRCLE, fill=(220, 20, 20))
    d.text((420, 180), c, fill="black", font=_font(56))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def circle_colour(data: bytes) -> tuple[int, int, int]:
    """Mean RGB of the central part of the circle, scaled to the output size."""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    sx, sy = img.width / 768, img.height / 432
    box = (int(150 * sx), int(150 * sy), int(270 * sx), int(270 * sy))
    crop = img.crop(box)
    px = [crop.getpixel((x, y)) for y in range(crop.height) for x in range(crop.width)]
    return tuple(sum(p[i] for p in px) // len(px) for i in range(3))


def raw(path: str) -> bytes:
    import urllib.request

    from prove_chat_attachments import BASE, _token
    req = urllib.request.Request(BASE + path, headers={"X-BCC-Token": _token()})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def jobs() -> list[dict]:
    j = api("GET", "/api/direct-gen/jobs")
    return j.get("items") or j.get("jobs") or []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--agent", type=int, default=41)
    ap.add_argument("--wait", type=int, default=900, help="seconds for the chat turn")
    ap.add_argument("--gen-wait", type=int, default=3600, help="seconds for the edit itself; 0 = only check the hand-off")
    ap.add_argument("--out", default="docs/audits/2026-10-23-chat-attachments")
    a = ap.parse_args(argv)
    c = code()
    before = {j["job_id"] for j in jobs()}
    tid = api("POST", "/api/chat/threads", {"title": "PROOF photo edit"})["id"]
    up = api("POST", f"/api/chat/attachments?filename=reference.png&thread_id={tid}", raw=picture(c),
             ctype="application/octet-stream")
    t0 = time.time()
    sent = api("POST", f"/api/chat/threads/{tid}/send", {
        "text": "Это фото — референс. Переделай его: круг должен стать синим, белый фон и надпись оставь как есть. "
                "Доработай промт сам и отправь на правку.", "agent_id": a.agent, "attachments": [up["id"]]}, timeout=900)
    task_id = sent.get("task_id") or (sent.get("task") or {}).get("id") or (sent.get("turn") or {}).get("task_id")
    answer, status = "", "?"
    while time.time() - t0 < a.wait:
        turn = next((x for x in api("GET", f"/api/chat/threads/{tid}").get("turns") or []
                     if int(x.get("task_id") or 0) == int(task_id or 0)), {})
        status = turn.get("status")
        if status in ("completed", "failed", "stopped", "cancelled"):
            answer = str(turn.get("result") or turn.get("error") or "")
            break
        time.sleep(5)
    turn_s = round(time.time() - t0, 1)
    new = [j for j in jobs() if j["job_id"] not in before]
    job = new[0] if new else {}
    prompt = str(job.get("raw_prompt") or "")
    checks = {
        "turn_completed": status == "completed",
        "answer_sees_photo": bool(answer) and not any(s in answer.lower() for s in CANT_SEE),
        "edit_job_created": bool(job) and job.get("model") in EDIT_MODELS,
        "reference_attached": int((job.get("params") or {}).get("ref_images_count") or 0) >= 1,
        "prompt_refined_blue": "blue" in prompt.lower() or "син" in prompt.lower(),
    }
    gen = {"waited": a.gen_wait > 0}
    if a.gen_wait > 0 and job:
        g0 = time.time()
        while time.time() - g0 < a.gen_wait:
            job = api("GET", f"/api/direct-gen/jobs/{job['job_id']}")
            if job.get("status") in ("completed", "failed", "cancelled", "stopped"):
                break
            time.sleep(15)
        gen.update(status=job.get("status"), seconds=round(time.time() - g0, 1),
                   verified=bool((job.get("result") or {}).get("verified")))
        checks["edit_completed_verified"] = job.get("status") == "completed" and gen["verified"]
        if checks["edit_completed_verified"]:
            rgb = circle_colour(raw(f"/api/direct-gen/jobs/{job['job_id']}/file"))
            gen["circle_rgb_after"] = rgb
            checks["circle_now_blue"] = rgb[2] > rgb[0] + 30
    ok = all(checks.values())
    receipt = {"verdict": "PASS" if ok else "FAIL", "agent_id": a.agent, "thread_id": tid, "task_id": task_id,
               "turn_status": status, "turn_seconds": turn_s, "answer_chars": len(answer),
               "attachment_id": up["id"], "attachment_kind": up.get("kind"), "job_id": job.get("job_id"),
               "job_model": job.get("model"), "prompt_chars": len(prompt), "prompt": prompt[:1500],
               "circle_rgb_before": [220, 20, 20], "generation": gen, "checks": checks,
               "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"photo-edit-{time.strftime('%Y%m%d-%H%M')}.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    for k, v in checks.items():
        print("PASS" if v else "FAIL", k)
    print("VERDICT", receipt["verdict"], "| task", task_id, status, "| job", job.get("job_id"), gen.get("status"))
    if not ok:
        print("answer:", answer[:800])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
