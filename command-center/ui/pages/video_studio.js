/* Bossman Video Studio. Human and agent share the revision-guarded command API. */
import { api, listOf } from '../api.js';
import { h, toastError } from '../components.js';
import { TIMEBASE, ticks, seconds, duration, activeSequence, selectedClip, endTime, snapTime, timecode, commandEnvelope, filterMedia } from './video_studio_state.js';

const BASE = '/api/video-studio';
const uid = () => crypto.randomUUID();
const readPreference = (key, fallback) => { try { return JSON.parse(localStorage.getItem(`vs.${key}`)) ?? fallback; } catch { return fallback; } };
const preference = (key, value) => { try { localStorage.setItem(`vs.${key}`, JSON.stringify(value)); } catch { /* storage optional */ } };
let editor;
const RU_LABELS = { 'Timeline, s': 'Позиция, с', 'Source in, s': 'Начало исходника, с', 'Source out, s': 'Конец исходника, с', brightness: 'Яркость', contrast: 'Контраст', saturation: 'Насыщенность', gamma: 'Гамма', x: 'Позиция X', y: 'Позиция Y', scale: 'Масштаб', rotation: 'Поворот', opacity: 'Непрозрачность', Volume: 'Громкость', Pan: 'Панорама', 'Speed ×': 'Скорость ×', Reverse: 'Реверс', 'Freeze frame': 'Стоп-кадр', 'Source second': 'Позиция в исходнике, с', 'Duration, s': 'Длительность, с', 'Delta, s': 'Сдвиг, с', Width: 'Ширина', Height: 'Высота', 'Font size': 'Размер шрифта', Color: 'Цвет', Kind: 'Тип', Profile: 'Профиль', 'Quality (CRF)': 'Качество (CRF)', 'Start, s': 'Начало, с', 'End, s': 'Конец, с', Parameter: 'Параметр', Value: 'Значение', 'Clip-local seconds': 'Время внутри клипа, с', Easing: 'Интерполяция', 'RGB curves': 'Кривые RGB', 'Color balance': 'Баланс цвета', 'Chroma key': 'Хромакей', Mask: 'Маска', 'Stabilize (deshake)': 'Стабилизация (deshake)', 'Sequence settings': 'Параметры секвенции', 'Duplicate sequence': 'Дублировать секвенцию', 'Imported replacement': 'Импортированная замена', Relink: 'Перепривязка', 'Render progress': 'Прогресс рендера', fade_in: 'Появление', fade_out: 'Исчезновение', audio_fade_in: 'Нарастание звука', audio_fade_out: 'Затухание звука', equalizer: 'Эквалайзер', compressor: 'Компрессор', limiter: 'Лимитер', denoise: 'Шумоподавление', loudnorm: 'Нормализация громкости', slip: 'Сдвиг содержимого', slide: 'Сдвиг клипа с подрезкой', 'Enable / disable': 'Включить / выключить' };

const TEXT = {
  ru: { media: 'Медиа', properties: 'Свойства', montage: 'Монтаж', color: 'Цвет', audio: 'Звук', vfx: 'VFX', ai: 'ИИ', import: 'Импорт', export: 'Экспорт', preview: 'Создать preview', project: 'Проект', create: 'Новый проект', open: 'Открыть', rename: 'Переименовать', duplicate: 'Дублировать', archive: 'Архивировать', search: 'Поиск файлов и тегов', all: 'Все папки', name: 'Название', duration: 'Длительность', size: 'Размер', empty: 'Ваш следующий монтаж начинается здесь', emptyHelp: 'Создайте проект и импортируйте исходники. Видео остаётся локально.', drop: 'Перетащите видео, аудио или изображения', noMedia: 'Исходники ещё не импортированы', addTrack: 'Дорожка', split: 'Разрезать', remove: 'Удалить', undo: 'Отменить', redo: 'Повторить', marker: 'Маркер', snap: 'Привязка', range: 'Диапазон', refresh: 'Обновить', saved: 'Сохранено', select: 'Выберите клип на таймлайне', apply: 'Применить', dry: 'Проверить изменения', history: 'История', commands: 'Команды', assistant: 'Bossman AI', proposal: 'Предлагаемые изменения', explain: 'Агент использует те же команды. Сначала проверьте план, затем примените его к текущей revision.', local: 'Локально · без передачи исходников', missing: 'Исходник недоступен', busy: 'Выполняется', cancel: 'Отмена', reset: 'Сбросить раскладку', noPreview: 'Предпросмотр проекта ещё не создан', previewHint: 'Выберите исходник или создайте preview текущей версии.', revisions: 'Версии', title: 'Титр', advanced: 'Расширенная команда', close: 'Закрыть', fullscreen: 'Полный экран', source: 'Исходник', output: 'Результат', conflict: 'Конфликт версии. Изменения не применены; обновите проект и проверьте план снова.', load: 'Загрузка…', folder: 'Папка', tags: 'Теги', add: 'На таймлайн', effect: 'Эффект', keyframe: 'Ключевой кадр', download: 'Скачать файл', verified: 'Проверено', unknown: 'Исход неизвестен', bounds: 'Начало / конец, секунды', inspect: 'Свойства источника', portable: 'Переносимый проект', timeline: 'Таймлайн', fit: 'По размеру', start: 'Начать', selectProject: 'Выберите проект', failure: 'Ошибка', lock: 'Заблокировать', agentUndo: 'Отменить последнюю операцию', changes: 'Изменённые объекты', warning: 'Предупреждения', current: 'Текущая версия', compare: 'Сравнить', unavailable: 'Недоступно', captions: 'Субтитры' },
  en: { media: 'Media', properties: 'Properties', montage: 'Edit', color: 'Color', audio: 'Audio', vfx: 'VFX', ai: 'AI', import: 'Import', export: 'Export', preview: 'Render preview', project: 'Project', create: 'New project', open: 'Open', rename: 'Rename', duplicate: 'Duplicate', archive: 'Archive', search: 'Search files and tags', all: 'All folders', name: 'Name', duration: 'Duration', size: 'Size', empty: 'Your next edit starts here', emptyHelp: 'Create a project and import sources. Your media stays local.', drop: 'Drop video, audio or images here', noMedia: 'No sources imported yet', addTrack: 'Track', split: 'Split', remove: 'Delete', undo: 'Undo', redo: 'Redo', marker: 'Marker', snap: 'Snap', range: 'Range', refresh: 'Refresh', saved: 'Saved', select: 'Select a timeline clip', apply: 'Apply', dry: 'Review changes', history: 'History', commands: 'Commands', assistant: 'Bossman AI', proposal: 'Proposed changes', explain: 'The agent uses the same commands. Review the plan, then apply it against the current revision.', local: 'Local · source files stay on this device', missing: 'Source unavailable', busy: 'Working', cancel: 'Cancel', reset: 'Reset layout', noPreview: 'No project preview yet', previewHint: 'Select a source or render a preview of this revision.', revisions: 'Versions', title: 'Title', advanced: 'Advanced command', close: 'Close', fullscreen: 'Fullscreen', source: 'Source', output: 'Output', conflict: 'Revision conflict. Nothing was applied; refresh the project and review again.', load: 'Loading…', folder: 'Folder', tags: 'Tags', add: 'Add to timeline', effect: 'Effect', keyframe: 'Keyframe', download: 'Download file', verified: 'Verified', unknown: 'Unknown outcome', bounds: 'Start / end, seconds', inspect: 'Source properties', portable: 'Portable project', timeline: 'Timeline', fit: 'Fit', start: 'Start', selectProject: 'Choose a project', failure: 'Error', lock: 'Lock', agentUndo: 'Undo last operation', changes: 'Changed objects', warning: 'Warnings', current: 'Current revision', compare: 'Compare', unavailable: 'Unavailable', captions: 'Captions' },
};

class Editor {
  constructor(ctx, params) {
    this.ctx = ctx; this.params = params; this.lang = readPreference('language', 'ru');
    this.workspace = readPreference('workspace', 'montage'); this.layout = readPreference('layout', { library: 238, inspector: 266, timeline: 284 });
    this.project = null; this.projects = []; this.selected = null; this.selectedIds = new Set(); this.mediaSelection = null;
    this.playhead = 0; this.zoom = 68; this.snap = true; this.query = ''; this.folder = ''; this.sort = 'name';
    this.agentOpen = true; this.busy = false; this.error = ''; this.previewUrl = ''; this.previewRevision = null;
    this.jobs = new Map(); this.root = h('section.vs-studio', { tabindex: '0', 'aria-label': 'Bossman Video Studio', onKeydown: e => this.keyboard(e) });
    this.proposalText = '{\n  "type": "clip.split",\n  "clip_id": "",\n  "at": 1000000\n}';
    this.review = null; this.lastChange = []; this.disposed = false;
  }
  t(key) { return (TEXT[this.lang] || TEXT.ru)[key] || key; }
  label(text) { return this.lang === 'ru' ? RU_LABELS[text] || text : text; }
  button(label, fn, opts = {}) {
    return h('button.vs-button', { type: 'button', title: this.label(opts.title || label), onClick: () => this.guard(fn), ...opts }, this.label(label));
  }
  async guard(fn) { try { return await fn(); } catch (error) { this.error = error.status === 409 ? this.t('conflict') : error.message; this.paint(); toastError(this.error); } }
  async load() {
    try {
      this.projects = listOf(await api.raw(`${BASE}/projects`), 'projects');
      this.capabilities = await api.raw(`${BASE}/capabilities`);
      const id = this.params.project_id || readPreference('project', null);
      if (id) { this.project = await api.raw(`${BASE}/projects/${encodeURIComponent(id)}`); await this.restoreJobs(); }
    } catch (error) { this.error = error.message; }
    this.paint(); return this.root;
  }
  async refresh() {
    if (!this.project || this.busy || this.dragging) return;
    const next = await api.raw(`${BASE}/projects/${encodeURIComponent(this.project.id)}`);
    if (next.revision !== this.project.revision) {
      if (this.root.contains(document.activeElement) && document.activeElement.matches('input,textarea,select')) {
        this.remoteChange = true; return; // Retain the edit's original revision; never overwrite concurrent work.
      }
      this.project = next; this.review = null; this.paint();
    }
  }
  async command(command, dryRun = false, operationId = uid()) {
    if (this.busy) throw new Error(this.t('busy'));
    this.busy = true; this.error = '';
    try {
      const result = await api.raw(`${BASE}/commands`, { method: 'POST', body: commandEnvelope(this.project, command, operationId, dryRun) });
      if (!dryRun) { this.project = result.project; this.lastChange = result.changed_ids || []; this.review = null; }
      return result;
    } finally { this.busy = false; this.paint(); }
  }
  async open(id) {
    this.project = await api.raw(`${BASE}/projects/${encodeURIComponent(id)}`);
    if (!this.projects.some(p => p.id === id)) this.projects.push(this.project);
    this.selected = null; this.selectedIds.clear(); this.mediaSelection = null; this.previewUrl = ''; this.review = null; this.playhead = 0; this.timelineOffset = { left: 0, top: 0 };
    preference('project', id); this.params.project_id = id; this.jobs.clear(); await this.restoreJobs();
    history.replaceState(null, '', `#/video-studio?project_id=${encodeURIComponent(id)}`); this.paint();
  }
  form(title, fields, submit) {
    const dialog = h('dialog.vs-dialog'); const inputs = {};
    const body = h('form', { onSubmit: e => { e.preventDefault(); this.guard(async () => { await submit(Object.fromEntries(Object.entries(inputs).map(([k, el]) => [k, el.type === 'checkbox' ? el.checked : el.value]))); dialog.close(); dialog.remove(); }); } });
    body.append(h('div.vs-dialog-head', h('h2', this.label(title)), this.button('×', () => { dialog.close(); dialog.remove(); }, { title: this.t('close') })));
    for (const field of fields) {
      const input = field.options ? h('select', ...field.options.map(x => h('option', { value: typeof x === 'string' ? x : x.value, selected: (typeof x === 'string' ? x : x.value) === field.value }, typeof x === 'string' ? x : x.label)))
        : h(field.multiline ? 'textarea' : 'input', { type: field.type || 'text', value: field.value ?? '', required: !!field.required, min: field.min, max: field.max, step: field.step ?? 'any', rows: field.multiline ? 5 : null });
      inputs[field.key] = input; body.append(h('label.vs-field', h('span', this.label(field.label)), input));
    }
    body.append(h('button.vs-button.vs-primary', { type: 'submit' }, this.t('apply'))); dialog.append(body); document.body.append(dialog);
    dialog.addEventListener('cancel', () => dialog.remove()); dialog.showModal();
  }
  create() {
    this.form(this.t('create'), [{ key: 'name', label: this.t('name'), required: true }], async ({ name }) => {
      const result = await api.raw(`${BASE}/projects`, { method: 'POST', body: { name, operation_id: uid(), links: {} } });
      this.projects = listOf(await api.raw(`${BASE}/projects`), 'projects'); await this.open(result.project_id || result.project.id);
    });
  }
  paint() {
    if (this.disposed) return;
    const oldScroll = this.root.querySelector('.vs-timeline-scroll'); const scrollLeft = oldScroll?.scrollLeft || 0; const scrollTop = oldScroll?.scrollTop || 0;
    this.root.style.setProperty('--vs-library', `${this.layout.library}px`); this.root.style.setProperty('--vs-inspector', `${this.layout.inspector}px`);
    this.root.style.setProperty('--vs-timeline', `${this.layout.timeline}px`);
    this.root.classList.toggle('vs-agent-closed', !this.agentOpen);
    this.root.replaceChildren(this.header());
    if (this.error) this.root.append(h('div.vs-error', { role: 'alert' }, this.error, this.button('×', () => { this.error = ''; this.paint(); })));
    if (!this.project) {
      this.root.append(h('div.vs-empty', h('div.vs-empty-icon', '▤'), h('h1', this.t('empty')), h('p', this.t('emptyHelp')), this.button(`＋ ${this.t('create')}`, () => this.create(), { class: 'vs-primary' }))); return;
    }
    const main = h('div.vs-main', this.library(), this.resizer('library'), this.preview(), this.resizer('inspector'), this.inspector(), this.agentOpen ? this.assistant() : null);
    this.root.append(main, this.resizer('timeline'), this.timeline(), this.footer());
    const scroll = this.root.querySelector('.vs-timeline-scroll'); if (scroll) { scroll.scrollLeft = scrollLeft; scroll.scrollTop = scrollTop; }
  }
  header() {
    return h('header.vs-header', h('a.vs-brand', { href: '#/home' }, 'BOSSMAN', h('span', '/ VIDEO STUDIO')),
      h('select.vs-project-select', { 'aria-label': this.t('selectProject'), onChange: e => this.guard(() => this.open(e.target.value)) },
        h('option', { value: '', selected: !this.project, disabled: true }, this.t('selectProject')),
        ...this.projects.map(p => h('option', { value: p.id, selected: p.id === this.project?.id }, p.name))),
      this.button('＋', () => this.create(), { title: this.t('create') }),
      this.button('⋯', () => this.projectMenu(), { title: this.t('project'), disabled: !this.project }),
      h('nav.vs-workspaces', { 'aria-label': 'Workspace' }, ...['montage', 'color', 'audio', 'vfx', 'ai'].map(key => this.button(this.t(key), () => { this.workspace = key; preference('workspace', key); this.paint(); }, { class: this.workspace === key ? 'active' : '', 'aria-pressed': this.workspace === key }))),
      this.button('↶', () => this.command({ type: 'history.undo' }), { title: `${this.t('undo')} · Ctrl+Z`, disabled: !this.project }),
      this.button('↷', () => this.command({ type: 'history.redo' }), { title: `${this.t('redo')} · Ctrl+Shift+Z`, disabled: !this.project }),
      this.button('⌘', () => this.palette(), { title: `${this.t('commands')} · Ctrl+K`, disabled: !this.project }),
      this.button('✦', () => { this.agentOpen = !this.agentOpen; this.paint(); }, { title: this.t('assistant') }),
      this.button(this.lang === 'ru' ? 'EN' : 'RU', () => { this.lang = this.lang === 'ru' ? 'en' : 'ru'; preference('language', this.lang); this.paint(); }),
      this.button(this.t('export'), () => this.exportDialog(false), { class: 'vs-primary', disabled: !this.project }));
  }
  resizer(kind) {
    return h('div.vs-resizer', { class: kind === 'timeline' ? 'horizontal' : '', role: 'separator', tabindex: '0', 'aria-label': `Resize ${kind}`,
      onPointerdown: e => {
        const start = kind === 'timeline' ? e.clientY : e.clientX; const initial = this.layout[kind]; e.currentTarget.setPointerCapture(e.pointerId);
        const target = e.currentTarget;
        const move = ev => { const delta = (kind === 'timeline' ? ev.clientY : ev.clientX) - start;
          this.layout[kind] = Math.max(kind === 'timeline' ? 180 : 180, Math.min(kind === 'timeline' ? 520 : 420, initial + delta * (kind === 'library' ? 1 : -1)));
          this.root.style.setProperty(`--vs-${kind}`, `${this.layout[kind]}px`); };
        const end = () => { preference('layout', this.layout); target.removeEventListener('pointermove', move); target.removeEventListener('pointerup', end); };
        target.addEventListener('pointermove', move); target.addEventListener('pointerup', end, { once: true });
      } });
  }
  library() {
    const media = filterMedia(this.project.media, this.query, this.folder, this.sort);
    const folders = [...new Set(Object.values(this.project.media).map(m => m.folder).filter(Boolean))];
    return h('aside.vs-library.vs-panel', { onDragover: e => { if (e.dataTransfer.types.includes('Files')) { e.preventDefault(); e.currentTarget.classList.add('drag-over'); } }, onDragleave: e => e.currentTarget.classList.remove('drag-over'), onDrop: e => { e.preventDefault(); e.currentTarget.classList.remove('drag-over'); if (e.dataTransfer.files.length) this.guard(() => this.importFiles(e.dataTransfer.files)); } },
      h('div.vs-panel-head', h('strong', this.t('media')), h('label.vs-button', this.t('import'), h('input', { type: 'file', multiple: true, hidden: true, accept: 'video/*,audio/*,image/*', onChange: e => this.guard(() => this.importFiles(e.target.files)) }))),
      h('input.vs-search', { type: 'search', placeholder: this.t('search'), value: this.query, onInput: e => { this.query = e.target.value; const pos = e.target.selectionStart; this.paint(); const input = this.root.querySelector('.vs-search'); input.focus(); if (input.type === 'text') input.setSelectionRange(pos, pos); } }),
      h('div.vs-library-filters', h('select', { 'aria-label': this.t('folder'), onChange: e => { this.folder = e.target.value; this.paint(); } }, h('option', { value: '' }, this.t('all')), ...folders.map(f => h('option', { value: f, selected: f === this.folder }, f))),
        h('select', { 'aria-label': 'Sort', onChange: e => { this.sort = e.target.value; this.paint(); } }, ...['name', 'duration', 'size'].map(k => h('option', { value: k, selected: this.sort === k }, this.t(k))))),
      h('div.vs-media-grid', ...media.map(m => h('article.vs-media-card', { tabindex: '0', draggable: true, class: this.mediaSelection === m.id ? 'selected' : '',
        onDragstart: e => e.dataTransfer.setData('application/bossman-media', m.id), onClick: () => { this.mediaSelection = m.id; this.selected = null; this.paint(); },
        onDblclick: () => this.guard(() => this.addMedia(m.id)), onContextmenu: e => { e.preventDefault(); this.mediaMenu(m); } },
        h('div.vs-thumb', m.has_video || m.width ? h('img', { src: `${BASE}/media/${encodeURIComponent(m.id)}/thumbnail?project_id=${encodeURIComponent(this.project.id)}`, alt: '', loading: 'lazy', onError: e => { e.target.hidden = true; } }) : h('span', '♫'), h('span.vs-duration', timecode(m.duration_ticks, m.fps))),
        h('div.vs-media-name', { title: m.name }, m.name), h('div.vs-media-meta', m.has_video ? `${m.width}×${m.height}` : `${m.sample_rate || '—'} Hz`, m.tags?.length ? ` · ${m.tags.join(', ')}` : '')))),
      !media.length ? h('div.vs-library-empty', h('span', '⇩'), h('p', this.t('noMedia')), h('small', this.t('drop'))) : null,
      this.uploadStage ? h('div.vs-stage', { role: 'status' }, this.uploadStage) : null);
  }
  async importFiles(files) {
    const list = [...files];
    for (let i = 0; i < list.length; i++) {
      const file = list[i]; this.uploadStage = `${this.t('import')} ${i + 1}/${list.length} · ${file.name}`; this.paint();
      const query = new URLSearchParams({ project_id: this.project.id, filename: file.name, expected_revision: this.project.revision, operation_id: uid() });
      let data;
      try { data = await api.raw(`${BASE}/media?${query}`, { method: 'POST', body: file }); }
      catch (error) { this.uploadStage = ''; throw error; }
      this.project = data.project;
    }
    this.uploadStage = ''; this.paint();
  }
  mediaMenu(media) {
    this.form(this.t('inspect'), [{ key: 'name', label: this.t('name'), value: media.name }, { key: 'folder', label: this.t('folder'), value: media.folder }, { key: 'tags', label: this.t('tags'), value: (media.tags || []).join(', ') }],
      ({ name, folder, tags }) => this.command({ type: 'media.update', media_id: media.id, patch: { name, folder, tags: tags.split(',').map(x => x.trim()).filter(Boolean) } }));
  }
  async addMedia(mediaId, trackId, start) {
    const media = this.project.media[mediaId]; const sequence = activeSequence(this.project);
    let track = sequence.tracks.find(t => t.id === trackId) || sequence.tracks.find(t => t.kind === (media.has_video || media.width ? 'video' : 'audio') && !t.locked);
    if (!track) { const result = await this.command({ type: 'track.add', kind: media.has_video || media.width ? 'video' : 'audio', name: media.has_video || media.width ? 'Video' : 'Audio' }); track = activeSequence(result.project).tracks.at(-1); }
    await this.command({ type: 'clip.add', track_id: track.id, clip: { media_id: mediaId, start: start ?? Math.max(0, ...track.clips.map(c => c.start + duration(c))), source_in: 0, source_out: media.duration_ticks || 5 * TIMEBASE } });
  }
  preview() {
    const media = this.project.media[this.mediaSelection];
    const url = media ? `${BASE}/media/${encodeURIComponent(media.id)}/file?project_id=${encodeURIComponent(this.project.id)}` : this.previewUrl;
    const stage = h('div.vs-preview-stage');
    if (url) {
      const player = h(media && !media.has_video && media.width ? 'img' : 'video', { src: url, controls: true, preload: 'metadata', playsinline: true, 'aria-label': this.t('preview'),
        onLoadedmetadata: e => { if (!media) e.target.currentTime = seconds(this.playhead); },
        onTimeupdate: e => { if (!media) { this.playhead = Math.round(e.target.currentTime * TIMEBASE); this.updatePlayhead(); } },
        onError: () => { stage.append(h('div.vs-preview-error', this.t('missing'))); } }); stage.append(player);
    } else stage.append(h('div.vs-preview-empty', h('div', '▷'), h('strong', this.t('noPreview')), h('p', this.t('previewHint')), this.button(this.t('preview'), () => this.startExport(true), { disabled: !endTime(this.project) })));
    return h('section.vs-preview.vs-panel', h('div.vs-panel-head', h('span', `${media ? this.t('source') : this.t('project')}: ${media?.name || this.project.name}`),
      h('small', media ? '' : this.previewRevision === null ? '' : `r${this.previewRevision}${this.previewRevision !== this.project.revision ? ' · outdated' : ''}`)), stage,
      h('div.vs-transport', h('span.vs-timecode', timecode(this.playhead, activeSequence(this.project).fps)),
        this.button('│◀', () => this.seek(0)), this.button('◀', () => this.seek(Math.max(0, this.playhead - TIMEBASE * activeSequence(this.project).fps.den / activeSequence(this.project).fps.num))),
        this.button('▶', () => this.togglePlay()), this.button('▶│', () => this.seek(endTime(this.project))), h('span', timecode(endTime(this.project), activeSequence(this.project).fps)),
        this.button('⛶', () => stage.requestFullscreen(), { title: this.t('fullscreen') })),
      h('div.vs-preview-actions', this.button(this.t('preview'), () => { this.mediaSelection = null; return this.startExport(true); }, { disabled: this.busy || !endTime(this.project) }), h('small', `${activeSequence(this.project).width}×${activeSequence(this.project).height} · ${activeSequence(this.project).fps.num}/${activeSequence(this.project).fps.den} fps`)),
      this.jobList());
  }
  togglePlay() { const player = this.root.querySelector('.vs-preview video'); if (player) { if (player.paused) return player.play(); player.pause(); } }
  seek(time) { this.playhead = Math.round(Math.max(0, time)); const player = this.root.querySelector('.vs-preview video'); if (player && !this.mediaSelection) player.currentTime = seconds(this.playhead); this.updatePlayhead(); }
  updatePlayhead() { this.root.querySelectorAll('.vs-timecode').forEach(el => el.textContent = timecode(this.playhead, activeSequence(this.project).fps)); this.root.querySelectorAll('.vs-playhead').forEach(el => el.style.left = `${seconds(this.playhead) * this.zoom}px`); }
  inspector() {
    const selected = selectedClip(this.project, this.selected); const media = this.project.media[this.mediaSelection];
    const panel = h('aside.vs-inspector.vs-panel', { onFocusin: () => { if (selected) this.guard(() => this.lease([selected.clip.id])); } }, h('div.vs-panel-head', h('strong', this.t('properties')), selected ? h('small', selected.clip.id.slice(0, 10)) : null));
    if (!selected) {
      if (media) panel.append(h('div.vs-inspector-body', h('h3', media.name), h('dl.vs-metadata', ...Object.entries({ duration: timecode(media.duration_ticks), dimensions: `${media.width || 0}×${media.height || 0}`, audio: media.has_audio, bytes: media.bytes, sha256: media.sha256 }).flatMap(([key, value]) => [h('dt', key), h('dd', String(value))])), this.button(this.t('add'), () => this.addMedia(media.id)), this.button(this.t('inspect'), () => this.mediaMenu(media)),
        this.button(this.lang === 'ru' ? 'Сцены / паузы · локально' : 'Scenes / pauses · local', () => this.analyse(media.id, 'analyse')),
        this.button(this.lang === 'ru' ? 'Распознать речь · локально' : 'Transcribe · local', () => this.analyse(media.id, 'transcribe')),
        this.button(this.lang === 'ru' ? 'Перепривязать' : 'Relink', () => this.form('Relink', [{ key: 'replacement_media_id', label: 'Imported replacement', options: Object.values(this.project.media).filter(m => m.id !== media.id).map(m => ({ value: m.id, label: m.name })) }], async f => {
          const result = await api.raw(`${BASE}/media/relink`, { method: 'POST', body: { project_id: this.project.id, media_id: media.id, replacement_media_id: f.replacement_media_id, expected_revision: this.project.revision, operation_id: uid() } }); this.project = result.project; this.paint();
        }))));
      else panel.append(h('p.vs-muted', this.t('select'))); return panel;
    }
    const { clip, track } = selected;
    const body = h('div.vs-inspector-body');
    body.append(h('h3', clip.title?.text || this.project.media[clip.media_id]?.name || 'Sequence'), h('small.vs-muted', `${track.name} · ${timecode(clip.start)} — ${timecode(clip.start + duration(clip))}`));
    const number = (label, value, apply, opts = {}) => h('label.vs-numeric', h('span', this.label(label)), h('input', { type: 'number', value, step: opts.step || 'any', min: opts.min, max: opts.max, disabled: track.locked,
      onChange: e => this.guard(() => { const n = Number(e.target.value); if (!Number.isFinite(n)) throw new Error('Finite number required'); return apply(n); }) }));
    body.append(number('Timeline, s', seconds(clip.start), n => this.command({ type: 'clip.move', clip_id: clip.id, start: ticks(n) }), { min: 0 }),
      number('Source in, s', seconds(clip.source_in), n => this.command({ type: 'clip.trim', clip_id: clip.id, source_in: ticks(n) }), { min: 0 }),
      number('Source out, s', seconds(clip.source_out), n => this.command({ type: 'clip.trim', clip_id: clip.id, source_out: ticks(n) }), { min: 0 }));
    const transform = h('details', { open: ['montage', 'vfx'].includes(this.workspace) }, h('summary', 'Transform'));
    for (const [key, fallback] of Object.entries({ x: 0, y: 0, scale: 1, rotation: 0, opacity: 1 })) transform.append(number(key, clip.transform?.[key] ?? fallback,
      n => this.command({ type: 'clip.transform', clip_id: clip.id, patch: { [key]: n } }), key === 'opacity' ? { min: 0, max: 1, step: .05 } : {}));
    body.append(transform, this.workspaceControls(clip, number));
    body.append(h('details', { open: true }, h('summary', this.t('effect')), ...(clip.effects || []).map(effect => h('div.vs-effect', h('strong', effect.type), h('small', JSON.stringify(effect.params)),
      this.button(effect.enabled ? '◉' : '○', () => this.command({ type: 'effect.apply', clip_id: clip.id, effect: { ...effect, enabled: !effect.enabled } }), { title: 'Enable / disable' }),
      this.button('×', () => this.command({ type: 'effect.remove', clip_id: clip.id, effect_id: effect.id }), { title: this.t('remove') }))),
      this.button(`＋ ${this.t('effect')}`, () => this.effectDialog(clip)), this.button(`◇ ${this.t('keyframe')}`, () => this.keyframeDialog(clip))));
    body.append(h('div.vs-inspector-tools', this.button(this.t('split'), () => this.command({ type: 'clip.split', clip_id: clip.id, at: this.playhead }), { disabled: track.locked }),
      this.button(this.t('remove'), () => this.command({ type: 'clip.remove', clip_id: clip.id }), { disabled: track.locked }),
      this.button(this.t('advanced'), () => this.palette({ type: 'clip.transform', clip_id: clip.id, patch: {} }))));
    panel.append(body); return panel;
  }
  effectDialog(clip) {
    const defaults = this.workspace === 'audio' ? { type: 'volume', params: { gain: 1 } } : { type: 'color', params: { brightness: 0, contrast: 1, saturation: 1 } };
    this.form(this.t('effect'), [{ key: 'effect', label: 'Effect JSON', value: JSON.stringify(defaults, null, 2), multiline: true }], ({ effect }) => this.command({ type: 'effect.apply', clip_id: clip.id, effect: JSON.parse(effect) }));
  }
  workspaceControls(clip, number) {
    const controls = h('div.vs-workspace-controls');
    const effect = (type, params) => { const existing = clip.effects.find(e => e.type === type); return this.command({ type: 'effect.apply', clip_id: clip.id, effect: { ...(existing || {}), type, params: { ...(existing?.params || {}), ...params }, enabled: true } }); };
    if (this.workspace === 'color') {
      controls.append(h('h3', this.t('color')));
      for (const [param, fallback, min, max] of [['brightness', 0, -1, 1], ['contrast', 1, 0, 3], ['saturation', 1, 0, 3], ['gamma', 1, .1, 10]]) {
        const old = clip.effects.find(e => e.type === 'color'); controls.append(number(param, old?.params?.[param] ?? fallback, value => effect('color', { [param]: value }), { min, max, step: .05 }));
      }
      controls.append(this.button('RGB curves', () => this.form('RGB curves', [{ key: 'params', label: 'Points 0..1', value: '{"master":[[0,0],[0.5,0.5],[1,1]]}', multiline: true }], f => effect('curves', JSON.parse(f.params)))),
        this.button('LUT .cube', () => this.form('LUT .cube', [{ key: 'cube_text', label: 'LUT_3D_SIZE + RGB samples', multiline: true }], f => effect('lut3d', f))),
        this.button('Color balance', () => this.form('Color balance', ['rs', 'gs', 'bs', 'rm', 'gm', 'bm', 'rh', 'gh', 'bh'].map(key => ({ key, label: key, type: 'number', min: -1, max: 1, value: 0 })), f => effect('colorbalance', Object.fromEntries(Object.entries(f).map(([k, v]) => [k, Number(v)]))))));
    } else if (this.workspace === 'audio') {
      controls.append(h('h3', this.t('audio')), number('Volume', clip.volume ?? 1, value => this.command({ type: 'clip.audio', clip_id: clip.id, patch: { volume: value } }), { min: 0, max: 16 }),
        number('Pan', clip.pan ?? 0, value => this.command({ type: 'clip.audio', clip_id: clip.id, patch: { pan: value } }), { min: -1, max: 1 }));
      const presets = { loudnorm: { integrated: -16, true_peak: -1, range: 11 }, denoise: { reduction: 12 }, limiter: { limit: .95 }, compressor: { threshold: .125, ratio: 4, attack: 20, release: 250 }, equalizer: { frequency: 1000, width: 1, gain: 0 }, audio_fade_in: { duration: .5 }, audio_fade_out: { duration: .5 } };
      for (const [name, params] of Object.entries(presets)) controls.append(this.button(name, () => this.form(name, Object.entries(params).map(([key, value]) => ({ key, label: key, type: 'number', value })), f => effect(name, Object.fromEntries(Object.entries(f).map(([k, v]) => [k, Number(v)]))))));
    } else if (this.workspace === 'vfx') {
      controls.append(h('h3', 'VFX'), this.button('Chroma key', () => this.form('Chroma key', [{ key: 'color', label: 'RGB hex', value: '00FF00' }, { key: 'similarity', label: 'Similarity', value: .1, min: .01, max: 1, type: 'number' }, { key: 'blend', label: 'Blend', value: .05, min: 0, max: 1, type: 'number' }], f => effect('chroma', { color: f.color, similarity: Number(f.similarity), blend: Number(f.blend) }))),
        this.button('Mask', () => this.form('Mask', [{ key: 'shape', label: 'Shape', options: ['rectangle', 'circle'] }, { key: 'x', label: 'x (0..1)', type: 'number', value: 0 }, { key: 'y', label: 'y (0..1)', type: 'number', value: 0 }, { key: 'width', label: 'Width (0..1)', type: 'number', value: 1 }, { key: 'height', label: 'Height (0..1)', type: 'number', value: 1 }, { key: 'radius', label: 'Radius', type: 'number', value: .5 }], f => effect('mask', Object.fromEntries(Object.entries(f).map(([k, v]) => [k, k === 'shape' ? v : Number(v)]))))));
      for (const name of ['fade_in', 'fade_out']) controls.append(this.button(name, () => this.form(name, [{ key: 'duration', label: 'Seconds', type: 'number', min: .001, value: .5 }], f => effect(name, { duration: Number(f.duration) }))));
      controls.append(this.button('Stabilize (deshake)', () => effect('stabilize', {})));
    } else if (this.workspace === 'ai') {
      const mediaId = clip.media_id;
      controls.append(h('h3', this.t('ai')), h('p.vs-muted', this.t('local')),
        this.button(this.lang === 'ru' ? 'Найти сцены и паузы' : 'Find scenes and pauses', () => this.analyse(mediaId, 'analyse'), { disabled: !mediaId }),
        this.button(this.lang === 'ru' ? 'Распознать речь' : 'Transcribe speech', () => this.analyse(mediaId, 'transcribe'), { disabled: !mediaId || this.capabilities?.transcription?.status !== 'AVAILABLE', title: this.capabilities?.transcription?.reason }),
        h('p.vs-muted', this.capabilities?.transcription?.reason || ''), h('div.vs-effect', h('strong', this.lang === 'ru' ? 'Генерация изображений / видео' : 'Image / video generation'), h('p', this.capabilities?.generation?.reason || this.t('unavailable'))));
    } else {
      controls.append(number('Speed ×', (clip.speed?.num || 1) / (clip.speed?.den || 1), value => this.command({ type: 'clip.speed', clip_id: clip.id, speed: { num: Math.round(value * 1000), den: 1000 } }), { min: .01, max: 100 }),
        this.button('Reverse', () => this.command({ type: 'clip.reverse', clip_id: clip.id, reverse: !clip.reverse }), { class: clip.reverse ? 'active' : '' }),
        this.button('Freeze frame', () => this.form('Freeze frame', [{ key: 'at_source', label: 'Source second', type: 'number', value: seconds(clip.source_in) }, { key: 'duration', label: 'Duration, s', type: 'number', min: .001, value: 2 }], f => this.command({ type: 'clip.freeze', clip_id: clip.id, at_source: ticks(f.at_source), duration: ticks(f.duration) }))));
      for (const type of ['clip.slip', 'clip.slide']) controls.append(this.button(type.split('.')[1], () => this.form(type, [{ key: 'delta', label: 'Delta, s', type: 'number', value: 0 }], f => this.command({ type, clip_id: clip.id, delta: Math.round(Number(f.delta) * TIMEBASE) }))));
    }
    return controls;
  }
  keyframeDialog(clip) {
    this.form(this.t('keyframe'), [{ key: 'param', label: 'Parameter', value: 'opacity', options: ['x', 'y', 'scale', 'rotation', 'opacity', 'volume'] }, { key: 't', label: 'Clip-local seconds', type: 'number', min: 0, value: seconds(Math.max(0, this.playhead - clip.start)) }, { key: 'value', label: 'Value', type: 'number', value: 1 }, { key: 'easing', label: 'Easing', options: ['linear', 'hold', 'ease_in', 'ease_out', 'ease_in_out'] }],
      f => this.command({ type: 'keyframe.set', clip_id: clip.id, param: f.param, t: ticks(f.t), value: Number(f.value), easing: f.easing }));
  }
  timeline() {
    const sequence = activeSequence(this.project); const width = Math.max(900, seconds(endTime(this.project) + 8 * TIMEBASE) * this.zoom); const interval = this.zoom > 100 ? 1 : this.zoom > 35 ? 5 : 10;
    const offset = this.timelineOffset || { left: 0, top: 0 }; const viewWidth = this.root.clientWidth || 1500;
    const visibleStart = Math.max(0, offset.left - viewWidth); const visibleEnd = offset.left + viewWidth * 2;
    const firstTick = Math.floor(visibleStart / this.zoom / interval); const lastTick = Math.min(Math.ceil(width / this.zoom / interval), Math.ceil(visibleEnd / this.zoom / interval));
    const ruler = h('div.vs-ruler', { style: { width: `${width}px` }, onClick: e => this.seek(ticks((e.clientX - e.currentTarget.getBoundingClientRect().left) / this.zoom)) },
      ...Array.from({ length: Math.max(0, lastTick - firstTick) }, (_, relative) => { const index = firstTick + relative; return h('span', { style: { left: `${index * interval * this.zoom}px` } }, timecode(index * interval * TIMEBASE).slice(3, 8)); }),
      ...(this.project.markers || []).map(m => h('button.vs-marker', { style: { left: `${seconds(m.t) * this.zoom}px` }, title: m.label, onClick: e => { e.stopPropagation(); this.seek(m.t); } }, '◆')));
    const labels = h('div.vs-track-labels', h('div.vs-track-label-spacer', this.t('timeline')), ...sequence.tracks.map(track => h('div.vs-track-label',
      h('div', h('strong', track.name), h('small', track.kind)), h('div.vs-track-toggles', ...[['mute', 'M'], ['solo', 'S'], ['locked', '▣']].map(([key, label]) => this.button(label, () => this.command({ type: 'track.update', track_id: track.id, patch: { [key]: !track[key] } }), { class: track[key] ? 'active' : '', title: key, 'aria-pressed': track[key] }))),
      this.button('⋮', () => this.trackDialog(track), { title: this.t('properties') }))));
    const lanes = h('div.vs-lanes', { style: { width: `${width}px` } }, ruler,
      ...sequence.tracks.map(track => h('div.vs-track-lane', { class: `kind-${track.kind}${track.locked ? ' locked' : ''}`, dataset: { trackId: track.id },
        onDragover: e => { if (!track.locked) e.preventDefault(); }, onDrop: e => { e.preventDefault(); const id = e.dataTransfer.getData('application/bossman-media'); if (id && !track.locked) this.guard(() => this.addMedia(id, track.id, ticks(Math.max(0, (e.clientX - e.currentTarget.getBoundingClientRect().left) / this.zoom)))); },
        onClick: e => { if (e.target === e.currentTarget) this.seek(ticks(Math.max(0, (e.clientX - e.currentTarget.getBoundingClientRect().left) / this.zoom))); } },
        ...track.clips.filter(clip => seconds(clip.start + duration(clip)) * this.zoom >= visibleStart && seconds(clip.start) * this.zoom <= visibleEnd).map(clip => this.clipNode(clip, track)), sequence.range ? h('div.vs-range-shade', { style: { left: `${seconds(sequence.range.start) * this.zoom}px`, width: `${seconds(sequence.range.end - sequence.range.start) * this.zoom}px` } }) : null)),
      h('div.vs-playhead', { style: { left: `${seconds(this.playhead) * this.zoom}px` } }));
    return h('section.vs-timeline.vs-panel', h('div.vs-timeline-tools',
      this.button('＋', () => this.trackDialog(), { title: this.t('addTrack') }),
      this.button('✂', () => this.selected && this.command({ type: 'clip.split', clip_id: this.selected, at: this.playhead }), { title: `${this.t('split')} · S`, disabled: !this.selected }),
      this.button('⌫', () => this.selected && this.command({ type: 'clip.remove', clip_id: this.selected, ripple: false }), { title: this.t('remove'), disabled: !this.selected }),
      this.button(this.t('snap'), () => { this.snap = !this.snap; this.paint(); }, { class: this.snap ? 'active' : '', 'aria-pressed': this.snap }),
      this.button(`◆ ${this.t('marker')}`, () => this.form(this.t('marker'), [{ key: 'label', label: this.t('name'), value: '' }], f => this.command({ type: 'marker.add', marker: { t: this.playhead, label: f.label } }))),
      this.button(this.t('range'), () => this.form(this.t('range'), [{ key: 'start', label: 'Start, s', value: seconds(sequence.range?.start || 0), type: 'number', min: 0 }, { key: 'end', label: 'End, s', value: seconds(sequence.range?.end || endTime(this.project)), type: 'number', min: 0 }], f => this.command({ type: 'range.set', start: ticks(f.start), end: ticks(f.end) }))),
      this.button(`T ${this.t('title')}`, () => this.titleDialog()), this.button(this.t('captions'), () => this.captionsDialog()),
      this.button(this.lang === 'ru' ? 'Группа' : 'Group', () => this.command({ type: 'clip.group', clip_ids: [...this.selectedIds] }), { disabled: this.selectedIds.size < 2 }),
      this.button(this.lang === 'ru' ? 'Связать' : 'Link', () => this.command({ type: 'clip.link', clip_ids: [...this.selectedIds] }), { disabled: this.selectedIds.size < 2 }),
      h('span.vs-spacer'), h('span', '−'), h('input', { type: 'range', min: 8, max: 220, value: this.zoom, 'aria-label': 'Timeline zoom', onInput: e => { this.zoom = Number(e.target.value); this.paint(); } }), h('span', '+')),
      h('div.vs-timeline-body', labels, h('div.vs-timeline-scroll', { onScroll: e => {
        labels.scrollTop = e.target.scrollTop; this.timelineOffset = { left: e.target.scrollLeft, top: e.target.scrollTop }; clearTimeout(this.timelineTimer);
        if (Math.abs(this.timelineOffset.left - offset.left) < viewWidth / 2) return;
        this.timelineTimer = setTimeout(() => { if (this.disposed || this.dragging || !this.root.isConnected) return; const saved = this.timelineOffset; this.root.querySelector('.vs-timeline')?.replaceWith(this.timeline()); const scroll = this.root.querySelector('.vs-timeline-scroll'); scroll.scrollLeft = saved.left; scroll.scrollTop = saved.top; }, 180);
      } }, lanes)));
  }
  clipNode(clip, track) {
    const media = this.project.media[clip.media_id];
    const node = h('div.vs-clip', { class: `${this.selected === clip.id || this.selectedIds.has(clip.id) ? 'selected' : ''} ${clip.title ? 'title' : ''}`, tabindex: '0', role: 'button', 'aria-label': media?.name || clip.title?.text || clip.id, dataset: { clipId: clip.id },
      style: { left: `${seconds(clip.start) * this.zoom}px`, width: `${Math.max(8, seconds(duration(clip)) * this.zoom)}px` },
      onClick: e => { e.stopPropagation(); if (e.ctrlKey || e.metaKey || e.shiftKey) { if (this.selectedIds.has(clip.id)) this.selectedIds.delete(clip.id); else this.selectedIds.add(clip.id); } else this.selectedIds = new Set([clip.id]); this.selected = clip.id; this.mediaSelection = null; this.paint(); this.root.focus({ preventScroll: true }); },
      onContextmenu: e => { e.preventDefault(); this.selected = clip.id; this.palette({ type: 'clip.remove', clip_id: clip.id, ripple: true }); },
      onPointerdown: e => { if (!track.locked && e.button === 0) this.dragClip(e, clip, track, 'move'); } },
      h('span.vs-trim-handle.left', { onPointerdown: e => { e.stopPropagation(); if (!track.locked) this.dragClip(e, clip, track, 'in'); } }),
      h('span.vs-clip-label', clip.title?.text || media?.name || 'Sequence'),
      h('small', `${clip.reverse ? '↶ ' : ''}${clip.speed?.num !== clip.speed?.den ? `${clip.speed?.num}/${clip.speed?.den}×` : ''}`),
      h('span.vs-trim-handle.right', { onPointerdown: e => { e.stopPropagation(); if (!track.locked) this.dragClip(e, clip, track, 'out'); } }));
    return node;
  }
  dragClip(event, clip, track, mode) {
    const node = event.currentTarget.closest('.vs-clip'); const startX = event.clientX; const revision = this.project.revision;
    const lease = this.lease([clip.id, track.id]); lease.catch(() => {});
    this.dragging = true;
    let delta = 0; let moved = false; let destination = track.id; node.setPointerCapture(event.pointerId); node.classList.add('dragging');
    const move = e => { delta = Math.round((e.clientX - startX) / this.zoom * TIMEBASE); moved = Math.abs(e.clientX - startX) > 3;
      if (mode === 'move') { let next = Math.max(0, clip.start + delta); if (this.snap) next = snapTime(next, this.project, clip.id, this.playhead, 8 / this.zoom * TIMEBASE); delta = next - clip.start; node.style.left = `${seconds(next) * this.zoom}px`; destination = document.elementFromPoint(e.clientX, e.clientY)?.closest('[data-track-id]')?.dataset.trackId || track.id; }
      else node.style.width = `${Math.max(8, seconds(duration(clip) + delta * (mode === 'in' ? -1 : 1)) * this.zoom)}px`;
    };
    const finish = () => { this.dragging = false; node.removeEventListener('pointermove', move); node.classList.remove('dragging'); if (!moved) return;
      this.guard(async () => { await lease; if (this.project.revision !== revision) throw new Error(this.t('conflict'));
        const command = mode === 'move' ? { type: 'clip.move', clip_id: clip.id, start: Math.max(0, clip.start + delta), track_id: destination }
          : { type: 'clip.trim', clip_id: clip.id, [mode === 'in' ? 'source_in' : 'source_out']: Math.max(0, (mode === 'in' ? clip.source_in : clip.source_out) + Math.round(delta * (clip.speed?.num || 1) / (clip.speed?.den || 1))) };
        await this.command(command); }); };
    node.addEventListener('pointermove', move); node.addEventListener('pointerup', finish, { once: true });
    node.addEventListener('pointercancel', () => { this.dragging = false; node.removeEventListener('pointermove', move); this.paint(); }, { once: true });
  }
  trackDialog(track) {
    this.form(this.t('addTrack'), [{ key: 'name', label: this.t('name'), value: track?.name || 'Video' }, { key: 'kind', label: 'Kind', value: track?.kind || 'video', options: ['video', 'audio', 'adjustment'] }, ...(track ? [{ key: 'volume', label: 'Volume', type: 'number', value: track.volume ?? 1, min: 0 }, { key: 'pan', label: 'Pan', type: 'number', value: track.pan || 0, min: -1, max: 1 }] : [])],
      f => this.command(track ? { type: 'track.update', track_id: track.id, patch: { name: f.name, volume: Number(f.volume), pan: Number(f.pan) } } : { type: 'track.add', name: f.name, kind: f.kind }));
  }
  projectMenu() {
    const dialog = h('dialog.vs-dialog', h('div.vs-dialog-head', h('h2', this.project.name), this.button('×', () => { dialog.close(); dialog.remove(); })));
    const action = (text, fn) => this.button(text, () => { dialog.close(); dialog.remove(); return fn(); });
    dialog.append(h('div.vs-project-actions',
      action(this.t('rename'), () => this.form(this.t('rename'), [{ key: 'name', label: this.t('name'), value: this.project.name }], async f => { await this.command({ type: 'project.rename', name: f.name }); this.projects = listOf(await api.raw(`${BASE}/projects`), 'projects'); this.paint(); })),
      action(this.t('archive'), () => this.command({ type: 'project.archive', archived: !this.project.archived })),
      action(this.t('duplicate'), async () => { const result = await this.command({ type: 'project.duplicate', name: `${this.project.name} — copy` }); this.projects = listOf(await api.raw(`${BASE}/projects`), 'projects'); await this.open(result.project_id); }),
      action(this.lang === 'ru' ? 'Открыть архив' : 'Open archive', async () => { const archived = listOf(await api.raw(`${BASE}/projects?archived=true`), 'projects'); this.form(this.t('open'), [{ key: 'id', label: this.t('project'), options: archived.map(p => ({ value: p.id, label: p.name })) }], f => this.open(f.id)); }),
      action(this.t('history'), () => this.historyDialog()), action(this.t('portable'), async () => {
        const job = await api.raw(`${BASE}/portable`, { method: 'POST', body: { project_id: this.project.id, expected_revision: this.project.revision, operation_id: uid() } }); this.jobs.set(job.job_id, job); this.paint(); this.pollJob(job.job_id);
      }), action('Sequence settings', () => {
        const seq = activeSequence(this.project);
        this.form('Sequence settings', [{ key: 'width', label: 'Width', type: 'number', value: seq.width }, { key: 'height', label: 'Height', type: 'number', value: seq.height }, { key: 'num', label: 'FPS numerator', type: 'number', value: seq.fps.num }, { key: 'den', label: 'FPS denominator', type: 'number', value: seq.fps.den }], f => this.command({ type: 'sequence.settings', patch: { width: Number(f.width), height: Number(f.height), fps: { num: Number(f.num), den: Number(f.den) } } }));
      }), action('Duplicate sequence', () => this.command({ type: 'sequence.duplicate' }))));
    document.body.append(dialog); dialog.addEventListener('cancel', () => dialog.remove()); dialog.showModal();
  }
  titleDialog() {
    const tracks = activeSequence(this.project).tracks.filter(t => t.kind === 'video' && !t.locked);
    if (!tracks.length) throw new Error('Add an unlocked video track first');
    this.form(this.t('title'), [{ key: 'track_id', label: this.t('addTrack'), options: tracks.map(t => ({ value: t.id, label: t.name })) }, { key: 'text', label: this.t('title'), multiline: true, required: true }, { key: 'duration', label: 'Duration, s', type: 'number', value: 3 }, { key: 'size', label: 'Font size', type: 'number', value: 48 }, { key: 'color', label: 'Color', value: '#ffffff' }], f => this.command({ type: 'title.add', track_id: f.track_id, start: this.playhead, duration: ticks(f.duration), title: { text: f.text, size: Number(f.size), color: f.color, font: 'Arial' } }));
  }
  captionsDialog() {
    let cues = structuredClone(this.project.captions || []);
    const dialog = h('dialog.vs-dialog.vs-caption-dialog'); const rows = h('div.vs-caption-rows');
    const draw = () => rows.replaceChildren(...cues.map((cue, index) => h('div.vs-caption-row',
      h('input', { type: 'number', step: '.001', min: 0, value: seconds(cue.start), 'aria-label': `Cue ${index + 1} start`, onChange: e => cue.start = ticks(e.target.value) }),
      h('input', { type: 'number', step: '.001', min: 0, value: seconds(cue.end), 'aria-label': `Cue ${index + 1} end`, onChange: e => cue.end = ticks(e.target.value) }),
      h('textarea', { rows: 2, value: cue.text, 'aria-label': `Cue ${index + 1} text`, onInput: e => cue.text = e.target.value }),
      this.button('×', () => { cues.splice(index, 1); draw(); }))));
    const subtitleTime = value => { const ms = Math.round(value / 1000); return `${String(Math.floor(ms / 3600000)).padStart(2, '0')}:${String(Math.floor(ms / 60000) % 60).padStart(2, '0')}:${String(Math.floor(ms / 1000) % 60).padStart(2, '0')},${String(ms % 1000).padStart(3, '0')}`; };
    const download = ext => {
      const body = cues.map((c, i) => `${i + 1}\n${subtitleTime(c.start)} --> ${subtitleTime(c.end)}\n${c.text}\n`).join('\n');
      const blob = new Blob([ext === 'vtt' ? `WEBVTT\n\n${body.replace(/,(\d{3})/g, '.$1')}` : body], { type: 'text/plain;charset=utf-8' });
      const url = URL.createObjectURL(blob); const link = h('a', { href: url, download: `captions.${ext}` }); link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    };
    dialog.append(h('div.vs-dialog-head', h('h2', this.t('captions')), this.button('×', () => { dialog.close(); dialog.remove(); })),
      h('p', this.lang === 'ru' ? 'Время в секундах. Субтитры вшиваются при preview и экспорте.' : 'Times in seconds. Captions are burned into preview and export.'),
      h('label.vs-button', 'SRT / VTT ↑', h('input', { type: 'file', hidden: true, accept: '.srt,.vtt', onChange: e => this.guard(async () => {
        const file = e.target.files?.[0]; if (!file) return; if (file.size > 2_000_000) throw new Error('Subtitle file exceeds 2 MB');
        const result = await api.raw(`${BASE}/captions/import`, { method: 'POST', body: { project_id: this.project.id, expected_revision: this.project.revision, operation_id: uid(), text: await file.text(), format: file.name.toLowerCase().endsWith('.vtt') ? 'vtt' : 'srt' } });
        this.project = result.project; cues = structuredClone(this.project.captions); draw(); this.paint();
      }) })), rows, this.button('＋ Cue', () => { cues.push({ id: uid().replaceAll('-', ''), start: this.playhead, end: this.playhead + 2 * TIMEBASE, text: '' }); draw(); }),
      this.button('SRT ↓', () => download('srt')), this.button('VTT ↓', () => download('vtt')),
      this.button(this.t('apply'), async () => { await this.command({ type: 'captions.replace', captions: cues }); dialog.close(); dialog.remove(); }, { class: 'vs-primary' }));
    draw(); document.body.append(dialog); dialog.addEventListener('cancel', () => dialog.remove()); dialog.showModal();
  }
  assistant() {
    const panel = h('aside.vs-assistant.vs-panel', h('div.vs-panel-head', h('strong', `✦ ${this.t('assistant')}`), this.button('×', () => { this.agentOpen = false; this.paint(); })),
      h('p.vs-muted', this.t('explain')),
      h('a.vs-chat-link', { href: this.project.links?.task_id ? `#/tasks?id=${encodeURIComponent(this.project.links.task_id)}` : '#/home' }, this.project.links?.task_id ? `↗ Task ${this.project.links.task_id}` : '↗ Bossman Chat'),
      h('label.vs-field', h('span', this.lang === 'ru' ? 'Что изменить в монтаже?' : 'What should change in this edit?'), h('textarea.vs-objective', { rows: 3, maxlength: 2000, value: this.objective || '', placeholder: this.lang === 'ru' ? 'Например: сделай выбранный клип вдвое медленнее' : 'For example: slow the selected clip to half speed', onInput: e => this.objective = e.target.value })),
      h('small.vs-local', `${this.lang === 'ru' ? 'Выбранный клип' : 'Selected clip'}: ${this.selected || '—'}`),
      this.button(this.lang === 'ru' ? 'Предложить монтаж · локальная модель' : 'Draft edit · local model', () => this.requestProposal(), { disabled: this.proposing || !this.selected }),
      this.proposalStatus ? h('p.vs-muted', this.proposalStatus) : null,
      this.draftRaw ? h('details.vs-raw-draft', h('summary', this.lang === 'ru' ? 'Исходный ответ модели' : 'Raw model response'), h('pre', this.draftRaw)) : null,
      h('label.vs-field', h('span', this.t('proposal')), h('textarea.vs-proposal', { rows: 10, spellcheck: false, value: this.proposalText, onInput: e => { this.proposalText = e.target.value; this.review = null; } })),
      this.button(this.t('dry'), () => this.reviewProposal()),
      this.review ? h('div.vs-review', h('strong', `${this.t('current')} r${this.review.baseRevision}`), h('p', `${this.t('changes')}: ${(this.review.result.changed_ids || []).join(', ') || '—'}`),
        ...(this.review.result.warnings || []).map(w => h('p.vs-warning', String(w))),
        this.button(this.t('apply'), () => { if (this.project.revision !== this.review.baseRevision || this.proposalText !== this.review.text) throw new Error(this.t('conflict')); return this.command(this.review.command, false, this.review.operationId); }, { class: 'vs-primary' })) : null,
      this.button(this.t('agentUndo'), () => this.command({ type: 'history.undo' })),
      this.button(this.t('compare'), () => this.historyDialog()), h('small.vs-local', `● ${this.t('local')}`));
    if (this.lastChange.length) panel.append(h('div.vs-changes', ...this.lastChange.map(id => this.button(id.slice(0, 16), () => { if (selectedClip(this.project, id)) { this.selected = id; this.paint(); this.root.querySelector(`[data-clip-id="${CSS.escape(id)}"]`)?.scrollIntoView({ block: 'nearest', inline: 'nearest' }); } }))));
    return panel;
  }
  async reviewProposal() {
    const command = JSON.parse(this.proposalText); const operationId = uid(); const baseRevision = this.project.revision; const text = this.proposalText;
    const result = await this.command(command, true, operationId); this.review = { command, operationId, baseRevision, result, text }; this.paint();
  }
  async requestProposal() {
    const objective = String(this.objective || '').trim(); if (!objective) throw new Error(this.lang === 'ru' ? 'Введите задачу монтажа' : 'Enter an editing objective');
    if (!this.selected) throw new Error(this.t('select'));
    const revision = this.project.revision; const selected = this.selected; const draftText = this.proposalText; this.review = null; this.proposing = true; this.proposalStatus = this.t('busy'); this.paint();
    try {
      const job = await api.raw(`${BASE}/proposals`, { method: 'POST', body: { project_id: this.project.id, expected_revision: revision, operation_id: uid(), objective, clip_id: selected } });
      this.jobs.set(job.job_id, { ...job, action: 'Draft', proposalRequest: { revision, selected, draftText } }); this.pollJob(job.job_id);
    } catch (error) { this.proposing = false; throw error; } finally { this.paint(); }
  }
  async historyDialog() {
    const history = await api.raw(`${BASE}/projects/${encodeURIComponent(this.project.id)}/history`);
    const dialog = h('dialog.vs-dialog.vs-history-dialog', h('div.vs-dialog-head', h('h2', this.t('history')), this.button('×', () => { dialog.close(); dialog.remove(); })));
    const items = listOf(history, 'history', 'revisions');
    const comparison = h('pre');
    dialog.append(h('p', `${this.t('current')}: ${this.project.revision}`), ...items.map(row => h('details', h('summary', `r${row.revision ?? row.after_revision ?? '—'} · ${row.actor || 'human'} · ${row.command?.type || row.operation_id || ''}`), h('pre', JSON.stringify(row, null, 2)),
      this.button(this.t('compare'), async () => { const result = await api.raw(`${BASE}/projects/${this.project.id}/compare?left=${row.revision}&right=${this.project.revision}`); comparison.textContent = JSON.stringify(result, null, 2); }))), comparison);
    document.body.append(dialog); dialog.addEventListener('cancel', () => dialog.remove()); dialog.showModal();
  }
  palette(initial) {
    const selected = selectedClip(this.project, this.selected); const clip = selected?.clip; const track = selected?.track;
    const sequence = activeSequence(this.project); const audioTrack = sequence.tracks.find(t => t.kind === 'audio' && !t.locked);
    const next = track?.clips.find(c => c.id !== clip?.id && c.start === clip.start + duration(clip));
    const examples = [
      { type: 'project.rename', name: this.project.name }, { type: 'track.add', kind: 'video', name: 'Video' },
      { type: 'clip.split', clip_id: this.selected || '', at: this.playhead },
      { type: 'clip.remove', clip_id: this.selected || '', ripple: true },
      { type: 'range.set', start: 0, end: endTime(this.project) },
      { type: 'marker.add', marker: { t: this.playhead, label: '' } }, { type: 'history.undo' },
      ...(clip ? [
        { type: 'clip.copy', clip_id: clip.id, track_id: track.id, start: clip.start + duration(clip) },
        { type: 'clip.trim', clip_id: clip.id, source_in: clip.source_in, source_out: clip.source_out, ripple: true },
        { type: 'clip.roll', clip_id: clip.id, right_clip_id: next?.id || '', delta: 100000 },
        { type: 'clip.speed_ramp', clip_id: clip.id, segments: [{ source_in: clip.source_in, source_out: clip.source_out, speed: { num: 1, den: 1 } }] },
        { type: 'clip.detach_audio', clip_id: clip.id, track_id: audioTrack?.id || '' },
        { type: 'clip.ungroup', clip_ids: [...this.selectedIds] },
        { type: 'effect.copy', source_clip_id: clip.id, target_clip_ids: [...this.selectedIds].filter(id => id !== clip.id) },
        { type: 'transcript.cut', clip_id: clip.id, ranges: [{ start: clip.source_in, end: clip.source_out }], ripple: true },
        { type: 'multicam.sync', method: 'manual', offsets: Object.fromEntries([...this.selectedIds].map(id => [id, selectedClip(this.project, id)?.clip.start || 0])), evidence: { note: 'Owner-supplied manual offsets' } },
        { type: 'track.move', track_id: track.id, index: sequence.tracks.indexOf(track) },
      ] : []),
      { type: 'sequence.add', name: 'Sequence' }, { type: 'sequence.select', sequence_id: sequence.id },
    ];
    const dialog = h('dialog.vs-dialog'); const input = h('textarea.vs-command-json', { rows: 9, spellcheck: false, value: JSON.stringify(initial || examples[0], null, 2) });
    dialog.append(h('div.vs-dialog-head', h('h2', this.t('commands')), this.button('×', () => { dialog.close(); dialog.remove(); })),
      h('div.vs-command-examples', ...examples.map(example => this.button(example.type, () => { input.value = JSON.stringify(example, null, 2); }))), input,
      this.button(this.t('dry'), () => { this.proposalText = input.value; this.agentOpen = true; dialog.close(); dialog.remove(); return this.reviewProposal(); }));
    document.body.append(dialog); dialog.addEventListener('cancel', () => dialog.remove()); dialog.showModal();
  }
  exportDialog(preview) {
    this.form(this.t('export'), [{ key: 'profile', label: 'Profile', value: 'source', options: ['source', 'youtube', 'reels', 'square'] }, { key: 'width', label: 'Width', type: 'number', min: 16, max: 7680, value: activeSequence(this.project).width }, { key: 'height', label: 'Height', type: 'number', min: 16, max: 4320, value: activeSequence(this.project).height }, { key: 'crf', label: 'Quality (CRF)', type: 'number', min: 0, max: 51, value: 20 }],
      f => this.startExport(preview, { profile: f.profile, width: Number(f.width), height: Number(f.height), crf: Number(f.crf) }));
  }
  async startExport(preview, options = {}) {
    const job = await api.raw(`${BASE}/exports`, { method: 'POST', body: { project_id: this.project.id, expected_revision: this.project.revision, operation_id: uid(), preview, options } });
    this.jobs.set(job.job_id, { ...job, preview }); this.paint(); this.pollJob(job.job_id);
  }
  async analyse(mediaId, action) {
    const job = await api.raw(`${BASE}/analysis`, { method: 'POST', body: { project_id: this.project.id, media_id: mediaId, expected_revision: this.project.revision, operation_id: uid(), action, language: this.lang } });
    this.jobs.set(job.job_id, { ...job, action }); this.paint(); this.pollJob(job.job_id);
  }
  async restoreJobs() {
    if (!this.project) return;
    const result = await api.raw(`${BASE}/projects/${encodeURIComponent(this.project.id)}/exports`);
    for (const job of listOf(result, 'jobs')) {
      this.jobs.set(job.job_id || job.id, job);
      if (!['completed', 'failed', 'cancelled', 'stopped', 'unknown'].includes(job.status)) this.pollJob(job.job_id || job.id);
      if (job.status === 'completed' && job.preview && job.output_url) { this.previewUrl = job.output_url; this.previewRevision = job.revision; }
    }
  }
  async pollJob(id) {
    while (!this.disposed) {
      await new Promise(resolve => setTimeout(resolve, 1200)); if (!this.root.isConnected) return;
      try {
        const old = this.jobs.get(id); const result = await api.raw(`${BASE}/exports/${encodeURIComponent(id)}`); const job = { ...old, ...result }; this.jobs.set(id, job);
        if (job.proposalRequest && ['completed', 'failed', 'cancelled', 'stopped', 'unknown'].includes(job.status)) {
          this.proposing = false; const draft = job.proposal;
          this.proposalStatus = draft ? `${draft.model || 'local'} · ${draft.mode || ''}` : job.error || job.status; this.draftRaw = draft?.raw || '';
          if (draft?.valid === true && draft?.applicable === true && draft.command && this.project.revision === job.proposalRequest.revision && this.selected === job.proposalRequest.selected && this.proposalText === job.proposalRequest.draftText) {
            this.proposalText = JSON.stringify(draft.command, null, 2); this.review = null;
          } else this.proposalStatus += ` · ${draft?.validation_error || draft?.error || (draft ? this.t('conflict') : '')}`;
          if (!this.root.querySelector('input:focus,textarea:focus,select:focus')) this.paint();
        }
        if (job.status === 'completed' && job.output_url && job.preview) { this.previewUrl = job.output_url; this.previewRevision = job.revision; this.mediaSelection = null; }
        if (job.status === 'completed' && job.preview && !this.dragging && !this.root.querySelector('input:focus,textarea:focus,select:focus')) this.paint();
        else this.root.querySelector('.vs-jobs')?.replaceWith(this.jobList());
        if (['completed', 'failed', 'cancelled', 'stopped', 'unknown'].includes(job.status)) return;
      } catch (e) { if (this.jobs.get(id)?.proposalRequest) this.proposing = false; this.jobs.set(id, { ...this.jobs.get(id), status: 'unknown', error: e.message }); this.paint(); return; }
    }
  }
  jobList() {
    return h('div.vs-jobs', ...[...this.jobs.values()].map(job => h('div.vs-job', h('div', h('strong', job.action || (job.preview ? 'Preview' : this.t('export'))), h('span', ` · ${job.status}${job.status === 'running' && (job.progress?.stage || job.stage) ? ` / ${job.progress?.stage || job.stage}` : ''}`)),
      job.progress?.details?.out_time_us ? h('small', `Rendered ${seconds(Number(job.progress.details.out_time_us)).toFixed(2)} s`) : null,
      job.analysis ? h('details', h('summary', 'Analysis'), h('pre', JSON.stringify(job.analysis, null, 2))) : null,
      job.progress !== undefined && Number.isFinite(job.progress) ? h('progress', { max: 1, value: job.progress, 'aria-label': 'Render progress' }) : null,
      job.error ? h('small.vs-warning', String(job.error)) : null,
      job.output_url ? h('a', { href: job.output_url, download: '' }, `↓ ${this.t('download')}`) : null,
      job.verification ? h('details', h('summary', this.t('verified')), h('pre', JSON.stringify(job.verification, null, 2))) : null,
      !['completed', 'failed', 'cancelled', 'stopped', 'unknown'].includes(job.status) ? this.button(this.t('cancel'), async () => { const result = await api.raw(`${BASE}/exports/${encodeURIComponent(job.job_id)}/cancel`, { method: 'POST' }); this.jobs.set(job.job_id, { ...job, ...result }); this.paint(); }) : null)));
  }
  footer() {
    return h('footer.vs-footer', h('span', `● ${this.t('local')}`), h('span', this.busy ? this.t('busy') : `${this.t('saved')} · r${this.project.revision}`),
      this.remoteChange ? this.button(this.t('refresh'), async () => { this.remoteChange = false; await this.refresh(); }) : null,
      h('span.vs-spacer'), this.button(this.t('history'), () => this.historyDialog()), this.button(this.t('reset'), () => { this.layout = { library: 238, inspector: 266, timeline: 284 }; preference('layout', this.layout); this.paint(); }));
  }
  async lease(ids) {
    if (!this.project) return;
    await api.raw(`${BASE}/projects/${encodeURIComponent(this.project.id)}/lease`, { method: 'POST', body: { object_ids: ids, ttl_seconds: 30 } });
    clearTimeout(this.leaseTimer);
    this.leaseTimer = setTimeout(() => {
      if (!this.disposed && this.root.isConnected && (this.dragging || this.root.querySelector('.vs-inspector input:focus'))) this.guard(() => this.lease(ids));
    }, 15000);
  }
  keyboard(event) {
    if (event.target.closest('input,textarea,select') || document.querySelector('dialog[open]')) return;
    const mod = event.ctrlKey || event.metaKey;
    if (mod && event.key.toLowerCase() === 'z') { event.preventDefault(); this.guard(() => this.command({ type: event.shiftKey ? 'history.redo' : 'history.undo' })); }
    else if (mod && event.key.toLowerCase() === 'k') { event.preventDefault(); this.palette(); }
    else if (event.code === 'Space') { event.preventDefault(); this.guard(() => this.togglePlay()); }
    else if (event.key.toLowerCase() === 's' && this.selected) { event.preventDefault(); this.guard(() => this.command({ type: 'clip.split', clip_id: this.selected, at: this.playhead })); }
    else if (event.key === 'Delete' && this.selected) { event.preventDefault(); this.guard(() => this.command({ type: 'clip.remove', clip_id: this.selected, ripple: event.shiftKey })); }
  }
}

export const VideoStudioPage = {
  id: 'video-studio', title: 'Video Studio', icon: 'film', nav: 'primary',
  async render(ctx, params = {}) { if (editor) { editor.disposed = true; clearTimeout(editor.leaseTimer); clearTimeout(editor.timelineTimer); } editor = new Editor(ctx, params); return editor.load(); },
  onEvent(event) { if (String(event.kind || '').startsWith('video.') && editor?.root.isConnected) editor.guard(() => editor.refresh()); return false; },
};
export default VideoStudioPage;
