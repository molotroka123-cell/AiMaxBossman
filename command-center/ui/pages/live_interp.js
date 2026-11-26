/* ============================================================
   live_interp.js — «Переводчик звонков»: локальные субтитры любого звонка + перевод вашего голоса в звонок.
   API: bcc/features/live_interp.py, /api/live-interp/*.

   Правила экрана:
   * запускает и останавливает только владелец; у агента такой кнопки нет;
   * STOP владельца: старт отказывается, идущая сессия заканчивается сама;
   * звук не сохраняется никогда; расшифровка на диск — только если включено «Сохранять расшифровку»;
   * «Говорить» — удерживать: отпустили → перевод вашим голосом Piper уходит в виртуальный микрофон (VB-Cable).
   Модуль безопасен для импорта в Node: document трогается только внутри render().
   ============================================================ */

import { api } from '../api.js';
import { h, toastError } from '../components.js';
import { panel, pageHead, btn } from './_ui.js';

export const BASE = '/api/live-interp';
export const POLL_MS = 1000;

export const QUALITY_WORDS = {
  source: 'оригинал', direct: 'перевод', two_hop: 'перевод через английский (качество ниже)',
  unsupported: 'нет переводчика для этой пары — показан оригинал', failed: 'переводчик сломался — показан оригинал',
};

export const SPEAK_REASONS = {
  OWNER_STOP: 'остановлено: STOP владельца', TOO_SHORT: 'слишком коротко — держите кнопку, пока говорите',
  NO_SPEECH: 'речь не распознана', UNSUPPORTED_PAIR: 'для этой пары языков локального переводчика нет',
  TRANSLATION_FAILED: 'переводчик сломался', EGRESS_GUARD: 'фраза похожа на секрет — в звонок не отправлена',
  NO_OUTPUT_DEVICE: 'нет виртуального микрофона (VB-Cable): перевод не озвучен',
};

/** Человеческая строка субтитра. Перевод никогда не выдумывается: при unsupported/failed — оригинал и пометка. */
export function subtitleLine(ev) {
  const lang = ev.language ? `[${ev.language}] ` : '';
  const same = !ev.translation || ev.translation === ev.text;
  return { main: same ? ev.text : ev.translation, orig: same ? '' : `${lang}${ev.text}`,
    note: ev.quality && ev.quality !== 'direct' && ev.quality !== 'source' ? QUALITY_WORDS[ev.quality] || ev.quality : '' };
}

export function speakWords(res) {
  if (!res) return '';
  if (res.spoken) return `Озвучено: «${res.translation}»${res.latency_ms ? ` · ${res.latency_ms} мс` : ''}`;
  const reason = String(res.reason || '');
  const key = Object.keys(SPEAK_REASONS).find((k) => reason.startsWith(k));
  return `Не озвучено: ${key ? SPEAK_REASONS[key] : reason || 'неизвестно'}`;
}

export function readinessWords(st) {
  const c = (st && st.capture) || {}; const o = ((st && st.speak) || {}).output || {};
  return [
    c.status === 'READY' ? 'Системный звук: готов' : `Системный звук: ${c.reason || 'недоступен'}`,
    st && st.translation_model ? 'Переводчик en→ru: установлен' : 'Переводчик en→ru: не задан BOSSMAN_VIDEO_TRANSLATION_MODEL',
    o.status === 'READY' ? `Виртуальный микрофон: ${o.output_device}` : `Виртуальный микрофон: ${o.reason || 'недоступен'}`,
  ];
}

class LiveView {
  constructor(ctx) {
    this.ctx = ctx; this.after = 0; this.timer = null;
    this.subs = h('div.li-subs');
    this.status = h('div.li-status');
    this.speakOut = h('p.muted', '');
    this.target = h('select', h('option', { value: 'ru' }, 'Субтитры на русском'), h('option', { value: 'en' }, 'Субтитры на английском'));
    this.speakTarget = h('select', h('option', { value: 'en' }, 'Мой голос → английский'), h('option', { value: 'ru' }, 'Мой голос → русский'));
    this.keep = h('input', { type: 'checkbox' });
    const talk = btn('Удерживайте и говорите', null, { variant: 'primary', size: 'lg' });
    talk.addEventListener('pointerdown', () => this.speakStart());
    for (const ev of ['pointerup', 'pointerleave', 'pointercancel']) talk.addEventListener(ev, () => this.speakStop());
    this.root = h('div.bx-page',
      pageHead('Переводчик звонков', 'Локально: Whisper + Marian + Piper. Звук не сохраняется. Работает с любым звонком на этом ПК.'),
      panel('Готовность', this.status),
      panel('Субтитры', h('div',
        h('div.li-row', this.target, h('label', this.keep, ' Сохранять расшифровку'),
          btn('Старт', () => this.start(), { variant: 'primary' }), btn('Стоп', () => this.stop()),
          h('a', { href: `${BASE}/export?fmt=srt`, target: '_blank' }, 'SRT'), ' · ',
          h('a', { href: `${BASE}/export?fmt=md`, target: '_blank' }, 'Markdown')),
        this.subs)),
      panel('Перевод моего голоса в звонок', h('div', h('div.li-row', this.speakTarget, talk), this.speakOut,
        h('p.muted', 'В Zoom/Telegram выберите микрофон «CABLE Output». Собеседников стоит предупредить о машинном переводе.'))));
    this.timer = setInterval(() => this.tick(), POLL_MS);
    this.refresh();
  }

  async refresh() {
    try {
      const st = await api.raw(`${BASE}/status`);
      this.status.replaceChildren(...readinessWords(st).map((t) => h('div', t)),
        h('div', st.global_stop ? 'STOP владельца активен — запуск невозможен' : ''),
        h('div', st.active ? `Идёт сессия: фраз ${st.active.segments}` : 'Сессии нет'));
    } catch (e) { this.status.replaceChildren(h('div', 'Статус недоступен')); }
  }

  async tick() {
    if (!this.root.isConnected) { clearInterval(this.timer); return; }
    try {
      const r = await api.raw(`${BASE}/events?after=${this.after}`);
      for (const ev of r.events || []) {
        this.after = Math.max(this.after, ev.id || 0);
        if (ev.type === 'subtitle') {
          const line = subtitleLine(ev);
          this.subs.prepend(h('div.li-line', h('div.li-main', line.main), line.orig ? h('div.muted', line.orig) : null,
            line.note ? h('div.li-note', line.note) : null));
          while (this.subs.childElementCount > 200) this.subs.lastElementChild.remove();
        } else if (ev.type === 'ended') this.refresh();
      }
    } catch { /* следующий тик */ }
  }

  async start() {
    try {
      await api.raw(`${BASE}/start`, { method: 'POST', body: { target: this.target.value, keep_transcript: this.keep.checked } });
      this.subs.replaceChildren(); this.refresh();
    } catch (e) { toastError(e); }
  }

  async stop() { try { await api.raw(`${BASE}/stop`, { method: 'POST' }); } catch { /* нет сессии */ } this.refresh(); }

  async speakStart() {
    if (this.speaking) return; this.speaking = true;
    this.speakOut.textContent = 'Говорите…';
    try { await api.raw(`${BASE}/speak/start`, { method: 'POST', body: { target: this.speakTarget.value } }); }
    catch (e) { this.speaking = false; this.speakOut.textContent = ''; toastError(e); }
  }

  async speakStop() {
    if (!this.speaking) return; this.speaking = false;
    this.speakOut.textContent = 'Перевожу…';
    try { this.speakOut.textContent = speakWords(await api.raw(`${BASE}/speak/stop`, { method: 'POST' })); }
    catch (e) { this.speakOut.textContent = ''; toastError(e); }
  }
}

const LiveInterpPage = {
  id: 'live-interp', title: 'Переводчик звонков', icon: 'activity', nav: 'more', section: 'apps',
  async render(ctx) { return new LiveView(ctx).root; },
};

export default LiveInterpPage;
