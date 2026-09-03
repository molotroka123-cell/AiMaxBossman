/* ============================================================
   testing.js — режим тестового периода на стороне браузера.

   Три задачи:
     1. плашка наверху — режим виден, а не подразумевается;
     2. запись того, что делает владелец: клики, переходы, отправки форм,
        ошибки JS и неудачные запросы;
     3. кнопка «отправить в GitHub» — журнал уезжает одним коммитом.

   Что НЕ записывается никогда: значения полей ввода, содержимое паролей и
   токенов, тексты, которые владелец печатает. Записываются только имена
   элементов, по которым он нажал, и адреса, на которые перешёл. Секрет,
   попавший в журнал, обесценил бы всю затею: журнал уезжает в git.

   События копятся и уходят пачкой раз в несколько секунд, чтобы запись не
   превращалась в поток запросов на каждый клик.
   ============================================================ */

import { api } from './api.js';

const FLUSH_MS = 4000;
const MAX_QUEUE = 500;
const MAX_LABEL = 120;

let queue = [];
let timer = null;
let active = false;
let banner = null;

/* ---------------- запись ---------------- */

function push(kind, data) {
  if (!active) return;
  if (queue.length >= MAX_QUEUE) {
    // Очередь переполнена: теряем СТАРОЕ и говорим об этом, а не молчим.
    queue = queue.slice(-Math.floor(MAX_QUEUE / 2));
    queue.push({ kind: 'ui.queue_overflow', data: { dropped: MAX_QUEUE / 2 } });
  }
  queue.push({ kind, data: data || {} });
  schedule();
}

function schedule() {
  if (timer !== null) return;
  timer = setTimeout(flush, FLUSH_MS);
}

async function flush() {
  timer = null;
  if (!active || queue.length === 0) return;
  const batch = queue;
  queue = [];
  try {
    await api.raw('/api/testing/log', { method: 'POST', body: { events: batch } });
  } catch {
    // Сервер недоступен — возвращаем пачку в очередь, но не бесконечно:
    // журнал не должен съесть память вкладки.
    queue = batch.slice(-Math.floor(MAX_QUEUE / 2)).concat(queue);
  }
}

/* Короткое человекочитаемое имя элемента: что нажали, а не что напечатали. */
function describe(node) {
  if (!node || node === document) return '';
  const el = node.closest?.('button, a, [role="button"], input[type="submit"], .btn, .nav-item, [data-page]');
  if (!el) return '';
  const label = (el.getAttribute('aria-label') || el.title || el.dataset.page ||
                 (el.tagName === 'INPUT' ? el.value : el.textContent) || '').trim();
  const kind = el.tagName.toLowerCase();
  const id = el.id ? `#${el.id}` : '';
  return `${kind}${id} «${label.slice(0, MAX_LABEL)}»`;
}

/* ---------------- плашка ---------------- */

function styles() {
  if (document.getElementById('bcc-testing-style')) return;
  const css = document.createElement('style');
  css.id = 'bcc-testing-style';
  css.textContent = `
  :root{--bcc-testing-h:30px}
  /* Шапка тоже липкая и тоже на top:0 — без сдвига она наезжала бы на плашку
     при прокрутке. Правило действует только при включённом режиме. */
  .bcc-testing-on .topbar{top:var(--bcc-testing-h)}
  .bcc-testing-bar{position:sticky;top:0;z-index:40;display:flex;align-items:center;gap:10px;
    padding:7px 14px;font:600 12px/1.35 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
    letter-spacing:.02em;color:#0e0b03;background:linear-gradient(90deg,#f5c451,#f0a93b);
    border-bottom:1px solid rgba(0,0,0,.25);box-shadow:0 1px 8px rgba(0,0,0,.25)}
  .bcc-testing-bar b{font-weight:800;letter-spacing:.06em}
  .bcc-testing-sp{flex:1}
  .bcc-testing-count{font-weight:600;opacity:.85;font-variant-numeric:tabular-nums}
  .bcc-testing-btn{appearance:none;border:1px solid rgba(0,0,0,.35);border-radius:7px;
    background:rgba(0,0,0,.14);color:#0e0b03;font:600 12px/1 inherit;padding:6px 11px;cursor:pointer}
  .bcc-testing-btn:hover{background:rgba(0,0,0,.24)}
  .bcc-testing-btn[disabled]{opacity:.55;cursor:progress}
  .bcc-testing-msg{font-weight:600;max-width:46ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  @media (max-width:640px){.bcc-testing-count,.bcc-testing-msg{display:none}}`;
  document.head.appendChild(css);
}

function mountBanner(status) {
  styles();
  const bar = document.createElement('div');
  bar.className = 'bcc-testing-bar';
  bar.setAttribute('role', 'status');
  bar.innerHTML = `
    <b>TESTING PERIOD</b>
    <span>идёт запись действий — сессия <code id="bcc-testing-session"></code></span>
    <span class="bcc-testing-sp"></span>
    <span class="bcc-testing-msg" id="bcc-testing-msg"></span>
    <span class="bcc-testing-count" id="bcc-testing-count"></span>
    <button class="bcc-testing-btn" id="bcc-testing-publish" type="button">Отправить в GitHub</button>`;
  // ВАЖНО: не в #shell. Он — двухколоночная сетка, и вставленная первой плашка
  // занимает колонку сайдбара, ломая всю раскладку. Место плашки — внутри
  // .main, над липкой шапкой: там же живёт баннер устаревших данных.
  const main = document.querySelector('#shell .main');
  if (main) {
    main.prepend(bar);
    document.body.classList.add('bcc-testing-on');
  } else {
    document.body.prepend(bar);
  }
  bar.querySelector('#bcc-testing-session').textContent = status.session || '—';
  bar.querySelector('#bcc-testing-publish').addEventListener('click', publish);
  banner = bar;
  return bar;
}

function say(text, ms = 8000) {
  const box = document.getElementById('bcc-testing-msg');
  if (!box) return;
  box.textContent = text;
  if (ms) setTimeout(() => { if (box.textContent === text) box.textContent = ''; }, ms);
}

async function refreshCount() {
  if (!active) return;
  try {
    const status = await api.raw('/api/testing/status');
    const box = document.getElementById('bcc-testing-count');
    if (box) box.textContent = `${status.events ?? 0} записей`;
  } catch { /* счётчик — удобство, его отказ не важен */ }
}

/* ---------------- публикация ---------------- */

async function publish() {
  const btn = document.getElementById('bcc-testing-publish');
  if (!btn) return;
  btn.disabled = true;
  const was = btn.textContent;
  btn.textContent = 'Отправляю…';
  say('собираю журнал и чищу секреты…', 0);
  try {
    await flush();                       // сначала доложить всё, что накопилось
    const res = await api.raw('/api/testing/publish', { method: 'POST', body: {} });
    if (res.published) {
      say(`отправлено: ${res.sha} · ${res.summary?.total ?? 0} записей · вычищено ${res.redactions}`);
    } else {
      // Отказ — это результат, а не молчание: показываем причину как есть.
      say(`не отправлено: ${res.reason || 'причина неизвестна'}`, 20000);
    }
  } catch (err) {
    say(`ошибка публикации: ${err?.message || err}`, 20000);
  } finally {
    btn.disabled = false;
    btn.textContent = was;
    refreshCount();
  }
}

/* ---------------- подключение слушателей ---------------- */

function listen() {
  document.addEventListener('click', (e) => {
    const what = describe(e.target);
    if (what) push('ui.click', { element: what, page: location.hash || '#' });
  }, true);

  document.addEventListener('submit', (e) => {
    const form = e.target;
    push('ui.submit', { form: form?.id || form?.name || 'без имени', page: location.hash || '#' });
  }, true);

  window.addEventListener('hashchange', () => {
    push('ui.navigate', { to: location.hash || '#' });
  });

  window.addEventListener('error', (e) => {
    push('ui.error', { message: String(e.message || '').slice(0, 500),
                       source: String(e.filename || '').slice(-120), line: e.lineno });
  });

  window.addEventListener('unhandledrejection', (e) => {
    push('ui.error', { message: `unhandledrejection: ${String(e.reason?.message || e.reason || '').slice(0, 500)}` });
  });

  // Уходя со страницы, стараемся не потерять хвост очереди.
  window.addEventListener('pagehide', () => { flush(); });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flush();
  });
}

/**
 * Включает режим, если сервер сказал, что он включён. Любая ошибка здесь не
 * должна мешать приложению работать: тестовый период — наблюдатель, а не
 * условие запуска.
 */
export async function mountTestingPeriod() {
  try {
    const status = await api.raw('/api/testing/status');
    if (!status || !status.enabled) return null;
    active = true;
    mountBanner(status);
    listen();
    push('ui.session_open', { page: location.hash || '#',
                              screen: `${window.innerWidth}x${window.innerHeight}`,
                              ua: navigator.userAgent.slice(0, 200) });
    refreshCount();
    setInterval(refreshCount, 15000);
    return status;
  } catch {
    return null;
  }
}

export const _internal = { describe, push, flush };
