"""Deliver to the owner's Telegram in ONE message: all shots + the full master (sendMediaGroup). Token never printed."""
import json, sys
from pathlib import Path
import httpx

RUN = Path(r"C:\Users\asd\Bossman\creative-runs\SWAPME-HYBRID-20260923")
ENV = Path(r"C:\Users\asd\AppData\Local\Bossman\telegram-companion\companion.env")
TOKEN = dict(l.split("=", 1) for l in ENV.read_text(encoding="utf-8").splitlines() if "=" in l)["TG_COMPANION_BOT_TOKEN"].strip()
caption = Path(sys.argv[1]).read_text(encoding="utf-8")
items = [("master", RUN / "final" / "swapme_fox_15s_master.mp4"), ("S1", RUN / "clips" / "S1.mp4"), ("L1", RUN / "clips" / "L1.mp4"),
         ("S2", RUN / "clips" / "S2.mp4"), ("L2", RUN / "clips" / "L2.mp4")]
media, files = [], {}
for i, (name, p) in enumerate(items):
    if not p.exists():
        continue
    key = f"f{i}"
    files[key] = (p.name, p.open("rb"), "video/mp4")
    m = {"type": "video", "media": f"attach://{key}", "supports_streaming": True}
    labels = {"master": "ФИНАЛ 15 с (9:16)", "S1": "S1 Seedance 0–5 с", "L1": "L1 local Wan 5–7.5 с",
              "S2": "S2 Seedance 7.5–12.5 с", "L2": "L2 local Wan 12.5–15 с"}
    m["caption"] = caption[:1000] if i == 0 else labels[name]
    media.append(m)
r = httpx.post(f"https://api.telegram.org/bot{TOKEN}/sendMediaGroup", data={"chat_id": 386321847, "media": json.dumps(media, ensure_ascii=False)},
               files=files, timeout=900)
print("sendMediaGroup", r.status_code, r.json().get("ok"), (r.json().get("description") or "")[:200])
