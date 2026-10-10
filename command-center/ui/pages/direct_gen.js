/* ============================================================
   direct_gen.js — Прямой генератор видео (DIRECT / ASSISTED).
   Endpoints (bcc/features/direct_gen.py, под /api):
     GET /direct-gen/models, GET /status, GET|POST /jobs, GET /jobs/{id},
     POST /jobs/{id}/cancel, POST /jobs/{id}/retry, GET /jobs/{id}/file, POST /assist
   DIRECT: prompt уходит в выбранную модель как есть. ASSISTED: локальный Qwen
   предлагает версию, показываются оба текста, выбирает человек.
   Процент — только из реальных шагов ComfyUI; иначе этап и прошедшее время.

   Дизайн: токены bx-* (theme.css) + pages/direct_gen.css, без внешних
   ресурсов. Форма и плеер не перерисовываются при опросе: обновляются
   только изменившиеся узлы (иначе сбрасывались бы поля и воспроизведение).
   Доступность: нативные radio/details/label, role=status для смены этапа,
   role=alert для ошибок, видимый фокус, prefers-reduced-motion.
   ============================================================ */

import { api } from '../api.js';
import { h, toastOk, toastError, icon } from '../components.js';
import { panel, pageHead, pill, btn, meter } from './_ui.js';
import { genjutsuPanel } from './genjutsu.js';

const STATUS_LABEL = { queued: 'В очереди', loading: 'Загрузка модели', generating: 'Генерация', postprocessing: 'Сохранение и проверка', completed: 'Готово', failed: 'Ошибка', cancelled: 'Остановлено' };
const STATUS_SHORT = { queued: 'в очереди', loading: 'загрузка', generating: 'генерация', postprocessing: 'сохранение', completed: 'готово', failed: 'ошибка', cancelled: 'остановлено' };
const STATUS_TONE = { queued: 'idle', loading: 'warn', generating: 'warn', postprocessing: 'warn', completed: 'ok', failed: 'err', cancelled: 'idle' };
const STAGES = [['queued', 'Очередь'], ['loading', 'Загрузка'], ['generating', 'Генерация'], ['postprocessing', 'Проверка'], ['completed', 'Готово']];
const STAGE_NOTE = {
  loading: 'Модель загружается в память.',
  generating: 'Идёт генерация кадров.',
  postprocessing: 'Файл сохраняется и проверяется (ffprobe).',
};
const LIVE = new Set(['queued', 'loading', 'generating', 'postprocessing']);
const POLL_LIVE_MS = 1500;
const POLL_IDLE_MS = 5000;
const MAX_PROMPT = 8000;
const RES_PRESETS = [[480, 272], [480, 320], [640, 384], [832, 480], [1024, 576], [1280, 720]];
const PHOTO_PRESETS = [[512, 512], [768, 768], [1024, 1024], [768, 512], [512, 768], [1152, 896]];
const KIND_LABEL = { photo: 'Фото', video: 'Видео' };
const base = '/api/direct-gen';
const LEGAL = 'Только для совершеннолетних; материалы реальных людей без их согласия не создавать';

/* Человеческие заголовки кодов. Исходный текст ошибки всегда показывается ниже без правок. */
const ERRORS = {
  model_unavailable: ['Модель недоступна', 'Не хватает весов или проверенного workflow-шаблона. Ничего не скачивается автоматически: положите файлы и обновите страницу.'],
  runtime_unreachable: ['ComfyUI не отвечает', 'Запустите ComfyUI на этом компьютере и повторите. Задача не создавалась.'],
  out_of_memory: ['Не хватило памяти', 'Уменьшите разрешение или длительность, закройте тяжёлые приложения и повторите с тем же seed.'],
  runtime_error: ['Ошибка среды выполнения', 'ComfyUI вернул ошибку. Её текст показан ниже без изменений.'],
  rejected: ['Запрос отклонён', 'Модель или ComfyUI не приняли задачу. Текст отказа ниже; prompt не менялся.'],
  timeout: ['Время ожидания вышло', 'Результат не появился за отведённое время. Можно повторить с тем же seed.'],
  empty_output: ['Пустой результат', 'ComfyUI завершил работу, но файл пустой.'],
  bad_output: ['Файл не прошёл проверку', 'Результат сохранён, но не распознан как воспроизводимое видео.'],
  interrupted: ['Запуск был прерван', 'Bossman перезапускали во время задачи; она не возобновлялась. Повторите вручную.'],
  assist_unavailable: ['ASSISTED недоступен', 'Нет подходящей локальной модели Qwen в реестре. Режим DIRECT работает без неё.'],
  assist_failed: ['Qwen не ответил', 'Версия не получена. Можно создать задачу в режиме DIRECT с вашим текстом.'],
  invalid_prompt: ['Некорректный prompt', `Нужен текст длиной от 1 до ${MAX_PROMPT} символов.`],
  invalid_duration: ['Недопустимая длительность', 'Проверьте предел длительности выбранной модели.'],
  invalid_resolution: ['Недопустимое разрешение', 'Стороны должны быть в пределах модели и кратны её шагу.'],
  invalid_seed: ['Недопустимый seed', 'Нужно неотрицательное целое число.'],
  image_required: ['Нужно референс-изображение', 'Эта модель принимает только запуск с референсом.'],
  audio_required: ['Нужен аудиофайл', 'Эта модель принимает только запуск с аудио.'],
  image_unsupported: ['Изображение не поддерживается', 'Workflow этой модели не принимает изображение: уберите файл.'],
  audio_unsupported: ['Аудио не поддерживается', 'Workflow этой модели не принимает аудио: уберите файл.'],
  unknown_model: ['Неизвестная модель', 'Обновите страницу и выберите модель из списка.'],
  insufficient_memory: ['Не хватает свободной памяти', 'Задача не запускалась: модели нужно больше свободной памяти, чем есть. Закройте тяжёлые приложения или выберите модель полегче.'],
  gpu_busy: ['GPU занят другим процессом', 'Уже работает другой sd-cli. Дождитесь его окончания; параллельный запуск не делается.'],
  engine_error: ['Ошибка движка sd-cli', 'Движок завершился с ошибкой. Последние строки его вывода показаны ниже без правок.'],
  image_invalid: ['Изображение не распознано', 'Нужен файл PNG или JPEG.'],
  invalid_steps: ['Недопустимое число шагов', 'Проверьте предел шагов выбранной модели.'],
  already_finished: ['Задача уже завершена', 'Останавливать нечего.'],
  consent_required: ['Нужно согласие', 'Поставьте галочку: человек на фото согласен на использование его лица. Без согласия замена не запускается.'],
  faces_required: ['Нужны фото лиц', 'Добавьте от 1 до 5 фотографий (PNG или JPEG) с лицом, которое переносится в видео.'],
  video_required: ['Нужно видео', 'Выберите исходный видеофайл, в котором меняется лицо.'],
  face_invalid: ['Фото лица не принято', 'Нужны чёткие фронтальные фото PNG/JPEG с одним лицом. Попробуйте другое фото.'],
  video_invalid: ['Видео не принято', 'Нужен читаемый видеофайл, который понимает ffmpeg/ffprobe. Попробуйте другой файл.'],
};

const errText = (e) => (e && e.message) || String(e);
const pad2 = (n) => String(n).padStart(2, '0');
function fmtClock(sec) {
  const s = Math.max(0, Math.round(Number(sec) || 0));
  return `${Math.floor(s / 60)}:${pad2(s % 60)}`;
}
function fmtBytes(n) {
  if (!Number.isFinite(n)) return '—';
  if (n >= 1048576) return `${(n / 1048576).toFixed(1)} МБ`;
  if (n >= 1024) return `${Math.round(n / 1024)} КБ`;
  return `${n} Б`;
}
function fmtWhen(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso).slice(0, 16) : `${pad2(d.getDate())}.${pad2(d.getMonth() + 1)} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

/* Сообщения каталога на английском: переводим известные обороты, остальное оставляем как есть. */
function humanReason(text) {
  return String(text || '')
    .replace(/weights missing on disk: /g, 'Нет весов на диске: ')
    .replace(/no verified workflow template installed \(([^)]+)\)/g, 'Нет проверенного workflow-шаблона ($1)')
    .replace(/нет движка sd-cli \(stable-diffusion\.cpp\)/, 'Нет движка sd-cli (stable-diffusion.cpp)')
    .replace(/ANIMATE needs a driving video and pose inputs; the Direct window has no such input yet/,
      'ANIMATE требует управляющее видео и позы; такого входа в этом окне пока нет');
}

function readB64(file) {
  return new Promise((resolve, reject) => {
    if (!file) { resolve(null); return; }
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(',', 2)[1] || null);
    r.onerror = () => reject(new Error('Не удалось прочитать файл'));
    r.readAsDataURL(file);
  });
}

/* Пословный diff (LCS): показывает, что именно изменила редактура. Ничего не скрывает. */
function wordDiff(a, b) {
  const x = a.split(/(\s+)/).filter(Boolean);
  const y = b.split(/(\s+)/).filter(Boolean);
  if (x.length * y.length > 400000) return [[a], [b]];
  const n = x.length; const m = y.length;
  const dp = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      dp[i][j] = x[i] === y[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const left = []; const right = [];
  let i = 0; let j = 0;
  const mark = (tag, s) => (/^\s+$/.test(s) ? s : h(tag, s));
  while (i < n && j < m) {
    if (x[i] === y[j]) { left.push(x[i]); right.push(y[j]); i += 1; j += 1; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { left.push(mark('del', x[i])); i += 1; }
    else { right.push(mark('ins', y[j])); j += 1; }
  }
  while (i < n) { left.push(mark('del', x[i])); i += 1; }
  while (j < m) { right.push(mark('ins', y[j])); j += 1; }
  return [left, right];
}

function errorBlock(code, message, extra) {
  const [title, hint] = ERRORS[code] || [`Ошибка [${code || 'unknown'}]`, ''];
  return h('div.dg-alert', { role: 'alert', dataset: { testid: 'dg-error' } },
    h('div.dg-alert-title', title),
    hint ? h('div.dg-alert-hint', hint) : null,
    h('pre.dg-raw', `[${code || 'unknown'}] ${message || ''}`),
    extra || null);
}

/* Черновик живёт в памяти вкладки между переходами по меню; на диск не пишется. */
let DRAFT = null;

/* ------------------------------------------------------------------ задача */

function createJobView(hooks) {
  const announce = h('div.dg-sr', { role: 'status', 'aria-live': 'polite', 'aria-atomic': 'true' });
  const statusEl = h('div.dg-big-status');
  const elapsedEl = h('div.dg-elapsed', { 'aria-hidden': 'true' });
  const metaEl = h('div.dg-small', { dataset: { testid: 'dg-meta' } });
  const stageEls = STAGES.map(([, label]) => h('li.dg-stage', label));
  const stagesEl = h('ol.dg-stages', { 'aria-label': 'Этапы задачи' }, stageEls);
  const noteEl = h('div.dg-small');
  const progressBox = h('div.dg-stack', { dataset: { testid: 'dg-progress' } });
  const errBox = h('div');
  const warnBox = h('div');
  const placeholder = h('div.dg-stage-ph');
  const mediaBox = h('div.dg-stage-box', placeholder);
  const resultInfo = h('div.dg-small');

  const stopBtn = btn('STOP', () => hooks.stop(state.id), { variant: 'danger', iconName: 'stop', title: 'Остановить задачу (STOP)' });
  stopBtn.classList.add('dg-btn-tight'); stopBtn.dataset.testid = 'dg-stop-job';
  const retryBtn = btn('Повторить с тем же seed', () => hooks.retry(state.id), { variant: 'primary', iconName: 'retry' });
  retryBtn.classList.add('dg-btn-tight');
  const formBtn = btn('Взять в форму', () => hooks.toForm(state.job), { variant: 'subtle', title: 'Подставить prompt и параметры этой задачи в форму' });
  formBtn.classList.add('dg-btn-tight');
  const saveLink = h('a', { class: 'bx-btn bx-btn-secondary dg-btn-tight', href: '#', download: 'bossman-direct', hidden: true, dataset: { testid: 'dg-save' } },
    icon('check', 14), h('span', 'Сохранить'));

  const logList = h('ol.dg-small', { style: { margin: '0', paddingLeft: '18px' } });
  const provPre = h('pre.dg-raw', { tabindex: '0', 'aria-label': 'Параметры и происхождение (JSON)' });
  const logBox = h('details.dg-details', { dataset: { testid: 'dg-log' } },
    h('summary', 'Журнал этапов и происхождение'),
    h('div.dg-details-body.dg-stack', logList, provPre));

  const state = { id: null, job: null, mediaKey: null, errSig: '', warnSig: '', logSig: '', announceKey: '' };
  let progressEl = null;

  const el = h('div.dg-job', { dataset: { testid: 'dg-job-view' } },
    h('div.dg-stack', mediaBox, resultInfo),
    h('div.dg-stack', announce,
      h('div.dg-row', statusEl, elapsedEl), metaEl, stagesEl, progressBox, noteEl,
      h('div.dg-row', stopBtn, retryBtn, saveLink, formBtn), errBox, warnBox, logBox));

  function buildMedia(job) {
    const url = `${base}/jobs/${encodeURIComponent(job.job_id)}/file`;
    const note = h('div.dg-stage-ph', { hidden: true }, 'Браузер не смог воспроизвести этот файл. Его можно сохранить и открыть во внешнем плеере.');
    if (job.result.mime && job.result.mime.startsWith('image/')) return [h('img', { src: url, alt: 'Результат генерации' })];
    const v = h('video', { src: url, controls: true, preload: 'metadata', playsinline: true, 'aria-label': 'Результат генерации' });
    v.addEventListener('error', () => { v.hidden = true; note.hidden = false; });
    return [v, note];
  }

  let progMode = '';
  function setProgress(job) {
    const p = job.progress || { kind: 'none' };
    if (p.kind === 'steps') {
      if (!progressEl) {
        progressEl = {
          bar: h('progress.dg-progress', { 'aria-label': 'Прогресс по шагам движка' }),
          txt: h('div.dg-small.dg-num'),
        };
      }
      progressEl.bar.max = p.max; progressEl.bar.value = p.value;
      progressEl.txt.textContent = `Шаг ${p.value} из ${p.max} (${p.percent}%): данные движка`;
      if (progMode !== 'steps') { progMode = 'steps'; progressBox.replaceChildren(progressEl.bar, progressEl.txt); }
    } else {
      const mode = LIVE.has(job.status) ? 'text' : '';
      if (progMode !== mode) {
        progMode = mode;
        progressBox.replaceChildren(...(mode ? [h('div.dg-small.dg-dim', 'Процент недоступен: модель не сообщает шаги. Показаны этап и прошедшее время.')] : []));
      }
    }
  }

  function setStages(job) {
    const live = LIVE.has(job.status);
    const reached = job.timeline.map((t) => t.stage).filter((s) => LIVE.has(s));
    const lastLive = reached.length ? reached[reached.length - 1] : 'queued';
    const failed = job.status === 'failed'; const stopped = job.status === 'cancelled';
    const nowIdx = live ? STAGES.findIndex(([k]) => k === job.stage) : -1;
    stageEls.forEach((li, i) => {
      const key = STAGES[i][0];
      let cls = 'dg-stage'; let cur = false; let label = STAGES[i][1];
      if (job.status === 'completed') cls += ' is-done is-ok';
      else if (live) {
        if (i < nowIdx) cls += ' is-done';
        else if (i === nowIdx) { cls += ' is-now' + (key === 'queued' ? '' : ' is-pulse'); cur = true; }
      } else {
        const lastIdx = STAGES.findIndex(([k]) => k === lastLive);
        if (i < lastIdx) cls += ' is-done';
        else if (i === lastIdx) { cls += failed ? ' is-bad' : ' is-stopped'; cur = true; }
        if (key === 'completed') { cls = 'dg-stage' + (failed ? ' is-bad' : ' is-stopped'); label = failed ? 'Ошибка' : 'Остановлено'; }
      }
      if (li.className !== cls) li.className = cls;
      if (li.textContent !== label) li.textContent = label;
      if (cur || (job.status === 'completed' && key === 'completed') || (!live && key === 'completed' && (failed || stopped))) li.setAttribute('aria-current', 'step');
      else li.removeAttribute('aria-current');
    });
  }

  function update(job) {
    if (job.job_id !== state.id) { state.id = job.job_id; state.errSig = ''; state.warnSig = ''; state.logSig = ''; progressEl = null; progMode = ''; progressBox.replaceChildren(); }
    state.job = job;
    const live = LIVE.has(job.status);
    const label = STATUS_LABEL[job.status] || job.status;
    if (statusEl.textContent !== label) statusEl.textContent = label;
    elapsedEl.textContent = job.status === 'queued' && !job.elapsed_s ? '' : fmtClock(job.elapsed_s);
    const p = job.params;
    metaEl.textContent = `${job.model} · ${job.mode} · seed ${p.seed} · ${p.width}×${p.height}`
      + (job.kind === 'video' ? ` · ${p.duration} с (${p.frames} кадров)` : '') + (p.steps ? ` · шагов ${p.steps}` : '')
      + (job.status === 'queued' ? ` · позиция в очереди: ${job.queue_position}` : '');
    setStages(job); setProgress(job);
    noteEl.textContent = STAGE_NOTE[job.stage] && live ? STAGE_NOTE[job.stage] : '';

    const key = announceKeyOf(job);
    if (key !== state.announceKey) { state.announceKey = key; announce.textContent = `Задача: ${label}`; }

    stopBtn.hidden = !live; retryBtn.hidden = live;
    const done = job.status === 'completed' && job.result;
    saveLink.hidden = !done;
    if (done) saveLink.href = `${base}/jobs/${encodeURIComponent(job.job_id)}/file`;

    const mk = done ? `${job.job_id}:${job.result.sha256}` : '';
    if (mk !== state.mediaKey) { state.mediaKey = mk; mediaBox.replaceChildren(...(done ? buildMedia(job) : [placeholder])); }
    placeholder.textContent = {
      queued: 'Задача в очереди. Превью появится после генерации.',
      loading: 'Модель загружается. Превью появится после генерации.',
      generating: 'Идёт генерация. Превью появится после сохранения файла.',
      postprocessing: 'Файл сохраняется и проверяется.',
      failed: 'Результата нет: см. сообщение об ошибке справа.',
      cancelled: 'Задача остановлена, результата нет.',
      completed: 'Результат недоступен.',
    }[job.status] || '';
    resultInfo.textContent = done
      ? `${fmtBytes(job.result.bytes)} · sha256 ${String(job.result.sha256).slice(0, 16)}… · `
        + (job.result.verified ? `проверен ffprobe, ${job.result.duration_s} с` : `не проверен независимо (${job.result.reason || 'нет данных'})`)
      : '';

    const es = job.error ? `${job.error.code}|${job.error.message}` : '';
    if (es !== state.errSig) { state.errSig = es; errBox.replaceChildren(...(job.error ? [errorBlock(job.error.code, job.error.message)] : [])); }
    const ws = job.warning || '';
    if (ws !== state.warnSig) { state.warnSig = ws; warnBox.replaceChildren(...(ws ? [h('div.dg-info', ws)] : [])); }

    const ls = `${job.timeline.length}|${job.status}`;
    if (ls !== state.logSig) {
      state.logSig = ls;
      logList.replaceChildren(...job.timeline.map((t) => h('li', `${fmtWhen(t.at)} · ${STATUS_SHORT[t.stage] || t.stage}${t.note ? `: ${t.note}` : ''}`)));
      const sent = job.effective_prompt === job.raw_prompt ? 'отправлен в модель без изменений' : 'отправлено в модель (отличается от оригинала)';
      provPre.textContent = JSON.stringify({ raw_prompt: job.raw_prompt, effective_prompt: job.effective_prompt, prompt_status: sent, negative: job.negative, provenance: job.provenance }, null, 1);
    }
  }
  const announceKeyOf = (job) => `${job.job_id}|${job.status}`;

  return { el, update, current: () => state.job };
}

/* ------------------------------------------------------------------ страница */

/* ---- Замена лица в видео (FaceFusion, локально): код — Mistral Large 4, проверка — Claude ---- */
function faceSwapPanel(ctx) {
  const videoInput = h('input.dg-file', { id: 'dg-swap-video', name: 'dg-swap-video', type: 'file', accept: 'video/*' });
  const facesInput = h('input.dg-file', { id: 'dg-swap-faces', name: 'dg-swap-faces', type: 'file', accept: 'image/png,image/jpeg', multiple: true });
  const faceCount = h('div.dg-note', '0 из 5');
  const presetSel = h('select.dg-input', { id: 'dg-swap-preset', name: 'dg-swap-preset' });
  const frame169 = h('input', { type: 'radio', name: 'dg-swap-frame', value: '16:9', id: 'dg-swap-frame-169' });
  const frameOrig = h('input', { type: 'radio', name: 'dg-swap-frame', value: 'original', id: 'dg-swap-frame-orig', checked: true });
  const consent = h('input', { type: 'checkbox', id: 'dg-swap-consent', name: 'dg-swap-consent' });
  const swapErr = h('div', { dataset: { testid: 'dg-swap-error' } });
  const unavailBox = h('div');

  let videoFile = null;
  let faceFiles = [];
  let available = true;

  const field = (text, id, input, note) => h('div', h('label.dg-label', { for: id }, text), input, note || null);
  const fillPresets = (list) => {
    const items = Array.isArray(list) && list.length ? list : [{ id: 'fast', label: 'Быстро' }, { id: 'quality', label: 'Качество' }];
    presetSel.replaceChildren(...items.map((p) => h('option', { value: p.id }, p.label || p.id)));
    presetSel.value = items.some((p) => p.id === 'fast') ? 'fast' : items[0].id;
  };
  fillPresets(null);

  const frameGroup = h('div.dg-seg', { role: 'radiogroup', 'aria-label': 'Кадр' },
    h('label.dg-seg-opt', { for: frame169.id }, frame169, h('span.dg-seg-t', '16:9 (YouTube, только для обычных клипов)')),
    h('label.dg-seg-opt', { for: frameOrig.id }, frameOrig, h('span.dg-seg-t', 'Как в исходнике')));
  const consentLabel = h('label.dg-label', { for: consent.id }, consent, h('span', 'Человек на фото согласен на использование его лица'));

  const showSwapErr = (e) => {
    const code = (e && e.code) || 'unknown';
    swapErr.replaceChildren(errorBlock(code, errText(e)));
  };

  const go = btn('Заменить лицо', async () => {
    swapErr.replaceChildren();
    if (!videoFile) { showSwapErr({ code: 'video_required', message: 'Видео не выбрано.' }); return; }
    if (!faceFiles.length) { showSwapErr({ code: 'faces_required', message: 'Фото лица не выбраны.' }); return; }
    try {
      const video_b64 = await readB64(videoFile);
      const faces_b64 = [];
      for (const f of faceFiles) faces_b64.push(await readB64(f));
      const frame = frameOrig.checked ? 'original' : '16:9';
      const job = await api.raw(`${base}/swap-jobs`, { method: 'POST', body: { video_b64, faces_b64, preset: presetSel.value, frame, consent: true } });
      ctx.setCurrent(job.job_id);
      await ctx.refresh();
      ctx.focusJob();
      toastOk('Задача создана', 'FaceFusion, локально');
    } catch (e) { toastError(e); showSwapErr(e); }
  }, { variant: 'primary', iconName: 'user', disabled: true });
  go.dataset.testid = 'dg-swap-go';

  const setFormDisabled = (off) => {
    for (const el of [videoInput, facesInput, presetSel, frame169, frameOrig, consent, go]) el.disabled = off;
    if (!off) go.disabled = !consent.checked;
  };
  consent.addEventListener('change', () => { if (available) go.disabled = !consent.checked; });
  videoInput.addEventListener('change', () => { videoFile = videoInput.files[0] || null; });
  facesInput.addEventListener('change', () => {
    faceFiles = Array.from(facesInput.files || []).slice(0, 5);
    faceCount.textContent = `${faceFiles.length} из 5`;
  });

  const el = panel('Замена лица в видео', h('div.dg-stack',
    field('Видео', videoInput.id, videoInput),
    field('Фото лиц (1–5)', facesInput.id, facesInput, faceCount),
    field('Качество', presetSel.id, presetSel),
    h('div.dg-row', h('span.dg-label', { style: { margin: '0' } }, 'Кадр'), frameGroup),
    h('div', consentLabel),
    h('div.dg-actions', go),
    swapErr,
    unavailBox,
    h('div.dg-note', 'Локально (FaceFusion), бесплатно. Звук берётся из оригинала. Только с согласия человека на фото.')), { icon: 'user' });

  (async () => {
    try {
      const info = await api.raw(`${base}/faceswap`);
      fillPresets(info.presets);
      available = !!info.available;
      if (!available) {
        unavailBox.replaceChildren(h('div.dg-alert', { role: 'alert' },
          h('div.dg-alert-title', 'Замена лица недоступна'),
          h('pre.dg-raw', String(info.reason || 'Причина не указана.'))));
        setFormDisabled(true);
      }
    } catch (e) {
      available = false;
      unavailBox.replaceChildren(h('div.dg-alert', { role: 'alert' },
        h('div.dg-alert-title', 'Состояние замены лица не получено'),
        h('pre.dg-raw', errText(e))));
      setFormDisabled(true);
    }
  })();

  return el;
}

const DirectGenPage = {
  id: 'direct-gen',
  title: 'Прямой генератор',
  icon: 'bolt',
  nav: 'more',
  section: 'studio',

  async render() {
    const head = pageHead('Прямой генератор: фото и видео',
      'Локальная генерация выбранной моделью. DIRECT: ваш текст уходит в модель без изменений и без промежуточной модерации; отказы и ошибки показываются как есть.');
    const state = { models: [], jobs: [], current: null, assist: null, modelId: null, kind: (DRAFT || {}).kind || 'photo', mode: 'DIRECT', tick: 0, modelSig: '', histSig: '' };
    const D = DRAFT || {};

    /* ---- форма ---- */
    const prompt = h('textarea.dg-prompt', { id: 'dg-prompt', name: 'dg-prompt', rows: 8, maxlength: String(MAX_PROMPT + 2000), placeholder: 'Опишите сцену: что в кадре, где, как движется камера', 'aria-describedby': 'dg-prompt-hint dg-prompt-count' });
    prompt.value = D.prompt || '';
    const count = h('div.dg-count', { id: 'dg-prompt-count' });
    const negative = h('textarea.dg-input', { id: 'dg-negative', name: 'dg-negative', rows: 2, placeholder: 'Чего не должно быть в кадре (необязательно)' });
    negative.value = D.negative || '';
    const duration = h('input.dg-input', { id: 'dg-duration', name: 'dg-duration', type: 'number', min: '0.5', step: '0.5', value: D.duration || '4', inputmode: 'decimal' });
    const resolution = h('input.dg-input', { id: 'dg-resolution', name: 'dg-resolution', type: 'text', value: D.resolution || '480x320', pattern: '\\d{3,4}x\\d{3,4}', inputmode: 'numeric', autocomplete: 'off', 'aria-describedby': 'dg-res-note' });
    const seed = h('input.dg-input', { id: 'dg-seed', name: 'dg-seed', type: 'number', min: '0', step: '1', placeholder: 'случайный', inputmode: 'numeric' });
    if (D.seed !== undefined && D.seed !== null && D.seed !== '') seed.value = D.seed;
    const image = h('input.dg-file', { id: 'dg-image', name: 'dg-image', type: 'file', accept: 'image/*' });
    const audio = h('input.dg-file', { id: 'dg-audio', name: 'dg-audio', type: 'file', accept: 'audio/*' });
    const imgPreview = h('div.dg-note', { 'aria-live': 'polite' });
    const resChips = h('div.dg-chips', { role: 'group', 'aria-label': 'Типовые разрешения' });
    const durNote = h('div.dg-note', { id: 'dg-dur-note' });
    const resNote = h('div.dg-note', { id: 'dg-res-note' });
    const paramSum = h('span.dg-details-sum.dg-num', { 'aria-hidden': 'true' });
    const createErr = h('div', { dataset: { testid: 'dg-create-error' } });
    const assistBox = h('div.dg-stack', { dataset: { testid: 'dg-assist' } });

    const lab = (text, id, note) => h('div', h('label.dg-label', { for: id }, text), note || null);

    const steps = h('input.dg-input', { id: 'dg-steps', name: 'dg-steps', type: 'number', min: '1', step: '1', inputmode: 'numeric' });
    if (D.steps) steps.value = D.steps;
    const stepsNote = h('div.dg-note');
    const imageLabel = h('label.dg-label', { for: 'dg-image' }, 'Референс-изображение');
    const imageNote = h('div.dg-note');
    const durBox = h('div', lab('Длительность, с', 'dg-duration'), duration, durNote);
    const audioBox = h('div', lab('Аудио (для S2V)', 'dg-audio'), audio);
    const imageBox = h('div', imageLabel, image, imageNote, imgPreview);
    const paramsBox = h('details.dg-details', { dataset: { testid: 'dg-params' } },
      h('summary', 'Параметры', paramSum),
      h('div.dg-details-body', h('div.dg-fields',
        durBox,
        h('div', lab('Шаги', 'dg-steps'), steps, stepsNote),
        h('div', lab('Разрешение', 'dg-resolution'), resolution, resNote, resChips),
        h('div', lab('Seed', 'dg-seed'), seed, h('div.dg-note', 'Пусто: случайный. Seed каждой задачи сохраняется для повтора.')),
        h('div.is-wide', lab('Negative', 'dg-negative'), negative),
        imageBox, audioBox)));
    if (D.paramsOpen) paramsBox.open = true;
    paramsBox.addEventListener('toggle', () => { DRAFT = { ...(DRAFT || {}), paramsOpen: paramsBox.open }; });

    /* ---- режим ---- */
    const modeRadio = (value, title, sub) => {
      const input = h('input', { type: 'radio', name: 'dg-mode', value, id: `dg-mode-${value}`, checked: value === (D.mode || 'DIRECT') });
      input.addEventListener('change', () => { if (input.checked) setMode(value); });
      return h('label.dg-seg-opt', { for: input.id }, input, h('span.dg-seg-t', title), h('span.dg-seg-s', sub));
    };
    const modeGroup = h('div.dg-seg', { role: 'radiogroup', 'aria-label': 'Режим' },
      modeRadio('DIRECT', 'DIRECT', 'как написано'), modeRadio('ASSISTED', 'ASSISTED', 'с предложением Qwen'));
    const modeNote = h('div.dg-hint', { id: 'dg-prompt-hint' });

    /* ---- модели ---- */
    const kindRadio = (value, title, sub) => {
      const input = h('input', { type: 'radio', name: 'dg-kind', value, id: `dg-kind-${value}`, checked: value === state.kind });
      input.addEventListener('change', () => { if (input.checked) setKind(value); });
      return h('label.dg-seg-opt', { for: input.id }, input, h('span.dg-seg-t', title), h('span.dg-seg-s', sub));
    };
    const kindGroup = h('div.dg-seg', { role: 'radiogroup', 'aria-label': 'Тип результата', dataset: { testid: 'dg-kind' } },
      kindRadio('photo', 'Фото', 'картинка'), kindRadio('video', 'Видео', 'ролик'));
    const modelCards = h('fieldset.dg-cards', { dataset: { testid: 'dg-models' } }, h('legend.dg-sr', 'Модель'));
    const modelDetail = h('div.dg-stack', { dataset: { testid: 'dg-model-note' } });
    const statusBox = h('div.dg-stack', { dataset: { testid: 'dg-status' } });

    /* ---- задача и история ---- */
    const jobView = createJobView({
      stop: async (id) => { try { await api.raw(`${base}/jobs/${id}/cancel`, { method: 'POST', body: {} }); await refresh(); } catch (e) { toastError(e); await refresh(); } },
      retry: async (id) => {
        try { const j = await api.raw(`${base}/jobs/${id}/retry`, { method: 'POST', body: {} }); toastOk('Повтор запущен', `seed ${j.params.seed}`); state.current = j.job_id; await refresh(); focusJob(); }
        catch (e) { toastError(e); }
      },
      toForm: (job) => loadToForm(job),
    });
    const jobEmpty = h('div.bx-blank', h('span.bx-blank-icon', icon('play', 24)),
      h('div.bx-blank-title', 'Задач пока не было'),
      h('div.bx-blank-hint', 'Напишите prompt и нажмите «Создать». Здесь появятся этапы, время, превью, кнопки «Сохранить» и «Повторить с тем же seed», а также журнал.'));
    const jobHost = h('div', { tabindex: '-1', class: 'dg-anchor', role: 'region', 'aria-label': 'Текущая задача', dataset: { testid: 'dg-job' } }, jobEmpty);
    const histBox = h('div');

    /* ---- вычисления ---- */
    const selected = () => state.models.find((m) => m.id === state.modelId);
    const countUpdate = () => {
      const n = prompt.value.length;
      count.textContent = `${n} / ${MAX_PROMPT}`;
      count.classList.toggle('is-over', n > MAX_PROMPT);
    };
    const sumUpdate = () => {
      paramSum.textContent = `${duration.value || '?'} с · ${resolution.value || '?'} · seed ${seed.value === '' ? 'случайный' : seed.value}`;
      resChips.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', b.dataset.res === resolution.value.trim() ? 'true' : 'false'));
    };
    const saveDraft = () => {
      DRAFT = { ...(DRAFT || {}), prompt: prompt.value, negative: negative.value, duration: duration.value, resolution: resolution.value, seed: seed.value, steps: steps.value, mode: state.mode, model: state.modelId, kind: state.kind };
    };
    const pickModel = (kind) => {
      const ofKind = state.models.filter((m) => m.kind === kind);
      return ofKind.find((m) => m.id === state.modelId) || ofKind.find((m) => m.available) || ofKind[0] || null;
    };
    function setKind(kind) {
      state.kind = kind;
      const m = pickModel(kind);
      state.modelId = m ? m.id : null;
      renderModels(true); renderModelDetail(); applyLimits(true); sumUpdate(); saveDraft();
    }
    const setMode = (mode) => {
      state.mode = mode;
      modeNote.textContent = mode === 'DIRECT'
        ? 'Текст уходит в модель как написан. Ctrl+Enter: создать.'
        : 'Сначала локальный Qwen предложит версию. Вы увидите оба текста и выберете сами: ничего не применяется молча.';
      createBtn.querySelector('span:last-child').textContent = mode === 'DIRECT' ? 'Создать' : 'Создать: сначала версия Qwen';
      if (mode === 'DIRECT') { state.assist = null; showAssist(); }
      saveDraft();
    };
    const applyLimits = (reset) => {
      const m = selected();
      resChips.replaceChildren();
      if (!m) { durNote.textContent = ''; resNote.textContent = ''; return; }
      const photo = m.kind === 'photo';
      durBox.hidden = photo;
      steps.parentElement.hidden = m.default_steps == null;
      audioBox.hidden = !m.requires_audio;
      imageBox.hidden = !m.accepts_image;
      imageLabel.textContent = m.requires_image ? 'Референс-изображение (обязательно)' : 'Стартовое изображение (необязательно)';
      imageNote.textContent = m.requires_image ? 'Правка по референсу: только для собственного или вымышленного персонажа.' : '';
      if (m.default_steps != null) {
        steps.max = String(m.max_steps);
        stepsNote.textContent = `По умолчанию ${m.default_steps}, не больше ${m.max_steps}`;
        steps.placeholder = String(m.default_steps);
        if (reset) steps.value = '';
      }
      if (reset && m.default_resolution) resolution.value = m.default_resolution;
      if (!photo) {
        duration.max = String(m.max_seconds);
        durNote.textContent = `Не больше ${m.max_seconds} с · ${m.fps} к/с`;
      }
      resNote.textContent = `Каждая сторона ${m.min_side}–${m.max_side}, кратно ${m.side_multiple}`;
      for (const [w, hh] of (photo ? PHOTO_PRESETS : RES_PRESETS)) {
        if (w < m.min_side || hh < m.min_side || w > m.max_side || hh > m.max_side || w % m.side_multiple || hh % m.side_multiple) continue;
        const val = `${w}x${hh}`;
        const chip = h('button.dg-chip', { type: 'button', dataset: { res: val }, 'aria-pressed': 'false' }, `${w}×${hh}`);
        chip.addEventListener('click', () => { resolution.value = val; sumUpdate(); saveDraft(); });
        resChips.append(chip);
      }
      sumUpdate();
    };

    const renderModelDetail = () => {
      const m = selected();
      modelDetail.replaceChildren();
      if (!m) { modelDetail.append(h('div.dg-info', 'Каталог моделей пуст.')); return; }
      const sha = m.sha256 === 'UNKNOWN' ? 'UNKNOWN' : `${m.sha256.slice(0, 16)}…`;
      modelDetail.append(...[
        h('div.dg-small', { 'aria-live': 'polite' }, m.kind === 'photo'
          ? `Выбрана: ${m.label} · ${m.modes.join(' / ')} · шагов по умолчанию ${m.default_steps}`
          : `Выбрана: ${m.label} · до ${m.max_seconds} с · ${m.fps} к/с`),
        m.notes ? h('div.dg-small', m.notes) : null,
        m.requires_image ? h('div.dg-small', 'Нужно референс-изображение (поле в «Параметрах»).') : null,
        m.requires_audio ? h('div.dg-small', 'Нужен аудиофайл (поле в «Параметрах»).') : null,
        h('div.dg-note', `Источник ${m.source} · ревизия ${m.revision} · лицензия ${m.license} · sha256 ${sha}`),
      ].filter(Boolean));
    };

    const renderModels = (force) => {
      const shown = state.models.filter((m) => m.kind === state.kind);
      const sig = JSON.stringify([state.kind, shown.map((m) => [m.id, m.available, m.reason])]);
      if (!force && sig === state.modelSig) return;
      state.modelSig = sig;
      const hadFocus = modelCards.contains(document.activeElement);
      modelCards.replaceChildren(h('legend.dg-sr', `${KIND_LABEL[state.kind]}: модель`), ...shown.map((m) => {
        const input = h('input', { type: 'radio', name: 'dg-model', value: m.id, id: `dg-model-${m.id}`, checked: m.id === state.modelId, 'aria-describedby': `dg-model-${m.id}-d` });
        input.addEventListener('change', () => { if (input.checked) { state.modelId = m.id; renderModels(true); renderModelDetail(); applyLimits(true); sumUpdate(); saveDraft(); } });
        return h('label', { class: `dg-card${m.id === state.modelId ? ' is-on' : ''}${m.available ? '' : ' is-off'}`, for: input.id },
          input,
          h('div.dg-card-in',
            h('div.dg-card-top', h('span.dg-card-name', m.label), pill(m.available ? 'доступна' : 'недоступна', { tone: m.available ? 'ok' : 'err' })),
            h('div.dg-card-meta', `${m.modes.join(' / ')} · ${m.runtime}`),
            h('div.dg-card-reason', { id: `dg-model-${m.id}-d` }, m.available ? (m.runtime === 'sdcpp' ? 'Веса и движок sd-cli на месте.' : 'Веса и workflow-шаблон на месте.') : [h('b', 'Почему недоступна: '), humanReason(m.reason)])));
      }));
      if (hadFocus) modelCards.querySelector('input:checked')?.focus();
    };

    const modelsHelp = h('details.dg-details', { dataset: { testid: 'dg-models-help' } },
      h('summary', 'Как сделать модель доступной'),
      h('div.dg-details-body.dg-small',
        'Фото и Wan2.2 TI2V запускаются через sd-cli: веса ищутся в каталоге медиа-моделей (BOSSMAN_MEDIA_MODELS; по умолчанию Bossman/models/media), движок в BOSSMAN_SDCPP_BIN. ',
        'Остальное видео идёт через ComfyUI: веса ищутся в его каталоге моделей (BOSSMAN_COMFYUI_MODELS_DIR; по умолчанию Bossman/media-runtime/ComfyUI/models). ',
        'Проверенный workflow-шаблон <id модели>.json кладётся в папку direct-gen/workflows каталога данных Bossman. ',
        'Ничего не скачивается и не подставляется автоматически; платной замены нет.'));

    /* ---- сообщения ---- */
    const showCreateErr = (e, fallbackCode) => {
      const code = (e && e.code) || fallbackCode || 'unknown';
      createErr.replaceChildren(errorBlock(code, errText(e)));
    };
    const clearCreateErr = () => createErr.replaceChildren();

    /* ---- ASSISTED ---- */
    function showAssist() {
      assistBox.replaceChildren();
      const a = state.assist;
      if (!a) return;
      const [l, r] = wordDiff(a.original, a.suggested);
      assistBox.append(
        h('div.dg-info', `Версию предложила модель ${a.model}. Ничего не применено и не отправлено: выберите сами. Зелёным отмечено добавленное, красным удалённое.`),
        h('div.dg-compare',
          h('section.dg-cmp-col', { 'aria-labelledby': 'dg-cmp-o' }, h('h3', { id: 'dg-cmp-o' }, 'Оригинал'), h('p.dg-cmp-text', l)),
          h('section.dg-cmp-col', { 'aria-labelledby': 'dg-cmp-s' }, h('h3', { id: 'dg-cmp-s' }, `Версия ${a.model}`), h('p.dg-cmp-text', r))),
        h('div.dg-actions',
          btn('Создать с оригиналом', () => submit({ prompt: a.original, mode: 'ASSISTED', assist_id: a.assist_id, assist_choice: 'original' }), { variant: 'secondary' }),
          btn('Создать с версией Qwen', () => submit({ prompt: a.original, mode: 'ASSISTED', assist_id: a.assist_id, assist_choice: 'suggested' }), { variant: 'primary' }),
          btn('Отклонить обе', () => { state.assist = null; showAssist(); prompt.focus(); }, { variant: 'ghost' })));
    }

    /* ---- создание ---- */
    const body = async (extra = {}) => ({
      model: state.modelId, prompt: prompt.value, negative: negative.value,
      ...(selected()?.kind === 'video' ? { duration: Number(duration.value) } : {}),
      ...(steps.value === '' || steps.parentElement.hidden ? {} : { steps: Number(steps.value) }),
      resolution: resolution.value.trim(),
      seed: seed.value === '' ? null : Number(seed.value), mode: state.mode,
      image_b64: await readB64(image.files[0]), audio_b64: await readB64(audio.files[0]), ...extra,
    });
    async function submit(extra) {
      clearCreateErr();
      try {
        const job = await api.raw(`${base}/jobs`, { method: 'POST', body: await body(extra) });
        state.current = job.job_id;
        state.assist = null; showAssist();
        toastOk('Задача создана', `seed ${job.params.seed}`);
        await refresh();
        focusJob();
      } catch (e) { showCreateErr(e); }
    }
    function focusJob() {
      const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      jobHost.focus({ preventScroll: true });
      jobHost.scrollIntoView({ block: 'start', behavior: reduce ? 'auto' : 'smooth' });
    }
    const checkPrompt = () => {
      if (prompt.value.trim()) { prompt.removeAttribute('aria-invalid'); return true; }
      prompt.setAttribute('aria-invalid', 'true');
      createErr.replaceChildren(errorBlock('invalid_prompt', 'prompt пуст'));
      prompt.focus();
      return false;
    };
    const create = async () => {
      clearCreateErr();
      if (!checkPrompt()) return;
      if (state.mode === 'ASSISTED') {
        try { state.assist = await api.raw(`${base}/assist`, { method: 'POST', body: { prompt: prompt.value } }); showAssist(); assistBox.querySelector('button')?.focus(); }
        catch (e) { state.assist = null; showAssist(); showCreateErr(e, 'assist_failed'); }
        return;
      }
      await submit();
    };
    const createBtn = btn('Создать', create, { variant: 'primary', size: 'lg', iconName: 'bolt' });
    createBtn.classList.add('dg-create'); createBtn.dataset.testid = 'dg-create';
    const stopBtn = btn('STOP', async () => { const j = jobView.current(); if (j && LIVE.has(j.status)) await jobView_stop(j.job_id); },
      { variant: 'danger', size: 'lg', iconName: 'stop', title: 'Остановить текущую задачу (STOP)', disabled: true });
    stopBtn.classList.add('dg-stop'); stopBtn.dataset.testid = 'dg-stop';
    async function jobView_stop(id) {
      try { await api.raw(`${base}/jobs/${id}/cancel`, { method: 'POST', body: {} }); } catch (e) { toastError(e); }
      await refresh();
    }

    function loadToForm(job) {
      prompt.value = job.raw_prompt; negative.value = job.negative || '';
      if (job.params.duration) duration.value = job.params.duration;
      seed.value = job.params.seed;
      const known = state.models.find((m) => m.id === job.model);
      if (known) {
        state.modelId = job.model; state.kind = known.kind;
        kindGroup.querySelector(`#dg-kind-${known.kind}`).checked = true;
        renderModels(true); renderModelDetail(); applyLimits(true);
      }
      resolution.value = `${job.params.width}x${job.params.height}`;
      steps.value = job.params.steps || '';
      modeGroup.querySelector('#dg-mode-DIRECT').checked = true; setMode('DIRECT');
      countUpdate(); sumUpdate(); saveDraft();
      prompt.scrollIntoView({ block: 'center', behavior: 'auto' });
      prompt.focus();
      toastOk('Параметры подставлены', 'Prompt и seed скопированы в форму');
    }

    /* ---- опрос ---- */
    const renderStatus = (st) => {
      const rows = [
        h('div.dg-row',
          pill(st.runtime.reachable ? 'ComfyUI на связи' : 'ComfyUI недоступен', { tone: st.runtime.reachable ? 'ok' : 'warn' }),
          pill(st.sd_cli && st.sd_cli.present ? 'sd-cli на месте' : 'sd-cli не найден', { tone: st.sd_cli && st.sd_cli.present ? 'ok' : 'err' })),
        st.runtime.reason ? h('div.dg-small', st.runtime.reason) : null,
        st.memory.measured
          ? meter('Память', st.memory.used_mb, st.memory.total_mb, `занято ${Math.round(st.memory.used_mb / 1024)} из ${Math.round(st.memory.total_mb / 1024)} ГБ · свободно ${Math.round(st.memory.free_mb / 1024)} ГБ`)
          : h('div.dg-small.dg-dim', 'Память: не измерена'),
        h('div.dg-small', `Очередь: ${st.queue.running ? 'выполняется 1' : 'пусто'}, ждут ${st.queue.waiting}`),
        h('div.dg-small', st.assist.available ? `ASSISTED: Qwen ${st.assist.model}` : `ASSISTED недоступен: ${st.assist.reason}`),
      ];
      statusBox.replaceChildren(...rows.filter(Boolean));
    };
    const renderHistory = () => {
      const jobs = state.jobs.slice(0, 24);
      const sig = JSON.stringify([state.current, jobs.map((j) => [j.job_id, j.status])]);
      if (sig === state.histSig) return;
      state.histSig = sig;
      if (!jobs.length) {
        histBox.replaceChildren(h('div.bx-blank', h('span.bx-blank-icon', icon('empty', 24)), h('div.bx-blank-title', 'История пуста'),
          h('div.bx-blank-hint', 'Завершённые и неудавшиеся задачи появятся здесь. История видна только вам.')));
        return;
      }
      histBox.replaceChildren(h('ul.dg-hist-grid', jobs.map((j) => {
        const thumb = h('div.dg-thumb', { 'aria-hidden': 'true' });
        if (j.status === 'completed' && j.result && String(j.result.mime).startsWith('video/')) {
          thumb.append(h('video', { src: `${base}/jobs/${encodeURIComponent(j.job_id)}/file#t=0.1`, muted: true, playsinline: true, preload: 'metadata', tabindex: '-1' }));
        } else if (j.status === 'completed' && j.result && String(j.result.mime).startsWith('image/')) {
          thumb.append(h('img', { src: `${base}/jobs/${encodeURIComponent(j.job_id)}/file`, alt: '', loading: 'lazy' }));
        } else thumb.append(STATUS_SHORT[j.status] || j.status);
        const b = h('button.dg-hist', { type: 'button', 'aria-current': j.job_id === state.current ? 'true' : 'false', dataset: { job: j.job_id } },
          thumb,
          h('div.dg-hist-body',
            h('div.dg-row', pill(STATUS_SHORT[j.status] || j.status, { tone: STATUS_TONE[j.status] || 'idle' })),
            h('div.dg-hist-prompt', j.raw_prompt),
            h('div.dg-hist-meta.dg-num', `${fmtWhen(j.created_at)} · ${j.model} · seed ${j.params.seed}`)));
        b.setAttribute('aria-label', `Открыть задачу: ${STATUS_SHORT[j.status] || j.status}, ${j.model}, ${fmtWhen(j.created_at)}`);
        b.addEventListener('click', () => { state.current = j.job_id; refresh(); focusJob(); });
        return h('li', b);
      })));
    };

    let inflight = false;
    async function refresh() {
      if (inflight) return;
      inflight = true;
      try {
        try {
          const [st, list] = await Promise.all([api.raw(`${base}/status`), api.raw(`${base}/jobs`)]);
          state.jobs = list.jobs; renderStatus(st);
        } catch (e) { statusBox.replaceChildren(h('div.dg-alert', { role: 'alert' }, h('div.dg-alert-title', 'Состояние не получено'), h('pre.dg-raw', errText(e)))); }
        if (state.tick % 4 === 0) {
          try { state.models = (await api.raw(`${base}/models`)).models; renderModels(false); renderModelDetail(); } catch { /* список моделей остаётся прежним */ }
        }
        state.tick += 1;
        const job = state.jobs.find((j) => j.job_id === state.current) || state.jobs[0];
        if (job) {
          if (jobHost.firstChild !== jobView.el) jobHost.replaceChildren(jobView.el);
          jobView.update(job);
        } else if (jobHost.firstChild !== jobEmpty) jobHost.replaceChildren(jobEmpty);
        const live = job && LIVE.has(job.status);
        stopBtn.disabled = !live;
        renderHistory();
      } finally { inflight = false; }
    }

    /* ---- первичная загрузка ---- */
    try {
      state.models = (await api.raw(`${base}/models`)).models;
    } catch (e) {
      return h('div.bx-page.dg', { dataset: { testid: 'direct-gen' } }, head,
        h('div.dg-alert', { role: 'alert' }, h('div.dg-alert-title', 'Каталог моделей не загружен'), h('pre.dg-raw', errText(e)),
          btn('Повторить', () => { location.reload(); }, { variant: 'subtle', size: 'sm' })));
    }
    const draftModel = state.models.find((m) => m.id === D.model);
    if (draftModel) state.kind = draftModel.kind;
    kindGroup.querySelector(`#dg-kind-${state.kind}`).checked = true;
    state.modelId = (draftModel || pickModel(state.kind) || {}).id || null;
    renderModels(true); renderModelDetail(); applyLimits(!draftModel); countUpdate(); sumUpdate();
    if (draftModel && D.resolution) resolution.value = D.resolution;
    sumUpdate();
    setMode(D.mode || 'DIRECT');

    prompt.addEventListener('input', () => {
      countUpdate(); saveDraft();
      if (prompt.value.trim()) prompt.removeAttribute('aria-invalid');
      if (state.assist && prompt.value !== state.assist.original) { state.assist = null; showAssist(); }
    });
    prompt.addEventListener('keydown', (ev) => { if ((ev.ctrlKey || ev.metaKey) && ev.key === 'Enter') { ev.preventDefault(); createBtn.click(); } });
    for (const f of [negative, duration, resolution, seed]) f.addEventListener('input', () => { sumUpdate(); saveDraft(); });
    let objectUrl = null;
    image.addEventListener('change', () => {
      if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = null; }
      const f = image.files[0];
      imgPreview.replaceChildren();
      if (f) { objectUrl = URL.createObjectURL(f); imgPreview.append(h('img', { src: objectUrl, alt: 'Выбранное референс-изображение', style: { maxWidth: '96px', maxHeight: '64px', borderRadius: '8px', display: 'block', marginBottom: '4px' } }), `${f.name} · ${fmtBytes(f.size)}`); }
    });

    const requestPanel = panel('Запрос', h('div.dg-stack',
      h('div.dg-row', h('span.dg-label', { style: { margin: '0' }, id: 'dg-kind-l' }, 'Что создаём'), kindGroup),
      h('div.dg-row', h('span.dg-label', { style: { margin: '0' }, id: 'dg-mode-l' }, 'Режим'), modeGroup),
      h('div', lab('Prompt', 'dg-prompt'), h('div.dg-prompt-wrap', prompt, count), modeNote),
      paramsBox,
      h('div.dg-actions', createBtn, stopBtn),
      createErr,
      assistBox), { icon: 'bolt' });

    const modelPanel = panel('Модель', h('div.dg-stack', modelCards, modelDetail, modelsHelp));
    const readyPanel = panel('Готовность', statusBox, { icon: 'activity' });

    const root = h('div.bx-page.dg', { dataset: { testid: 'direct-gen' } }, head,
      h('div.dg-grid',
        h('div.dg-col', requestPanel),
        h('div.dg-col', modelPanel, readyPanel)),
      faceSwapPanel({ refresh, focusJob, setCurrent: (id) => { state.current = id; } }),
      genjutsuPanel(),
      panel('Текущая задача', jobHost, { icon: 'play' }),
      panel('История (только ваша)', histBox),
      h('p.dg-legal', { dataset: { testid: 'dg-legal' } }, LEGAL));

    await refresh();
    let misses = 0; let seen = false;
    const tick = async () => {
      if (!root.isConnected) {
        if (seen || (misses += 1) > 10) { if (objectUrl) URL.revokeObjectURL(objectUrl); return; }
      } else seen = true;
      if (!document.hidden) await refresh();
      const live = state.jobs.some((j) => LIVE.has(j.status));
      setTimeout(tick, live ? POLL_LIVE_MS : POLL_IDLE_MS);
    };
    setTimeout(tick, POLL_LIVE_MS);
    return root;
  },

  onEvent() { return false; },
};

export default DirectGenPage;
