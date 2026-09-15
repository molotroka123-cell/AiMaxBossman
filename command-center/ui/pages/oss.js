import { api } from '../api.js';
import { h, toastError, textarea, select, input, field } from '../components.js';
import { pageHead, panel, pill } from './_ui.js';
import { errorBanner } from './_shared.js';

const STATES = {
  integrated: 'Подключено в приложении', installed: 'Пакет установлен',
  configured: 'Настроено · запуск не проверен', needs_setup: 'Нужна настройка',
  invalid_configuration: 'Исправьте настройки подключения',
};

export default {
  id: 'oss', title: 'Локальные инструменты', icon: 'system', nav: 'more', section: 'system',
  async render(ctx) {
    let data;
    try { data = await api.raw('/api/oss/status'); }
    catch (error) { return h('div.stack', pageHead('Локальные инструменты'), errorBanner(error, ctx)); }
    const cards = data.items.map(item => panel(item.name, h('div.stack',
      pill(STATES[item.state] || 'Нужна проверка'), h('p', item.detail),
      h('div.row',
        item.page ? h('a.btn', { href: `#/${item.page}` }, 'Открыть') : null,
        h('a.btn', { href: item.upstream, target: '_blank', rel: 'noopener noreferrer' }, 'Исходный код')))));
    const output = textarea({ rows: 8, readOnly: true, placeholder: 'Здесь появится расшифровка' });
    const file = h('input', { type: 'file', accept: '.wav,audio/wav', 'aria-label': 'Запись WAV' });
    const language = select([{value:'auto',label:'Определить язык'}, {value:'ru',label:'Русский'},
      {value:'en',label:'English'}, {value:'cs',label:'Čeština'}], { 'aria-label': 'Язык записи' });
    const message = h('p', { role: 'status', 'aria-live': 'polite' });
    const button = h('button.btn.btn-primary', { type: 'button', onClick: async () => {
      const audio = file.files?.[0];
      if (!audio) { message.textContent = 'Выберите WAV-файл.'; return; }
      if (audio.size > 32 * 1024 * 1024) { message.textContent = 'Максимальный размер — 32 МиБ.'; return; }
      button.disabled = true; output.value = ''; message.textContent = 'Расшифровываю запись…';
      try {
        const result = await api.raw(`/api/oss/speech/transcribe?language=${encodeURIComponent(language.value)}`,
          { method: 'POST', body: audio });
        output.value = result.text;
        message.textContent = result.text ? 'Расшифровка готова.' : 'Речь в записи не найдена.';
      } catch (error) { message.textContent = error.message; toastError(error); }
      finally { button.disabled = false; }
    } }, 'Расшифровать');
    const documentPath = input({ placeholder: 'C:\\Documents\\report.pdf', 'aria-label': 'Путь к документу' });
    const documentText = textarea({ rows: 8, readOnly: true, placeholder: 'Текст документа' });
    const documentMessage = h('p', { role: 'status', 'aria-live': 'polite' });
    const extract = h('button.btn', { type: 'button', onClick: async () => {
      if (!documentPath.value.trim()) { documentMessage.textContent = 'Укажите путь к документу.'; return; }
      extract.disabled = true; documentText.value = ''; documentMessage.textContent = 'Читаю документ…';
      try {
        const result = await api.raw('/api/file-intelligence/extract',
          { method: 'POST', body: { path: documentPath.value.trim() } });
        if (!result.ok) throw new Error(result.message || result.reason || result.refused || 'Не удалось прочитать документ');
        documentText.value = result.markdown;
        documentMessage.textContent = result.truncated ? 'Показана часть текста: достигнут лимит.' : 'Документ прочитан.';
      } catch (error) { documentMessage.textContent = error.message; toastError(error); }
      finally { extract.disabled = false; }
    } }, 'Прочитать документ');
    let memoryConfig = {};
    try { memoryConfig = await api.raw('/api/memory/config'); } catch { /* form remains usable */ }
    const memoryRoot = input({ value: memoryConfig.root || '', placeholder: 'C:\\Documents\\Notes' });
    const embeddingUrl = input({ value: memoryConfig.qdrant?.endpoint || 'http://127.0.0.1:8081/v1/embeddings' });
    const embeddingModel = input({ value: memoryConfig.qdrant?.model || '', placeholder: 'Имя загруженной модели' });
    const embeddingDimensions = input({ type: 'number', min: 1, max: 4096, value: memoryConfig.qdrant?.dimensions || 768 });
    const memoryMessage = h('p', { role: 'status', 'aria-live': 'polite' });
    const memoryOutput = textarea({ rows: 7, readOnly: true, placeholder: 'Найденные заметки' });
    const query = input({ placeholder: 'Что найти в заметках?' });
    const memoryAction = (label, operation) => {
      const control = h('button.btn', { type: 'button', onClick: async () => {
        control.disabled = true; memoryMessage.textContent = 'Выполняю…';
        try { await operation(); }
        catch (error) { memoryMessage.textContent = error.message; toastError(error); }
        finally { control.disabled = false; }
      } }, label);
      return control;
    };
    const saveMemory = async backend => {
      if (!memoryRoot.value.trim()) { memoryMessage.textContent = 'Укажите папку с заметками.'; return; }
      memoryConfig = await api.raw('/api/memory/config', { method: 'POST', body: {
        ...memoryConfig, root: memoryRoot.value.trim(), backend,
        qdrant: backend === 'qdrant' ? { endpoint: embeddingUrl.value.trim(), model: embeddingModel.value.trim(),
          dimensions: Number(embeddingDimensions.value), max_chunks: 2000 } : undefined,
      } });
      memoryMessage.textContent = 'Настройки сохранены. Обновите индекс перед поиском.';
    };
    return h('div.stack.lg', pageHead('Локальные инструменты',
      'Готовые движки для задач Bossman. Настройка подключения ещё не подтверждает выполнение задачи.'),
      ...cards,
      panel('Документ → текст', h('div.stack',
        h('p', 'PDF с текстом, DOCX, PPTX, XLSX и CSV из разрешённых папок. До 20 МиБ. Распознавание сканов здесь не включено.'),
        field('Путь к файлу на компьютере Bossman', documentPath), extract, documentMessage, documentText)),
      panel('Поиск по заметкам', h('div.stack',
        h('p', 'Qdrant — дополнительный режим до 2000 фрагментов. Нужна локальная модель эмбеддингов. Обычный поиск работает без неё.'),
        field('Папка с заметками', memoryRoot), field('Адрес модели эмбеддингов', embeddingUrl),
        field('Модель', embeddingModel), field('Размерность', embeddingDimensions),
        h('div.row', memoryAction('Включить Qdrant', () => saveMemory('qdrant')),
          memoryAction('Использовать обычный поиск', () => saveMemory('sqlite')),
          memoryAction('Обновить индекс', async () => {
            if (!memoryRoot.value.trim()) {
              memoryMessage.textContent = 'Сначала укажите папку с заметками и сохраните настройки.';
              return;
            }
            const result = await api.raw('/api/memory/index', { method: 'POST', body: {} });
            memoryMessage.textContent = result.result?.error || 'Индекс обновлён.';
          })),
        field('Запрос', query), memoryAction('Найти в заметках', async () => {
          if (!query.value.trim()) { memoryMessage.textContent = 'Введите запрос.'; return; }
          memoryOutput.value = '';
          const result = await api.raw('/api/memory/search', { method: 'POST', body: { query: query.value.trim() } });
          memoryOutput.value = result.items.map(item => `${item.source}\n${item.content}`).join('\n\n');
          memoryMessage.textContent = result.items.length ? `Найдено фрагментов: ${result.items.length}.` : 'Ничего не найдено.';
        }), memoryMessage, memoryOutput)),
      panel('Запись → текст', h('div.stack',
        h('p', 'WAV PCM16, до 10 минут и 32 МиБ. Обработка на этом компьютере.'),
        file, language, button, message, output)));
  },
};
