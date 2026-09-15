/* ============================================================
   file_intelligence.js — §20: минимальная поверхность File Intelligence.

   Никакого редизайна: язык интерфейса тот же, что у остальных страниц.
   Экран показывает ровно то, что система ЗНАЕТ, и ничего сверх того:

   * состояние приватности (LOCAL / REMOTE / UNKNOWN) — заметно, потому что от
     него зависит, уйдут ли имена и содержимое файлов наружу;
   * состояние бинаря, включая VERSION_UNVERIFIED — «манифест закреплён» и
     «сборка доказана» это разные утверждения;
   * честные состояния работы: Review required / Stale / Denied / Applying /
     Verified / Failed.

   Анимации прогресса здесь нет намеренно. Полоска, которая едет, пока система
   ничего не знает о ходе работы, — это утверждение о прогрессе без улики.
   Пока сайдкар не прислал состояние, экран говорит, что ждёт, и не более.
   ============================================================ */

import { api } from '../api.js';
import {
  h, badge, toastOk, toastError, actionButton, field, input, select, checkbox,
} from '../components.js';
import { panel, pageHead, errorNote } from './_ui.js';

const OPERATIONS = [
  { value: 'file.rename_smart', label: 'Переименовать' },
  { value: 'file.categorize', label: 'Разложить по категориям' },
  { value: 'file.categorize_and_rename', label: 'Разложить и переименовать' },
];

/* Тон состояния. Ничего «почти готового»: либо проверено, либо нет. */
const STATE_TONE = {
  VERIFIED: 'ok',
  REVIEW_REQUIRED: 'warn',
  WAITING: 'warn',
  APPLYING: 'warn',
  AMBIGUOUS: 'warn',
  STALE: 'bad',
  DENIED: 'bad',
  VERIFICATION_FAILED: 'bad',
  FAILED: 'bad',
  CANCELLED: 'muted',
};

const STATE_LABEL = {
  QUEUED: 'В очереди',
  WAITING: 'Ждёт освобождения сортировщика',
  ANALYZING: 'Анализ',
  REVIEW_REQUIRED: 'Нужен просмотр',
  APPROVED: 'Утверждено',
  APPLYING: 'Применяется',
  VERIFIED: 'Проверено',
  VERIFICATION_FAILED: 'Проверка не подтвердила результат',
  AMBIGUOUS: 'Исход неизвестен — нужен разбор',
  DENIED: 'Отказано',
  STALE: 'План устарел',
  CANCELLED: 'Остановлено владельцем',
  FAILED: 'Не удалось',
};

const PRIVACY_LABEL = {
  LOCAL: 'ЛОКАЛЬНО',
  REMOTE: 'УДАЛЁННО',
  UNKNOWN: 'НЕИЗВЕСТНО',
};

function privacyBanner(status) {
  const mode = status.backend_mode || 'UNKNOWN';
  const tone = mode === 'LOCAL' ? 'ok' : (mode === 'REMOTE' ? 'bad' : 'warn');
  const explain = {
    LOCAL: 'Модель работает на этой машине. Файлы наружу не уходят.',
    REMOTE: 'Сортировщик настроен на удалённую модель. Обработка требует явного '
          + 'разрешения владельца.',
    UNKNOWN: 'Не удалось доказать, где обрабатываются данные. Пока это так, '
           + 'File Intelligence отказывает — «локально» не выводится из того, '
           + 'что запустился локальный файл.',
  }[mode];
  return panel('Приватность', h('div.fi-privacy',
    h('div.fi-privacy-state', badge(PRIVACY_LABEL[mode] || mode, tone)),
    h('p.muted', explain),
    h('p.muted', `Отмена (undo) через headless-контракт: ${status.undo_headless}.`)));
}

function binaryPanel(status) {
  const binary = status.binary || {};
  const tone = binary.status === 'AVAILABLE' ? 'ok' : 'bad';
  const rows = [
    ['Состояние', binary.status || '—'],
    ['Исполняемый файл', binary.resolved_executable || 'не найден'],
    ['Версия сборки', binary.binary_version || '—'],
    ['Ожидаемый upstream SHA', binary.pinned_upstream_sha_expected || '—'],
  ];
  return panel('Сортировщик', h('div',
    h('div', badge(binary.status || 'NOT_INSTALLED', tone)),
    h('dl.fi-kv', ...rows.flatMap(([k, v]) => [h('dt', k), h('dd', String(v))])),
    binary.version_verified === false
      ? h('p.muted', 'Сборка не доказала своё происхождение (VERSION_UNVERIFIED): '
        + 'закреплён документ интеграции, а не этот файл.')
      : null,
    ...(binary.notes || []).map((note) => h('p.muted', note)),
    binary.detail ? h('p.muted', binary.detail) : null));
}

function reviewTable(job, selection, onToggle) {
  const entries = (job.envelope && job.envelope.entries) || [];
  if (!entries.length) return h('p.muted', 'Сортировщик не предложил изменений.');
  return h('div.fi-scroll', h('table.fi-table',
    h('thead', h('tr',
      h('th', ''), h('th', 'Сейчас'), h('th', 'Предлагается'),
      h('th', 'Категория'), h('th', 'Куда'), h('th', 'Состояние'))),
    h('tbody', ...entries.map((entry) => h('tr',
      h('td', checkbox({
        checked: selection.has(entry.file_path),
        onChange: () => onToggle(entry.file_path),
        'aria-label': `Выбрать ${entry.file_name}`,
      })),
      h('td', entry.file_name),
      h('td', entry.suggested_name || entry.file_name),
      h('td', [entry.category, entry.subcategory].filter(Boolean).join(' / ') || '—'),
      h('td.fi-path', entry.destination || '—'),
      h('td', entry.refusal
        ? badge(entry.refusal, 'bad')
        : badge('готово к применению', 'muted')))))));
}

function receiptPanel(job) {
  const receipt = job.receipt;
  if (!receipt) return null;
  const applied = receipt.applied || [];
  return panel('Что произошло на самом деле', h('div',
    h('p', `Подтверждённых эффектов: ${receipt.effect_count} из ${applied.length}.`),
    receipt.refusal ? h('p.bad', `${receipt.refusal}: ${receipt.detail || ''}`) : null,
    h('div.fi-scroll', h('table.fi-table',
      h('thead', h('tr', h('th', 'Источник'), h('th', 'Назначение'),
        h('th', 'Содержимое совпало'), h('th', 'Проверено'))),
      h('tbody', ...applied.map((row) => h('tr',
        h('td.fi-path', row.source_path),
        h('td.fi-path', row.destination_path),
        h('td', row.content_sha256_matches ? 'да' : 'нет'),
        h('td', badge(row.verified ? 'проверено' : (row.note || 'нет'),
          row.verified ? 'ok' : 'bad'))))))),
    (receipt.unexpected_mutations || []).length
      ? h('div', h('p.bad', 'Внутри области изменилось лишнее:'),
        h('ul', ...receipt.unexpected_mutations.map((m) => h('li.fi-path', m))))
      : null));
}

const FileIntelligencePage = {
  id: 'file-intelligence',
  title: 'File Intelligence',
  icon: 'folder',
  nav: 'more',
  section: 'work',

  async render(ctx) {
    let status;
    try {
      status = await api.raw('/api/file-intelligence/status');
    } catch (err) {
      return errorNote(err, () => ctx.refresh());
    }

    const head = pageHead('File Intelligence',
      'Сортировщик предлагает — вы решаете. Ничего не двигается, пока вы не '
      + 'утвердите, и результат подтверждается наблюдением за файлами, а не '
      + 'отчётом программы.');

    if (!status.enabled) {
      return h('div.page',
        head,
        privacyBanner(status),
        binaryPanel(status),
        panel('Функция выключена', h('div',
          h('p', `Флаг ${status.flag} по умолчанию выключен.`),
          h('p.muted', 'Владелец включает его явно, на время проверки. '
            + 'Пока он выключен, ничего не запускается и не индексируется.'))));
    }

    const state = { job: null, selection: new Set(), busy: false };
    const body = h('div.page');

    const rerender = () => {
      const parts = [head, privacyBanner(status), binaryPanel(status),
        controls(), state.job ? jobPanel() : null,
        state.job ? receiptPanel(state.job) : null].filter(Boolean);
      body.replaceChildren(...parts);
    };

    const targetInput = input({ placeholder: 'Папка или файлы одного каталога',
      'aria-label': 'Целевой путь' });
    const operationSelect = select(OPERATIONS.map((o) => ({ value: o.value, label: o.label })));

    function controls() {
      return panel('Что разобрать', h('div.fi-form',
        field('Путь', targetInput),
        field('Операция', operationSelect),
        actionButton('Проанализировать', async () => {
          if (state.busy) return;
          state.busy = true;
          try {
            const res = await api.raw('/api/file-intelligence/analyze', {
              method: 'POST',
              body: { task_class: operationSelect.value,
                targets: targetInput.value.split('\n').map((s) => s.trim()).filter(Boolean) },
            });
            state.job = res.job || null;
            state.selection = new Set();
            if (!res.ok) toastError(new Error(res.refused ? `${res.refused}: ${res.detail || ''}` : 'отказано'));
            else toastOk('Анализ завершён — посмотрите предложение');
          } catch (err) { toastError(err); } finally { state.busy = false; rerender(); }
        })));
    }

    function jobPanel() {
      const job = state.job;
      const tone = STATE_TONE[job.state] || 'muted';
      const canApply = job.state === 'REVIEW_REQUIRED' && state.selection.size > 0;
      return panel('Предложение', h('div',
        h('div.fi-state', badge(STATE_LABEL[job.state] || job.state, tone),
          job.refusal ? h('span.bad', ` ${job.refusal}${job.detail ? `: ${job.detail}` : ''}`) : null),
        reviewTable(job, state.selection, (path) => {
          if (state.selection.has(path)) state.selection.delete(path);
          else state.selection.add(path);
          rerender();
        }),
        h('div.fi-actions',
          actionButton(`Применить выбранное (${state.selection.size})`, async () => {
            if (!canApply || state.busy) return;
            state.busy = true;
            try {
              const res = await api.raw('/api/file-intelligence/apply', {
                method: 'POST',
                body: { job_id: job.job_id, selected: [...state.selection] },
              });
              state.job = res.job || state.job;
              if (res.ok) toastOk('Применено и проверено');
              else toastError(new Error(res.refused
                ? `${res.refused}: ${res.detail || ''}`
                : (state.job.detail || 'результат не подтверждён')));
            } catch (err) { toastError(err); } finally { state.busy = false; rerender(); }
          }, { disabled: !canApply }),
          actionButton('Остановить', async () => {
            try {
              const res = await api.raw(
                `/api/file-intelligence/jobs/${job.job_id}/cancel`, { method: 'POST' });
              state.job = res.job || state.job;
              toastOk('Остановлено');
            } catch (err) { toastError(err); } finally { rerender(); }
          }),
          job.state === 'AMBIGUOUS'
            ? actionButton('Разобрать исход', async () => {
              try {
                const res = await api.raw(
                  `/api/file-intelligence/jobs/${job.job_id}/reconcile`, { method: 'POST' });
                state.job = res.job || state.job;
                toastOk('Исход разобран наблюдением');
              } catch (err) { toastError(err); } finally { rerender(); }
            })
            : null)));
    }

    rerender();
    return body;
  },

  onEvent() { return false; },
};

export default FileIntelligencePage;
