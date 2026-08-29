export function bearer(token) {
  const clean = String(token || "").trim();
  if (!clean) throw new Error("Missing session token");
  return { Authorization: `Bearer ${clean}` };
}

export function formatTime(value) {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString();
}

export class SSEParser {
  constructor(onEvent) {
    this.onEvent = onEvent;
    this.buffer = "";
  }
  push(chunk) {
    this.buffer += chunk.replace(/\r\n/g, "\n");
    let idx;
    while ((idx = this.buffer.indexOf("\n\n")) >= 0) {
      const block = this.buffer.slice(0, idx);
      this.buffer = this.buffer.slice(idx + 2);
      this.#emit(block);
    }
  }
  #emit(block) {
    if (!block || block.startsWith(":")) return;
    let event = "message";
    const data = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }
    if (!data.length) return;
    const raw = data.join("\n");
    let parsed = raw;
    try { parsed = JSON.parse(raw); } catch (_) {}
    this.onEvent({ event, data: parsed });
  }
}

export function can(scopeSet, scope) {
  return scopeSet instanceof Set && scopeSet.has(scope);
}
