/* ============================================================
   motion_workflow.js — панель «Рабочий процесс» внутри Motion Studio.
   Материалы (видео + @ссылки) -> анализ -> сцены -> правка -> запуск -> ход -> результат -> открыть позже.
   Тонкий клиент над bcc/features/motion_workflow_api.py (под /api/motion-workflow).
   Всё локально: ffmpeg на этом ПК, облака и платных вызовов нет.

   Честность интерфейса:
   - то, что неизвестно анализатору, показано как UNKNOWN (не как «нет»);
   - до запуска показаны лимиты, оценка машинного времени и список того, что рендерер НЕ выполнит;
   - «готово» только после проверки файла; не выполненные элементы перечислены с причиной.
   ============================================================ */

import { api } from '../api.js';
import { h, toastOk, toastError, input, textarea, select, checkbox } from '../components.js';
import { panel, pill, btn, field, segmented, bar, tag, errorNote } from './_ui.js';

const BASE = '/api/motion-workflow';
const POLL_MS = 1000;
const STORE_KEY = 'mw.project';
const ELEMENTS = ['face_character', 'clothes', 'background', 'product', 'music'];
const ELEMENT_LABEL = {
  face_character: 'Лицо / персонаж', clothes: 'Одежда', background: 'Фон', product: 'Товар', text: 'Текст', music: 'Музыка',
};
const ELEMENT_KIND = {
  face_character: 'character', clothes: 'clothing', background: 'background', product: 'product', music: 'audio', text: 'text',
};
const POLICY_LABEL = { keep: 'оставить', replace: 'заменить', unknown: 'не решено' };
const JOB_STATE = {
  planned: ['в очереди', 'idle'], running: ['идёт', 'warn'], preview_done: ['предпросмотр готов', 'ok'],
  done: ['готово и проверено', 'ok'], failed: ['ошибка', 'err'], interrupted: ['прервано', 'err'],
  cancelled: ['остановлено', 'idle'],
};
const STAGE_LABEL = {
  queued: 'в очереди', prepare: 'подготовка', preview: 'предпросмотр кадров', render: 'рендер (ffmpeg)',
  verify: 'проверка файла', cover: 'обложка', report: 'отчёт',
};
const REASON_RU = {
  needs_generative_tool: 'нужен инструмент лица / маски / генерации; этот локальный рендерер выполняет только ffmpeg, картинка сцены оставлена как в исходнике',
  cannot_separate_mixed_stream: 'исходный звук — один смешанный поток: речь и музыку по отдельности этот рендерер не разделяет, оставлен исходный звук',
  music_is_soundtrack_lane: 'музыка меняется на уровне всей дорожки (блок «Звук»), а не по сценам',
  no_text: 'нет текста для наложения',
  text_reference_unreadable: 'файл текстовой ссылки не читается',
  undecided: 'решение по элементу не принято',
  invalid_cue: 'у реплик неверное время или пустой текст',
  no_reference: 'выбрана замена, но не привязан @материал',
  two_references_not_supported: 'две разные замены звука (речь и музыка) не смешиваются: взята одна дорожка',
  video_lane_not_supported: 'режим видеодорожки не выполняется, картинка сохранена',
};

/* ---------------------------------------------------------------- мелочи */

const fmtSec = (s) => (s === null || s === undefined || Number.isNaN(Number(s)) ? '—' : `${Number(s).toFixed(1)} с`);
const isUnknown = (v) => typeof v === 'string' && v.startsWith('UNKNOWN');
const isLive = (job) => job && (job.state === 'running' || job.state === 'planned');
function fmtBytes(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return '—';
  if (v >= 1048576) return `${(v / 1048576).toFixed(1)} МБ`;
  if (v >= 1024) return `${(v / 1024).toFixed(0)} КБ`;
  return `${v} Б`;
}
const shortSha = (s) => (s ? `${String(s).slice(0, 12)}…` : '—');
const errText = (e) => {
  const problems = e && e.detail && Array.isArray(e.detail.problems) ? e.detail.problems.slice(0, 4).join('; ') : '';
  return problems ? `${e.message}: ${problems}` : (e && e.message) || String(e);
};
const remember = (v) => { try { if (v) sessionStorage.setItem(STORE_KEY, v); else sessionStorage.removeItem(STORE_KEY); } catch { /* приватный режим */ } };
const recall = () => { try { return sessionStorage.getItem(STORE_KEY) || ''; } catch { return ''; } };
const call = (path, opts) => api.raw(BASE + path, opts);

function unknownPill(value) {
  return h('span', { class: 'bx-tag', title: String(value), dataset: { unknown: '1' } }, 'UNKNOWN');
}

function ruReason(code, fallback) {
  return REASON_RU[code] || fallback || code || 'причина не указана';
}

function fileUrl(pid, jid, name) {
  return `${BASE}/projects/${encodeURIComponent(pid)}/jobs/${encodeURIComponent(jid)}/file?name=${encodeURIComponent(name)}`;
}

function parseCues(text) {
  const cues = [];
  const bad = [];
  String(text || '').split(/\r?\n/).forEach((line, index) => {
    if (!line.trim()) return;
    const m = line.match(/^\s*([0-9]+(?:[.,][0-9]+)?)\s*[-–—]\s*([0-9]+(?:[.,][0-9]+)?)\s+(.+?)\s*$/);
    if (!m) { bad.push(index + 1); return; }
    const start = Number(m[1].replace(',', '.'));
    const end = Number(m[2].replace(',', '.'));
    if (!(end > start)) { bad.push(index + 1); return; }
    cues.push({ start, end, text: m[3] });
  });
  return { cues, bad };
}

function cuesToText(cues) {
  return (cues || []).map((c) => `${Number(c.start).toFixed(1)}-${Number(c.end).toFixed(1)} ${c.text}`).join('\n');
}

/* ---------------------------------------------------------------- панель */

export async function mountWorkflow(ctx) {
  const root = h('div.stack.md', { dataset: { testid: 'mw-root' } });
  const S = { status: null, projects: [], pid: '', view: null, jobId: '', job: null, timer: null, uploading: false };

  const alive = () => root.isConnected;
  const stopPolling = () => { if (S.timer) { clearTimeout(S.timer); S.timer = null; } };

  /* ---- загрузка ---- */

  async function loadList() {
    S.projects = (await call('/projects')).projects || [];
  }

  async function openProject(pid, { keepJob = false } = {}) {
    stopPolling();
    S.view = await call(`/projects/${encodeURIComponent(pid)}`);
    S.pid = pid;
    remember(pid);
    const jobs = S.view.jobs || [];
    if (!keepJob || !jobs.some((j) => j.job_id === S.jobId)) {
      const active = jobs.find((j) => isLive(j));
      S.jobId = (active || jobs.find((j) => j.state === 'done') || jobs[0] || {}).job_id || '';
    }
    S.job = jobs.find((j) => j.job_id === S.jobId) || null;
    drawAll();
    if (S.job && isLive(S.job)) schedulePoll();
  }

  async function reload() {
    try { await loadList(); await openProject(S.pid, { keepJob: true }); } catch (e) { toastError(e); }
  }

  function schedulePoll() {
    stopPolling();
    S.timer = setTimeout(pollJob, POLL_MS);
  }

  async function pollJob() {
    S.timer = null;
    if (!alive() || !S.pid || !S.jobId) return;
    try {
      const job = await call(`/projects/${encodeURIComponent(S.pid)}/jobs/${encodeURIComponent(S.jobId)}`);
      if (!alive()) return;
      S.job = job;
      if (isLive(job)) {
        updateProgress(job);          // обновляет узлы на месте: кнопка «Остановить» не пересоздаётся каждую секунду
        schedulePoll();
      } else {
        await openProject(S.pid, { keepJob: true });   // финал: перечитать проект целиком
      }
    } catch (e) {
      if (alive()) { S.timer = setTimeout(pollJob, POLL_MS * 3); }
    }
  }

  function updateProgress(job) {
    const ui = S.ui;
    if (!ui || ui.jobId !== job.job_id) { drawRun(); return; }
    const pct = Math.round((job.progress || 0) * 100);
    if (ui.fill) ui.fill.style.width = `${pct}%`;
    ui.pctText.textContent = `${pct}% · ${STAGE_LABEL[job.stage] || job.stage || 'ожидание'}${job.progress_is_live ? '' : ' (последнее известное)'}`;
    ui.scenesText.textContent = `сцен выполнено: ${(job.completed && job.completed[job.phase || 'preview'] || []).length} из ${(job.scene_ids || []).length}`;
    if (ui.logNode && job.log_tail) ui.logNode.textContent = job.log_tail;
  }

  /* Пока идёт сохранение и перечитывание проекта, панель не принимает кликов: иначе действие, начатое
     в момент перерисовки, пропадало бы вместе с заменённым узлом. */
  let busyCount = 0;
  function setBusy(delta) {
    busyCount = Math.max(0, busyCount + delta);
    root.style.pointerEvents = busyCount ? 'none' : '';
    root.style.opacity = busyCount ? '0.7' : '';
    root.setAttribute('aria-busy', busyCount ? 'true' : 'false');
  }

  async function act(fn, okTitle) {
    setBusy(1);
    try { const r = await fn(); if (okTitle) toastOk(okTitle); return r; } catch (e) { toastError(new Error(errText(e))); return null; } finally { setBusy(-1); }
  }

  /* Правки идут по очереди: следующая берёт версию ПОСЛЕ перечитывания проекта предыдущей, иначе быстрая
     серия действий сама себе давала бы конфликт версий. Чужая правка по-прежнему даёт 409. */
  let editChain = Promise.resolve();
  function edit(ops, summary) {
    setBusy(1);
    const run = async () => {
      try {
        const r = await call(`/projects/${encodeURIComponent(S.pid)}/ops`, { method: 'POST',
          body: { ops, expected_version: S.view.version, summary: summary || undefined } });
        toastOk('Сохранено', r.changed ? `версия ${r.version}: ${r.summary}` : 'без изменений');
      } catch (e) {
        if (e && e.status === 409) toastError(new Error('Проект изменился в другом месте: данные обновлены, повторите правку'));
        else toastError(new Error(errText(e)));
      }
      await reload();
    };
    editChain = editChain.then(run, run).finally(() => setBusy(-1));
    return editChain;
  }

  const execLine = (c) => (c
    ? `запрошено операций ${c.requested}: будет выполнено ${c.applied}${c.not_executed ? `, НЕ будет выполнено ${c.not_executed}` : ''}`
    : '—');

  /* ---- секции ---- */

  const secProjects = h('div', { dataset: { testid: 'mw-sec-projects' } });
  const secMaterials = h('div', { dataset: { testid: 'mw-sec-materials' } });
  const secLimits = h('div', { dataset: { testid: 'mw-sec-limits' } });
  const secScenes = h('div', { dataset: { testid: 'mw-sec-scenes' } });
  const secEdit = h('div', { dataset: { testid: 'mw-sec-edit' } });
  const secRun = h('div', { dataset: { testid: 'mw-sec-run' } });
  const secResult = h('div', { dataset: { testid: 'mw-sec-result' } });
  const secVersions = h('div', { dataset: { testid: 'mw-sec-versions' } });
  root.append(secProjects, secMaterials, secLimits, secScenes, secEdit, secRun, secResult, secVersions);

  function drawAll() {
    drawProjects();
    const has = !!S.view;
    for (const sec of [secMaterials, secLimits, secScenes, secEdit, secRun, secResult, secVersions]) sec.replaceChildren();
    if (!has) return;
    drawMaterials();
    drawLimits();
    drawScenes();
    drawEdit();
    drawRun();
    drawResult();
    drawVersions();
  }

  /* ---- проекты ---- */

  function drawProjects() {
    const st = S.status;
    const renderer = st && st.renderer;
    const notes = [];
    if (renderer) {
      notes.push(pill(`ffmpeg: ${renderer.ffmpeg ? 'есть' : 'нет'}`, { tone: renderer.ffmpeg ? 'ok' : 'err' }));
      notes.push(pill(`ffprobe: ${renderer.ffprobe ? 'есть' : 'нет'}`, { tone: renderer.ffprobe ? 'ok' : 'err' }));
      notes.push(pill(`текст на видео: ${renderer.text_ready ? 'есть' : 'нет'}`, { tone: renderer.text_ready ? 'ok' : 'warn' }));
    }
    const problems = renderer && renderer.problems && renderer.problems.length
      ? h('div.small', { style: { color: 'var(--err)' }, dataset: { testid: 'mw-renderer-problems' } },
        `Рендер недоступен: ${renderer.problems.join('; ')}. Готового файла не будет, пока это не исправлено.`) : null;

    const select1 = select([{ value: '', label: S.projects.length ? '— выберите проект —' : '— проектов пока нет —' },
      ...S.projects.map((p) => ({ value: p.project_id,
        label: `${p.title || p.project_id} · v${p.version}${p.last_job ? ` · ${(JOB_STATE[p.last_job.state] || [p.last_job.state])[0]}` : ''}${p.source_staged ? ' · видео ждёт анализа' : ''}` }))],
    { value: S.pid, name: 'mw-project', dataset: { testid: 'mw-project-select' }, style: { maxWidth: '100%', minWidth: 0 } });
    const openBtn = btn('Открыть проект', async () => {
      if (!select1.value) { toastError(new Error('Выберите проект в списке')); return; }
      try { await openProject(select1.value); } catch (e) { toastError(new Error(errText(e))); }
    }, { variant: 'secondary', size: 'sm' });
    openBtn.dataset.testid = 'mw-open';

    const title = input({ placeholder: 'название проекта (необязательно)', name: 'mw-title', dataset: { testid: 'mw-new-title' } });
    const file = h('input', { type: 'file', accept: 'video/*,.mp4,.mov,.mkv,.webm,.avi,.m4v', dataset: { testid: 'mw-upload-file' },
      'aria-label': 'Исходное видео' });
    const startBtn = btn('Загрузить видео и начать', async () => {
      const f = file.files && file.files[0];
      if (!f) { toastError(new Error('Выберите видеофайл')); return; }
      S.uploading = true;
      startBtn.disabled = true;
      try {
        const q = `?name=${encodeURIComponent(f.name)}&title=${encodeURIComponent(title.value || f.name.replace(/\.[^.]+$/, ''))}`;
        const view = await api.raw(`${BASE}/projects${q}`, { method: 'POST', body: f });
        toastOk('Видео загружено', 'Теперь запустите анализ');
        remember(view.project.project_id);
        await loadList();
        await openProject(view.project.project_id);
      } catch (e) { toastError(new Error(errText(e))); } finally { S.uploading = false; startBtn.disabled = false; }
    }, { variant: 'primary', size: 'sm' });
    startBtn.dataset.testid = 'mw-create';

    secProjects.replaceChildren(panel('Рабочий процесс: проекты',
      h('div.stack.sm',
        h('p.small.dim', 'Загрузите своё видео, разберите его на сцены, поправьте текст, звук и обложку, запустите локальный рендер и получите проверенный файл с отчётом. '
          + 'Облако и платные вызовы не используются (0 USD).'),
        h('div.row.tight', { style: { flexWrap: 'wrap', gap: '8px' } }, ...notes),
        problems,
        h('div.row.tight', { style: { flexWrap: 'wrap', gap: '8px', alignItems: 'flex-end' } },
          field('Открыть сохранённый проект', select1), openBtn),
        h('div.row.tight', { style: { flexWrap: 'wrap', gap: '8px', alignItems: 'flex-end' } },
          field('Название', title), field('Исходное видео', file), startBtn))));
  }

  /* ---- материалы ---- */

  function drawMaterials() {
    const v = S.view;
    const src = v.source || {};
    const info = src.info || {};
    const nodes = [];
    if (src.analyzed) {
      nodes.push(h('div.stack.sm', { dataset: { testid: 'mw-source' } },
        h('div.row.tight', { style: { flexWrap: 'wrap', gap: '8px' } },
          pill('исходное видео', { tone: 'ok' }),
          h('span.small', `${info.file_name || '—'} · ${fmtSec(info.duration)} · ${info.display_width || info.width}×${info.display_height || info.height} · `
            + `${info.fps} к/с · звук: ${info.has_audio ? (info.audio_codec || 'есть') : 'нет'} · ${fmtBytes(info.bytes)} · sha256 ${shortSha(info.asset)}`)),
        src.file_url ? h('video', { src: src.file_url, controls: true, preload: 'metadata', style: { maxWidth: '420px', width: '100%', borderRadius: '6px' },
          dataset: { testid: 'mw-source-video' } }) : null));
    } else if (src.staged) {
      nodes.push(h('div.small', { dataset: { testid: 'mw-source' } }, `Загружено: ${src.staged.file} (${fmtBytes(src.staged.bytes)}). Анализ ещё не запускался.`));
    } else {
      nodes.push(h('div.small.dim', { dataset: { testid: 'mw-source' } }, 'Исходного видео в проекте нет: загрузите его выше.'));
    }
    const analyzeBtn = btn(src.analyzed ? 'Анализировать заново' : 'Анализировать', async () => {
      await act(async () => {
        analysisBox.textContent = 'Анализ идёт…';
        const poll = setInterval(async () => {
          try {
            const a = await call(`/projects/${encodeURIComponent(S.pid)}/analysis`);
            if (a.state === 'running') analysisBox.textContent = `Анализ: ${a.stage} ${Math.round((a.fraction || 0) * 100)}%`;
          } catch { /* ход анализа — только подсказка */ }
        }, 700);
        try {
          await call(`/projects/${encodeURIComponent(S.pid)}/analyze`, { method: 'POST', body: {} });
        } finally { clearInterval(poll); }
        toastOk('Анализ готов', 'Сцены и факты записаны; то, что не измерено, помечено UNKNOWN');
        await reload();
      });
    }, { variant: 'primary', size: 'sm', disabled: !(src.analyzed || src.staged) });
    analyzeBtn.dataset.testid = 'mw-analyze';
    const analysisBox = h('span.small.dim', { dataset: { testid: 'mw-analysis-state' } },
      (v.analysis && v.analysis.state === 'failed') ? `Анализ не удался: ${v.analysis.error}` : '');
    const warnings = ((v.plan.analysis || {}).warnings || []);
    nodes.push(h('div.row.tight', { style: { gap: '8px', flexWrap: 'wrap', alignItems: 'center' } }, analyzeBtn, analysisBox));
    if (warnings.length) {
      nodes.push(h('div.small', { style: { color: 'var(--warn)' }, dataset: { testid: 'mw-analysis-warnings' } },
        `Предупреждения анализа: ${warnings.slice(0, 4).join(' · ')}`));
    }

    // материалы-ссылки
    const refs = v.references || [];
    const refTable = refs.length
      ? h('div', { style: { overflowX: 'auto', maxWidth: '100%' } },
        h('table.fi-table', { dataset: { testid: 'mw-refs' } },
          h('thead', h('tr', ...['@имя', 'вид', 'файл', 'размер', 'где используется', ''].map((t) => h('th', t)))),
          h('tbody', ...refs.map((r) => h('tr', { dataset: { ref: r.name } },
            h('td', `@${r.name}`), h('td', r.kind_label || r.kind), h('td', r.original_name || `${r.sha256.slice(0, 8)}${r.ext}`),
            h('td', fmtBytes(r.bytes)),
            h('td', [...(r.used_by_scenes || []).map((s) => `сцена ${s}`), ...(r.used_by_lanes || []).map((l) => `дорожка ${l}`)].join(', ') || '—'),
            h('td', btn('Убрать', async () => {
              await act(async () => { await call(`/projects/${encodeURIComponent(S.pid)}/references/${encodeURIComponent(r.name)}`, { method: 'DELETE' }); await reload(); });
            }, { variant: 'ghost', size: 'sm' })))))))
      : h('div.small.dim', 'Материалов-ссылок пока нет (@персонаж, @товар, @музыка, @текст).');
    const refName = input({ placeholder: 'имя без @, латиница', name: 'mw-ref-name', dataset: { testid: 'mw-ref-name' }, maxlength: '32' });
    const refKind = select([{ value: 'audio', label: 'аудио (музыка, голос)' }, { value: 'character', label: 'персонаж (фото)' },
      { value: 'product', label: 'товар (фото)' }, { value: 'clothing', label: 'одежда (фото)' },
      { value: 'background', label: 'фон (фото)' }], { value: 'audio', name: 'mw-ref-kind', dataset: { testid: 'mw-ref-kind' } });
    const refFile = h('input', { type: 'file', dataset: { testid: 'mw-ref-file' }, 'aria-label': 'Файл материала' });
    const bindBtn = btn('Привязать материал', async () => {
      const f = refFile.files && refFile.files[0];
      const name = (refName.value || '').trim().replace(/^@/, '');
      if (!f || !name) { toastError(new Error('Выберите файл и задайте имя')); return; }
      await act(async () => {
        await api.raw(`${BASE}/projects/${encodeURIComponent(S.pid)}/references?name=${encodeURIComponent(f.name)}&kind=${encodeURIComponent(refKind.value)}&ref=${encodeURIComponent(name)}`,
          { method: 'POST', body: f });
        toastOk(`@${name} привязан`);
        await reload();
      });
    }, { variant: 'secondary', size: 'sm' });
    bindBtn.dataset.testid = 'mw-ref-add';
    const textName = input({ placeholder: 'имя текста, например title', name: 'mw-text-name', dataset: { testid: 'mw-text-name' }, maxlength: '32' });
    const textBody = textarea({ rows: 2, placeholder: 'Текст, который можно подставлять в сцены как @имя', name: 'mw-text-body', dataset: { testid: 'mw-text-body' } });
    const bindText = btn('Привязать текст', async () => {
      const name = (textName.value || '').trim().replace(/^@/, '');
      if (!name || !textBody.value.trim()) { toastError(new Error('Задайте имя и текст')); return; }
      await act(async () => {
        await call(`/projects/${encodeURIComponent(S.pid)}/references`, { method: 'POST', body: { name, text: textBody.value } });
        toastOk(`@${name} привязан`);
        await reload();
      });
    }, { variant: 'secondary', size: 'sm' });
    bindText.dataset.testid = 'mw-text-add';
    nodes.push(h('div.stack.sm', refTable,
      h('div.row.tight', { style: { flexWrap: 'wrap', gap: '8px', alignItems: 'flex-end' } },
        field('Имя', refName), field('Вид', refKind), field('Файл', refFile), bindBtn),
      h('div.row.tight', { style: { flexWrap: 'wrap', gap: '8px', alignItems: 'flex-end' } },
        field('Имя', textName), field('Текст', textBody), bindText)));
    secMaterials.replaceChildren(panel('Материалы и анализ', h('div.stack.sm', ...nodes)));
  }

  /* ---- лимиты и оценка (до запуска) ---- */

  function drawLimits() {
    const v = S.view;
    const usage = (v.limits && v.limits.usage) || {};
    const lim = (v.limits && v.limits.limits) || {};
    const rows = [
      ['материалы-ссылки', usage.references && `${usage.references.used} из ${usage.references.max}`],
      ['размер материалов', usage.reference_bytes && `${fmtBytes(usage.reference_bytes.used)} из ${fmtBytes(usage.reference_bytes.max)}`],
      ['длительность видео', usage.duration_seconds && `${usage.duration_seconds.used === null ? '—' : fmtSec(usage.duration_seconds.used)} из ${fmtSec(usage.duration_seconds.max)}`],
      ['сцены', usage.scenes && `${usage.scenes.used} из ${usage.scenes.max}`],
      ['исходное видео, байт', lim.max_source_bytes ? `до ${fmtBytes(lim.max_source_bytes)}` : null],
    ].filter((r) => r[1]);
    const est = v.estimate || {};
    const estx = v.estimate_executed || est;
    const ep = v.execution_preview || {};
    const ready = v.readiness || {};
    const blockers = [...(ready.blockers || []), ...(ready.render_blockers || [])];
    const notExec = ep.not_executed || [];
    const executed = (v.capabilities && v.capabilities.executed) || [];
    const unsupported = (v.capabilities && v.capabilities.not_executed) || [];
    secLimits.replaceChildren(panel('Лимиты, оценка и что будет выполнено (до запуска)',
      h('div.stack.sm',
        h('div', { style: { overflowX: 'auto', maxWidth: '100%' } },
          h('table.fi-table', { dataset: { testid: 'mw-limits' } }, h('tbody', ...rows.map(([k, val]) => h('tr', h('td', k), h('td', val)))))),
        (v.limits && v.limits.violations && v.limits.violations.length)
          ? h('div.small', { style: { color: 'var(--err)' } }, `Лимиты превышены: ${v.limits.violations.join('; ')}`) : null,
        h('div.small', { dataset: { testid: 'mw-estimate' } },
          `Оценка машинного времени (объявленные константы модели, не замер): то, что исполнит этот рендерер — предпросмотр ≈ ${fmtSec(estx.preview_seconds)}, полный рендер ≈ ${fmtSec(estx.render_seconds)}`
          + `${(est.render_seconds || 0) - (estx.render_seconds || 0) > 0.5 ? `; по полной модели плана (с операциями, которые здесь не выполняются) ≈ ${fmtSec(est.render_seconds)}` : ''}. `
          + `Облако: ${est.cloud_spend_usd === 0 ? '0 USD' : est.cloud_spend_usd}, платных вызовов: ${est.paid_calls === 0 ? '0' : est.paid_calls}.`),
        h('div.small', { dataset: { testid: 'mw-execution-preview' } },
          `План выполнения: ${execLine(ep.counts)}. Наложений текста: ${ep.overlays ?? 0}. Звук: ${ep.audio ? ({ keep: 'оставить исходный', replace: 'заменить дорожкой', mute: 'без звука', none: 'в файле нет звука' }[ep.audio.action] || ep.audio.action) : '—'}.`),
        notExec.length
          ? h('div.stack.sm', { dataset: { testid: 'mw-not-executed-preview' }, style: { borderLeft: '3px solid var(--warn)', paddingLeft: '10px' } },
            h('div.small', { style: { fontWeight: 600 } }, `Не будет выполнено (${notExec.length}):`),
            ...notExec.slice(0, 12).map((o) => h('div.small', `${o.scene_id ? `сцена ${o.scene_id}, ` : ''}${ELEMENT_LABEL[o.element] || o.lane || o.kind}: ${ruReason(o.reason_code, o.reason)}`)),
            notExec.length > 12 ? h('div.small.dim', `…и ещё ${notExec.length - 12}`) : null) : null,
        blockers.length
          ? h('div.stack.sm', { dataset: { testid: 'mw-readiness' }, style: { borderLeft: '3px solid var(--err)', paddingLeft: '10px' } },
            h('div.small', { style: { fontWeight: 600 } }, 'Запуск заблокирован:'),
            ...blockers.slice(0, 8).map((b) => h('div.small', b)))
          : h('div.small', { dataset: { testid: 'mw-readiness' }, style: { color: 'var(--ok)' } }, 'Проект готов к запуску.'),
        h('details', h('summary.small', 'Что умеет этот рендерер'),
          h('div.stack.sm',
            ...executed.map((c) => h('div.small', `выполняет: ${c.title}`)),
            ...unsupported.map((c) => h('div.small.dim', `не выполняет (${c.id}): ${ruReason(c.reason_code, c.reason)}`)))),
        v.unknowns && v.unknowns.count
          ? h('div.small.dim', { dataset: { testid: 'mw-unknowns' } },
            `Не измерено (UNKNOWN): ${v.unknowns.count} полей. Они остаются UNKNOWN и не выдумываются; такие части исходника сохраняются как есть.`) : null)));
  }

  /* ---- сцены ---- */

  function sceneCells(scene) {
    const cut = scene.cut_in || {};
    const cam = scene.camera_motion || {};
    const txt = scene.text || {};
    const aud = scene.audio || {};
    const unknownOr = (value, reason, label) => (value === 'unknown' || isUnknown(reason) ? unknownPill(reason || value) : label);
    const boxes = (txt.boxes || []).length;
    const textCell = isUnknown(txt.status) ? unknownPill(txt.status)
      : (boxes ? `${boxes}: ${txt.boxes.slice(0, 2).map((b) => b.text).join(' / ')}` : 'нет');
    const audioCell = aud.has_audio_stream === false ? 'нет звука'
      : `${Math.round((aud.audible_ratio || 0) * 100)}% слышно${isUnknown(aud.speech_present) ? ' · речь UNKNOWN' : ''}${isUnknown(aud.music_present) ? ' · музыка UNKNOWN' : ''}`;
    return {
      cut: unknownOr(cut.type, cut.reason, `${cut.type}`),
      camera: unknownOr(cam.class, cam.reason, `${cam.class}${cam.direction ? ` (${cam.direction})` : ''}`),
      text: textCell, audio: audioCell,
    };
  }

  function drawScenes() {
    const v = S.view;
    const plan = v.plan;
    const scenes = plan.scenes || [];
    if (!scenes.length) {
      secScenes.replaceChildren(panel('Сцены', h('div.small.dim', { dataset: { testid: 'mw-scenes-empty' } },
        'Сцен пока нет: загрузите видео и запустите анализ.')));
      return;
    }
    const refsByKind = {};
    (v.references || []).forEach((r) => { (refsByKind[r.kind] = refsByKind[r.kind] || []).push(r); });
    const head = ['Сцена', 'Время', 'Переход', 'Камера', 'Текст в кадре', 'Звук', ...ELEMENTS.filter((e) => e !== 'music').map((e) => ELEMENT_LABEL[e]), 'Музыка', 'Текст поверх сцены']
      .map((t) => h('th', t));
    const rows = scenes.map((scene) => {
      const c = sceneCells(scene);
      const el = scene.elements || {};
      const policyCell = (name) => {
        const e = el[name] || { policy: 'unknown' };
        const sel = select(Object.entries(POLICY_LABEL).map(([value, label]) => ({ value, label })), {
          value: e.policy, dataset: { testid: `mw-policy-${scene.id}-${name}` }, 'aria-label': `${ELEMENT_LABEL[name]}, сцена ${scene.id}`,
          style: { minWidth: '112px' } });
        sel.addEventListener('change', async () => {
          await edit([{ op: 'set_policy', scene: scene.id, element: name, policy: sel.value, reason: 'выбор владельца в Motion Studio',
            with: sel.value === 'replace' && withSel.value ? withSel.value : undefined }]);
        });
        const kind = ELEMENT_KIND[name];
        const options = [{ value: '', label: '— без @ссылки —' }, ...((refsByKind[kind] || []).map((r) => ({ value: `@${r.name}`, label: `@${r.name}` })))];
        const withSel = select(options, { value: e.with || '', dataset: { testid: `mw-with-${scene.id}-${name}` }, 'aria-label': `Чем заменить: ${ELEMENT_LABEL[name]}, сцена ${scene.id}`,
          style: { minWidth: '112px' } });
        withSel.addEventListener('change', async () => {
          if (sel.value !== 'replace') return;
          await edit([{ op: 'set_policy', scene: scene.id, element: name, policy: 'replace', reason: 'выбор владельца в Motion Studio', with: withSel.value || undefined }]);
        });
        const generative = name !== 'music' && e.policy === 'replace';
        return h('td', h('div.stack.sm', sel, e.policy === 'replace' ? withSel : null,
          generative ? tag('не выполняется этим рендерером', { accent: false }) : null));
      };
      const textEl = el.text || { policy: 'keep' };
      const textVal = textEl.policy === 'replace' ? (scene.prompt || '') : '';
      const textInp = input({ value: textVal, placeholder: 'пусто = не менять', maxlength: '400',
        dataset: { testid: `mw-scene-text-${scene.id}` }, 'aria-label': `Текст поверх сцены ${scene.id}` });
      const textApply = btn('Применить', async () => {
        const val = textInp.value.trim();
        if (val) {
          await edit([{ op: 'set_policy', scene: scene.id, element: 'text', policy: 'replace', reason: 'текст поверх сцены от владельца' },
            { op: 'set_prompt', scene: scene.id, prompt: val }]);
        } else {
          await edit([{ op: 'set_policy', scene: scene.id, element: 'text', policy: 'keep', reason: 'текст поверх сцены убран владельцем' },
            { op: 'set_prompt', scene: scene.id, prompt: '' }]);
        }
      }, { variant: 'subtle', size: 'sm' });
      textApply.dataset.testid = `mw-scene-text-apply-${scene.id}`;
      return h('tr', { dataset: { testid: `mw-scene-row-${scene.id}`, scene: scene.id } },
        h('td', scene.id), h('td', `${scene.start.toFixed(1)}–${scene.end.toFixed(1)} с`),
        h('td', c.cut), h('td', c.camera), h('td', c.text), h('td', c.audio),
        policyCell('face_character'), policyCell('clothes'), policyCell('background'), policyCell('product'), policyCell('music'),
        h('td', h('div.stack.sm', textInp, textApply)));
    });
    secScenes.replaceChildren(panel(`Сцены (${scenes.length})`,
      h('div.stack.sm',
        h('p.small.dim', 'Неизмеренное показано как UNKNOWN. «Не решено» блокирует запуск: выберите «оставить» или «заменить». '
          + 'Замена лица, одежды, фона и товара требует отдельных инструментов: этот рендерер её не выполняет и скажет об этом в результате.'),
        h('div', { style: { overflowX: 'auto', maxWidth: '100%' } },
          h('table.fi-table', { dataset: { testid: 'mw-scenes-table' }, style: { minWidth: '1180px' } },
            h('thead', h('tr', ...head)), h('tbody', ...rows))))));
  }

  /* ---- правка: звук, субтитры, обложка ---- */

  function drawEdit() {
    const v = S.view;
    const plan = v.plan;
    const audioRefs = (v.references || []).filter((r) => r.kind === 'audio');
    const music = (plan.tracks || {}).music || {};
    const speech = (plan.tracks || {}).speech || {};
    const opts = plan.audio_options || {};
    const currentMode = music.mode === 'replace' ? 'replace'
      : (music.mode === 'mute' && speech.mode === 'mute') ? 'mute' : 'keep';
    let mode = currentMode;
    const currentRef = (music.source || '').replace(/^@/, '');
    const refSel = select(audioRefs.length ? audioRefs.map((r) => ({ value: r.name, label: `@${r.name}` })) : [{ value: '', label: 'нет аудио-материалов' }],
      { value: audioRefs.some((r) => r.name === currentRef) ? currentRef : (audioRefs[0] || {}).name || '',
        dataset: { testid: 'mw-audio-ref' }, 'aria-label': 'Аудио для замены' });
    const loud = checkbox('Выровнять громкость (-16 LUFS, двухпроходно)', opts.loudnorm !== false, { dataset: { testid: 'mw-audio-loudnorm' } });
    const fadeIn = input({ type: 'number', min: '0', max: '30', step: '0.1', value: String(opts.fade_in ?? 0.3), dataset: { testid: 'mw-audio-fade-in' }, 'aria-label': 'Нарастание, с' });
    const fadeOut = input({ type: 'number', min: '0', max: '30', step: '0.1', value: String(opts.fade_out ?? 0.8), dataset: { testid: 'mw-audio-fade-out' }, 'aria-label': 'Затухание, с' });
    const refStart = input({ type: 'number', min: '0', step: '0.5', value: String(opts.ref_start ?? 0), dataset: { testid: 'mw-audio-start' }, 'aria-label': 'Начать с секунды' });
    const seg = segmented([{ value: 'keep', label: 'оставить звук' }, { value: 'replace', label: 'заменить дорожкой' }, { value: 'mute', label: 'без звука' }],
      currentMode, (value) => { mode = value; });
    seg.dataset.testid = 'mw-audio-mode';
    const applyAudio = btn('Применить звук', async () => {
      if (mode === 'replace' && !refSel.value) { toastError(new Error('Сначала привяжите аудио-материал')); return; }
      const op = { op: 'set_soundtrack', mode };
      if (mode === 'replace') {
        op.reference = refSel.value;
        op.options = { loudnorm: loud.querySelector('input').checked, fade_in: Number(fadeIn.value) || 0,
          fade_out: Number(fadeOut.value) || 0, ref_start: Number(refStart.value) || 0 };
      }
      await edit([op], `звук: ${mode}`);
    }, { variant: 'secondary', size: 'sm' });
    applyAudio.dataset.testid = 'mw-audio-apply';
    const audioPreview = (v.execution_preview || {}).audio || {};

    // субтитры
    const subs = (plan.tracks || {}).subtitles || {};
    const subsText = textarea({ rows: 4, placeholder: '0.5-3.0 Первая реплика\n3.5-6.0 Вторая реплика', name: 'mw-subs',
      dataset: { testid: 'mw-subs-text' } });
    subsText.value = cuesToText(subs.cues);
    const subsInfo = h('span.small.dim', `Реплик в плане: ${(subs.cues || []).length}${subs.enabled ? '' : ' (выключены)'}`);
    const applySubs = btn('Применить субтитры', async () => {
      const { cues, bad } = parseCues(subsText.value);
      if (bad.length) { toastError(new Error(`Не разобраны строки: ${bad.slice(0, 6).join(', ')}. Формат: «0.5-3.0 текст»`)); return; }
      await edit([{ op: 'set_track', lane: 'subtitles', settings: { enabled: cues.length > 0, cues } }], 'субтитры');
    }, { variant: 'secondary', size: 'sm' });
    applySubs.dataset.testid = 'mw-subs-apply';

    // обложка
    const cover = plan.cover || {};
    const coverTime = input({ type: 'number', min: '0', step: '0.1', placeholder: 'сек (пусто = середина)', value: cover.time === null || cover.time === undefined ? '' : String(cover.time),
      dataset: { testid: 'mw-cover-time' }, 'aria-label': 'Секунда обложки' });
    const coverTitle = input({ value: cover.title || '', placeholder: 'заголовок на обложке', maxlength: '120', dataset: { testid: 'mw-cover-title' }, 'aria-label': 'Заголовок обложки' });
    const coverPos = select([{ value: 'top', label: 'сверху' }, { value: 'center', label: 'по центру' }, { value: 'bottom', label: 'снизу' }],
      { value: cover.position || 'center', dataset: { testid: 'mw-cover-position' }, 'aria-label': 'Положение заголовка' });
    const applyCover = btn('Задать обложку', async () => {
      const t = coverTime.value === '' ? null : Number(coverTime.value);
      await edit([{ op: 'set_cover', time: t, title: coverTitle.value, position: coverPos.value }], 'обложка');
    }, { variant: 'secondary', size: 'sm' });
    applyCover.dataset.testid = 'mw-cover-apply';
    const clearCover = btn('Сбросить обложку', async () => { await edit([{ op: 'set_cover', clear: true }], 'обложка сброшена'); },
      { variant: 'ghost', size: 'sm' });
    clearCover.dataset.testid = 'mw-cover-clear';

    secEdit.replaceChildren(
      panel('Звук', h('div.stack.sm',
        seg,
        h('div.row.tight', { style: { flexWrap: 'wrap', gap: '8px', alignItems: 'flex-end' } },
          field('Дорожка для замены', refSel), field('Начать с, с', refStart), field('Нарастание, с', fadeIn), field('Затухание, с', fadeOut)),
        loud,
        h('div.small.dim', { dataset: { testid: 'mw-audio-note' } },
          `Сейчас: ${{ keep: 'исходный звук', replace: `дорожка @${audioPreview.reference || ''}`, mute: 'без звука', none: 'в исходнике нет звука' }[audioPreview.action] || audioPreview.action || '—'}. `
          + 'Исходная дорожка — один смешанный поток: раздельно менять речь и музыку нельзя.'),
        (audioPreview.not_executed || []).length
          ? h('div.small', { style: { color: 'var(--warn)' } }, (audioPreview.not_executed || []).map((n) => `${n.lane}: ${ruReason(n.reason_code, n.reason)}`).join(' · ')) : null,
        h('div.row.tight', applyAudio))),
      panel('Субтитры', h('div.stack.sm', field('Реплики: «начало-конец текст», по одной в строке', subsText), subsInfo,
        h('div.row.tight', applySubs))),
      panel('Обложка', h('div.stack.sm',
        h('div.row.tight', { style: { flexWrap: 'wrap', gap: '8px', alignItems: 'flex-end' } },
          field('Секунда кадра', coverTime), field('Заголовок', coverTitle), field('Положение', coverPos)),
        h('div.row.tight', { style: { gap: '8px' } }, applyCover, clearCover),
        h('div.small.dim', plan.cover ? `Задано: кадр ${cover.time === null ? 'из середины' : fmtSec(cover.time)}${cover.title ? `, заголовок «${cover.title}»` : ''}`
          : 'Не задано: будет взят кадр из середины видео без заголовка.'))));
  }

  /* ---- запуск и ход ---- */

  function drawRun() {
    const v = S.view;
    if (!v) return;
    const can = !!(v.readiness && v.readiness.can_run);
    const running = !!(S.job && isLive(S.job)) || !!v.active_job;
    const telegram = checkbox('Ещё и вариант для Telegram (1280 px)', false, { dataset: { testid: 'mw-telegram' } });
    const start = (mode) => async () => {
      await act(async () => {
        const job = await call(`/projects/${encodeURIComponent(S.pid)}/jobs`, { method: 'POST',
          body: { mode, telegram_variant: telegram.querySelector('input').checked } });
        S.jobId = job.job_id;
        S.job = job;
        toastOk(mode === 'full' ? 'Полный рендер запущен' : 'Предпросмотр запущен');
        await openProject(S.pid, { keepJob: true });
      });
    };
    const preview = btn('Предпросмотр кадров', start('preview'), { variant: 'secondary', disabled: !can || running });
    preview.dataset.testid = 'mw-run-preview';
    const full = btn('Полный рендер', start('full'), { variant: 'primary', disabled: !can || running });
    full.dataset.testid = 'mw-run-full';
    const job = S.job;
    const body = [h('div.row.tight', { style: { gap: '8px', flexWrap: 'wrap', alignItems: 'center' } }, preview, full, telegram)];
    if (!can) {
      const why = [...((v.readiness || {}).blockers || []), ...((v.readiness || {}).render_blockers || [])];
      body.push(h('div.small.dim', `Запуск недоступен: ${why[0] || 'проект не готов'}${why.length > 1 ? ` (и ещё ${why.length - 1})` : ''}`));
    }
    S.ui = null;
    if (job) {
      const [label, tone] = JOB_STATE[job.state] || [job.state, 'idle'];
      const pct = Math.round((job.progress || 0) * 100);
      const barNode = bar(pct, 100);
      const pctText = h('span.small', `${pct}% · ${STAGE_LABEL[job.stage] || job.stage || 'ожидание'}${job.progress_is_live ? '' : ' (последнее известное)'}`);
      const scenesText = h('div.small.dim', `сцен выполнено: ${(job.completed && job.completed[job.phase || 'preview'] || []).length} из ${(job.scene_ids || []).length}`);
      const logNode = job.log_tail ? h('pre.xsmall.mono', { style: { whiteSpace: 'pre-wrap', maxHeight: '160px', overflow: 'auto' }, dataset: { testid: 'mw-log' } }, job.log_tail) : null;
      S.ui = { fill: barNode.querySelector('i'), pctText, scenesText, logNode, jobId: job.job_id };
      const card = h('div.stack.sm', { dataset: { testid: 'mw-progress', job: job.job_id, state: job.state } },
        h('div.row.tight', { style: { gap: '8px', flexWrap: 'wrap', alignItems: 'center' } },
          pill(label, { tone, live: job.state === 'running' }),
          h('span.small', `задача ${job.job_id} · план v${job.plan_version} · ${job.mode === 'full' ? 'полный рендер' : 'предпросмотр'}${job.attempt > 1 ? ` · попытка ${job.attempt}` : ''}`)),
        job.state === 'running' || job.state === 'preview_done' || job.state === 'planned'
          ? h('div.stack.sm', barNode, pctText) : null,
        scenesText);
      if (isLive(job)) {
        const stop = btn('Остановить', async () => {
          await act(async () => { await call(`/projects/${encodeURIComponent(S.pid)}/jobs/${encodeURIComponent(job.job_id)}/cancel`, { method: 'POST', body: {} }); await reload(); }, 'Остановлено');
        }, { variant: 'danger', size: 'sm' });
        stop.dataset.testid = 'mw-cancel';
        card.append(stop);
      }
      if (job.state === 'interrupted') {
        const resume = btn('Возобновить', async () => {
          await act(async () => { await call(`/projects/${encodeURIComponent(S.pid)}/jobs/${encodeURIComponent(job.job_id)}/resume`, { method: 'POST', body: {} }); await reload(); }, 'Возобновлено');
        }, { variant: 'primary', size: 'sm' });
        resume.dataset.testid = 'mw-resume';
        card.append(h('div.small', { style: { color: 'var(--err)' } },
          `Задача прервана: ${(job.interrupted && job.interrupted.reason) || 'причина не записана'}. Файл не считается готовым.`), resume);
      }
      if (job.state === 'preview_done') {
        const go = btn('Продолжить: полный рендер', async () => {
          await act(async () => { await call(`/projects/${encodeURIComponent(S.pid)}/jobs/${encodeURIComponent(job.job_id)}/render`, { method: 'POST', body: {} }); await reload(); }, 'Полный рендер запущен');
        }, { variant: 'primary', size: 'sm', disabled: running });
        go.dataset.testid = 'mw-continue-render';
        card.append(go);
      }
      if (job.state === 'failed') card.append(h('div.small', { style: { color: 'var(--err)' }, dataset: { testid: 'mw-job-error' } }, `Ошибка: ${job.error || 'причина не записана'}`));
      if (logNode) card.append(logNode);
      body.push(card);
    } else {
      body.push(h('div.small.dim', 'Запусков пока не было.'));
    }
    secRun.replaceChildren(panel('Запуск и ход', h('div.stack.sm', ...body)));
    if (S.job && S.job.state !== 'running') drawResult();
  }

  /* ---- результат ---- */

  function drawResult() {
    const v = S.view;
    const job = S.job;
    if (!v) return;
    const jobs = v.jobs || [];
    const picker = jobs.length > 1
      ? select(jobs.map((j) => ({ value: j.job_id, label: `${j.job_id} · ${(JOB_STATE[j.state] || [j.state])[0]} · план v${j.plan_version}` })),
        { value: S.jobId, dataset: { testid: 'mw-job-select' }, 'aria-label': 'Задача' }) : null;
    if (picker) {
      picker.addEventListener('change', () => { S.jobId = picker.value; S.job = jobs.find((j) => j.job_id === S.jobId) || null; drawRun(); drawResult(); });
    }
    if (!job || job.state === 'running' || job.state === 'planned') {
      secResult.replaceChildren(panel('Результат', h('div.stack.sm', picker,
        h('div.small.dim', { dataset: { testid: 'mw-result-empty' } }, job ? 'Результат появится после завершения задачи.' : 'Результата пока нет.'))));
      return;
    }
    const r = job.result || {};
    const ex = r.execution || {};
    const out = r.output || {};
    const par = r.parameters || {};
    const nodes = [picker];
    if (job.state === 'done' && out.file) {
      const partial = (r.not_executed || []).length > 0;
      nodes.push(h('div.stack.sm', {
        dataset: { testid: 'mw-result-banner', partial: partial ? '1' : '0' },
        style: { borderLeft: `3px solid var(${partial ? '--warn' : '--ok'})`, paddingLeft: '10px' } },
      h('div.row.tight', { style: { gap: '8px', flexWrap: 'wrap' } },
        pill(r.verified ? 'файл проверен' : 'файл НЕ проверен', { tone: r.verified ? 'ok' : 'err' }),
        pill(partial ? `выполнено частично: не выполнено ${(r.not_executed || []).length}` : 'все запрошенные операции выполнены', { tone: partial ? 'warn' : 'ok' })),
      h('div.small', execLine(ex))));
      nodes.push(h('div.row.tight', { style: { gap: '12px', flexWrap: 'wrap', alignItems: 'flex-start' } },
        h('video', { src: fileUrl(S.pid, job.job_id, 'output.mp4'), controls: true, preload: 'metadata',
          style: { maxWidth: '480px', width: '100%', borderRadius: '6px' }, dataset: { testid: 'mw-video' } }),
        (job.outputs || []).includes('cover.png')
          ? h('img', { src: `${fileUrl(S.pid, job.job_id, 'cover.png')}&t=${encodeURIComponent(job.updated_ts || '')}`, alt: 'Обложка',
            style: { maxWidth: '240px', width: '100%', borderRadius: '6px' }, dataset: { testid: 'mw-cover-img' } }) : null));
      const vid = out.video || {};
      const aud = (out.audio || [])[0];
      const rows = [
        ['файл', `${out.file} · ${fmtBytes(out.bytes)} · sha256 ${shortSha(out.sha256)}`],
        ['видео', `${vid.codec || '?'} ${vid.profile || ''} ${vid.pix_fmt || ''} · ${vid.width}×${vid.height} · ${vid.fps} к/с · кадров ${vid.nb_frames_decoded ?? '?'}`],
        ['звук', aud ? `${aud.codec} · ${aud.sample_rate} Гц · ${aud.channels} кан.` : 'нет звуковой дорожки (так и запрошено)'],
        ['длительность', fmtSec((out.format || {}).duration)],
        ['разрешение и fps', `${par.resolution ? `${par.resolution.width}×${par.resolution.height}${par.resolution.changed ? ' (изменено планом)' : ' (как в исходнике)'}` : '—'} · `
          + `${par.fps ? `${par.fps.value} к/с${par.fps.changed ? ' (изменено планом)' : ' (как в исходнике)'}` : '—'}`],
        ['кодирование', par.video ? `${par.video.codec_action}, preset ${par.video.preset}, crf ${par.video.crf}` : '—'],
        ['звук (решение)', par.audio ? `${par.audio.action}${par.audio.applied_seconds ? `, ${par.audio.applied_seconds} с из дорожки` : ''}${par.audio.padded_silence_seconds ? `, тишина +${par.audio.padded_silence_seconds} с` : ''}${par.audio.loudnorm && par.audio.loudnorm.mode ? `, loudnorm: ${par.audio.loudnorm.mode}` : ''}` : '—'],
        ['наложений текста', `${par.text_overlays || 0}, реплик субтитров: ${par.subtitle_cues ? par.subtitle_cues.used : 0}`],
        ['обложка', par.cover ? `cover.png, кадр ${fmtSec(par.cover.time)} (${par.cover.time_source || par.cover.frame_from || ''})${par.cover.title ? `, «${par.cover.title}»` : ''}` : '—'],
        ['ffmpeg', r.ffmpeg ? `${r.ffmpeg.version || ''}` : '—'],
        ['время', r.timing ? `всего ${fmtSec(r.timing.wall_total_seconds)} (рендер ${fmtSec(r.timing.render_seconds)}; оценка до запуска ≈ ${fmtSec((r.timing.estimate_executed_only || r.timing.estimate || {}).render_seconds)})` : '—'],
        ['платные вызовы', `${r.paid_calls ?? 0}, облако ${r.cloud_spend_usd ?? 0} USD`],
      ];
      nodes.push(h('div', { style: { overflowX: 'auto', maxWidth: '100%' } },
        h('table.fi-table', { dataset: { testid: 'mw-parameters' } }, h('tbody', ...rows.map(([k, val]) => h('tr', h('th', k), h('td', val)))))));
      const ev = r.text_evidence || [];
      if (ev.length) {
        nodes.push(h('div.small.dim', { dataset: { testid: 'mw-evidence' } },
          `Проверка наложенного текста по пикселям (кадр результата против исходника): ${ev.map((e) => (e.text_present_heuristic ? '✓' : '?')).join(' ')} — `
          + `${ev.filter((e) => e.text_present_heuristic).length} из ${ev.length} точек подтверждены.`));
      }
      const links = [
        h('a.btn.btn-sm', { href: fileUrl(S.pid, job.job_id, 'output.mp4'), download: 'output.mp4' }, 'Скачать видео'),
        (job.outputs || []).includes('cover.png') ? h('a.btn.btn-sm', { href: fileUrl(S.pid, job.job_id, 'cover.png'), download: 'cover.png' }, 'Скачать обложку') : null,
        (job.outputs || []).includes('output-telegram.mp4') ? h('a.btn.btn-sm', { href: fileUrl(S.pid, job.job_id, 'output-telegram.mp4'), download: 'output-telegram.mp4' }, 'Вариант для Telegram') : null,
        h('a.btn.btn-sm', { href: fileUrl(S.pid, job.job_id, 'render_report.json'), download: 'render_report.json', dataset: { testid: 'mw-report' } }, 'Отчёт (render_report.json)'),
      ];
      const checkBox = h('div.small', { dataset: { testid: 'mw-check-result' } });
      const check = btn('Проверить файл', async () => {
        await act(async () => {
          const c = await call(`/projects/${encodeURIComponent(S.pid)}/jobs/${encodeURIComponent(job.job_id)}/check`);
          checkBox.textContent = c.verified
            ? `Файл проверен сейчас: ${fmtSec(c.duration_s)}, ${c.video && c.video.width}×${c.video && c.video.height}, ${c.bytes} байт, sha256 ${shortSha(c.sha256)} совпадает с отчётом.`
            : `Файл НЕ прошёл проверку: ${c.reason}`;
        });
      }, { variant: 'subtle', size: 'sm', iconName: 'check' });
      check.dataset.testid = 'mw-check';
      nodes.push(h('div.row.tight', { style: { gap: '8px', flexWrap: 'wrap' } }, ...links.filter(Boolean), check), checkBox);
    } else if (job.state === 'preview_done') {
      nodes.push(h('div.small', 'Предпросмотр готов: три кадра на сцену и обложка ниже. Видео ещё не отрендерено.'));
    } else if (job.state === 'failed') {
      nodes.push(h('div.small', { style: { color: 'var(--err)' } }, `Видео не получено: ${job.error || 'ошибка'}`));
    } else if (job.state === 'interrupted' || job.state === 'cancelled') {
      nodes.push(h('div.small', { style: { color: 'var(--warn)' } },
        `Задача ${job.state === 'cancelled' ? 'остановлена' : 'прервана'}; готового файла нет.`));
    }
    // не выполнено — всегда видно, с причиной
    const ne = r.not_executed || [];
    if (ne.length) {
      nodes.push(h('div.stack.sm', { dataset: { testid: 'mw-not-executed' }, style: { borderLeft: '3px solid var(--warn)', paddingLeft: '10px' } },
        h('div.small', { style: { fontWeight: 600 } }, `НЕ выполнено (${ne.length}):`),
        ...ne.map((n) => h('div.small', { dataset: { sceneId: n.scene_id || '', element: n.element || '' } },
          `${n.scene_id ? `сцена ${n.scene_id}` : 'проект'} · ${ELEMENT_LABEL[n.element] || n.element}: ${ruReason(n.reason_code, n.reason)}`))));
    }
    const applied = (r.applied || []);
    if (applied.length) {
      nodes.push(h('details', h('summary.small', `Выполнено (${applied.length})`),
        h('div.stack.sm', ...applied.map((a) => h('div.small', `сцена ${a.scene_id} · ${ELEMENT_LABEL[a.element] || a.element}: ${a.detail || ''}${a.note ? ` (${a.note})` : ''}`)))));
    }
    // кадры предпросмотра
    const frames = (job.outputs || []).filter((n) => n.startsWith('preview/'));
    if (frames.length) {
      nodes.push(h('div.stack.sm',
        h('div.small', 'Кадры предпросмотра (по три на сцену, с наложенным текстом):'),
        h('div.row.tight', { style: { flexWrap: 'wrap', gap: '8px' }, dataset: { testid: 'mw-preview-frames' } },
          ...frames.slice(0, 30).map((n) => h('img', { src: fileUrl(S.pid, job.job_id, n), alt: n, loading: 'lazy',
            style: { width: '200px', maxWidth: '45%', borderRadius: '4px' } })))));
    }
    if ((r.warnings || []).length) {
      nodes.push(h('div.small.dim', `Предупреждения: ${r.warnings.slice(0, 5).join(' · ')}`));
    }
    secResult.replaceChildren(panel('Результат', h('div.stack.sm', { dataset: { testid: 'mw-result' } }, ...nodes.filter(Boolean))));
  }

  /* ---- версии ---- */

  function drawVersions() {
    const v = S.view;
    const hist = v.history || [];
    secVersions.replaceChildren(panel(`История правок (версия ${v.version})`,
      hist.length ? h('div.stack.sm', { dataset: { testid: 'mw-versions' } },
        ...hist.slice(0, 12).map((e) => h('div.row.tight', { style: { gap: '8px', flexWrap: 'wrap', alignItems: 'center' } },
          h('span.small', `v${e.version} · ${e.author || ''} · ${e.summary || ''}`),
          e.version !== v.version
            ? btn('Вернуть эту версию', async () => {
              await act(async () => { await call(`/projects/${encodeURIComponent(S.pid)}/revert`, { method: 'POST', body: { to_version: e.version } }); await reload(); }, `Возвращена версия ${e.version}`);
            }, { variant: 'ghost', size: 'sm' }) : tag('текущая'))))
        : h('div.small.dim', 'Правок пока нет.')));
  }

  /* ---- старт ---- */

  try {
    S.status = await call('/status');
    await loadList();
  } catch (e) {
    root.replaceChildren(errorNote(e, () => ctx && ctx.refresh && ctx.refresh()));
    return root;
  }
  drawProjects();
  const saved = recall();
  if (saved && S.projects.some((p) => p.project_id === saved)) {
    try { await openProject(saved); } catch { remember(''); }
  }
  return root;
}
