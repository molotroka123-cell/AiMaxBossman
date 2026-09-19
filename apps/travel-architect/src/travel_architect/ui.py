
from __future__ import annotations
from fastapi.responses import HTMLResponse

def romantic_weekend_page(data: dict) -> HTMLResponse:
    """Демо-показ: карточки мест и таймлайн — без зависимости от сети или БД."""
    kind_icon = {"hotel": "🏨", "dinner": "🍷", "breakfast": "☕", "activity": "🚤"}
    cards = "".join(
        f"""<div class="place">
              <div class="place-top"><span class="ic">{kind_icon.get(p['kind'], '📍')}</span>
              <b>{p['name']}</b></div>
              <div class="small">{p['time']}</div>
              <p class="small">{p['note']}</p>
              <div class="price">{p['price_eur']} €</div>
            </div>"""
        for p in data["places"]
    )
    timeline = "".join(
        f"""<div class="tl-day"><b>{d['day']}</b><ul>{''.join(f'<li>{x}</li>' for x in d['items'])}</ul></div>"""
        for d in data["timeline"]
    )
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{data['title']}</title>
<style>
body{{font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:linear-gradient(160deg,#1a0f2e,#2a1240 60%,#3d1240);color:#fdf3ff;margin:0}}
main{{max-width:1040px;margin:0 auto;padding:40px 20px}}
h1{{margin:0 0 4px}} .sub{{opacity:.78;margin:0 0 28px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:16px;margin-bottom:32px}}
.place{{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);border-radius:16px;padding:16px;backdrop-filter:blur(4px)}}
.place-top{{display:flex;gap:8px;align-items:center;margin-bottom:6px}} .ic{{font-size:20px}}
.price{{margin-top:10px;font-weight:700;color:#ffb3ec}}
.small{{opacity:.78;font-size:13px;margin:2px 0}}
.timeline{{background:rgba(255,255,255,.05);border-radius:16px;padding:20px}}
.tl-day{{display:flex;gap:14px;padding:8px 0;border-top:1px solid rgba(255,255,255,.08)}}
.tl-day:first-child{{border-top:none}} .tl-day b{{min-width:110px;flex:none}}
.tl-day ul{{margin:0;padding-left:18px}}
</style></head><body><main>
<h1>{data['title']}</h1>
<p class="sub">{data['subtitle']}</p>
<div class="grid">{cards}</div>
<h2>Таймлайн</h2>
<div class="timeline">{timeline}</div>
</main></body></html>"""
    return HTMLResponse(html)


def app_shell(title: str, app_id: str, features: list[str]) -> HTMLResponse:
    cards="".join(f"<li>{x}</li>" for x in features)
    html=f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
body{{font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#0e1116;color:#eef2f7;margin:0}}
main{{max-width:980px;margin:40px auto;padding:0 20px}} .card{{background:#171c24;border:1px solid #2a3340;border-radius:16px;padding:20px;margin:16px 0}}
code{{background:#10141a;padding:3px 7px;border-radius:8px}} a{{color:#8fc7ff}} li{{margin:8px 0}}
.small{{opacity:.72}} .ok{{color:#8ee6a1}}
</style></head><body><main>
<h1>{title}</h1>
<p class="small">{app_id} · standalone-first · local-first</p>
<div class="card"><b class="ok">Service ready</b><p>OpenAPI: <a href="/docs">/docs</a> · Health: <a href="/health">/health</a> · Metrics: <a href="/metrics">/metrics</a></p></div>
<div class="card"><h3>Implemented features</h3><ul>{cards}</ul></div>
<div class="card"><h3>Bossman boundary</h3><p>App owns domain/data/jobs. Bossman is optional intelligence/control plane. No Bossman internal imports.</p></div>
</main></body></html>"""
    return HTMLResponse(html)
