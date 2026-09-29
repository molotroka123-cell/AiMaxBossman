/* ============================================================
   jeff_settings.js — «Настройки Jeff» (Bossman Command v0.1).
   Endpoints (bcc/features/jeff_settings.py, под /api с обычной auth/CSRF):
     GET/PUT /api/jeff-settings, GET/PUT/DELETE /api/jeff-settings/users/{key},
     POST /api/jeff-settings/reset.
   Меняется только манера речи Jeff (стиль/тон) и лимит расходов (только вниз).
   Участники перечисляются по имени/нейтральной метке, Telegram ID здесь нет.
   ============================================================ */

import { api } from '../api.js';
import { h, toastOk, toastError, actionButton, field, textarea, select, input } from '../components.js';
import { panel, pageHead, pill } from './_ui.js';

const PRESET_ORDER = ['stock', 'bold', 'warm', 'brief'];

function scaleEditor(data, initial, prefix, { perUser = false } = {}) {
  /* initial: {name: value} для всех 8 шкал; stock — «настройка не задана».
     Для участника «Обычный» = явные стоковые значения (иначе он унаследовал бы общий стиль). */
  const state = { values: { ...initial.values }, stock: initial.stock };
  const [lo, hi] = data.scale_range || [0, 10];
  const mark = h('span.small.dim');
  const sliders = {};
  const showMark = () => {
    mark.textContent = state.stock
      ? (perUser ? 'Сейчас: как для всех участников' : 'Сейчас: обычный Jeff (без настройки стиля)')
      : 'Сейчас: своя настройка стиля';
  };
  const rows = data.scale_names.map((name) => {
    const out = h('span.badge.mono', String(state.values[name]));
    const range = h('input', {
      type: 'range', min: String(lo), max: String(hi), step: '1', value: String(state.values[name]),
      name: `${prefix}-scale-${name}`, 'aria-label': data.scale_labels[name] || name,
      style: { width: '100%' },
    });
    range.addEventListener('input', () => {
      state.values[name] = Number(range.value);
      out.textContent = range.value;
      state.stock = false;
      showMark();
    });
    sliders[name] = { range, out };
    return h('div', { style: { display: 'grid', gridTemplateColumns: '150px 1fr 40px', gap: '10px', alignItems: 'center' } },
      h('div', h('div', data.scale_labels[name] || name), h('div.xsmall.dim', data.scale_hints[name] || '')),
      range, out);
  });
  const setAll = (values, stock) => {
    for (const name of data.scale_names) {
      state.values[name] = values[name];
      sliders[name].range.value = String(values[name]);
      sliders[name].out.textContent = String(values[name]);
    }
    state.stock = stock;
    showMark();
  };
  const presets = h('div.row.tight', { style: { flexWrap: 'wrap', gap: '6px' } },
    PRESET_ORDER.filter((id) => id in data.presets).map((id) => h('button.btn.btn-sm', {
      type: 'button', dataset: { preset: id, scope: prefix },
      onClick: () => {
        const p = data.presets[id];
        if (!p || !Object.keys(p).length) setAll(data.stock_scales, !perUser);
        else setAll({ ...data.stock_scales, ...p }, false);
      },
    }, data.preset_labels[id] || id)));
  showMark();
  return {
    node: h('div.stack.sm', field('Пресет', presets), mark, ...rows),
    scales: () => (state.stock ? {} : { ...state.values }),
    setAll,
  };
}

function valuesFrom(data, scales) {
  const has = scales && Object.keys(scales).length > 0;
  return { values: { ...data.stock_scales, ...(scales || {}) }, stock: !has };
}

const JeffSettingsPage = {
  id: 'jeff-settings',
  title: 'Настройки Jeff',
  icon: 'agents',
  nav: 'more',
  section: 'brains',

  async render(ctx) {
    const head = pageHead('Настройки Jeff',
      'Как Jeff разговаривает с участниками. Меняется только стиль и тон — не права, не доступы и не память. Действует со следующего ответа, без перезапуска.');
    let data;
    try { data = await api.raw('/api/jeff-settings'); } catch (e) {
      return h('div.bx-page', head, panel('Не удалось загрузить', h('div.small', e.message || String(e))));
    }
    const s = data.settings;
    const status = h('div.row.tight', { style: { flexWrap: 'wrap', gap: '8px' } },
      data.valid ? pill(data.exists ? 'Настройки применяются' : 'Обычный Jeff', { tone: 'ok' })
        : pill('Файл настроек повреждён — Jeff работает как обычно', { tone: 'err' }),
      data.jeff_configured ? null : pill('Jeff на этой машине ещё не настроен', { tone: 'warn' }),
      h('span.xsmall.dim.mono', data.path));
    const invalidNote = data.valid ? null
      : h('div.small', { style: { color: 'var(--err)' } }, `Ошибка: ${data.error}. «Сохранить» запишет новые настройки, а старый файл отложит в копию .bak.`);

    /* ---- все участники ---- */
    const defaults = scaleEditor(data, valuesFrom(data, s.defaults.behavior_scales), 'js');
    const extra = textarea({ rows: 3, name: 'js-extra', maxlength: String(data.system_extra_max),
      placeholder: 'Например: «Отвечай с лёгкой иронией, без канцелярита». Только стиль — права и доступы этим не выдаются.' });
    extra.value = s.defaults.system_extra || '';
    const saveDefaults = actionButton('Сохранить', async () => {
      try {
        await api.raw('/api/jeff-settings', { method: 'PUT', body: {
          defaults: { behavior_scales: defaults.scales(), system_extra: extra.value },
          budgets: s.budgets } });
        toastOk('Настройки Jeff сохранены', 'Действуют со следующего ответа');
        ctx.refresh();
      } catch (e) { toastError(e); }
    }, { cls: 'btn btn-primary', iconName: 'check' });
    const resetAll = actionButton('Откат к обычному', async () => {
      try {
        await api.raw('/api/jeff-settings/reset', { method: 'POST', body: {} });
        toastOk('Jeff снова обычный', 'Стиль и настройки участников сброшены; лимиты сохранены');
        ctx.refresh();
      } catch (e) { toastError(e); }
    }, { cls: 'btn', iconName: 'retry' });

    /* ---- отдельный участник ---- */
    const people = data.participants || [];
    const userSel = select([{ value: '', label: people.length ? 'выберите участника' : 'участников пока нет' },
      ...people.map((p) => ({ value: p.key, label: `${p.label}${p.has_override ? ' · своя настройка' : ''}` }))],
    { name: 'js-user' });
    const userBox = h('div.stack.sm');
    const renderUser = () => {
      userBox.textContent = '';
      const key = userSel.value;
      if (!key) { userBox.appendChild(h('div.small.dim', 'Выберите участника, чтобы задать ему отдельный стиль. Остальных это не затронет.')); return; }
      const own = (s.users || {})[key];
      const base = own && Object.keys(own.behavior_scales || {}).length
        ? { ...s.defaults.behavior_scales, ...own.behavior_scales } : s.defaults.behavior_scales;
      const ed = scaleEditor(data, valuesFrom(data, base), 'js-user', { perUser: true });
      const uExtra = textarea({ rows: 2, name: 'js-user-extra', maxlength: String(data.system_extra_max),
        placeholder: 'Пусто — как для всех' });
      uExtra.value = own && typeof own.system_extra === 'string' ? own.system_extra : '';
      userBox.append(ed.node, field('Дополнительно о стиле для этого участника', uExtra),
        h('div.row.tight', { style: { gap: '8px' } },
          actionButton('Сохранить для участника', async () => {
            try {
              const body = { behavior_scales: ed.scales() };
              if (uExtra.value.trim()) body.system_extra = uExtra.value;
              await api.raw(`/api/jeff-settings/users/${encodeURIComponent(key)}`, { method: 'PUT', body });
              toastOk('Настройка участника сохранена');
              ctx.refresh();
            } catch (e) { toastError(e); }
          }, { cls: 'btn btn-primary', iconName: 'check' }),
          own ? actionButton('Убрать настройку участника', async () => {
            try {
              await api.raw(`/api/jeff-settings/users/${encodeURIComponent(key)}`, { method: 'DELETE' });
              toastOk('Участник снова на общих настройках');
              ctx.refresh();
            } catch (e) { toastError(e); }
          }, { cls: 'btn', iconName: 'retry' }) : null));
    };
    userSel.addEventListener('change', renderUser);
    renderUser();

    /* ---- лимиты ---- */
    const perDay = input({ type: 'number', min: '0', step: '0.1', name: 'js-usd-day', value: String(s.budgets.usd_per_day) });
    const perJob = input({ type: 'number', min: '0', step: '0.1', name: 'js-usd-job', value: String(s.budgets.usd_per_job) });
    const eff = data.budget.effective;
    const saveBudget = actionButton('Сохранить лимиты', async () => {
      try {
        await api.raw('/api/jeff-settings', { method: 'PUT', body: {
          defaults: { behavior_scales: s.defaults.behavior_scales, system_extra: s.defaults.system_extra || '' },
          budgets: { usd_per_day: Number(perDay.value), usd_per_job: Number(perJob.value) } } });
        toastOk('Лимиты сохранены');
        ctx.refresh();
      } catch (e) { toastError(e); }
    }, { cls: 'btn', iconName: 'check' });

    return h('div.bx-page', { dataset: { testid: 'jeff-settings' } }, head,
      panel('Состояние', h('div.stack.sm', status, invalidNote)),
      panel('Для всех участников', h('div.stack.sm', defaults.node,
        field('Дополнительно о стиле (необязательно)', extra, `До ${data.system_extra_max} символов. Только манера речи.`),
        h('div.row.tight', { style: { gap: '8px' } }, saveDefaults, resetAll))),
      panel('Отдельный участник', h('div.stack.sm', field('Участник', userSel), userBox)),
      panel('Лимит расходов', h('div.stack.sm',
        h('div.row.tight', { style: { gap: '12px', flexWrap: 'wrap' } },
          field('$ в день', perDay), field('$ на одну задачу', perJob)),
        h('div.small.dim', `Лимит может только понизить уже настроенный потолок, но не поднять его. Сейчас действует: $${eff.usd_per_day} в день, $${eff.usd_per_job} на задачу (Jeff отвечает только через бесплатные модели).`),
        h('div.row.tight', saveBudget))));
  },

  onEvent() { return false; },
};

export default JeffSettingsPage;
