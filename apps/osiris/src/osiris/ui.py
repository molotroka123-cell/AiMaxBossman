from __future__ import annotations

from fastapi.responses import HTMLResponse

HTML = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>OSIRIS</title>
<style>
:root{--bg:#0e1116;--card:#171c24;--line:#2a3340;--txt:#eef2f7;--mut:#8b98a8;--ok:#8ee6a1;--bad:#ff8d8d;--acc:#8fc7ff}
*{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:var(--bg);color:var(--txt)}
main{max-width:1100px;margin:0 auto;padding:28px 20px}h1{margin:0 0 6px}
.mut{color:var(--mut);font-size:13px}.row{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin:12px 0}
input,textarea,select,button{width:100%;margin:6px 0;padding:8px 10px;border-radius:8px;border:1px solid var(--line);background:#10141a;color:var(--txt)}
button{cursor:pointer;background:#203049}button:hover{background:#27405e}
pre{white-space:pre-wrap;word-break:break-word;font-size:12px;background:#10141a;padding:10px;border-radius:8px;max-height:280px;overflow:auto}
.ok{color:var(--ok)}.bad{color:var(--bad)}a{color:var(--acc)}
svg{width:100%;height:280px;background:#10141a;border-radius:8px}
@media(max-width:800px){.row{grid-template-columns:1fr}}
</style></head><body><main>
<h1>OSIRIS</h1>
<p class="mut">публичные источники · паспорт факта · уровень 0 закрыт · 127.0.0.1</p>
<div class="card"><b class="ok" id="health">…</b> · <a href="/docs">/docs</a> · <a href="/health">/health</a></div>
<div class="row">
<div class="card">
<h3>Факт об организации</h3>
<input id="f_subj" placeholder="субъект (org:ИНН или slug)">
<input id="f_pred" placeholder="предикат: legal_name | director | founder | official_url">
<input id="f_obj" placeholder="значение">
<input id="f_src" placeholder="источник">
<input id="f_url" placeholder="https://…">
<input id="f_lic" placeholder="лицензия/условия" value="source-tos">
<select id="f_method"><option>http_get</option><option>official_api</option><option>sparql</option><option>registry</option><option>manual</option></select>
<input id="f_conf" type="number" step="0.1" min="0" max="1" value="0.7">
<button onclick="saveFact()">Сохранить факт</button>
</div>
<div class="card">
<h3>Именной грант уровня 1</h3>
<input id="g_author" placeholder="автор">
<input id="g_subj" placeholder="источник или субъект">
<input id="g_reason" placeholder="причина">
<select id="g_clause">
<option value="scrape_unspecified_robots">scrape_unspecified_robots</option>
<option value="rate_above_polite">rate_above_polite</option>
<option value="public_official_by_office">public_official_by_office</option>
<option value="registry_related_persons">registry_related_persons</option>
<option value="ttl_extension">ttl_extension</option>
<option value="paid_api">paid_api</option>
<option value="export_outbound">export_outbound</option>
<option value="hypothesis_to_fact">hypothesis_to_fact</option>
</select>
<input id="g_ttl" type="number" value="24" min="1" max="2160">
<button onclick="issueGrant()">Выдать грант</button>
</div>
</div>
<div class="row">
<div class="card"><h3>Граф</h3><svg id="g"></svg></div>
<div class="card"><h3>Журнал</h3><pre id="log"></pre></div>
</div>
<div class="card"><h3>Ответ</h3><pre id="out"></pre></div>
<script>
async function j(u,opt){const r=await fetch(u,opt);const t=await r.text();let d;try{d=JSON.parse(t)}catch{d={raw:t}}
 if(!r.ok) throw d; return d;}
function show(x){document.getElementById('out').textContent=JSON.stringify(x,null,2)}
async function refresh(){
  const h=await j('/health'); document.getElementById('health').textContent=h.status+' · '+h.app+' · L0 sealed';
  const log=await j('/api/journal?limit=20'); document.getElementById('log').textContent=JSON.stringify(log.events,null,2);
  const g=await j('/api/graph'); draw(g);
}
function draw(g){
  const svg=document.getElementById('g'); svg.innerHTML='';
  const nodes=g.nodes||[]; const W=900,H=260;
  nodes.forEach((n,i)=>{
    const x=80+(i%6)*140, y=50+Math.floor(i/6)*90;
    const c=document.createElementNS('http://www.w3.org/2000/svg','circle');
    c.setAttribute('cx',x);c.setAttribute('cy',y);c.setAttribute('r',18);c.setAttribute('fill','#2a4a6a');
    const t=document.createElementNS('http://www.w3.org/2000/svg','text');
    t.setAttribute('x',x-40);t.setAttribute('y',y+36);t.setAttribute('fill','#eef2f7');t.setAttribute('font-size','11');
    t.textContent=(n.label||n.id).slice(0,18);
    svg.appendChild(c);svg.appendChild(t);
  });
  (g.edges||[]).forEach(e=>{
    const a=nodes.findIndex(n=>n.id===e.src), b=nodes.findIndex(n=>n.id===e.dst);
    if(a<0||b<0) return;
    const l=document.createElementNS('http://www.w3.org/2000/svg','line');
    l.setAttribute('x1',80+(a%6)*140); l.setAttribute('y1',50+Math.floor(a/6)*90);
    l.setAttribute('x2',80+(b%6)*140); l.setAttribute('y2',50+Math.floor(b/6)*90);
    l.setAttribute('stroke','#8fc7ff'); svg.appendChild(l);
  });
}
async function saveFact(){
  try{
    const body={subject:f_subj.value,predicate:f_pred.value,object:f_obj.value,source:f_src.value,url:f_url.value,method:f_method.value,license:f_lic.value,confidence:Number(f_conf.value)};
    show(await j('/api/facts',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)}));
    await refresh();
  }catch(e){show(e)}
}
async function issueGrant(){
  try{
    const body={author:g_author.value,source_or_subject:g_subj.value,reason:g_reason.value,clause:g_clause.value,ttl_hours:Number(g_ttl.value)};
    show(await j('/api/grants',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)}));
    await refresh();
  }catch(e){show(e)}
}
refresh().catch(e=>show(e));
</script>
</main></body></html>
"""


def page() -> HTMLResponse:
    return HTMLResponse(HTML)
