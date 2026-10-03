"""Runs the Jeff math corpus through Jeff's REAL runtime path (everything except the Telegram transport).

    python tests/data/jeff_math_eval.py --label baseline --out RESULTS.json [--route cloud|local] [--rpm 12]
    python tests/data/jeff_math_eval.py --rescore RESULTS.json          # re-score a saved run with the current rule

What is real: ``ParticipantRuntime.handle`` (guards, crisis check, j2, ``_chat_route_core``, the system prompt from
``build_participant_context``, history/consent handling, free-route verification against the live OpenRouter catalog).
What is replaced: the Telegram transport (never started) and the adapter is wrapped by a meter that spaces requests
(default 12 per minute), waits out HTTP 429 and records usage.cost of every call. A non-zero cost aborts the run.

The OpenRouter key is read from the owner's key file inside this process, handed to the provider adapter and never
printed, logged or written anywhere. Each run uses a fresh temporary data dir with one synthetic participant.

Scoring (strict, documented; the same code scores before and after):
  number   FINAL NUMBER of the reply: the first number after the last "Ответ"/"Итого"/"Итог" marker (on that line; after
           the last "=" of that line when it has one), else the number
           inside the last bold span that has one, else the last number of the reply. Numbers are read as
           digits with optional decimal point/comma; groups of exactly three digits separated by spaces or NBSP are
           thousands separators; "1,234,567" (two or more comma groups) is thousands; the minus sign must touch the
           digits. Correct iff it equals the key exactly (the key is rounded half-up to `decimals` when the question
           asked for rounding; a reply with more digits than asked is wrong).
  date     the last DD.MM.YYYY of the reply equals the key.
  time     the last HH:MM equals the key.
  weekday  the last weekday word of the reply equals the key.
  The report also gives two diagnostics, never the headline: LENIENT (the key appears as ANY number of the reply) and
  APPROX (the final number is within 0.6% of the key, i.e. the model rounded a value nobody asked it to round).
  Marker rule detail: after "Ответ"/"Итого"/"Итог" at most 8 non-digit characters are skipped, then the number is read.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORPUS = HERE / "jeff_math_corpus.json"
MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
KEY_FILE = Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "keys" / "provider-keys.env"

# ---------------------------------------------------------------------------------------------- scoring
_NUM_TOKEN = re.compile(
    r"(?<![\w.,])-?(?:\d{1,3}(?:,\d{3}){2,}(?:\.\d+)?|\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)")
_MARKER = re.compile(r"(?:ответ|итого|итог)\b[^\d\n]{0,8}", re.I)
_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_WEEKDAY = re.compile(r"\b(понедельник|вторник|сред[аыуе]|четверг|пятниц[аыуе]|суббот[аыуе]|воскресень[ея])\b", re.I)


def parse_number(token: str) -> Fraction | None:
    t = token.strip()
    t = re.sub(r"[ \u00a0\u202f]", "", t)
    if re.fullmatch(r"-?\d{1,3}(?:,\d{3}){2,}(?:\.\d+)?", t):
        t = t.replace(",", "")
    t = t.replace(",", ".")
    try:
        return Fraction(Decimal(t))
    except (InvalidOperation, ValueError):
        return None


def _numbers(text: str) -> list[Fraction]:
    out = []
    for m in _NUM_TOKEN.finditer(text.replace("\u2212", "-")):
        value = parse_number(m.group(0))
        if value is not None:
            out.append(value)
    return out


def final_number(reply: str) -> Fraction | None:
    text = reply.replace("`", "")
    markers = list(_MARKER.finditer(text))
    if markers:
        rest = text[markers[-1].end():].split("\n", 1)[0]       # the rest of the marker's own line
        if "=" in rest:
            rest = rest.rsplit("=", 1)[1]                        # "Итого: 387 + 470 = 857 рублей" -> 857
        found = _numbers(rest)
        if found:
            return found[0]
    for m in reversed(list(_BOLD.finditer(text))):
        found = _numbers(m.group(1))
        if found:
            return found[-1]
    found = _numbers(text.replace("*", ""))
    return found[-1] if found else None


def score(item: dict, reply: str) -> dict:
    kind = item["answer_type"]
    if kind == "none":
        return {"correct": None, "lenient": None, "extracted": None}
    if kind == "number":
        key = Fraction(item["answer"])
        got = final_number(reply)
        lenient = any(n == key for n in _numbers(reply.replace("*", "")))
        approx = got is not None and key != 0 and abs(got - key) / abs(key) <= Fraction(6, 1000)
        return {"correct": got is not None and got == key, "lenient": lenient, "approx": approx,
                "extracted": None if got is None else (str(got) if got.denominator == 1 else str(float(got)))}
    if kind == "date":
        found = re.findall(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", reply)
        got = "%02d.%02d.%s" % (int(found[-1][0]), int(found[-1][1]), found[-1][2]) if found else None
        return {"correct": got == item["answer"], "lenient": item["answer"] in reply, "extracted": got}
    if kind == "time":
        found = re.findall(r"\b(\d{1,2}):(\d{2})\b", reply)
        got = "%02d:%s" % (int(found[-1][0]), found[-1][1]) if found else None
        return {"correct": got == item["answer"], "lenient": item["answer"] in reply, "extracted": got}
    if kind == "weekday":
        found = _WEEKDAY.findall(reply)
        got = found[-1].lower() if found else None
        stem = lambda w: (w or "")[:5]          # noqa: E731 - среда/среду/среды share a stem
        return {"correct": stem(got) == stem(item["answer"]),
                "lenient": any(stem(w.lower()) == stem(item["answer"]) for w in found), "extracted": got}
    raise ValueError(kind)


def summarize(results: list[dict]) -> dict:
    cats: dict[str, dict] = {}
    for row in results:
        if row["control"]:
            continue
        c = cats.setdefault(row["category"], {"n": 0, "correct": 0, "lenient": 0, "approx": 0})
        c["n"] += 1
        c["correct"] += bool(row["score"]["correct"])
        c["lenient"] += bool(row["score"]["lenient"])
        c["approx"] += bool(row["score"].get("approx") or row["score"]["correct"])
    total = {k: sum(c[k] for c in cats.values()) for k in ("n", "correct", "lenient", "approx")}
    return {"overall": total, "by_category": cats}


# ---------------------------------------------------------------------------------------------- key / meter
def read_key() -> str:
    for line in KEY_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:]
        if line.startswith("OPENROUTER_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("OPENROUTER_API_KEY not found in the owner key file")


class CostViolation(RuntimeError):
    pass


class MeteredAdapter:
    """Wraps a provider adapter: min gap between chat calls, 429 patience, usage.cost accounting, hard stop on cost."""

    def __init__(self, inner, *, rpm: float, log):
        self.inner, self.gap, self.log = inner, 60.0 / rpm, log
        self.next_ok = 0.0
        self.calls: list[dict] = []
        self.violation: str | None = None
        self.last_messages: list[dict] = []

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def chat(self, model, messages, **kw):
        if self.violation:
            raise CostViolation(self.violation)
        self.last_messages = [dict(m) for m in messages]
        for attempt in range(8):
            wait = self.next_ok - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self.next_ok = time.monotonic() + self.gap
            started = time.monotonic()
            try:
                result = await self.inner.chat(model, messages, **kw)
            except Exception as exc:  # noqa: BLE001
                text = str(exc)
                if "(402)" in text:
                    self.violation = "HTTP 402 payment required"
                    raise CostViolation(self.violation) from None
                if "(429)" in text or getattr(exc, "kind", "") == "rate_limit":
                    pause = min(150.0, 25.0 * (attempt + 1))
                    self.log(f"  429/overload, waiting {pause:.0f}s (attempt {attempt + 1})")
                    await asyncio.sleep(pause)
                    self.next_ok = time.monotonic() + self.gap
                    continue
                raise
            usage = (getattr(result, "provider_meta", None) or {}).get("usage") or {}
            cost = usage.get("cost")
            self.calls.append({"model": model, "latency_s": round(time.monotonic() - started, 2),
                               "tokens_in": getattr(result, "tokens_in", 0),
                               "tokens_out": getattr(result, "tokens_out", 0),
                               "cost": cost, "finish": getattr(result, "finish", "")})
            if isinstance(cost, (int, float)) and cost > 0:
                self.violation = f"non-zero usage.cost {cost}"
                raise CostViolation(self.violation)
            return result
        raise RuntimeError("rate limited too long")


# ---------------------------------------------------------------------------------------------- run
def _digest(messages: list[dict]) -> dict:
    return {"sha256": hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
            "roles": [m.get("role") for m in messages],
            "system_extras": [str(m.get("content", "")) for m in messages[1:] if m.get("role") == "system"
                              and str(m.get("content", "")).startswith("Точный расчёт")],
            "system_sha256": hashlib.sha256(str(messages[0].get("content", "")).encode()).hexdigest()[:16]}


FAILURE_MARKERS = ("Бесплатный облачный лимит", "не отвечает", "Не удалось", "временно недоступ", "ответ получился неполным")


async def run(args) -> dict:
    sys.path.insert(0, str(HERE.parents[1]))
    from bcc.pit.config import PITSettings
    from bcc.pit.models import ConsentState
    from bcc.pit.runtime import ParticipantRuntime
    from bcc.telegram_companion.config import Person

    corpus = json.loads(Path(args.corpus).read_text(encoding="utf-8"))
    items = corpus["items"]
    if args.only:
        wanted = set(args.only.split(","))
        items = [i for i in items if i["id"] in wanted or i["category"] in wanted]
    if args.limit:
        items = items[:args.limit]
    log = lambda *a: print(*a, file=sys.stderr, flush=True)       # noqa: E731

    tmp = tempfile.mkdtemp(prefix="jeffmath-")
    base = dict(data_dir=Path(tmp), people=(Person(user_id=7000001, chat_id=7000001, role="guest"),),
                bot_token="0:synthetic", identity_salt="cd" * 32, chat_deadline_seconds=60, remote_timeout=120.0,
                local_timeout=180.0, cloud_daily_request_budget=400)
    if args.route == "cloud":
        settings = PITSettings(chat_models=(MODEL,), provider_base_url="https://openrouter.ai/api/v1",
                               provider_key=read_key(), **base)
    else:
        settings = PITSettings(chat_models=(), local_url=args.local_url, local_models=(args.local_model,),
                               local_chat_only=True, **base)
    runtime = ParticipantRuntime(settings)
    meter = MeteredAdapter(runtime.adapter, rpm=args.rpm, log=log)
    if args.route == "cloud":
        runtime.adapter = meter
    else:
        meter.inner = runtime.local_adapter
        runtime.local_adapter = meter
    if args.math == "off":
        # the owner switch of the patch (jeff-settings.json math_assist=false): the hook is inert, the path is the old one
        from bcc.pit import jeff_settings as js
        js.write_overlay(js.settings_path(runtime.vault.data_dir), {"version": 1, "math_assist": False})
        assert js.math_assist_enabled(runtime.vault.data_dir) is False
    person = settings.people[0]
    person_key = runtime.vault.key_for_telegram(person.user_id)
    runtime.vault.set_consent(person_key, ConsentState(memory_enabled=True, remote_processing_enabled=True))

    results = []
    started_all = time.monotonic()
    for index, item in enumerate(items, 1):
        reply, attempts, latency = "", 0, 0.0
        for attempts in range(1, 4):
            meter.calls.clear()
            began = time.monotonic()
            body = {"_user_id": person.user_id, "_chat_id": person.chat_id, "_message_id": 1000 + index,
                    "text": item["question"], "_photo": "", "_document": None, "_voice": False}
            try:
                reply = await runtime.handle(person, body) or ""
            except CostViolation as exc:
                raise SystemExit(f"STOP: {exc}")
            latency = time.monotonic() - began
            if meter.violation:
                raise SystemExit(f"STOP: {meter.violation}")
            if reply and not any(marker in reply for marker in FAILURE_MARKERS):
                break
            log(f"  [{item['id']}] failure-like reply, retry {attempts}: {reply[:80]!r}")
            await asyncio.sleep(40)
        sent = meter.last_messages
        row = {"id": item["id"], "category": item["category"], "control": item["control"],
               "question": item["question"], "answer": item["answer"], "answer_type": item["answer_type"],
               "reply": reply, "attempts": attempts, "latency_s": round(latency, 2),
               "model_calls": list(meter.calls), "prompt": _digest(sent), "score": score(item, reply)}
        results.append(row)
        flag = "-" if item["control"] else ("OK" if row["score"]["correct"] else "NO")
        log(f"[{index}/{len(items)}] {item['id']:26} {flag:2} hint={bool(row['prompt']['system_extras'])} "
            f"{row['latency_s']}s")
    await runtime.close()
    return {"label": args.label, "route": args.route, "model": MODEL if args.route == "cloud" else args.local_model,
            "corpus_version": corpus["version"], "math_assist": args.math, "n_items": len(results), "rpm": args.rpm,
            "wall_seconds": round(time.monotonic() - started_all, 1),
            "total_cost": sum((c.get("cost") or 0) for r in results for c in r["model_calls"]),
            "costs_reported": sum(c.get("cost") is not None for r in results for c in r["model_calls"]),
            "model_call_count": sum(len(r["model_calls"]) for r in results),
            "summary": summarize(results), "results": results}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="run")
    parser.add_argument("--out")
    parser.add_argument("--route", choices=("cloud", "local"), default="cloud")
    parser.add_argument("--rpm", type=float, default=12.0)
    parser.add_argument("--only", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--local-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--local-model", default="")
    parser.add_argument("--rescore")
    parser.add_argument("--corpus", default=str(CORPUS))
    parser.add_argument("--math", choices=("on", "off"), default="on",
                        help="off writes math_assist=false into the temporary jeff-settings.json (baseline of the patched code)")
    args = parser.parse_args()
    if args.rescore:
        data = json.loads(Path(args.rescore).read_text(encoding="utf-8"))
        items = {i["id"]: i for i in json.loads(Path(args.corpus).read_text(encoding="utf-8"))["items"]}
        for row in data["results"]:
            row["score"] = score(items[row["id"]], row["reply"])
        data["summary"] = summarize(data["results"])
        Path(args.rescore).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(json.dumps(data["summary"], ensure_ascii=False))
        return
    data = asyncio.run(run(args))
    text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
    print(json.dumps({k: data[k] for k in ("label", "route", "math_assist", "n_items", "wall_seconds", "total_cost", "summary")},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
