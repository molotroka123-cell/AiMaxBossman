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
import { h, toastOk, toastError, fmtRelative, fmtCost, fmtDateTime,
  openModal } from '../components.js';
import { panel, pageHead, pill, errorNote, blank, btn, stat, tag, codeBlock, field,
  segmented } from './_ui.js';

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
  section: 'work',

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
      pageHead('Цели', HEAD_SUB, {
        pills,
        actions: [btn('Новая цель', () => openWizard(ctx), { variant: 'primary', size: 'sm' })],
      }),
      autonomyNote(status),
      items.length
        ? h('div.bx-grid', items.map((it) => objectiveCard(it, ctx)))
        : panel(null, blank({
          action: btn('Новая цель', () => openWizard(ctx), { variant: 'primary' }),
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
      actions(it, ctx),
      detailTabs(it))));
}

/* ------------------------------------------------------------------ вкладки

   «Улики» и «Ревизии» грузятся ТОЛЬКО по требованию: на странице с двадцатью
   целями предзагрузка обеих вкладок — это сорок запросов ради вкладки, которую
   никто не открыл. Ответ каждой вкладки показывается как есть, включая честное
   «улики нет» и «часть прежних редакций не сохранена». */

function detailTabs(it) {
  const body = h('div.stack.sm');
  let current = '';
  const load = async (tab) => {
    if (current === tab) { current = ''; body.replaceChildren(); return; }
    current = tab;
    body.replaceChildren(h('div.bx-muted.sm', 'Загружаю…'));
    try {
      const data = await api.raw(
        `/api/objectives/${encodeURIComponent(it.objective_id)}/${tab}`);
      body.replaceChildren(tab === 'evidence' ? evidenceTab(data) : revisionsTab(data));
    } catch (e) {
      // Ошибка загрузки — это ошибка, а не «ничего нет».
      body.replaceChildren(errorNote(e, () => { current = ''; load(tab); }));
    }
  };
  return h('div.stack.sm',
    segmented([{ value: 'evidence', label: 'Улики' }, { value: 'revisions', label: 'Ревизии' }],
      '', (tab) => load(tab)),
    body);
}

function evidenceTab(d) {
  const usage = d.usage || {};
  return h('div.stack.sm',
    h('div.row.wrap',
      stat('Состояние', d.condition),
      stat('Подтверждено', d.verified ? 'да' : 'нет'),
      stat('Наблюдений', String(usage.observations ?? 0)),
      stat('Миссий', String(usage.missions ?? 0)),
      stat('Потрачено', fmtCost(usage.cost_usd ?? 0))),
    d.last_verified_evidence_ref
      ? h('div.row', h('span.bx-muted', 'Ссылка на улику:'), codeBlock(d.last_verified_evidence_ref))
      : null,
    h('div.bx-note' + (d.verified ? '' : '.is-warn'), d.note),
    (d.open_reservations || []).length
      ? h('div.stack.sm',
        h('div.bx-note.is-warn', 'Незакрытые резервы: исход прошлой работы неизвестен. '
          + 'Это неопределённость после падения, а не разрешение повторить эффект.'),
        ...d.open_reservations.map((r) => codeBlock(r.reservation_id)))
      : null,
    (d.journal || []).length
      ? h('div.stack.sm', h('span.bx-muted', 'Журнал:'),
        ...d.journal.slice(-12).reverse().map((row) => h('div.row.sm',
          h('span.bx-muted', fmtDateTime(row.at * 1000)), tag(row.event),
          h('span', String(row.detail || '')))))
      : h('div.bx-muted.sm', 'В журнале пока пусто.'));
}

function revisionsTab(d) {
  const cur = d.current || {};
  const sum = cur.summary || {};
  return h('div.stack.sm',
    h('div.row.wrap', stat('Ревизия', String(cur.revision)),
      stat('Полномочия', (sum.capabilities || []).join(', ') || 'нет'),
      stat('Потолок миссий', String((sum.budget || {}).max_missions ?? '—'))),
    h('div.row', h('span.bx-muted', 'Цифра спецификации:'), codeBlock(cur.spec_digest || '')),
    d.note ? h('div.bx-note.is-warn', d.note) : null,
    (d.superseded || []).length
      ? h('div.stack.sm', h('span.bx-muted', 'Прежние редакции:'),
        ...d.superseded.map((row) => h('div.row.sm',
          tag(`ревизия ${row.revision}`),
          h('span.bx-muted', row.superseded_at ? fmtDateTime(row.superseded_at * 1000) : ''),
          codeBlock(row.spec_digest))))
      : h('div.bx-muted.sm', 'Прежних редакций не сохранено.'),
    (d.revision_events || []).length
      ? h('div.stack.sm', h('span.bx-muted', 'События ревизий:'),
        ...d.revision_events.map((e) => h('div.row.sm',
          h('span.bx-muted', fmtDateTime(e.at * 1000)), h('span', String(e.detail || '')))))
      : h('div.bx-muted.sm', 'Цель ни разу не пересматривалась.'));
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

/* ---------------------------------------------------------------- мастер

   Мастер собирает СПЕЦИФИКАЦИЮ, а не «настройки»: условие успеха, ритм,
   область, бюджет, приватность и полномочия — это поля, на которые владелец
   даёт согласие поимённо. Последний шаг — предпросмотр: сервер возвращает
   ровно то, что получит цель, вместе с цифрой спецификации, по которой потом
   можно сверить, что согласовано было именно это.

   Созданная цель попадает в DRAFT и ничего не наблюдает: подписка на источники
   и включение остаются отдельными явными действиями. */

const WIZARD_STEPS = ['Условие', 'Ритм и область', 'Бюджет и права', 'Согласие'];

export const OBJECTIVE_DRAFT = {
  objective_id: '', scope_id: 'project',
  source_ref: 'local-build', field: 'passed', expected: 'true',
  trigger: 'source_change', cooldown_seconds: 300, max_age_seconds: 900,
  expires_in_days: 30, priority: 3,
  max_missions: 5, max_observations: 50, max_wall_seconds: 600, max_cost_usd: 2,
  permission_refs: '', conflict_keys: '', private_only: true,
};

/**
 * Число из поля формы.
 *
 * Пустая строка — НЕ ноль: `Number('')` равен нулю и проходит isFinite, поэтому
 * очищенное поле «Потолок времени» молча превращалось в бюджет 0 с. Пустое поле
 * означает «оставить значение по умолчанию», которое и подставляется. А вот
 * явно набранный 0 сохраняется как 0: его отвергнет сервер и назовёт поле —
 * это разные намерения, и различать их обязан клиент.
 */
const num = (value, fallback) => {
  if (value === null || value === undefined || String(value).trim() === '') return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};
const list = (value) => String(value || '').split(',').map((x) => x.trim()).filter(Boolean);

/**
 * Собрать спецификацию цели из черновика мастера.
 *
 * Вынесено из мастера и экспортировано намеренно: это единственная часть формы
 * с настоящей логикой (числа, списки, срок, форма предиката), и именно её
 * отвергнет сервер, если она неверна. Функция чистая — проверяется без DOM.
 * `nowMs` передаётся, чтобы срок годности не зависел от часов в тесте.
 */
export function buildObjectiveSpec(draft, ownerId, nowMs) {
  return {
    schema_version: 1,
    owner_id: ownerId || 'owner',
    scope_id: String(draft.scope_id || '').trim() || 'project',
    objective_id: String(draft.objective_id || '').trim(),
    revision: 1,
    previous_digest: null,
    predicates: [{
      predicate_id: 'success',
      source_ref: String(draft.source_ref || '').trim(),
      field: String(draft.field || '').trim(),
      value_type: 'boolean',
      operator: 'eq',
      // Условие успеха — булево значение, а не строка 'true' из формы.
      expected: draft.expected === 'true' || draft.expected === true,
    }],
    sources: [{
      source_ref: String(draft.source_ref || '').trim(),
      source_revision: 'v1',
      max_age_seconds: num(draft.max_age_seconds, 900),
    }],
    // Цель без срока — автономия без конца, поэтому срок обязателен и считается
    // от переданного момента, а не от часов внутри функции.
    expires_at: Math.floor(nowMs / 1000) + num(draft.expires_in_days, 30) * 86400,
    priority: num(draft.priority, 3),
    allowed_triggers: [draft.trigger],
    permission_refs: list(draft.permission_refs),
    conflict_keys: list(draft.conflict_keys),
    cooldown_seconds: num(draft.cooldown_seconds, 300),
    limits: {
      max_observations: num(draft.max_observations, 50),
      max_missions: num(draft.max_missions, 5),
      max_wall_seconds: num(draft.max_wall_seconds, 600),
      max_cost_usd: num(draft.max_cost_usd, 2),
    },
    stop_conditions: ['owner-revocation'],
  };
}

function openWizard(ctx) {
  const draft = { ...OBJECTIVE_DRAFT };
  let step = 0;
  let preview = null;

  // Общий модал приложения, а не свой оверлей: он уже умеет Escape, клик мимо,
  // scrim, фокус и стек модалов. Второй такой механизм разошёлся бы с первым.
  const body = h('div.stack');
  const handle = openModal({ title: 'Новая цель', body, wide: true });
  const close = () => handle.close();

  const buildSpec = () => buildObjectiveSpec(
    draft, ctx.ownerId || (ctx.session && ctx.session.owner_id), Date.now());

  const text = (key, label, note, attrs = {}) => field(label,
    h('input.bx-input', {
      value: String(draft[key]), ...attrs,
      oninput: (e) => { draft[key] = e.target.value; },
    }), note);

  function render() {
    const steps = h('div.row.wrap', ...WIZARD_STEPS.map((name, i) => pill(
      `${i + 1}. ${name}`, { tone: i === step ? 'ok' : 'idle' })));

    let pane;
    if (step === 0) {
      pane = h('div.stack.sm',
        text('objective_id', 'Идентификатор цели',
          'Короткое стабильное имя. По нему цель видна в журнале и в уликах.'),
        text('source_ref', 'Источник',
          'Откуда берутся наблюдения. Цель не подписывается на него сама — подписка отдельным действием.'),
        text('field', 'Поле наблюдения', 'Какое поле источника проверяется.'),
        field('Условие успеха', segmented(
          [{ value: 'true', label: 'должно быть true' }, { value: 'false', label: 'должно быть false' }],
          draft.expected, (v) => { draft.expected = v; }),
        'Условие держится, пока это остаётся верным по ПРОВЕРЕННОМУ наблюдению.'));
    } else if (step === 1) {
      pane = h('div.stack.sm',
        field('Чем будится', segmented(
          [{ value: 'source_change', label: 'изменение источника' },
            { value: 'scheduled', label: 'по расписанию' }],
          draft.trigger, (v) => { draft.trigger = v; }),
        'Иных поводов у цели не будет: список триггеров закрытый.'),
        text('cooldown_seconds', 'Пауза между предложениями, с',
          'Ниже этого цель не будет предлагать работу повторно.'),
        text('max_age_seconds', 'Предельный возраст наблюдения, с',
          'Более старое наблюдение не считается свежим и не даёт основания действовать.'),
        text('scope_id', 'Область', 'Границы, внутри которых цель вообще может что-то делать.'),
        text('expires_in_days', 'Истекает через, дней',
          'Цель без срока — это автономия без конца. Срок обязателен.'));
    } else if (step === 2) {
      pane = h('div.stack.sm',
        text('max_missions', 'Потолок миссий'),
        text('max_observations', 'Потолок наблюдений'),
        text('max_wall_seconds', 'Потолок времени, с'),
        text('max_cost_usd', 'Потолок расхода, $',
          'Жёсткий предел. Цель не может его поднять — только владелец новой ревизией.'),
        text('permission_refs', 'Полномочия (через запятую)',
          'Поимённо. Пустой список означает, что цель не вправе ничего, кроме наблюдения.'),
        text('conflict_keys', 'Ключи конфликта (через запятую)',
          'Области, которые цель занимает эксклюзивно, чтобы две цели не правили одно и то же.'),
        field('Приватность', segmented(
          [{ value: 'private', label: 'только локально' }, { value: 'any', label: 'разрешить облако' }],
          draft.private_only ? 'private' : 'any', (v) => { draft.private_only = v === 'private'; }),
        'Локальный режим не отправляет данные цели наружу.'));
    } else {
      pane = preview
        ? h('div.stack.sm',
          h('div.bx-note', 'Согласие даётся на это. После создания цель попадает в '
            + 'DRAFT и ничего не наблюдает, пока вы не подпишете источники и не включите её.'),
          h('div.row.wrap',
            stat('Цель', preview.objective_id), stat('Область', preview.scope_id),
            stat('Приоритет', String(preview.priority))),
          h('div.row', h('span.bx-muted', 'Полномочия:'),
            ...(preview.capabilities.length
              ? preview.capabilities.map((c) => tag(c, { accent: true }))
              : [h('span', 'нет — только наблюдение')])),
          h('div.row.wrap',
            stat('Миссий', String(preview.budget.max_missions)),
            stat('Наблюдений', String(preview.budget.max_observations)),
            stat('Времени, с', String(preview.budget.max_wall_seconds)),
            stat('Расход', fmtCost(preview.budget.max_cost_usd))),
          h('div.row.wrap', h('span.bx-muted', 'Ритм:'),
            tag(preview.cadence.allowed_triggers.join(', ')),
            tag(`пауза ${preview.cadence.cooldown_seconds} с`)),
          h('div.row', h('span.bx-muted', 'Цифра спецификации:'), codeBlock(preview.spec_digest)))
        : h('div.bx-muted.sm', 'Готовлю предпросмотр…');
    }

    const nav = h('div.row',
      step > 0 ? btn('Назад', () => { step -= 1; render(); }) : null,
      h('div.bx-spacer'),
      btn('Отмена', close),
      step < WIZARD_STEPS.length - 1
        ? btn('Далее', async () => {
          step += 1;
          if (step === WIZARD_STEPS.length - 1) {
            preview = null; render();
            try {
              const r = await api.raw('/api/objectives/preview',
                { method: 'POST', body: { spec: buildSpec() } });
              preview = r.preview;
            } catch (e) {
              // Причина отказа принадлежит владельцу: какое поле не годится.
              toastError(e, 'Спецификация не принята');
              step -= 1;
            }
          }
          render();
        }, { variant: 'primary' })
        : btn('Создать цель', async () => {
          try {
            await api.raw('/api/objectives', { method: 'POST', body: { spec: buildSpec() } });
            toastOk('Цель создана черновиком');
            close();
            ctx.refresh();
          } catch (e) { toastError(e, 'Не удалось создать цель'); }
        }, { variant: 'primary', disabled: !preview }));

    body.replaceChildren(steps, pane, nav);
  }

  render();
  return handle;
}

export default ObjectivesPage;
