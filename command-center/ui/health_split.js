/* Разделение сводного статуса топбара на «Ядро» и «Опциональные адаптеры».
   Ядро — то, без чего сервер не работает (БД, очередь, планировщик, метрики, фоновые tick:*).
   Опциональные — внешние провайдеры и модели, браузер: отсутствие ключа или оффлайн-модель не должны красить
   живой локальный сервер в «сбой». Неизвестный компонент считается ЯДРОМ (no-fake-green: не прячем новое).
   Пустой список компонентов — предупреждение, а не зелёный. */

const OPTIONAL = /^(browser|models|providers|provider:\d+)$/;
const BAD = ['down', 'error', 'failed', 'false', 'offline'];
const WARN = ['degraded', 'warning', 'warn', 'unknown', 'empty', 'stale', 'starting', 'stopped'];

export function healthEntries(rawHealth) {
  if (Array.isArray(rawHealth)) {
    return rawHealth.map((c, i) => [String((c && (c.name || c.id)) ?? i), String((c && (c.status || c.state)) || 'unknown')]);
  }
  return Object.entries(rawHealth || {}).map(([k, v]) =>
    [k, v && typeof v === 'object' ? String(v.status || v.state || 'unknown') : String(v)]);
}

export function splitHealth(rawHealth) {
  const core = { bad: [], warn: [], total: 0 };
  const optional = { notReady: [], total: 0 };
  for (const [name, status] of healthEntries(rawHealth)) {
    const s = status.toLowerCase();
    if (OPTIONAL.test(name)) {
      optional.total += 1;
      if (s !== 'ok') optional.notReady.push(name);
      continue;
    }
    core.total += 1;
    if (BAD.includes(s)) core.bad.push(name);
    else if (WARN.includes(s)) core.warn.push(name);
  }
  const overall = core.bad.length ? 'err' : (core.warn.length || core.total === 0) ? 'warn' : 'ok';
  return { overall, core, optional };
}
