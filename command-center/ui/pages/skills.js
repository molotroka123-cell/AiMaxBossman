/* ============================================================
   skills.js — Feature 10: Skill Library + Skill Forge + MCP Hub.
   Endpoints: GET/POST /api/skills, GET /api/skills/{id},
   POST /api/skills/{id}/clone|assign|run, GET /api/skills/{id}/export,
   POST /api/skills/import; GET /api/mcp/servers, POST /api/mcp/servers,
   DELETE /api/mcp/servers/{id}, GET /api/mcp/tools, POST /api/mcp/policy.
   ============================================================ */

import { api, listOf, pick } from '../api.js';
import {
  h, icon, badge, statusBadge, empty,
  toast, toastOk, toastError, openModal, confirmDialog, actionButton,
  field, input, textarea, select,
} from '../components.js';
import { idVal, errorBanner } from './_shared.js';
import * as ui from './_ui.js';

/* Совместимость: старые вызовы передают массив кнопок третьим аргументом. */
const pageHead = (title, sub, opts) =>
  ui.pageHead(title, sub, Array.isArray(opts) ? { actions: opts } : (opts || {}));
const emptyPanel = (opts) => ui.blank(opts);

const SKILL_TEMPLATE = `---
metadata:
  version: "1.0"
permissions: []
required_tools: []
input_schema:
  type: object
  properties:
    topic:
      type: string
      description: О чём задача
  required: [topic]
---

# Название скилла

Опиши шаг за шагом, что должен сделать агент, когда запускает этот скилл.
`;

const SkillsPage = {
  id: 'skills',
  title: 'Навыки',
  icon: 'edit',
  nav: 'primary',

  async render(ctx) {
    const [skillsR, serversR, toolsR] = await Promise.allSettled([
      api.raw('/api/skills'), api.raw('/api/mcp/servers'), api.raw('/api/mcp/tools'),
    ]);
    const skills = skillsR.status === 'fulfilled' ? listOf(skillsR.value, 'skills') : [];
    const servers = serversR.status === 'fulfilled' ? listOf(serversR.value, 'servers') : [];
    const tools = toolsR.status === 'fulfilled' ? listOf(toolsR.value, 'tools') : [];

    const head = pageHead('Навыки',
      skills.length ? `${skills.length} готовых наборов действий для агентов` : 'Готовые пошаговые процессы, которые агент выполняет по команде.', [
        h('button.btn', { type: 'button', onClick: () => openImportSkill(ctx) }, icon('search', 14), h('span', 'Загрузить')),
        h('button.btn.btn-primary', { type: 'button', onClick: () => openCreateSkill(ctx) }, icon('plus', 14), h('span', 'Новый навык')),
      ]);

    const body = skillsR.status === 'rejected'
      ? errorBanner(skillsR.reason, ctx)
      : skills.length
        ? h('div.grid.auto-lg', skills.map((s) => skillCard(s, ctx)))
        : h('section.panel', empty({
          iconName: 'edit',
          title: 'Навыков пока нет',
          hint: 'Навык — это готовый порядок действий с описанием, что подать на вход. Запуск навыка сразу ставит задачу агенту.',
          action: h('button.btn.btn-primary', { type: 'button', onClick: () => openCreateSkill(ctx) }, icon('plus', 14), h('span', 'Новый навык')),
        }));

    const mcpServersPanel = mcpServersSection(servers, serversR, ctx);
    const mcpToolsPanel = mcpToolsSection(tools, toolsR, ctx);

    return h('div.stack.lg',
      head, body,
      h('div.section-title', 'Подключённые инструменты'),
      mcpServersPanel, mcpToolsPanel);
  },

  onEvent(ev) { return ev.kind.startsWith('skill.') || ev.kind.startsWith('mcp.'); },
};

function skillCard(s, ctx) {
  const agents = (s.agents || []).length;
  return ui.tile({
    accent: 'var(--bx-azure)',
    iconName: 'bolt',
    title: s.name || s.id,
    sub: `${s.id} · v${s.version || '1.0'}`,
    statusNode: ui.tag(ui.plural(agents, 'агент', 'агента', 'агентов')),
    tags: (s.required_tools || []).map((t) => ui.tag(t)),
    body: [s.description ? h('div.xsmall.dim.wrap-any', s.description) : null],
    actions: [
      ui.btn('Запустить', () => openRunSkill(ctx, s), { variant: 'primary', size: 'sm', iconName: 'play' }),
      ui.btn('Копия', () => openCloneSkill(ctx, s), { size: 'sm', iconName: 'plus' }),
      ui.btn('Скачать', () => openExportSkill(s), { size: 'sm' }),
      ui.btn('Назначить', () => openAssignSkill(ctx, s), { size: 'sm' }),
    ],
  });
}

/* ---------------- Create / Import ---------------- */

function openCreateSkill(ctx) {
  skillEditorModal(ctx, { title: 'Новый навык', endpoint: '/api/skills', idValue: '', content: SKILL_TEMPLATE });
}
function openImportSkill(ctx) {
  skillEditorModal(ctx, { title: 'Загрузка навыка', endpoint: '/api/skills/import', idValue: '', content: '', importMode: true });
}

function skillEditorModal(ctx, { title, endpoint, idValue, content, importMode }) {
  const idEl = input({ placeholder: 'my-skill-id', value: idValue, class: 'input mono' });
  const contentEl = textarea({ rows: 16, class: 'textarea mono', value: content });
  const overwriteEl = h('input', { type: 'checkbox' });

  const modal = openModal({
    title, wide: true,
    body: h('div.stack',
      field('Короткое имя (id)', idEl, 'Латиница, цифры и дефис — это же имя папки навыка.'),
      field('Описание навыка', contentEl, 'Сверху — настройки навыка, ниже — пошаговый текст, что делать.'),
      h('label.check', overwriteEl, h('span', 'Заменить, если навык с таким именем уже есть'))),
    footer: (handle) => [
      h('div.spacer'),
      h('button.btn', { type: 'button', onClick: () => handle.close() }, 'Отмена'),
      actionButton(importMode ? 'Импортировать' : 'Создать', async () => {
        if (!idEl.value.trim()) { toast('Укажите имя навыка', { type: 'warn' }); idEl.focus(); return; }
        if (!contentEl.value.trim()) { toast('Содержимое не может быть пустым', { type: 'warn' }); return; }
        try {
          await api.raw(endpoint, { method: 'POST', body: { id: idEl.value.trim(), content: contentEl.value, overwrite: overwriteEl.checked } });
          handle.close();
          toastOk(importMode ? 'Навык загружен' : 'Навык создан');
          ctx.refresh();
        } catch (e) { toastError(e, 'Не удалось сохранить навык'); }
      }, { cls: 'btn btn-primary', iconName: 'check' }),
    ],
  });
}

function openCloneSkill(ctx, s) {
  const idEl = input({ value: `${s.id}-copy`, class: 'input mono' });
  const modal = openModal({
    title: `Сделать копию «${s.name || s.id}»`,
    body: field('Имя копии', idEl),
    footer: (handle) => [
      h('div.spacer'),
      h('button.btn', { type: 'button', onClick: () => handle.close() }, 'Отмена'),
      actionButton('Создать копию', async () => {
        if (!idEl.value.trim()) { toast('Укажите имя копии', { type: 'warn' }); return; }
        try {
          await api.raw(`/api/skills/${encodeURIComponent(s.id)}/clone`, { method: 'POST', body: { new_id: idEl.value.trim() } });
          handle.close();
          toastOk('Копия навыка создана');
          ctx.refresh();
        } catch (e) { toastError(e, 'Не удалось создать копию'); }
      }, { cls: 'btn btn-primary', iconName: 'check' }),
    ],
  });
}

async function openExportSkill(s) {
  const modal = openModal({ title: `Скачать · ${s.name || s.id}`, wide: true, body: h('div.small.dim', 'Загрузка…'), footer: h('div') });
  modal.footer.appendChild(h('div.spacer'));
  modal.footer.appendChild(h('button.btn', { type: 'button', onClick: () => modal.close() }, 'Закрыть'));
  try {
    const r = await api.raw(`/api/skills/${encodeURIComponent(s.id)}/export`);
    modal.body.textContent = '';
    const ta = textarea({ rows: 18, class: 'textarea mono', value: r.content || '', readonly: true });
    ta.addEventListener('focus', () => ta.select());
    modal.body.appendChild(h('div.stack.sm',
      h('div.xsmall.dim.mono', `отпечаток: ${r.fingerprint || '—'}`), ta));
  } catch (e) {
    modal.body.textContent = '';
    modal.body.appendChild(h('div.small', { style: { color: 'var(--err)' } }, e.message || 'Не удалось выгрузить'));
  }
}

async function openAssignSkill(ctx, s) {
  let agents = ctx.state.agents;
  if (!agents || !agents.length) { try { agents = listOf(await api.agents(), 'agents'); } catch { agents = []; } }
  if (!agents.length) { toast('Сначала создайте агента', { type: 'warn' }); return; }
  const agentEl = select(agents.map((a) => ({ value: pick(a, ['id']), label: pick(a, ['name'], 'без имени') })));
  const modal = openModal({
    title: `Кому доверить «${s.name || s.id}»`,
    body: field('Агент', agentEl),
    footer: (handle) => [
      h('div.spacer'),
      h('button.btn', { type: 'button', onClick: () => handle.close() }, 'Отмена'),
      actionButton('Назначить', async () => {
        try {
          await api.raw(`/api/skills/${encodeURIComponent(s.id)}/assign`, { method: 'POST', body: { agent_id: idVal(agentEl.value) } });
          handle.close();
          toastOk('Навык назначен агенту');
          ctx.refresh();
        } catch (e) { toastError(e, 'Не удалось назначить'); }
      }, { cls: 'btn btn-primary', iconName: 'check' }),
    ],
  });
}

async function openRunSkill(ctx, s) {
  const modal = openModal({ title: `Запуск · ${s.name || s.id}`, wide: true, body: h('div.small.dim', 'Загрузка…'), footer: h('div') });
  let detail;
  try { detail = await api.raw(`/api/skills/${encodeURIComponent(s.id)}`); }
  catch (e) {
    modal.body.textContent = '';
    modal.body.appendChild(h('div.small', { style: { color: 'var(--err)' } }, e.message || 'Не удалось загрузить навык'));
    return;
  }
  let agents = ctx.state.agents;
  if (!agents || !agents.length) { try { agents = listOf(await api.agents(), 'agents'); } catch { agents = []; } }

  const schema = (detail.frontmatter && detail.frontmatter.input_schema) || {};
  const props = schema.properties || {};
  const required = new Set(schema.required || []);
  const propNames = Object.keys(props);

  const agentEl = select(
    [{ value: '', label: agents.length ? '— не запускать, сохранить черновик —' : 'агентов нет' },
      ...agents.map((a) => ({ value: pick(a, ['id']), label: pick(a, ['name'], 'без имени') }))],
  );

  const inputEls = {};
  const formFields = propNames.length
    ? propNames.map((name) => {
      const p = props[name] || {};
      const el = input({ placeholder: p.description || '', value: '' });
      inputEls[name] = el;
      return field(`${name}${required.has(name) ? ' *' : ''}`, el, p.description || '');
    })
    : [h('div.small.dim', 'У навыка нет заданных полей — можно добавить свои ниже.')];

  const freeRows = h('div.stack.sm');
  const freePairs = [];
  function addFreeRow() {
    const keyEl = input({ placeholder: 'ключ' });
    const valEl = input({ placeholder: 'значение' });
    const row = h('div.row.tight', keyEl, valEl,
      h('button.btn.btn-sm.btn-ghost', { type: 'button', title: 'Убрать строку',
        'aria-label': 'Убрать строку', onClick: () => { row.remove(); } }, icon('trash', 12)));
    freePairs.push({ row, keyEl, valEl });
    freeRows.appendChild(row);
  }
  if (!propNames.length) addFreeRow();

  modal.body.textContent = '';
  modal.body.appendChild(h('div.stack',
    detail.process ? h('pre.block', String(detail.process).slice(0, 400)) : null,
    field('Кто выполнит', agentEl, 'Пусто — просто сохраним черновик задачи, без запуска.'),
    ...formFields,
    !propNames.length ? h('div.stack.sm', freeRows, h('button.btn.btn-sm', { type: 'button', onClick: () => addFreeRow() }, icon('plus', 12), h('span', 'Ещё поле'))) : null));

  modal.footer.textContent = '';
  modal.footer.appendChild(h('div.spacer'));
  modal.footer.appendChild(h('button.btn', { type: 'button', onClick: () => modal.close() }, 'Отмена'));
  modal.footer.appendChild(actionButton('Запустить', async () => {
    const inputData = {};
    if (propNames.length) {
      for (const name of propNames) inputData[name] = inputEls[name].value;
    } else {
      for (const p of freePairs) if (p.keyEl.value.trim()) inputData[p.keyEl.value.trim()] = p.valEl.value;
    }
    for (const req of required) {
      if (!inputData[req]) { toast(`Заполните обязательное поле «${req}»`, { type: 'warn' }); return; }
    }
    try {
      const r = await api.raw(`/api/skills/${encodeURIComponent(s.id)}/run`, {
        method: 'POST', body: { input: inputData, agent_id: idVal(agentEl.value) },
      });
      modal.close();
      toastOk(agentEl.value ? 'Навык запущен как задача' : 'Черновик задачи создан', `задача #${r.task_id}`);
      ctx.navigate('tasks', { task: r.task_id });
    } catch (e) { toastError(e, 'Не удалось запустить навык'); }
  }, { cls: 'btn btn-primary', iconName: 'play' }));
}

/* ---------------- MCP Hub ---------------- */

function mcpServersSection(servers, serversR, ctx) {
  const head = pageHead('Серверы инструментов', servers.length ? `${servers.length} подключено` : 'Ни одного не подключено', [
    h('button.btn.btn-sm', { type: 'button', onClick: () => openAddMcpServer(ctx) }, icon('plus', 13), h('span', 'Добавить сервер')),
  ]);
  const body = serversR.status === 'rejected'
    ? errorBanner(serversR.reason, ctx)
    : servers.length
      ? h('div.mini-list', servers.map((srv) => h('div.mini-row',
        statusBadge(srv.status || 'unknown'),
        h('div', { style: { flex: '1', minWidth: 0 } },
          h('div', h('b', srv.name), h('span.xsmall.dim', ` · ${srv.transport}`)),
          h('div.xsmall.dim.mono.truncate', srv.transport === 'http' ? (srv.url || '') : (srv.command || []).join(' '))),
        h('button.btn.btn-sm.btn-danger', {
          type: 'button', title: `Удалить сервер ${srv.name}`, 'aria-label': `Удалить сервер ${srv.name}`,
          onClick: async () => {
            const ok = await confirmDialog({ title: 'Удалить MCP-сервер?', text: srv.name, okText: 'Удалить', danger: true });
            if (!ok) return;
            try { await api.raw(`/api/mcp/servers/${encodeURIComponent(srv.id)}`, { method: 'DELETE' }); toastOk('Удалено'); ctx.refresh(); }
            catch (e) { toastError(e, 'Не удалось удалить'); }
          },
        }, icon('trash', 13)))))
      : h('section.panel', empty({ iconName: 'key', title: 'Серверов инструментов нет', hint: 'Подключите сервер, чтобы его инструменты стали доступны агентам. Для каждого можно выбрать: разрешать, спрашивать или запрещать.' }));
  return h('div.stack', head, body);
}

function openAddMcpServer(ctx) {
  const nameEl = input({ placeholder: 'filesystem' });
  const transportEl = select([{ value: 'stdio', label: 'stdio (command)' }, { value: 'http', label: 'http (url)' }], { value: 'stdio' });
  const cmdEl = input({ placeholder: 'npx -y @modelcontextprotocol/server-filesystem /data', class: 'input mono' });
  const urlEl = input({ placeholder: 'https://…', class: 'input mono' });
  const cmdField = field('Команда (stdio)', cmdEl, 'Через пробел — будет разбито на аргументы.');
  const urlField = field('URL (http)', urlEl);
  urlField.hidden = true;
  transportEl.addEventListener('change', () => {
    const isHttp = transportEl.value === 'http';
    cmdField.hidden = isHttp; urlField.hidden = !isHttp;
  });

  const modal = openModal({
    title: 'Добавить MCP-сервер',
    body: h('div.stack', field('Имя', nameEl), field('Транспорт', transportEl), cmdField, urlField),
    footer: (handle) => [
      h('div.spacer'),
      h('button.btn', { type: 'button', onClick: () => handle.close() }, 'Отмена'),
      actionButton('Добавить', async () => {
        if (!nameEl.value.trim()) { toast('Укажите имя', { type: 'warn' }); return; }
        const body = { name: nameEl.value.trim(), transport: transportEl.value };
        if (transportEl.value === 'stdio') body.command = cmdEl.value.trim().split(/\s+/).filter(Boolean);
        else body.url = urlEl.value.trim();
        try {
          await api.raw('/api/mcp/servers', { method: 'POST', body });
          handle.close();
          toastOk('Сервер добавлен');
          ctx.refresh();
        } catch (e) { toastError(e, 'Не удалось добавить сервер'); }
      }, { cls: 'btn btn-primary', iconName: 'check' }),
    ],
  });
}

function mcpToolsSection(tools, toolsR, ctx) {
  const head = h('div.section-title', 'Инструменты и права');
  const body = toolsR.status === 'rejected'
    ? errorBanner(toolsR.reason, ctx)
    : tools.length
      ? h('div.mini-list', tools.map((t) => h('div.mini-row',
        h('div', { style: { flex: '1', minWidth: 0 } },
          h('div.mono.small', t.canonical),
          t.description ? h('div.xsmall.dim.truncate', t.description) : null),
        select([{ value: 'auto', label: 'разрешать' }, { value: 'ask', label: 'спрашивать' }, { value: 'deny', label: 'запрещать' }], {
          value: t.policy || 'ask',
          onChange: async (e) => {
            try { await api.raw('/api/mcp/policy', { method: 'POST', body: { canonical: t.canonical, policy: e.target.value } }); toastOk('Право обновлено'); }
            catch (err) { toastError(err, 'Не удалось сохранить право'); }
          },
        }))))
      : emptyPanel({ iconName: 'key', title: 'Инструментов пока нет', hint: 'Они появятся, когда подключённый сервер сообщит, что умеет.' });
  return h('div.stack', head, body);
}

export default SkillsPage;
