/* ============================================================
   chat/sidebar.js — левая панель: знак, «Новый чат», поиск, треды по
   датам, проекты, архив, режим маршрута, настройки и тема.

   Треды — это те же сессии, что у `bossman chat` в терминале
   (GET /api/chat/threads): разговор, начатый в CMD, виден здесь, и наоборот.
   ============================================================ */

import { h, icon, iconButton, clear } from './dom.js';
import { sphere } from './sphere.js';
import { groupThreads, threadTimeLabel } from './format.js';

const IS_MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/i.test(navigator.platform || '');
export const MOD = IS_MAC ? '⌘' : 'Ctrl';

function kbd(text) { return h('kbd.kbd', text); }

function threadItem(t, { activeId, onOpen }) {
  const running = t.last && ['queued', 'running', 'paused', 'waiting_approval'].includes(t.last.status);
  const active = String(t.id) === String(activeId);
  return h('li',
    h('a.thread', {
      href: `#t=${encodeURIComponent(t.id)}`,
      'aria-current': active ? 'page' : null,
      title: t.title || 'Новый чат',
      dataset: { id: t.id, surface: t.surface || '' },
      onClick: (e) => { if (e.button === 0 && !e.ctrlKey && !e.metaKey) { e.preventDefault(); onOpen(t.id); } },
    },
    h('span.thread-title', t.title || 'Новый чат'),
    running ? h('span.thread-live', { title: 'Ход выполняется', 'aria-label': 'выполняется' }) : null,
    t.surface === 'cli' ? h('span.thread-cli', { title: 'Начат в терминале (bossman chat)' }, 'CMD') : null,
    h('span.thread-time', threadTimeLabel(t.updated_at || t.created_at)),
    t.pinned ? h('span.thread-pin', { title: 'Закреплён' }, icon('pin', 14)) : null));
}

/**
 * data: {threads, activeId, loading, error, filter:{q, project, archived},
 *  projects, collapsed:Set, route:{tone,title,sub}, theme, collapsedBar,
 *  handlers:{onNew, onSearch, onOpen, onToggleGroup, onProject, onArchive,
 *  onCollapse, onTheme, onHelp}}.
 */
export function renderSidebar(root, data) {
  clear(root);
  const hd = data.handlers;
  root.appendChild(h('div.sb-brand',
    sphere(34, data.brandState || 'idle', 'Bossman'),
    h('div.sb-brand-text', h('b', 'Bossman'), h('span', 'AI command center')),
    iconButton(data.collapsedBar ? 'expand' : 'collapse', data.collapsedBar ? 'Развернуть боковую панель' : 'Свернуть боковую панель',
      hd.onCollapse, { cls: 'sb-collapse', id: 'chat-sidebar-toggle' })));

  root.appendChild(h('button.sb-new', { type: 'button', id: 'chat-new', 'aria-label': `Новый чат (${MOD}+N)`, title: `Новый чат (${MOD}+N)`, onClick: hd.onNew },
    icon('plus', 18), h('span.sb-label', 'Новый чат'), h('span.sb-kbd', kbd(`${MOD} N`))));

  const search = h('input.sb-search-input', {
    id: 'chat-search', type: 'search', placeholder: 'Поиск чатов…', 'aria-label': `Поиск чатов (${MOD}+K)`,
    value: data.filter.q || '', autocomplete: 'off', spellcheck: 'false',
    onInput: (e) => hd.onSearch(e.target.value),
  });
  root.appendChild(h('label.sb-search', icon('search', 16), search, h('span.sb-kbd', kbd(`${MOD} K`))));

  const scroll = h('nav.sb-scroll', { 'aria-label': 'Чаты' });
  if (data.filter.project || data.filter.archived) {
    scroll.appendChild(h('div.sb-filter',
      icon(data.filter.archived ? 'archive' : 'folder', 15),
      h('span', data.filter.archived ? 'Архив' : `Проект: ${data.filter.project}`),
      iconButton('close', 'Показать все чаты', () => hd.onProject(null), { size: 14 })));
  }
  if (data.error) {
    scroll.appendChild(h('div.sb-empty.is-error', h('b', 'История чатов недоступна'), h('span', data.error)));
  } else if (data.loading && !data.threads.length) {
    scroll.appendChild(h('div.sb-empty', 'Загружаю чаты…'));
  } else if (!data.threads.length) {
    scroll.appendChild(h('div.sb-empty', data.filter.q ? 'Ничего не найдено.' : 'Чатов пока нет — начните новый.'));
  }
  for (const group of groupThreads(data.threads)) {
    const collapsed = data.collapsed.has(group.id);
    scroll.appendChild(h('div.sb-group',
      h('button.sb-group-head', { type: 'button', 'aria-expanded': collapsed ? 'false' : 'true',
        'aria-label': `${group.label}: ${collapsed ? 'развернуть' : 'свернуть'}`, onClick: () => hd.onToggleGroup(group.id) },
      icon(collapsed ? 'chevronRight' : 'chevronDown', 14), h('span', group.label), h('span.sb-count', String(group.items.length))),
      collapsed ? null : h('ul.sb-list', group.items.map((t) => threadItem(t, { activeId: data.activeId, onOpen: hd.onOpen })))));
  }

  const projCollapsed = data.collapsed.has('projects');
  const projects = data.projects || [];
  scroll.appendChild(h('div.sb-group.sb-projects',
    h('button.sb-group-head', { type: 'button', 'aria-expanded': projCollapsed ? 'false' : 'true',
      'aria-label': `Проекты: ${projCollapsed ? 'развернуть' : 'свернуть'}`, onClick: () => hd.onToggleGroup('projects') },
    icon(projCollapsed ? 'chevronRight' : 'chevronDown', 14), h('span', 'Проекты'), h('span.sb-count', String(projects.length))),
    projCollapsed ? null : h('ul.sb-list',
      projects.length ? projects.map((p) => h('li', h('button.sb-proj', {
        type: 'button', 'aria-pressed': data.filter.project === p.name ? 'true' : 'false',
        'aria-label': `Чаты проекта ${p.name}`, onClick: () => hd.onProject(data.filter.project === p.name ? null : p.name),
      }, icon('folder', 16), h('span.thread-title', p.name), h('span.thread-time', String(p.threads ?? ''))))) : h('li.sb-hint', 'Проект задаётся у чата: ⋯ → «Переместить в проект».'),
      h('li', h('button.sb-proj', { type: 'button', 'aria-pressed': data.filter.archived ? 'true' : 'false',
        'aria-label': 'Архив чатов', onClick: () => hd.onArchive(!data.filter.archived) },
      icon('archive', 16), h('span.thread-title', 'Архив'))))));
  root.appendChild(scroll);

  const r = data.route || {};
  root.appendChild(h('div.sb-route', { dataset: { tone: r.tone || 'auto' }, role: 'status', 'aria-label': `Режим: ${r.title || ''}. ${r.sub || ''}` },
    h('span.sb-route-dot', { 'aria-hidden': 'true' }),
    h('div.sb-route-text', h('b', r.title || ''), h('span', r.sub || ''))));

  root.appendChild(h('div.sb-foot',
    h('a.icon-btn', { href: '/#/settings', 'aria-label': 'Настройки Command Center', title: 'Настройки Command Center' }, icon('settings', 18)),
    h('a.icon-btn', { href: '/', 'aria-label': 'Открыть Command Center', title: 'Открыть Command Center' }, icon('home', 18)),
    iconButton('help', 'Горячие клавиши и справка', hd.onHelp, { id: 'chat-help' }),
    h('span.sb-spacer'),
    iconButton(data.theme === 'light' ? 'moon' : 'sun', data.theme === 'light' ? 'Тёмная тема' : 'Светлая тема', hd.onTheme, { id: 'chat-theme' })));
}
