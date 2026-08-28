/* ============================================================
   resources.js — Feature 12: Resource Brain.
   Endpoints: GET /api/resources, POST /api/resources/policy
   (+ POST /api/resources/estimate — используется опционально ниже).
   ============================================================ */

import { api } from '../api.js';
import {
  h, statusBadge, meter, toggle,
  toastOk, toastError, actionButton, field, input,
  fmtGb, fmtDateShort, fmtNum,
} from '../components.js';
import { panel, pageHead, errorBanner, emptyPanel } from './_shared.js';

const POLICIES = [
  { value: 'balanced', label: 'Balanced' },
  { value: 'performance', label: 'Performance' },
  { value: 'low_power', label: 'Low power' },
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

    if (err) return h('div.stack.lg', pageHead('Ресурсы', 'Resource Brain: RAM-бюджет для локальных моделей'), errorBanner(err, ctx));

    const reservations = Array.isArray(data.reservations) ? data.reservations : [];

    const sensors = panel('Память системы', h('div.stack.sm',
      meter('Использовано системой', data.used_mb || 0, data.total_mb || 1, `${fmtGb(data.used_mb)} / ${fmtGb(data.total_mb)} ГБ`),
      meter('Зарезервировано под задачи', data.reserved_mb || 0, data.total_mb || 1, `${fmtGb(data.reserved_mb)} ГБ`),
      h('div.row', h('span.small.dim', 'Доступно для новых задач'), h('div.spacer'),
        h('b', `${fmtGb(data.available_mb)} ГБ`)),
      h('div.xsmall.dim', `резервный минимум (reserve floor): ${fmtGb(data.reserve_floor_mb)} ГБ`)));

    const policyPanel = buildPolicyPanel(data, ctx);

    const resPanel = reservations.length
      ? panel(`Активные резервации (${reservations.length})`, h('div.mini-list',
        reservations.map((r) => h('div.mini-row',
          statusBadge(r.status || 'held'),
          h('span.name', `${r.holder_kind}:${r.holder_id}`),
          h('span.badge.mono', `${fmtNum(r.amount_mb)} MB`),
          h('span.xsmall.dim', fmtDateShort(r.created_at))))))
      : emptyPanel({ iconName: 'system', title: 'Резерваций нет', hint: 'Ресурсы резервируются автоматически, когда включён enforce (или задача помечена resource_managed).' });

    return h('div.stack.lg',
      pageHead('Ресурсы', 'Resource Brain: RAM-бюджет для локальных моделей'),
      h('div.grid.cols-2', sensors, policyPanel),
      resPanel);
  },

  onEvent(ev) { return ev.kind.startsWith('resource.'); },
};

function buildPolicyPanel(data, ctx) {
  const seg = h('div.seg', POLICIES.map((p) => h('button', {
    type: 'button', class: data.policy === p.value ? 'on' : '',
    onClick: async () => {
      try { await api.raw('/api/resources/policy', { method: 'POST', body: { policy: p.value } }); toastOk(`Политика: ${p.label}`); ctx.refresh(); }
      catch (e) { toastError(e, 'Не удалось сменить политику'); }
    },
  }, p.label)));

  const enforceRow = h('div.row',
    h('div', h('div.small', 'Enforce'), h('div.xsmall.dim', 'Блокировать запуск при нехватке памяти (иначе Resource Brain только наблюдает).')),
    h('div.spacer'),
    toggle(!!data.enforce, async (checked) => {
      try { await api.raw('/api/resources/policy', { method: 'POST', body: { enforce: checked } }); toastOk(checked ? 'Enforce включён' : 'Enforce выключен'); ctx.refresh(); }
      catch (e) { toastError(e, 'Не удалось изменить'); }
    }, 'Enforce'));

  const overrideEl = input({ type: 'number', min: '0', placeholder: 'авто (из метрик)', value: data.total_override_mb ? String(data.total_override_mb) : '', class: 'input mono' });
  const floorEl = input({ type: 'number', min: '0', value: String(data.reserve_floor_mb || 16000), class: 'input mono' });

  return panel('Политика', h('div.stack.sm',
    seg,
    enforceRow,
    h('div.grid.cols-2',
      field('Total override, MB', overrideEl, 'Переопределить общий объём памяти вместо значения из метрик.'),
      field('Reserve floor, MB', floorEl, 'Неприкосновенный минимум свободной памяти.')),
    h('div.row', h('div.spacer'),
      actionButton('Сохранить', async () => {
        try {
          await api.raw('/api/resources/policy', {
            method: 'POST',
            body: {
              total_override_mb: overrideEl.value ? Number(overrideEl.value) : null,
              reserve_floor_mb: Number(floorEl.value) || 0,
            },
          });
          toastOk('Политика сохранена');
          ctx.refresh();
        } catch (e) { toastError(e, 'Не удалось сохранить'); }
      }, { cls: 'btn btn-primary btn-sm', iconName: 'check' }))));
}

export default ResourcesPage;
