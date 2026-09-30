/* ============================================================
   chat.js — вход в настольный чат Bossman из Command Center.

   Сам чат живёт на отдельной странице /chat.html (тот же сервер, те же
   задачи, память и подтверждения). Здесь — только недавние треды
   (GET /api/chat/threads) ссылками и кнопка-ссылка «Открыть чат».
   Треды — это те же сессии, что у `bossman chat` в терминале.
   ============================================================ */

import { api, listOf } from '../api.js';
import { h, icon, fmtClock } from '../components.js';
import { pageHead, panel, errorNote } from './_ui.js';

function threadLine(t) {
  const title = t.title || 'Новый чат';
  return h('div.log-line',
    h('span.log-ts', fmtClock(t.updated_at || t.created_at)),
    h('span.log-msg',
      h('a', { href: `/chat.html#t=${encodeURIComponent(t.id)}`, title: `Открыть «${title}» в чате` }, title),
      t.surface === 'cli' ? h('span.small.dim', ' · начат в терминале') : null,
      t.project ? h('span.small.dim', ` · ${t.project}`) : null));
}

const ChatPage = {
  id: 'chat',
  title: 'Чат',
  icon: 'tasks',
  nav: 'primary',
  section: 'main',

  async render(ctx) {
    let items = [];
    let err = null;
    try {
      items = listOf(await api.raw('/api/chat/threads?limit=12'), 'items');
    } catch (e) {
      err = e;
    }
    const open = h('a', { class: 'bx-btn bx-btn-primary', href: '/chat.html', style: { textDecoration: 'none' } },
      icon('tasks', 14), h('span', 'Открыть чат'));
    const head = pageHead('Чат Bossman',
      'Отдельное окно диалога: живой ответ, шаги, подтверждения и STOP. Тот же Bossman, что здесь и в CMD.',
      { actions: [open] });
    const recent = err
      ? errorNote(err, () => ctx.refresh())
      : panel(`Недавние чаты · ${items.length}`,
        items.length
          ? h('div.log', items.map(threadLine))
          : h('div.log-empty', 'Чатов пока нет. Откройте чат и напишите первую задачу.'));
    const how = panel('Как открыть', h('div.small',
      h('p', 'В этом окне: ссылка «Открыть чат» выше.'),
      h('p', 'Отдельным окном: ', h('code', 'bcc-desktop --chat'),
        ' — только когда окно BOSSMAN закрыто (у них один профиль браузера); пока оно открыто, команда покажет адрес чата.'),
      h('p', 'Из терминала тот же разговор продолжается командой ', h('code', 'bossman chat'), '.')));
    return h('div.bx-page', head, recent, how);
  },
};

export default ChatPage;
