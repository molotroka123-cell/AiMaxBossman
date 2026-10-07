"""earn_research.py - local-LLM-only agent loop: "how can this PC earn money".

The LOCAL model (Ollama) chooses queries, reads pages, ranks options.
The harness only provides tools: web_search, fetch_url, apify_run, note, finish.
Read-only web access. No cloud LLM. Secrets are never printed or logged.
"""
import json
import os
import re
import sys
import time
import html as htmlmod
import urllib.parse

import httpx

OLLAMA = "http://127.0.0.1:11434/api/chat"
MODEL = "bossman-fast-qwen36-35b-a3b-q5:latest"
SEARCH = "http://127.0.0.1:8850/search"
OUT = r"C:\Users\asd\Bossman\bugtest-20261001\tree-1005\earn-run-20261007"
KEYS = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Bossman", "keys", "provider-keys.env")
MAX_STEPS = 30
MODEL_TIMEOUT = 90
PAGE_CAP = 8000
APIFY_CAP_USD = 1.50
UA = "Mozilla/5.0 (compatible; BossmanResearch/1.0; polite, read-only)"

PROMPT = """You are Bossman's research brain, running fully on a LOCAL model with open web access.
WHAT BOSSMAN IS: an autonomous agent platform that searches, processes and acts on the internet to produce value (and money) for its owner.

OWNER TASK: find how THIS computer can earn money with its current hardware and skills. Work like a careful analyst, not a dreamer.

HARDWARE / ASSETS (facts):
- Windows 11 PC, AMD Ryzen AI (Strix Halo) with Radeon 8060S iGPU, ~122 GB unified memory. It is NOT NVIDIA/CUDA. Most GPU-rental marketplaces need NVIDIA: check AMD/ROCm/Vulkan support BEFORE suggesting any.
- Local Ollama models (Qwen3.6 35B-A3B coder/chat, qwen3-coder 30B, Qwen3.8 27B, gemma3 27B, a 37GB chat model), llama.cpp Vulkan build, ComfyUI with ROCm torch (no Wan video model installed), ffmpeg.
- Elegoo Neptune 4 Pro 3D printer + OrcaSlicer.
- Bossman's own tools: Video/Music/Image studio, Motion Studio, Telegram bot framework, document parsing (docling, faster-whisper), coding worker framework.
- PC runs 24/7, home internet. Owner: Russian-speaking, AI tooling, video/music generation, 3D printing skills.
- Budget: start with $0. Cloud LLMs are NOT allowed for thinking in this run.

RULES: web research is READ-ONLY: never create accounts, log in, post, apply, pay or click anything that commits money. No secrets. Modest request rates. No illegal or deceptive schemes (fake engagement, botting, platform evasion, deceiving people): if you find such options list them as "REJECTED: why". Only claim numbers (rates, $ per month) that you actually saw in a fetched page, and cite the URL. If you did not verify a number say "unverified".

HINTS (like a father to a child): do not wander. Use search queries that include 2026 and the word AMD/ROCm/Vulkan where relevant. Prefer official pricing/docs pages and reputable articles over random blogs. Fetch a page only if the search snippet looks relevant. Call note() to save each verified finding (with URL).

STEP PLAN:
(a) list 6+ candidate ways to earn with THIS hardware;
(b) search each for current 2026 evidence (rates, requirements, AMD support);
(c) check feasibility for AMD + Windows;
(d) rank by expected $/month and effort, with source URLs;
(e) propose the first 3 concrete actions for the owner.
Also state in one paragraph what Bossman is for.

TOOLS: web_search(query), fetch_url(url), apify_run(actor_id, input) [optional, public scrapers only, small budget], note(text), finish(plan).
When ready, call finish(plan) with the FINAL PLAN as markdown: purpose paragraph, ranked options table (option, expected $/month, effort, AMD feasibility, source URLs), rejected options with why, first 3 actions. You have at most %d steps; start finishing by step %d.
Call exactly one tool per turn."""

TOOLS = [
    {"type": "function", "function": {"name": "web_search", "description": "Search the web (Google results). Returns titles, urls, snippets.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "fetch_url", "description": "Fetch a web page as plain text (8KB cap).",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "apify_run", "description": "Run a public read-only Apify scraper actor (hard cost cap). Optional.",
        "parameters": {"type": "object", "properties": {"actor_id": {"type": "string"}, "input": {"type": "object"}}, "required": ["actor_id", "input"]}}},
    {"type": "function", "function": {"name": "note", "description": "Save a verified finding with its URL.",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}},
    {"type": "function", "function": {"name": "finish", "description": "Submit the final markdown plan.",
        "parameters": {"type": "object", "properties": {"plan": {"type": "string"}}, "required": ["plan"]}}},
]


def load_env():
    env = {}
    try:
        for line in open(KEYS, encoding="utf-8"):
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                env[k] = v.strip().strip('"')
    except OSError:
        pass
    return env


ENV = load_env()
STATE = {"apify_usd": 0.0, "pages": [], "queries": [], "notes": [], "fetched_text": {}}


def strip_html(raw):
    raw = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<!--.*?-->", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h\d>|</tr>", "\n", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = htmlmod.unescape(raw)
    raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
    return re.sub(r"\n\s*\n+", "\n", raw).strip()


def tavily_search(query):
    key = ENV.get("TAVILY_API_KEY", "")
    if not key or STATE.get("tavily_credits", 0) >= 60:
        return []
    try:
        r = httpx.post("https://api.tavily.com/search", headers={"Authorization": "Bearer " + key},
                       json={"query": query, "max_results": 5, "search_depth": "basic", "include_answer": False}, timeout=30)
        r.raise_for_status()
        STATE["tavily_credits"] = STATE.get("tavily_credits", 0) + 1
        return [{"title": x.get("title", ""), "url": x.get("url", ""), "content": x.get("content", "")} for x in r.json().get("results", [])]
    except Exception:
        return []


def web_search(query):
    STATE["queries"].append(query)
    res = tavily_search(query)
    try:
        if not res:
            r = httpx.get(SEARCH, params={"q": query}, timeout=30)
            res = r.json().get("results", [])
    except Exception:
        res = []
    if not res:
        # polite DuckDuckGo HTML fallback
        try:
            r = httpx.get("https://html.duckduckgo.com/html/", params={"q": query}, headers={"User-Agent": UA}, timeout=30)
            items = re.findall(r'class="result__a" href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>', r.text, re.S)
            res = []
            for u, t, s in items[:8]:
                m = re.search(r"uddg=([^&]+)", u)
                res.append({"title": strip_html(t), "url": urllib.parse.unquote(m.group(1)) if m else u, "content": strip_html(s)})
        except Exception:
            pass
    time.sleep(1.0)
    return "\n".join("%d. %s | %s | %s" % (i + 1, x.get("title", "")[:100], x.get("url", ""), x.get("content", "")[:220]) for i, x in enumerate(res[:8])) or "no results"


def fetch_url(url):
    if not url.startswith(("http://", "https://")):
        return "bad url"
    try:
        with httpx.stream("GET", url, headers={"User-Agent": UA}, timeout=25, follow_redirects=True) as r:
            buf = b""
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > 600_000:
                    break
            ct = r.headers.get("content-type", "")
            if r.status_code >= 400:
                return "HTTP %d" % r.status_code
        text = buf.decode("utf-8", "replace")
        if "html" in ct or "<html" in text[:500].lower():
            text = strip_html(text)
        STATE["pages"].append(url)
        STATE["fetched_text"][url] = text[:60000]
        time.sleep(1.0)
        return text[:PAGE_CAP]
    except Exception as e:
        return "fetch error: " + type(e).__name__


def apify_run(actor_id, inp):
    tok = ENV.get("APIFY_API_TOKEN", "")
    left = APIFY_CAP_USD - STATE["apify_usd"]
    if not tok or left < 0.05:
        return "apify unavailable or budget exhausted"
    per = min(0.30, left)
    try:
        r = httpx.post("https://api.apify.com/v2/acts/%s/run-sync-get-dataset-items" % actor_id.replace("/", "~"),
                       params={"token": tok, "maxTotalChargeUsd": per, "timeout": 60},
                       json=inp, timeout=90)
        rid = r.headers.get("x-apify-run-id")
        cost = per
        if rid:
            try:
                info = httpx.get("https://api.apify.com/v2/actor-runs/" + rid, params={"token": tok}, timeout=20).json()["data"]
                cost = float(info.get("usageTotalUsd") or 0)
            except Exception:
                pass
        STATE["apify_usd"] += cost
        return ("[apify cost $%.4f, total $%.4f] " % (cost, STATE["apify_usd"])) + r.text[:PAGE_CAP]
    except Exception as e:
        STATE["apify_usd"] += 0.05
        return "apify error: " + type(e).__name__


def chat(messages, use_tools=True):
    body = {"model": MODEL, "messages": messages, "stream": False, "think": False,
            "options": {"temperature": 0.3, "num_ctx": 24576, "num_predict": 3500}}
    if use_tools:
        body["tools"] = TOOLS
    t = time.time()
    r = httpx.post(OLLAMA, json=body, timeout=MODEL_TIMEOUT)
    r.raise_for_status()
    d = r.json()
    return d, time.time() - t


def parse_action(msg):
    for tc in msg.get("tool_calls") or []:
        f = tc.get("function", {})
        a = f.get("arguments", {})
        if isinstance(a, str):
            try:
                a = json.loads(a)
            except Exception:
                a = {}
        return f.get("name"), a
    c = msg.get("content") or ""
    m = re.search(r'(web_search|fetch_url|apify_run|note|finish)\s*\((.*)\)', c, re.S)
    if m:
        body = m.group(2).strip()
        try:
            j = json.loads(body)
            if isinstance(j, dict):
                return m.group(1), j
        except Exception:
            pass
        kv = re.match(r"""\s*(\w+)\s*=\s*["'](.*?)["']\s*(?:,|$)""", body, re.S)
        if kv:
            return m.group(1), {kv.group(1): kv.group(2)}
        if body[:1] in "\"'":
            key = {"web_search": "query", "fetch_url": "url", "note": "text", "finish": "plan"}.get(m.group(1), "query")
            return m.group(1), {key: body.strip("\"' ")}
    m = re.search(r'\{.*"(?:name|tool|action)".*\}', c, re.S)
    if m:
        try:
            j = json.loads(m.group(0))
            return j.get("name") or j.get("tool") or j.get("action"), j.get("arguments") or j.get("args") or j.get("parameters") or {}
        except Exception:
            pass
    return None, {}


def build_context(system, history, notes):
    """Keep system + notes + last 3 observations in full; older ones become one-liners."""
    msgs = [{"role": "system", "content": system}]
    if notes:
        msgs.append({"role": "user", "content": "YOUR SAVED NOTES SO FAR:\n" + "\n".join("- " + n for n in notes[-25:])})
    n = len(history)
    for i, (call, obs) in enumerate(history):
        recent = i >= n - 3
        nm, ar = call
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{"function": {"name": nm, "arguments": ar}}]})
        msgs.append({"role": "tool", "tool_name": nm, "content": (obs if recent else obs[:300] + " ...[older, truncated]")})
    return msgs


def main():
    os.makedirs(OUT, exist_ok=True)
    t0 = time.time()
    system = PROMPT % (MAX_STEPS, MAX_STEPS - 4)
    history, notes, transcript, plan = [], [], [], None
    tok_in = tok_out = 0
    for step in range(1, MAX_STEPS + 1):
        msgs = build_context(system, history, notes)
        if step >= MAX_STEPS - 3:
            msgs.append({"role": "user", "content": "Step %d of %d: you MUST call finish(plan) now with the full markdown plan." % (step, MAX_STEPS)})
        else:
            msgs.append({"role": "user", "content": "Step %d of %d. Choose the next tool call." % (step, MAX_STEPS)})
        name, args, dur, raw = None, {}, 0, ""
        for attempt in range(3):
            try:
                d, dur = chat(msgs if attempt == 0 else [msgs[0]] + msgs[-5:])
                tok_in += d.get("prompt_eval_count", 0)
                tok_out += d.get("eval_count", 0)
                name, args = parse_action(d.get("message", {}))
                raw = (d.get("message", {}).get("content") or "")[:300]
                if name:
                    break
            except Exception as e:
                raw = "model error " + type(e).__name__
        entry = {"step": step, "t": round(time.time() - t0, 1), "model_s": round(dur, 1), "tool": name, "args": args if name != "finish" else {"plan": "..."}, "raw": raw if not name else ""}
        if not name:
            pass
            entry["obs"] = "no tool"
        elif name == "finish":
            plan = args.get("plan", "")
            entry["obs"] = "finished"
            transcript.append(entry)
            break
        else:
            t1 = time.time()
            if name == "web_search":
                obs = web_search(str(args.get("query", "")))
            elif name == "fetch_url":
                obs = fetch_url(str(args.get("url", "")))
            elif name == "apify_run":
                obs = apify_run(str(args.get("actor_id", "")), args.get("input") or {})
            elif name == "note":
                notes.append(str(args.get("text", ""))[:600])
                STATE["notes"].append(notes[-1])
                obs = "noted"
            else:
                obs = "unknown tool"
            entry["tool_s"] = round(time.time() - t1, 1)
            entry["obs"] = obs[:1500]
            history.append(((name, args if isinstance(args, dict) else {}), obs))
        transcript.append(entry)
        print("step %d %s %s" % (step, name, json.dumps(args, ensure_ascii=False)[:120]), flush=True)
        json.dump({"transcript": transcript}, open(os.path.join(OUT, "transcript.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if plan is None:  # forced final call without tools
        msgs = build_context(system, history, notes) + [{"role": "user", "content": "Write the FINAL PLAN now as plain markdown, no tool call."}]
        try:
            d, _ = chat(msgs, use_tools=False)
            plan = d["message"]["content"]
            tok_in += d.get("prompt_eval_count", 0)
            tok_out += d.get("eval_count", 0)
        except Exception as e:
            plan = "NO PLAN: " + type(e).__name__
    summary = {"total_s": round(time.time() - t0, 1), "steps": len(transcript), "tokens_in": tok_in, "tokens_out": tok_out,
               "apify_usd": round(STATE["apify_usd"], 4), "tavily_credits": STATE.get("tavily_credits", 0), "queries": STATE["queries"], "pages": STATE["pages"], "notes": STATE["notes"]}
    json.dump({"summary": summary, "transcript": transcript}, open(os.path.join(OUT, "transcript.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    with open(os.path.join(OUT, "PLAN.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(plan.strip() + "\n")
    with open(os.path.join(OUT, "fetched_pages.json"), "w", encoding="utf-8") as f:
        json.dump(STATE["fetched_text"], f, ensure_ascii=False)
    print("DONE", json.dumps({k: v for k, v in summary.items() if k not in ("queries", "pages", "notes")}))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
