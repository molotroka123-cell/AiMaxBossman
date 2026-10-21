/* ============================================================
   coding.js — Coding Sessions: изолированные git-worktree сессии.
   Цепочка оператора: Session → Activity → Diff → Merge/Discard.
   Endpoints: GET/POST /api/coding-sessions, GET /api/coding-sessions/{id},
   GET .../diff, POST .../merge_preview | merge | discard.
   Бэкенд — единственный CodingWorktreeManager, второго движка нет.
   ============================================================ */

import { api, listOf } from '../api.js';
import {
  h, icon, statusBadge,
  toast, toastOk, toastError, openModal, actionButton,
  input, textarea, fmtDateShort,
} from '../components.js';
import { pageHead, errorNote, blank } from './_ui.js';

const CodingPage = {
  id: 'coding',
  title: 'Coding-сессии',
  icon: 'edit',
  nav: 'more',
  section: 'studio',

  async render(ctx) {
    let sessions = []; let err = null;
    try { sessions = listOf(await api.raw('/api/coding-sessions'), 'sessions'); }
    catch (e) { err = e; }
    /* Путь «поручить задачу агенту» (аудит владельца 2026-09-08, F4): раньше
       страница управляла только worktree, и до OpenHands из интерфейса было
       не дотянуться. Готовность читается с сервера и показывается честно. */
    /* Готовность — это рукопожатие с сайдкаром (живой вызов модели, до 150 с на
       холодной модели). Страница ждала его целиком и всё это время стояла на
       скелете (RC 1.9 soak: > 25 с после каждого перезапуска). Теперь ждём
       недолго, а поздний ответ дописываем на месте. */
    let tasks = [];
    let catalog = { workers: [], keys: [], self_coding: {}, summary: {} };
    const readyP = api.raw('/api/coding-tasks/readiness')
      .catch((e) => ({ available: false, reason: e.message || String(e) }));
    const [early] = await Promise.all([
      Promise.race([readyP, new Promise((r) => setTimeout(() => r(null), READINESS_WAIT_MS))]),
      api.raw('/api/coding-tasks').then((d) => { tasks = listOf(d, 'items'); }, () => { tasks = []; }),
      api.raw('/api/coding-tasks/workers').then((d) => { if (d && typeof d === 'object') catalog = d; }, () => {}),
    ]);
    let ready = early || { available: false, pending: true, reason: 'проверяю готовность OpenHands…' };

    const newTask = h('button.btn.btn-primary', { type: 'button', id: 'coding-new-task',
      onClick: () => taskModal(ctx, ready, catalog) }, icon('bolt', 14), h('span', 'Новая задача агенту'));
    const selfCodeBtn = h('button.btn.btn-primary', {
      type: 'button',
      id: 'opencode-self-code-btn',
      onClick: () => taskModal(ctx, ready, catalog, { selfCode: true }),
    }, icon('bolt', 14), h('span', 'OpenCode · Самокодинг Bossman'));
    const connectKeyBtn = h('button.btn', {
      type: 'button',
      id: 'opencode-connect-key-btn',
      onClick: () => connectKeyModal(ctx, catalog),
    }, icon('plus', 14), h('span', 'Ключи облака'));

    const applyReady = () => {
      newTask.disabled = !ready.available;
      newTask.title = ready.available ? 'Поручить задачу агенту OpenHands' : (ready.reason || 'OpenHands недоступен');
      const cloudReady = Boolean(catalog && catalog.summary && catalog.summary.any_cloud_ready);
      selfCodeBtn.disabled = !(ready.available || cloudReady);
      selfCodeBtn.title = (ready.available || cloudReady)
        ? 'Написать или улучшить код самого Bossman через облачные (FREE / PAID) или локальные модели'
        : 'Подключите облачный ключ или запустите локальный сайдкар';
    };
    applyReady();
    if (!early) {
      readyP.then((late) => {
        ready = late;
        applyReady();
        if (readinessSlot.isConnected) readinessSlot.replaceChildren(readinessLine(ready));
      });
    }

    const active = sessions.filter((s) => (s.status || 'active') === 'active').length;
    const head = pageHead('Coding-сессии · OpenCode Пульт', active
      ? `${active} активных · изоляция агента в отдельном git-worktree`
      : 'Аналог OpenCode в веб-пульте: топовые бесплатные и платные облачные кодеры, самокодинг Bossman, независимый pytest и безопасный Apply.', {
      actions: [
        h('button.btn', { type: 'button', title: 'Обновить', 'aria-label': 'Обновить', onClick: () => ctx.refresh() }, icon('retry', 14)),
        connectKeyBtn,
        selfCodeBtn,
        h('button.btn', { type: 'button', onClick: () => createModal(ctx) }, icon('plus', 14), h('span', 'Новая сессия')),
        newTask,
      ],
    });

    const readinessSlot = h('div', readinessLine(ready));
    const workbenchPanel = buildOpenCodeWorkbench(ctx, ready, catalog);
    const agentBlock = h('div.stack', { id: 'coding-tasks' },
      h('div.section-title', 'Задачи агенту (OpenCode / OpenHands)'),
      readinessSlot,
      tasks.length
        ? h('div.grid.auto-lg', tasks.map((t) => taskCard(t, ctx)))
        : h('div.small.dim', 'Задач агенту пока не было.'));

    const body = err
      ? errorNote(err, () => ctx.refresh())
      : sessions.length
        ? h('div.grid.auto-lg', sessions.map((s) => sessionCard(s, ctx)))
        : blank({
          iconName: 'apps', title: 'Сессий пока нет',
          hint: 'Создайте сессию для репозитория из разрешённых корней: агент получит отдельный worktree, а вы — честный diff и безопасный merge.',
        });

    return h('div.bx-page', head, workbenchPanel, agentBlock, h('div.section-title', 'Worktree-сессии'), body);
  },

  onEvent(ev) { return typeof ev.kind === 'string' && (ev.kind.startsWith('coding.session') || ev.kind.startsWith('coding.task')); },
};

const READINESS_WAIT_MS = 1500;

function readinessLine(ready) {
  if (ready.pending) {
    return h('div.small.dim', { id: 'coding-readiness', 'aria-busy': 'true' }, ready.reason);
  }
  return ready.available
    ? h('div.small.dim', `Агент работает в одноразовой копии репозитория без remote; результат — патч и доказательства. Push/merge/deploy у агента нет. Корни: ${(ready.roots || []).join(', ') || '—'}`)
    : h('div.small', { style: { color: 'var(--warn,#d99a2b)' }, id: 'coding-readiness' },
      `OpenHands сейчас недоступен: ${ready.reason || 'причина не названа'}`);
}

function buildOpenCodeWorkbench(ctx, ready, catalog) {
  const workers = Array.isArray(catalog && catalog.workers) ? catalog.workers : [];
  const keys = Array.isArray(catalog && catalog.keys) ? catalog.keys : [];
  const summary = (catalog && catalog.summary) || {};
  const selfCoding = (catalog && catalog.self_coding) || {};
  const freeWorkers = workers.filter((w) => w.tier === 'free');
  const paidWorkers = workers.filter((w) => w.tier === 'paid');

  const keyBadges = keys.map((k) => h('span.badge' + (k.connected ? '' : '.badge-warn'),
    { 'data-key-env': k.key_env, style: { maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis' } },
    `${k.key_env}: ${k.connected ? `подключён (${k.masked_preview || 'ok'})` : 'нет ключа'}`));

  const renderWorkerChip = (w) => h('button.btn.btn-sm', {
    type: 'button',
    'data-worker-chip': w.id,
    'data-worker-tier': w.tier,
    style: { textAlign: 'left', maxWidth: '100%', whiteSpace: 'normal', height: 'auto', lineHeight: '1.3' },
    title: `${w.model} · ${w.price_display} · контекст ${w.context_window}`,
    onClick: () => taskModal(ctx, ready, catalog, { worker: w.id, selfCode: true }),
  },
  h('span', { style: { overflowWrap: 'anywhere' } }, `${w.key_connected ? '🟢' : '⚪'} ${w.label} · ${w.price_display}`));

  return h('div.card.stack', { id: 'opencode-workbench', 'data-testid': 'opencode-workbench', style: { maxWidth: '100%', overflow: 'hidden' } },
    h('div.card-head', { style: { flexWrap: 'wrap', gap: '8px' } },
      h('div', { style: { flex: '1 1 220px', minWidth: 0 } },
        h('div.card-title', { style: { overflowWrap: 'anywhere' } }, 'OpenCode Workbench · Облачные и локальные модели для самокодинга Bossman'),
        h('div.card-sub', { style: { overflowWrap: 'anywhere', wordBreak: 'break-word' } },
          `Доступно облачных кодеров: ${summary.connected_cloud_workers || 0} из ${summary.total_cloud_workers || workers.length} `
          + `(FREE: ${(summary.ready_free_workers || []).length}/${freeWorkers.length}, `
          + `PAID: ${(summary.ready_paid_workers || []).length}/${paidWorkers.length}) · `
          + `Приоритет: Local → Free Cloud → Cheap Paid → Premium · Лимит: $0.50/задача, $2.00/сутки · `
          + `Репозиторий Bossman: ${selfCoding.source_repo || '—'}`)),
      h('div.row.tight', { style: { flexWrap: 'wrap' } },
        actionButton('Подключить API-ключ', () => connectKeyModal(ctx, catalog), { cls: 'btn btn-sm', iconName: 'plus' }),
        actionButton('Написать код самому себе', () => taskModal(ctx, ready, catalog, { selfCode: true }), { cls: 'btn btn-sm btn-primary', iconName: 'bolt' }))),
    keyBadges.length ? h('div.row.tight', { style: { flexWrap: 'wrap' } }, ...keyBadges) : null,
    freeWorkers.length ? h('div.stack.sm',
      h('div.xsmall.dim', 'Бесплатные топовые облачные модели (FREE · $0/1M · с поддержкой инструментов OpenCode):'),
      h('div.row.tight', { style: { flexWrap: 'wrap' } }, ...freeWorkers.map(renderWorkerChip))) : null,
    paidWorkers.length ? h('div.stack.sm',
      h('div.xsmall.dim', 'Платные топовые облачные модели (PAID · минимальная цена и максимальная точность кода):'),
      h('div.row.tight', { style: { flexWrap: 'wrap' } }, ...paidWorkers.map(renderWorkerChip))) : null);
}

function connectKeyModal(ctx, catalog) {
  const keys = Array.isArray(catalog && catalog.keys) ? catalog.keys : [
    { key_env: 'OPENROUTER_API_KEY' },
    { key_env: 'GROQ_API_KEY' },
    { key_env: 'GEMINI_API_KEY' },
    { key_env: 'NVIDIA_API_KEY' },
  ];
  const keyEnvEl = h('select.input', { id: 'opencode-key-env', 'aria-label': 'Провайдер ключа' },
    ...keys.map((k) => h('option', { value: k.key_env },
      `${k.key_env}${k.connected ? ` (сейчас подключён: ${k.masked_preview || 'да'})` : ' (не подключён)'}`)));
  const apiKeyEl = input({ id: 'opencode-key-input', type: 'password', placeholder: 'вставьте API-ключ (sk-or-..., gsk_..., AIza..., nvapi-...)' });
  const modal = openModal({
    title: 'Подключить облачный API-ключ для OpenCode',
    body: h('div.stack',
      h('div', h('div.section-title', 'Ключ провайдера'), keyEnvEl),
      h('div', h('div.section-title', 'Секретный API-ключ'), apiKeyEl),
      h('div.small.dim', 'Ключ шифруется в Vault Bossman и сразу открывает доступ к соответствующим бесплатным и платным облачным моделям OpenCode.')),
    footer: h('div'),
  });
  modal.footer.appendChild(actionButton('Сохранить и подключить', async () => {
    const val = apiKeyEl.value.trim();
    if (!val) { toastError({ message: 'Введите API-ключ' }); return; }
    try {
      const r = await api.raw('/api/coding-tasks/connect-key', {
        method: 'POST',
        body: { key_env: keyEnvEl.value, api_key: val, persist_to_owner_file: false },
      });
      apiKeyEl.value = '';
      toastOk(`Ключ ${r.key_env} подключён (${r.masked_preview}) · открыто моделей: ${(r.workers_unlocked || []).length}`);
      modal.close();
      ctx.refresh();
    } catch (e) { toastError(e, 'Не удалось сохранить ключ'); }
  }, { cls: 'btn btn-primary', iconName: 'bolt' }));
  modal.footer.appendChild(h('button.btn', { type: 'button', onClick: () => modal.close() }, 'Отмена'));
}

const TASK_LABEL = { running: 'выполняется', completed: 'выполнено', failed: 'сбой', blocked: 'заблокировано' };

function taskCard(t, ctx) {
  const status = t.status || 'running';
  const cleanup = t.sandbox_cleanup || null;
  const costLabel = t.cost_display || (t.worker ? '$0.0000 (FREE)' : '$0.0000 (LOCAL)');
  return h('div.card', { 'data-coding-task': t.id },
    h('div.card-head',
      h('div', { style: { flex: '1', minWidth: 0 } },
        h('div.card-title.truncate', t.instruction || '—'),
        h('div.card-sub.truncate', t.source_repo || '')),
      statusBadge(status, { live: status === 'running', label: TASK_LABEL[status] || status })),
    h('div.row.tight', { style: { flexWrap: 'wrap' } },
      t.worker ? h('span.badge', `облако: ${t.worker}`) : h('span.badge', 'локально'),
      t.model ? h('span.badge', `модель: ${t.model}`) : null,
      h('span.badge', { 'data-task-cost': t.id }, `стоимость: ${costLabel}`),
      t.self_target ? h('span.badge', 'самокодинг Bossman') : null,
      t.applied_at ? h('span.badge', 'ПРИМЕНЕНО В BOSSMAN') : null,
      h('span.badge', `файлов: ${(t.changed_files || []).length}`),
      h('span.badge', `область: ${(t.allowed_paths || []).join(', ') || '—'}`),
      (t.protected_paths || []).length ? h('span.badge', `защищено: ${t.protected_paths.join(', ')}`) : null,
      cleanup ? h('span.badge' + (cleanup.removed ? '' : '.badge-warn'),
        cleanup.removed ? 'песочница удалена' : `песочница осталась: ${cleanup.error || cleanup.path || ''}`) : null),
    t.error ? h('div.small', { style: { color: status === 'blocked' ? 'var(--warn,#d99a2b)' : 'var(--err)' } }, t.error) : null,
    h('div.row.tight',
      status !== 'running' ? actionButton('Diff, шаги OpenCode и Apply', () => showTask(t, ctx), { cls: 'btn btn-sm', iconName: 'search' }) : h('span.xsmall.dim', 'агент работает…')));
}

async function showTask(t, ctx) {
  const modal = openModal({ title: `Задача OpenCode · ${t.id}`, wide: true, body: h('div.small.dim', 'Загрузка…'), footer: h('div') });
  let d;
  try { d = await api.raw(`/api/coding-tasks/${encodeURIComponent(t.id)}`); }
  catch (e) { modal.body.textContent = ''; modal.body.appendChild(h('div.small', { style: { color: 'var(--err)' } }, e.message || 'нет данных')); return; }
  modal.body.textContent = '';
  const ev = d.evidence || {};
  const sidecar = d.sidecar || {};
  const toolCalls = Array.isArray(sidecar.tool_calls) ? sidecar.tool_calls : [];
  const receipt = d.receipt || null;
  const applyStatusSlot = h('div.stack.sm');

  const canApply = d.status === 'completed' && !d.applied_at && d.diff && d.verification && d.verification.passed;
  if (d.applied_at) {
    applyStatusSlot.appendChild(h('div.small', { style: { color: 'var(--ok,#2dd4bf)' } },
      `Патч уже применён к каноническому репозиторию Bossman (${d.applied_digest || 'exact'}).`));
  } else if (canApply) {
    applyStatusSlot.appendChild(actionButton('Применить проверенный патч к Bossman (Apply)', async () => {
      try {
        const wait = await api.raw(`/api/coding-tasks/${encodeURIComponent(d.id)}/apply`, { method: 'POST', body: {} });
        if (wait && wait.state === 'WAIT_APPROVAL' && wait.approval_id) {
          await api.raw(`/api/approvals/${encodeURIComponent(wait.approval_id)}`, {
            method: 'POST',
            body: { approve: true, by: 'owner' },
          });
          const applied = await api.raw(`/api/coding-tasks/${encodeURIComponent(d.id)}/apply`, {
            method: 'POST',
            body: { approval_id: wait.approval_id },
          });
          toastOk(`Патч применён к проекту (${applied.applied_digest || 'OK'})`);
          modal.close();
          ctx.refresh();
          return;
        }
        toastOk('Патч применён');
        modal.close();
        ctx.refresh();
      } catch (e) { toastError(e, 'Не удалось применить патч'); }
    }, { cls: 'btn btn-primary btn-sm', iconName: 'play' }));
  }

  const modalCostLabel = d.cost_display || (d.worker ? '$0.0000 (FREE)' : '$0.0000 (LOCAL)');
  modal.body.appendChild(h('div.stack',
    h('div.row.tight', { style: { flexWrap: 'wrap' } },
      statusBadge(d.status, { label: TASK_LABEL[d.status] || d.status }),
      d.worker ? h('span.badge', `исполнитель: ${d.worker}`) : h('span.badge', 'локальный сайдкар'),
      d.model ? h('span.badge', `модель: ${d.model}`) : null,
      h('span.badge', `стоимость: ${modalCostLabel}`),
      h('span.badge', `лимит: $${Number(d.spend_cap_usd ?? 0.50).toFixed(2)}`),
      h('span.badge', `файлов: ${(d.changed_files || []).length}`),
      d.duration_seconds != null ? h('span.badge', `${d.duration_seconds} с`) : null,
      sidecar.steps != null ? h('span.badge', `шагов: ${sidecar.steps}`) : null,
      h('span.badge', 'push/merge: нет'),
      d.agent ? h('span.badge', `агент: ${d.agent.name}`) : null,
      d.sidecar && d.sidecar.deterministic_test_model ? h('span.badge.badge-warn', 'MOCK_MODEL') : null,
      d.verification ? h('span.badge' + (d.verification.passed ? '' : '.badge-warn'), d.verification.passed ? 'проверка Bossman: пройдена' : 'проверка Bossman: не пройдена') : null,
      d.outcome ? h('span.badge.badge-warn', d.outcome) : null),
    d.error ? h('div.small', { style: { color: 'var(--err)' } }, d.error) : null,
    applyStatusSlot,
    sidecar.summary ? h('div.card', h('div.section-title', 'Отчёт модели OpenCode'), h('div.small', sidecar.summary)) : null,
    toolCalls.length ? h('div',
      h('div.section-title', `Трасса вызовов инструментов OpenCode (${toolCalls.length})`),
      h('pre.block', { style: { maxHeight: '24vh', overflow: 'auto' } },
        toolCalls.map((tc) => `[шаг ${tc.step} · ${tc.t}s] ${tc.tool}(${tc.path || tc.pattern || (tc.paths || []).join(',') || ''}) → ${tc.ok ? 'OK' : ('ERR: ' + (tc.err || ''))}`).join('\n'))) : null,
    (d.changed_files || []).length ? h('pre.block', d.changed_files.join('\n')) : null,
    d.diff ? h('pre.block', { style: { maxHeight: '45vh', overflow: 'auto' } }, d.diff) : h('div.small.dim', 'Патча нет.'),
    receipt ? h('div',
      h('div.section-title', 'Квитанция доказательства (Mission Receipt v1)'),
      h('pre.block', JSON.stringify({
        receipt_id: receipt.receipt_id,
        model: receipt.model,
        evidence_kind: receipt.evidence_kind,
        postcondition_verified: receipt.postcondition_verified,
        workspace_integrity_verified: receipt.workspace_integrity_verified,
        diff_digest: receipt.diff_digest,
        receipt_sha256: receipt.receipt_sha256,
      }, null, 1))) : null,
    h('div.section-title', 'Доказательства'),
    h('pre.block', JSON.stringify({ head_before: ev.head_before, head_after: ev.head_after,
      untracked: ev.untracked_files, sidecar: d.sidecar, verification: d.verification, memory: d.memory,
      worker_attempts: d.worker_attempts, sandbox_cleanup: d.sandbox_cleanup }, null, 1)),
    h('div.small.dim', 'Патч — доказательство работы агента. Применение к репозиторию защищено одноразовым подтверждением владельца и сверкой SHA-256.')));
  modal.footer.appendChild(h('button.btn', { type: 'button', onClick: () => modal.close() }, 'Закрыть'));
}

async function taskModal(ctx, ready, catalog = {}, opts = {}) {
  /* Сохранённые агенты продукта (RAW, TOOL_FIRST, MEMORY…) — профиль сайдкара:
     промпт, инструменты, лимит шагов, «тесты перед finish». */
  let agents = [];
  try { agents = listOf(await api.raw('/api/agents'), 'items'); } catch (e) { agents = []; }
  const workers = Array.isArray(catalog && catalog.workers) ? catalog.workers : [];
  const freeWorkers = workers.filter((w) => w.tier === 'free');
  const paidWorkers = workers.filter((w) => w.tier === 'paid');
  const defaultWorker = opts.worker || (opts.selfCode && ((catalog.summary && (catalog.summary.ready_free_workers || [])[0]) || (catalog.summary && (catalog.summary.ready_paid_workers || [])[0]))) || '';

  const workerEl = h('select.input', { id: 'coding-worker-select', 'aria-label': 'Исполнитель (модель OpenCode)' },
    h('option', { value: '', selected: !defaultWorker }, 'Локальная модель Bossman (по умолчанию)'),
    freeWorkers.length ? h('optgroup', { label: 'Бесплатные облачные кодеры (FREE · $0)' },
      ...freeWorkers.map((w) => h('option', { value: w.id, selected: w.id === defaultWorker },
        `${w.key_connected ? '🟢' : '⚪'} ${w.label} [${w.model}]`))) : null,
    paidWorkers.length ? h('optgroup', { label: 'Платные топовые облачные кодеры (PAID)' },
      ...paidWorkers.map((w) => h('option', { value: w.id, selected: w.id === defaultWorker },
        `${w.key_connected ? '🟢' : '⚪'} ${w.label} [${w.model}]`))) : null);

  const fallbackCheckEl = h('input', { type: 'checkbox', id: 'coding-auto-fallback', checked: Boolean(opts.selfCode) });
  const agentEl = h('select.input', { 'aria-label': 'Агент' },
    h('option', { value: '' }, 'без профиля (по умолчанию)'),
    ...agents.filter((a) => a.enabled !== false).map((a) => h('option', { value: String(a.id) }, `${a.name}${a.role ? ' — ' + a.role : ''}`)));

  const selfCoding = (catalog && catalog.self_coding) || {};
  const preset = ((selfCoding.presets || [])[0]) || {};
  const verifyEl = input({
    placeholder: 'тесты, которые Bossman сам прогонит после агента, например tests/test_x.py',
    value: opts.selfCode ? (preset.verify_tests || ['command-center/tests/test_coding_workers.py']).join(', ') : '',
  });
  const repoEl = input({
    placeholder: 'путь к git-репозиторию из разрешённых корней',
    value: opts.selfCode ? (selfCoding.source_repo || (ready.roots || [])[0] || 'self') : ((ready.roots || [])[0] || ''),
  });
  const instrEl = textarea({
    rows: '4',
    placeholder: 'Что сделать: например, добавить тест на парсер дат и починить падение',
    value: opts.selfCode ? (preset.instruction || '') : '',
  });
  const allowEl = input({
    placeholder: 'разрешённые пути через запятую, например src, tests',
    value: opts.selfCode ? (preset.allowed_paths || ['command-center/bcc/features', 'command-center/tests']).join(', ') : '',
  });
  const protectEl = input({
    placeholder: 'защищённые пути через запятую (необязательно), например .github, secrets',
    value: opts.selfCode ? (preset.protected_paths || ['.github', 'secrets']).join(', ') : '',
  });

  const fillSelfPresetBtn = actionButton('Заполнить для самокодинга Bossman', async () => {
    try {
      const st = await api.raw('/api/coding-tasks/self-target', { method: 'POST', body: { ensure_allowed_root: true } });
      repoEl.value = st.source_repo || 'self';
      const p0 = (st.presets || [])[0] || {};
      allowEl.value = (p0.allowed_paths || st.default_allowed_paths || []).join(', ');
      protectEl.value = (p0.protected_paths || st.default_protected_paths || []).join(', ');
      verifyEl.value = (p0.verify_tests || []).join(', ');
      if (!instrEl.value.trim() && p0.instruction) instrEl.value = p0.instruction;
      toastOk('Подставлен репозиторий самого Bossman и проверочные тесты');
    } catch (e) { toastError(e, 'Не удалось подготовить самокодинг'); }
  }, { cls: 'btn btn-sm', iconName: 'bolt' });

  const modal = openModal({
    title: opts.selfCode ? 'OpenCode · Самокодинг Bossman (Облако FREE/PAID или Локально)' : 'Новая задача агенту OpenHands',
    body: h('div.stack',
      h('div.row.tight', fillSelfPresetBtn),
      h('div', h('div.section-title', 'Исполнитель (Облачная модель FREE / PAID или Локально)'), workerEl),
      h('label.row.tight', fallbackCheckEl, h('span.small', 'Авто-резерв облачных моделей (при 429/недоступности переходить к следующей подключённой модели)')),
      h('div', h('div.section-title', 'Репозиторий'), repoEl),
      h('div', h('div.section-title', 'Задача'), instrEl),
      h('div', h('div.section-title', 'Область правок'), allowEl),
      h('div', h('div.section-title', 'Защищённые пути'), protectEl),
      h('div', h('div.section-title', 'Агент'), agentEl),
      h('div', h('div.section-title', 'Независимая проверка'), verifyEl),
      ready.handshake && ready.handshake.deterministic_test_model
        ? h('div.small', { style: { color: 'var(--warn,#d99a2b)' } }, 'Подключена MOCK_MODEL (детерминированная тестовая модель): результат проверяет механику, а не качество модели.')
        : null,
      h('div.small.dim', 'Агент получит одноразовую копию без remote. Итог — патч и доказательства; ничего не пушится и не вливается.')),
    footer: h('div'),
  });
  const split = (v) => String(v || '').split(',').map((x) => x.trim()).filter(Boolean);
  modal.footer.appendChild(actionButton('Поручить', async () => {
    try {
      const pickedWorker = workerEl.value || null;
      let fallbacks = [];
      if (pickedWorker && fallbackCheckEl.checked) {
        const readyFree = (catalog.summary && catalog.summary.ready_free_workers) || [];
        const readyPaid = (catalog.summary && catalog.summary.ready_paid_workers) || [];
        fallbacks = [...readyFree, ...readyPaid].filter((id) => id !== pickedWorker).slice(0, 5);
      }
      await api.raw('/api/coding-tasks', { method: 'POST', body: {
        instruction: instrEl.value.trim(), source_repo: repoEl.value.trim(),
        allowed_paths: split(allowEl.value), protected_paths: split(protectEl.value),
        agent_id: agentEl.value ? Number(agentEl.value) : null, verify_tests: split(verifyEl.value),
        worker: pickedWorker, fallback_workers: fallbacks,
        self_target: Boolean(opts.selfCode || repoEl.value.trim() === 'self') } });
      toastOk('Задача передана агенту');
      modal.close();
      ctx.refresh();
    } catch (e) { toastError(e, 'Не удалось поручить задачу'); }
  }, { cls: 'btn btn-primary', iconName: 'bolt' }));
  modal.footer.appendChild(h('button.btn', { type: 'button', onClick: () => modal.close() }, 'Отмена'));
}

function sessionCard(s, ctx) {
  const status = s.status || 'active';
  return h('div.card',
    h('div.card-head',
      h('div', { style: { flex: '1', minWidth: 0 } },
        h('div.card-title', s.session_id || '—'),
        h('div.card-sub.truncate', `${s.branch || ''} ← ${s.source_repo || ''}`)),
      statusBadge(status, { live: status === 'active' })),
    h('div.row.tight',
      h('span.badge', `база: ${String(s.base_ref || '').slice(0, 10) || '—'}`),
      s.created_at ? h('span.xsmall.dim', `создана ${fmtDateShort(s.created_at * 1000 || s.created_at)}`) : null),
    h('div.row.tight', status === 'active' ? [
      actionButton('Diff', () => showDiff(s, ctx), { cls: 'btn btn-sm', iconName: 'search' }),
      actionButton('Merge', () => doMerge(s, ctx), { cls: 'btn btn-sm btn-primary', iconName: 'play' }),
      actionButton('Отбросить', () => doDiscard(s, ctx), { cls: 'btn btn-sm btn-danger', iconName: 'stop' }),
    ] : [h('span.xsmall.dim', 'сессия закрыта — доступен только журнал')]));
}

function createModal(ctx) {
  const idEl = input({ placeholder: 'имя сессии, например fix-login' });
  const repoEl = input({ placeholder: 'путь к git-репозиторию из разрешённых корней' });
  const baseEl = input({ placeholder: 'HEAD', value: 'HEAD' });
  const modal = openModal({
    title: 'Новая coding-сессия',
    body: h('div.stack',
      h('div', h('div.section-title', 'Имя'), idEl),
      h('div', h('div.section-title', 'Репозиторий'), repoEl),
      h('div', h('div.section-title', 'Базовый ref'), baseEl)),
    footer: h('div'),
  });
  modal.footer.appendChild(actionButton('Создать', async () => {
    /* UX-13: пустая форма уходила на сервер и возвращалась 400 с полем «source_repo
       обязателен» (английское имя поля вместо подсказки). Спрашиваем по-русски до запроса. */
    if (!repoEl.value.trim()) { toast('Укажите папку репозитория', { type: 'warn', hint: 'Нужна папка с git-репозиторием из разрешённых корней.' }); return; }
    try {
      await api.raw('/api/coding-sessions', {
        method: 'POST',
        body: { session_id: idEl.value.trim(), source_repo: repoEl.value.trim(), base_ref: baseEl.value.trim() || 'HEAD' },
      });
      toastOk('Сессия создана — worktree готов');
      modal.close();
      ctx.refresh();
    } catch (e) { toastError(e, 'Не удалось создать сессию'); }
  }, { cls: 'btn btn-primary', iconName: 'plus' }));
  modal.footer.appendChild(h('button.btn', { type: 'button', onClick: () => modal.close() }, 'Отмена'));
}

async function showDiff(s, ctx) {
  const modal = openModal({ title: `Diff · ${s.session_id}`, wide: true, body: h('div.small.dim', 'Загрузка…'), footer: h('div') });
  let d;
  try { d = await api.raw(`/api/coding-sessions/${encodeURIComponent(s.session_id)}/diff`); }
  catch (e) { modal.body.textContent = ''; modal.body.appendChild(h('div.small', { style: { color: 'var(--err)' } }, e.message || 'нет дифа')); return; }
  modal.body.textContent = '';
  modal.body.appendChild(h('div.stack',
    h('div.row.tight',
      h('span.badge', `файлов: ${(d.files || []).length}`),
      d.truncated ? h('span.badge.badge-warn', 'диф обрезан (400 КБ)') : null),
    d.stat ? h('pre.block', d.stat) : null,
    d.patch ? h('pre.block', { style: { maxHeight: '55vh', overflow: 'auto' } }, d.patch) : h('div.small.dim', 'Изменений против базы нет.')));
  modal.footer.appendChild(h('button.btn', { type: 'button', onClick: () => modal.close() }, 'Закрыть'));
}

async function doMerge(s, ctx) {
  let preview;
  try { preview = await api.raw(`/api/coding-sessions/${encodeURIComponent(s.session_id)}/merge_preview`, { method: 'POST', body: {} }); }
  catch (e) { toastError(e, 'Не удалось получить превью слияния'); return; }
  if (preview && preview.clean === false) {
    toast('Merge отклонён политикой: конфликты не вливаются принудительно', { type: 'warn' });
    return;
  }
  try {
    const r = await api.raw(`/api/coding-sessions/${encodeURIComponent(s.session_id)}/merge`, { method: 'POST', body: {} });
    toastOk(`Слито в ${r.into || 'базу'} · ${(r.head || '').slice(0, 10)}`);
    ctx.refresh();
  } catch (e) { toastError(e, 'Merge отклонён (конфликты или ошибка git)'); }
}

async function doDiscard(s, ctx) {
  try {
    await api.raw(`/api/coding-sessions/${encodeURIComponent(s.session_id)}/discard`, { method: 'POST', body: {} });
    toastOk('Сессия отброшена: worktree и ветка удалены');
    ctx.refresh();
  } catch (e) { toastError(e, 'Не удалось отбросить сессию'); }
}

export default CodingPage;
