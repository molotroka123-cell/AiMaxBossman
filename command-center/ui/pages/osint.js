/* OSINT — кнопка в сайдбаре. Панель OSIRIS открывается внутри оболочки. */

import { api } from '../api.js';
import { h, icon } from '../components.js';
import { pageHead } from './_ui.js';

const OsintPage = {
  id: 'osint',
  title: 'OSINT',
  icon: 'search',
  nav: 'primary',

  async render(ctx) {
    let twitter = { status: 'frozen', reason: 'слот заморожен' };
    try { twitter = await api.raw('/api/twitter/status'); } catch { /* frozen anyway */ }

    const open = () => ctx.navigate('apps', { open: 'osiris' });

    return h('div.bx-page',
      pageHead('OSINT', 'Публичные источники. Уровень 0 закрыт. Twitter — заморожен.', {
        actions: [
          h('button.bx-btn.bx-btn-primary', { type: 'button', onClick: open },
            icon('search', 16), h('span', 'Открыть OSIRIS')),
        ],
      }),
      h('section.bx-panel',
        h('div.bx-panel-body',
          h('div.bx-attn-list',
            h('button.bx-attn-row', { type: 'button', onClick: open },
              h('span.bx-attn-dot'),
              h('span.bx-attn-main',
                h('span.bx-attn-title', 'OSIRIS'),
                h('span.bx-attn-note', 'граф организаций · паспорт факта · 127.0.0.1:8920')),
              h('span.bx-attn-go', 'Открыть')),
            h('div.bx-attn-row', { style: { cursor: 'default' } },
              h('span.bx-attn-dot.is-warn'),
              h('span.bx-attn-main',
                h('span.bx-attn-title', 'Twitter / X'),
                h('span.bx-attn-note', twitter.reason || 'frozen')),
              h('span.badge', String(twitter.status || 'frozen').toUpperCase()))))));
  },
};

export default OsintPage;
