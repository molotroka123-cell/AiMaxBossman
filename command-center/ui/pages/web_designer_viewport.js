/* Responsive preview settings only. Never transforms or saves project HTML. */
export const VIEWPORT_PRESETS = Object.freeze([
  Object.freeze({ id: 'desktop', label: 'ПК · 1440 × 900', width: 1440, height: 900 }),
  Object.freeze({ id: 'laptop', label: 'Ноутбук · 1280 × 800', width: 1280, height: 800 }),
  Object.freeze({ id: 'tablet', label: 'Планшет · 768 × 1024', width: 768, height: 1024 }),
  Object.freeze({ id: 'mobile', label: 'Телефон · 390 × 844', width: 390, height: 844 }),
]);
export const VIEWPORT_ZOOMS = Object.freeze([0.25, 0.5, 0.75, 1, 1.25, 1.5, 2]);
export const VIEWPORT_LIMITS = Object.freeze({ min: 240, max: 4096 });

export function viewportSettings(width = 1440, height = 900, zoom = 'fit') {
  for (const value of [width, height]) {
    if (!Number.isSafeInteger(value) || value < VIEWPORT_LIMITS.min || value > VIEWPORT_LIMITS.max) {
      throw new RangeError('Ширина и высота: целые числа от 240 до 4096 пикселей');
    }
  }
  if (zoom !== 'fit' && zoom !== 'width' && !VIEWPORT_ZOOMS.includes(zoom)) {
    throw new RangeError('Выберите доступный масштаб или «Вписать»');
  }
  return Object.freeze({ schemaVersion: 1, width, height, zoom });
}

export function serializeViewport(settings) {
  return JSON.stringify(parseViewport(JSON.stringify(settings)));
}

export function parseViewport(serialized) {
  if (typeof serialized !== 'string' || serialized.length > 512) throw new TypeError('Invalid viewport settings');
  const value = JSON.parse(serialized);
  if (!value || Array.isArray(value) || typeof value !== 'object' || value.schemaVersion !== 1
      || Object.keys(value).sort().join(',') !== 'height,schemaVersion,width,zoom') {
    throw new TypeError('Unknown viewport settings schema');
  }
  return viewportSettings(value.width, value.height, value.zoom);
}

export function viewportStorageKey(projectId) {
  if (!Number.isSafeInteger(projectId) || projectId < 1) throw new TypeError('Invalid project identity');
  return `bd.viewport.project.${projectId}`;
}

export function loadViewport(storage, projectId) {
  try {
    const value = storage.getItem(viewportStorageKey(projectId));
    return value === null ? viewportSettings() : parseViewport(value);
  } catch {
    // Unavailable/private storage or a future/corrupt schema cannot break the editor.
    return viewportSettings();
  }
}

export function saveViewport(storage, projectId, settings) {
  // Validate before writing; malformed state leaves the last accepted value intact.
  const key = viewportStorageKey(projectId);
  const serialized = serializeViewport(settings);
  try {
    storage.setItem(key, serialized);
    return true;
  } catch {
    return false;
  }
}

export function viewportGeometry(settings, availableWidth, availableHeight) {
  const { width, height, zoom } = viewportSettings(settings.width, settings.height, settings.zoom);
  if (![availableWidth, availableHeight].every((v) => Number.isFinite(v) && v > 0)) {
    throw new RangeError('Viewport container must have positive finite dimensions');
  }
  const scale = zoom === 'fit' ? Math.min(1, availableWidth / width, availableHeight / height)
    : zoom === 'width' ? Math.min(1, availableWidth / width) : zoom;
  return Object.freeze({ width, height, scale, renderedWidth: width * scale, renderedHeight: height * scale });
}
