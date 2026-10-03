/* ============================================================
   chat/render.js — сообщения чата: пузырь владельца, ответ Bossman,
   карточки шагов, подтверждений, заметок и ошибок.

   Всё строится только из состояния хода (state.js), которое в свою очередь
   строится только из событий сервера. Текст модели проходит через
   markdown.js + domFactory (textContent), innerHTML не используется.
   Скрытые рассуждения модели сюда не попадают: state.js их отбрасывает.

   Живой текст патчится на месте (planTextRender): готовые блоки
   добавляются один раз, заново рисуется только хвост — выделение и кнопки
   «Копировать» в готовых блоках не пересоздаются на каждом кадре. Пока ход
   идёт, у строки ответа aria-busy="true", а строка состояния
   перестраивается только когда меняется её смысл (читалка экрана не
   получает одно и то же каждый кадр).
   ============================================================ */

import { h, icon, clear, domFactory } from './dom.js';
import { renderMarkdown, planTextRender } from './markdown.js';
import { sphere, setSphereState } from './sphere.js';
import { displaySegments, isTerminalStatus } from './state.js';
import { clockLabel, fmtBytes, fmtDuration, fmtTokens, localityBadge, costLabel, verdictLabel } from './format.js';

const RUNNING_WORDS = {
  queued: 'В очереди…',
  draft: 'Готовлю задачу…',
  running: 'Работаю…',
  paused: 'Пауза',
  waiting_approval: 'Жду вашего решения…',
};

export function renderMarkdownInto(el, text) {
  clear(el);
  for (const node of renderMarkdown(text, domFactory)) el.appendChild(node);
  return el;
}

/**
 * Текстовый сегмент ответа: замороженный префикс + хвост (P1/P2).
 * Состояние узла — el._md {frozenAt, frozenText, tail:[узлы хвоста], text, final}.
 */
export function updateTextSeg(el, seg) {
  const prev = el._md || null;
  if (prev && prev.text === seg.text && prev.final === seg.final) return el;
  const plan = planTextRender(prev, seg.text, seg.final);
  if (plan.reset) clear(el);
  else for (const node of prev.tail) node.remove();
  if (plan.append) for (const node of renderMarkdown(plan.append, domFactory)) el.appendChild(node);
  const tail = [];
  for (const node of renderMarkdown(plan.tail, domFactory)) { el.appendChild(node); tail.push(node); }
  el._md = { frozenAt: plan.frozenAt, frozenText: plan.frozenText, tail, text: seg.text, final: seg.final };
  el.dataset.final = seg.final ? '1' : '0';
  return el;
}

/* ---------------------------------------------------------------- владелец */

export function userRow(turn, initial = 'В') {
  const chips = (turn.attachments || []).map((a) => h('span.att-chip.is-static', { title: a.name },
    icon('file', 14), h('span.att-name', a.name), a.size ? h('span.att-size', fmtBytes(a.size)) : null));
  return h('div.msg.msg-user', { dataset: { turn: turn.localId } },
    h('span.avatar-user', { 'aria-hidden': 'true' }, initial),
    h('div.bubble-user',
      h('div.bubble-text', turn.text || ''),
      chips.length ? h('div.bubble-atts', chips) : null,
      turn.at ? h('time.bubble-time', { datetime: String(turn.at) }, clockLabel(turn.at)) : null));
}

/* ---------------------------------------------------------------- карточки */

function statusIcon(card, running) {
  if (card.ok === true) return h('span.step-status.is-ok', { title: 'Готово' }, icon('checkCircle', 20));
  if (card.ok === false) return h('span.step-status.is-fail', { title: 'Ошибка' }, icon('failCircle', 20));
  if (running) return h('span.step-status.is-run', { title: 'Выполняется' }, h('span.spin', { 'aria-hidden': 'true' }));
  return h('span.step-status.is-wait', { title: 'Нет результата' }, icon('clock', 20));
}

function detailsBlock(label, text) {
  if (!text) return null;
  return h('div.step-detail', h('div.step-detail-label', label), h('pre.step-pre', text));
}

export function toolCard(card, { running, expanded, onToggle }) {
  const dur = fmtDuration(card.durationMs);
  const title = card.tool || 'инструмент';
  const summary = card.ok === false && card.summary ? card.summary : (card.argsSummary || card.summary || '');
  const argsText = card.args ? JSON.stringify(card.args, null, 2).slice(0, 4000) : '';
  const body = expanded
    ? h('div.step-body',
      detailsBlock('Аргументы (без секретов)', argsText),
      detailsBlock('Итог', card.summary),
      detailsBlock(card.truncated ? 'Вывод (обрезан)' : 'Вывод', card.preview))
    : null;
  return h('div.step-card', { dataset: { state: card.ok === true ? 'ok' : card.ok === false ? 'fail' : running ? 'run' : 'wait' } },
    h('button.step-head', {
      type: 'button', 'aria-expanded': expanded ? 'true' : 'false',
      'aria-label': `${title}: ${expanded ? 'свернуть' : 'подробнее'}`, onClick: onToggle,
    },
    statusIcon(card, running),
    h('span.step-main',
      h('span.step-title', h('span.tool-chip', card.chip || 'Инструмент'), h('span.step-name', title)),
      summary ? h('span.step-sub', summary) : null),
    h('span.step-dur', dur || (card.ok === null || card.ok === undefined ? (running ? '…' : '') : '')),
    icon(expanded ? 'chevronDown' : 'chevronRight', 16, 'step-chev')),
    body);
}

export function approvalCard(a, { onDecide, busy, error }) {
  const pending = a.status === 'pending';
  const statusText = {
    approved: 'Разрешено', rejected: 'Отклонено', revoked: 'Отозвано', expired: 'Истекло', consumed: 'Использовано',
  }[a.status] || (pending ? 'Ждёт решения' : a.status);
  return h('div.approval-card', { dataset: { state: pending ? 'pending' : 'done' } },
    h('div.approval-head', icon('shield', 18), h('b', 'Нужно ваше решение'), h('span.approval-kind', a.kind || ''),
      h('span.approval-state', statusText)),
    a.preview ? h('pre.approval-preview', a.preview) : null,
    error ? h('div.approval-error', error) : null,
    pending ? h('div.approval-actions',
      h('button.btn.btn-primary', { type: 'button', disabled: busy, 'aria-label': `Разрешить действие ${a.kind || ''}`.trim(),
        onClick: () => onDecide(a, true) }, icon('check', 16), 'Разрешить'),
      h('button.btn.btn-ghost', { type: 'button', disabled: busy, 'aria-label': `Отклонить действие ${a.kind || ''}`.trim(),
        onClick: () => onDecide(a, false) }, icon('close', 16), 'Отклонить')) : null);
}

export function noteCard(n) {
  return h('div.note-card', { dataset: { tone: n.tone || 'info' } },
    icon(n.tone === 'error' ? 'failCircle' : 'warn', 16), h('b', n.title || ''), n.detail ? h('span', n.detail) : null);
}

/** Карточка ошибки: настоящая причина + подсказка + «Отправить ещё раз» (не «Повторить»). */
export function errorCard({ title, message, hint, code }, onResend) {
  return h('div.error-card', { role: 'alert' },
    h('div.error-head', icon('failCircle', 18), h('b', title || 'Ошибка'), code ? h('code.error-code', code) : null),
    message ? h('div.error-msg', message) : null,
    hint ? h('div.error-hint', hint) : null,
    onResend ? h('div.error-actions',
      h('button.btn.btn-ghost', { type: 'button', 'aria-label': 'Отправить это сообщение ещё раз', onClick: onResend },
        icon('send', 16), 'Отправить ещё раз')) : null);
}

/* ---------------------------------------------------------------- ответ Bossman */

export function assistantRow(turn) {
  const orb = sphere(30, 'thinking', 'Bossman');
  const row = h('div.msg.msg-bot', { dataset: { turn: turn.localId } },
    h('span.avatar-bot', orb),
    h('div.bot-body',
      h('div.bot-segs'),
      /* без aria-live: лента сообщений уже role=log, итог хода объявляет #chat-announce */
      h('div.bot-status'),
      h('div.bot-error'),
      h('div.bot-foot')));
  row._orb = orb;
  row._segNodes = new Map();
  return row;
}

export function turnErrorInfo(turn) {
  if (turn.sendError) return { title: 'Не удалось отправить', ...turn.sendError };
  if (turn.status === 'failed') return { title: 'Задача не выполнена', message: turn.error || 'Сервер не сообщил причину.', hint: 'Подробности — в задаче в Command Center.' };
  if (turn.status === 'blocked') {
    return { title: 'Задача не запущена', message: turn.blockReason || turn.error || 'Допуск к запуску не выдан.', code: turn.blockCode || '',
      hint: 'Проверьте модель агента и её доступность (Command Center → Модели).' };
  }
  if (turn.status === 'missing') return { title: 'Задача не найдена', message: 'Запись о задаче отсутствует в базе.', hint: '' };
  return null;
}

/**
 * Строка состояния хода и её подпись. Строка перестраивается только когда
 * меняется подпись (слово и шаг), а не на каждой дельте текста.
 */
export function statusLine(turn) {
  const live = !isTerminalStatus(turn.status) && !turn.sendError;
  const last = live && turn.progress.length ? turn.progress[turn.progress.length - 1] : null;
  const word = RUNNING_WORDS[turn.status] || 'Работаю…';
  const step = last && last.maxSteps ? `шаг ${last.step} из ${last.maxSteps}` : '';
  const sig = live ? `live|${word}|${step}` : turn.status === 'stopped' ? 'stopped' : '';
  /* ход стоит, пока владелец не решит: статус сервера или неотвеченное подтверждение */
  const waiting = live && (turn.status === 'waiting_approval'
    || [...turn.approvals.values()].some((a) => a.status === 'pending'));
  return { live, waiting, word, step, sig };
}

/** Что один раз сказать читалке экрана, когда ход закончился; '' — итога ещё нет. */
export function turnEndAnnouncement(turn) {
  const info = turnErrorInfo(turn);
  if (info) return `Ошибка: ${info.title}`;
  if (turn.status === 'completed') return 'Ответ готов';
  if (turn.status === 'stopped') return 'Остановлено';
  return '';
}

function segSignature(seg, ctx) {
  /* текст не пересобирается целиком: тот же узел патчится в updateTextSeg */
  if (seg.type === 'text') return `t|${seg.key}`;
  if (seg.type === 'tool') {
    const c = seg.card;
    return `x|${c.ok}|${c.durationMs}|${c.summary}|${ctx.expanded.has(seg.key) ? 1 : 0}|${ctx.running ? 1 : 0}`;
  }
  if (seg.type === 'approval') {
    const a = seg.approval;
    const ui = ctx.approvalUi.get(String(a.id)) || {};
    return `a|${a.status}|${ui.busy ? 1 : 0}|${ui.error || ''}`;
  }
  return `n|${seg.note.title}|${seg.note.detail}`;
}

function buildSeg(seg, ctx) {
  if (seg.type === 'text') return updateTextSeg(h('div.md', { dataset: { final: seg.final ? '1' : '0' } }), seg);
  if (seg.type === 'tool') {
    return toolCard(seg.card, { running: ctx.running, expanded: ctx.expanded.has(seg.key),
      onToggle: () => ctx.onToggle(seg.key) });
  }
  if (seg.type === 'approval') {
    const ui = ctx.approvalUi.get(String(seg.approval.id)) || {};
    return approvalCard(seg.approval, { onDecide: ctx.onDecide, busy: Boolean(ui.busy), error: ui.error || '' });
  }
  return noteCard(seg.note);
}

function footerFor(turn, ctx) {
  const parts = [];
  /* место работы по факту важнее догадки до отправки: main.js выводит его из модели, которой
     прогон реально пользовался (run.usage.model); сервер своего поля locality в run.usage
     сейчас не шлёт — usage.locality здесь только задел на будущее */
  const loc = (turn.usage && turn.usage.locality) || turn.locality || null;
  const model = turn.model || (turn.run && turn.run.model_alias) || '';
  if (model) parts.push(h('span.foot-model', model));
  const badge = localityBadge(loc);
  if (badge) parts.push(h('span.badge', { dataset: { tone: badge.tone }, title: badge.title }, badge.text));
  const tin = turn.usage ? turn.usage.tokens_in : turn.run && turn.run.tokens_in;
  const tout = turn.usage ? turn.usage.tokens_out : turn.run && turn.run.tokens_out;
  if (tin !== null && tin !== undefined) parts.push(h('span.foot-meta', { title: 'Токены: вход / выход' }, `${fmtTokens(tin)} → ${fmtTokens(tout)}`));
  if (turn.usage || turn.run) {
    const usage = turn.usage || { cost_usd: turn.run.cost_usd, pricing_known: turn.run.pricing_known };
    parts.push(h('span.foot-meta', costLabel(usage, { locality: loc, billing: (turn.usage && turn.usage.billing) || turn.billing || null })));
  }
  if (turn.startedAt && turn.finishedAt) parts.push(h('span.foot-meta', fmtDuration(turn.finishedAt - turn.startedAt)));
  const evalRow = turn.evaluations.length ? turn.evaluations[turn.evaluations.length - 1] : null;
  if (evalRow) parts.push(h('span.badge', { dataset: { tone: evalRow.verdict === 'PASS' ? 'local' : evalRow.verdict === 'FAIL' ? 'danger' : 'lan' }, title: evalRow.reasons || evalRow.verdict || '' }, `Проверка: ${verdictLabel(evalRow.verdict)}`));
  const actions = h('span.foot-actions',
    h('button.link-btn', { type: 'button', 'aria-label': 'Скопировать ответ', title: 'Скопировать ответ', onClick: () => ctx.onCopy(turn) }, icon('copy', 15)),
    h('button.link-btn', { type: 'button', 'aria-label': 'Шаги и источники этого ответа в правой панели', title: 'Показать в панели Thinking & Actions', onClick: () => ctx.onFocus(turn) }, icon('list', 15)),
    turn.taskId ? h('a.link-btn', { href: `/#/tasks?task=${encodeURIComponent(turn.taskId)}`, target: '_blank', rel: 'noopener noreferrer',
      'aria-label': `Открыть задачу ${turn.taskId} в Command Center`, title: `Задача #${turn.taskId} в Command Center` }, icon('open', 15)) : null);
  return [...parts, actions];
}

/** Всё, от чего зависит подвал ответа: подвал пересобирается, только когда это меняется. */
function footerSignature(turn) {
  const ev = turn.evaluations.length ? turn.evaluations[turn.evaluations.length - 1] : null;
  return JSON.stringify([turn.model, turn.locality, turn.billing, turn.usage, turn.run, turn.startedAt, turn.finishedAt,
    ev ? [ev.verdict, ev.reasons] : null, turn.taskId]);
}

/**
 * Обновить строку ответа. ctx: {running, expanded:Set, approvalUi:Map,
 * onToggle(key), onDecide(a, approve), onResend(turn), onCopy(turn), onFocus(turn)}.
 */
export function updateAssistant(row, turn, ctx) {
  const segsEl = row.querySelector('.bot-segs');
  const segs = displaySegments(turn);
  const keep = new Set();
  let prev = null;
  for (const seg of segs) {
    keep.add(seg.key);
    const sig = segSignature(seg, ctx);
    let node = row._segNodes.get(seg.key);
    if (!node || node._sig !== sig) {
      const fresh = buildSeg(seg, ctx);
      fresh._sig = sig;
      fresh.dataset.seg = seg.key;
      if (node) segsEl.replaceChild(fresh, node);
      node = fresh;
      row._segNodes.set(seg.key, node);
    } else if (seg.type === 'text') {
      updateTextSeg(node, seg);
    }
    const want = prev ? prev.nextSibling : segsEl.firstChild;
    if (node !== want) segsEl.insertBefore(node, want);
    prev = node;
  }
  for (const [key, node] of row._segNodes) {
    if (!keep.has(key)) { node.remove(); row._segNodes.delete(key); }
  }

  const { live, waiting, word, step, sig: statusSig } = statusLine(turn);
  /* ход ждёт решения владельца — строка не «занята»: карточка подтверждения должна
     дойти до читалки экрана сразу, а не после конца хода. Отрисовка при этом та же. */
  row.setAttribute('aria-busy', live && !waiting ? 'true' : 'false');
  row.dataset.live = live ? '1' : '0';
  const status = row.querySelector('.bot-status');
  if (row._statusSig !== statusSig) {
    row._statusSig = statusSig;
    clear(status);
    if (live) {
      status.appendChild(h('span.status-line',
        h('span.dots', { 'aria-hidden': 'true' }, h('i'), h('i'), h('i')),
        h('span', word),
        step ? h('span.status-step', step) : null));
    } else if (turn.status === 'stopped') {
      status.appendChild(h('span.status-line.is-stopped', icon('stop', 14), h('span', 'Остановлено владельцем')));
    }
  }

  /* карточка ошибки — role=alert: пересоздание на каждой перерисовке объявляло бы её снова */
  const errEl = row.querySelector('.bot-error');
  const info = turnErrorInfo(turn);
  const errSig = info ? `e|${info.title}|${info.message || ''}|${info.hint || ''}|${info.code || ''}` : turn.status === 'stopped' ? 'stopped' : '';
  if (row._errSig !== errSig) {
    row._errSig = errSig;
    clear(errEl);
    if (info) errEl.appendChild(errorCard(info, () => ctx.onResend(turn)));
    else if (turn.status === 'stopped') {
      errEl.appendChild(h('div.error-actions.is-soft',
        h('button.btn.btn-ghost', { type: 'button', 'aria-label': 'Отправить это сообщение ещё раз', onClick: () => ctx.onResend(turn) },
          icon('send', 16), 'Отправить ещё раз')));
    }
  }

  const foot = row.querySelector('.bot-foot');
  const footSig = live ? '' : footerSignature(turn);
  if (row._footSig !== footSig) {
    row._footSig = footSig;
    clear(foot);
    if (!live) for (const n of footerFor(turn, ctx)) foot.appendChild(n);
  }

  const hasText = segs.some((s) => s.type === 'text');
  setSphereState(row._orb, info ? 'error' : live ? (hasText ? 'streaming' : 'thinking') : 'idle');
}
