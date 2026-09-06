/* ============================================================
   objectives.js — V5 Bossman Steward: рабочее место целей.
   Endpoints: GET /api/objectives, GET /api/objectives/{id},
              GET /api/objectives/status,
              POST /api/objectives/{id}/lifecycle, .../enrollment.

   Страница показывает ЦЕЛИ, а не «агентов, которые бесконечно думают».
   Зелёным горит только свежее проверенное SATISFIED с уликой; UNKNOWN
   показывается как UNKNOWN и никогда не выдаётся за «всё хорошо».
   ============================================================ */

import { api } from '../api.js';
import { h, toastOk, toastError, fmtRelative, fmtCost } from '../components.js';
import { panel, pageHead, pill, errorNote, blank, btn, stat, tag, codeBlock } from './_ui.js';

const LIFECYCLE_WORDS = {
  DRAFT: { word: 'черновик', tone: 'idle' },
  ACTIVE: { word: 'следит', tone: 'ok', live: true },
  PAUSED: { word: 'на паузе', tone: 'idle' },
  EXPIRED: { word: 'истекла', tone: 'idle' },
  REVOKED: { word: 'отозвана', tone: 'idle' },
};

const CONDITION_WORDS = {
  SATISFIED: { word: 'условие держится', tone: 'ok' },
  DEVIATED: { word: 'условие нарушено', tone: 'err' },
  UNKNOWN: { word: 'нет проверенных данных', tone: 'warn' },
};

const ObjectivesPage = {
  id: 'objectives',
  title: 'Цели',
  icon: 'target',
  nav: 'more',

  async render(ctx) {
    const [statusR, listR] = await Promise.allSettled([
      api.raw('/api/objectives/status'), api.raw('/api/objectives'),
    ]);
    if (listR.status === 'rejected') {
      return h('div.bx-page', pageHead('Цели', HEAD_SUB), errorNote(listR.reason, () => ctx.refresh()));
    }
    const status = statusR.status === 'fulfilled' ? statusR.value : null;
    const items = Array.isArray(listR.value) ? listR.value : [];

    const pills = [];
    if (status) {
      const c = status.counts || {};
      pills.push(pill('следят', { tone: c.active ? 'ok' : 'idle', value: c.active ?? 0 }));
      if (c.deviated) pills.push(pill('нарушено', { tone: 'err', value: c.deviated }));
      if (c.unknown) pills.push(pill('без данных', { tone: 'warn', value: c.unknown }));
    }

    return h('div.bx-page',
      pageHead('Цели', HEAD_SUB, { pills }),
      autonomyNote(status),
      items.length
        ? h('div.bx-grid', items.map((it) => objectiveCard(it, ctx)))
        : panel(null, blank({
          title: 'Стоячих целей пока нет',
          hint: 'Цель — это условие, которое BOSSMAN держит до отзыва или истечения. '
              + 'Новая цель создаётся черновиком и ничего не наблюдает, пока вы явно '
              + 'не подпишете её на источники и не включите.',
        })));
  },

  onEvent(ev) { return String(ev.kind || '').startsWith('objective.'); },
};

const HEAD_SUB = 'Условия, которые BOSSMAN держит сам: что именно должно оставаться верным, '
  + 'по каким проверенным данным это видно и что он вправе сделать при отклонении.';

/** Честная плашка про автономию: пока приёмный контур не принят, так и написано. */
function autonomyNote(status) {
  if (!status) return null;
  if (status.standing_autonomy_enabled) return null;
  return panel(null, h('div.bx-note',
    h('b', 'Постоянная автономия выключена. '),
    'Наблюдатели не запущены и миссии по целям не отправляются: это инспектор для чтения '
    + 'и ваших явных действий. Включение — отдельное принятое решение, а не переключатель здесь.'));
}

function objectiveCard(it, ctx) {
  const life = LIFECYCLE_WORDS[it.lifecycle] || { word: it.lifecycle, tone: 'idle' };
  const cond = CONDITION_WORDS[it.condition] || { word: it.condition, tone: 'warn' };
  const limits = it.spec && it.spec.limits;

  return h('section.bx-panel',
    h('div.bx-panel-head',
      h('h2', it.objective_id), h('div.bx-spacer'),
      pill(life.word, { tone: life.tone, live: !!life.live }),
      pill(cond.word, { tone: cond.tone })),
    h('div.bx-panel-body', h('div.stack.sm',
      h('div.row.wrap',
        stat('Проверено', it.last_observation_at ? fmtRelative(it.last_observation_at * 1000) : 'ни разу'),
        stat('Наблюдений', String(it.observations_used)),
        stat('Миссий', String(it.missions_used)),
        stat('Потрачено', fmtCost(it.cost_usd_used)),
        stat('Ревизия', String(it.revision))),
      evidenceLine(it),
      it.enrolled_sources.length
        ? h('div.row.wrap', h('span.bx-muted', 'Источники:'),
          ...it.enrolled_sources.map((s) => tag(s)))
        : h('div.bx-note', 'Ни один источник не подписан — цель ничего не наблюдает. '
          + 'Подписка всегда явная: ни один каталог или аккаунт не подключается сам.'),
      it.stopped ? h('div.bx-note.is-warn', 'Сработало условие остановки владельца.') : null,
      limits ? h('div.bx-muted.sm',
        `Потолки: ${limits.max_missions} миссий, ${limits.max_observations} наблюдений, `
        + `${limits.max_wall_seconds} с, ${fmtCost(limits.max_cost_usd)}`) : null,
      actions(it, ctx))));
}

/** SATISFIED без ссылки на улику — не «зелёный», а несогласованная запись. */
function evidenceLine(it) {
  if (it.condition === 'SATISFIED' && it.last_verified_evidence_ref) {
    return h('div.row', h('span.bx-muted', 'Улика:'), codeBlock(it.last_verified_evidence_ref));
  }
  if (it.condition === 'UNKNOWN') {
    return h('div.bx-note', 'Нет свежих проверенных наблюдений. Это не «всё хорошо» и не «сломано» — '
      + 'это отсутствие ответа, и по нему BOSSMAN ничего не делает.');
  }
  if (it.condition === 'DEVIATED') {
    return h('div.bx-note.is-err', 'Наблюдения показывают отклонение от условия. '
      + 'Предложение о работе — ещё не разрешение: полномочия и бюджет проверяются заново.');
  }
  return null;
}

function actions(it, ctx) {
  const row = h('div.row');
  const move = (lifecycle, label, variant) => btn(label, async () => {
    try {
      await api.raw(`/api/objectives/${encodeURIComponent(it.objective_id)}/lifecycle`, {
        method: 'POST', body: { lifecycle, version: it.version },
      });
      toastOk('Состояние цели обновлено');
      ctx.refresh();
    } catch (e) { toastError(e, 'Не удалось изменить состояние цели'); }
  }, { variant, size: 'sm' });

  if (it.lifecycle === 'ACTIVE') row.append(move('PAUSED', 'Пауза', 'secondary'));
  if (it.lifecycle === 'PAUSED' || it.lifecycle === 'DRAFT') {
    row.append(move('ACTIVE', 'Включить', 'primary'));
  }
  if (it.lifecycle !== 'REVOKED') {
    row.append(btn('Отозвать', async () => {
      // Отзыв необратим: истечение и новая ревизия его не воскрешают.
      if (!window.confirm('Отозвать цель? Это необратимо — вернуть её будет нельзя.')) return;
      try {
        await api.raw(`/api/objectives/${encodeURIComponent(it.objective_id)}/lifecycle`, {
          method: 'POST', body: { lifecycle: 'REVOKED', version: it.version },
        });
        toastOk('Цель отозвана');
        ctx.refresh();
      } catch (e) { toastError(e, 'Не удалось отозвать цель'); }
    }, { variant: 'danger', size: 'sm' }));
  }
  return row;
}

export default ObjectivesPage;
