/* ============================================================
   motion_studio.js — Motion Studio: пресет Epic, предпросмотр, музыка, субтитры,
   полный рендер, проверка файла. Тонкая панель над tools/motion_studio/make_video.py.
   Endpoints (bcc/features/motion_studio.py, под /api):
     GET /api/motion-studio/status, GET /examples/{name}, POST /jobs, GET /jobs/{id},
     POST /jobs/{id}/cancel, GET /jobs/{id}/check, GET /jobs/{id}/file?name=…
   Рендер идёт отдельным процессом; «готово» — только после проверки файла (ffprobe).
   ============================================================ */

import { api } from '../api.js';
import { h, toastOk, toastError, actionButton, field, textarea, select, checkbox } from '../components.js';
import { panel, pageHead, pill } from './_ui.js';

const STATE_LABEL = { running: 'идёт', done: 'готово и проверено', failed: 'ошибка', cancelled: 'остановлено', interrupted: 'прервано' };
const STATE_TONE = { running: 'warn', done: 'ok', failed: 'err', cancelled: 'idle', interrupted: 'err' };
const POLL_MS = 2000;

function fileUrl(job, name) {
  return `/api/motion-studio/jobs/${encodeURIComponent(job.id)}/file?name=${encodeURIComponent(name)}`;
}

function jobCard(ctx, job) {
  const state = h('div.row.tight', { style: { gap: '8px', flexWrap: 'wrap' } },
    pill(STATE_LABEL[job.state] || job.state, { tone: STATE_TONE[job.state] || 'idle', live: job.state === 'running' }),
    h('span.small.dim', `${job.mode === 'full' ? 'полный рендер' : 'предпросмотр'} · Epic · сцен ${job.scenes} · субтитров ${job.subtitle_lines}`
      + (job.mode === 'full' ? ` · музыка ${job.music ? 'да' : 'нет'} · озвучка ${job.no_voice ? 'нет' : 'да'}` : '')));
  const body = h('div.stack.sm', { dataset: { testid: 'motion-job', job: job.id } }, state,
    job.error ? h('div.small', { style: { color: 'var(--err)' } }, job.error) : null);
  if (job.state === 'running') {
    body.append(actionButton('Остановить', async () => {
      try { await api.raw(`/api/motion-studio/jobs/${job.id}/cancel`, { method: 'POST', body: {} }); ctx.refresh(); }
      catch (e) { toastError(e); }
    }, { cls: 'btn btn-sm' }));
  }
  const previews = (job.outputs || []).filter((n) => n.startsWith('epic-preview-'));
  if (previews.length) {
    body.append(h('div.row.tight', { style: { flexWrap: 'wrap', gap: '8px' } },
      ...previews.map((n) => h('img', { src: fileUrl(job, n), alt: n, style: { width: '240px', borderRadius: '6px' } }))));
  }
  if (job.state === 'done' && (job.outputs || []).includes('video.mp4')) {
    body.append(h('video', { src: fileUrl(job, 'video.mp4'), controls: true, preload: 'metadata', style: { maxWidth: '480px' } }),
      h('div.row.tight', { style: { gap: '8px' } },
        h('a.btn.btn-sm', { href: fileUrl(job, 'video.mp4'), download: 'video.mp4' }, 'Скачать видео'),
        actionButton('Проверить файл', async () => {
          try {
            const c = await api.raw(`/api/motion-studio/jobs/${job.id}/check`);
            const box = body.querySelector('[data-check]') || h('div.small', { dataset: { check: '1' } });
            box.textContent = c.verified
              ? `Файл проверен: ${c.duration_s} с, ${c.video && c.video.width}×${c.video && c.video.height}, видео ${c.video && c.video.codec}, звук ${c.audio && c.audio.codec}, ${c.bytes} байт, sha256 ${String(c.sha256).slice(0, 16)}…`
              : `Файл НЕ прошёл проверку: ${c.reason}`;
            if (!box.parentNode) body.append(box);
          } catch (e) { toastError(e); }
        }, { cls: 'btn btn-sm', iconName: 'check' })));
  }
  if (job.log_tail && job.state !== 'done') body.append(h('pre.xsmall.mono', { style: { whiteSpace: 'pre-wrap', maxHeight: '140px', overflow: 'auto' } }, job.log_tail));
  return body;
}

const MotionStudioPage = {
  id: 'motion-studio',
  title: 'Motion Studio',
  icon: 'bolt',
  nav: 'more',
  section: 'studio',

  async render(ctx) {
    const head = pageHead('Motion Studio',
      'Моушн-трейлер из сценария: пресет Epic, предпросмотр кадров, оригинальная музыка, субтитры из сценария, полный рендер и проверка файла. Всё локально, без облака.');
    let st;
    try { st = await api.raw('/api/motion-studio/status'); } catch (e) {
      return h('div.bx-page', head, panel('Не удалось загрузить', h('div.small', e.message || String(e))));
    }
    if (!st.available) {
      return h('div.bx-page', head, panel('Недоступно', h('div.small', st.why_not || 'Motion Studio не входит в эту сборку.')));
    }
    const dep = (name, ok) => pill(`${name}: ${ok ? 'есть' : 'нет'}`, { tone: ok ? 'ok' : 'warn' });
    const status = h('div.row.tight', { style: { flexWrap: 'wrap', gap: '8px' } },
      ...Object.entries(st.deps).map(([k, v]) => dep(k, v)),
      st.why_not ? h('span.small', { style: { color: 'var(--err)' } }, st.why_not) : null);

    const exampleSel = select(st.examples.map((n) => ({ value: n, label: n })), { name: 'ms-example', value: st.examples.includes('bossman_epic_22s') ? 'bossman_epic_22s' : st.examples[0] });
    const spec = textarea({ rows: 10, name: 'ms-spec', spellcheck: 'false', style: { fontFamily: 'var(--mono, monospace)' } });
    const loadExample = async () => {
      try { spec.value = JSON.stringify(await api.raw(`/api/motion-studio/examples/${encodeURIComponent(exampleSel.value)}`), null, 2); }
      catch (e) { toastError(e); }
    };
    exampleSel.addEventListener('change', loadExample);
    if (exampleSel.value) loadExample();
    const noVoice = checkbox('Без озвучки (только музыка и субтитры)', true, { name: 'ms-novoice' });

    const start = (mode) => async () => {
      let parsed;
      try { parsed = JSON.parse(spec.value); } catch { toastError(new Error('Сценарий — не корректный JSON')); return; }
      try {
        await api.raw('/api/motion-studio/jobs', { method: 'POST', body: {
          mode, spec: parsed, no_voice: noVoice.querySelector('input').checked } });
        toastOk(mode === 'full' ? 'Полный рендер запущен' : 'Предпросмотр запущен', 'Ход виден ниже');
        ctx.refresh();
      } catch (e) { toastError(e); }
    };
    const running = st.jobs.some((j) => j.state === 'running');
    const root = h('div.bx-page', { dataset: { testid: 'motion-studio' } }, head,
      panel('Готовность', status),
      panel('Сценарий', h('div.stack.sm', field('Пример', exampleSel), field('Сценарий (JSON)', spec), noVoice,
        h('div.row.tight', { style: { gap: '8px' } },
          actionButton('Предпросмотр кадров', start('preview'), { cls: 'btn', disabled: !st.ready_preview || running }),
          actionButton('Полный рендер (музыка + субтитры)', start('full'), { cls: 'btn btn-primary', disabled: !st.ready_full || running })))),
      panel('Рендеры', st.jobs.length ? h('div.stack.sm', ...st.jobs.map((j) => jobCard(ctx, j)))
        : h('div.small.dim', 'Рендеров пока не было.')));
    if (running) {
      setTimeout(() => { if (root.isConnected) ctx.refresh(); }, POLL_MS);
    }
    return root;
  },

  onEvent() { return false; },
};

export default MotionStudioPage;
