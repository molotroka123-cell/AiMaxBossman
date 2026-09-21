/* Opt-in, in-memory transport evidence. Never retain URL/query/body/header/error.
   Not a browser-wide recorder, click inference, distributed trace or new timer. */
let enabled = false, epoch = 0, serial = 0, rows = [], contracts = [];
const LIMIT = 120;
const ORIGINS = new Set(['VideoStudio.command.dry_run','VideoStudio.command.apply','Studio.shotRecipes','Observatory.snapshot']);
const sensitive = /(?:auth|login|logout|token|secret|provider|credential|egress)/i;
const escape = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
export function setTraceRoutes(routes = []) {
  contracts = routes.slice(0, 2000).filter(r => typeof r.path === 'string' &&
    r.path.startsWith('/api/') && r.path.length < 240 && !sensitive.test(r.path))
    .map(r => ({path: r.path, methods: Array.isArray(r.methods) ? r.methods : [],
      pattern: new RegExp('^' + r.path.split(/(\{[^}]+\})/).map(x => x.startsWith('{') ? '[^/]+' : escape(x)).join('') + '$')}));
}
export function setTraceEnabled(value) { enabled = value === true; epoch += 1; rows = []; }
export function clearTrace() { epoch += 1; rows = []; }
export function resetTraceSession() { setTraceEnabled(false); contracts = []; }
export function traceSnapshot() { return {enabled, limit: LIMIT, scope: 'THIS_TAB_CANONICAL_API_ONLY', rows: rows.map(r => ({...r}))}; }
export function traceBegin(method, path, origin) {
  if (!enabled) return null;
  // The raw value exists only during matching. Unknown routes become a literal.
  const pathname = typeof path === 'string' ? path.split(/[?#]/, 1)[0] : '';
  if (!pathname.startsWith('/api/') || sensitive.test(pathname)) return null;
  const found = contracts.find(c => c.methods.includes(method) && c.pattern.test(pathname));
  return {epoch, method: ['GET','POST','PUT','PATCH','DELETE'].includes(method) ? method : 'OTHER',
    path: found?.path || '/api/:unmapped', ui_action: ORIGINS.has(origin) ? origin : 'NOT_INSTRUMENTED', start: performance.now()};
}
export function traceEnd(ticket, status = null, outcome = 'UNKNOWN') {
  if (!ticket || !enabled || ticket.epoch !== epoch) return;
  const elapsed = performance.now() - ticket.start;
  rows.push({id: ++serial, method: ticket.method, path: ticket.path,
    status: Number.isInteger(status) && status >= 100 && status <= 599 ? status : null,
    duration_ms: Number.isFinite(elapsed) && elapsed >= 0 ? Math.round(elapsed * 10) / 10 : null,
    outcome: ['HTTP_OK','HTTP_ERROR','ABORTED','TRANSPORT_ERROR'].includes(outcome) ? outcome : 'UNKNOWN',
    ui_action: ticket.ui_action, service_span: 'NOT_CAPTURED'});
  if (rows.length > LIMIT) rows.splice(0, rows.length-LIMIT);
}
