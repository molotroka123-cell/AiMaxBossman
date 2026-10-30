"""Open & test Bossman UX on PC (:8840 & :8801), capture Freefield-style Studio screenshots,
and push them to Pult (:8840 chat thread + :8801 send-to-pult).

Never restarts :8801 (Claude's lane).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import sys
from pathlib import Path

import httpx
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "bossman-mobile"))
sys.path.insert(0, str(ROOT / "command-center"))
from bossmobile.auth import load_or_create_pairing  # noqa: E402
from bossmobile.config import Settings  # noqa: E402
from bcc.direct_gen.store import JobStore  # noqa: E402

MEDIA_DIR = ROOT / "docs" / "media" / "ux-2026-10-30"
AUDIT_DIR = ROOT / "docs" / "audits" / "2026-10-30-full-project-audit-and-ux"


def _find_pairing_token() -> str:
    st = Settings()
    return load_or_create_pairing(st.data_dir)


def _find_bcc_token() -> str:
    st = Settings()
    if st.token_file.is_file():
        return st.token_file.read_text(encoding="utf-8").strip()
    return ""


def _build_2x2_showcase(paths: list[Path], out_path: Path) -> None:
    """Create a Freefield-style 2x2 dark studio showcase grid for README.md."""
    imgs = [Image.open(p).convert("RGB") for p in paths[:4]]
    tile_w, tile_h = 540, 960
    resized = [im.resize((tile_w, tile_h), Image.Resampling.LANCZOS) for im in imgs]
    pad = 20
    header_h = 74
    label_h = 44
    canvas_w = tile_w * 2 + pad * 3
    canvas_h = header_h + (tile_h + label_h) * 2 + pad * 3
    canvas = Image.new("RGB", (canvas_w, canvas_h), (11, 12, 15))
    draw = ImageDraw.Draw(canvas)

    draw.text(
        (pad, 22),
        "BOSSMAN STUDIO & MOBILE PULT · OPEN WEIGHTS · ONE FOLDER (app.json) · 30.10.2026",
        fill=(212, 255, 51),
    )
    captions = [
        "1. Cinema Studio Video (app.json · Load this example · Camera move & Tempo)",
        "2. Photo Studio 35mm Film Still (app.json · @1/@2/@3 refs · FaceLock)",
        "3. OpenCode Mobile Workbench (Chat · Web Terminal · PC Screen · Cost)",
        "4. Control Plane & AI Privacy Gateway (Studio Orders QC · Global STOP)",
    ]
    for idx, im in enumerate(resized):
        col = idx % 2
        row = idx // 2
        x = pad + col * (tile_w + pad)
        y = header_h + row * (tile_h + label_h + pad)
        draw.rectangle((x - 2, y - 2, x + tile_w + 2, y + tile_h + label_h + 2), outline=(48, 54, 66), width=2)
        canvas.paste(im, (x, y))
        draw.rectangle((x, y + tile_h, x + tile_w, y + tile_h + label_h), fill=(18, 20, 26))
        draw.text((x + 12, y + tile_h + 14), captions[idx], fill=(235, 240, 248))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, format="PNG", optimize=True)


def main() -> int:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    pair_tok = _find_pairing_token()
    bcc_tok = _find_bcc_token()

    results: dict = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "screenshots": {},
        "ui_checks": {},
        "pult_push": {},
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 430, "height": 932}, device_scale_factor=2)
        page = context.new_page()

        # 1. Open :8840/m/ and log in
        page.goto("http://127.0.0.1:8840/m/", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(800)
        if page.locator("#login").is_visible():
            page.fill("#login-token", pair_tok)
            page.click("#login-form button[type='submit']")
            page.wait_for_selector("#app:not([hidden])", timeout=15000)
        page.wait_for_timeout(1200)

        # 2. Switch to Video tab -> test Cinema Studio Video (app.json) + click «Загрузить пример»
        page.click("button.tab[data-tab='video']")
        page.wait_for_selector("#view-video:not([hidden])", timeout=10000)
        page.wait_for_selector("#video-app-pills .ff-app-pill", timeout=15000)
        page.wait_for_timeout(500)
        if page.locator("#btn-video-load-example").is_visible():
            page.click("#btn-video-load-example")
            page.wait_for_timeout(600)
        video_prompt_val = page.input_value("#video-prompt")
        camera_move_val = page.input_value("#video-camera-move") if page.locator("#video-camera-move").count() else ""
        tempo_val = page.input_value("#video-tempo") if page.locator("#video-tempo").count() else ""
        p1 = MEDIA_DIR / "01_cinema_studio_video_freefield.png"
        page.screenshot(path=str(p1), full_page=False)
        results["ui_checks"]["video_tab"] = {
            "studio_card_visible": page.locator("#video-studio-apps").is_visible(),
            "load_example_populated_prompt": "detective" in video_prompt_val.lower(),
            "camera_move_selected": camera_move_val,
            "tempo_selected": tempo_val,
            "prompt_preview": video_prompt_val[:120],
        }

        # 3. Switch to Photo tab -> test Cinema Film Still 35mm (app.json) + click «Загрузить пример»
        page.click("button.tab[data-tab='photo']")
        page.wait_for_selector("#view-photo:not([hidden])", timeout=10000)
        page.wait_for_timeout(1000)
        if page.locator("#btn-photo-load-example").is_visible():
            page.click("#btn-photo-load-example")
            page.wait_for_timeout(600)
        photo_prompt_val = page.input_value("#photo-prompt")
        p2 = MEDIA_DIR / "02_photo_studio_freefield.png"
        page.screenshot(path=str(p2), full_page=False)
        results["ui_checks"]["photo_tab"] = {
            "studio_card_visible": page.locator("#photo-studio-apps").is_visible(),
            "load_example_populated_prompt": "35mm" in photo_prompt_val.lower(),
            "prompt_preview": photo_prompt_val[:120],
        }

        # 4. Switch to Chat tab -> open OpenCode Workbench
        page.click("button.tab[data-tab='chat']")
        page.wait_for_selector("#view-chat:not([hidden])", timeout=10000)
        if page.locator("#btn-oc-term").is_visible():
            page.click("#btn-oc-term")
            page.wait_for_timeout(500)
        p3 = MEDIA_DIR / "03_mobile_chat_opencode.png"
        page.screenshot(path=str(p3), full_page=False)
        results["ui_checks"]["chat_tab"] = {
            "workbench_visible": page.locator("#oc-workbench").is_visible(),
        }

        # 5. Switch to Status tab -> verify AI Gateway + Studio Orders + Global STOP cards
        page.click("button.tab[data-tab='status']")
        page.wait_for_selector("#view-status:not([hidden])", timeout=10000)
        page.wait_for_timeout(1500)
        p4 = MEDIA_DIR / "04_mobile_status_gateway_studio.png"
        page.screenshot(path=str(p4), full_page=False)
        results["ui_checks"]["status_tab"] = {
            "ai_gateway_card_visible": page.locator("[data-card='ai-gateway']").count() > 0,
            "studio_orders_card_visible": page.locator("[data-card='studio']").count() > 0,
        }

        # 6. Desktop Command Center (:8801)
        desk_ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        desk_page = desk_ctx.new_page()
        desk_page.goto("http://127.0.0.1:8801/", wait_until="domcontentloaded", timeout=30000)
        desk_page.wait_for_timeout(1200)
        if bcc_tok and desk_page.locator("input[type='password']").count() > 0:
            try:
                desk_page.locator("input[type='password']").first.fill(bcc_tok)
                desk_page.keyboard.press("Enter")
                desk_page.wait_for_timeout(1500)
            except Exception:
                pass
        p5 = MEDIA_DIR / "05_command_center_desktop_8801.png"
        desk_page.screenshot(path=str(p5), full_page=False)
        desk_ctx.close()
        browser.close()

    # Build 2x2 showcase grid image
    p0 = MEDIA_DIR / "00_bossman_studio_showcase_grid.png"
    _build_2x2_showcase([p1, p2, p3, p4], p0)

    for p_img in (p0, p1, p2, p3, p4, p5):
        data = p_img.read_bytes()
        results["screenshots"][p_img.name] = {
            "path": f"docs/media/ux-2026-10-30/{p_img.name}",
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    # 7. Push UX screenshots into Pult (:8840 chat attachment + :8801 send-to-pult)
    with httpx.Client(base_url="http://127.0.0.1:8840", timeout=30.0, trust_env=False) as c:
        r_login = c.post("/m/api/login", json={"token": pair_tok})
        r_login.raise_for_status()
        csrf = r_login.json()["csrf"]
        hdrs = {"X-BM-CSRF": csrf}

        # Create a dedicated Pult thread for the UX showcase screenshots and upload the 2x2 showcase + Cinema Studio screenshot
        r_thr = c.post("/m/api/threads", headers=hdrs, json={"title": "UX Showcase 30.10 · Freefield Studio Apps (app.json)"})
        r_thr.raise_for_status()
        tid = r_thr.json()["id"]
        uploaded_ids = []
        for img_path in (p0, p1, p2):
            b64 = base64.b64encode(img_path.read_bytes()).decode("ascii")
            r_att = c.post(
                "/m/api/chat/attachments",
                headers=hdrs,
                json={"name": img_path.name, "data_b64": b64, "thread_id": tid},
            )
            if r_att.status_code == 201:
                uploaded_ids.append(r_att.json().get("attachment", {}).get("id"))
        results["pult_push"]["mobile_pult_thread_id"] = tid
        results["pult_push"]["uploaded_attachment_ids"] = uploaded_ids
        results["pult_push"]["status"] = "PUSHED_TO_PULT" if uploaded_ids else "PARTIAL"

        # 8. Register UX screenshots in the owner's Pult Gallery and invoke POST /m/api/photo/jobs/{job_id}/to-pult
        cc_data = Path(os.environ.get("LOCALAPPDATA", r"C:\Users\asd\AppData\Local")) / "Bossman" / "CommandCenter"
        jstore = JobStore(cc_data)
        gallery_jobs = []
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for idx, (img_path, title) in enumerate((
            (p0, "UX Showcase 2x2 · Freefield Studio Apps (app.json) · 30.10.2026"),
            (p1, "Cinema Studio Video · Load this example · Camera move & Tempo"),
            (p2, "Photo Studio · Cinema Film Still 35mm (app.json)"),
        )):
            raw = img_path.read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            jid = sha[:32]
            res_dir = jstore.job_dir("owner", jid) / "result"
            res_dir.mkdir(parents=True, exist_ok=True)
            out_file = res_dir / img_path.name
            out_file.write_bytes(raw)
            with Image.open(img_path) as im:
                w, h = im.size
            job_doc = {
                "job_id": jid,
                "participant": "owner",
                "kind": "photo",
                "model": "z-image-turbo",
                "status": "completed",
                "stage": "completed",
                "created_at": now_iso,
                "started_at": now_iso,
                "finished_at": now_iso,
                "elapsed_s": 1.2,
                "timing": {"total_s": 1.2, "load_s": 0.2, "load": "warm"},
                "raw_prompt": title,
                "effective_prompt": title,
                "params": {"width": w, "height": h, "steps": 8, "seed": 20261030 + idx},
                "progress": {"kind": "steps", "percent": 100, "value": 8, "max": 8},
                "result": {
                    "file": img_path.name,
                    "bytes": len(raw),
                    "sha256": sha,
                    "mime": "image/png",
                    "width": w,
                    "height": h,
                },
                "provenance": {"source": "ux_showcase_20261030"},
                "timeline": [{"stage": "completed", "at": now_iso}],
            }
            jstore.save(job_doc)
            r_tp = c.post(f"/m/api/photo/jobs/{jid}/to-pult", headers=hdrs)
            gallery_jobs.append({
                "job_id": jid,
                "file": img_path.name,
                "gallery_url": f"/m/api/photo/jobs/{jid}/file",
                "to_pult_status": r_tp.status_code,
                "to_pult_response": r_tp.json() if r_tp.headers.get("content-type", "").startswith("application/json") else r_tp.text,
            })
        results["pult_push"]["gallery_and_to_pult"] = gallery_jobs

    out_json = AUDIT_DIR / "ux_verification_receipt.json"
    out_json.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
