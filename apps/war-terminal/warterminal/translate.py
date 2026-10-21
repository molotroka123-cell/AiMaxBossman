"""Machine translation of foreign-language notice text, ORIGINAL and TRANSLATION kept strictly apart.

Rules (owner, 18.10): the original text is never overwritten or merged with a translation; a record carries its own language,
the machine flag, the source hash and the model that produced it; nothing is reported as 'translated' unless a translation exists;
a text that is already in the target language, or comes back unchanged, is NOT a translation. Names of organisations, CPV codes,
amounts, currencies, URLs, e-mails, phones and product designations are masked before the text leaves the machine and are restored
byte-for-byte afterwards (a reply that loses or duplicates a mask is rejected).

Models: ONLY free Bossman routes (price 0/0 + a free-tier flag, never local, never paid). The terminal asks Bossman for the model list
and sends each batch through Bossman's free-only gateway, so provider keys never reach this process. Every reply is checked for
usage.cost: a cost above zero drops the reply and halts translation for the rest of the UTC day.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from datetime import datetime, timezone

LANGS = {"ru": "русский", "en": "English"}
MAX_PER_CALL = 60                 # strings per API call (owner limit)
MAX_PER_DAY = 300                 # fresh model translations per UTC day without a new owner click
BATCH_STRINGS = 20                # strings per model call
BATCH_CHARS = 5000
MAX_TEXT = 1200                   # one notice title; longer text is cut for translation and flagged
MAX_ATTEMPTS = 4                  # routes tried for one batch
PROMPT_VERSION = 1


def norm(text: str) -> str:
    return unicodedata.normalize("NFC", str(text or "")).strip()


def source_sha(text: str) -> str:
    return hashlib.sha256(norm(text).encode("utf-8")).hexdigest()


def cache_key(text: str, target: str) -> str:
    return f"tr:{target}:{source_sha(text)}"


# ------------------------------------------------------------------ language detection (heuristic, tags only)
_WORDS = {
    "en": "the of and for to in on with supply delivery services service purchase procurement contract works fuel equipment maintenance framework agreement",
    "cs": "a pro na dodávka dodávky služby služeb nákup zajištění paliva poskytování provoz údržba oprava rámcová smlouva veřejná zakázka se ve k",
    "sk": "a pre na dodávka dodávky služby nákup zabezpečenie paliva poskytovanie prevádzka údržba oprava zákazka",
    "de": "und der die das für von mit lieferung dienstleistungen beschaffung wartung betrieb rahmenvereinbarung kraftstoff ausschreibung",
    "pl": "i oraz dla na w dostawa dostawy usługi zakup zakupu paliwa utrzymanie umowa ramowa zamówienie",
    "fr": "et de des la le les pour fourniture services achat marché entretien carburant accord-cadre",
    "es": "y de del la el los para suministro servicios compra contrato mantenimiento combustible",
    "it": "e di del della il per fornitura servizi acquisto appalto manutenzione carburante",
    "ru": "и для на по поставка поставки услуги закупка топлива обслуживание договор",
}
_WORDSET = {k: set(v.split()) for k, v in _WORDS.items()}
_DIAC = {"cs": "ěščřžůďťň", "sk": "ľĺŕôä", "pl": "ąćęłńśźż", "de": "äöüß", "fr": "âçèêëîïôùûœ", "es": "ñ¿¡", "it": "ìù", "hu": "őű"}   # letters distinctive for one language


def detect_lang(text: str) -> str | None:
    """ISO-639-1 guess for short notice titles, or None when unsure. A tag for the reader, not a verdict."""
    t = norm(text).lower()
    letters = [c for c in t if c.isalpha()]
    if len(letters) < 3:
        return None
    cyr = sum(1 for c in letters if "а" <= c <= "я" or c in "ёіїєґ")
    if cyr / len(letters) > 0.4:
        return "uk" if any(c in t for c in "іїєґ") else "ru"
    words = re.findall(r"[^\W\d_]+(?:-[^\W\d_]+)*", t)
    scores: dict[str, float] = {}
    for lang, ws in _WORDSET.items():
        if lang == "ru":
            continue
        scores[lang] = sum(1 for w in words if w in ws) + 1.5 * sum(1 for c in t if c in _DIAC.get(lang, ""))
    if not any(c in t for c in "ěščřžýůďťňąćęłńóśźżäöüßľĺŕôçñ") and not any(c > "\x7f" for c in t):
        scores["en"] = scores.get("en", 0) + 0.5
    best = sorted(scores.items(), key=lambda kv: -kv[1])
    if not best or best[0][1] < 1.5:
        return None
    if len(best) > 1 and best[0][1] - best[1][1] < 1:
        return None
    return best[0][0]


# ------------------------------------------------------------------ protected tokens
_LEGAL = r"(?:s\.\s?r\.\s?o\.|a\.\s?s\.|spol\.\s?s\s?r\.\s?o\.|GmbH(?:\s?&\s?Co\.\s?KG)?|AG|SE|S\.A\.|S\.p\.A\.|Sp\.\s?z\s?o\.o\.|S\.A\.S\.|B\.V\.|N\.V\.|Ltd\.?|LLC|Inc\.?|PLC|ООО|АО|ПАО)"
_PROTECT = [
    re.compile(r"https?://[^\s<>\"']*[^\s<>\"'.,;:)]", re.I),
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    re.compile(r"\+?\d[\d\s().-]{8,}\d"),
    re.compile(r"\b\d{8}(?:-\d)?\b"),                                                      # CPV
    re.compile(r"(?<![\w])(?:[A-ZÀ-Ž][\w&'’.-]*,?\s+){1,5}" + _LEGAL + r"(?![\w])"),          # organisation + legal form
    re.compile(r"(?<![\w])(?:EUR|CZK|USD|PLN|GBP|CHF|HUF|RON|SEK|DKK|NOK|RUB|UAH|Kč|€|\$|£)\s?\d[\d\s.,]*\d|\d[\d\s.,]*\d\s?(?:EUR|CZK|USD|PLN|GBP|CHF|HUF|RON|SEK|DKK|NOK|RUB|UAH|Kč|€|\$|£)(?![\w])"),
    re.compile(r"\b[A-ZА-Я]{1,6}[-/ ]?\d[\w/.-]*\b"),                                       # designations: T-72, ISO 9001, A4, EN 1234
    re.compile(r"\b\d+(?:[.,]\d+)?(?:\s?(?:mm|cm|m|km|kg|g|t|l|ml|kV|V|W|kW|MW|Hz|%))\b"),
    re.compile(r"\b\d[\d.,/]*\b"),
]
_MASK = re.compile(r"⟦(\d+)⟧")


def protect(text: str) -> tuple[str, list[str]]:
    """Replace untranslatable spans with ⟦n⟧ and return the saved spans."""
    saved: list[str] = []
    out = norm(text)
    def sub(m):
        saved.append(m.group(0))
        return f"⟦{len(saved) - 1}⟧"
    for rx in _PROTECT:
        parts = re.split(r"(⟦\d+⟧)", out)                    # never re-mask inside an existing mask
        out = "".join(p if i % 2 else rx.sub(sub, p) for i, p in enumerate(parts))
    return out, saved


def restore(text: str, saved: list[str]) -> str | None:
    """Put the spans back. None when a mask was lost, duplicated or invented (the reply is then not trusted)."""
    seen = [int(i) for i in _MASK.findall(text)]
    if sorted(seen) != list(range(len(saved))):
        return None
    return _MASK.sub(lambda m: saved[int(m.group(1))], text)


# ------------------------------------------------------------------ free route selection
BAD_NAME = re.compile(r"code|coder|codestral|starcoder|vision|diffusion|embed|guard|reward|image|audio|tts|whisper", re.I)
PROVIDER_ORDER = ["experiential_labs_free_plan", "howtofix", "groq", "google_ai_studio", "nvidia_nim"]
PREFERRED_IDS = (100, 101)        # owner: Experiential Labs free-plan ids first (usage.cost verified 0.0 on 18.10)


def is_free_route(m: dict) -> bool:
    """A model Bossman lists as free by its own record: price 0/0, a free-tier flag, not marked paid, not local, not broken."""
    caps = m.get("caps") or {}
    pi, po = m.get("price_in"), m.get("price_out")
    zero = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v == 0     # noqa: E731
    if not (zero(pi) and zero(po)) or not caps.get("free_tier") or caps.get("paid"):
        return False
    if m.get("kind") == "local" or m.get("status") in ("error", "unavailable", "offline", "disabled"):
        return False
    if caps.get("chat") is False or BAD_NAME.search(str(m.get("name") or m.get("alias") or "")):
        return False
    return True


def free_routes(models: list[dict], limit: int = 6) -> list[dict]:
    """Ordered fallback chain: owner-preferred ids, then free tiers by provider preference, online before unknown."""
    cands = [m for m in models if is_free_route(m)]

    def group(m):
        ft = str((m.get("caps") or {}).get("free_tier"))
        if ft == "True":
            ft = "howtofix"
        return PROVIDER_ORDER.index(ft) if ft in PROVIDER_ORDER else len(PROVIDER_ORDER)

    def key(m):
        if m.get("id") in PREFERRED_IDS:
            return (0, PREFERRED_IDS.index(m["id"]), 0, 0)
        riva = 0 if "riva-translate" in str(m.get("name")) else 1
        return (1, group(m), 0 if m.get("status") == "online" else 1, riva, m.get("id") or 0)
    return sorted(cands, key=key)[:limit]


class Bucket:
    """Small per-provider token bucket: `rate` tokens/second up to `cap`."""

    def __init__(self, rate: float = 0.5, cap: float = 2.0, clock=time.monotonic):
        self.rate, self.cap, self.clock = rate, cap, clock
        self.tokens, self.t = cap, clock()

    def wait_time(self) -> float:
        now = self.clock()
        self.tokens = min(self.cap, self.tokens + (now - self.t) * self.rate)
        self.t = now
        return 0.0 if self.tokens >= 1 else (1 - self.tokens) / self.rate

    def take(self) -> None:
        self.wait_time()
        self.tokens -= 1


class CostHalt(Exception):
    """A free route reported usage.cost > 0: the reply is dropped and translation stops."""


class RouteError(Exception):
    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.status = status


# ------------------------------------------------------------------ prompt + parsing
def build_messages(items: list[tuple[int, str]], target: str) -> list[dict]:
    system = (f"You translate short public-procurement notice titles into {LANGS[target]}. Source languages vary (Czech, German, Polish, English, "
              "Russian, ...). Rules: translate meaning only; keep every token like ⟦3⟧ exactly as is, once, in the right place; never translate or "
              "transliterate names of organisations or people, product designations, codes, amounts or URLs; do not add, explain or shorten. "
              "If a text is already in the target language return it unchanged. Reply with ONE JSON object and nothing else: "
              "{\"items\":[{\"i\":<number>,\"l\":\"<source language ISO 639-1>\",\"t\":\"<translation>\"}]}.")
    user = json.dumps({"target": target, "items": [{"i": i, "t": t} for i, t in items]}, ensure_ascii=False)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse_reply(text: str) -> dict[int, tuple[str | None, str]]:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("model reply has no JSON")
    data = json.loads(m.group(0))
    out: dict[int, tuple[str | None, str]] = {}
    for it in data.get("items") or []:
        try:
            out[int(it["i"])] = ((str(it.get("l") or "").lower()[:3] or None), str(it["t"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Translator:
    """store: has kv_get/kv_set. bossman: object with models() -> list[dict] and free_chat(model_id, messages, max_tokens) -> dict
    {text, model, usage:{cost,...}} (raises RouteError). None = gateway not configured."""

    def __init__(self, store, bossman, *, sleep=time.sleep, clock=time.monotonic):
        self.store, self.bossman, self.sleep, self.clock = store, bossman, sleep, clock
        self.buckets: dict[str, Bucket] = {}
        self.cooldown: dict[int, float] = {}

    # ---- state
    def status(self) -> dict:
        day = _day()
        st = self.store.kv_get(f"tr_stats:{day}") or {"calls": 0, "fresh": 0, "by_model": {}, "cost_total": 0.0}
        halt = self.store.kv_get(f"tr_halt:{day}")
        return {"configured": self.bossman is not None, "day": day, "limit_per_day": MAX_PER_DAY, "limit_per_call": MAX_PER_CALL,
                "used_today": st["fresh"], "calls_today": st["calls"], "models_used": st["by_model"], "cost_total_usd": st["cost_total"],
                "halted": bool(halt), "halt": halt, "targets": list(LANGS)}

    def _bump(self, fresh: int, model: str | None, cost: float | None) -> None:
        day = _day()
        st = self.store.kv_get(f"tr_stats:{day}") or {"calls": 0, "fresh": 0, "by_model": {}, "cost_total": 0.0}
        st["calls"] += 1
        st["fresh"] += fresh
        if model:
            st["by_model"][model] = st["by_model"].get(model, 0) + 1
        st["cost_total"] = round(st["cost_total"] + float(cost or 0), 6)
        self.store.kv_set(f"tr_stats:{day}", st)

    # ---- lookup (no model, no spend)
    def lookup(self, texts: list[str], target: str) -> list[dict]:
        return [self._cached_or_pending(t, target) for t in texts]

    def _cached_or_pending(self, text: str, target: str) -> dict:
        t = norm(text)
        base = {"source_sha256": source_sha(t), "target": target, "machine": True}
        if not t:
            return {**base, "status": "EMPTY", "source_lang": None}
        lang = detect_lang(t)
        if lang == target:
            return {**base, "status": "SAME_LANGUAGE", "source_lang": lang}
        hit = self.store.kv_get(cache_key(t, target))
        if hit:
            return {**base, **hit, "cached": True}
        return {**base, "status": "NOT_TRANSLATED", "source_lang": lang}

    # ---- translate
    def translate(self, texts: list[str], target: str) -> dict:
        if target not in LANGS:
            raise ValueError("unsupported target language")
        if len(texts) > MAX_PER_CALL:
            raise ValueError(f"не более {MAX_PER_CALL} текстов за вызов")
        recs = [self._cached_or_pending(t, target) for t in texts]
        day = _day()
        halt = self.store.kv_get(f"tr_halt:{day}")
        todo = [i for i, r in enumerate(recs) if r["status"] == "NOT_TRANSLATED"]
        # identical texts are sent once
        seen: dict[str, int] = {}
        uniq: list[int] = []
        for i in todo:
            h = recs[i]["source_sha256"]
            if h not in seen:
                seen[h] = i
                uniq.append(i)
        meta = {"fresh": 0, "models": [], "errors": []}
        if uniq and halt:
            self._mark(recs, uniq, "HALTED_COST", f"перевод остановлен: маршрут сообщил cost>0 ({halt.get('model')})")
        elif uniq and self.bossman is None:
            self._mark(recs, uniq, "NOT_CONNECTED", "шлюз Bossman не настроен (WAR_TERMINAL_BOSSMAN_URL / токен)")
        elif uniq:
            used = (self.store.kv_get(f"tr_stats:{day}") or {}).get("fresh", 0)
            room = max(0, MAX_PER_DAY - used)
            run, over = uniq[:room], uniq[room:]
            if over:
                self._mark(recs, over, "LIMIT", f"дневной лимит {MAX_PER_DAY} переводов исчерпан")
            self._run(recs, run, texts, target, meta)
        for i in todo:                                   # duplicates inherit the first one's record
            first = seen[recs[i]["source_sha256"]]
            if first != i:
                recs[i] = dict(recs[first])
        return {"items": recs, **meta, **{k: v for k, v in self.status().items() if k in ("used_today", "limit_per_day", "halted")}}

    @staticmethod
    def _mark(recs, idx, status, reason):
        for i in idx:
            recs[i] = {**recs[i], "status": status, "reason": reason}

    def _routes(self) -> list[dict]:
        try:
            return free_routes(self.bossman.models())
        except Exception as exc:                          # noqa: BLE001 - listing failure = honest NOT_CONNECTED
            raise RouteError(f"список моделей Bossman недоступен: {type(exc).__name__}") from exc

    def _run(self, recs, idx, texts, target, meta) -> None:
        try:
            routes = self._routes()
        except RouteError as exc:
            self._mark(recs, idx, "NOT_CONNECTED", str(exc))
            meta["errors"].append(str(exc))
            return
        if not routes:
            self._mark(recs, idx, "UNAVAILABLE", "нет доступных бесплатных маршрутов (price 0/0 + free tier) в Bossman")
            return
        batches: list[list[int]] = []
        cur: list[int] = []
        chars = 0
        for i in idx:
            n = min(len(norm(texts[i])), MAX_TEXT)
            if cur and (len(cur) >= BATCH_STRINGS or chars + n > BATCH_CHARS):
                batches.append(cur)
                cur, chars = [], 0
            cur.append(i)
            chars += n
        if cur:
            batches.append(cur)
        for b in batches:
            if self.store.kv_get(f"tr_halt:{_day()}"):
                self._mark(recs, b, "HALTED_COST", "перевод остановлен: cost>0")
                continue
            self._batch(recs, b, texts, target, routes, meta)

    def _batch(self, recs, b, texts, target, routes, meta) -> None:
        masked: dict[int, tuple[str, list[str]]] = {}
        for i in b:
            t = norm(texts[i])[:MAX_TEXT]
            masked[i] = protect(t)
        msgs = build_messages([(i, masked[i][0]) for i in b], target)
        errors: list[str] = []
        attempts = 0
        for route in routes:
            rid = route["id"]
            if self.cooldown.get(rid, 0) > self.clock() or attempts >= MAX_ATTEMPTS:
                continue
            bucket = self.buckets.setdefault(str(route.get("provider_id")), Bucket())
            w = bucket.wait_time()
            if w > 6:
                continue
            if w > 0:
                self.sleep(w)
            bucket.take()
            attempts += 1
            try:
                res = self.bossman.free_chat(rid, msgs, 3000)
                cost = ((res.get("usage") or {}).get("cost"))
                if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost > 0:
                    self._bump(0, route.get("alias"), cost)
                    self.store.kv_set(f"tr_halt:{_day()}", {"model": route.get("alias"), "cost": cost, "at": _now()})
                    raise CostHalt(f"{route.get('alias')}: usage.cost={cost}")
                parsed = parse_reply(res.get("text") or "")
            except CostHalt as exc:
                self._mark(recs, b, "HALTED_COST", f"маршрут сообщил cost>0, ответ отброшен, перевод остановлен ({exc})")
                meta["errors"].append(str(exc))
                return
            except (RouteError, ValueError, KeyError, json.JSONDecodeError) as exc:
                st = getattr(exc, "status", None)
                self.cooldown[rid] = self.clock() + (60 if st in (429, 502, 503, 504) or st is None else 300)
                errors.append(f"{route.get('alias')}: {str(exc)[:80] if isinstance(exc, RouteError) else type(exc).__name__}")
                continue
            alias = str(res.get("model") or route.get("alias"))
            done = 0
            for i in b:
                src_lang, tr = (parsed.get(i) or (None, ""))
                rec = self._finish(texts[i], masked[i], src_lang, tr, target, alias)
                recs[i] = {**recs[i], **rec}
                if rec["status"] == "TRANSLATED":
                    done += 1
                    self.store.kv_set(cache_key(texts[i], target), {k: rec[k] for k in ("status", "source_lang", "translation", "model", "created_at", "prompt_version")})
            self._bump(done, alias, cost if isinstance(cost, (int, float)) else 0)
            meta["fresh"] += done
            meta["models"].append(alias)
            return
        self._mark(recs, b, "UNAVAILABLE", "все бесплатные маршруты не ответили: " + "; ".join(errors)[:300] if errors else "нет свободных маршрутов (лимиты/пауза)")
        meta["errors"].extend(errors)

    @staticmethod
    def _finish(src: str, masked: tuple[str, list[str]], src_lang, tr: str, target: str, model: str) -> dict:
        text, saved = masked
        lang = detect_lang(src) or (src_lang if src_lang and src_lang.isalpha() else None)
        if not tr.strip():
            return {"status": "FAILED", "source_lang": lang, "reason": "пустой ответ модели"}
        back = restore(tr, saved)
        if back is None:
            return {"status": "FAILED", "source_lang": lang, "reason": "модель потеряла защищённые фрагменты, ответ отброшен"}
        if src_lang == target or lang == target or norm(back).casefold() == norm(src).casefold():
            return {"status": "SAME_LANGUAGE", "source_lang": lang or target}
        return {"status": "TRANSLATED", "source_lang": lang, "translation": norm(back), "machine": True, "model": model,
                "created_at": _now(), "prompt_version": PROMPT_VERSION}


# ------------------------------------------------------------------ Bossman gateway client
class BossmanGateway:
    """Read-only model list + free-only one-shot chat through the local Bossman backend. The token is read from a file and never logged."""

    def __init__(self, base_url: str, token_file: str, *, http=None, timeout: float = 120.0):
        self.base, self.token_file, self.timeout = base_url.rstrip("/"), token_file, timeout
        self.http = http

    def _headers(self) -> dict:
        try:
            tok = open(self.token_file, encoding="utf-8").read().strip()
        except OSError as exc:
            raise RouteError(f"токен Bossman не читается ({type(exc).__name__})") from exc
        return {"X-BCC-Token": tok}

    def _client(self):
        import httpx
        return self.http or httpx

    def models(self) -> list[dict]:
        r = self._client().get(self.base + "/api/models", headers=self._headers(), timeout=15)
        r.raise_for_status()
        return r.json()

    def free_chat(self, model_id: int, messages: list[dict], max_tokens: int) -> dict:
        import httpx
        try:
            r = self._client().post(self.base + "/api/war-terminal/free-chat", headers=self._headers(), timeout=self.timeout,
                                    json={"model_id": model_id, "messages": messages, "max_tokens": max_tokens})
        except httpx.HTTPError as exc:
            raise RouteError(f"{type(exc).__name__}") from exc
        if r.status_code == 404:
            raise RouteError("шлюз /api/war-terminal/free-chat отсутствует: перезапустите Bossman с этой версией", 404)
        if r.status_code >= 400:
            raise RouteError(f"HTTP {r.status_code}", r.status_code)
        return r.json()

    def memory_write(self, title: str, content: str, kind: str = "lesson", tags: list[str] | None = None) -> dict:
        """One note into Bossman memory (POST /api/memory/write as the owner participant). Called only by an explicit owner/analyst click."""
        import httpx
        try:
            r = self._client().post(self.base + "/api/memory/write", headers={**self._headers(), "X-Participant": "owner"}, timeout=60,
                                    json={"title": title, "content": content, "kind": kind, "tags": list(tags or [])})
        except httpx.HTTPError as exc:
            raise RouteError(f"{type(exc).__name__}") from exc
        if r.status_code >= 400:
            raise RouteError(f"HTTP {r.status_code}", r.status_code)
        return r.json()


def gateway_from_env(env: dict) -> BossmanGateway | None:
    import os
    if env.get("WAR_TERMINAL_BOSSMAN_URL", "").lower() in ("off", "0", "none"):
        return None
    base = env.get("WAR_TERMINAL_BOSSMAN_URL") or "http://127.0.0.1:8801"
    if not base.startswith(("http://127.0.0.1:", "http://localhost:")):
        return None                                   # the Bossman token is never sent off this machine
    tok = env.get("WAR_TERMINAL_BOSSMAN_TOKEN_FILE") or os.path.join(env.get("LOCALAPPDATA", ""), "Bossman", "CommandCenter", "token")
    if not os.path.isfile(tok):
        return None
    return BossmanGateway(base, tok)
