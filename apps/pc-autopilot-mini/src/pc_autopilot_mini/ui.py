from __future__ import annotations

from fastapi.responses import HTMLResponse


def home_page() -> HTMLResponse:
    """A draft-first macro builder; this screen deliberately has no run action."""
    return HTMLResponse(r"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark"><title>PC Autopilot Mini</title>
<link rel="icon" href="data:,">
<style>
:root{font:15px/1.5 system-ui,sans-serif;color:#edf2fa;background:#0c1118;--panel:#151d27;--line:#2a3949;--muted:#9aaabd;--blue:#75b9ff;--green:#85d8a5;--red:#ff9d9d;--amber:#ffd48a}*{box-sizing:border-box}body{margin:0}main{max-width:980px;margin:32px auto;padding:0 20px 48px}h1{font-size:clamp(28px,5vw,42px);line-height:1.1;margin:0 0 8px}h2{font-size:18px;margin:0 0 8px}.muted{color:var(--muted)}.eyebrow{color:var(--blue);font-size:12px;text-transform:uppercase;letter-spacing:.12em;font-weight:700}.panel{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:20px;margin:16px 0}.field{display:grid;gap:5px;margin:10px 0}input,select,textarea,button{font:inherit;color:inherit;border-radius:9px;border:1px solid var(--line);background:#0f161f;padding:10px 12px}textarea{min-height:180px;resize:vertical;font:13px/1.5 ui-monospace,monospace}button{cursor:pointer;background:#193251;border-color:#315779}button:hover{background:#21466d}button:disabled{opacity:.6;cursor:wait}input:focus,select:focus,textarea:focus,button:focus-visible{outline:2px solid var(--blue);outline-offset:2px}.row{display:flex;gap:8px;align-items:end;flex-wrap:wrap}.row .field{flex:1;min-width:240px}.notice{min-height:1.6em;color:var(--muted)}.notice[data-kind="error"]{color:var(--red)}.notice[data-kind="success"]{color:var(--green)}.warning{border-left:3px solid var(--amber);padding:10px 14px;color:#ffe4b1;background:#2b2418;border-radius:6px}.output{min-height:90px;max-height:420px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;background:#0d131b;border:1px solid var(--line);border-radius:10px;padding:14px;font:13px/1.55 ui-monospace,monospace;color:#cee2f7}code{color:#bddcff}a{color:var(--blue)}
</style></head><body><main>
<p class="eyebrow">Local automation drafts</p><h1>PC Autopilot Mini</h1>
<p class="muted">Создайте макрос, проверьте структуру и посмотрите план dry-run. Этот экран намеренно не запускает макрос на компьютере.</p>
<p class="warning"><strong>Безопасный режим.</strong> Dry-run показывает шаги и нужные полномочия. Действия на ПК, файловые операции и клики здесь не исполняются.</p>
<p id="message" class="notice" role="status" aria-live="polite">Черновики и версии сохраняются локально сервисом.</p>
<section class="panel"><h2>1 · Новый макрос</h2><form id="create-macro">
<label class="field">Название<input name="name" required maxlength="120" placeholder="Например, собрать еженедельный отчёт"></label>
<label class="field">Шаги — JSON массив. Начните с безопасного ожидания<textarea name="steps" required>[{"action":"wait","seconds":1}]</textarea></label>
<p class="muted">Поддерживаемые локальные типы включают <code>wait</code>, <code>file.copy</code>, <code>file.move</code>, <code>file.mkdir</code> и <code>verify.exists</code>. Клики и ввод текста требуют отдельного управления через BOSSMAN.</p>
<button type="submit">Проверить и сохранить черновик</button></form></section>
<section class="panel"><h2>2 · Проверить план</h2><div class="row"><label class="field">Макрос<select id="macro-select" aria-label="Выберите макрос"><option value="">Загружаю…</option></select></label><button id="reload-macros" type="button">Обновить список</button><button id="dry-run" type="button">Показать dry-run</button></div><pre id="preview" class="output" aria-label="План dry-run" aria-live="polite">Здесь появится план без выполнения.</pre></section>
<section class="panel"><h2>Почему здесь нет кнопки «Запустить»</h2><p class="muted">Автоматическое исполнение затрагивает реальный компьютер. Этот интерфейс только валидирует и показывает план. Для действий через BOSSMAN требуется отдельная проверка, явное подтверждение владельца и действующие разрешения. Никогда не помещайте пароли, токены или платёжные данные в шаги.</p><a href="/docs">API и схема данных</a></section>
</main><script>
const $=(s)=>document.querySelector(s),message=$('#message'),select=$('#macro-select'),preview=$('#preview');
function say(text,kind=''){message.textContent=text;message.dataset.kind=kind}
async function api(path,options={}){const response=await fetch(path,{...options,headers:{'content-type':'application/json',...(options.headers||{})}});let data={};try{data=await response.json()}catch{}if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:(data.detail?.message||data.message||`HTTP ${response.status}`));return data}
const body=(value)=>JSON.stringify(value);
async function loadMacros(preferred=''){const {macros=[]}=await api('/api/macros');select.replaceChildren();for(const macro of macros){const option=document.createElement('option');option.value=macro.id;option.textContent=`${macro.name} · draft v${macro.version}`;select.append(option)}if(!macros.length){const option=document.createElement('option');option.value='';option.textContent='Создайте первый черновик';select.append(option)}select.value=preferred||macros[0]?.id||''}
$('#create-macro').addEventListener('submit',async(event)=>{event.preventDefault();const form=new FormData(event.currentTarget);try{const steps=JSON.parse(form.get('steps'));if(!Array.isArray(steps)||!steps.length)throw new Error('Добавьте хотя бы один шаг');const macro=await api('/api/macros',{method:'POST',body:body({name:form.get('name'),steps})});await loadMacros(macro.id);preview.textContent='Черновик сохранён. Нажмите «Показать dry-run», чтобы посмотреть его план.';say('Макрос прошёл проверку и сохранён как черновик. Ничего не запускалось.','success')}catch(e){say(e.message,'error')}});
$('#reload-macros').addEventListener('click',()=>loadMacros(select.value).then(()=>say('Список обновлён.','success')).catch(e=>say(e.message,'error')));
$('#dry-run').addEventListener('click',async()=>{if(!select.value){say('Сначала создайте или выберите черновик.','error');return}try{const result=await api(`/api/macros/${encodeURIComponent(select.value)}/dry-run`,{method:'POST',body:body({variables:{}})});preview.textContent=JSON.stringify(result,null,2);say(result.requires_bossman?'План показан. Для части шагов требуется отдельный путь через BOSSMAN.':'План показан. Шаги не исполнялись.','success')}catch(e){say(e.message,'error')}});
loadMacros().catch(e=>say(`Не удалось загрузить макросы: ${e.message}`,'error'));
</script></body></html>""")
