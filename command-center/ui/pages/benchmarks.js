/* ============================================================
   benchmarks.js — Feature 04: Model Benchmark Lab.
   Endpoints: GET/POST /api/benchmarks, GET /api/benchmarks/{id},
   GET /api/benchmarks/recommendations.
   ============================================================ */

import { api, listOf, pick } from '../api.js';
import {
  h, icon, statusBadge,
  toast, toastOk, toastError, openModal, actionButton,
  field, select, fmtDateShort, fmtRelative, fmtNum,
} from '../components.js';
import { idVal, panel, pageHead, errorBanner, emptyPanel } from './_shared.js';

const BenchmarksPage = {
  id: 'benchmarks',
  title: 'Benchmark Lab',
  icon: 'bolt',
  nav: 'more',

  async render(ctx) {
    const [benchR, modelsR, recR] = await Promise.allSettled([
      api.raw('/api/benchmarks'), api.models(), api.raw('/api/benchmarks/recommendations'),
    ]);
    const benches = benchR.status === 'fulfilled' ? listOf(benchR.value, 'benchmarks') : [];
    const models = modelsR.status === 'fulfilled' ? listOf(modelsR.value, 'models') : [];
    const rec = recR.status === 'fulfilled' ? recR.value : null;
    ctx.state.models = models;

    const modelById = new Map(models.map((m) => [String(pick(m, ['id'])), m]));
    const modelLabel = (id) => { const m = modelById.get(String(id)); return m ? pick(m, ['alias', 'name'], `#${id}`) : `модель #${id}`; };

    const head = pageHead('Benchmark Lab', benches.length ? `${benches.length} прогонов` : 'Реальные замеры: TTFT, tok/s, latency, стабильность', [
      h('button.btn.btn-primary', { type: 'button', onClick: () => openStartBench(ctx, models) }, icon('play', 14), h('span', 'Запустить')),
    ]);

    const recPanel = panel('Рекомендации', rec && rec.for_speed
      ? h('div.row', h('span.small', 'Быстрее всех:'), h('div.spacer'),
        h('span.badge.badge-ok.mono', modelLabel(rec.for_speed.model_id)),
        h('span.xsmall.dim', `${fmtNum(rec.for_speed.gen_tps, 1)} tok/s`))
      : h('div.small.dim', rec ? `Пока недостаточно завершённых замеров (учтено: ${rec.based_on}).` : 'Недоступно.'));

    const body = benchR.status === 'rejected'
      ? errorBanner(benchR.reason, ctx)
      : benches.length
        ? h('div.grid.auto-lg', benches.map((b) => benchCard(b, modelLabel, ctx)))
        : emptyPanel({ iconName: 'bolt', title: 'Замеров ещё нет', hint: 'Запустите бенчмарк модели — прогон идёт в фоне, не блокирует интерфейс.' });

    return h('div.stack.lg', head, recPanel, body);
  },

  onEvent(ev) { return ev.kind.startsWith('benchmark.'); },
};

function benchCard(b, modelLabel, ctx) {
  const res = b.results || {};
  return h('div.card.clickable', { onClick: () => openBenchDetail(b.id, modelLabel), style: { cursor: 'pointer' } },
    h('div.card-head',
      h('div', { style: { flex: '1', minWidth: 0 } },
        h('div.card-title', modelLabel(b.model_id)),
        h('div.card-sub', `${b.kind || 'full'} · создан ${fmtRelative(b.created_at)}`)),
      statusBadge(b.status, { live: b.status === 'running' })),
    b.status === 'completed'
      ? h('div.stat-strip',
        res.gen_tps ? h('div', h('span.s-label', 'gen'), h('span.s-value', `${fmtNum(res.gen_tps, 1)} tok/s`)) : null,
        res.ttft_ms_approx ? h('div', h('span.s-label', 'TTFT'), h('span.s-value', `${fmtNum(res.ttft_ms_approx)} мс`)) : null,
        res.latency_ms_median ? h('div', h('span.s-label', 'latency'), h('span.s-value', `${fmtNum(res.latency_ms_median)} мс`)) : null,
        res.stability ? h('div', h('span.s-label', 'stability'), h('span.s-value', `${Math.round((res.stability.success_rate || 0) * 100)}%`)) : null)
      : b.status === 'failed' ? h('div.xsmall', { style: { color: 'var(--err)' } }, b.error || 'ошибка')
        : h('div.small.dim', 'выполняется…'));
}

function openStartBench(ctx, models) {
  if (!models.length) { toast('Сначала добавьте модель на странице «Модели»', { type: 'warn' }); return; }
  const modelEl = select(models.map((m) => ({ value: pick(m, ['id']), label: pick(m, ['alias', 'name'], `#${pick(m, ['id'])}`) })));
  const modal = openModal({
    title: 'Запустить benchmark',
    body: field('Модель', modelEl, '3 прогона на tok/s, TTFT approx, coding/reasoning-семплы, 5 запросов на стабильность — займёт до минуты.'),
    footer: (handle) => [
      h('div.spacer'),
      h('button.btn', { type: 'button', onClick: () => handle.close() }, 'Отмена'),
      actionButton('Запустить', async () => {
        try {
          await api.raw('/api/benchmarks', { method: 'POST', body: { model_id: idVal(modelEl.value) } });
          handle.close();
          toastOk('Benchmark запущен в фоне');
          ctx.refresh();
        } catch (e) { toastError(e, 'Не удалось запустить benchmark'); }
      }, { cls: 'btn btn-primary', iconName: 'play' }),
    ],
  });
}

async function openBenchDetail(id, modelLabel) {
  const modal = openModal({ title: `Benchmark #${id}`, wide: true, body: h('div.small.dim', 'Загрузка…'), footer: h('div') });
  modal.footer.appendChild(h('div.spacer'));
  modal.footer.appendChild(h('button.btn', { type: 'button', onClick: () => modal.close() }, 'Закрыть'));
  let b;
  try { b = await api.raw(`/api/benchmarks/${encodeURIComponent(id)}`); }
  catch (e) {
    modal.body.textContent = '';
    modal.body.appendChild(h('div.small', { style: { color: 'var(--err)' } }, e.message || 'Не удалось загрузить'));
    return;
  }
  const res = b.results || {};
  modal.body.textContent = '';
  modal.el.querySelector('.modal-head h2').textContent = `Benchmark #${id} · ${modelLabel(b.model_id)}`;
  modal.body.appendChild(h('div.stack',
    h('div.row', statusBadge(b.status, { live: b.status === 'running' }), h('div.spacer'),
      h('span.xsmall.dim', fmtDateShort(b.created_at))),
    b.status === 'failed' ? h('div.small', { style: { color: 'var(--err)' } }, b.error) : null,
    b.status === 'completed' ? h('div.stack.sm',
      h('div.stat-strip',
        h('div', h('span.s-label', 'TTFT approx'), h('span.s-value', res.ttft_ms_approx ? `${fmtNum(res.ttft_ms_approx)} мс` : '—')),
        h('div', h('span.s-label', 'prompt tok/s'), h('span.s-value', res.prompt_tps ? fmtNum(res.prompt_tps, 1) : '—')),
        h('div', h('span.s-label', 'gen tok/s'), h('span.s-value', res.gen_tps ? fmtNum(res.gen_tps, 1) : '—')),
        h('div', h('span.s-label', 'latency медиана'), h('span.s-value', res.latency_ms_median ? `${fmtNum(res.latency_ms_median)} мс` : '—'))),
      res.stability ? h('div.row', h('span.small', 'Стабильность:'), h('div.spacer'),
        h('span.badge', `${Math.round((res.stability.success_rate || 0) * 100)}% успехов`),
        h('span.xsmall.dim', `σ latency ${fmtNum(res.stability.latency_stdev_ms, 1)} мс`)) : null,
      res.coding_sample ? panel('Coding sample', h('pre.block', res.coding_sample)) : null,
      res.reasoning_sample ? panel('Reasoning sample', h('pre.block', res.reasoning_sample)) : null,
      h('div.xsmall.dim', `tool calling: ${res.tool_calling || '—'}`)) : null));
}

export default BenchmarksPage;
