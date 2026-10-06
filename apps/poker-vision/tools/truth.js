// DOM ground truth for Poker Train frames. Runs in the page. Independent of the vision pipeline:
// the pipeline never sees this; it is only written next to the screenshot as the label.
() => {
  const W = window.innerWidth, H = window.innerHeight;
  const rect = (el) => { const r = el.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height, cx: (r.left + r.right) / 2, cy: (r.top + r.bottom) / 2 }; };
  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.right > 0 && r.bottom > 0 && r.left < W && r.top < H; };
  const textRect = (el) => { const r = document.createRange(); r.selectNodeContents(el); const b = r.getBoundingClientRect(); return { x: b.left, y: b.top, w: b.width, h: b.height, cx: (b.left + b.right) / 2, cy: (b.top + b.bottom) / 2 }; };
  // fraction of grid sample points inside the rect whose top-most element is the element itself (or a relative):
  // 1 = fully visible, 0 = covered. A 6x3 grid so that a card/banner covering only part of a text line is detected.
  const vis = (el, r, nx = 6, ny = 3) => { let hit = 0, tot = 0;
    for (let i = 0; i < nx; i++) for (let j = 0; j < ny; j++) { const x = r.x + r.w * (0.08 + 0.84 * (nx === 1 ? 0.5 : i / (nx - 1))), y = r.y + r.h * (0.15 + 0.7 * (ny === 1 ? 0.5 : j / (ny - 1)));
      if (x < 0 || y < 0 || x >= W || y >= H) continue; tot++; const e = document.elementFromPoint(x, y); if (e && (el.contains(e) || e.contains(el))) hit++; }
    return tot ? hit / tot : 0; };
  const own = (el) => Array.from(el.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim();
  const all = Array.from(document.querySelectorAll('div,span,button'));
  const out = { vw: W, vh: H, dpr: window.devicePixelRatio, animating: document.getAnimations().length };
  const num = (s) => { const m = String(s).replace(/[$,\s]/g, ''); const k = /K$/i.test(m) ? 1e3 : /M$/i.test(m) ? 1e6 : 1; const v = parseFloat(m); return isNaN(v) ? null : v * k; };
  // pot / to call
  for (const el of all) {
    const t = own(el);
    if (/^Pot:$/.test(t) && el.querySelector('span')) { out.pot_text = el.querySelector('span').textContent.trim(); out.pot_rect = rect(el.querySelector('span')); out.pot_line_rect = textRect(el); out.pot_vis = vis(el, out.pot_line_rect); }
    if (/^To call:$/.test(t) && el.querySelector('span')) { out.to_call_text = el.querySelector('span').textContent.trim(); out.to_call_rect = rect(el.querySelector('span')); out.to_call_line_rect = textRect(el); out.to_call_vis = vis(el, out.to_call_line_rect); }
    if (/^Stack:\s*[\d.,]+[KM]?$/.test(t)) { out.hero_stack_text = t.replace(/^Stack:\s*/, ''); out.hero_stack_rect = rect(el); out.hero_stack_line_rect = textRect(el); out.hero_stack_vis = vis(el, out.hero_stack_line_rect); }
  }
  // cards (face-up, flip container)
  const cards = [];
  for (const el of all) {
    if (el.tagName === 'DIV' && el.style && el.style.perspective === '800px' && visible(el)) {
      const raw = el.textContent.replace(/\s+/g, '');
      const m = raw.match(/^(10|[2-9JQKA])([♠♥♦♣])/);
      if (m) { const r = rect(el); const suit = { '♠': 's', '♥': 'h', '♦': 'd', '♣': 'c' }[m[2]]; cards.push({ card: (m[1] === '10' ? 'T' : m[1]) + suit, ...r, vis_corner: vis(el, { x: r.x + 0.02 * r.w, y: r.y + 0.02 * r.h, w: 0.3 * r.w, h: 0.3 * r.h }, 3, 3), vis_center: vis(el, { x: r.x + 0.3 * r.w, y: r.y + 0.4 * r.h, w: 0.4 * r.w, h: 0.3 * r.h }, 3, 2), vis_all: vis(el, r, 5, 6) }); }
    }
  }
  // face-up opponent cards (showdown) sit in seats; hero = largest group lowest on screen with 2 cards
  const hero = cards.filter(c => c.cy > H * 0.42 && c.cy < H * 0.70 && c.w >= 40);
  const heroRow = hero.length ? hero.map(c => c.cy).sort((a, b) => b - a)[0] : null;
  out.hero_cards = cards.filter(c => heroRow !== null && Math.abs(c.cy - heroRow) < 6 && c.w >= 40).sort((a, b) => a.cx - b.cx);
  out.board = cards.filter(c => !out.hero_cards.includes(c) && c.cy < (heroRow ?? H) - 40).sort((a, b) => a.cx - b.cx);
  out.other_faceup = cards.filter(c => !out.hero_cards.includes(c) && !out.board.includes(c));
  // seats: "$..." stack labels; bets: small numeric pills
  out.seats = []; out.bets = []; out.dealer = null; out.position = null; out.buttons = [];
  for (const el of all) {
    if (!visible(el)) continue;
    const t = own(el);
    if (/^\$[\d.,]+[KM]?$/.test(t) && el.children.length === 0) out.seats.push({ text: t, value: num(t), ...textRect(el), vis: vis(el, textRect(el)) });
    else if (/^AI PRO\$[\d.,]+[KM]?$/.test(t.replace(/\s/g, ''))) out.seats.push({ text: t.replace(/^AI PRO/, ''), value: num(t.replace(/^AI PRO/, '')), ...rect(el) });
    if (/^D$/.test(t) && el.children.length === 0) out.dealer = rect(el);
    if (/^(UTG\+1|UTG|MP|HJ|CO|BTN|SB|BB)$/.test(t) && el.children.length === 0 && rect(el).y < 100) { out.position = t; out.position_rect = rect(el); }
    if (el.tagName === 'DIV' && el.children.length === 0 && /^\d[\d.,]*[KM]?$/.test(t) && getComputedStyle(el).fontSize === '10px' && rect(el).y > 60 && rect(el).y < H * 0.65) out.bets.push({ text: t, value: num(t), ...textRect(el), vis: vis(el, textRect(el)) });
    if (el.tagName === 'BUTTON') { const bt = el.innerText.trim().split('\n'); out.buttons.push({ label: bt[0], amount: bt[1] ? num(bt[1]) : null, amount_text: bt[1] || null, ...rect(el) }); }
  }
  out.hand_header = (document.body.innerText.match(/Hand #(\d+)/) || [])[1] || null;
  out.blinds = (document.body.innerText.match(/(\d[\d,]*\/\d[\d,]*)\s*·\s*Lvl/) || [])[1] || null;
  out.raise_panel = /RAISE TO/.test(document.body.innerText);
  out.raise_to_text = null;
  for (const el of all) { if (el.tagName === 'DIV' && el.children.length === 0 && getComputedStyle(el).fontSize === '28px' && /^[\d.,]+[KM]?$/.test(own(el)) && visible(el)) { out.raise_to_text = own(el); out.raise_to_rect = textRect(el); out.raise_to_vis = vis(el, out.raise_to_rect); } }
  const log = Array.from(document.querySelectorAll('div')).find(d => own(d) === 'HAND LOG');
  out.hand_log = log && log.parentElement ? log.parentElement.innerText.split('\n').slice(1).filter(Boolean) : [];
  out.winner_overlay = /wins|Winner|WIN/.test(document.body.innerText.slice(0, 4000));
  return out;
}
