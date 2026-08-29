/* ============================================================
   router.js — Feature 02: Smart Model Router.
   Endpoints: GET/PATCH /api/router/rules, POST /api/router/preview,
   GET /api/router/explain?task_id=.
   ============================================================ */

import { api } from '../api.js';
import {
  h, icon, badge, toast, toastOk, toastError,
  actionButton, field, input, checkbox, textarea, fmtNum,
} from '../components.js';
import { mono } from './_shared.js';
import { panel, pageHead, errorNote } from './_ui.js';

const RouterPage = {
  id: 'router',
  title: 'Выбор модели',
  icon: 'retry',
  nav: 'more',

  async render(ctx) {
    let rules = null; let err = null;
    try { rules = await api.raw('/api/router/rules'); }
    catch (e) { err = e; }

    if (err) return h('div.bx-page', pageHead('Выбор модели', 'Как BOSSMAN решает, какой модели отдать задачу'), errorNote(err, () => ctx.refresh()));

    const requires = (rules && rules.requires) || {};
    const roleScores = (rules && rules.role_scores) || {};

    const preferLocal = h('div');
    const syncPrefer = () => {
      preferLocal.textContent = '';
      preferLocal.appendChild(checkbox('Предпочитать локальные модели при прочих равных', !!rules.prefer_local, {
        onChange: async (e) => {
          try {
            rules = await api.raw('/api/router/rules', { method: 'PATCH', body: { prefer_local: e.target.checked } });
            toastOk('Правило обновлено');
          } catch (err2) { toastError(err2, 'Не удалось сохранить'); e.target.checked = !e.target.checked; }
        },
      }));
    };
    syncPrefer();

    const requiresPanel = panel('Что модель должна уметь для разных задач',
      h('div.stack.sm',
        Object.keys(requires).length
          ? Object.entries(requires).map(([kind, caps]) => h('div.row.tight',
            h('span.badge.mono', kind), h('span.small.dim', '→'),
            (caps || []).length ? caps.map((c) => badge(c)) : h('span.xsmall.dim', 'без требований')))
          : h('div.small.dim', 'Требований нет — подойдёт любая модель.'),
        h('div.xsmall.dim', { style: { marginTop: '4px' } }, 'Менять — в блоке для продвинутых ниже.')));

    const roleScoresPanel = Object.keys(roleScores).length
      ? panel('Оценки моделей по ролям', h('div.stack.sm',
        Object.entries(roleScores).map(([alias, scores]) => h('div.row.tight',
          h('span.badge.mono', alias),
          ...Object.entries(scores || {}).map(([kind, v]) => badge(`${kind}: ${fmtNum(v, 2)}`))))))
      : null;

    const rulesJson = textarea({ rows: 10, class: 'textarea mono', value: JSON.stringify(rules, null, 2) });
    const rawPanel = panel('Все правила целиком · для продвинутых',
      h('div.stack.sm',
        rulesJson,
        h('div.row', h('div.spacer'),
          actionButton('Сохранить', async () => {
            let parsed;
            try { parsed = JSON.parse(rulesJson.value); }
            catch { toast('Невалидный JSON', { type: 'warn' }); return; }
            try {
              rules = await api.raw('/api/router/rules', { method: 'PATCH', body: parsed });
              toastOk('Правила обновлены');
              ctx.refresh();
            } catch (e) { toastError(e, 'Не удалось сохранить правила'); }
          }, { cls: 'btn btn-primary btn-sm', iconName: 'check' }))));

    const preview = buildPreviewPanel();
    const explain = buildExplainPanel();

    return h('div.bx-page',
      pageHead('Выбор модели', 'Как BOSSMAN решает, какой модели отдать задачу: требования, предпросмотр выбора и объяснение.'),
      h('div.bx-row', requiresPanel, panel('Предпочтения', preferLocal)),
      roleScoresPanel,
      h('div.bx-row', preview, explain),
      rawPanel);
  },
};

function buildPreviewPanel() {
  const typeEl = input({ placeholder: 'coding', value: 'generic' });
  const cloudEl = h('input', { type: 'checkbox', checked: true });
  const ctxEl = input({ type: 'number', min: '0', value: '0', class: 'input mono' });
  const priceEl = input({ type: 'number', min: '0', step: '0.1', placeholder: 'без лимита', class: 'input mono' });
  const memEl = input({ type: 'number', min: '0', placeholder: 'без лимита', class: 'input mono' });
  const out = h('div.small.dim', 'Заполните форму и нажмите «Проверить».');

  const run = async () => {
    out.textContent = '';
    out.appendChild(h('div.small.dim', 'Считаю…'));
    try {
      const r = await api.raw('/api/router/preview', {
        method: 'POST',
        body: {
          task_type: typeEl.value.trim() || 'generic',
          cloud_allowed: cloudEl.checked,
          min_context: Number(ctxEl.value) || 0,
          max_price_out: priceEl.value ? Number(priceEl.value) : null,
          available_memory_mb: memEl.value ? Number(memEl.value) : null,
        },
      });
      out.textContent = '';
      out.appendChild(h('div.stack.sm',
        h('div.row', h('span.small', 'Выбор:'), h('div.spacer'),
          r.selected ? h('span.badge.badge-ok.mono', r.selected) : h('span.badge.badge-warn', 'никто не подошёл')),
        r.selected ? h('div.xsmall.dim', `оценка ${fmtScore(r.score)}`) : null,
        r.reasons && r.reasons.length
          ? h('ul.small', { style: { margin: 0, paddingLeft: '18px' } }, r.reasons.map((x) => h('li', x))) : null,
        r.rejected && Object.keys(r.rejected).length
          ? h('div.stack.sm', h('div.xsmall.dim', 'Отклонены:'),
            Object.entries(r.rejected).map(([alias, reasons]) => h('div.xsmall.dim',
              h('span.mono', alias), ': ', (reasons || []).join('; '))))
          : null));
    } catch (e) {
      out.textContent = '';
      out.appendChild(h('div.small', { style: { color: 'var(--err)' } }, e.message || 'Ошибка предпросмотра'));
    }
  };

  return panel('Проверить, кого выберет', h('div.stack.sm',
    h('div.grid.cols-2',
      field('Тип задачи', typeEl),
      field('Минимальный размер контекста', ctxEl)),
    h('div.grid.cols-2',
      field('Макс. цена ответа, $ за 1М', priceEl),
      field('Свободно памяти, МБ', memEl)),
    h('label.check', cloudEl, h('span', 'Можно использовать облако')),
    h('div.row', h('div.spacer'), actionButton('Проверить', run, { cls: 'btn btn-primary btn-sm', iconName: 'search' })),
    out));
}

function fmtScore(v) { const n = Number(v); return Number.isFinite(n) ? n.toFixed(1) : '—'; }

function buildExplainPanel() {
  const taskEl = input({ type: 'number', min: '1', placeholder: 'ID задачи', class: 'input mono' });
  const out = h('div.small.dim', 'Укажите ID задачи с хотя бы одним прогоном.');

  const run = async () => {
    if (!taskEl.value) { toast('Укажите ID задачи', { type: 'warn' }); return; }
    out.textContent = '';
    out.appendChild(h('div.small.dim', 'Загрузка…'));
    try {
      const r = await api.raw(`/api/router/explain?task_id=${encodeURIComponent(taskEl.value)}`);
      const route = r.route || {};
      out.textContent = '';
      out.appendChild(h('div.stack.sm',
        h('div.row', h('span.small', 'Модель:'), h('div.spacer'), mono(r.model_alias || route.alias || '—')),
        route.reasons && route.reasons.length
          ? h('ul.small', { style: { margin: 0, paddingLeft: '18px' } }, route.reasons.map((x) => h('li', x))) : null,
        !route.reasons ? h('div.xsmall.dim', 'Роутер не выбирал модель для этого прогона (использована модель агента).') : null));
    } catch (e) {
      out.textContent = '';
      out.appendChild(h('div.small', { style: { color: 'var(--err)' } }, e.message || 'Не найдено'));
    }
  };

  return panel('Почему выбрали эту модель', h('div.stack.sm',
    field('Номер задачи', taskEl),
    h('div.row', h('div.spacer'), actionButton('Показать', run, { cls: 'btn btn-sm', iconName: 'info' })),
    out));
}

export default RouterPage;
