/* ============================================================
   chat/sphere.js — знак Bossman: живая сфера.

   Чистый CSS (градиенты и один вращающийся блик в chat.css), без внешних
   файлов. Состояния: idle | thinking | streaming | error. При
   prefers-reduced-motion сфера статична (правило в chat.css).
   ============================================================ */

import { h } from './dom.js';

export const SPHERE_STATES = ['idle', 'thinking', 'streaming', 'error'];

export function sphere(size = 32, state = 'idle', label = 'Bossman') {
  return h('span.orb', {
    role: 'img',
    'aria-label': label,
    dataset: { state: SPHERE_STATES.includes(state) ? state : 'idle' },
    style: { '--orb-size': `${size}px` },
  }, h('span.orb-core'), h('span.orb-glint'));
}

export function setSphereState(el, state) {
  if (el && el.dataset) el.dataset.state = SPHERE_STATES.includes(state) ? state : 'idle';
}
