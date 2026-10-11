/* ============================================================
   vibe.js — Vibe Studio: «пишете словами — получаете маленькое приложение».
   Endpoints (bcc/features/vibe.py, под /api):
     GET /vibe/status, GET|POST /vibe/jobs, GET /vibe/jobs/{id}/events?after=,
     POST /vibe/jobs/{id}/cancel|retry, GET /vibe/apps, GET /vibe/apps/{slug},
     GET /vibe/apps/{slug}/versions/{n}/preview, GET /vibe/apps/{slug}/diff,
     POST /vibe/apps/{slug}/rollback|promote|promote/finish,
     GET /vibe/long-run, POST /vibe/stop|resume.

   Паттерн взят у vibeOS (MIT): запрос словами → агент пишет маленькое
   приложение → каждый шаг виден → решает владелец. Без ВМ, без внешних ресурсов.

   Безопасность превью: iframe с sandbox="allow-scripts" БЕЗ allow-same-origin
   (непрозрачный origin, нет cookie и хранилища Command Center), документ
   отдаётся с жёстким CSP (connect-src 'none'). Код приложения в основном окне
   не исполняется никогда.
   Приватность: «только локально» запрещает облачные модели для задачи; в
   облако (если не включено «только локально») уходит текст запроса и мини-спека.
   ============================================================ */

import { api } from '../api.js';
import { h, toastOk, toastError } from '../components.js';
import { panel, pageHead, pill, btn, errorNote, blank } from './_ui.js';

const base = '/api/vibe';
const LIVE = new Set(['queued', 'planning', 'generating', 'checking']);
const STATUS_LABEL = { queued: 'в очереди', planning: 'план', generating: 'генерация', checking: 'проверка',
  completed: 'готово', failed: 'ошибка', cancelled: 'остановлено', stopped: 'STOP' };
const STATUS_TONE = { queued: 'idle', planning: 'warn', generating: 'warn', checking: 'warn',
  completed: 'ok', failed: 'err', cancelled: 'idle', stopped: 'err' };
const KIND_LABEL = { queued: 'очередь', planning: 'план', ladder: 'модели', generating: 'модель', model_reply: 'ответ',
  plan: 'шаг плана', file_written: 'файл записан', check_passed: 'проверка пройдена', check_failed: 'проверка отклонила',
  model_error: 'ошибка модели', cap_hit: 'ЛИМИТ', privacy: 'приватность', completed: 'готово', failed: 'ошибка',
  cancelled: 'остановлено', stopped: 'STOP', resumed: 'возобновлено' };
/* Единственное место, где задаётся sandbox. НЕ добавлять allow-same-origin. */
export const PREVIEW_SANDBOX = 'allow-scripts';

const G = (path) => api.raw(path);
const P = (path, body) => api.raw(path, { method: 'POST', body });
const errText = (e) => (e && e.message) || String(e);
const fmtEur = (n) => `${Number(n || 0).toFixed(2)} €`;

const VibePage = {
  id: 'vibe',
  title: 'Vibe Studio',
  icon: 'builder',
  nav: 'more',
  section: 'studio',

  async render() {
    const state = { jobs: [], apps: [], status: null, current: null, cursor: 0, log: [], app: null, version: null };

    const head = pageHead('Vibe Studio',
      'Опишите приложение словами: Bossman напишет маленькую статическую страницу, проверит её и покажет в песочнице. '
      + 'В облако уходит только текст запроса; репозиторий, чаты, фото, логи и ключи — никогда.');

    /* ---- форма ---- */
    const request = h('textarea', { id: 'vibe-request', name: 'vibe-request', rows: 5, maxlength: '4000',
      placeholder: 'Например: счётчик подходов с кнопками плюс и минус и сбросом', style: { width: '100%' } });
    const title = h('input', { id: 'vibe-title', name: 'vibe-title', type: 'text', maxlength: '80', placeholder: 'Название (необязательно)' });
    const localOnly = h('input', { id: 'vibe-local', name: 'vibe-local', type: 'checkbox' });
    const appSelect = h('select', { id: 'vibe-app', name: 'vibe-app' }, h('option', { value: '' }, 'Новое приложение'));
    const formErr = h('div', { 'aria-live': 'polite' });
    const createBtn = btn('Собрать', async () => {
      formErr.replaceChildren();
      const text = request.value.trim();
      if (!text) { formErr.append(h('p', { role: 'alert' }, 'Опишите, что нужно построить.')); return; }
      try {
        const body = { request: text, privacy: localOnly.checked ? 'local_only' : 'default' };
        if (title.value.trim()) body.title = title.value.trim();
        if (appSelect.value) body.slug = appSelect.value;
        const job = await P(`${base}/jobs`, body);
        state.current = job.job_id; state.cursor = 0; state.log = [];
        toastOk('Задача поставлена в очередь');
        await refresh();
      } catch (e) { formErr.append(h('p', { role: 'alert' }, errText(e))); toastError(e); }
    }, { variant: 'primary', iconName: 'play' });
    const stopBtn = btn('STOP', async () => {
      try { await P(`${base}/stop`, {}); toastOk('Файл STOP записан: платные вызовы и новые задачи остановлены'); await refresh(); }
      catch (e) { toastError(e); }
    }, { variant: 'danger', iconName: 'stop' });
    const resumeBtn = btn('Снять STOP', async () => {
      try { await P(`${base}/resume`, {}); await refresh(); } catch (e) { toastError(e); }
    }, { variant: 'subtle', size: 'sm' });

    const formPanel = panel('Запрос', h('div', { style: { display: 'grid', gap: '10px' } },
      h('label', { for: 'vibe-request' }, 'Что построить или изменить'), request,
      h('div', { style: { display: 'flex', gap: '10px', flexWrap: 'wrap' } },
        h('label', { for: 'vibe-app' }, 'Приложение'), appSelect, title),
      h('label', { for: 'vibe-local', style: { display: 'flex', gap: '8px', alignItems: 'center' } }, localOnly,
        'Только локальные модели (ничего не уходит в облако)'),
      h('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap' } }, createBtn, stopBtn, resumeBtn),
      formErr), { icon: 'builder' });

    /* ---- статус, лестница моделей, лимиты ---- */
    const statusHost = h('div', { 'aria-live': 'polite', dataset: { testid: 'vibe-status' } });
    /* ---- ход работы ---- */
    const logHost = h('ol', { dataset: { testid: 'vibe-log' }, 'aria-live': 'polite',
      style: { listStyle: 'none', padding: 0, margin: 0, display: 'grid', gap: '4px', fontSize: '13px' } });
    const jobHead = h('div');
    const jobBox = h('div', { style: { display: 'grid', gap: '8px' } }, jobHead, logHost);
    /* ---- приложения / версии / превью ---- */
    const appsHost = h('div', { style: { display: 'grid', gap: '8px' } });
    const versionsHost = h('div', { style: { display: 'grid', gap: '8px' } });
    const diffHost = h('pre', { dataset: { testid: 'vibe-diff' }, style: { maxHeight: '260px', overflow: 'auto', fontSize: '12px' } });
    const previewHost = h('div');
    const historyHost = h('div', { style: { display: 'grid', gap: '4px' } });

    function renderStatus() {
      const s = state.status;
      statusHost.replaceChildren();
      if (!s) return;
      const lr = s.long_run || {};
      const tiers = s.ladder || {};
      const pills = [
        pill(s.stop ? 'STOP' : 'работает', { tone: s.stop ? 'err' : 'ok', title: s.stop || '' }),
        pill(`локальных: ${(tiers.local || []).length}`, { tone: (tiers.local || []).length ? 'ok' : 'warn' }),
        pill(`бесплатных: ${(tiers.free || []).length}`, { tone: 'idle' }),
        pill(`платных: ${(tiers.paid || []).length}`, { tone: lr.profile_enabled ? 'warn' : 'idle' }),
        pill(lr.profile_enabled ? 'long-run включён' : 'long-run выключен', { tone: lr.profile_enabled ? 'warn' : 'idle' }),
      ];
      statusHost.append(h('div', { style: { display: 'flex', gap: '6px', flexWrap: 'wrap' } }, pills));
      if (s.stop) statusHost.append(h('p', { role: 'status' }, `Остановлено: ${s.stop}. Новые задачи ждут в очереди.`));
      if (lr.profile_enabled) {
        const rows = Object.entries(lr.providers || {}).map(([k, p]) =>
          h('li', `${p.label}: потрачено ${fmtEur(p.spent_eur)} из ${fmtEur(p.cap_eur)} сегодня, осталось ${fmtEur(p.remaining_eur)}`));
        statusHost.append(h('ul', { style: { margin: '6px 0 0', paddingLeft: '18px' } }, rows));
        if (lr.refusals_today) {
          statusHost.append(h('p', { role: 'alert' }, `Отказов по лимиту сегодня: ${lr.refusals_today}. `
            + `${(lr.last_refusal && lr.last_refusal.why) || ''}`));
        }
      }
    }

    function renderLog() {
      logHost.replaceChildren(...state.log.map((e) => h('li', { class: e.kind === 'check_failed' || e.kind === 'failed' || e.kind === 'cap_hit' ? 'is-err' : '' },
        h('strong', `${KIND_LABEL[e.kind] || e.kind}: `), h('span', e.message))));
      logHost.lastElementChild?.scrollIntoView?.({ block: 'nearest' });
    }

    function renderJob(job) {
      jobHead.replaceChildren();
      if (!job) { jobHead.append(h('p', 'Выберите задачу или создайте новую.')); return; }
      jobHead.append(h('div', { style: { display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' } },
        pill(STATUS_LABEL[job.status] || job.status, { tone: STATUS_TONE[job.status] || 'idle', live: LIVE.has(job.status) }),
        h('span', `${job.slug} · ${job.kind === 'modify' ? 'изменение' : 'создание'}${job.privacy === 'local_only' ? ' · только локально' : ''}`),
        LIVE.has(job.status) ? btn('Отменить', async () => { try { await P(`${base}/jobs/${job.job_id}/cancel`, {}); await refresh(); } catch (e) { toastError(e); } }, { size: 'sm', variant: 'danger' }) : null,
        ['failed', 'stopped', 'cancelled'].includes(job.status) ? btn('Повторить', async () => { try { const j = await P(`${base}/jobs/${job.job_id}/retry`, {}); state.current = j.job_id; state.cursor = 0; state.log = []; await refresh(); } catch (e) { toastError(e); } }, { size: 'sm' }) : null,
        job.status === 'completed' && job.result ? btn('Открыть превью', () => openApp(job.result.slug, job.result.version), { size: 'sm', variant: 'primary' }) : null));
      if (job.error) jobHead.append(h('p', { role: 'alert' }, `${job.error.code}: ${job.error.message}`));
    }

    async function openApp(slug, version) {
      try {
        state.app = await G(`${base}/apps/${slug}`);
        state.version = version || state.app.current;
        renderApp();
      } catch (e) { toastError(e); }
    }

    function renderApps() {
      appsHost.replaceChildren();
      appSelect.replaceChildren(h('option', { value: '' }, 'Новое приложение'),
        ...state.apps.map((a) => h('option', { value: a.slug }, `${a.title || a.slug} (v${a.current})`)));
      if (!state.apps.length) { appsHost.append(h('p', 'Приложений пока нет. Первое появится после успешной сборки.')); return; }
      for (const a of state.apps) {
        appsHost.append(h('div', { style: { display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' } },
          h('strong', a.title || a.slug), h('span', `v${a.current} · версий: ${a.versions}`),
          a.promoted ? pill('в списке приложений', { tone: 'ok' }) : null,
          btn('Открыть', () => openApp(a.slug), { size: 'sm' })));
      }
    }

    function renderApp() {
      const app = state.app;
      versionsHost.replaceChildren(); previewHost.replaceChildren(); diffHost.textContent = '';
      if (!app) return;
      const cur = app.current;
      versionsHost.append(h('p', `${app.title} — текущая версия v${cur}. Откат только переставляет указатель, версии не удаляются.`));
      for (const v of [...app.versions].reverse()) {
        versionsHost.append(h('div', { style: { display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' } },
          h('strong', `v${v.n}`), h('span', `${v.tier} · ${v.model} · ${v.created_at}`),
          h('span', { title: v.request }, (v.request || '').slice(0, 60)),
          btn('Превью', () => { state.version = v.n; renderApp(); }, { size: 'sm' }),
          v.n > 1 ? btn('Diff к предыдущей', async () => {
            try {
              const prev = app.versions[app.versions.findIndex((x) => x.n === v.n) - 1];
              const d = await G(`${base}/apps/${app.slug}/diff?from=${prev.n}&to=${v.n}`);
              diffHost.textContent = d.files.map((f) => `# ${f.path} (${f.status})\n${f.diff}`).join('\n\n') || 'Без изменений';
            } catch (e) { toastError(e); }
          }, { size: 'sm' }) : null,
          v.n !== cur ? btn('Откатить сюда', async () => {
            try { await P(`${base}/apps/${app.slug}/rollback`, { version: v.n }); await openApp(app.slug, v.n); await refresh(); toastOk(`Текущая версия: v${v.n}`); }
            catch (e) { toastError(e); }
          }, { size: 'sm' }) : null,
          btn('В список приложений…', async () => {
            try {
              const r = await P(`${base}/apps/${app.slug}/promote`, { version: v.n });
              toastOk(`Запрошено подтверждение владельца №${r.approval_id}. Решите его в «Подтверждениях», затем нажмите «Завершить».`);
              renderApp();
            } catch (e) { toastError(e); }
          }, { size: 'sm' })));
      }
      versionsHost.append(btn('Завершить продвижение (после подтверждения)', async () => {
        try {
          const r = await P(`${base}/apps/${app.slug}/promote/finish`, {});
          toastOk(r.status === 'promoted' ? `v${r.version} добавлена в список приложений` : 'Решение владельца ещё не принято');
          await refresh();
        } catch (e) { toastError(e); }
      }, { size: 'sm', variant: 'subtle' }));
      /* Песочница: sandbox без allow-same-origin; src — отдельный путь с CSP-заголовком. */
      const frame = h('iframe', { title: `Превью ${app.slug} v${state.version}`, sandbox: PREVIEW_SANDBOX,
        referrerpolicy: 'no-referrer', src: `${base}/apps/${app.slug}/versions/${state.version}/preview`,
        style: { width: '100%', height: '360px', border: '1px solid var(--bx-line, #888)', borderRadius: '8px', background: '#fff' } });
      previewHost.append(h('p', `Превью v${state.version} (песочница, сеть заблокирована политикой CSP)`), frame);
    }

    const rootPanels = h('div.bx-page.vibe', { dataset: { testid: 'vibe' } }, head,
      panel('Состояние', statusHost, { icon: 'activity' }),
      formPanel,
      panel('Ход работы', jobBox, { icon: 'play' }),
      panel('Приложения', h('div', { style: { display: 'grid', gap: '10px' } }, appsHost, versionsHost, diffHost), { icon: 'builder' }),
      panel('Превью', previewHost, { icon: 'browser' }),
      panel('История задач', historyHost));

    async function refresh() {
      try {
        const [st, jobs, apps] = await Promise.all([
          G(`${base}/status`), G(`${base}/jobs`), G(`${base}/apps`)]);
        state.status = st; state.jobs = jobs.jobs || []; state.apps = apps.apps || [];
        if (!state.current && state.jobs.length) state.current = state.jobs[0].job_id;
        renderStatus(); renderApps();
        historyHost.replaceChildren(...state.jobs.slice(0, 15).map((j) => h('div', { style: { display: 'flex', gap: '8px', alignItems: 'center' } },
          pill(STATUS_LABEL[j.status] || j.status, { tone: STATUS_TONE[j.status] || 'idle' }),
          btn(`${j.slug}: ${(j.request || '').slice(0, 50)}`, () => { state.current = j.job_id; state.cursor = 0; state.log = []; refresh(); }, { variant: 'subtle', size: 'sm' }))));
        if (state.current) {
          const ev = await G(`${base}/jobs/${state.current}/events?after=${state.cursor}`);
          for (const e of ev.events || []) { state.log.push(e); state.cursor = Math.max(state.cursor, e.n); }
          renderLog();
          renderJob(state.jobs.find((j) => j.job_id === state.current) || null);
        } else { renderJob(null); }
      } catch (e) {
        historyHost.replaceChildren(errorNote(e, refresh));
      }
    }

    await refresh();
    let misses = 0; let seen = false;
    const tick = async () => {
      if (!rootPanels.isConnected) {
        if (seen || (misses += 1) > 10) return;
      } else seen = true;
      if (!document.hidden) await refresh();
      const live = state.jobs.some((j) => LIVE.has(j.status));
      setTimeout(tick, live ? 1200 : 5000);
    };
    setTimeout(tick, 1200);
    return rootPanels;
  },

  onEvent() { return false; },
};

export default VibePage;
