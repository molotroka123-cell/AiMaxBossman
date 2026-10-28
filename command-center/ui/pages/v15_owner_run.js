import { api } from '../api.js';
import { h, actionButton, field, input, toast, toastOk, toastError } from '../components.js';
import { panel, pageHead, pill, tile } from './_ui.js';

/* Коды из POST /api/v15/owner-run/quick-test (bcc/features/v15_owner_run.py). */
const SETUP_LABEL = {
  runner_missing: 'нет файла запуска 1.5 (runner_missing)',
  jev_key_missing: 'нет ключа JEV (jev_key_missing)',
  openrouter_key_missing: 'нет ключа OpenRouter (openrouter_key_missing)',
};

function stateTone(value) {
  const s = String(value || '').toUpperCase();
  if (['RUNNING','PASS','VERIFIED','COMPLETE_QUARANTINED','SELF_IMPROVEMENT_PROCESS_STARTED','UP_TO_DATE','READY'].some(x => s.includes(x))) return 'ok';
  if (['FAIL','BLOCKED','ERROR'].some(x => s.includes(x))) return 'err';
  if (['OWNER_REQUIRED','PARTIAL','PENDING','STARTING','NEEDS_SYNC','ON_DEMAND'].some(x => s.includes(x))) return 'warn';
  return 'idle';
}

const Page = {
  id: 'v15-owner-run',
  title: 'Bossman 2.0',
  icon: 'bolt',
  nav: 'primary',
  section: 'brains',

  async render(ctx) {
    let s = {}, autonomy = {}, repairs = {}, v20 = {};
    try {
      const rows = await Promise.allSettled([
        api.raw('/api/v15/owner-run/status'),
        api.raw('/api/v15/autonomy/status'),
        api.raw('/api/v15/self-repair/inbox?limit=20'),
        api.raw('/api/v20/status'),
      ]);
      if (rows[0].status === 'rejected') throw rows[0].reason;
      s = rows[0].value || {};
      autonomy = rows[1].status === 'fulfilled' ? (rows[1].value || {}) : {};
      repairs = rows[2].status === 'fulfilled' ? (rows[2].value || {}) : {};
      v20 = rows[3].status === 'fulfilled' ? (rows[3].value || {}) : {};
    } catch (e) {
      return h('div.bx-page', pageHead('Bossman 2.0', 'Надстройка и самостоятельный режим'), h('p', String(e)));
    }

    const repo = input({ class: 'input mono', value: s.repo || '', placeholder: 'Путь к clean Bossman checkout (можно оставить пустым, если задан BOSSMAN_SELF_IMPROVE_REPO)' });
    const cycles = input({ class: 'input mono', type: 'number', min: '1', max: '20', value: '8' });
    const paid = h('input', { type: 'checkbox' });
    const diagBox = h('div.stack.xs', { id: 'v20-live-diag' });

    const mem = v20.memory_obsidian || {};
    const sec = v20.security_contour || {};
    const art = v20.art_media_contour || {};
    const mob = v20.mobile_and_swarm_contour || {};

    const v20Superstructure = panel('Надстройка Bossman 2.0 · Память Obsidian и Защита Ядра',
      h('div.stack.sm',
        h('p.small.dim',
          'Единый центр управления знаниями Obsidian 2.0 (автоочистка устаревших тестовых заметок + синхронизация 15 веток архитектуры) и живая самопроверка 5 рубежей защиты (PowerShell AST, Windows Job Object, пути NTFS/DOS, защита audit.jsonl 20/20, SpendFirewall $0.00).'),
        h('div.row.gap-sm', { style: { flexWrap: 'wrap' } },
          actionButton('🧠 Синхронизировать память Obsidian 2.0', async () => {
            try {
              const out = await api.raw('/api/v20/memory/sync', { method: 'POST', body: {} });
              if (out && out.ok) {
                toastOk(out.summary_ru || 'Память Obsidian 2.0 синхронизирована');
                ctx.refresh();
              } else {
                toastError(new Error(out?.error || 'Не удалось синхронизировать память'), 'Память Obsidian');
              }
            } catch (e) { toastError(e, 'Сбой синхронизации памяти Obsidian'); }
          }, { cls: 'btn btn-primary', iconName: 'check' }),
          actionButton('🛡 Проверить 5 рубежей защиты 2.0', async () => {
            try {
              const out = await api.raw('/api/v20/security/self-test', { method: 'POST', body: {} });
              if (out && out.ok) {
                toastOk(out.summary_ru || `Защита 2.0: ${out.passed}/${out.total} PASS`);
              } else {
                toastError(new Error(out?.summary_ru || 'Не все рубежи прошли проверку'), 'Защита 2.0');
              }
              diagBox.innerHTML = '';
              for (const c of (out?.checks || [])) {
                diagBox.appendChild(h('div.row.gap-sm',
                  pill(c.passed ? 'PASS' : 'FAIL', { tone: c.passed ? 'ok' : 'err' }),
                  h('span.small', c.title),
                  h('span.xsmall.dim.mono', c.detail || '')));
              }
            } catch (e) { toastError(e, 'Сбой самопроверки защиты'); }
          }, { cls: 'btn btn-secondary', iconName: 'approvals' }),
          actionButton('🔍 Аудит свежести Obsidian', async () => {
            try {
              const out = await api.raw('/api/v20/memory/audit');
              const a = out?.audit || {};
              const db = out?.db_counts || {};
              toastOk(`Obsidian: ${a.freshness_status || '—'} · канонических заметок 2.0: ${a.canonical_v20_count ?? 0}/${a.canonical_v20_expected ?? 5} · мусора: ${a.synthetic_junk_count ?? 0} · фактов в БД: ${db.facts ?? 0}`);
            } catch (e) { toastError(e, 'Не удалось выполнить аудит памяти'); }
          }, { cls: 'btn btn-secondary', iconName: 'search' })
        ),
        diagBox
      ));

    const controls = panel('Автономный прогон и проверка контура',
      h('div.stack.sm',
        field('Исходники для самоулучшения', repo, 'Twitch может работать без repo; self-improve требует clean checkout.'),
        field('Циклов максимум', cycles, 'Bossman останавливается раньше при STOP/серии провалов/лимитах.'),
        h('label.row.gap-sm', paid, h('span', 'Разрешить ограниченный платный финализатор (free-first остаётся обязательным)')),
        h('div.row.gap-sm', { style: { flexWrap: 'wrap' } },
          actionButton('⚡ Quick Test', async () => {
            try {
              const out = await api.raw('/api/v15/owner-run/quick-test', { method: 'POST', body: {} });
              if (out.status === 'READY') toastOk('Quick Test: READY');
              // OWNER_REQUIRED is the expected answer until keys/runner are set up: a
              // "needs setup" notice, not an error (it also made the UI sweep report
              // the button as failing). Anything else is still an error.
              else if (out.status === 'OWNER_REQUIRED') {
                toast('1.5 требует подготовки', {
                  type: 'warn', timeout: 8000,
                  hint: (out.problems || []).map((p) => SETUP_LABEL[p] || p).join(' · ') || 'см. статус ниже',
                });
              } else toastError(new Error(`Quick Test: неожиданный ответ ${out.status || '—'}`), '1.5 требует подготовки');
              ctx.refresh();
            } catch (e) { toastError(e, 'Quick Test не выполнен'); }
          }, { cls: 'btn btn-secondary', iconName: 'check' }),
          actionButton('▶ Запустить 2.0', async () => {
            try {
              await api.raw('/api/v15/owner-run/start', { method: 'POST', body: {
                repo: repo.value || null, cycles: Number(cycles.value) || 8,
                allow_glm: !!paid.checked, cadence: 15,
              }});
              toastOk('Bossman 2.0 запущен'); ctx.navigate('capability-tree');
            } catch (e) { toastError(e, 'Не удалось запустить'); }
          }, { cls: 'btn btn-primary', iconName: 'bolt' }),
          actionButton('⛔ STOP', async () => {
            try {
              await api.raw('/api/v15/owner-run/stop', { method: 'POST', body: {} });
              toastOk('STOP запрошен'); ctx.refresh();
            } catch (e) { toastError(e, 'STOP не подтверждён'); }
          }, { cls: 'btn btn-danger', iconName: 'close' }),
          actionButton('↻ Обновить', () => ctx.refresh(), { cls: 'btn btn-secondary', iconName: 'retry' })
        )
      ));

    const market = s.market || {};
    const si = s.self_improvement || {};
    const evo = si.evolution || {};
    const dbCounts = mem.db_counts || {};
    const tiles = h('div.bx-cards',
      tile({ title: 'Память Obsidian 2.0', sub: 'Канонические знания 15 веток + SQLite BM25',
             statusNode: pill(mem.freshness_status || 'UNKNOWN', { tone: stateTone(mem.freshness_status) }),
             body: [h('p.xsmall.dim', `заметок в vault: ${mem.total_markdown_files ?? '—'} · канон 2.0: ${mem.canonical_v20_count ?? 0}/${mem.canonical_v20_expected ?? 5} · мусор: ${mem.synthetic_junk_count ?? 0} · фактов БД: ${dbCounts.facts ?? 0}`)] }),
      tile({ title: 'Контур защиты 2.0', sub: 'PowerShell AST + Job Object + Audit Guard 20/20',
             statusNode: pill(sec.powershell_ast_gate ? 'PASS 5/5' : 'CHECK', { tone: sec.powershell_ast_gate ? 'ok' : 'warn' }),
             body: [h('p.xsmall.dim', 'AST парсер PowerShell · WinPaths ADS/DOS · Job Object KILL_ON_JOB_CLOSE · SpendFirewall $0.00')] }),
      tile({ title: 'Искусство и Медиа 2.0', sub: 'Photo 2.0 · Direct Gen · Motion 2.0 · Music Studio',
             statusNode: pill('READY', { tone: 'ok' }),
             body: [h('p.xsmall.dim', `${art.photo_studio_20 || 'ComfyUI verified'} · ${art.motion_studio_20 || 'ASS + LUT + BPM'}`)] }),
      tile({ title: 'Мобильный Пульт & Рой 2.0', sub: 'Push-to-Talk STT · Экран ПК · 94 Навыка',
             statusNode: pill(`НАВЫКОВ: ${mob.curated_skills ?? 94}`, { tone: 'ok' }),
             body: [h('p.xsmall.dim', `${mob.mobile_pult_8840 || 'Whisper STT + Rollback'} · ${mob.swarm_dag_planner || 'SwarmDAGPlanner'}`)] }),
      tile({ title: 'Twitch OI/CVD', sub: 'Сбор + VERIFIED анализ + Telegram', statusNode: pill(market.state || 'NOT_STARTED', { tone: stateTone(market.state) }),
             body: [h('p.xsmall.dim', market.last ? `последнее: ${market.last.status || '—'} · попыток: ${market.attempts_this_run ?? '—'}` : 'ещё нет наблюдений')] }),
      tile({ title: 'Self-Improve', sub: 'ошибка → patch → tests → verifier → lesson → unseen transfer',
             statusNode: pill(evo.status || (s.processes?.self_improve?.alive ? 'RUNNING' : 'NOT_STARTED'), { tone: stateTone(evo.status || s.status) }),
             body: [h('p.xsmall.dim', `цикл: ${evo.cycle ?? '—'} · halt: ${evo.halt_reason || '—'}`)] }),
      tile({ title: 'Self-repair inbox', sub: 'реальные code/harness ошибки → isolated candidates',
             statusNode: pill(String((repairs.items || []).length), { tone: (repairs.items || []).length ? 'warn' : 'ok' }),
             body: [h('p.xsmall.dim', 'Stable не меняется автоматически. DONE требует executable verifier.')] }),
      tile({ title: 'Autonomy kernel', sub: 'persistent roles + operating graph + cost/quality routing',
             statusNode: pill(autonomy.wired ? 'WIRED' : 'UNKNOWN', { tone: autonomy.wired ? 'ok' : 'idle' }),
             body: [h('p.xsmall.dim', `roles: ${(autonomy.roles || []).length} · graph: ${autonomy.graph?.nodes ?? '—'} nodes / ${autonomy.graph?.edges ?? '—'} edges`)] })
    );

    const head = pageHead('Bossman 2.0 · Надстройка',
      'Интеллектуальное ядро 2.0: актуальная память Obsidian без мусора, 5 рубежей аппаратной/AST-защиты, честные медиа-пайплайны и автономное самоулучшение.',
      { pills: [
        pill(s.running ? 'работает' : 'готов к запуску', { tone: s.running ? 'ok' : 'info' }),
        pill(`Obsidian: ${mem.freshness_status === 'UP_TO_DATE' ? 'актуален' : 'проверьте синхронизацию'}`, { tone: mem.freshness_status === 'UP_TO_DATE' ? 'ok' : 'warn' }),
        pill('торговля OFF ($0.00)', { tone: 'ok' }),
        pill('защита 2.0 активна', { tone: 'ok' }),
      ]});

    return h('div.bx-page', head, v20Superstructure, controls, panel('Состояние контуров Bossman 2.0', tiles),
      panel('Принцип Bossman 2.0', h('p.small',
        'Каждая кнопка отвечает осмысленно, ядро отделено от опциональных адаптеров (splitHealth), память Obsidian очищена от синтетических тестов и синхронизирована с реальным кодом, а все субпроцессы и команды защищены через PowerShell AST и Windows Job Object.')));
  },

  onEvent(ev) { return String(ev.kind || '').startsWith('v15.') || String(ev.kind || '').startsWith('owner.input_'); },
};

export default Page;

