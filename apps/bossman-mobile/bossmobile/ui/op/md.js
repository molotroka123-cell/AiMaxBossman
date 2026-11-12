/* Safe Markdown -> DOM for the operator chat. Never parses HTML: every piece of model text becomes a text node, so
   «<img onerror>» stays visible text and no handler or script can appear. Links: only http(s) and mailto get an
   href (rel=noopener noreferrer, new tab); any other scheme (javascript:, data:, vbscript:…) stays plain text.
   Supported: ``` fenced code (also unterminated while streaming), #-headings, - / * / 1. lists, > quotes,
   paragraphs, `code`, **bold**, *italic*, [text](url) and bare https:// links. */

const MD_SAFE_URL = /^(https?:\/\/|mailto:)/i;

function md_el(doc, tag, cls, text) {
  const n = doc.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function md_link(doc, label, url) {
  const href = String(url || '').trim();
  if (!MD_SAFE_URL.test(href)) return doc.createTextNode(label === href ? label : `${label} (${href})`);
  const a = md_el(doc, 'a', 'md-link', label);
  a.setAttribute('href', href);
  a.setAttribute('target', '_blank');
  a.setAttribute('rel', 'noopener noreferrer');
  return a;
}

const MD_INLINE = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\[[^\]\n]{1,300}\]\([^)\s]{1,2000}\))|(https?:\/\/[^\s<>()"'`]+[^\s<>()"'`.,;:!?])|(\*[^*\s][^*\n]*\*)|(_[^_\s][^_\n]*_)/g;

function md_inline(doc, parent, text) {
  let last = 0;
  let m;
  MD_INLINE.lastIndex = 0;
  while ((m = MD_INLINE.exec(text))) {
    if (m.index > last) parent.appendChild(doc.createTextNode(text.slice(last, m.index)));
    const tok = m[0];
    if (m[1]) parent.appendChild(md_el(doc, 'code', 'md-code', tok.slice(1, -1)));
    else if (m[2]) { const s = md_el(doc, 'strong'); md_inline(doc, s, tok.slice(2, -2)); parent.appendChild(s); }
    else if (m[3]) {
      const k = tok.indexOf('](');
      parent.appendChild(md_link(doc, tok.slice(1, k), tok.slice(k + 2, -1)));
    } else if (m[4]) parent.appendChild(md_link(doc, tok, tok));
    else if (m[5] || m[6]) {
      // _snake_case_ inside a word is not emphasis
      const before = m.index > 0 ? text[m.index - 1] : ' ';
      if (m[6] && /\w/.test(before)) parent.appendChild(doc.createTextNode(tok));
      else { const e = md_el(doc, 'em'); md_inline(doc, e, tok.slice(1, -1)); parent.appendChild(e); }
    }
    last = m.index + tok.length;
    MD_INLINE.lastIndex = last;
  }
  if (last < text.length) parent.appendChild(doc.createTextNode(text.slice(last)));
}

function md_codeBlock(doc, lang, code, opts) {
  const wrap = md_el(doc, 'div', 'md-pre');
  const bar = md_el(doc, 'div', 'md-pre-bar');
  bar.appendChild(md_el(doc, 'span', 'md-pre-lang', lang || 'код'));
  const btn = md_el(doc, 'button', 'md-copy', 'Копировать');
  btn.type = 'button';
  btn.setAttribute('aria-label', 'Копировать код');
  btn.addEventListener('click', () => { if (opts && opts.copy) opts.copy(code, btn); });
  bar.appendChild(btn);
  const pre = md_el(doc, 'pre');
  pre.appendChild(md_el(doc, 'code', '', code));
  wrap.append(bar, pre);
  return wrap;
}

const MD_LIST = /^\s{0,3}([-*•+]|\d{1,3}[.)])\s+(.*)$/;

function md_blocks(doc, target, text) {
  const lines = text.split('\n');
  let i = 0;
  let para = [];
  const flush = () => {
    if (!para.length) return;
    const p = md_el(doc, 'p');
    para.forEach((l, j) => { if (j) p.appendChild(md_el(doc, 'br')); md_inline(doc, p, l); });
    target.appendChild(p);
    para = [];
  };
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { flush(); i++; continue; }
    const h = /^\s{0,3}(#{1,6})\s+(.*)$/.exec(line);
    if (h) {
      flush();
      const n = md_el(doc, 'p', `md-h md-h${Math.min(h[1].length, 3)}`);
      md_inline(doc, n, h[2].replace(/\s#+\s*$/, ''));
      target.appendChild(n); i++; continue;
    }
    if (/^\s{0,3}>/.test(line)) {
      flush();
      const q = md_el(doc, 'blockquote');
      const buf = [];
      while (i < lines.length && /^\s{0,3}>/.test(lines[i])) { buf.push(lines[i].replace(/^\s{0,3}>\s?/, '')); i++; }
      md_blocks(doc, q, buf.join('\n'));
      target.appendChild(q); continue;
    }
    const li = MD_LIST.exec(line);
    if (li) {
      flush();
      const ordered = /\d/.test(li[1]);
      const list = md_el(doc, ordered ? 'ol' : 'ul');
      if (ordered) { const start = parseInt(li[1], 10); if (start > 1) list.setAttribute('start', String(start)); }
      while (i < lines.length) {
        const mm = MD_LIST.exec(lines[i]);
        if (mm && /\d/.test(mm[1]) === ordered) {
          const item = md_el(doc, 'li'); md_inline(doc, item, mm[2]); list.appendChild(item); i++;
        } else if (lines[i].trim() && /^\s{2,}\S/.test(lines[i]) && list.lastChild) {
          list.lastChild.appendChild(md_el(doc, 'br')); md_inline(doc, list.lastChild, lines[i].trim()); i++;
        } else break;
      }
      target.appendChild(list); continue;
    }
    if (/^\s{0,3}([-*_])(\s*\1){2,}\s*$/.test(line)) { flush(); target.appendChild(md_el(doc, 'hr')); i++; continue; }
    para.push(line);
    i++;
  }
  flush();
}

/** Render `text` into `target` (its children are replaced). opts.copy(code, button) is called by code-block buttons. */
export function renderMarkdown(target, text, opts) {
  const doc = target.ownerDocument;
  const frag = doc.createDocumentFragment();
  const src = String(text == null ? '' : text).replace(/\r\n?/g, '\n');
  const fence = /^\s{0,3}```([^\n`]*)$/;
  const lines = src.split('\n');
  let buf = [];
  let i = 0;
  while (i < lines.length) {
    const f = fence.exec(lines[i]);
    if (!f) { buf.push(lines[i]); i++; continue; }
    if (buf.length) { md_blocks(doc, frag, buf.join('\n')); buf = []; }
    const lang = f[1].trim().slice(0, 24);
    const code = [];
    i++;
    while (i < lines.length && !/^\s{0,3}```\s*$/.test(lines[i])) { code.push(lines[i]); i++; }
    i++;                                   // the closing fence (absent while a block is still streaming)
    frag.appendChild(md_codeBlock(doc, lang, code.join('\n'), opts));
  }
  if (buf.length) md_blocks(doc, frag, buf.join('\n'));
  target.replaceChildren(frag);
}
