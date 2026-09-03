/* ============================================================
   trading_lab.js — Trading Learning Lab.

   Экран показывает ровно то, что подтверждено кодом: состояние приёма
   источника, какие шаги пайплайна реально работают, а какие заблокированы
   отсутствующей технологией, что именно сказал K1mba и что из этого
   подтверждено данными, результат бенчмарка и состояние памяти.

   Слов «готово» и «прибыльно» здесь нет как класса. Бейдж считается из
   ответа сервера: PAPER — когда весь пайплайн работает, BLOCKED — когда
   хотя бы один шаг недоступен. LIVE_PROVEN невозможен: торгового исполнения
   в системе нет.

   Endpoints: GET /api/trading-lab/{status,seed,benchmark,memory}
   ============================================================ */

import { api } from '../api.js';
import { h, icon, badge, fmtNum } from '../components.js';
import { panel, pageHead, errorNote, blank, pill, tile, tag, plural } from './_ui.js';

/* Класс доказательности → тон пилюли. Ниже HISTORICAL_REPLAY — не повод для
   зелёного цвета: моки и заблокированное красить в «ок» нельзя. */
const EVIDENCE_TONE = {
  LIVE_PROVEN: 'ok',
  REAL_SANDBOX: 'ok',
  HISTORICAL_REPLAY: 'info',
  SIMULATED: 'warn',
  MOCK: 'idle',
  BLOCKED: 'err',
  DEAD_OR_UNWIRED: 'err',
};

const EVIDENCE_WORD = {
  LIVE_PROVEN: 'доказано на реальных сделках',
  REAL_SANDBOX: 'реально работает',
  HISTORICAL_REPLAY: 'проверено на истории',
  SIMULATED: 'симуляция',
  MOCK: 'заглушка',
  BLOCKED: 'заблокировано',
  DEAD_OR_UNWIRED: 'не подключено',
};

function evidencePill(cls) {
  const key = String(cls || 'MOCK').toUpperCase();
  // Технический класс уезжает в подсказку: в самой пилюле он удваивает ширину,
  // а она не переносится и вытягивает страницу за границу телефонного экрана.
  return pill(EVIDENCE_WORD[key] || key, { tone: EVIDENCE_TONE[key] || 'idle', title: key });
}

const TradingLabPage = {
  id: 'trading_lab',
  title: 'Обучение трейдингу',
  icon: 'activity',
  nav: 'more',

  async render(ctx) {
    const [statusR, seedR, benchR, memR] = await Promise.allSettled([
      api.raw('/api/trading-lab/status'),
      api.raw('/api/trading-lab/seed'),
      api.raw('/api/trading-lab/benchmark'),
      api.raw('/api/trading-lab/memory'),
    ]);

    if (statusR.status === 'rejected') {
      return h('div.bx-page',
        pageHead('Обучение трейдингу', 'Учимся на разборах трейдера и сами проверяем их данными.'),
        errorNote(statusR.reason, () => ctx.refresh()));
    }
    const status = statusR.value || {};
    if (status.available === false) {
      return h('div.bx-page',
        pageHead('Обучение трейдингу', 'Модуль обучения не подключён к этой сборке.'),
        panel('Состояние', h('div.small', status.reason || 'ядро недоступно'),
          { aside: evidencePill(status.evidence_class) }));
    }

    const safety = status.safety || {};
    const blocked = status.blocked_steps || [];

    const head = pageHead('Обучение трейдингу',
      'Разбор трейдера — это гипотеза, а не сигнал. Каждое утверждение проверяется данными, '
      + 'и без доказательств система говорит «не знаю», а не «покупай».',
      {
        pills: [
          pill('бумажный режим', {
            tone: blocked.length ? 'warn' : 'info',
            value: status.badge || 'PAPER',
            title: blocked.length ? 'часть шагов пайплайна недоступна' : 'весь путь доступен',
          }),
          pill('торговля', { tone: 'ok', value: safety.trading_execution || 'OFF',
            title: 'реальное исполнение сделок выключено' }),
        ],
      });

    const safetyPanel = panel('Что системе запрещено',
      h('div',
        h('div.bx-tags',
          tag('сделки', { bold: 'нет' }),
          tag('переводы', { bold: 'нет' }),
          tag('вывод средств', { bold: 'нет' }),
          tag('ключи на запись', { bold: 'нет' }),
          tag('подтверждение', { bold: safety.owner_approval_required ? 'нужно' : '—' }),
          tag('внешние действия', { bold: safety.external_write_actions || 'DENY' })),
        safety.env_requested_live_ignored
          ? h('p.small', 'В настройках пытались включить реальную торговлю — запрос отклонён.')
          : null),
      { icon: 'approvals' });

    const stepsPanel = panel('Путь от видео до вывода',
      h('div.bx-cards', (status.steps || []).map(stepTile)),
      { icon: 'tasks', aside: blocked.length
        ? pill('недоступно', { tone: 'err', value: blocked.length,
          title: `${plural(blocked.length, 'шаг', 'шага', 'шагов')} нельзя выполнить` })
        : pill('всё доступно', { tone: 'ok' }) });

    const seedPanel = seedR.status === 'fulfilled'
      ? seedSection(seedR.value)
      : panel('Разбор K1mba', errorNote(seedR.reason, () => ctx.refresh()));

    const benchPanel = benchR.status === 'fulfilled'
      ? benchSection(benchR.value)
      : panel('Проверка модуля', errorNote(benchR.reason, () => ctx.refresh()));

    const memoryPanel = memR.status === 'fulfilled'
      ? memorySection(memR.value)
      : panel('Память', errorNote(memR.reason, () => ctx.refresh()));

    return h('div.bx-page', head, safetyPanel, stepsPanel, seedPanel, benchPanel, memoryPanel);
  },

  onEvent(ev) { return String(ev.kind || '').startsWith('trading_lab.'); },
};

/* ---------------------------------------------------------------- шаги */

function stepTile(step) {
  const ok = step.status === 'OK';
  return tile({
    accent: ok ? 'var(--bx-azure)' : 'var(--bx-amber, #d98324)',
    iconName: ok ? 'check' : 'close',
    title: STEP_TITLE[step.step] || step.step,
    sub: step.detail || '',
    statusNode: evidencePill(step.evidence_class),
    muted: !ok,
    // Список недостающих зависимостей — переносимой строкой, а не чипами:
    // чип дизайн-системы не переносится, и «whisper|faster_whisper|vosk» задаёт
    // минимальную ширину всей колонки карточек, вылезая за телефонный экран.
    body: (step.missing && step.missing.length)
      ? [h('p.xsmall.dim', { style: { overflowWrap: 'anywhere', margin: '6px 0 0' } },
        `Не хватает: ${step.missing.join(', ')}`)]
      : [],
  });
}

const STEP_TITLE = {
  ingest_video: 'Приём материала',
  extract_audio: 'Звуковая дорожка',
  transcribe: 'Расшифровка речи',
  extract_frames: 'Кадры графика',
  chart_ocr: 'Чтение цифр с графика',
  extract_claims: 'Разбор на утверждения',
  normalize_strategy: 'Правило решения',
  verify_claims: 'Проверка данными',
  compile_backtest: 'Сборка проверки на истории',
  run_backtest: 'Прогон по истории',
  paper_trade: 'Бумажная торговля',
  lesson_builder: 'Урок в память',
  trading_benchmark: 'Итоговая проверка',
};

/* ------------------------------------------------------- разбор K1mba */

function seedSection(seed) {
  if (!seed || seed.available === false) {
    return panel('Разбор K1mba', h('div.small.dim', (seed && seed.reason) || 'нет данных'));
  }
  const frame = seed.final_frame || {};
  const numbers = h('div.bx-tags',
    frame.price_approx ? tag(`${fmtNum(frame.price_approx)} $`, { bold: 'цена' }) : null,
    frame.cvd_approx ? tag(`${fmtNum(frame.cvd_approx / 1e9, 2)} млрд`, { bold: 'CVD' }) : null,
    frame.open_interest_approx
      ? tag(`${fmtNum(frame.open_interest_approx / 1e9, 2)} млрд`, { bold: 'открытый интерес' }) : null,
    frame.short_liquidations_approx
      ? tag(`${fmtNum(frame.short_liquidations_approx / 1e6, 2)} млн`, { bold: 'ликвидации шортов' }) : null);

  const observations = h('div.bx-list', (seed.observations || []).map((o) =>
    h('div.bx-list-row',
      h('span.bx-tag', CLAIM_WORD[o.type] || o.type),
      h('span.bx-list-name', o.text))));

  return panel('Разбор K1mba: что сказано и чем это является',
    h('div',
      h('p.small', 'Это структурный пример разметки, снятый с кадров. '
        + 'Прибыльность им не доказана. '
        + 'Нет биржи, размера, плеча, фактических исполнений, комиссий и funding — '
        + 'поэтому ни одно утверждение отсюда не может стать правилом.'),
      numbers, observations,
      h('p.xsmall.dim', seed.disclaimer || '')),
    { icon: 'info', aside: h('div.bx-pagehead-aside',
      evidencePill(seed.evidence_class), badge('мнение, не факт', 'warn')) });
}

const CLAIM_WORD = {
  AUTHOR_CLAIM: 'мнение автора',
  MARKET_OBSERVATION: 'наблюдение с графика',
  HYPOTHESIS: 'гипотеза',
  ENTRY_CONDITION: 'условие входа',
  EXIT_CONDITION: 'условие выхода',
  INVALIDATION: 'отмена сценария',
  RISK_RULE: 'правило риска',
  POSITION_MANAGEMENT: 'работа с позицией',
  EXPECTED_OUTCOME: 'ожидаемый исход',
  RETROSPECTIVE_COMMENTARY: 'разбор задним числом',
};

/* ------------------------------------------------------------ бенчмарк */

function benchSection(report) {
  if (!report || report.available === false) {
    return panel('Проверка модуля', h('div.small.dim', (report && report.reason) || 'нет данных'));
  }
  const ready = report.verdict === 'READY';
  const rows = report.rows || [];
  const byMode = {};
  rows.forEach((r) => { (byMode[r.mode] = byMode[r.mode] || []).push(r); });

  const modeCards = Object.keys(byMode).sort().map((mode) => {
    const list = byMode[mode];
    const passed = list.filter((r) => r.passed).length;
    return tile({
      accent: passed === list.length ? 'var(--bx-azure)' : 'var(--bx-rose, #c04848)',
      title: MODE_TITLE[mode] || mode,
      sub: MODE_HINT[mode] || '',
      statusNode: pill(`${passed} из ${list.length}`,
        { tone: passed === list.length ? 'ok' : 'err' }),
      body: [h('div.bx-list', list.map((r) => h('div.bx-list-row',
        h('span.bx-tag', r.passed ? 'прошло' : 'не прошло'),
        h('span.bx-list-name', CASE_WORD[r.case_id] || r.case_id))))],
    });
  });

  const blockers = report.blockers || [];
  return panel('Проверка модуля',
    h('div',
      h('p.small', ready
        ? 'Все проверки пройдены и подкреплены прогоном по истории.'
        : 'Готовность не подтверждена. Ниже перечислено, чего именно не хватает — '
          + 'до устранения этих пунктов модуль не считается готовым.'),
      blockers.length
        ? h('div.bx-list', blockers.map((b) => h('div.bx-list-row',
          h('span.bx-tag', 'блокер'), h('span.bx-list-name', b))))
        : null,
      h('div.bx-cards', modeCards)),
    { icon: 'approvals',
      aside: pill(ready ? 'пройдено' : 'не подтверждено',
        { tone: ready ? 'ok' : 'err', value: report.verdict,
          title: ready ? 'все проверки пройдены'
            : 'готовность модуля доказательствами не подтверждена' }) });
}

/* Человеческие названия проверок. Технический case_id — длинный неразрывный
   токен: на узком экране он расширяет страницу и ломает вёрстку, поэтому в
   интерфейс идёт понятная формулировка, а сам id остаётся в API. */
const CASE_WORD = {
  'dev.no_future_in_context': 'решение не видит будущих свечей',
  'dev.small_sample_refuses': 'на малой выборке система отказывается судить',
  'dev.plan_hash_stable': 'условия проверки зафиксированы и воспроизводимы',
  'sealed.cannot_enumerate': 'закрытую выборку нельзя перечислить',
  'sealed.membership_check': 'принадлежность к закрытой выборке проверяется',
  'adv.prompt_injection_quarantined': 'вредные подсказки уходят в карантин',
  'adv.weak_cvd_detected': 'слабый CVD при росте цены распознан',
  'adv.failed_breakout_detected': 'ложный пробой распознан',
  'adv.teacher_contradicted_by_data': 'слова трейдера против данных — данные важнее',
  'adv.fake_ocr_price_rejected': 'несуществующая цена с кадра отклонена',
  'adv.missing_data_unverifiable': 'без данных ответ «проверить нельзя»',
  'paper.execution_delayed': 'исполнение только на следующей свече',
  'paper.costs_charged': 'комиссия и проскальзывание учтены',
  'paper.duplicate_order_rejected': 'повторная заявка отклонена',
  'paper.impossible_fill_rejected': 'невозможное исполнение отклонено',
};

const MODE_TITLE = {
  DEVELOPMENT: 'Открытые случаи',
  SEALED_HOLDOUT: 'Закрытая выборка',
  ADVERSARIAL: 'Враждебные случаи',
  PAPER_REPLAY: 'Бумажная симуляция',
};
const MODE_HINT = {
  DEVELOPMENT: 'исторические примеры, на которые можно смотреть',
  SEALED_HOLDOUT: 'запечатанные примеры: перечислить их нельзя',
  ADVERSARIAL: 'ложные пробои, подменённые данные, вредные подсказки в субтитрах',
  PAPER_REPLAY: 'комиссии, фондирование, проскальзывание и задержка исполнения',
};

/* --------------------------------------------------------------- память */

function memorySection(memory) {
  if (!memory || memory.available === false) {
    return panel('Память', h('div.small.dim', (memory && memory.reason) || 'нет данных'));
  }
  const layers = [
    ['Текущая задача', (memory.working_state_keys || []).length,
      'только актуальный контекст, ничего лишнего'],
    ['Эпизоды', memory.episodic || 0, 'случаи с метками времени и доказательствами'],
    ['Правила в работе', memory.procedural || 0,
      'сюда попадает только то, что прошло проверку и не ухудшило результат'],
    ['Карантин', memory.quarantine || 0,
      'неподтверждённое, противоречивое и потенциально отравленное'],
  ];
  const cards = layers.map(([title, value, hint]) => tile({
    title, sub: hint,
    statusNode: pill(String(value), { tone: value ? 'info' : 'idle' }),
  }));
  const quarantined = memory.quarantined_rules || [];
  return panel('Память',
    h('div',
      h('div.bx-cards', cards),
      quarantined.length
        ? h('div.bx-list', quarantined.map((q) => h('div.bx-list-row',
          h('span.bx-list-name', q.lesson_id),
          h('span.bx-list-note', q.notes || ''))))
        : null,
      memory.note ? h('p.xsmall.dim', memory.note) : null),
    { icon: 'system' });
}

export default TradingLabPage;
