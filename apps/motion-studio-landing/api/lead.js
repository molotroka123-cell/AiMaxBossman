// Vercel serverless function: forwards a landing-page lead to the owner's private Telegram chat.
// Required project env vars (never commit values): TG_BOT_TOKEN, TG_CHAT_ID.
"use strict";

const LIMITS = { name: 100, business: 120, email: 160, phone: 40, link: 300, message: 1500 };
const WINDOW_MS = 10 * 60 * 1000;
const MAX_PER_WINDOW = 5;
const hits = new Map(); // in-memory, per warm instance (best effort)

function clientIp(req) {
  const xff = String(req.headers["x-forwarded-for"] || "").split(",")[0].trim();
  return xff || String(req.headers["x-real-ip"] || "") || "unknown";
}

function rateLimited(ip) {
  const now = Date.now();
  if (hits.size > 5000) hits.clear();
  const arr = (hits.get(ip) || []).filter((t) => now - t < WINDOW_MS);
  arr.push(now);
  hits.set(ip, arr);
  return arr.length > MAX_PER_WINDOW;
}

function clean(v, max) {
  if (typeof v !== "string") return "";
  // strip control chars except newline/tab
  return v.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, "").trim().slice(0, max + 1);
}

function send(res, status, body) {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Cache-Control", "no-store");
  res.end(JSON.stringify(body));
}

module.exports = async function handler(req, res) {
  if (req.method !== "POST") {
    res.setHeader("Allow", "POST");
    return send(res, 405, { ok: false, error: "Method not allowed" });
  }
  const ctype = String(req.headers["content-type"] || "");
  if (!ctype.includes("application/json")) return send(res, 415, { ok: false, error: "Unsupported content type" });

  const ip = clientIp(req);
  if (rateLimited(ip)) return send(res, 429, { ok: false, error: "Příliš mnoho pokusů. Zkuste to prosím později." });

  let body = req.body;
  if (typeof body === "string") {
    if (body.length > 10000) return send(res, 413, { ok: false, error: "Too large" });
    try { body = JSON.parse(body); } catch (_) { body = null; }
  }
  if (!body || typeof body !== "object") return send(res, 400, { ok: false, error: "Neplatný požadavek." });

  // Honeypot / too-fast submit: pretend success, drop silently.
  if ((typeof body.website === "string" && body.website.trim() !== "") || Number(body.elapsedMs) < 2500) {
    return send(res, 200, { ok: true });
  }

  const f = {};
  for (const k of Object.keys(LIMITS)) {
    f[k] = clean(body[k], LIMITS[k]);
    if (f[k].length > LIMITS[k]) return send(res, 400, { ok: false, error: "Některé pole je příliš dlouhé." });
  }
  if (!f.name || !f.business) return send(res, 400, { ok: false, error: "Vyplňte jméno a název firmy." });
  if (!f.email && !f.phone) return send(res, 400, { ok: false, error: "Vyplňte e-mail nebo telefon." });
  if (f.email && !/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(f.email)) return send(res, 400, { ok: false, error: "E-mail nemá správný tvar." });
  if (f.phone && !/^[+0-9 ()-]{9,20}$/.test(f.phone)) return send(res, 400, { ok: false, error: "Telefon nemá správný tvar." });
  if (body.consent !== true) return send(res, 400, { ok: false, error: "Chybí souhlas se zpracováním údajů." });

  const token = process.env.TG_BOT_TOKEN;
  const chatId = process.env.TG_CHAT_ID;
  if (!token || !chatId) return send(res, 503, { ok: false, error: "Formulář je dočasně mimo provoz." });

  const text = [
    "Motion Studio – nová poptávka",
    "Jméno: " + f.name,
    "Firma: " + f.business,
    f.email ? "E-mail: " + f.email : null,
    f.phone ? "Telefon: " + f.phone : null,
    f.link ? "Odkaz: " + f.link : null,
    f.message ? "Zpráva: " + f.message : null,
    "Souhlas GDPR: ano",
    "Čas: " + new Date().toISOString(),
  ].filter(Boolean).join("\n");

  try {
    const r = await fetch("https://api.telegram.org/bot" + token + "/sendMessage", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: chatId, text, disable_web_page_preview: true }),
    });
    if (!r.ok) {
      console.error("telegram send failed", r.status);
      return send(res, 502, { ok: false, error: "Odeslání se nepovedlo. Zkuste to prosím znovu za chvíli." });
    }
  } catch (e) {
    console.error("telegram send error", e && e.name);
    return send(res, 502, { ok: false, error: "Odeslání se nepovedlo. Zkuste to prosím znovu za chvíli." });
  }
  return send(res, 200, { ok: true });
};
