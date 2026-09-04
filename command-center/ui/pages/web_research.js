/* ============================================================
   web_research.js — «Поиск в интернете»: что система умеет искать
   сейчас, куда она за этим ходит и что уже прочитала.

   Endpoints: GET /api/web, GET /api/web/sources, GET /api/web/episodes.
   Ничего не меняет: страница только показывает. Поиск запускает модель
   инструментами web.*, а разовый запрос владельца — POST /api/web/search,
   и он сознательно НЕ вынесен сюда кнопкой: страница отвечает на вопрос
   «откуда у тебя это», а не заменяет собой поисковик.

   Главное правило страницы то же, что у операторского канала: ни одной
   цифры без источника. Всё, чего в ответе ручки нет, подписано «нет
   данных», а не показано нулём — ноль читается как факт.
   ============================================================ */

import { api } from '../api.js';
import { h, fmtDateShort, fmtNum } from '../components.js';
import { pageHead, panel, stat, tag, blank, errorNote, pill } from './_ui.js';

const NO_DATA = 'нет данных';

/* Пусто — честное «нет данных», а не подставленный ноль: ноль читается как
   факт («собрано ноль»), а отсутствие ответа ручки фактом не является.
   Возвращается СТРОКА: stat() из _ui.js делает String(value), и узел там
   превратился бы в «[object HTMLElement]». */
function val(value) {
  const empty = value === null || value === undefined || value === '';
  return empty ? NO_DATA : String(value);
}

function readinessTone(code) {
  if (code === 'ready_general') return 'ok';
  if (code === 'ready_keyless') return 'accent';
  if (code === 'disabled' || code === 'osiris_disabled') return 'idle';
  return 'warn';
}

function backendRow(b) {
  const tone = b.ready ? 'ok' : (b.needs_key ? 'warn' : 'idle');
  const why = b.ready ? '' : (b.reason || '');
  return h('div.mini-row.column', { 'data-backend': String(b.id || '') },
    h('div.row.tight',
      h('b', String(b.id || '?')),
      pill(b.ready ? 'готов' : 'не опрашивается', { tone }),
      b.general_web ? tag('открытый веб', { accent: true }) : null,
      b.keyless ? tag('без ключа') : tag('нужен ключ')),
    /* honest_capability — одна строка про то, что источник РЕАЛЬНО умеет.
       Она здесь не украшение: без неё «нашлось в Википедии» и «нашлось в
       интернете» для владельца выглядят одинаково. */
    h('div.dim.small', String(b.honest_capability || NO_DATA)),
    why ? h('div.dim-2.small', why) : null,
    h('div.row.tight.dim-2.small',
      h('span', String(b.base_url || NO_DATA)),
      h('span', `лицензия: ${b.license || NO_DATA}`),
      h('span', `проверка живьём: ${b.live_status || NO_DATA}`)));
}

function episodeRow(ep) {
  const subject = String(ep.subject || ep.id || '');
  return h('div.mini-row.column', { 'data-episode': subject },
    h('div.row.tight',
      h('b', subject.replace(/^web:/, '') || NO_DATA),
      ep.pages ? tag(`страниц: ${ep.pages}`) : null,
      ep.quotes ? tag(`цитат: ${ep.quotes}`) : null),
    h('div.row.tight.dim-2.small',
      h('span', ep.first_seen ? fmtDateShort(ep.first_seen) : NO_DATA),
      h('span', String(ep.source_id || ''))));
}

const WebResearchPage = {
  id: 'web_research',
  title: 'Поиск в интернете',
  icon: 'browser',
  nav: 'more',

  async render(ctx) {
    let state = null; let sources = null; let episodes = null; let err = null;
    try {
      /* Три запроса разом и по отдельности: упавшая ручка превращается в
         «нет данных» на своей панели, а не роняет всю страницу. */
      const [s, src, ep] = await Promise.allSettled([
        api.raw('/api/web'),
        api.raw('/api/web/sources'),
        api.raw('/api/web/episodes?limit=20'),
      ]);
      state = s.status === 'fulfilled' ? s.value : null;
      sources = src.status === 'fulfilled' ? src.value : null;
      episodes = ep.status === 'fulfilled' ? ep.value : null;
      if (!state) err = s.reason;
    } catch (e) { err = e; }

    const head = pageHead('Поиск в интернете',
      'Что система умеет искать прямо сейчас, куда она за этим ходит и что уже прочитала. '
      + 'Страница только показывает: искать умеет модель инструментами web.*');

    if (err) return h('div.bx-page', head, errorNote(err, () => ctx.refresh()));

    const ready = (state && state.readiness) || {};
    const enabled = Boolean(state && state.enabled);

    /* Текст готовности берётся у сервера ДОСЛОВНО и не пересказывается:
       владельцу и модели полагается один и тот же текст, а собственная
       формулировка здесь означала бы вторую правду. */
    const readyPanel = panel('Что доступно сейчас',
      h('div.stack.sm',
        h('div.row.tight',
          pill(enabled ? 'включено' : 'выключено',
               { tone: readinessTone(ready.code) }),
          ready.general_web ? tag('открытый веб доступен', { accent: true })
                            : tag('открытого веба нет')),
        h('p', String(ready.text || NO_DATA)),
        Array.isArray(ready.recommendations) && ready.recommendations.length
          ? h('ul.stack.sm', ready.recommendations.map((r) => h('li', String(r))))
          : null,
        h('div.row',
          stat('источников без ключа', val(ready.keyless_ready)),
          stat('страница, знаков', val(ready.page_chars)),
          stat('ссылок со страницы', val(ready.page_links)))),
      { icon: 'browser' });

    const counts = (state && state.counts) || {};
    const budget = (state && state.budget) || {};
    const daily = budget.daily || {};
    const countPanel = panel('Сколько собрано',
      h('div.row',
        stat('эпизодов', val(counts.subjects)),
        stat('наблюдений', val(counts.observations)),
        stat('файлов сырья', val(counts.raw_records)),
        stat('за сутки', val(daily.used === undefined
          ? '' : `${fmtNum(daily.used)} из ${fmtNum(daily.limit)}`))),
      { icon: 'system' });

    const backends = (sources && Array.isArray(sources.backends)) ? sources.backends : [];
    const sourcesPanel = panel('Куда система ходит',
      backends.length
        ? h('div.mini-list', backends.map(backendRow))
        : blank({ title: 'Источники не читаются',
                  hint: 'ручка /api/web/sources не ответила — здесь было бы неправдой '
                        + 'показать пустой список как «источников нет»' }),
      { icon: 'plugins' });

    const eps = (episodes && Array.isArray(episodes.episodes)) ? episodes.episodes : [];
    const trailPanel = panel('Что уже прочитано',
      eps.length
        ? h('div.mini-list', eps.map(episodeRow))
        : blank({ title: 'Пока ничего не искали',
                  hint: enabled
                    ? 'когда модель что-нибудь найдёт, здесь появится цепочка: запрос → выдача → страница → цитата'
                    : 'режим выключен, поэтому и следа нет' }),
      { icon: 'history' });

    return h('div.bx-page', head, readyPanel, countPanel, sourcesPanel, trailPanel);
  },

  onEvent(ev) {
    /* Перерисовываемся только на своих событиях. Метрики системы и служебные
       ws.* сюда не относятся: перерисовка на каждый чужой удар — это и есть
       «страница мигает». */
    const kind = String((ev && ev.kind) || '');
    return kind.startsWith('web.') || kind.startsWith('osiris.');
  },
};

export default WebResearchPage;
