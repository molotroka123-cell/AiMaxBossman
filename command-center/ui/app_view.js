/* Keep an embedded app's browsing context while refreshing its launcher state.
   Moving an iframe, even back to the same parent, unloads its document. */
export function retainAppFrame(container, next) {
  const previous = container.firstElementChild;
  const appId = next?.dataset?.appId;
  if (!appId || previous?.dataset?.appId !== appId) return false;
  const currentFrame = previous.querySelector('.bx-appview-frame iframe');
  const nextFrame = next.querySelector('.bx-appview-frame iframe');
  const currentHead = previous.querySelector('.bx-appview-head');
  const nextHead = next.querySelector('.bx-appview-head');
  if (!currentFrame || !nextFrame || !currentHead || !nextHead) return false;
  // A changed target or sandbox starts a new context; never retain a frame
  // for a different process endpoint or with changed origin permissions.
  for (const name of ['src', 'sandbox', 'title']) {
    if (currentFrame.getAttribute(name) !== nextFrame.getAttribute(name)) return false;
  }
  currentHead.replaceWith(nextHead);
  return true;
}
