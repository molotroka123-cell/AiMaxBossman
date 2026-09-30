/* ============================================================
   chat/markdown.js — безопасный markdown для ответов Bossman.

   Чистая логика: при импорте модуль не трогает DOM, поэтому его разбор
   проверяется в node (ui/tests/chat_core.test.mjs).

   Текст модели — ДАННЫЕ, а не разметка. Сырой HTML не поддерживается вовсе:
   `<script>` из ответа остаётся буквами на экране. Разбор даёт дерево, а
   узлы строит «фабрика»: в браузере — document.createElement + textContent
   (render.js, без innerHTML), в тестах — строки с экранированием
   (htmlFactory). Путь один и тот же, поэтому тест проверяет то, что
   видит владелец.

   Поддержано: абзацы, заголовки, списки (в т.ч. вложенные), цитаты,
   блоки кода ``` (незакрытый блок во время стриминга — тоже код), `код`,
   **жирный**, *курсив*, [ссылки](https://…) и голые https://-адреса.
   Ссылки — только http(s) и mailto, всегда target=_blank и
   rel="noopener noreferrer".

   Живой ответ (render.js) рисуется «замороженным префиксом»: stableCut()
   находит границу, до которой текст уже не изменится при дописывании,
   готовые блоки добавляются один раз, заново рисуется только хвост.
   healTail() чинит ТОЛЬКО этот хвост и только пока ответ не окончен:
   недописанная ссылка и недописанный адрес не становятся живой ссылкой на
   чужой обрезанный домен. Окончательный текст и история не «чинятся».
   ============================================================ */

/** Сколько символов вперёд искать закрывающий маркер выделения: длинные ответы
 *  с одиночными `*` иначе давали бы квадратичный разбор на каждом кадре. */
const EMPHASIS_WINDOW = 1000;
const MAX_DEPTH = 6;

const VOID_TAGS = new Set(['br', 'hr']);

export function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** Ссылка, которую можно открыть: только http(s) и mailto, без пробелов и управляющих символов. */
export function safeHref(url) {
  const u = String(url ?? '').trim();
  if (!u || u.length > 2000) return null;
  for (let i = 0; i < u.length; i += 1) {
    const code = u.charCodeAt(i);
    if (code <= 0x20 || code === 0x7f) return null;
  }
  if (/^https?:\/\/[^/]/i.test(u) || /^mailto:[^/]/i.test(u)) return u;
  return null;
}

/* ---------------------------------------------------------------- блоки */

const FENCE = /^ {0,3}(`{3,}|~{3,})\s*([\w+#.-]*)[^`]*$/;
const HEADING = /^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const HR = /^ {0,3}([-*_])(?:\s*\1){2,}\s*$/;
const QUOTE = /^ {0,3}>/;
const LIST_ITEM = /^( {0,12})([-*+]|\d{1,9}[.)])\s+(.*)$/;
const BLANK = /^\s*$/;

function isBlockStart(line) {
  return FENCE.test(line) || HEADING.test(line) || HR.test(line) || QUOTE.test(line)
    || LIST_ITEM.test(line);
}

function indentOf(line) {
  const m = /^( *)/.exec(line);
  return m ? m[1].length : 0;
}

function dedent(line, width) {
  let i = 0;
  while (i < width && line[i] === ' ') i += 1;
  return line.slice(i);
}

function splitLines(src) {
  return String(src ?? '').replace(/\r\n?/g, '\n').replace(/\t/g, '    ').split('\n');
}

/**
 * Один шаг разбора верхнего уровня: какой блок начинается со строки i и где
 * он кончается (`end` — индекс строки после блока). Этот же шаг использует
 * stableCut(), поэтому граница замороженного префикса совпадает с тем, как
 * parseBlocks режет текст (вложенный в пункт списка ``` — часть пункта, а не
 * блок кода верхнего уровня).
 */
function scanBlock(lines, i) {
  const line = lines[i];
  const fence = FENCE.exec(line);
  if (fence) {
    const marker = fence[1];
    const body = [];
    let closed = false;
    let j = i + 1;
    while (j < lines.length) {
      const t = lines[j].trim();
      if (t.length >= marker.length && t === marker[0].repeat(t.length)) { closed = true; j += 1; break; }
      body.push(lines[j]);
      j += 1;
    }
    return { type: 'code', end: j, lang: fence[2] || '', body, closed };
  }
  if (BLANK.test(line)) return { type: 'blank', end: i + 1 };
  const heading = HEADING.exec(line);
  if (heading) return { type: 'heading', end: i + 1, level: heading[1].length, text: heading[2] };
  if (HR.test(line)) return { type: 'hr', end: i + 1 };
  if (QUOTE.test(line)) {
    const inner = [];
    let j = i;
    while (j < lines.length && QUOTE.test(lines[j])) {
      inner.push(lines[j].replace(/^ {0,3}> ?/, ''));
      j += 1;
    }
    return { type: 'quote', end: j, inner };
  }
  const item = LIST_ITEM.exec(line);
  if (item && indentOf(line) <= 3) {
    const ordered = /\d/.test(item[2]);
    const start = ordered ? parseInt(item[2], 10) : 1;
    const items = [];
    let current = null;
    let sawBlank = false;
    let j = i;
    while (j < lines.length) {
      const l = lines[j];
      const m = LIST_ITEM.exec(l);
      /* пункт с отступом до колонки текста текущего пункта — вложенный, а не соседний */
      if (m && indentOf(l) <= 3 && !(current && indentOf(l) >= current.width)) {
        if (/\d/.test(m[2]) !== ordered) break;
        current = { lines: [m[3]], width: m[1].length + m[2].length + 1 };
        items.push(current);
        sawBlank = false;
        j += 1;
        continue;
      }
      if (BLANK.test(l)) {
        /* пустая строка: список продолжается, только если дальше снова пункт или отступ */
        const next = lines[j + 1];
        if (next === undefined || !(LIST_ITEM.test(next) || indentOf(next) >= 2)) break;
        sawBlank = true;
        current.lines.push('');
        j += 1;
        continue;
      }
      if (indentOf(l) >= 2) { current.lines.push(dedent(l, Math.max(2, current.width))); j += 1; continue; }
      if (sawBlank || isBlockStart(l)) break;
      current.lines.push(l.trim());      /* ленивое продолжение абзаца пункта */
      j += 1;
    }
    return { type: 'list', end: j, ordered, start, items };
  }
  const para = [line.trim()];
  let j = i + 1;
  while (j < lines.length && !BLANK.test(lines[j]) && !isBlockStart(lines[j])) {
    para.push(lines[j].trim());
    j += 1;
  }
  return { type: 'paragraph', end: j, text: para.join('\n') };
}

/** Текст → список блоков {type: paragraph|heading|code|list|quote|hr}. */
export function parseBlocks(src, depth = 0) {
  const lines = splitLines(src);
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const b = scanBlock(lines, i);
    i = b.end;
    if (b.type === 'code') blocks.push({ type: 'code', lang: b.lang, text: b.body.join('\n'), open: !b.closed });
    else if (b.type === 'heading') blocks.push({ type: 'heading', level: b.level, text: b.text });
    else if (b.type === 'hr') blocks.push({ type: 'hr' });
    else if (b.type === 'quote') {
      blocks.push({ type: 'quote', children: depth < MAX_DEPTH ? parseBlocks(b.inner.join('\n'), depth + 1)
        : [{ type: 'paragraph', text: b.inner.join('\n') }] });
    } else if (b.type === 'list') {
      blocks.push({
        type: 'list', ordered: b.ordered, start: b.start,
        items: b.items.map((it) => ({
          loose: it.lines.slice(0, -1).includes(''),
          children: depth < MAX_DEPTH ? parseBlocks(it.lines.join('\n'), depth + 1)
            : [{ type: 'paragraph', text: it.lines.join('\n') }],
        })),
      });
    } else if (b.type === 'paragraph') blocks.push({ type: 'paragraph', text: b.text });
  }
  return blocks;
}

/* ---------------------------------------------------------------- живой ответ: замороженный префикс */

/**
 * Смещение, до которого текст уже не изменится при дописывании (P1).
 *
 * Граница — начало строки сразу после пустой строки вне блока кода, если эта
 * строка начинается с колонки 0, не пункт списка и не цитата (иначе
 * дописанный текст мог бы продолжить список или цитату), и сама строка уже
 * закончена переводом строки. Разбор ведёт тот же scanBlock, что и
 * parseBlocks, поэтому render(text[0:cut]) + render(text[cut:]) ===
 * render(text) — это свойство проверяется в node на случайных нарезках.
 *
 * `from` — прежняя граница (всегда начало строки): просмотр идёт только по
 * хвосту, результат >= from и совпадает с stableCut(text) от нуля.
 */
export function stableCut(text, from = 0) {
  const src = String(text ?? '');
  const base = Math.max(0, Math.min(Math.floor(Number(from) || 0), src.length));
  const lines = [];
  const offsets = [];
  const sep = /\r\n|\r|\n/g;
  sep.lastIndex = base;
  let at = base;
  for (let m = sep.exec(src); m; m = sep.exec(src)) {
    offsets.push(at);
    lines.push(src.slice(at, m.index).replace(/\t/g, '    '));
    at = m.index + m[0].length;
  }
  offsets.push(at);
  lines.push(src.slice(at).replace(/\t/g, '    '));
  let cut = base;
  let i = 0;
  /* последняя строка ещё дописывается: границу перед ней не ставим */
  while (i < lines.length - 1) {
    if (i > 0 && BLANK.test(lines[i - 1]) && /^\S/.test(lines[i]) && !LIST_ITEM.test(lines[i]) && !QUOTE.test(lines[i])) {
      cut = offsets[i];
    }
    i = scanBlock(lines, i).end;
  }
  return cut;
}

function lastLeaf(blocks) {
  let b = blocks[blocks.length - 1] || null;
  while (b) {
    if (b.type === 'list') {
      const it = b.items[b.items.length - 1];
      b = it ? it.children[it.children.length - 1] || null : null;
    } else if (b.type === 'quote') {
      b = b.children[b.children.length - 1] || null;
    } else {
      return b;
    }
  }
  return null;
}

/** Незакрытый `код` в строке абзаца: те же правила пар, что у parseInline. */
function openCodeSpan(text) {
  let i = 0;
  while (i < text.length) {
    const ch = text[i];
    if (ch === '\\' && ESCAPABLE.test(text[i + 1] || '')) { i += 2; continue; }
    if (ch === '`') {
      let n = 1;
      while (text[i + n] === '`') n += 1;
      const end = text.indexOf('`'.repeat(n), i + n);
      if (end === -1) return { at: i, ticks: n };
      i = end + n;
      continue;
    }
    i += 1;
  }
  return null;
}

/* недописанная ссылка в самом конце: [подпись](адрес-ещё-пишется */
const PARTIAL_LINK = /(?<!\\)\[([^\]\n]{1,500})\]\([^\S\n]*[^()\s]*(?:[^\S\n]+"[^"\n]*"?)?[^\S\n]*$/;
/* голый адрес в самом конце, после него ещё нет пробела */
const TRAILING_URL = /(^|[^\p{L}\p{N}])(https?):(\/\/[^\s<>"'`]+)$/iu;
/* готовая ссылка [подпись](адрес) — те же правила, что у LINK в parseInline */
const DONE_LINK = /(?<!\\)\[[^\]\n]{1,500}\]\(\s*[^()\s]{1,2000}(?:\s+"[^"\n]*")?\s*\)/g;

/** Где кончается последняя готовая ссылка: её адрес уже дописан и голым не считается. */
function doneLinksEnd(text) {
  let end = 0;
  DONE_LINK.lastIndex = 0;
  for (let m = DONE_LINK.exec(text); m; m = DONE_LINK.exec(text)) end = m.index + m[0].length;
  return end;
}

/**
 * Хвост живого ответа без ложных ссылок (P2). Вызывается ТОЛЬКО для
 * незамороженного хвоста и только пока ответ не окончен.
 *
 * - внутри блока кода ничего не меняется;
 * - незакрытый `код` в последнем абзаце закрывается (иначе мигают обратные
 *   кавычки);
 * - `[подпись](недописанный-адрес` → просто подпись;
 * - голый адрес без пробела после него — обычный текст (двоеточие
 *   экранировано), пока адрес не дописан.
 * `**`, `*`, `_`, `~~` не закрываются никогда: `2**10 = 1024`, `a * b` и
 * `snake_case` остаются как есть.
 */
export function healTail(text) {
  const src = String(text ?? '');
  const lastLine = src.slice(Math.max(src.lastIndexOf('\n'), src.lastIndexOf('\r')) + 1);
  if (!src || BLANK.test(lastLine)) return src;          // текущая строка закончена — чинить нечего
  const mayTick = src.includes('`');
  const link = PARTIAL_LINK.exec(src);
  const url = TRAILING_URL.exec(src);
  if (!mayTick && !link && !url) return src;
  const leaf = lastLeaf(parseBlocks(src));
  if (!leaf || leaf.type === 'code' || typeof leaf.text !== 'string') return src;
  if (mayTick) {
    const open = openCodeSpan(leaf.text);
    if (open) {
      const after = leaf.text.slice(open.at + open.ticks);
      if (!/\S/.test(after)) return src;
      const trailing = /`*$/.exec(src)[0].length;
      return trailing < open.ticks ? src + '`'.repeat(open.ticks - trailing) : src;
    }
  }
  let out = src;
  const partial = PARTIAL_LINK.exec(out);
  if (partial) out = out.slice(0, partial.index) + partial[1];
  if (TRAILING_URL.test(out)) {
    /* готовая ссылка в конце (`[docs](https://a.com)`, `[https://a.com](https://a.com).`) —
       не голый адрес: ищем только после неё, иначе ссылка не жила бы до следующего текста,
       а подпись-адрес показала бы «https\://» */
    const from = doneLinksEnd(out);
    const bare = TRAILING_URL.exec(out.slice(from));
    if (bare) {
      /* и вложенные адреса того же слова (…?u=https://b) — иначе ссылкой стал бы обрывок */
      const rest = bare[3].replace(/(https?):\/\//gi, '$1\\://');
      out = `${out.slice(0, from + bare.index)}${bare[1]}${bare[2]}\\:${rest}`;
    }
  }
  return out;
}

/**
 * Чистый план перерисовки одного текстового сегмента (P1 + P2), без DOM.
 *
 * prev — {frozenAt, frozenText} прошлой отрисовки или null. Возвращает
 * {reset, append, tail, frozenAt, frozenText}: reset — очистить узел
 * (первая отрисовка или текст заменён, например правдой сервера или
 * повтором шага); append — markdown готовых блоков, которые добавить один
 * раз; tail — markdown хвоста, который перерисовать. Окончательный текст не
 * режется и не «чинится»: хвост рисуется как есть.
 */
export function planTextRender(prev, text, final) {
  const src = String(text ?? '');
  let reset = !prev;
  let frozenAt = prev ? prev.frozenAt : 0;
  let frozenText = prev ? prev.frozenText : '';
  if (!src.startsWith(frozenText)) {
    reset = true;
    frozenAt = 0;
    frozenText = '';
  }
  let append = null;
  if (!final) {
    const cut = stableCut(src, frozenAt);
    if (cut > frozenAt) {
      append = src.slice(frozenAt, cut);
      frozenAt = cut;
      frozenText = src.slice(0, cut);
    }
  }
  const rest = src.slice(frozenAt);
  return { reset, append, tail: final ? rest : healTail(rest), frozenAt, frozenText };
}

/* ---------------------------------------------------------------- строки */

/* ':' — как в CommonMark (любая ASCII-пунктуация экранируется); нужен healTail */
const ESCAPABLE = /[\\`*_[\]()#+\-.!>~|{}:]/;
const AUTOLINK = /^https?:\/\/[^\s<>"'`]+/i;
const LINK = /^\[([^\]\n]{1,500})\]\(\s*([^()\s]{1,2000})(?:\s+"[^"\n]*")?\s*\)/;

function isWordChar(ch) {
  return Boolean(ch) && /[\p{L}\p{N}]/u.test(ch);
}

function findClosing(text, from, marker) {
  const limit = Math.min(text.length, from + EMPHASIS_WINDOW);
  for (let j = from; j < limit; j += 1) {
    if (text[j] === '\n' && text[j + 1] === '\n') return -1;
    if (text.startsWith(marker, j) && text[j - 1] !== ' ' && text[j - 1] !== '\\') {
      if (marker.length === 1 && text[j + 1] === marker) { j += 1; continue; }
      if (marker === '_' && isWordChar(text[j + 1])) continue;
      return j;
    }
  }
  return -1;
}

/** Строка абзаца → узлы {type: text|code|strong|em|link|br}. */
export function parseInline(text, depth = 0) {
  const src = String(text ?? '');
  const out = [];
  let buf = '';
  const flush = () => { if (buf) { out.push({ type: 'text', text: buf }); buf = ''; } };
  let i = 0;
  while (i < src.length) {
    const ch = src[i];
    if (ch === '\\' && ESCAPABLE.test(src[i + 1] || '')) { buf += src[i + 1]; i += 2; continue; }
    if (ch === '\n') { flush(); out.push({ type: 'br' }); i += 1; continue; }
    if (ch === '`') {
      let n = 1;
      while (src[i + n] === '`') n += 1;
      const ticks = '`'.repeat(n);
      const end = src.indexOf(ticks, i + n);
      if (end !== -1) {
        flush();
        out.push({ type: 'code', text: src.slice(i + n, end).replace(/^ (.+) $/s, '$1') });
        i = end + n;
        continue;
      }
      buf += ticks;
      i += n;
      continue;
    }
    if (ch === '[') {
      const m = LINK.exec(src.slice(i, i + 2600));
      if (m) {
        const href = safeHref(m[2]);
        flush();
        if (href) out.push({ type: 'link', href, children: depth < MAX_DEPTH ? parseInline(m[1], depth + 1) : [{ type: 'text', text: m[1] }] });
        else out.push({ type: 'text', text: m[1] });
        i += m[0].length;
        continue;
      }
    }
    if ((ch === 'h' || ch === 'H') && !isWordChar(src[i - 1])) {
      const m = AUTOLINK.exec(src.slice(i, i + 2100));
      if (m) {
        let url = m[0];
        while (/[.,;:!?)\]]$/.test(url)) url = url.slice(0, -1);
        const href = safeHref(url);
        if (href) {
          flush();
          out.push({ type: 'link', href, children: [{ type: 'text', text: url }] });
          i += url.length;
          continue;
        }
      }
    }
    if ((ch === '*' || ch === '_') && depth < MAX_DEPTH) {
      const double = src[i + 1] === ch;
      const marker = double ? ch + ch : ch;
      const open = i + marker.length;
      const leftOk = ch === '*' || !isWordChar(src[i - 1]);
      if (leftOk && src[open] && src[open] !== ' ' && src[open] !== '\n') {
        /* закрывающий маркер ищем со второго символа: выделение не бывает пустым */
        const end = findClosing(src, open + 1, marker);
        if (end !== -1) {
          flush();
          out.push({ type: double ? 'strong' : 'em', children: parseInline(src.slice(open, end), depth + 1) });
          i = end + marker.length;
          continue;
        }
      }
    }
    buf += ch;
    i += 1;
  }
  flush();
  return out;
}

/* ---------------------------------------------------------------- отрисовка через фабрику */

function inlineNodes(nodes, f) {
  const out = [];
  for (const n of nodes) {
    if (n.type === 'text') out.push(f.text(n.text));
    else if (n.type === 'br') out.push(f.el('br', {}, []));
    else if (n.type === 'code') out.push(f.el('code', { class: 'md-inline-code' }, [f.text(n.text)]));
    else if (n.type === 'strong') out.push(f.el('strong', {}, inlineNodes(n.children, f)));
    else if (n.type === 'em') out.push(f.el('em', {}, inlineNodes(n.children, f)));
    else if (n.type === 'link') {
      out.push(f.el('a', { href: n.href, target: '_blank', rel: 'noopener noreferrer' }, inlineNodes(n.children, f)));
    }
  }
  return out;
}

function blockNodes(blocks, f, tight = false) {
  const out = [];
  for (const b of blocks) {
    if (b.type === 'paragraph') {
      const kids = inlineNodes(parseInline(b.text), f);
      out.push(...(tight ? kids : [f.el('p', {}, kids)]));
    } else if (b.type === 'heading') {
      const tag = `h${Math.min(6, b.level + 2)}`;
      out.push(f.el(tag, { class: 'md-h' }, inlineNodes(parseInline(b.text), f)));
    } else if (b.type === 'hr') {
      out.push(f.el('hr', {}, []));
    } else if (b.type === 'quote') {
      out.push(f.el('blockquote', {}, blockNodes(b.children, f)));
    } else if (b.type === 'code') {
      const attrs = { class: 'md-code' };
      if (b.open) attrs['data-open'] = 'true';
      out.push(f.el('div', attrs, [
        f.el('div', { class: 'md-code-head' }, [
          f.el('span', { class: 'md-code-lang' }, [f.text(b.lang || 'код')]),
          f.el('button', { type: 'button', class: 'md-copy', 'data-copy': 'code', 'aria-label': 'Скопировать код', title: 'Скопировать код' },
            [f.text('Копировать')]),
        ]),
        f.el('pre', {}, [f.el('code', b.lang ? { 'data-lang': b.lang } : {}, [f.text(b.text)])]),
      ]));
    } else if (b.type === 'list') {
      const attrs = b.ordered && b.start !== 1 ? { start: String(b.start) } : {};
      /* «плотный» пункт (без пустых строк) — текст без <p>, как в обычном чате */
      const items = b.items.map((it) => f.el('li', {}, blockNodes(it.children, f, !it.loose)));
      out.push(f.el(b.ordered ? 'ol' : 'ul', attrs, items));
    }
  }
  return out;
}

/**
 * Markdown → узлы фабрики. `f.el(tag, attrs, children)` и `f.text(string)`.
 * Атрибуты приходят только из этого модуля (href уже проверен safeHref).
 */
export function renderMarkdown(src, f) {
  return blockNodes(parseBlocks(src), f);
}

/** Фабрика-строка: всё экранировано. Для тестов и для экспорта. */
export const htmlFactory = {
  text: (s) => escapeHtml(s),
  el: (tag, attrs = {}, children = []) => {
    const a = Object.entries(attrs).map(([k, v]) => ` ${k}="${escapeHtml(v)}"`).join('');
    return VOID_TAGS.has(tag) ? `<${tag}${a}>` : `<${tag}${a}>${children.join('')}</${tag}>`;
  },
};

export function markdownToHtml(src) {
  return renderMarkdown(src, htmlFactory).join('');
}
