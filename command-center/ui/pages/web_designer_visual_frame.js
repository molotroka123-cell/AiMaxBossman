/* Trusted editor shell inside an opaque-origin iframe. No API, credentials,
   telemetry, localStorage, remote dependencies or second project store. */
(() => {
  'use strict';
  if (window.parent === window) return;
  const nonce = location.hash.slice(1);
  const hostOrigin = new URL(document.currentScript.src).origin;
  let editor = null;
  let initialized = false;
  const reply = (type, data = {}) => parent.postMessage({source: 'bossman-visual', nonce, type, ...data}, hostOrigin);
  const block = (id, label, content) => ({id, label, content});

  addEventListener('message', async (event) => {
    const data = event.data;
    if (event.source !== parent || event.origin !== hostOrigin || !nonce
      || data?.source !== 'bossman-visual-host' || data.nonce !== nonce) return;
    try {
      if (data.type === 'init' && !initialized) {
        initialized = true;
        const site = data.site;
        editor = grapesjs.init({
          container: '#editor', height: '100%', width: 'auto',
          i18n: {locale: 'ru', detectLocale: false, messages: {ru: window.bossmanGrapesRu}},
          storageManager: false, telemetry: false, noticeOnUnload: false, cssIcons: '',
          protectedCss: '',
          allowScripts: false, jsInHtml: false,
          parser: {optionsHtml: {allowScripts: false, allowUnsafeAttr: false, allowUnsafeAttrValue: false}},
          components: site.body, style: site.css, panels: {defaults: []},
          deviceManager: {devices: [
            {name: 'Компьютер', width: ''}, {name: 'Планшет', width: '768px', widthMedia: '992px'},
            {name: 'Телефон', width: '390px', widthMedia: '480px'},
          ]},
          blockManager: {appendTo: '#blocks', blocks: [
            block('section', 'Секция', '<section style="padding:40px 24px"><h2>Новая секция</h2><p>Расскажите о вашем проекте.</p></section>'),
            block('columns', 'Две колонки', '<div style="display:flex;gap:24px;flex-wrap:wrap;padding:24px"><div style="flex:1;min-width:180px"><h3>Первая колонка</h3><p>Описание</p></div><div style="flex:1;min-width:180px"><h3>Вторая колонка</h3><p>Описание</p></div></div>'),
            block('heading', 'Заголовок', '<h2>Новый заголовок</h2>'),
            block('text', 'Текст', '<p>Новый текст</p>'),
            block('button', 'Кнопка-ссылка', '<a href="#" style="display:inline-block;padding:12px 24px;border-radius:8px;background:#3568d4;color:white">Подробнее</a>'),
            block('image', 'Изображение', {type: 'image'}),
          ]},
          layerManager: {appendTo: '#layers'}, traitManager: {appendTo: '#traits'},
          selectorManager: {componentFirst: true},
          styleManager: {appendTo: '#styles', sectors: [
            {name: 'Размер и положение', open: true, buildProps: ['display','width','height','max-width','margin','padding']},
            {name: 'Текст', open: true, buildProps: ['font-family','font-size','font-weight','line-height','color','text-align']},
            {name: 'Оформление', buildProps: ['background-color','border','border-radius','box-shadow','opacity']},
          ]},
          assetManager: {upload: false, autoAdd: false},
        });
        editor.on('load', () => {
          // Original CSS is display-only. Bossman keeps its exact source; new
          // style rules override it without passing it through a CSS serializer.
          const doc = editor.Canvas.getDocument();
          const style = doc.createElement('style');
          style.textContent = site.base_css || '';
          doc.head.prepend(style);
          editor.getWrapper().addAttributes(site.body_attributes || {});
          editor.clearDirtyCount();
          editor.UndoManager.clear();
          editor.on('update', () => reply('changed'));
          reply('loaded');
        });
      } else if (data.type === 'snapshot' && editor) {
        reply('snapshot', {requestId: data.requestId,
          body: editor.getWrapper().components().map(component => component.toHTML()).join(''),
          body_attributes: editor.getWrapper().getAttributes(), css: editor.getCss() || ''});
      }
    } catch (error) { reply('error', {message: String(error.message || error)}); }
  });
  document.querySelector('select').addEventListener('change', e => editor?.setDevice(e.target.value));
  document.querySelectorAll('[data-command]').forEach(button => button.addEventListener('click', () => {
    if (!editor) return;
    if (button.dataset.command === 'undo') editor.UndoManager.undo(); else editor.UndoManager.redo();
  }));
  reply('ready');
})();
