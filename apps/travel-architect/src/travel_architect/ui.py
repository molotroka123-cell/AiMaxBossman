
from __future__ import annotations
from fastapi.responses import HTMLResponse

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
