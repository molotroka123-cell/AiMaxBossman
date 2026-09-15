import {api} from '../api.js';

// A modal owns one captured project/version. Navigation and retries cannot
// retarget a draft, and only the existing Bossman API is allowed to persist it.
export async function openVisualEditor({id, site, originalCode}) {
  const response = await fetch('/api/web-designer/visual-editor', {credentials: 'same-origin'});
  if (!response.ok) throw new Error('Файлы конструктора недоступны');
  const shell = (await response.text()).replaceAll('src="/', `src="${location.origin}/`)
    .replaceAll('href="/', `href="${location.origin}/`);
  return new Promise(resolve => {
    const nonce = crypto.randomUUID();
    const dialog = document.createElement('dialog');
    dialog.setAttribute('aria-label', 'Визуальный конструктор');
    dialog.style.cssText = 'width:96vw;max-width:1800px;height:92vh;padding:0;border:1px solid #596174;border-radius:12px;background:#20242e;color:#e7eaf2;';
    const bar = document.createElement('div');
    bar.style.cssText = 'display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:10px 14px;font:13px system-ui,sans-serif';
    const title = document.createElement('strong'); title.textContent = 'Конструктор сайта';
    const status = document.createElement('span'); status.textContent = 'Загрузка…';
    status.setAttribute('role', 'status'); status.style.flex = '1';
    const button = text => {
      const b = document.createElement('button'); b.type = 'button'; b.textContent = text;
      b.style.cssText = 'padding:8px 12px;border:1px solid #596174;border-radius:7px;background:#303745;color:inherit;cursor:pointer';
      return b;
    };
    const save = button('Сохранить сайт'); const download = button('Скачать черновик'); const close = button('Закрыть');
    save.disabled = download.disabled = true;
    bar.append(title, status, download, save, close);
    const frame = document.createElement('iframe');
    // A data: document has its OWN opaque origin. allow-same-origin lets its
    // about:blank canvas inherit that opaque origin, so GrapesJS can edit the
    // canvas. It never gives this document the Command Center origin.
    // srcdoc/blob/HTTP must NOT be substituted here: those inherit our origin.
    frame.title = 'Конструктор блоков'; frame.setAttribute('sandbox', 'allow-scripts allow-same-origin');
    frame.style.cssText = 'width:100%;height:calc(100% - 62px);border:0;display:block';
    let dirty = false, busy = false, committed = false, ready = false;
    const pending = new Map();
    const send = (type, data = {}) => frame.contentWindow?.postMessage({source: 'bossman-visual-host', nonce, type, ...data}, '*');
    const timer = setTimeout(() => { if (!ready) status.textContent = 'Конструктор не загрузился. Закройте его и откройте снова.'; }, 15000);
    function finish() {
      clearTimeout(timer); window.removeEventListener('message', onMessage);
      window.removeEventListener('beforeunload', beforeUnload);
      for (const entry of pending.values()) { clearTimeout(entry.timer); entry.reject(new Error('Конструктор закрыт')); }
      pending.clear(); dialog.close(); dialog.remove(); resolve(committed);
    }
    function beforeUnload(event) { if (dirty) { event.preventDefault(); event.returnValue = ''; } }
    function requestClose() {
      if (busy) return;
      if (!dirty || window.confirm('Закрыть конструктор без сохранения изменений?')) finish();
    }
    function snapshot() {
      return new Promise((accept, reject) => {
        const requestId = crypto.randomUUID();
        const timer = setTimeout(() => { pending.delete(requestId); reject(new Error('Конструктор не ответил. Черновик остаётся открыт.')); }, 10000);
        pending.set(requestId, {accept, reject, timer}); send('snapshot', {requestId});
      });
    }
    function onMessage(event) {
      const data = event.data;
      if (event.source !== frame.contentWindow || event.origin !== 'null'
        || data?.source !== 'bossman-visual' || data.nonce !== nonce) return;
      if (data.type === 'ready') send('init', {site});
      if (data.type === 'loaded') {
        ready = true; clearTimeout(timer); save.disabled = download.disabled = false;
        status.textContent = 'Перетаскивайте блоки на страницу';
      }
      if (data.type === 'changed') { dirty = true; status.textContent = 'Есть несохранённые изменения'; }
      if (data.type === 'error') status.textContent = data.message || 'Ошибка конструктора';
      if (data.type === 'snapshot' && pending.has(data.requestId)) {
        const entry = pending.get(data.requestId); pending.delete(data.requestId); clearTimeout(entry.timer);
        if (typeof data.body !== 'string' || typeof data.css !== 'string' || data.body.length + data.css.length > 2000000) entry.reject(new Error('Некорректный размер черновика'));
        else entry.accept({body: data.body, css: data.css, body_attributes: data.body_attributes || {}});
      }
    }
    save.addEventListener('click', async () => {
      if (busy || !ready) return;
      if (!dirty) { finish(); return; }
      busy = true; save.disabled = close.disabled = true;
      // Prevent edits while a snapshot is being saved: a late keystroke must
      // not disappear when the accepted snapshot closes the modal.
      frame.style.pointerEvents = 'none'; frame.inert = true;
      try {
        const draft = await snapshot();
        status.textContent = 'Сохранение…';
        const result = await api.raw(`/api/web-designer/projects/${id}/visual`, {
          method: 'PUT', body: {...draft, base_version: site.meta.version},
        });
        if (!result.ok) throw new Error('Сервер не подтвердил сохранение');
        committed = true; dirty = false; finish();
      } catch (error) { status.textContent = error.message || 'Не удалось сохранить. Черновик остаётся открыт.'; }
      finally { busy = false; save.disabled = close.disabled = false; frame.style.pointerEvents = ''; frame.inert = false; }
    });
    download.addEventListener('click', async () => {
      try {
        const draft = await snapshot();
        const doc = new DOMParser().parseFromString(originalCode, 'text/html');
        doc.body.innerHTML = draft.body;
        for (const [name, value] of Object.entries(draft.body_attributes)) doc.body.setAttribute(name, value ?? '');
        doc.head.querySelectorAll('style[data-bossman-visual]').forEach(n => n.remove());
        const style = doc.createElement('style'); style.textContent = draft.css;
        style.setAttribute('data-bossman-visual', 'grapesjs'); doc.head.append(style);
        const url = URL.createObjectURL(new Blob(['<!doctype html>\n' + doc.documentElement.outerHTML], {type: 'text/html;charset=utf-8'}));
        const link = document.createElement('a'); link.href = url; link.download = `site-${id}-draft.html`; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      } catch (error) { status.textContent = error.message; }
    });
    close.addEventListener('click', requestClose);
    dialog.addEventListener('cancel', e => { e.preventDefault(); requestClose(); });
    window.addEventListener('message', onMessage); window.addEventListener('beforeunload', beforeUnload);
    frame.src = `data:text/html;charset=utf-8,${encodeURIComponent(shell)}#${nonce}`;
    dialog.append(bar, frame); document.body.append(dialog); dialog.showModal();
  });
}
