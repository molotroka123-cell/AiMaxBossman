/* ============================================================
   direct_gen.js — Прямой генератор видео (DIRECT / ASSISTED).
   Endpoints (bcc/features/direct_gen.py, под /api):
     GET /direct-gen/models, GET /status, GET|POST /jobs, GET /jobs/{id},
     POST /jobs/{id}/cancel, POST /jobs/{id}/retry, GET /jobs/{id}/file, POST /assist
   DIRECT: prompt уходит в выбранную модель как есть. ASSISTED: локальный Qwen
   предлагает версию, показываются оба текста, выбирает человек.
   Процент — только из реальных шагов ComfyUI; иначе этап и прошедшее время.
   Страница не перерисовывается целиком: поля формы живут между опросами.
   ============================================================ */

import { api } from '../api.js';
import { h, toastOk, toastError, actionButton, field, textarea, select } from '../components.js';
import { panel, pageHead, pill } from './_ui.js';

const STATUS_LABEL = { queued: 'в очереди', loading: 'загрузка', generating: 'генерация', postprocessing: 'сохранение и проверка', completed: 'готово', failed: 'ошибка', cancelled: 'остановлено' };
const STATUS_TONE = { queued: 'idle', loading: 'warn', generating: 'warn', postprocessing: 'warn', completed: 'ok', failed: 'err', cancelled: 'idle' };
const LIVE = new Set(['queued', 'loading', 'generating', 'postprocessing']);
const POLL_MS = 1500;
const base = '/api/direct-gen';

const errText = (e) => (e && e.message) || String(e);

function readB64(file) {
  return new Promise((resolve, reject) => {
    if (!file) { resolve(null); return; }
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(',', 2)[1] || null);
    r.onerror = () => reject(new Error('Не удалось прочитать файл'));
    r.readAsDataURL(file);
  });
}

function progressView(job) {
  const p = job.progress || { kind: 'none' };
  const wrap = h('div.stack.sm', { dataset: { testid: 'dg-progress' } });
  if (p.kind === 'steps') {
    wrap.append(h('progress', { max: p.max, value: p.value, style: { width: '100%' } }),
      h('div.small', `Шаг ${p.value} из ${p.max} (${p.percent}%) — данные ComfyUI`));
  } else if (LIVE.has(job.status)) {
    wrap.append(h('div.small.dim', 'Процент недоступен: модель не сообщает шаги. Показаны этап и прошедшее время.'));
  }
  return wrap;
}

function jobView(job, onChange) {
  const live = LIVE.has(job.status);
  const body = h('div.stack.sm', { dataset: { testid: 'dg-job', job: job.job_id } },
    h('div.row.tight', { style: { gap: '8px', flexWrap: 'wrap' } },
      pill(STATUS_LABEL[job.status] || job.status, { tone: STATUS_TONE[job.status] || 'idle', live }),
      h('span.small', `${job.model} · ${job.mode} · seed ${job.params.seed} · ${job.params.width}×${job.params.height} · ${job.params.duration} с (${job.params.frames} кадров)`),
      h('span.small.dim', `прошло ${job.elapsed_s} с` + (job.status === 'queued' ? ` · позиция в очереди ${job.queue_position}` : ''))),
    progressView(job));
  const actions = h('div.row.tight', { style: { gap: '8px', flexWrap: 'wrap' } });
  if (live) {
    actions.append(actionButton('STOP', async () => {
      try { await api.raw(`${base}/jobs/${job.job_id}/cancel`, { method: 'POST', body: {} }); onChange(); }
      catch (e) { toastError(e); }
    }, { cls: 'btn btn-sm btn-danger' }));
  } else {
    actions.append(actionButton('Повторить с тем же seed', async () => {
      try { const j = await api.raw(`${base}/jobs/${job.job_id}/retry`, { method: 'POST', body: {} }); toastOk('Повтор запущен', `seed ${j.params.seed}`); onChange(j.job_id); }
      catch (e) { toastError(e); }
    }, { cls: 'btn btn-sm' }));
  }
  body.append(actions);
  if (job.status === 'completed' && job.result) {
    const url = `${base}/jobs/${encodeURIComponent(job.job_id)}/file`;
    body.append(job.result.mime.startsWith('video/')
      ? h('video', { src: url, controls: true, preload: 'metadata', style: { maxWidth: '520px', width: '100%' } })
      : h('img', { src: url, alt: 'result', style: { maxWidth: '520px' } }),
    h('div.row.tight', { style: { gap: '8px' } },
      h('a.btn.btn-sm', { href: url, download: 'bossman-direct' }, 'Сохранить'),
      h('span.small.dim', `${job.result.bytes} байт · sha256 ${job.result.sha256.slice(0, 16)}… · ` +
        (job.result.verified ? `проверен ffprobe, ${job.result.duration_s} с` : `не проверен независимо (${job.result.reason || 'нет данных'})`))));
  }
  if (job.error) {
    body.append(h('div.small', { style: { color: 'var(--err)' }, dataset: { testid: 'dg-error' } },
      `Ошибка [${job.error.code}]: ${job.error.message}`));
  }
  if (job.warning) body.append(h('div.small.dim', job.warning));
  body.append(h('details', h('summary.small', 'Журнал и происхождение'),
    h('pre.xsmall.mono', { style: { whiteSpace: 'pre-wrap', maxHeight: '180px', overflow: 'auto' } },
      job.timeline.map((t) => `${t.at}  ${t.stage}${t.note ? '  ' + t.note : ''}`).join('\n') +
      '\n\n' + JSON.stringify({ raw_prompt: job.raw_prompt, effective_prompt: job.effective_prompt, negative: job.negative, provenance: job.provenance }, null, 1))));
  return body;
}

const DirectGenPage = {
  id: 'direct-gen',
  title: 'Прямой генератор',
  icon: 'bolt',
  nav: 'more',
  section: 'studio',

  async render(ctx) {
    const head = pageHead('Прямой генератор видео',
      'Локальная генерация для совершеннолетних. DIRECT: ваш текст уходит в выбранную модель без изменений и без промежуточной модерации. Отказы, нехватка памяти и ошибки показываются как есть. Не для несовершеннолетних и не для интимных изображений реальных людей без их согласия.');
    const state = { models: [], jobs: [], current: null, assist: null };
    const modelSel = select([], { name: 'dg-model' });
    const modelNote = h('div.small', { dataset: { testid: 'dg-model-note' } });
    const modeSel = select([{ value: 'DIRECT', label: 'DIRECT — как написано' }, { value: 'ASSISTED', label: 'ASSISTED — с редактурой Qwen (вы выбираете)' }], { name: 'dg-mode', value: 'DIRECT' });
    const prompt = textarea({ rows: 8, name: 'dg-prompt', placeholder: 'Опишите сцену', style: { width: '100%', fontSize: '1.05em' } });
    const negative = textarea({ rows: 2, name: 'dg-negative', placeholder: 'Negative (необязательно)' });
    const image = h('input', { type: 'file', accept: 'image/*', name: 'dg-image' });
    const audio = h('input', { type: 'file', accept: 'audio/*', name: 'dg-audio' });
    const duration = h('input.input', { type: 'number', min: 1, step: 0.5, value: 4, name: 'dg-duration' });
    const resolution = h('input.input', { type: 'text', value: '480x320', name: 'dg-resolution' });
    const seed = h('input.input', { type: 'number', min: 0, placeholder: 'случайный', name: 'dg-seed' });
    const statusBox = h('div.stack.sm', { dataset: { testid: 'dg-status' } });
    const assistBox = h('div.stack.sm', { dataset: { testid: 'dg-assist' } });
    const jobBox = h('div.stack.sm');
    const historyBox = h('div.stack.sm');

    const selected = () => state.models.find((m) => m.id === modelSel.value);
    const showModel = () => {
      const m = selected();
      modelNote.replaceChildren();
      if (!m) return;
      modelNote.append(
        pill(m.available ? 'доступна' : 'недоступна', { tone: m.available ? 'ok' : 'err' }),
        h('span.small', ` ${m.modes.join('/')} · ${m.runtime} · до ${m.max_seconds} с · стороны ${m.min_side}–${m.max_side} кратно ${m.side_multiple} · ${m.fps} к/с`),
        m.reason ? h('div.small', { style: { color: 'var(--err)' } }, m.reason) : null,
        m.requires_image ? h('div.small', 'Нужно референс-изображение.') : null,
        m.requires_audio ? h('div.small', 'Нужен аудиофайл.') : null,
        h('div.small.dim', `источник ${m.source} · ревизия ${m.revision} · лицензия ${m.license} · sha256 ${m.sha256 === 'UNKNOWN' ? 'UNKNOWN' : m.sha256.slice(0, 16) + '…'}`));
    };
    modelSel.addEventListener('change', showModel);

    const body = async (extra = {}) => ({
      model: modelSel.value, prompt: prompt.value, negative: negative.value,
      duration: Number(duration.value), resolution: resolution.value.trim(),
      seed: seed.value === '' ? null : Number(seed.value), mode: modeSel.value,
      image_b64: await readB64(image.files[0]), audio_b64: await readB64(audio.files[0]), ...extra,
    });
    const submit = async (extra) => {
      try {
        const job = await api.raw(`${base}/jobs`, { method: 'POST', body: await body(extra) });
        state.current = job.job_id;
        toastOk('Задача создана', `seed ${job.params.seed}`);
        await refresh();
      } catch (e) { toastError(e); }
    };

    const showAssist = () => {
      assistBox.replaceChildren();
      const a = state.assist;
      if (!a) return;
      assistBox.append(
        h('div.small', `Версия предложена моделью ${a.model}. Ничего не применено — выберите сами.`),
        h('div.row', { style: { gap: '12px', alignItems: 'flex-start', flexWrap: 'wrap' } },
          h('div.stack.sm', { style: { flex: '1 1 280px' } }, h('strong.small', 'Оригинал'), h('pre.small', { style: { whiteSpace: 'pre-wrap' } }, a.original)),
          h('div.stack.sm', { style: { flex: '1 1 280px' } }, h('strong.small', 'Версия Qwen'), h('pre.small', { style: { whiteSpace: 'pre-wrap' } }, a.suggested))),
        h('div.row.tight', { style: { gap: '8px' } },
          actionButton('Создать с оригиналом', () => submit({ prompt: a.original, mode: 'ASSISTED', assist_id: a.assist_id, assist_choice: 'original' }), { cls: 'btn' }),
          actionButton('Создать с версией Qwen', () => submit({ prompt: a.original, mode: 'ASSISTED', assist_id: a.assist_id, assist_choice: 'suggested' }), { cls: 'btn btn-primary' })));
    };

    const create = actionButton('Создать', async () => {
      if (modeSel.value === 'ASSISTED') {
        try { state.assist = await api.raw(`${base}/assist`, { method: 'POST', body: { prompt: prompt.value } }); showAssist(); }
        catch (e) { state.assist = null; showAssist(); toastError(e); }
        return;
      }
      await submit();
    }, { cls: 'btn btn-primary' });

    const refresh = async (focusId) => {
      if (focusId) state.current = focusId;
      try {
        const [st, list] = await Promise.all([api.raw(`${base}/status`), api.raw(`${base}/jobs`)]);
        state.jobs = list.jobs;
        statusBox.replaceChildren(
          pill(st.runtime.reachable ? 'ComfyUI на связи' : 'ComfyUI недоступен', { tone: st.runtime.reachable ? 'ok' : 'err' }),
          st.runtime.reason ? h('span.small', ` ${st.runtime.reason}`) : null,
          h('span.small', st.memory.measured ? ` · память: занято ${Math.round(st.memory.used_mb / 1024)} из ${Math.round(st.memory.total_mb / 1024)} ГБ, свободно ${Math.round(st.memory.free_mb / 1024)} ГБ` : ' · память: не измерена'),
          h('span.small.dim', ` · очередь: ${st.queue.running ? 'идёт 1' : 'пусто'}, ждут ${st.queue.waiting}`),
          h('div.small', st.assist.available ? `ASSISTED: Qwen ${st.assist.model}` : `ASSISTED недоступен: ${st.assist.reason}`));
      } catch (e) { statusBox.replaceChildren(h('div.small', { style: { color: 'var(--err)' } }, errText(e))); }
      const job = state.jobs.find((j) => j.job_id === state.current) || state.jobs[0];
      jobBox.replaceChildren(job ? jobView(job, refresh) : h('div.small.dim', 'Задач пока не было.'));
      historyBox.replaceChildren(...state.jobs.slice(0, 15).map((j) => h('div.row.tight', { style: { gap: '8px' } },
        pill(STATUS_LABEL[j.status] || j.status, { tone: STATUS_TONE[j.status] || 'idle' }),
        h('a.small', { href: '#', onclick: (ev) => { ev.preventDefault(); state.current = j.job_id; refresh(); } }, `${j.created_at.slice(0, 19)} · ${j.model} · seed ${j.params.seed}`))));
    };

    try {
      state.models = (await api.raw(`${base}/models`)).models;
    } catch (e) {
      return h('div.bx-page', head, panel('Не удалось загрузить', h('div.small', errText(e))));
    }
    for (const m of state.models) modelSel.append(h('option', { value: m.id }, `${m.label}${m.available ? '' : ' — недоступна'}`));
    const firstOk = state.models.find((m) => m.available) || state.models[0];
    if (firstOk) modelSel.value = firstOk.id;
    showModel();

    const root = h('div.bx-page', { dataset: { testid: 'direct-gen' } }, head,
      panel('Модель и готовность', h('div.stack.sm', field('Видео-модель (установленные)', modelSel), modelNote, statusBox)),
      panel('Запрос', h('div.stack.sm', field('Режим', modeSel), field('Prompt', prompt), field('Negative', negative),
        h('div.row', { style: { gap: '12px', flexWrap: 'wrap' } }, field('Референс-изображение', image), field('Аудио (для S2V)', audio)),
        h('div.row', { style: { gap: '12px', flexWrap: 'wrap' } }, field('Длительность, с', duration), field('Разрешение', resolution), field('Seed', seed)),
        h('div.row.tight', { style: { gap: '8px' } }, create), assistBox)),
      panel('Текущая задача', jobBox),
      panel('История (только ваша)', historyBox));

    await refresh();
    const timer = setInterval(() => {
      if (!root.isConnected) { clearInterval(timer); return; }
      refresh();
    }, POLL_MS);
    return root;
  },

  onEvent() { return false; },
};

export default DirectGenPage;
