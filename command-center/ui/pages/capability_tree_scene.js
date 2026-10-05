/* Дерево развития — живая сцена (Canvas 2D).
 *
 * Мир 1600×900, вписывается в ширину контейнера. Слои за кадр:
 *   фон (заранее: небо, туманность, луна) → звёзды, горы (параллакс) → отражение кроны в озере
 *   (полосами, с рябью) → крона (ствол из волокон, ветви с изгибом от ветра, «сок» по ветвям,
 *   листья-лепестки с ореолом) → отметки (маяк цикла, искра работы, кольцо выбора) → лупа →
 *   светлячки и бабочки. Цвет листа — уровень доказательства из карты (не PASS). Ветер
 *   иерархический: подветвь наследует изгиб ветви. При открытии дерево вырастает и распускается.
 * prefers-reduced-motion: один статичный кадр (перерисовка только на наведение/клик).
 */
const W = 1600, H = 900, GROUND = 792, TX = 800, TY = 520;
const TAU = Math.PI * 2;
const clamp = (v, a = 0, b = 1) => Math.max(a, Math.min(b, v));
const easeOut = (t) => 1 - Math.pow(1 - t, 3);
const easeBack = (t) => { const c = 1.6; return 1 + (c + 1) * Math.pow(t - 1, 3) + c * Math.pow(t - 1, 2); };

function rng(seed) {
  let x = 2166136261;
  for (const ch of String(seed)) x = Math.imul(x ^ ch.charCodeAt(0), 16777619);
  return () => { x ^= x << 13; x ^= x >>> 17; x ^= x << 5; return ((x >>> 0) % 1000003) / 1000003; };
}

function bez(p0, p1, p2, p3, t) {
  const u = 1 - t;
  return [u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
    u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1]];
}

function hexToRgb(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function glowSprite(hex, size = 64) {
  const c = document.createElement('canvas');
  c.width = c.height = size;
  const g = c.getContext('2d');
  const [r, gg, b] = hexToRgb(hex);
  const grd = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  grd.addColorStop(0, `rgba(${r},${gg},${b},.95)`);
  grd.addColorStop(0.25, `rgba(${r},${gg},${b},.45)`);
  grd.addColorStop(1, `rgba(${r},${gg},${b},0)`);
  g.fillStyle = grd;
  g.fillRect(0, 0, size, size);
  return c;
}

function rotateAround(px, py, ox, oy, a) {
  const s = Math.sin(a), c = Math.cos(a), dx = px - ox, dy = py - oy;
  return [ox + dx * c - dy * s, oy + dx * s + dy * c];
}

/* ---------------------------------------------------------------- geometry */
function buildModel(nodes, colors) {
  const families = nodes.filter(n => n.parent === 'bossman')
    .sort((a, b) => (a.id === 'jeff' ? -1 : b.id === 'jeff' ? 1 : 0));
  const kids = new Map();
  nodes.forEach(n => { if (!kids.has(n.parent)) kids.set(n.parent, []); kids.get(n.parent).push(n); });
  const branches = [];
  const leaves = [];
  const labels = [];
  const n = families.length;
  families.forEach((fam, fi) => {
    const r = rng(fam.id);
    const a = Math.PI * (0.94 - (0.88 * fi) / Math.max(1, n - 1));
    const side = Math.cos(a);
    const reach = 470 + 60 * Math.sin(a);
    const end = [TX + 560 * side, Math.max(96, TY - 70 - 380 * Math.sin(a))];
    const base = [TX + side * 16, TY + 24 - Math.abs(side) * 60];
    const main = {
      p: [base, [TX + side * 70, TY - 150 - 50 * Math.sin(a)], [TX + (end[0] - TX) * 0.6, end[1] + 70 + 30 * Math.abs(side)], end],
      w0: fam.id === 'jeff' ? 15 : 11, w1: 1.6, parent: -1, attach: 0, phase: r() * TAU,
      amp: 0.022 + 0.012 * Math.abs(side), family: fi, depth: 0, delay: 0.12 + fi * 0.035, len: reach,
    };
    const mainIdx = branches.push(main) - 1;
    const subs = [mainIdx];
    for (const [t, spread, lenK] of [[0.42, 0.55, 0.42], [0.66, -0.45, 0.33], [0.82, 0.35, 0.22]]) {
      const from = bez(...main.p, t);
      const ahead = bez(...main.p, Math.min(1, t + 0.02));
      const dir = Math.atan2(ahead[1] - from[1], ahead[0] - from[0]) + spread * (side >= 0 ? -1 : 1) * (0.7 + r() * 0.5);
      const L = reach * lenK * (0.85 + r() * 0.3);
      const e = [from[0] + Math.cos(dir) * L, Math.max(70, from[1] + Math.sin(dir) * L)];
      const c1 = [from[0] + Math.cos(dir - 0.3) * L * 0.35, from[1] + Math.sin(dir - 0.3) * L * 0.35 - 12];
      const c2 = [from[0] + Math.cos(dir + 0.15) * L * 0.75, from[1] + Math.sin(dir + 0.15) * L * 0.75];
      subs.push(branches.push({ p: [from, c1, c2, e], w0: main.w0 * (1 - t) * 0.75 + 1.5, w1: 0.9, parent: mainIdx,
        attach: t, phase: r() * TAU, amp: 0.05 + r() * 0.03, family: fi, depth: 1, delay: main.delay + 0.25 + t * 0.3, len: L }) - 1);
    }
    const famLeaves = kids.get(fam.id) || [];
    famLeaves.forEach((leaf, li) => {
      const bi = subs[li % subs.length];
      const b = branches[bi];
      const s = b.depth === 0 ? 0.45 + 0.55 * r() : 0.15 + 0.85 * r();
      const spread = (b.depth === 0 ? 46 : 30) * (0.4 + s * 0.8) * (0.5 + Math.min(1, Math.sqrt(famLeaves.length) / 12));
      const off = [(r() - 0.5) * 2 * spread, (r() - 0.65) * 2 * spread * 0.8];
      leaves.push({ node: leaf, branch: bi, s, off, color: colors[leaf.status] || colors.mixed,
        rot: r() * TAU, size: 6.2 + r() * 2.6, flutter: r() * TAU, bloom: b.delay + 0.55 + r() * 0.7, x: 0, y: 0, scale: 1 });
    });
    labels.push({ node: fam, branch: mainIdx, text: String(fam.label || fam.id).split('·')[0].trim().toUpperCase(),
      jeff: fam.id === 'jeff', side });
  });
  const strands = [];
  const rs = rng('trunk');
  for (let i = 0; i < 11; i++) {
    // Strands cross as they rise: a twisted, braided trunk wide at the root.
    const bx = TX + (i - 5) * 13, tx = TX + (5 - i) * 4.2, mid = TX + (i - 5) * 6 * (i % 2 ? -1 : 1);
    strands.push({ p: [[bx, GROUND + 4], [bx + (rs() - 0.5) * 40, GROUND - 120], [mid + (rs() - 0.5) * 30, TY + 120], [tx, TY + 6]], w: 9 + rs() * 5 });
  }
  const roots = [];
  for (let i = 0; i < 12; i++) {
    const dir = i < 6 ? -1 : 1, k = (i % 6) + 1;
    roots.push({ p: [[TX + dir * 18, GROUND - 4], [TX + dir * (40 + k * 30), GROUND + 4], [TX + dir * (90 + k * 60), GROUND - 2 + rs() * 10], [TX + dir * (120 + k * 85), GROUND + 6 + rs() * 12]], w: 5 - k * 0.5 });
  }
  return { branches, leaves, labels, strands, roots };
}

/* ---------------------------------------------------------------- background */
function paintBackground(ctx) {
  const sky = ctx.createLinearGradient(0, 0, 0, GROUND);
  sky.addColorStop(0, '#050817'); sky.addColorStop(0.45, '#0d1636'); sky.addColorStop(0.8, '#1b2650'); sky.addColorStop(1, '#2a2c4f');
  ctx.fillStyle = sky; ctx.fillRect(0, 0, W, H);
  const neb = [[420, 160, 300, 'rgba(110,80,200,.10)'], [1150, 220, 360, 'rgba(60,120,220,.10)'], [780, 90, 260, 'rgba(200,120,255,.07)']];
  for (const [x, y, r, c] of neb) {
    const g = ctx.createRadialGradient(x, y, 0, x, y, r); g.addColorStop(0, c); g.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = g; ctx.fillRect(x - r, y - r, r * 2, r * 2);
  }
  const mx = 1430, my = 140;
  const halo = ctx.createRadialGradient(mx, my, 10, mx, my, 150);
  halo.addColorStop(0, 'rgba(255,246,220,.35)'); halo.addColorStop(1, 'rgba(255,246,220,0)');
  ctx.fillStyle = halo; ctx.fillRect(mx - 150, my - 150, 300, 300);
  const moon = ctx.createRadialGradient(mx - 8, my - 8, 4, mx, my, 28);
  moon.addColorStop(0, '#fffdf2'); moon.addColorStop(1, '#e9e2c8');
  ctx.fillStyle = moon; ctx.beginPath(); ctx.arc(mx, my, 26, 0, TAU); ctx.fill();
  const crown = ctx.createRadialGradient(TX, TY - 60, 30, TX, TY - 60, 560);
  crown.addColorStop(0, 'rgba(255,214,140,.16)'); crown.addColorStop(1, 'rgba(255,214,140,0)');
  ctx.fillStyle = crown; ctx.fillRect(0, 0, W, GROUND);
}

function mountains(ctx, shift, seed, baseY, amp, color) {
  const r = rng(seed);
  ctx.fillStyle = color; ctx.beginPath(); ctx.moveTo(-40 + shift, GROUND);
  let x = -40;
  while (x < W + 80) { ctx.lineTo(x + shift, baseY - r() * amp); x += 40 + r() * 70; }
  ctx.lineTo(W + 80 + shift, GROUND); ctx.closePath(); ctx.fill();
}

/* ---------------------------------------------------------------- scene */
export function createScene({ nodes, colors, onPick, onHover }) {
  const wrap = document.createElement('div');
  wrap.className = 'ct-scene';
  const canvas = document.createElement('canvas');
  canvas.className = 'ct-canvas';
  canvas.setAttribute('role', 'img');
  canvas.setAttribute('aria-label', 'Дерево развития Bossman: ветви — направления, листья — возможности. Список узлов ниже.');
  canvas.tabIndex = 0;
  const tip = document.createElement('div');
  tip.className = 'ct-tip';
  tip.setAttribute('aria-live', 'polite');
  wrap.append(canvas, tip);
  const ctx = canvas.getContext('2d');
  const model = buildModel(nodes, colors);
  canvas.dataset.leaves = String(model.leaves.length);
  const sprites = new Map(Object.values(colors).map(c => [c, glowSprite(c)]));
  const gold = glowSprite('#ffd27a');
  const white = glowSprite('#eaf2ff');
  const bg = document.createElement('canvas'); bg.width = W; bg.height = H; paintBackground(bg.getContext('2d'));
  const crown = document.createElement('canvas'); crown.width = W; crown.height = H;
  const cctx = crown.getContext('2d');
  const reduced = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  const rs = rng('sky');
  const stars = Array.from({ length: 230 }, () => ({ x: rs() * W, y: rs() * GROUND * 0.92, r: rs() * 1.3 + 0.25, p: rs() * TAU, s: 0.6 + rs() * 1.6 }));
  const fireflies = Array.from({ length: 44 }, (_, i) => ({ x: TX + (rs() - 0.5) * 1300, y: 160 + rs() * 600, p: rs() * TAU, s: 0.3 + rs() * 0.7, k: i }));
  const butterflies = Array.from({ length: 6 }, (_, i) => ({ cx: TX + (i - 2.5) * 190, cy: 300 + (i % 3) * 90, ax: 140 + i * 25, ay: 70 + i * 12,
    w: 0.11 + i * 0.017, p: i * 1.7, hue: [196, 268, 48, 160, 300, 220][i] }));
  const pulses = [];
  let marks = { active: [], working: [], selected: null };
  let hover = null, mouse = null, par = [0, 0], scale = 1, dpr = 1, start = performance.now(), last = start, raf = 0, shoot = null;
  const byNode = new Map(model.leaves.map(l => [l.node.id, l]));
  const labelByNode = new Map(model.labels.map(l => [l.node.id, l]));

  function resize() {
    const w = Math.max(320, wrap.clientWidth || 1200);
    dpr = Math.min(2, window.devicePixelRatio || 1);
    scale = w / W;
    canvas.style.width = `${w}px`; canvas.style.height = `${Math.round(w * H / W)}px`;
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(w * H / W * dpr);
    if (reduced) draw(performance.now());
  }

  function pose(t) {
    // Wind: each branch bends around its base; a child inherits its parent's bend at the attachment point.
    const wind = Math.sin(t * 0.00031) * 0.6 + Math.sin(t * 0.00073) * 0.4;
    const out = [];
    model.branches.forEach((b, i) => {
      const bend = reduced ? 0 : (b.amp * (0.6 + 0.4 * wind) * Math.sin(t * 0.0011 + b.phase) + b.amp * 0.35 * wind);
      let pts = [];
      for (let k = 0; k <= 22; k++) pts.push(bez(...b.p, k / 22));
      const ox = b.p[0][0], oy = b.p[0][1];
      let shiftX = 0, shiftY = 0;
      if (b.parent >= 0) {
        const at = out[b.parent].at(b.attach);
        shiftX = at[0] - b.p[0][0]; shiftY = at[1] - b.p[0][1];
      }
      pts = pts.map(([x, y], k) => {
        const [rx, ry] = rotateAround(x, y, ox, oy, bend * Math.pow(k / 22, 1.4));
        return [rx + shiftX, ry + shiftY];
      });
      const at = (s) => {
        const f = clamp(s) * 22, k = Math.min(21, Math.floor(f)), u = f - k;
        return [pts[k][0] + (pts[k + 1][0] - pts[k][0]) * u, pts[k][1] + (pts[k + 1][1] - pts[k][1]) * u];
      };
      out[i] = { pts, at, b };
    });
    return out;
  }

  /** One smooth tapered ribbon (a filled outline), not overlapping translucent segments. */
  function strokeTapered(g, pts, w0, w1, reveal, style) {
    const n = Math.max(1, Math.floor((pts.length - 1) * reveal));
    const left = [], right = [];
    for (let k = 0; k <= n; k++) {
      const a = pts[Math.max(0, k - 1)], b = pts[Math.min(pts.length - 1, k + 1)];
      let nx = -(b[1] - a[1]), ny = b[0] - a[0];
      const len = Math.hypot(nx, ny) || 1;
      nx /= len; ny /= len;
      const w = (w0 + (w1 - w0) * (k / (pts.length - 1))) / 2;
      left.push([pts[k][0] + nx * w, pts[k][1] + ny * w]);
      right.push([pts[k][0] - nx * w, pts[k][1] - ny * w]);
    }
    g.fillStyle = style;
    g.beginPath();
    g.moveTo(left[0][0], left[0][1]);
    for (let k = 1; k < left.length; k++) g.lineTo(left[k][0], left[k][1]);
    const tip = right[right.length - 1], end = pts[n];
    g.quadraticCurveTo(end[0] + (end[0] - pts[Math.max(0, n - 1)][0]) * 0.6, end[1] + (end[1] - pts[Math.max(0, n - 1)][1]) * 0.6, tip[0], tip[1]);
    for (let k = right.length - 2; k >= 0; k--) g.lineTo(right[k][0], right[k][1]);
    g.closePath();
    g.fill();
  }

  function drawCrown(grow, poses) {
    const g = cctx;
    g.clearRect(0, 0, W, H);
    g.lineCap = 'round';
    for (const r of model.roots) {
      const pts = Array.from({ length: 16 }, (_, k) => bez(...r.p, k / 15));
      g.globalCompositeOperation = 'lighter';
      strokeTapered(g, pts, r.w + 6, 1, clamp(grow * 3), 'rgba(255,205,120,.10)');
      g.globalCompositeOperation = 'source-over';
      strokeTapered(g, pts, r.w, 0.6, clamp(grow * 3), 'rgba(214,226,255,.55)');
    }
    const trunkReveal = clamp(grow * 2.4);
    const core = g.createRadialGradient(TX, TY + 150, 10, TX, TY + 150, 220);
    core.addColorStop(0, `rgba(255,214,140,${0.32 * trunkReveal})`); core.addColorStop(1, 'rgba(255,214,140,0)');
    g.fillStyle = core; g.fillRect(TX - 240, TY - 80, 480, 420);
    model.strands.forEach((st, i) => {
      const pts = Array.from({ length: 24 }, (_, k) => bez(...st.p, k / 23));
      const shade = i % 3 === 0 ? 'rgba(196,210,245,.9)' : i % 3 === 1 ? 'rgba(232,239,255,.92)' : 'rgba(170,188,232,.88)';
      g.globalCompositeOperation = 'lighter';
      strokeTapered(g, pts, st.w + 12, 8, trunkReveal, 'rgba(150,180,255,.05)');
      g.globalCompositeOperation = 'source-over';
      strokeTapered(g, pts, st.w, st.w * 0.5, trunkReveal, shade);
      strokeTapered(g, pts, st.w * 0.28, 1, trunkReveal, 'rgba(255,240,205,.5)');
    });
    poses.forEach((p) => {
      const b = p.b;
      const reveal = easeOut(clamp((grow - b.delay) / 0.55));
      if (reveal <= 0) return;
      g.globalCompositeOperation = 'lighter';
      strokeTapered(g, p.pts, b.w0 + 9, b.w1 + 3, reveal, 'rgba(140,175,255,.07)');
      g.globalCompositeOperation = 'source-over';
      strokeTapered(g, p.pts, b.w0, b.w1, reveal, b.depth ? 'rgba(220,232,255,.78)' : 'rgba(236,243,255,.92)');
      strokeTapered(g, p.pts, Math.max(1, b.w0 * 0.3), 0.5, reveal, 'rgba(255,230,170,.55)');
    });
  }

  function leafShape(g, size) {
    g.beginPath();
    g.moveTo(-size, 0);
    g.quadraticCurveTo(0, -size * 0.62, size, 0);
    g.quadraticCurveTo(0, size * 0.62, -size, 0);
    g.closePath();
  }

  function drawLeaves(t, grow, poses) {
    const g = cctx;
    const sel = marks.selected;
    for (const l of model.leaves) {
      const [bx, by] = poses[l.branch].at(l.s);
      const sway = reduced ? 0 : Math.sin(t * 0.0016 + l.flutter) * 2.2;
      l.x = bx + l.off[0] + sway; l.y = by + l.off[1] + sway * 0.4;
      const bloom = reduced ? 1 : easeBack(clamp((grow - l.bloom) / 0.45));
      let s = bloom * l.scale;
      if (s <= 0.02) continue;
      if (hover && hover.kind === 'leaf') {
        const d = Math.hypot(l.x - hover.x, l.y - hover.y);
        if (d < 70) s *= 1 + 0.9 * (1 - d / 70) * (1 - d / 70);
      }
      const dim = sel && sel !== l.node.id ? 0.78 : 1;
      g.globalAlpha = 0.34 * dim;
      g.globalCompositeOperation = 'lighter';
      const sp = sprites.get(l.color) || white, R = l.size * 2.6 * s;
      g.drawImage(sp, l.x - R, l.y - R, R * 2, R * 2);
      g.globalCompositeOperation = 'source-over';
      g.globalAlpha = dim;
      g.save();
      g.translate(l.x, l.y);
      g.rotate(l.rot + (reduced ? 0 : Math.sin(t * 0.0023 + l.flutter) * 0.32));
      leafShape(g, l.size * s);
      g.fillStyle = l.color; g.fill();
      g.globalAlpha = 0.55 * dim;
      g.strokeStyle = 'rgba(255,255,255,.85)'; g.lineWidth = 0.7;
      g.beginPath(); g.moveTo(-l.size * s * 0.8, 0); g.lineTo(l.size * s * 0.8, 0); g.stroke();
      g.restore();
    }
    g.globalAlpha = 1;
  }

  let labelsLaidOut = false;
  function layoutLabels(g, poses) {
    // Once, on the rest pose: measure every label and push vertically overlapping ones apart.
    const rows = model.labels.map((lb) => {
      const [ex, ey] = poses[lb.branch].at(1);
      lb.align = lb.side < -0.15 ? 'right' : lb.side > 0.15 ? 'left' : 'center';
      g.font = `600 ${lb.jeff ? 30 : 17}px Georgia, 'Times New Roman', serif`;
      const w = g.measureText(lb.text).width * 1.08 + 12;
      const x = ex + (lb.align === 'right' ? -16 : lb.align === 'left' ? 16 : 0);
      const x0 = lb.align === 'right' ? x - w : lb.align === 'left' ? x : x - w / 2;
      lb.dy = 0;
      return { lb, x0, x1: x0 + w, y: ey - 14, h: lb.jeff ? 34 : 24 };
    });
    for (let it = 0; it < 60; it++) {
      let moved = false;
      for (let i = 0; i < rows.length; i++) {
        for (let j = i + 1; j < rows.length; j++) {
          const a = rows[i], b = rows[j];
          if (a.x1 < b.x0 || b.x1 < a.x0) continue;
          const gap = Math.max(a.h, b.h) - Math.abs((a.y + a.lb.dy) - (b.y + b.lb.dy));
          if (gap <= 0) continue;
          const up = (a.y + a.lb.dy) <= (b.y + b.lb.dy) ? a : b, down = up === a ? b : a;
          up.lb.dy -= gap / 2 + 0.5; down.lb.dy += gap / 2 + 0.5;
          moved = true;
        }
      }
      if (!moved) break;
    }
    labelsLaidOut = true;
  }

  function drawLabels(grow, poses) {
    const g = cctx;
    const alpha = clamp((grow - 1.05) / 0.5);
    if (alpha <= 0) return;
    if (!labelsLaidOut) layoutLabels(g, poses);
    for (const lb of model.labels) {
      const [ex, ey] = poses[lb.branch].at(1);
      const align = lb.align;
      const x = ex + (align === 'right' ? -16 : align === 'left' ? 16 : 0);
      const y = ey - 14 + lb.dy;
      g.font = `600 ${lb.jeff ? 30 : 17}px Georgia, 'Times New Roman', serif`;
      g.textAlign = align; g.textBaseline = 'alphabetic';
      g.globalAlpha = alpha;
      g.shadowColor = 'rgba(140,185,255,.95)'; g.shadowBlur = 14;
      g.fillStyle = hover && hover.kind === 'label' && hover.node.id === lb.node.id ? '#fff3c4' : '#eef4ff';
      g.fillText(lb.text.split('').join(' '), x, y);
      g.shadowBlur = 0;
      const w = g.measureText(lb.text).width + 20;
      lb.box = { x: align === 'right' ? x - w : align === 'left' ? x : x - w / 2, y: y - 30, w, h: 40, ex, ey };
    }
    g.globalAlpha = 1;
  }

  function spawnPulse(poses) {
    const hot = new Set([...(marks.working || []), ...(marks.active || [])].map(id => byNode.get(id)?.branch).filter(v => v != null));
    const pool = hot.size && Math.random() < 0.55 ? [...hot] : poses.map((_, i) => i);
    const bi = pool[Math.floor(Math.random() * pool.length)];
    pulses.push({ bi, s: 0, v: 0.00042 + Math.random() * 0.0003, hot: hot.has(bi) });
  }

  function drawPulses(dt, poses) {
    const g = cctx;
    g.globalCompositeOperation = 'lighter';
    for (let i = pulses.length - 1; i >= 0; i--) {
      const p = pulses[i];
      p.s += p.v * dt;
      if (p.s >= 1) { pulses.splice(i, 1); continue; }
      for (let k = 0; k < 5; k++) {
        const [x, y] = poses[p.bi].at(Math.max(0, p.s - k * 0.012));
        const R = (p.hot ? 16 : 11) * (1 - k * 0.17);
        g.globalAlpha = (1 - k * 0.2) * (p.hot ? 0.95 : 0.7);
        g.drawImage(p.hot ? gold : white, x - R, y - R, R * 2, R * 2);
      }
    }
    g.globalAlpha = 1;
    g.globalCompositeOperation = 'source-over';
  }

  function drawSky(t) {
    ctx.drawImage(bg, 0, 0);
    ctx.fillStyle = '#e8eeff';
    for (const s of stars) {
      ctx.globalAlpha = reduced ? 0.6 : 0.35 + 0.65 * (0.5 + 0.5 * Math.sin(t * 0.001 * s.s + s.p));
      ctx.beginPath(); ctx.arc(s.x, s.y, s.r, 0, TAU); ctx.fill();
    }
    ctx.globalAlpha = 1;
    if (!reduced) {
      if (!shoot && Math.random() < 0.0025) shoot = { x: 200 + Math.random() * 1000, y: 40 + Math.random() * 200, life: 0 };
      if (shoot) {
        shoot.life += 16;
        const k = shoot.life / 900, x = shoot.x + k * 420, y = shoot.y + k * 160;
        const grd = ctx.createLinearGradient(x - 120, y - 46, x, y);
        grd.addColorStop(0, 'rgba(255,255,255,0)'); grd.addColorStop(1, `rgba(255,255,255,${0.9 * (1 - Math.min(1, k))})`);
        ctx.strokeStyle = grd; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(x - 120, y - 46); ctx.lineTo(x, y); ctx.stroke();
        if (k >= 1) shoot = null;
      }
    }
    mountains(ctx, par[0] * 8, 'far', GROUND - 90, 110, 'rgba(36,46,86,.85)');
    mountains(ctx, par[0] * 16, 'near', GROUND - 30, 70, 'rgba(18,24,48,.95)');
  }

  function drawLake(t) {
    const lake = ctx.createLinearGradient(0, GROUND, 0, H);
    lake.addColorStop(0, '#16224a'); lake.addColorStop(1, '#070b1c');
    ctx.fillStyle = lake; ctx.fillRect(0, GROUND, W, H - GROUND);
    // Reflection of the crown: thin mirrored strips, each with its own ripple offset.
    const strips = 18, sh = (H - GROUND) / strips, srcH = sh * 3.2;
    for (let i = 0; i < strips; i++) {
      const y = GROUND + i * sh;
      const srcY = GROUND - (i + 1) * srcH;
      const ripple = reduced ? 0 : Math.sin(t * 0.002 + i * 0.9) * (1 + i * 0.5);
      ctx.globalAlpha = 0.22 * (1 - i / strips);
      ctx.save();
      ctx.translate(0, 2 * y + sh);
      ctx.scale(1, -1);
      ctx.drawImage(crown, 0, Math.max(0, srcY), W, srcH, ripple, y, W, sh);
      ctx.restore();
    }
    ctx.globalAlpha = 1;
    const mx = 1430;
    ctx.globalCompositeOperation = 'lighter';
    ctx.fillStyle = '#fff3d6';
    for (let i = 0; i < 7; i++) {
      ctx.globalAlpha = 0.16 - i * 0.02;
      ctx.fillRect(mx - 30 + (reduced ? 0 : Math.sin(t * 0.002 + i) * 6), GROUND + 6 + i * 13, 60 - i * 6, 2);
    }
    ctx.globalCompositeOperation = 'source-over';
    ctx.globalAlpha = 1;
  }

  function drawCritters(t, dt) {
    ctx.globalCompositeOperation = 'lighter';
    for (const f of fireflies) {
      if (!reduced) { f.p += dt * 0.0006 * f.s; f.y -= dt * 0.006 * f.s; if (f.y < 90) f.y = 720; }
      const x = f.x + Math.sin(f.p * 1.7 + f.k) * 40, y = f.y + Math.cos(f.p * 1.3) * 16;
      ctx.globalAlpha = 0.25 + 0.75 * Math.max(0, Math.sin(t * 0.0021 + f.k));
      ctx.drawImage(gold, x - 9, y - 9, 18, 18);
    }
    ctx.globalAlpha = 1;
    for (const b of butterflies) {
      const k = t * 0.001 * b.w + b.p;
      const x = b.cx + Math.sin(k * 2.1) * b.ax + par[0] * 10, y = b.cy + Math.sin(k * 3.3 + 1) * b.ay;
      const heading = Math.atan2(Math.cos(k * 3.3 + 1) * b.ay * 3.3, Math.cos(k * 2.1) * b.ax * 2.1);
      const flap = reduced ? 0.8 : Math.abs(Math.sin(t * 0.018 + b.p * 3));
      ctx.save(); ctx.translate(x, y); ctx.rotate(heading * 0.25);
      ctx.globalAlpha = 0.55; ctx.drawImage(white, -26, -26, 52, 52);
      ctx.globalAlpha = 0.95;
      for (const dir of [-1, 1]) {
        ctx.save(); ctx.scale(dir * (0.25 + 0.75 * flap), 1);
        const grd = ctx.createLinearGradient(0, -10, 16, 8);
        grd.addColorStop(0, `hsla(${b.hue},95%,80%,.95)`); grd.addColorStop(1, `hsla(${b.hue + 40},90%,60%,.65)`);
        ctx.fillStyle = grd;
        ctx.beginPath(); ctx.ellipse(8, -5, 9, 6.5, -0.5, 0, TAU); ctx.fill();
        ctx.beginPath(); ctx.ellipse(6, 5, 6, 4.5, 0.6, 0, TAU); ctx.fill();
        ctx.restore();
      }
      ctx.fillStyle = 'rgba(255,255,255,.9)'; ctx.fillRect(-0.8, -6, 1.6, 12);
      ctx.restore();
    }
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = 'source-over';
  }

  function ring(x, y, r, color, width, dash = null, rot = 0) {
    ctx.save(); ctx.translate(x, y); ctx.rotate(rot);
    ctx.strokeStyle = color; ctx.lineWidth = width; if (dash) ctx.setLineDash(dash);
    ctx.beginPath(); ctx.arc(0, 0, r, 0, TAU); ctx.stroke(); ctx.restore();
  }

  function drawMarks(t) {
    for (const id of marks.active || []) {
      const l = byNode.get(id); if (!l) continue;
      ctx.globalCompositeOperation = 'lighter';
      const beam = ctx.createLinearGradient(l.x, l.y, l.x, l.y - 260);
      beam.addColorStop(0, 'rgba(255,236,170,.35)'); beam.addColorStop(1, 'rgba(255,236,170,0)');
      ctx.fillStyle = beam; ctx.fillRect(l.x - 5, l.y - 260, 10, 260);
      ctx.globalCompositeOperation = 'source-over';
      for (let i = 0; i < 2; i++) {
        const k = ((t * 0.0006) + i * 0.5) % 1;
        ctx.globalAlpha = 1 - k; ring(l.x, l.y, 8 + k * 34, '#fff1b0', 2);
      }
      ctx.globalAlpha = 1;
    }
    for (const id of marks.working || []) {
      const l = byNode.get(id); if (!l) continue;
      const a = t * 0.004;
      ctx.globalCompositeOperation = 'lighter';
      for (let k = 0; k < 6; k++) {
        const x = l.x + Math.cos(a - k * 0.18) * 18, y = l.y + Math.sin(a - k * 0.18) * 18;
        ctx.globalAlpha = 1 - k * 0.15; ctx.drawImage(gold, x - 8, y - 8, 16, 16);
      }
      ctx.globalAlpha = 1; ctx.globalCompositeOperation = 'source-over';
    }
    const id = marks.selected;
    const leaf = id && byNode.get(id);
    const label = id && labelByNode.get(id);
    const x = leaf ? leaf.x : label?.box?.ex, y = leaf ? leaf.y : label?.box?.ey;
    if (x != null) {
      ring(x, y, 15, 'rgba(255,255,255,.95)', 2, [5, 4], reduced ? 0 : t * 0.0012);
      ring(x, y, 24 + (reduced ? 0 : Math.sin(t * 0.004) * 2), 'rgba(160,200,255,.45)', 1.2);
    }
  }

  function drawHover() {
    if (!hover) { tip.classList.remove('is-on'); return; }
    tip.textContent = hover.text;
    tip.style.transform = `translate(${Math.round((hover.x - par[0] * 6) * scale + 14)}px, ${Math.round((hover.y - par[1] * 4) * scale - 34)}px)`;
    tip.classList.add('is-on');
    if (hover.kind === 'leaf') {
      ctx.strokeStyle = 'rgba(255,255,255,.95)'; ctx.lineWidth = 1.6;
      ctx.beginPath(); ctx.arc(hover.x, hover.y, 13, 0, TAU); ctx.stroke();
    }
  }

  function draw(now) {
    const t = now - start;
    const dt = Math.min(64, Math.max(0, now - last));
    last = now;
    const grow = reduced ? 3 : t / 1000;
    if (mouse && !reduced) par = [par[0] + ((mouse[0] / W - 0.5) - par[0]) * 0.04, par[1] + ((mouse[1] / H - 0.5) - par[1]) * 0.04];
    const poses = pose(t);
    drawCrown(grow, poses);
    if (!reduced && grow > 1.2 && Math.random() < dt * 0.008) spawnPulse(poses);
    drawPulses(dt, poses);
    drawLeaves(t, grow, poses);
    drawLabels(grow, poses);
    ctx.setTransform(dpr * scale, 0, 0, dpr * scale, 0, 0);
    drawSky(t);
    drawLake(t);
    ctx.save();
    ctx.translate(par[0] * -6, par[1] * -4);
    ctx.drawImage(crown, 0, 0);
    drawMarks(t);
    drawHover();
    ctx.restore();
    drawCritters(t, dt);
  }

  function loop(now) {
    if (!wrap.isConnected) { cancelAnimationFrame(raf); raf = 0; ro.disconnect(); return; }
    if (!document.hidden) draw(now);
    raf = requestAnimationFrame(loop);
  }

  function hit(px, py) {
    let best = null, bd = 16;
    for (const l of model.leaves) {
      const d = Math.hypot(l.x - px, l.y - py);
      if (d < bd) { bd = d; best = { kind: 'leaf', node: l.node, x: l.x, y: l.y, text: `${l.node.label}` }; }
    }
    if (best) return best;
    for (const lb of model.labels) {
      const b = lb.box;
      if (b && px >= b.x && px <= b.x + b.w && py >= b.y && py <= b.y + b.h) {
        return { kind: 'label', node: lb.node, x: b.ex, y: b.ey, text: lb.node.label };
      }
    }
    return null;
  }

  const toWorld = (e) => {
    const r = canvas.getBoundingClientRect();
    return [(e.clientX - r.left) / scale + par[0] * 6, (e.clientY - r.top) / scale + par[1] * 4];
  };
  canvas.addEventListener('mousemove', (e) => {
    const [x, y] = toWorld(e);
    mouse = [x, y];
    hover = hit(x, y);
    canvas.style.cursor = hover ? 'pointer' : 'default';
    if (onHover) onHover(hover && hover.node);
    if (reduced) draw(performance.now());
  });
  canvas.addEventListener('mouseleave', () => { hover = null; mouse = null; if (reduced) draw(performance.now()); });
  canvas.addEventListener('click', (e) => {
    const [x, y] = toWorld(e);
    const h = hit(x, y);
    if (h && onPick) onPick(h.node);
  });

  const ro = new ResizeObserver(() => resize());
  ro.observe(wrap);
  requestAnimationFrame(() => {
    resize();
    if (reduced) draw(performance.now());
    else raf = requestAnimationFrame(loop);
  });

  return {
    el: wrap,
    update(next) { marks = { ...marks, ...next }; if (reduced) draw(performance.now()); },
    /** Automation hook: the same path as a click on that leaf or family label. */
    pick(id) { const l = byNode.get(id) || labelByNode.get(id); if (l && onPick) onPick(l.node); return !!l; },
    destroy() { ro.disconnect(); cancelAnimationFrame(raf); },
  };
}
