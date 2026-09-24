/* Bossman 1.5 owner page — one runtime control surface shared with CMD. */
import { api } from '../api.js';
import { h, toast, toastError, toastOk } from '../components.js';
import { panel, pageHead, pill, tag, tile, errorNote } from './_ui.js';

function yn(v) { return v ? 'да' : 'нет'; }
function stateTone(v) {
  const s = String(v || '').toUpperCase();
  if (['READY','STARTED','RUNNING','COMPLETE_QUARANTINED','FINISHED'].includes(s)) return 'ok';
  if (['BLOCKED','FAIL','ERROR','UNREADABLE'].includes(s)) return 'err';
  return 'info';
}
function valuePair(pair) {
  return Array.isArray(pair) && pair[0] != null ? `${pair[0]} ${pair[1] || ''}`.trim() : 'UNKNOWN';
}

async function call(path, body, ctx, okText) {
  try {
    const out = await api.raw(path, { method: 'POST', body: body || {} });
    toastOk(okText || 'Готово');
    ctx.refresh();
    return out;
  } catch (err) {
    toastError(err);
    throw err;
  }
}

const Page = {
  id: 'v15_owner',
  title: 'Bossman 1.5',
  icon: 'bolt',
  nav: 'primary',
  section: 'work',

  async render(ctx) {
    let data;
    try { data = await api.raw('/api/v15/owner/status'); }
    catch (err) {
      return h('div.bx-page',
        pageHead('Bossman 1.5', 'Owner-run без Aster: Twitch + Telegram + self-improvement.'),
        errorNote(err, () => ctx.refresh()));
    }

    const pf = data.preflight || {};
    const market = data.market || {};
    const ms = market.state || {};
    const last = ms.last || {};
    const self = data.self_improvement || {};
    const bootstrap = self.bootstrap || {};
    const evo = self.evolution || {};
    const economy = data.economy || {};
    const delivery = market.last_delivery || {};

    const head = pageHead('Bossman 1.5',
      'Одна кнопка запускает read-only Twitch монитор с Telegram и Bossman self-improvement/YouTube learning параллельно.',
      { pills: [
        pill(self.running ? 'работает' : 'остановлен', { tone: self.running ? 'ok' : 'idle' }),
        pill('Aster', { tone: 'info', value: 'не нужен runtime' }),
      ] });

    const controls = panel('Управление',
      h('div',
        h('div.bx-actions',
          h('button.btn.btn-primary', { type: 'button', onClick: () =>
            call('/api/v15/owner/start', {
              allow_paid_finalizer: true, glm_cap_usd: 0.50, market_cadence_s: 15, youtube_url: '',
            }, ctx, 'Bossman 1.5 запущен') }, '▶ Старт 1.5'),
          h('button.btn', { type: 'button', onClick: () =>
            call('/api/v15/owner/quick-test', {}, ctx, 'Quick Test выполнен') }, '⚡ Quick Test'),
          h('button.btn', { type: 'button', onClick: () => ctx.refresh() }, '↻ Обновить'),
          h('button.btn.btn-danger', { type: 'button', onClick: () =>
            call('/api/v15/owner/stop', {}, ctx, 'STOP отправлен') }, '■ STOP')),
        h('p.small.dim',
          'Старт не включает реальные сделки. GLM имеет campaign cap $0.50; free workers идут первыми. '
          + 'STOP пишет durable stop для Twitch, economy и self-improvement.')),
      { icon: 'play' });

    const preflight = panel('Готовность',
      h('div.bx-cards',
        tile({ title: 'Exact build', sub: pf.source_sha || 'UNKNOWN',
          statusNode: pill(pf.source_proven ? 'PASS' : 'UNKNOWN', { tone: pf.source_proven ? 'ok' : 'err' }) }),
        tile({ title: 'OpenRouter', sub: 'ключ не показывается',
          statusNode: pill(yn(pf.openrouter_key_present), { tone: pf.openrouter_key_present ? 'ok' : 'err' }) }),
        tile({ title: 'Jev', sub: 'System-1 router',
          statusNode: pill(yn(pf.jev_key_present), { tone: pf.jev_key_present ? 'ok' : 'err' }) }),
        tile({ title: 'Runtime', sub: 'Codex/Aster не нужны для обычной работы',
          statusNode: pill('Bossman', { tone: 'ok' }) })),
      { icon: 'check' });

    const marketPanel = panel('Twitch → OI/CVD → Telegram',
      h('div',
        h('div.bx-tags',
          tag('collector', { bold: market.running ? 'RUNNING' : (ms.state || 'STOPPED') }),
          tag('attempts', { bold: String(ms.attempts_this_run ?? 0) }),
          tag('price', { bold: last.price != null ? String(last.price) : 'UNKNOWN' }),
          tag('CVD', { bold: valuePair(last.cvd) }),
          tag('OI', { bold: valuePair(last.oi) })),
        h('p.small',
          `Последний sample: ${last.at || 'нет'} · quality ${last.status || 'UNKNOWN'} · stream ${last.stream_state || 'UNKNOWN'}`),
        h('p.small.dim',
          delivery.sent
            ? `Telegram: отправлено · message_id ${delivery.message_id || delivery.id || '?'}`
            : `Telegram: ${delivery.error || delivery.reason || 'последней доставки пока нет'}`)),
      { icon: 'activity', aside: pill(market.running ? 'live' : 'idle', { tone: market.running ? 'ok' : 'idle' }) });

    const learnPanel = panel('Self-improvement + YouTube 14–27 августа',
      h('div',
        h('div.bx-tags',
          tag('process', { bold: self.running ? 'RUNNING' : 'STOPPED' }),
          tag('bootstrap', { bold: bootstrap.status || 'NOT_RUN' }),
          tag('evolution', { bold: evo.status || evo.control || 'NOT_RUN' }),
          tag('economy', { bold: economy.status || 'NOT_RUN' })),
        h('p.small',
          '3× Nemotron free → Ling free → GLM только финализатор. '
          + 'Trading teacher claims остаются QUARANTINE до outcome/OOS/TradingMemory verification.'),
        h('p.small.dim',
          bootstrap.gain_claim || 'Улучшение считается доказанным только после unseen transfer.')),
      { icon: 'models', aside: pill(self.running ? 'обучается' : 'готово к запуску',
        { tone: self.running ? 'ok' : 'info' }) });

    const phone = panel('Telegram owner-input',
      h('div',
        h('p.small', 'Заполнение текущей формы с телефона:'),
        h('pre.code', '/fill Имя=Timur; Телефон=+420...; Email=...'),
        h('p.small.dim',
          'Bossman свежо перечитывает экран, заполняет только однозначно сопоставленные поля и не нажимает '
          + 'Submit/Pay/Send. Пароли, OTP, CVV, номера карт и API keys через Telegram блокируются.')),
      { icon: 'edit' });

    return h('div.bx-page', head, controls, preflight, marketPanel, learnPanel, phone);
  },

  onEvent(ev) {
    const k = String(ev.kind || '');
    return k.startsWith('v15.') || k.startsWith('market.') || k.startsWith('evolution.');
  },
};

export default Page;
