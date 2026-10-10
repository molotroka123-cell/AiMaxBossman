() => {
  const CARD_RE = /^(10|[2-9JQKA])\n?([♠♥♦♣])$/;
  const SUIT = { '♠': 's', '♥': 'h', '♦': 'd', '♣': 'c' };
  const num = (s) => {
    if (!s) return null;
    const t = s.replace(/[\s  ,$]/g, '');
    const m = t.match(/^([0-9.]+)([KM]?)$/);
    if (!m) return null;
    const v = parseFloat(m[1]);
    return Math.round(m[2] === 'M' ? v * 1e6 : m[2] === 'K' ? v * 1e3 : v);
  };
  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const cardEls = [...document.querySelectorAll('div')].filter((d) =>
    d.children.length === 2 && CARD_RE.test(d.innerText.trim()) && !(d.style.transform || '').includes('rotate'));
  const cardOf = (d) => {
    const m = d.innerText.trim().match(CARD_RE);
    return (m[1] === '10' ? 'T' : m[1]) + SUIT[m[2]];
  };
  const stackDiv = [...document.querySelectorAll('div')].find((d) => d.children.length === 0 && /^Stack:/.test(d.innerText.trim()));
  const heroArea = stackDiv ? stackDiv.parentElement : null;
  const hero = [], board = [], shown = [];
  for (const d of cardEls) {
    const c = cardOf(d);
    if (heroArea && heroArea.contains(d)) { if (!hero.includes(c)) hero.push(c); continue; }
    // opponent seat containers include a "$<stack>" badge; community cards do not
    let a = d.parentElement, inSeat = false;
    for (let i = 0; i < 8 && a; i++, a = a.parentElement) {
      if ([...a.children].some((ch) => ch.children.length === 0 && /^\$/.test(ch.innerText.trim()))) { inSeat = true; break; }
    }
    if (inSeat) shown.push(c); else if (!board.includes(c)) board.push(c);
  }
  const leaf = (re) => [...document.querySelectorAll('div,span')].find((d) => re.test((d.innerText || '').trim()) && visible(d));
  const potEl = leaf(/^Pot:\s*\S+$/);
  const toCallEl = leaf(/^To call:\s*\S+$/);
  const btn = (name) => [...document.querySelectorAll('button')].find((b) => visible(b) && b.innerText.replace(/\s+/g, ' ').trim().toUpperCase().startsWith(name));
  const callBtn = btn('CALL');
  const allInBtn = btn('ALL IN');
  const slider = document.querySelector('input[type=range]');
  const seatStacks = [...document.querySelectorAll('div')].filter((d) => d.children.length === 0 && /^\$[0-9\s  .,KM]+$/.test(d.innerText.trim())).map((d) => num(d.innerText));
  return {
    heroTurn: !!btn('FOLD'),
    handOver: !!btn('DEAL'),
    hero, board, shown,
    pot: potEl ? num(potEl.innerText.replace(/^Pot:/, '')) : 0,
    toCall: toCallEl ? num(toCallEl.innerText.replace(/^To call:/, '')) : (callBtn ? num(callBtn.innerText.replace(/CALL/i, '')) : 0),
    canCheck: !!btn('CHECK'),
    canCall: !!callBtn,
    canRaise: !!btn('RAISE') || !!btn('CONFIRM'),
    allInOnly: !!allInBtn && !btn('RAISE') && !btn('CONFIRM'),
    allInAmount: allInBtn ? num(allInBtn.innerText.replace(/ALL IN/i, '')) : null,
    heroStack: stackDiv ? num(stackDiv.innerText.replace(/^Stack:/, '')) : null,
    opponentStacks: seatStacks,
    sliderMin: slider ? Number(slider.min) : null,
    sliderMax: slider ? Number(slider.max) : null,
    handLog: (() => { const h = leaf(/^HAND LOG/); return h ? h.innerText.split('\n').slice(1, 12) : []; })(),
    header: (() => { const h = leaf(/^Hand #\d+/); return h ? h.innerText.trim() : null; })(),
  };
}
