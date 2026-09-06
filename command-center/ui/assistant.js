/* ============================================================
   assistant.js — the assistant column as a reusable component.

   `mountAssistantPanel({ mount, taskId })` renders, top to bottom, exactly what
   docs/v3/BOSSMAN_DESIGN_SYSTEM.md ("The assistant column") specifies: the
   assistant mark and subtitle, the current request as a quiet card, the
   execution plan, quick actions, the capability surface, a prompt box and
   example pills. Nothing here is page-specific; the panel is mounted by a page,
   it does not mount pages.

   THE ONE RULE THIS FILE EXISTS TO KEEP
   -------------------------------------
   A reported success and a verified post-state must never be drawn the same
   way. `markNode()` draws five marks with five different geometries and five
   different role colours, and only `mark === 'verified'` — a value the SERVER
   computes in bcc/features/assistant_plan.py, and only from the finalizer's own
   `verification.result` — produces the filled check. The panel has no path that
   upgrades a mark on its own, and any mark it does not recognise falls back to
   the faint ring, never to a check and never to something that reads as done.
   The model's answer text is not rendered as a step at all: steps come from
   recorded tool calls or they do not exist.

   COLOUR. Every colour is a `--bxa-*` role token from assistant.css, scoped to
   `.bxa`. Two of the three themes are light; nothing here assumes a dark
   ground, and no raw hex is written for a themed surface.

   OFFLINE. No font, script, style or icon is fetched at runtime: the marks and
   the icons are inline SVG in this file, and assistant.css is a local file.
   ============================================================ */
import { api as defaultApi } from './api.js';
import { h, append, clear } from './components.js';

const CSS_HREF = '/assistant.css';
const CSS_ID = 'bxa-style-link';
const POLL_MS = 2500;

/* The state-mark table. Closed on purpose: a mark outside it cannot be drawn.
   `tone` names the role token the SVG paints itself with. */
export const MARK_SPEC = {
  verified: { tone: 'accent', label: 'подтверждено: пост-состояние перечитано и совпало' },
  reported: { tone: 'accent', label: 'доложено исполнителем; независимой проверки не было' },
  waiting: { tone: 'warn', label: 'ждёт решения владельца' },
  failed: { tone: 'danger', label: 'не выполнено' },
  pending: { tone: 'faint', label: 'не начато' },
};
const TONE_VAR = {
  accent: 'var(--bxa-accent)',
  warn: 'var(--bxa-warn)',
  danger: 'var(--bxa-danger)',
  faint: 'var(--bxa-text-faint)',
};

function svg(children, attrs = {}) {
  return h('svg', {
    viewBox: '0 0 14 14', width: 14, height: 14, 'aria-hidden': 'true',
    focusable: 'false', ...attrs,
  }, ...children);
}

/**
 * The state mark for one plan step.
 *
 * A FILLED check and a HOLLOW ring are different elements, not the same element
 * in two colours: the check has `fill` on its disc and a tick path inside it,
 * the ring has `fill="none"` and no tick. They can therefore never be confused
 * by a reader, by a screenshot, or by a test.
 */
export function markNode(mark) {
  const known = Object.prototype.hasOwnProperty.call(MARK_SPEC, mark);
  /* Unknown mark => the server and this panel disagree. Draw the weakest thing
     that claims nothing: never a check, never anything that reads as done. */
  const key = known ? mark : 'pending';
  const spec = MARK_SPEC[key];
  const colour = TONE_VAR[spec.tone];
  const attrs = {
    class: 'bxa-mark', 'data-mark': key, role: 'img',
    'aria-label': known ? spec.label : `состояние неизвестно: ${String(mark)}`,
  };
  if (key === 'verified') {
    return svg([
      h('circle', { cx: 7, cy: 7, r: 6, fill: colour }),
      h('path', {
        d: 'M4 7.3 L6.1 9.4 L10 5.2', fill: 'none', stroke: 'var(--bxa-on-accent)',
        'stroke-width': 1.7, 'stroke-linecap': 'round', 'stroke-linejoin': 'round',
      }),
    ], attrs);
  }
  if (key === 'reported') {
    return svg([
      h('circle', { cx: 7, cy: 7, r: 5.3, fill: 'none', stroke: colour, 'stroke-width': 1.7 }),
    ], attrs);
  }
  if (key === 'waiting') {
    return svg([h('circle', { cx: 7, cy: 7, r: 3.4, fill: colour })], attrs);
  }
  if (key === 'failed') {
    return svg([
      h('path', {
        d: 'M3.6 3.6 L10.4 10.4 M10.4 3.6 L3.6 10.4', fill: 'none', stroke: colour,
        'stroke-width': 1.8, 'stroke-linecap': 'round',
      }),
    ], attrs);
  }
  return svg([
    h('circle', {
      cx: 7, cy: 7, r: 5.3, fill: 'none', stroke: colour, 'stroke-width': 1.4,
      'stroke-dasharray': '2 2',
    }),
  ], attrs);
}

const ICON_PATHS = {
  spark: 'M7 1 L8.4 5.6 L13 7 L8.4 8.4 L7 13 L5.6 8.4 L1 7 L5.6 5.6 Z',
  file: 'M3.5 1.5 h5 l2.5 2.5 v8 h-7.5 z M8.5 1.5 v2.5 h2.5',
  memory: 'M2.5 4 a4.5 3 0 0 1 9 0 v6 a4.5 3 0 0 1 -9 0 z M2.5 7 a4.5 3 0 0 0 9 0',
  app: 'M2 2.5 h10 v9 h-10 z M2 5 h10',
  browser: 'M7 1.5 a5.5 5.5 0 1 0 0 11 a5.5 5.5 0 1 0 0 -11 M1.5 7 h11 M7 1.5 a8 5.5 0 0 1 0 11 a8 5.5 0 0 1 0 -11',
  image: 'M2 3 h10 v8 h-10 z M4 9 l2.5 -2.5 l2 2 l1.5 -1.5 l2 2',
};

function iconNode(name) {
  const d = ICON_PATHS[name] || ICON_PATHS.spark;
  return svg([h('path', {
    d, fill: 'none', stroke: 'currentColor', 'stroke-width': 1.2,
    'stroke-linecap': 'round', 'stroke-linejoin': 'round',
  })], { class: 'bxa-btn-icon', 'aria-hidden': 'true' });
}

/* ---------------- formatting ---------------- */

export function fmtElapsedMs(ms) {
  if (ms === null || ms === undefined || Number.isNaN(Number(ms))) return '—';
  const value = Number(ms);
  if (value < 1000) return `${Math.max(0, Math.round(value))} мс`;
  if (value < 60000) return `${(value / 1000).toFixed(1)} с`;
  const total = Math.round(value / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}

function ensureStylesheet(doc) {
  const document_ = doc || document;
  if (document_.getElementById(CSS_ID)) return;
  const link = document_.createElement('link');
  link.id = CSS_ID;
  link.rel = 'stylesheet';
  link.href = CSS_HREF;
  document_.head.appendChild(link);
}

/* ---------------- panel states (loading / empty / error / offline) ---------- */

function stateNode(kind, title, hint, action) {
  return h('div.bxa-state', { 'data-state': kind },
    kind === 'loading'
      ? h('div', { style: { display: 'flex', flexDirection: 'column', gap: '6px' } },
        h('div.bxa-skel', { style: { width: '60%' } }),
        h('div.bxa-skel', { style: { width: '90%' } }),
        h('div.bxa-skel', { style: { width: '75%' } }))
      : null,
    title ? h('div.bxa-state-title', title) : null,
    hint ? h('div.bxa-state-hint', hint) : null,
    action || null);
}

function errorState(err, retry) {
  const offline = Boolean(err && (err.isOffline || err.status === 0));
  return stateNode(
    offline ? 'offline' : 'error',
    offline ? 'Сервер не отвечает' : 'Не удалось получить план',
    /* The typed reason the backend returned, verbatim — not a friendly
       paraphrase that hides which layer refused. */
    String((err && err.message) || 'причина не названа'),
    retry ? h('button.bxa-btn', { type: 'button', onClick: retry },
      h('span.bxa-btn-label', 'Повторить')) : null);
}

/* ---------------- sections ---------------- */

function headNode(title, subtitle) {
  return h('div.bxa-head',
    svg([h('path', {
      d: ICON_PATHS.spark, fill: 'currentColor',
    })], { class: 'bxa-spark', 'aria-hidden': 'true' }),
    h('div.bxa-head-text',
      h('div.bxa-title', title),
      h('div.bxa-sub', subtitle)));
}

function requestNode(request) {
  if (!request) return null;
  return h('div.bxa-card.bxa-request',
    h('div.bxa-label', `Запрос · ${request.status || '—'}`),
    h('div.bxa-request-text', request.prompt || request.title || ''));
}

function verdictNode(verification) {
  const status = (verification && verification.status) || 'NOT_RUN';
  const text = {
    VERIFIED: 'Пост-состояние перечитано и совпало',
    FAILED: 'Пост-состояние перечитано и НЕ совпало',
    UNVERIFIED: 'Проверить пост-состояние не удалось — доказательства нет',
    NOT_RUN: 'Наблюдение пост-состояния ещё не проводилось',
  }[status] || `Состояние проверки: ${status}`;
  return h('div.bxa-verdict', { 'data-status': status },
    markNode(status === 'VERIFIED' ? 'verified'
      : status === 'FAILED' ? 'failed'
        : status === 'UNVERIFIED' ? 'waiting' : 'pending'),
    h('div',
      h('div', text),
      verification && verification.reason
        ? h('div.bxa-verdict-reason.bxa-micro', verification.reason) : null));
}

function legendNode() {
  return h('div.bxa-legend',
    ...['verified', 'reported', 'waiting', 'failed', 'pending'].map((mark) => h(
      'span.bxa-legend-item', markNode(mark), h('span', {
        verified: 'проверено',
        reported: 'доложено',
        waiting: 'ждёт владельца',
        failed: 'не вышло',
        pending: 'не начато',
      }[mark]))));
}

function stepNode(step, index) {
  const obligation = step.obligation;
  return h('li.bxa-step', { 'data-mark': step.mark, 'data-capability': step.capability },
    markNode(step.mark),
    h('div.bxa-step-body',
      h('div.bxa-step-title', `${index + 1}. ${step.title || step.capability}`),
      h('div.bxa-step-detail', { title: step.detail || '' }, step.detail || '—'),
      h('div.bxa-step-reason', step.reason || ''),
      obligation
        ? h('span.bxa-step-obl',
          `обязательство ${obligation.kind}: ${obligation.target}`)
        : null,
      step.executor_known
        ? h('div.bxa-micro', `исполнитель: ${step.executor || '—'}`)
        : h('div.bxa-micro', 'исполнитель этой способности не зарегистрирован')),
    h('div.bxa-step-time.bxa-mono', fmtElapsedMs(step.elapsed_ms)));
}

function planNode(plan) {
  if (!plan) return stateNode('loading', '', '');
  const steps = Array.isArray(plan.steps) ? plan.steps : [];
  const section = h('div.bxa-section',
    h('div.bxa-section-head',
      h('div.bxa-label', 'План исполнения'),
      h('div.bxa-micro.bxa-mono', fmtElapsedMs(plan.elapsed_ms))),
    verdictNode(plan.verification));
  if (!steps.length) {
    append(section, stateNode('empty', 'Шагов ещё нет',
      'Здесь появятся вызовы инструментов этой задачи. Текст ответа модели шагом не является '
      + 'и здесь не показывается.'));
  } else {
    append(section, h('ol.bxa-steps', ...steps.map(stepNode)));
  }
  (plan.notes || []).forEach((note) => append(section, h('div.bxa-note', note)));
  append(section, legendNode());
  return section;
}

/* ---------------- quick actions ---------------- */

function quickActionsNode(actions, onRun) {
  const section = h('div.bxa-section', h('div.bxa-label', 'Быстрые действия'));
  if (!actions) return append(section, stateNode('loading', '', ''));
  if (!actions.length) {
    return append(section, stateNode('empty', 'Быстрых действий нет',
      'Их объявляет сервер вместе с их способностью и ожидаемым пост-состоянием.'));
  }
  const form = h('div', { hidden: true });
  const grid = h('div.bxa-grid');
  actions.forEach((action) => {
    const grant = action.grant || {};
    const why = grant.reason || '';
    const how = (grant.remediation || []).map((r) => r.how).join(' ');
    const button = h('button.bxa-btn', {
      type: 'button',
      disabled: !action.available,
      /* A disabled control must expose the reason on hover, never just go dead. */
      title: action.available
        ? `${action.capability} → ожидается ${action.expect_kind}`
        : `${why}\n${how}`.trim(),
      'data-action': action.action_id,
      'data-available': String(Boolean(action.available)),
      onClick: () => openForm(action),
    }, iconNode(action.icon), h('span.bxa-btn-label', action.label));
    append(grid, button);
  });
  append(section, grid, form);

  function openForm(action) {
    clear(form);
    form.hidden = false;
    const input = h('input.bxa-filter', {
      type: 'text', placeholder: action.target_label,
      'aria-label': action.target_label, 'data-role': 'quick-target',
    });
    append(form,
      h('div.bxa-card',
        h('div.bxa-label', `${action.label} · способность ${action.capability}`),
        h('div.bxa-micro',
          `ожидаемое пост-состояние: ${action.expect_kind} — проверяется тем же финализатором`),
        input,
        h('div', { style: { display: 'flex', gap: '8px', marginTop: '8px' } },
          h('button.bxa-btn.bxa-btn-primary', {
            type: 'button', 'data-role': 'quick-submit',
            onClick: () => onRun(action, input.value.trim()),
          }, h('span.bxa-btn-label', 'Поставить в работу')),
          h('button.bxa-btn', {
            type: 'button', onClick: () => { form.hidden = true; clear(form); },
          }, h('span.bxa-btn-label', 'Отмена')))));
    input.focus();
  }
  return section;
}

/* ---------------- capability surface ---------------- */

function capabilityNode(item) {
  const grant = item.grant || {};
  const remediation = grant.remediation || [];
  return h('div.bxa-cap', { 'data-granted': String(Boolean(grant.granted)),
    'data-capability': item.capability_id },
  h('div.bxa-cap-head',
    h('span.bxa-cap-id.bxa-mono', item.capability_id),
    h('span.bxa-cap-exec', item.executor ? `исполнитель: ${item.executor}` : 'исполнителя нет')),
  h('div.bxa-micro', item.verification_strategy
    ? `доказывается: ${item.verification_strategy}`
    : 'доказать эффект нечем'),
  grant.granted ? null : h('div.bxa-cap-why', grant.reason || 'причина не названа'),
  ...remediation.map((r) => h('div.bxa-cap-how', `${r.what} ${r.how}`)));
}

function capabilitySurfaceNode(surface, filterText, onFilter) {
  const section = h('div.bxa-section');
  if (!surface) {
    append(section, h('div.bxa-label', 'Способности'), stateNode('loading', '', ''));
    return section;
  }
  const items = (surface.capabilities || []).filter((c) => {
    if (!filterText) return true;
    return String(c.capability_id).toLowerCase().includes(filterText.toLowerCase());
  });
  append(section,
    h('div.bxa-section-head',
      h('div.bxa-label', 'Способности этой сборки'),
      h('div.bxa-micro', `${surface.granted} доступно · ${surface.blocked} закрыто`)),
    h('input.bxa-filter', {
      type: 'search', placeholder: 'фильтр по способности', value: filterText || '',
      'aria-label': 'фильтр по способности', onInput: (e) => onFilter(e.target.value),
    }));
  if (!items.length) {
    append(section, stateNode('empty', 'Ничего не найдено',
      'Список приходит из живого реестра инструментов: если способности здесь нет, '
      + 'её нет и у модели.'));
    return section;
  }
  append(section, h('div.bxa-caps', ...items.map(capabilityNode)));
  return section;
}

/* ---------------- the panel ---------------- */

/**
 * Mount the assistant panel.
 *
 * options:
 *   mount      Element to render into (required).
 *   taskId     Task whose plan to follow (may be null; set later with setTask).
 *   api        API client, defaults to ui/api.js. Injectable for tests.
 *   pollMs     Poll interval; the poll is compact — it sends the last plan
 *              version and gets three fields back while nothing has changed.
 *   agentId    Agent used for tasks the composer and quick actions create.
 *   runNow     Whether created tasks are enqueued immediately (default false).
 *   examples   Example prompts rendered as pills.
 *   onTask     Called with a newly created task id.
 */
export function mountAssistantPanel(options = {}) {
  const opts = {
    api: defaultApi, pollMs: POLL_MS, title: 'Ассистент',
    subtitle: 'план, способности и доказательства — без слов на веру',
    examples: ['Создай файл отчёта и запиши в него итог',
      'Запомни решение по этой задаче',
      'Что эта сборка умеет прямо сейчас?'],
    runNow: false, agentId: null, onTask: null, wide: false, ...options,
  };
  const mount = opts.mount;
  if (!mount) throw new Error('mountAssistantPanel: нужен mount');
  ensureStylesheet(mount.ownerDocument);

  const state = {
    taskId: opts.taskId ?? null,
    plan: null, planError: null,
    quick: null, quickError: null,
    surface: null, surfaceError: null,
    filter: '', busy: false, stopped: false,
  };

  const root = h('section.bxa', {
    class: opts.wide ? 'bxa-wide' : null,
    'aria-label': 'Панель ассистента', 'data-task-id': String(state.taskId ?? ''),
  });
  const planHost = h('div', { 'data-role': 'plan' });
  const quickHost = h('div', { 'data-role': 'quick' });
  const surfaceHost = h('div', { 'data-role': 'surface' });
  const composerHost = h('div.bxa-composer', { 'data-role': 'composer' });
  append(root, headNode(opts.title, opts.subtitle));
  const requestHost = h('div', { 'data-role': 'request' });
  append(root, requestHost, planHost, quickHost, surfaceHost, composerHost);
  append(mount, root);

  function renderPlan() {
    clear(requestHost);
    clear(planHost);
    if (state.planError) { append(planHost, errorState(state.planError, refresh)); return; }
    if (state.taskId === null) {
      append(planHost, stateNode('empty', 'Задача не выбрана',
        'Напишите запрос ниже или выберите быстрое действие — план появится здесь.'));
      return;
    }
    if (!state.plan) { append(planHost, stateNode('loading', 'Загружаю план', '')); return; }
    append(requestHost, requestNode(state.plan.request));
    append(planHost, planNode(state.plan));
  }

  function renderQuick() {
    clear(quickHost);
    if (state.quickError) { append(quickHost, errorState(state.quickError, loadStatic)); return; }
    append(quickHost, quickActionsNode(state.quick, runQuickAction));
  }

  function renderSurface() {
    clear(surfaceHost);
    if (state.surfaceError) { append(surfaceHost, errorState(state.surfaceError, loadStatic)); return; }
    append(surfaceHost, capabilitySurfaceNode(state.surface, state.filter, (value) => {
      state.filter = value;
      renderSurface();
      const field = surfaceHost.querySelector('.bxa-filter');
      if (field) field.focus();
    }));
  }

  function renderComposer() {
    clear(composerHost);
    const box = h('textarea.bxa-input', {
      placeholder: 'Что нужно сделать?', 'aria-label': 'Запрос ассистенту',
      'data-role': 'prompt',
    });
    append(composerHost,
      box,
      h('button.bxa-btn.bxa-btn-primary', {
        type: 'button', 'data-role': 'send', disabled: state.busy,
        onClick: () => submitPrompt(box.value.trim()),
      }, h('span.bxa-btn-label', state.busy ? 'Отправляю…' : 'Отправить')),
      h('div.bxa-pills', ...opts.examples.map((text) => h('button.bxa-pill', {
        type: 'button', onClick: () => { box.value = text; box.focus(); },
      }, text))));
  }

  async function loadStatic() {
    state.quickError = null;
    state.surfaceError = null;
    renderQuick();
    renderSurface();
    try {
      const body = await opts.api.raw('/api/assistant/quick-actions');
      state.quick = body.actions || [];
    } catch (err) { state.quickError = err; }
    try {
      state.surface = await opts.api.raw('/api/assistant/capabilities');
    } catch (err) { state.surfaceError = err; }
    renderQuick();
    renderSurface();
  }

  async function refresh() {
    if (state.taskId === null) { state.plan = null; renderPlan(); return; }
    const version = state.plan && state.plan.version ? state.plan.version : '';
    const path = `/api/assistant/plan/${state.taskId}`
      + (version ? `?version=${encodeURIComponent(version)}` : '');
    try {
      const body = await opts.api.raw(path);
      state.planError = null;
      /* changed:false is the compact answer — the version we already hold is
         still current, so nothing is redrawn and nothing is invented. */
      if (body && body.changed === false) return;
      state.plan = body;
    } catch (err) {
      state.planError = err;
      state.plan = null;
    }
    renderPlan();
  }

  async function submitPrompt(text) {
    if (!text || state.busy) return;
    state.busy = true;
    renderComposer();
    try {
      const body = await opts.api.raw('/api/tasks', {
        method: 'POST',
        body: { title: text.slice(0, 80), prompt: text, agent_id: opts.agentId,
          run_now: Boolean(opts.runNow) },
      });
      const id = body && body.task ? body.task.id : null;
      if (id) setTask(id);
      if (id && typeof opts.onTask === 'function') opts.onTask(id);
    } catch (err) {
      state.planError = err;
      renderPlan();
    } finally {
      state.busy = false;
      renderComposer();
    }
  }

  async function runQuickAction(action, target) {
    if (!target) return;
    try {
      const body = await opts.api.raw(
        `/api/assistant/quick-actions/${encodeURIComponent(action.action_id)}`,
        { method: 'POST', body: { target, agent_id: opts.agentId, run_now: Boolean(opts.runNow) } });
      if (body && body.task_id) {
        setTask(body.task_id);
        if (typeof opts.onTask === 'function') opts.onTask(body.task_id);
      }
    } catch (err) {
      /* A refused capability is shown as the refusal it is — never as a task
         that quietly does nothing. */
      state.planError = err;
      renderPlan();
    }
  }

  function setTask(taskId) {
    state.taskId = taskId === null || taskId === undefined ? null : Number(taskId);
    state.plan = null;
    state.planError = null;
    root.dataset.taskId = String(state.taskId ?? '');
    renderPlan();
    return refresh();
  }

  let timer = null;
  function tick() {
    if (state.stopped) return;
    timer = setTimeout(async () => {
      await refresh();
      tick();
    }, opts.pollMs);
  }

  function destroy() {
    state.stopped = true;
    if (timer) clearTimeout(timer);
    if (root.parentNode) root.parentNode.removeChild(root);
  }

  renderPlan();
  renderQuick();
  renderSurface();
  renderComposer();
  const ready = Promise.all([loadStatic(), refresh()]);
  tick();

  return { el: root, ready, refresh, setTask, destroy, state: () => state };
}

export default mountAssistantPanel;
