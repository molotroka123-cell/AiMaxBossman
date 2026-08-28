/* ============================================================
   orchestras.js — Feature 11: Natural Language Orchestration.
   Endpoints: POST /api/orchestras/parse, POST /api/orchestras/confirm,
   GET /api/orchestras (справочно, список уже созданных команд).
   ============================================================ */

import { api } from '../api.js';
import {
  h, icon, badge,
  toast, toastOk, toastError, actionButton, field, input, textarea,
} from '../components.js';
import { panel, pageHead, errorBanner, emptyPanel } from './_shared.js';

const OrchestrasPage = {
  id: 'orchestras',
  title: 'NL-оркестрация',
  icon: 'plus',
  nav: 'more',

  async render(ctx) {
    let orchestras = []; let err = null;
    try { const r = await api.raw('/api/orchestras'); orchestras = Array.isArray(r) ? r : []; }
    catch (e) { err = e; }

    const head = pageHead('NL-оркестрация', 'Опишите команду словами — конфигурация проверяется до создания');

    const nameEl = input({ placeholder: 'NL-команда', value: '' });
    const textEl = textarea({
      rows: 5,
      placeholder: 'Пример: Researcher главный, qwen-coder воркер, максимум 3 воркера, бюджет $5, всё опасное — подтверждать.',
    });
    const previewOut = h('div.small.dim', 'Опишите команду и нажмите «Разобрать».');
    let preview = null;

    async function doParse() {
      if (!textEl.value.trim()) { toast('Опишите команду', { type: 'warn' }); textEl.focus(); return; }
      previewOut.textContent = ''; previewOut.appendChild(h('div.small.dim', 'Разбираю…'));
      try {
        preview = await api.raw('/api/orchestras/parse', { method: 'POST', body: { text: textEl.value, name: nameEl.value.trim() || undefined } });
        renderPreview();
      } catch (e) { toastError(e, 'Не удалось разобрать текст'); }
    }

    function renderPreview() {
      previewOut.textContent = '';
      if (!preview) return;
      const o = preview.orchestra || {};
      const cfg = o.config || {};
      previewOut.appendChild(h('div.stack.sm',
        h('div.row',
          h('span.badge', preview.valid ? 'валидно' : 'есть проблемы'),
          h('div.spacer'), h('b', o.name || 'без имени'), h('span.xsmall.dim', ` · ${o.mode || 'manager'}`)),
        h('div.row.tight',
          cfg.max_workers ? badge(`до ${cfg.max_workers} воркеров`) : null,
          cfg.duration_hours ? badge(`${cfg.duration_hours} ч`) : null,
          cfg.cloud_budget_usd ? badge(`бюджет $${cfg.cloud_budget_usd}`) : null,
          cfg.approval_policy ? badge(`approvals: ${cfg.approval_policy}`, cfg.approval_policy === 'required' ? 'warn' : 'ok') : null),
        (preview.members || []).length
          ? h('div.mini-list', preview.members.map((m) => h('div.mini-row',
            h('span.badge', m.role),
            h('span.name', m.agent_name || m.model_alias || '—'),
            m.create_agent ? h('span.xsmall.dim', 'новый агент-обёртка') : null)))
          : h('div.small.dim', 'Участники не распознаны.'),
        (preview.created_agents || []).length
          ? h('div.xsmall.dim', `Будут созданы агенты-обёртки под модели: ${preview.created_agents.join(', ')}`) : null,
        (preview.warnings || []).length
          ? h('div.stack.sm', preview.warnings.map((w) => h('div.small', { style: { color: 'var(--warn)' } }, '⚠ ', w)))
          : null));
    }

    const confirmBtn = h('button.btn.btn-primary', {
      type: 'button',
      title: 'Сначала разберите текст в валидную конфигурацию',
      disabled: true,
      onClick: async () => {
        if (!preview || !preview.valid) return;
        try {
          const r = await api.raw('/api/orchestras/confirm', { method: 'POST', body: preview });
          toastOk('Оркестр создан', `#${r.orchestra_id} · ${r.members} участников`);
          preview = null; textEl.value = ''; renderPreview();
          confirmBtn.disabled = true;
          ctx.refresh();
        } catch (e) { toastError(e, 'Не удалось создать оркестр'); }
      },
    }, icon('check', 14), h('span', 'Создать'));

    const parseBtn = actionButton('Разобрать', async () => { await doParse(); confirmBtn.disabled = !(preview && preview.valid); },
      { cls: 'btn btn-sm', iconName: 'search' });

    const composer = panel('Описание команды', h('div.stack.sm',
      field('Название (необязательно)', nameEl),
      field('Текст', textEl, 'Ключевые слова: «главный/manager» — manager, «reviewer/проверяет» — reviewer, «воркер» — worker. Числа — лимиты и бюджет.'),
      h('div.row', h('div.spacer'), parseBtn),
      panel('Proposed configuration', previewOut),
      h('div.row', h('div.spacer'), confirmBtn)));

    const listBody = err
      ? errorBanner(err, ctx)
      : orchestras.length
        ? h('div.grid.auto-lg', orchestras.map(orchestraCard))
        : emptyPanel({ iconName: 'plus', title: 'Оркестров пока нет', hint: 'Создайте первый через форму выше.' });

    return h('div.stack.lg', head, composer, h('div.section-title', 'Существующие оркестры'), listBody);
  },

  onEvent(ev) { return ev.kind.startsWith('orchestra.'); },
};

function orchestraCard(o) {
  const cfg = o.config || {};
  return h('div.card',
    h('div.card-head',
      h('div', { style: { flex: '1', minWidth: 0 } },
        h('div.card-title', o.name), h('div.card-sub', `${o.mode || 'manager'} · ${(o.members || []).length} участников`)),
      cfg.max_workers ? badge(`до ${cfg.max_workers} воркеров`) : null),
    (o.members || []).length
      ? h('div.mini-list', o.members.map((m) => h('div.mini-row',
        h('span.badge', m.role), h('span.name', m.name || `агент #${m.agent_id}`))))
      : h('div.small.dim', 'Без участников.'));
}

export default OrchestrasPage;
