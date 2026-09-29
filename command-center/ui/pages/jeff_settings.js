/* ============================================================
   jeff_settings.js — «Настройки Jeff» (Bossman Command v0.1).
   Endpoints (bcc/features/jeff_settings.py, под /api с обычной auth/CSRF):
     GET/PUT /api/jeff-settings, GET/PUT/DELETE /api/jeff-settings/users/{key},
     POST /api/jeff-settings/reset.
   Jeff Admin (Bossman 1.9), тот же роутер: GET /api/jeff-settings/status,
     GET /api/jeff-settings/participants/{key}, PUT .../profile, POST .../clear|revoke|restore|pause-memory,
     DELETE .../facts/{id}. Ключ участника — псевдоним, а не Telegram ID.
   Меняется только манера речи Jeff (стиль/тон) и лимит расходов (только вниз).
   Участники перечисляются по имени/нейтральной метке, Telegram ID здесь нет.
   ============================================================ */

import { api } from '../api.js';
import { h, toastOk, toastError, actionButton, field, textarea, select, input, checkbox, confirmDialog } from '../components.js';
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

const CLEAR_SCOPES = [
  ['facts', 'Факты'], ['history', 'Сырую историю'], ['summaries', 'Сводки'],
  ['derived', 'Производные данные'], ['all', 'Всё об участнике'],
];

function list(text) { return String(text || '').split(/[\n,;]+/).map((t) => t.trim()).filter(Boolean); }

function statusPanel(st) {
  const r = st.readiness || {};
  const voice = (v) => (v && v.available ? pill('готов', { tone: 'ok' }) : pill('недоступен', { tone: 'warn' }));
  const budget = r.cloud_budget || {};
  const iso = st.isolation;
  const errors = (st.errors || []).map((e) => h('div.small', { style: { color: 'var(--err)' } },
    `${e.where}: ${e.error}${e.at ? ` · ${e.at}` : ''}`));
  const owner = (st.events && st.events.owner) || [];
  const replies = (st.events && st.events.replies) || [];
  return panel('Готовность и события', h('div.stack.sm', { dataset: { testid: 'jeff-status' } },
    h('div.row.tight', { style: { flexWrap: 'wrap', gap: '8px' } },
      r.ready ? pill('Jeff готов', { tone: 'ok' }) : pill('Jeff не готов', { tone: 'warn' }),
      pill(r.telegram_token_set ? 'Telegram: токен задан' : 'Telegram: токена нет', { tone: r.telegram_token_set ? 'ok' : 'idle' }),
      pill(`Окно Jeff: аккаунтов ${r.window_accounts ?? 0}`, { tone: 'idle' }),
      pill(`Участников: ${r.participants ?? 0}, закрыт доступ: ${r.revoked ?? 0}`, { tone: 'idle' })),
    h('div.row.tight', { style: { gap: '8px', flexWrap: 'wrap' } },
      h('span.small', 'Распознавание речи:'), voice(r.voice_asr),
      h('span.small', 'Озвучка:'), voice(r.voice_tts),
      h('span.small.dim', budget.used_today !== undefined ? `Облачных запросов сегодня: ${budget.used_today}/${budget.budget}` : '')),
    iso ? h('div.small', { dataset: { testid: 'jeff-isolation' } },
      (iso.ok ? 'Изоляция памяти: включена и не отключается. ' : 'Изоляция памяти: ПРОБЛЕМА. ') + iso.statement
      + (iso.problems && iso.problems.length ? ` (${iso.problems.join('; ')})` : '')) : null,
    errors.length ? h('div.stack.sm', h('div.small', 'Ошибки'), ...errors) : h('div.small.dim', 'Ошибок нет.'),
    h('div.small.dim', 'Последние действия владельца: ' + (owner.length
      ? owner.slice(-5).map((e) => `${e.at} ${e.action}`).join(' · ') : 'пока нет')),
    h('div.small.dim', 'Последние ответы Jeff (Telegram): ' + (replies.length
      ? replies.slice(-3).map((e) => e.at).join(' · ') : 'пока нет'))));
}

function participantAdmin(ctx, data) {
  const people = data.participants || [];
  const sel = select([{ value: '', label: people.length ? 'выберите участника' : 'участников пока нет' },
    ...people.map((p) => ({ value: p.key,
      label: `${p.display_name || p.label}${p.role_label ? ` · ${p.role_label}` : ''}${p.access === 'revoked' ? ' · доступ закрыт' : ''}` }))],
  { name: 'ja-user' });
  const box = h('div.stack.sm');
  const load = async () => {
    box.textContent = '';
    const key = sel.value;
    if (!key) { box.appendChild(h('div.small.dim', 'Выберите участника: профиль, память и данные каждого хранятся отдельно.')); return; }
    let d;
    try { d = await api.raw(`/api/jeff-settings/participants/${encodeURIComponent(key)}`); } catch (e) {
      box.appendChild(h('div.small', { style: { color: 'var(--err)' } }, e.message || String(e))); return;
    }
    const p = d.profile; const c = d.context;
    const base = `/api/jeff-settings/participants/${encodeURIComponent(key)}`;
    const name = input({ name: 'ja-name', value: p.display_name, maxlength: '40' });
    const role = input({ name: 'ja-role', value: p.role_label, maxlength: '40', placeholder: 'например: друг, клиент (только пометка, прав не даёт)' });
    const allowed = textarea({ rows: 2, name: 'ja-allowed', placeholder: 'через запятую; пусто — любые темы' });
    allowed.value = (p.allowed_topics || []).join(', ');
    const blocked = textarea({ rows: 2, name: 'ja-blocked', placeholder: 'через запятую' });
    blocked.value = (p.blocked_topics || []).join(', ');
    const lang = select((d.languages || []).map((l) => ({ value: l, label: { auto: 'как пишет собеседник', ru: 'русский', en: 'English' }[l] || l })),
      { name: 'ja-lang', value: p.language });
    const voice = checkbox('Отвечать голосом (локальная озвучка, если доступна)', p.voice_reply, { name: 'ja-voice' });
    const tg = checkbox('Разрешить Jeff в Telegram', p.telegram_enabled, { name: 'ja-telegram' });
    const save = actionButton('Сохранить профиль', async () => {
      try {
        await api.raw(`${base}/profile`, { method: 'PUT', body: {
          display_name: name.value, role_label: role.value, allowed_topics: list(allowed.value),
          blocked_topics: list(blocked.value), language: lang.value,
          voice_reply: voice.querySelector('input').checked, telegram_enabled: tg.querySelector('input').checked } });
        toastOk('Профиль участника сохранён', 'Действует со следующего ответа');
        ctx.refresh();
      } catch (e) { toastError(e); }
    }, { cls: 'btn btn-primary', iconName: 'check' });

    const facts = (c.facts || []).map((f) => h('div.row.tight', { style: { gap: '8px', alignItems: 'center' }, dataset: { fact: f.id } },
      pill(f.confirmed ? 'подтверждён' : f.evidence_kind, { tone: f.confirmed ? 'ok' : 'idle' }),
      h('span.small', `${f.category} · ${f.key}: ${f.value}`),
      actionButton('Удалить', async () => {
        try { await api.raw(`${base}/facts/${encodeURIComponent(f.id)}`, { method: 'DELETE' }); toastOk('Факт удалён'); load(); }
        catch (e) { toastError(e); }
      }, { cls: 'btn btn-sm' })));
    const consent = c.consent || {};
    const clears = CLEAR_SCOPES.map(([scope, label]) => actionButton(`Очистить: ${label}`, async () => {
      if (!await confirmDialog({ title: 'Очистить данные участника', danger: true, okText: 'Очистить',
        text: `${label}: только у этого участника, у остальных ничего не меняется.` })) return;
      try { await api.raw(`${base}/clear`, { method: 'POST', body: { scope } }); toastOk('Готово'); ctx.refresh(); }
      catch (e) { toastError(e); }
    }, { cls: 'btn btn-sm' }));
    const revoked = p.access === 'revoked';
    const access = revoked
      ? actionButton('Вернуть доступ', async () => {
        try { await api.raw(`${base}/restore`, { method: 'POST', body: {} }); toastOk('Доступ возвращён'); ctx.refresh(); }
        catch (e) { toastError(e); }
      }, { cls: 'btn', iconName: 'retry' })
      : actionButton('Закрыть доступ и стереть данные', async () => {
        if (!await confirmDialog({ title: 'Закрыть доступ участнику', danger: true, okText: 'Закрыть и стереть',
          text: 'Jeff перестанет отвечать этому человеку (Telegram и окно), а его память и история будут стёрты. Другие участники не затрагиваются.' })) return;
        try { await api.raw(`${base}/revoke`, { method: 'POST', body: { clear_data: true } }); toastOk('Доступ закрыт'); ctx.refresh(); }
        catch (e) { toastError(e); }
      }, { cls: 'btn btn-danger', iconName: 'retry' });

    box.append(
      h('div.small', revoked ? 'Доступ закрыт: Jeff не отвечает этому участнику.' : 'Доступ открыт.'),
      field('Имя', name), field('Роль (пометка)', role),
      field('Разрешённые темы', allowed, 'Если заполнено — Jeff обсуждает только их.'),
      field('Закрытые темы', blocked), field('Язык ответов', lang),
      voice, tg, h('div.row.tight', save),
      h('div.small', { dataset: { testid: 'ja-context' } },
        `Сохранённый контекст: фактов ${(c.facts || []).length} (подтверждённых ${c.confirmed_facts}), сырых записей ${c.raw_events}, сводок ${(c.summaries || []).length}.`),
      h('div.small.dim', `Память: ${consent.memory ? 'включена самим участником' : 'выключена'}; владелец может только выключить, включает участник.`),
      ...(facts.length ? facts : [h('div.small.dim', 'Сохранённых фактов нет.')]),
      h('div.row.tight', { style: { flexWrap: 'wrap', gap: '6px' } },
        actionButton('Поставить память на паузу', async () => {
          try { await api.raw(`${base}/pause-memory`, { method: 'POST', body: {} }); toastOk('Память участника на паузе'); load(); }
          catch (e) { toastError(e); }
        }, { cls: 'btn btn-sm' }), ...clears),
      h('div.row.tight', access));
  };
  sel.addEventListener('change', load);
  load();
  return panel('Участники и их профили', h('div.stack.sm', field('Участник', sel), box));
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

    let jeffStatus = null;
    try { jeffStatus = await api.raw('/api/jeff-settings/status'); } catch { jeffStatus = null; }

    return h('div.bx-page', { dataset: { testid: 'jeff-settings' } }, head,
      panel('Состояние', h('div.stack.sm', status, invalidNote)),
      jeffStatus ? statusPanel(jeffStatus) : null,
      participantAdmin(ctx, data),
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
