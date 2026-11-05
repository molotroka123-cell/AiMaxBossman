/* ============================================================
   creator_mode.js — Creator Mode (DeepSeek Harness-style «Into the Unknown»).
   Опишите фичу простыми словами (`/plugin`, `/console`, `/game`, `/3d` + `@memory`, `@guards`, `@swapme`, `@weights`)
   — Bossman соберёт плагин, дизайн-консоль, мини-игру или 3D LLM визуализатор весов,
   протестирует на локальных моделях (Multi-Agent Arena) и установит навык в 1 клик.
   ============================================================ */

import { api } from '../api.js';
import { h, toastOk, toastError } from '../components.js';
import { panel, pageHead, pill, btn, blank } from './_ui.js';

const base = '/api/creator';
export const PREVIEW_SANDBOX = 'allow-scripts';

const G = (path) => api.raw(path);
const P = (path, body) => api.raw(path, { method: 'POST', body });
const errText = (e) => (e && e.message) || String(e);

const TARGET_LABELS = {
  plugin: 'Плагин / Skill',
  design_console: 'Дизайн-консоль',
  game: 'Мини-игра',
  visualizer_3d: '3D LLM Визуализатор',
};

const QUICK_PROMPTS = [
  {
    label: '🔌 Плагин аудита G1–G7',
    cmd: '/plugin @guards @memory Собери плагин проверки дельт G1-G7 и верификации SHA перед пушем',
    target: 'plugin',
  },
  {
    label: '🎨 Дизайн-консоль владельца',
    cmd: '/console @memory @guards Собери дизайн-консоль управления агентами, темами и лимитами',
    target: 'design_console',
  },
  {
    label: '🎮 3D/2D Игра SwapMe Gate Runner',
    cmd: '/game @swapme @guards Собери аркаду SwapMe Gate Runner с проверкой монет G1-G7',
    target: 'game',
  },
  {
    label: '🧠 3D LLM Визуализатор весов',
    cmd: '/3d @weights @memory Собери интерактивный 3D визуализатор слоёв LLM и дельт весов LoRA',
    target: 'visualizer_3d',
  },
];

const CreatorModePage = {
  id: 'creator-mode',
  title: 'Creator Mode',
  icon: 'builder',
  nav: 'primary',
  section: 'studio',

  async render() {
    const state = {
      status: null,
      projects: [],
      selected: null,
      version: null,
      arenaResult: null,
    };

    const head = pageHead(
      'Creator Mode — Into the Unknown',
      'Опишите фичу обычным языком (/plugin, /console, /game, /3d и контексты @memory, @guards, @swapme, @weights) — '
        + 'Bossman соберёт плагин, дизайн-консоль, игру или 3D LLM визуализатор, проверит 6 гейтов и протестирует на разных локальных агентах.'
    );

    const promptInput = h('textarea', {
      id: 'creator-prompt',
      name: 'creator-prompt',
      rows: 4,
      maxlength: '8000',
      placeholder: 'Опишите что построить: /plugin, /console, /game, /3d + @memory @guards @swapme @weights ...',
      style: { width: '100%' },
    });

    const targetSelect = h(
      'select',
      { id: 'creator-target', name: 'creator-target' },
      h('option', { value: '' }, 'Автоопределение (/команда или текст)'),
      h('option', { value: 'plugin' }, 'Плагин / Skill (/plugin)'),
      h('option', { value: 'design_console' }, 'Дизайн-консоль (/console)'),
      h('option', { value: 'game' }, 'Мини-игра (/game)'),
      h('option', { value: 'visualizer_3d' }, '3D LLM Визуализатор (/3d)')
    );

    const modelSelect = h(
      'select',
      { id: 'creator-model', name: 'creator-model' },
      h('option', { value: 'auto' }, 'Авто (локальный конвейер Bossman)'),
      h('option', { value: 'qwen35-3d-coder:latest' }, 'qwen35-3d-coder:latest (3D/UI)'),
      h('option', { value: 'ace-qwen3-17b:latest' }, 'ace-qwen3-17b:latest (1.7B быстрый)'),
      h('option', { value: 'ace-decider-08b:latest' }, 'ace-decider-08b:latest (0.8B ультра-лёгкий)'),
      h('option', { value: 'qwen3-coder:30b' }, 'qwen3-coder:30b (30B архитектор)')
    );

    const useLocalLlm = h('input', {
      id: 'creator-use-llm',
      name: 'creator-use-llm',
      type: 'checkbox',
    });

    const formErr = h('div', { 'aria-live': 'polite' });

    const chipRow = h(
      'div',
      { style: { display: 'flex', gap: '6px', flexWrap: 'wrap' } },
      ...QUICK_PROMPTS.map((q) =>
        btn(
          q.label,
          () => {
            promptInput.value = q.cmd;
            targetSelect.value = q.target;
          },
          { variant: 'subtle', size: 'sm' }
        )
      ),
      ...['@memory', '@guards', '@swapme', '@weights'].map((tag) =>
        btn(
          tag,
          () => {
            const cur = promptInput.value.trim();
            promptInput.value = cur ? `${cur} ${tag}` : `${tag} `;
          },
          { variant: 'subtle', size: 'sm' }
        )
      )
    );

    const buildBtn = btn(
      'Собрать в Creator Mode',
      async () => {
        formErr.replaceChildren();
        const text = promptInput.value.trim();
        if (!text) {
          formErr.append(h('p', { role: 'alert' }, 'Введите описание фичи или выберите быстрый шаблон.'));
          return;
        }
        try {
          const res = await P(`${base}/projects`, {
            prompt: text,
            target_type: targetSelect.value || null,
            model: modelSelect.value || 'auto',
            slug: state.selected ? state.selected.slug : null,
            use_local_llm: Boolean(useLocalLlm.checked),
          });
          const vNum = res.version.n || res.version.version_num || 1;
          toastOk(`Собрано: ${res.project.title} (v${vNum}, ${res.version.score.total_score}/100)`);
          await loadProject(res.project.slug, vNum);
          await refresh();
        } catch (e) {
          formErr.append(h('p', { role: 'alert' }, errText(e)));
          toastError(e);
        }
      },
      { variant: 'primary', iconName: 'play' }
    );

    const arenaBtn = btn(
      'Прогнать на локальных агентах (Arena)',
      async () => {
        formErr.replaceChildren();
        const text =
          promptInput.value.trim()
          || '/3d @weights @memory Собери 3D LLM визуализатор весов и дельт обучения Bossman';
        try {
          toastOk('Запуск Multi-Agent Arena на локальных моделях...');
          const res = await P(`${base}/arena`, {
            prompt: text,
            target_type: targetSelect.value || null,
          });
          state.arenaResult = res;
          toastOk(`Победитель арены: ${res.winner ? res.winner.model : 'н/д'}`);
          await refresh();
        } catch (e) {
          formErr.append(h('p', { role: 'alert' }, errText(e)));
          toastError(e);
        }
      },
      { variant: 'subtle', iconName: 'bolt' }
    );

    const newSessionBtn = btn(
      '+ Новая сессия',
      () => {
        state.selected = null;
        state.version = null;
        promptInput.value = '';
        renderProjectDetail();
      },
      { variant: 'subtle', size: 'sm' }
    );

    const composerPanel = panel(
      'Into the Unknown — Поле созидания',
      h(
        'div',
        { style: { display: 'grid', gap: '10px' } },
        chipRow,
        h('label', { for: 'creator-prompt' }, 'Опишите на обычном языке, что нужно построить'),
        promptInput,
        h(
          'div',
          { style: { display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'center' } },
          h('label', { for: 'creator-target' }, 'Тип:'),
          targetSelect,
          h('label', { for: 'creator-model' }, 'Модель:'),
          modelSelect,
          h(
            'label',
            { for: 'creator-use-llm', style: { display: 'flex', gap: '6px', alignItems: 'center' } },
            useLocalLlm,
            'Живой синтез через локальную LLM'
          )
        ),
        h('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap' } }, buildBtn, arenaBtn, newSessionBtn),
        formErr
      ),
      { icon: 'builder' }
    );

    const statusHost = h('div', { dataset: { testid: 'creator-status' } });
    const projectsHost = h('div', { dataset: { testid: 'creator-projects' }, style: { display: 'grid', gap: '6px' } });
    const detailHost = h('div', { dataset: { testid: 'creator-detail' }, style: { display: 'grid', gap: '10px' } });
    const arenaHost = h('div', { dataset: { testid: 'creator-arena' }, style: { display: 'grid', gap: '8px' } });
    const k1m6aHost = h('div', { dataset: { testid: 'creator-k1m6a' }, style: { display: 'grid', gap: '8px' } });

    const renderStatus = () => {
      statusHost.replaceChildren();
      const st = state.status;
      if (!st) return;
      const t = st.weights_telemetry || {};
      statusHost.append(
        h(
          'div',
          { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' } },
          pill(`Проектов: ${st.projects_count || 0}`, 'ok'),
          pill(`Установлено навыков: ${(st.installed_skills || []).length}`, 'ok'),
          pill(`Арена прогонов: ${(st.recent_arena_runs || []).length}`, 'idle'),
          pill(`Модель телеметрии весов: ${t.model_name || 'Qwen3'}`, 'ok'),
          pill(`Изменено весов: ${t.modified_weights_pct || 0.016}%`, 'warn')
        )
      );
    };

    const renderProjects = () => {
      projectsHost.replaceChildren();
      if (!state.projects.length) {
        projectsHost.append(blank('Пока нет собранных проектов Creator Mode. Нажмите на шаблон выше и «Собрать».'));
        return;
      }
      for (const p of state.projects) {
        const active = state.selected && state.selected.slug === p.slug;
        const kindKey = p.kind || p.target_type;
        const curV = p.current || p.current_version || 1;
        const lastVer = Array.isArray(p.versions) && p.versions.length ? p.versions[p.versions.length - 1] : null;
        const latestScore = (lastVer && lastVer.score && lastVer.score.total_score) ?? p.latest_score ?? 100;
        const row = h(
          'div',
          {
            style: {
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              gap: '8px',
              padding: '8px 10px',
              borderRadius: '8px',
              border: active ? '1px solid var(--accent, #22d3ee)' : '1px solid rgba(148,163,184,0.2)',
            },
          },
          h(
            'div',
            null,
            h('strong', null, p.title || p.slug),
            h(
              'div',
              { style: { fontSize: '12px', opacity: 0.78 } },
              `${TARGET_LABELS[kindKey] || kindKey} · v${curV} · ${latestScore}/100`
            )
          ),
          btn('Открыть', () => loadProject(p.slug), { size: 'sm', variant: active ? 'primary' : 'subtle' })
        );
        projectsHost.append(row);
      }
    };

    const renderProjectDetail = () => {
      detailHost.replaceChildren();
      const proj = state.selected;
      if (!proj) {
        detailHost.append(blank('Выберите проект слева или создайте новый через поле созидания.'));
        return;
      }
      const versions = proj.versions || [];
      const ver = state.version || versions[versions.length - 1];
      if (!ver) return;

      const vNum = ver.n || ver.version_num || 1;
      const kindKey = proj.kind || proj.target_type;
      const score = ver.score || {};
      const previewUrl = `${base}/projects/${encodeURIComponent(proj.slug)}/versions/${vNum}/preview`;

      const installBtn = btn(
        'Установить как Skill в Bossman',
        async () => {
          try {
            const res = await P(`${base}/projects/${encodeURIComponent(proj.slug)}/install-skill`, {});
            toastOk(`Навык установлен: ${(res.skill && res.skill.id) || res.skill_md || proj.slug}`);
            await refresh();
          } catch (e) {
            toastError(e);
          }
        },
        { variant: 'primary', size: 'sm' }
      );

      const pultBtn = btn(
        'Отправить в Telegram Пульт',
        async () => {
          try {
            const res = await P(`${base}/projects/${encodeURIComponent(proj.slug)}/send-to-pult`, {
              dry_run: false,
            });
            if (res.ok || res.sent) toastOk('Отчёт отправлен в Пульт владельца');
            else toastError(new Error(res.message || res.reason || 'Пульт не настроен'));
          } catch (e) {
            toastError(e);
          }
        },
        { variant: 'subtle', size: 'sm' }
      );

      const verButtons = versions.map((v) => {
        const vn = v.n || v.version_num || 1;
        return btn(
          `v${vn} (${v.score ? v.score.total_score : 100})`,
          () => {
            state.version = v;
            renderProjectDetail();
          },
          { size: 'sm', variant: vn === vNum ? 'primary' : 'subtle' }
        );
      });

      const iframe = h('iframe', {
        title: `Превью ${proj.title} v${vNum}`,
        src: previewUrl,
        sandbox: PREVIEW_SANDBOX,
        referrerpolicy: 'no-referrer',
        style: {
          width: '100%',
          height: '440px',
          border: '1px solid rgba(148,163,184,0.28)',
          borderRadius: '10px',
          background: '#090d16',
        },
      });

      const curFiles = proj.current_files || {};
      const codeDetails = h(
        'details',
        null,
        h('summary', { style: { cursor: 'pointer', fontSize: '13px' } }, 'Исходники: SKILL.md, plugin.manifest.json, app.js'),
        h(
          'pre',
          { style: { fontSize: '12px', overflowX: 'auto', maxHeight: '220px' } },
          `--- SKILL.md ---\n${curFiles['SKILL.md'] || ver.skill_md || ''}\n\n--- plugin.manifest.json ---\n${curFiles['plugin.manifest.json'] || ''}`
        )
      );

      const passedVerdict = score.passed_all_gates !== false ? 'PASS' : 'WARN';
      detailHost.append(
        h(
          'div',
          { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' } },
          h('strong', null, `${proj.title} (${proj.slug})`),
          pill(TARGET_LABELS[kindKey] || kindKey, 'ok'),
          pill(`Оценка: ${score.total_score ?? 100}/100 (${passedVerdict})`, passedVerdict === 'PASS' ? 'ok' : 'warn'),
          pill(`Модель: ${ver.model || 'auto'}`, 'idle'),
          installBtn,
          pultBtn
        ),
        h('div', { style: { display: 'flex', gap: '6px', flexWrap: 'wrap' } }, ...verButtons),
        iframe,
        codeDetails
      );
    };

    const renderArena = () => {
      arenaHost.replaceChildren();
      const runs = (state.status && state.status.recent_arena_runs) || [];
      const latest = state.arenaResult || runs[0];
      if (!latest) {
        arenaHost.append(blank('Запустите «Прогнать на локальных агентах (Arena)», чтобы сравнить локальные модели.'));
        return;
      }
      const list = latest.results || latest.contenders || [];
      const rows = list.map((c) => {
        const sc = c.score || {};
        const tot = sc.total_score ?? c.total_score ?? 100;
        const elMs = sc.elapsed_s ? Math.round(sc.elapsed_s * 1000) : (c.latency_ms || 0);
        const usedLlm = c.used_llm_directly ?? c.llm_ok;
        return h(
          'div',
          {
            style: {
              display: 'flex',
              justifyContent: 'space-between',
              gap: '8px',
              padding: '6px 10px',
              borderRadius: '6px',
              border: '1px solid rgba(148,163,184,0.2)',
            },
          },
          h('span', null, `${c.model} (${c.kind || c.target_type})`),
          h(
            'span',
            null,
            `${tot}/100 · ${elMs} мс · LLM=${usedLlm ? 'DIRECT' : 'BLUEPRINT'}`
          )
        );
      });
      const winModel = typeof latest.winner === 'string' ? latest.winner : (latest.winner && latest.winner.model) || 'н/д';
      arenaHost.append(
        h(
          'div',
          { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' } },
          pill(`Победитель: ${winModel}`, 'ok')
        ),
        ...rows
      );
    };

    const renderK1m6a = () => {
      k1m6aHost.replaceChildren();
      const k = state.status && state.status.k1m6a_live;
      if (!k || k.state === 'NOT_STARTED') {
        k1m6aHost.append(blank('Наблюдение за стримом K1m6a ещё не запущено или ожидает первого кадра.'));
        return;
      }
      const obs = k.latest_observation || {};
      const rev = k.latest_teacher_review || {};
      k1m6aHost.append(
        h(
          'div',
          { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' } },
          pill(`Статус: ${k.state}`, k.active ? 'ok' : 'idle'),
          pill(`Записей ученика: ${k.observations_count || 0}`, 'ok'),
          pill(`Модель ученика: ${k.student_model || 'ace-qwen3-17b'}`, 'idle'),
          rev.teacher_grade ? pill(`Оценка Учителя: ${rev.teacher_grade}`, 'ok') : h('span')
        ),
        obs.student_diary_ru
          ? h(
              'div',
              { style: { fontSize: '13px', display: 'grid', gap: '4px' } },
              h('strong', null, `Последняя запись ученика (Шаг #${obs.step_idx || 1}, ${obs.ticker || 'CHART'}):`),
              h('p', { style: { margin: 0 } }, obs.student_diary_ru),
              h('p', { style: { margin: 0, opacity: 0.85 } }, `Вопрос Учителю: ${obs.question_for_teacher_ru || ''}`)
            )
          : h('span'),
        rev.when_setup_applies
          ? h(
              'div',
              { style: { fontSize: '12px', display: 'grid', gap: '3px', paddingTop: '6px' } },
              h('strong', null, 'Текущий вердикт Учителя (WHEN / CONFIRM / INVALIDATE):'),
              h('div', null, `WHEN: ${rev.when_setup_applies}`),
              h('div', null, `CONFIRM: ${rev.entry_confirmation}`),
              h('div', null, `INVALIDATE: ${rev.invalidation_rule}`)
            )
          : h('span')
      );
    };

    const loadProject = async (slug, versionNum = null) => {
      const proj = await G(`${base}/projects/${encodeURIComponent(slug)}`);
      state.selected = proj;
      const versions = proj.versions || [];
      state.version =
        (versionNum && versions.find((v) => v.version_num === versionNum))
        || versions[versions.length - 1]
        || null;
      renderProjects();
      renderProjectDetail();
    };

    const refresh = async () => {
      const [st, pr] = await Promise.all([G(`${base}/status`), G(`${base}/projects`)]);
      state.status = st;
      state.projects = (pr && pr.projects) || [];
      if (!state.selected && state.projects.length) {
        await loadProject(state.projects[0].slug);
      } else {
        renderProjects();
        renderProjectDetail();
      }
      renderStatus();
      renderArena();
      renderK1m6a();
    };

    await refresh();

    const grid = h(
      'div',
      { style: { display: 'grid', gridTemplateColumns: 'minmax(260px, 1fr) minmax(420px, 2fr)', gap: '12px' } },
      h(
        'div',
        { style: { display: 'grid', gap: '12px', alignContent: 'start' } },
        panel('Сессии и сборки', projectsHost, { icon: 'layers' }),
        panel('Multi-Agent Arena (локальные модели)', arenaHost, { icon: 'bolt' }),
        panel('Ученик + Учитель: Live-стрим K1m6a', k1m6aHost, { icon: 'activity' })
      ),
      panel('Песочница Creator Mode (CSP Sandbox)', detailHost, { icon: 'eye' })
    );

    return h('div', { class: 'page-stack' }, head, statusHost, composerPanel, grid);
  },

  onEvent() {},
};

export default CreatorModePage;
