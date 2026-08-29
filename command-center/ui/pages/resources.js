/* ============================================================
   resources.js — Нагрузка на память: сколько её занято, сколько
   отдано под задачи и по какому правилу BOSSMAN её делит.
   Endpoints: GET /api/resources, POST /api/resources/policy.
   ============================================================ */

import { api } from '../api.js';
import {
  h, toggle, toastOk, toastError, input,
  fmtGb, fmtDateShort, fmtNum,
} from '../components.js';
import {
  pageHead, panel, meter, stat, tag, btn, blank, errorNote,
  segmented, field, statusPill,
} from './_ui.js';

const POLICIES = [
  { value: 'balanced', label: 'Поровну' },
  { value: 'performance', label: 'На скорость' },
  { value: 'low_power', label: 'Экономно' },
];

const ResourcesPage = {
  id: 'resources',
  title: 'Ресурсы',
  icon: 'system',
  nav: 'primary',

  async render(ctx) {
    let data = null; let err = null;
    try { data = await api.raw('/api/resources'); }
    catch (e) { err = e; }

    const head = pageHead('Нагрузка на память',
      'Сколько оперативной памяти занято и сколько её отдавать под задачи. BOSSMAN следит, чтобы серверу всегда хватало.');

    if (err) return h('div.bx-page', head, errorNote(err, () => ctx.refresh()));

    const reservations = Array.isArray(data.reservations) ? data.reservations : [];
    const availGb = fmtGb(data.available_mb);

    const memPanel = panel('Память сервера', h('div.stack.sm',
      meter('Занято прямо сейчас', data.used_mb || 0, data.total_mb || 1,
        `${fmtGb(data.used_mb)} / ${fmtGb(data.total_mb)} ГБ`, { accent: 'var(--bx-violet)' }),
      meter('Отдано под задачи', data.reserved_mb || 0, data.total_mb || 1,
        `${fmtGb(data.reserved_mb)} ГБ`, { accent: 'var(--bx-azure)' }),
      h('div', { style: { display: 'flex', gap: 'var(--bx-6)', marginTop: '6px' } },
        stat('Свободно для новых задач', `${availGb} ГБ`),
        stat('Неприкосновенный запас', `${fmtGb(data.reserve_floor_mb)} ГБ`))),
    { icon: 'system' });

    const policyPanel = buildPolicyPanel(data, ctx);

    const resPanel = reservations.length
      ? panel(`Что сейчас держит память · ${reservations.length}`,
        h('div.bx-list', reservations.map((r) => h('div.bx-list-row',
          h('div', { style: { minWidth: 0 } },
            h('div.bx-list-name', `${r.holder_kind}: ${r.holder_id}`),
            h('div.bx-list-note', fmtDateShort(r.created_at))),
          h('span.bx-list-end',
            tag(`${fmtNum(r.amount_mb)} МБ`),
            statusPill(r.status || 'held'))))))
      : blank({
        iconName: 'system',
        title: 'Память под задачи никто не держит',
        hint: 'Как только пойдут задачи, требующие памяти, они появятся здесь — и BOSSMAN проследит, чтобы её хватило.',
      });

    return h('div.bx-page', head,
      h('div.bx-row', memPanel, policyPanel),
      resPanel);
  },

  onEvent(ev) { return ev.kind.startsWith('resource.'); },
};

function buildPolicyPanel(data, ctx) {
  const seg = segmented(POLICIES, data.policy, async (value) => {
    try {
      await api.raw('/api/resources/policy', { method: 'POST', body: { policy: value } });
      toastOk('Правило обновлено');
      ctx.refresh();
    } catch (e) { toastError(e, 'Не удалось сменить правило'); }
  });

  const enforceRow = h('div', { style: { display: 'flex', alignItems: 'flex-start', gap: 'var(--bx-3)' } },
    h('div', { style: { flex: '1 1 auto', minWidth: 0 } },
      h('div', { style: { fontSize: '13.5px', color: 'var(--bx-ink)', fontWeight: 600 } },
        'Не запускать задачи, если памяти не хватает'),
      h('div', { style: { fontSize: '12px', color: 'var(--bx-ink-3)', marginTop: '2px', lineHeight: 1.45 } },
        'Если выключено — BOSSMAN только предупреждает, но всё равно запускает.')),
    toggle(!!data.enforce, async (checked) => {
      try {
        await api.raw('/api/resources/policy', { method: 'POST', body: { enforce: checked } });
        toastOk(checked ? 'Защита включена' : 'Защита выключена');
        ctx.refresh();
      } catch (e) { toastError(e, 'Не удалось изменить'); }
    }, 'Не запускать при нехватке памяти'));

  const overrideEl = input({ type: 'number', min: '0', placeholder: 'считать автоматически',
    value: data.total_override_mb ? String(data.total_override_mb) : '', class: 'input mono' });
  const floorEl = input({ type: 'number', min: '0', value: String(data.reserve_floor_mb || 16000), class: 'input mono' });

  return panel('Как делить память', h('div.stack.sm',
    seg,
    enforceRow,
    h('div.bx-form-grid',
      field('Всего памяти считать (МБ)', overrideEl, 'Оставьте пустым — BOSSMAN возьмёт реальное значение с сервера.'),
      field('Всегда держать свободным (МБ)', floorEl, 'Столько памяти BOSSMAN не отдаст ни одной задаче.')),
    h('div', { style: { display: 'flex', justifyContent: 'flex-end' } },
      btn('Сохранить', async () => {
        try {
          await api.raw('/api/resources/policy', {
            method: 'POST',
            body: {
              total_override_mb: overrideEl.value ? Number(overrideEl.value) : null,
              reserve_floor_mb: Number(floorEl.value) || 0,
            },
          });
          toastOk('Настройки сохранены');
          ctx.refresh();
        } catch (e) { toastError(e, 'Не удалось сохранить'); }
      }, { variant: 'primary', size: 'sm', iconName: 'check' }))),
  { icon: 'settings' });
}

export default ResourcesPage;
