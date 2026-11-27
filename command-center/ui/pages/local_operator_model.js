/* local_operator_model.js — pure logic of the «Локальные модели» page (no DOM, no imports; tested in node). */

export const CHAT_PREFIX = 'Локально · ';
export const PC_SUFFIX = ' · ПК';
export const HARNESS_NAME = 'Bossman Harness';

export function modelBadges(m) {
  const out = [];
  if (m.cloud) out.push({ text: 'облако — не локально', tone: 'warn' });
  if (!m.chat) out.push({ text: 'только embedding', tone: 'idle' });
  if (m.capabilities_source !== 'ollama') out.push({ text: 'возможности неизвестны', tone: 'idle' });
  if (m.tools) out.push({ text: 'инструменты', tone: 'ok' });
  if (m.vision) out.push({ text: 'зрение', tone: 'ok' });
  if (m.thinking) out.push({ text: 'рассуждение', tone: 'idle' });
  return out;
}

export function agentsFor(m, agents) {
  const by = new Map((agents || []).map((a) => [a.name, a]));
  const chat = by.get(CHAT_PREFIX + m.name) || null;
  const pc = by.get(CHAT_PREFIX + m.name + PC_SUFFIX) || null;
  return { chat, pc };
}

/* What the owner can do with this model right now, in words. */
export function readiness(m, agents) {
  if (m.cloud) return { state: 'skip', note: 'Облачный тег: запросы уходят в ollama.com — в локальный чат не добавляется.' };
  if (!m.chat) return { state: 'skip', note: 'Модель для поиска по смыслу (embedding), не для чата.' };
  const { chat, pc } = agentsFor(m, agents);
  if (!chat) return { state: 'not_synced', note: 'Не подключена. Нажмите «Взять модели из Ollama».' };
  if (pc) return { state: 'ready_pc', note: 'Готова: чат и управление компьютером (каждое действие — с вашим одобрением).', chat, pc };
  const why = m.tools ? 'агент «· ПК» ещё не создан' : 'модель не умеет инструменты — только чат';
  return { state: 'ready_chat', note: `Готова для чата; ${why}.`, chat, pc: null };
}

export function syncSummary(out) {
  if (!out || out.ok === false) {
    const why = (out && (out.error || out.hint)) || 'нет ответа';
    return `Не подключено: ${why}`;
  }
  const count = (list, status) => (list || []).filter((r) => r.status === status).length;
  const parts = [
    `моделей подключено: ${count(out.models, 'create')}`,
    `уже были: ${count(out.models, 'exists')}`,
    `агентов создано: ${count(out.agents, 'create')}`,
  ];
  const skipped = count(out.models, 'skipped');
  if (skipped) parts.push(`пропущено: ${skipped}`);
  const drift = count(out.agents, 'drift');
  if (drift) parts.push(`не тронуто (настроено вами иначе): ${drift}`);
  return 'Готово — ' + parts.join(', ') + '.';
}

export function humanSize(bytes) {
  if (!bytes) return '';
  const gb = bytes / 1e9;
  return gb >= 1 ? `${gb.toFixed(1)} ГБ` : `${Math.round(bytes / 1e6)} МБ`;
}

/* The chat window restores its picker from localStorage['bcc.chat.route'] ('agent:<id>' | 'auto'). */
export const CHAT_ROUTE_KEY = 'bcc.chat.route';

export function chatRoute(agent) {
  return agent && agent.id !== null && agent.id !== undefined ? `agent:${agent.id}` : 'auto';
}
