/* ============================================================
   chat/scroll.js — лента «прилипает» к низу по намерению владельца (P4).

   Раньше каждый кадр стриминга проверял «до низа меньше 120 px» и тянул
   ленту вниз: читающего чуть выше выдёргивало назад, а дальше 120 px не
   было ни знака, что пришёл новый текст, ни способа вернуться одним
   нажатием. Теперь:
   - колесо вверх, PageUp / ArrowUp / Home (не в поле ввода) или ручная
     прокрутка вверх (scrollTop уменьшился не нашим кодом) — «отлипнуть»;
   - до низа ≤ 4 px — снова «прилипнуть», но не пока владелец сам крутит
     вверх (медленная тачпад-прокрутка сдвигает меньше 4 px за кадр, и
     иначе стрим тут же тянул бы её назад); сжатие ленты (браузер сам
     уменьшил scrollTop) — не намерение, у низа снова прилипаем;
   - пока прилипли, ResizeObserver держит низ при росте ленты (и поздние
     поправки размеров content-visibility);
   - кнопка «К последнему сообщению (End)» видна, пока не прилипли; data-new=1,
     если с тех пор лента выросла.

   nextStuck() — чистая функция (проверяется в node); createStick() трогает
   DOM только при вызове.
   ============================================================ */

/** Насколько близко к низу считается «внизу», px. */
export const STICK_EPSILON = 4;

const UNSTICK_KEYS = new Set(['PageUp', 'ArrowUp', 'Home']);

/**
 * Следующее состояние «прилипли к низу».
 * evt: {type:'wheel', deltaY} | {type:'key', key, inInput}
 *    | {type:'scroll', scrollTop, prevTop, scrollHeight, clientHeight, programmatic, userUp}
 *    | {type:'jump'}
 * userUp — только что было колесо вверх или клавиша вверх (намерение владельца).
 */
export function nextStuck(prev, evt) {
  if (!evt || typeof evt !== 'object') return Boolean(prev);
  switch (evt.type) {
    case 'wheel':
      return Number(evt.deltaY) < 0 ? false : Boolean(prev);
    case 'key':
      if (evt.inInput) return Boolean(prev);
      return UNSTICK_KEYS.has(evt.key) ? false : Boolean(prev);
    case 'scroll': {
      const gap = Number(evt.scrollHeight) - Number(evt.scrollTop) - Number(evt.clientHeight);
      const movedUp = Number(evt.scrollTop) < Number(evt.prevTop);
      if (movedUp && evt.userUp && !evt.programmatic) return false;
      if (Number.isFinite(gap) && gap <= STICK_EPSILON) return true;
      if (!evt.programmatic && movedUp) return false;
      return Boolean(prev);
    }
    case 'jump':
      return true;
    default:
      return Boolean(prev);
  }
}

function isTypingTarget(target) {
  if (!target) return false;
  const tag = String(target.tagName || '').toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select' || Boolean(target.isContentEditable);
}

/**
 * Прилипание для прокручиваемого `scroller` с содержимым `list`.
 * onChange({stuck, grew}) — после каждого изменения (показать/спрятать кнопку).
 * reducedMotion() — true, если плавная прокрутка запрещена настройкой системы.
 */
export function createStick(scroller, list, { onChange = () => {}, reducedMotion = () => false } = {}) {
  const st = { stuck: true, grew: false, lastTop: scroller.scrollTop, lastHeight: scroller.scrollHeight, progUntil: 0, userUpAt: -Infinity };
  const now = () => (typeof performance !== 'undefined' && performance.now ? performance.now() : Date.now());
  const emit = () => { try { onChange({ stuck: st.stuck, grew: st.grew }); } catch { /* наблюдатель не ломает ленту */ } };
  const set = (value) => {
    if (value === st.stuck) return;
    st.stuck = value;
    if (value) st.grew = false;
    emit();
  };

  function toBottom({ smooth = false } = {}) {
    const useSmooth = smooth && !reducedMotion() && typeof scroller.scrollTo === 'function';
    if (useSmooth) {
      /* промежуточные события плавной прокрутки — наши, а не владельца */
      st.progUntil = now() + 800;
      scroller.scrollTo({ top: scroller.scrollHeight, behavior: 'smooth' });
    } else {
      scroller.scrollTop = scroller.scrollHeight;
      /* следующий ручной сдвиг вверх сравнивается с этой позицией, а не с прошлой */
      st.lastTop = scroller.scrollTop;
    }
    st.lastHeight = scroller.scrollHeight;
    set(nextStuck(st.stuck, { type: 'jump' }));
    if (st.grew) { st.grew = false; emit(); }
  }

  /** После отрисовки: прилипли — держим низ; нет — отмечаем, что пришло новое. */
  function follow() {
    const height = scroller.scrollHeight;
    const gap = height - scroller.scrollTop - scroller.clientHeight;
    if (st.stuck) {
      /* пока идёт плавный прыжок кнопкой — не обрываем его мгновенным */
      if (gap > 0 && now() >= st.progUntil) toBottom();
    } else if (gap <= STICK_EPSILON && now() - st.userUpAt >= 300) {
      /* лента снова у низа (сжалась, новый пустой чат) — прилипаем, даже без события прокрутки;
         но не сразу после колеса/клавиши вверх: владелец как раз уходит от низа */
      set(true);
    } else if (height > st.lastHeight && !st.grew) {
      st.grew = true;
      emit();
    }
    st.lastHeight = height;
  }

  scroller.addEventListener('wheel', (e) => {
    if (e.deltaY < 0) st.userUpAt = now();
    set(nextStuck(st.stuck, { type: 'wheel', deltaY: e.deltaY }));
  }, { passive: true });
  scroller.addEventListener('keydown', (e) => {
    const evt = { type: 'key', key: e.key, inInput: isTypingTarget(e.target) };
    if (!evt.inInput && UNSTICK_KEYS.has(e.key)) st.userUpAt = now();
    set(nextStuck(st.stuck, evt));
  });
  scroller.addEventListener('scroll', () => {
    const top = scroller.scrollTop;
    const value = nextStuck(st.stuck, {
      type: 'scroll', scrollTop: top, prevTop: st.lastTop, scrollHeight: scroller.scrollHeight,
      clientHeight: scroller.clientHeight, programmatic: now() < st.progUntil, userUp: now() - st.userUpAt < 300,
    });
    st.lastTop = top;
    set(value);
  }, { passive: true });

  if (typeof ResizeObserver === 'function') {
    const ro = new ResizeObserver(() => follow());
    ro.observe(list);
    ro.observe(scroller);
  }

  return {
    get stuck() { return st.stuck; },
    get grew() { return st.grew; },
    toBottom,
    follow,
  };
}
