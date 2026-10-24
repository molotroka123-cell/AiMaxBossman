"""Five research roles (+ the weekend comparison), each a function over an ``AgentContext``.

Agents get only: ask a model, search, read a public page, write a learning record. They hold no queue access —
this module does not import the store or the orchestrator, and ``AgentContext`` exposes no enqueue path; only the
orchestrator creates tasks. Every role result records the models and tools it used.

Grounding: quotes the model returns are kept as observations only if they literally occur in the fetched page
text; anything else is downgraded to «не подтверждено». Unknown = «неизвестно». Prices are not proof of sales.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable

from niche_research import PAYMENT_DISCLAIMER
from niche_research.pii import strip_pii
from niche_research.plan import CRITERIA

UNKNOWN = "неизвестно"

SYSTEM_BASE = (
    "Ты участник исследования коммерческих ниш для малого бизнеса. Правила без исключений:\n"
    "1) Пиши по-русски. Используй ТОЛЬКО факты из переданных источников; ничего не выдумывай, URL не придумывай.\n"
    "2) Чего нет в источниках — пиши «неизвестно».\n"
    "3) Разделяй: наблюдение (что прямо написано в источнике), вывод (что из этого следует), предположение "
    "(догадка без прямого подтверждения).\n"
    "4) Не обещай доход. Исследовательский сигнал — не платёжный спрос: «платёжный спрос не проверен».\n"
    "5) Цены конкурентов не доказывают, что кто-то это покупает.\n"
    "6) Не сохраняй личные данные частных лиц (телефоны, e-mail, имена клиентов).\n"
    "7) Отвечай строго в указанном формате, без вступлений."
)

PROMPTS: dict[str, str] = {
    "researcher": (
        "РОЛЬ: researcher (исследователь).\nНиша: «{niche}». Регион: {region}.\n"
        "Ниже — тексты публичных страниц с номерами источников. Найди факты о том, какие рутинные задачи и "
        "проблемы есть у владельцев таких бизнесов и их клиентов (запись, ответы на вопросы, цены, отзывы, "
        "жалобы, сезонность).\n"
        "Для каждого наблюдения дай ДОСЛОВНУЮ короткую цитату (до 200 символов) из текста источника.\n"
        "Верни JSON: {{\"observations\":[{{\"source_id\":1,\"quote\":\"дословно\",\"fact\":\"кратко\"}}],"
        "\"conclusions\":[\"...\"],\"assumptions\":[\"...\"],\"unknowns\":[\"...\"]}}\n\nИСТОЧНИКИ:\n{sources}"),
    "competitor": (
        "РОЛЬ: competitor (аналитик конкурентов).\nНиша: «{niche}». Регион: {region}.\n"
        "Найди в источниках не меньше 3 публичных предложений (услуга/пакет/тариф). Для каждого: кто предлагает, "
        "что именно, цена как написана (или «неизвестно»), ДОСЛОВНАЯ цитата до 200 символов.\n"
        "Помни: цена на сайте — не доказательство продаж.\n"
        "Верни JSON: {{\"offers\":[{{\"source_id\":1,\"name\":\"...\",\"offer\":\"...\",\"price_text\":\"...\","
        "\"quote\":\"дословно\"}}],\"notes\":\"...\"}}\n\nИСТОЧНИКИ:\n{sources}"),
    "packager": (
        "РОЛЬ: packager (упаковщик оффера).\nНиша: «{niche}». Регион: {region}.\n"
        "На основе проверенных наблюдений и предложений конкурентов сформулируй ОДИН конкретный платный оффер "
        "нашей услуги для владельца такого бизнеса: для кого, какую рутинную задачу снимает, что именно получает "
        "клиент (результат), за какой срок, гипотеза цены (явно как гипотеза).\n"
        "Оцени нишу по 5 критериям от 1 до 5 (5 = лучше для нас): problem_visibility (видимость проблемы), "
        "buyer_accessibility (доступность покупателя), competition (5 = конкуренция слабая), output_quality "
        "(можем ли сделать качественный результат), expected_margin (ожидаемая маржа). Обоснуй каждую оценку.\n"
        "Верни JSON: {{\"offer\":{{\"for_whom\":\"...\",\"routine_task\":\"...\",\"deliverable\":\"...\","
        "\"timeframe\":\"...\",\"price_hypothesis\":\"...\"}},\"scores\":{{\"problem_visibility\":3,"
        "\"buyer_accessibility\":3,\"competition\":3,\"output_quality\":3,\"expected_margin\":3}},"
        "\"score_reasons\":{{\"problem_visibility\":\"...\"}},\"production_cost\":\"часы/ресурсы или неизвестно\"}}"
        "\n\nПРОВЕРЕННЫЕ НАБЛЮДЕНИЯ:\n{facts}\n\nПРЕДЛОЖЕНИЯ КОНКУРЕНТОВ:\n{offers}"),
    "draft": (
        "РОЛЬ: draft (автор образца).\nНиша: «{niche}». Тип образца: {sample_kind}.\n"
        "Напиши образец результата, который мы продавали бы по офферу ниже. Используй только проверяемые факты "
        "из списка; любые конкретные данные (цены, адреса, сроки), которых нет в фактах, заменяй на «неизвестно». "
        "Никаких обещаний дохода и гарантий. Формат: Markdown, заголовок первой строкой, до 400 слов.\n\n"
        "ОФФЕР:\n{offer}\n\nФАКТЫ:\n{facts}"),
    "critic": (
        "РОЛЬ: critic (критик и фактчекер).\nНиша: «{niche}».\n"
        "Проверь оффер и образец: найди утверждения без опоры на факты, слабые места и минимум 3 причины, почему "
        "клиент НЕ заплатит. Пересмотри оценки 1–5 по тем же 5 критериям, если они завышены.\n"
        "Верни JSON: {{\"unsupported_claims\":[\"...\"],\"weak_points\":[\"...\"],\"reasons_wont_pay\":[\"...\","
        "\"...\",\"...\"],\"adjusted_scores\":{{\"problem_visibility\":3,\"buyer_accessibility\":3,"
        "\"competition\":3,\"output_quality\":3,\"expected_margin\":3}},\"verdict\":\"...\"}}\n\n"
        "ФАКТЫ:\n{facts}\n\nОФФЕР:\n{offer}\n\nОБРАЗЕЦ:\n{sample}"),
    "weekly": (
        "РОЛЬ: weekly (сравнение недели).\nТема недели: {theme}.\n"
        "Сравни ниши недели по данным ниже и упорядочи их от самой перспективной к наименее перспективной для "
        "проверки готовности платить. Не называй ни одну нишу доказанной.\n"
        "Верни JSON: {{\"ranking\":[\"ниша\", \"...\"],\"why\":\"...\"}}\n\nДАННЫЕ:\n{summary}"),
}

SAMPLE_KINDS = ["FAQ для клиентов", "шаблон ответа на вопрос о цене", "типовые ответы на частые обращения",
                "описание услуги"]


class RoleError(RuntimeError):
    pass


@dataclass
class RoleOutput:
    content: dict
    models: list[str]
    tools: list[str]
    sample_markdown: str | None = None


@dataclass
class AgentContext:
    """What an agent may touch. Deliberately no store / queue / enqueue access."""

    task: dict
    _ask: Callable[..., object]
    _search: object | None = None
    _fetch: Callable[[str], object] | None = None
    _note: Callable[..., None] | None = None
    prior: dict = field(default_factory=dict)
    region: str = ""
    seed_urls: list[str] = field(default_factory=list)
    max_pages: int = 3
    models_used: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)

    def _tool(self, name: str) -> None:
        if name not in self.tools_used:
            self.tools_used.append(name)

    def ask(self, system: str, prompt: str, exclude: set[str] | None = None):
        reply = self._ask(system, prompt, exclude=exclude)
        label = f"{reply.provider}:{reply.model}"
        if label not in self.models_used:
            self.models_used.append(label)
        self._tool("model.complete")
        return reply

    def ask_json(self, system: str, prompt: str) -> dict:
        reply = self.ask(system, prompt)
        data = extract_json(reply.text)
        if data is None:  # one retry on a different model
            reply = self.ask(system, prompt, exclude={f"{reply.provider}:{reply.model}"})
            data = extract_json(reply.text)
        if data is None:
            raise RoleError("model returned no parseable JSON")
        return data

    def search(self, query: str):
        if self._search is None:
            return None
        self._tool("web.search")
        return self._search.search(query, task_id=self.task.get("id"))

    def fetch(self, url: str):
        if self._fetch is None:
            return None
        self._tool("web.fetch")
        return self._fetch(url)

    def note(self, url: str, kind: str, text: str) -> None:
        if self._note is not None:
            self._note(url=url, kind=kind, text=text, task_id=self.task.get("id"))


def extract_json(text: str) -> dict | None:
    if not text:
        return None
    candidates = [text.strip()]
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        candidates.append(m.group(1).strip())
    i, j = text.find("{"), text.rfind("}")
    if 0 <= i < j:
        candidates.append(text[i:j + 1])
    for c in candidates:
        try:
            val = json.loads(c)
        except (ValueError, TypeError):
            continue
        if isinstance(val, dict):
            return val
    return None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower().replace("ё", "е")


def _region(ctx: AgentContext) -> str:
    return ctx.region or "не задан (уточнить у владельца)"


def _excerpt(text: str, niche: str, limit: int = 1800) -> str:
    words = [w for w in re.split(r"\W+", niche.lower()) if len(w) > 3]
    lines = [ln for ln in text.splitlines() if ln.strip()]
    hit = [ln for ln in lines if any(w[:5] in ln.lower() for w in words)]
    chosen = "\n".join(hit) if hit else "\n".join(lines)
    return strip_pii(chosen)[:limit]


def _gather_sources(ctx: AgentContext, queries: list[str], skip: set[str] | None = None) -> tuple[list[dict], list[str]]:
    """Search + polite fetch. Returns (sources, unknowns)."""
    unknowns: list[str] = []
    urls: list[str] = []
    for q in queries:
        res = ctx.search(q)
        if res is None:
            unknowns.append(f"поиск недоступен для запроса «{q}»: {UNKNOWN}")
            continue
        if res.error:
            unknowns.append(f"поиск «{q}» не дал результатов ({res.error}): {UNKNOWN}")
        for hit in res.hits:
            if hit.url not in urls:
                urls.append(hit.url)
    for u in ctx.seed_urls:
        if u not in urls:
            urls.insert(0, u)
    sources: list[dict] = []
    for url in urls:
        if len(sources) >= ctx.max_pages:
            break
        if skip and url in skip:
            continue
        page = ctx.fetch(url)
        if page is None:
            break
        if not page.ok:
            unknowns.append(f"{url}: {page.error}")
            continue
        from niche_research.web import iso_date  # local import keeps web optional for pure-role tests
        sources.append({"id": len(sources) + 1, "url": url, "date": iso_date(page.fetched_at),
                        "title": page.title, "text": page.text, "excerpt": _excerpt(page.text, ctx.task["niche"])})
    return sources, unknowns


def _sources_block(sources: list[dict]) -> str:
    return "\n\n".join(f"[{s['id']}] {s['url']} ({s['date']}) {s['title']}\n{s['excerpt']}" for s in sources)


def _verified_quote(src: dict | None, quote: str) -> bool:
    return bool(src and quote and len(_norm(quote)) >= 8 and _norm(quote) in _norm(src["text"]))


def run_researcher(ctx: AgentContext) -> RoleOutput:
    niche, region = ctx.task["niche"], ctx.region
    queries = [f"{niche} услуги цены {region}".strip(), f"{niche} отзывы клиентов проблемы {region}".strip()]
    sources, unknowns = _gather_sources(ctx, queries)
    content = {"niche": niche, "sources": [{k: s[k] for k in ("id", "url", "date", "title")} for s in sources],
               "observations": [], "unverified": [], "conclusions": [], "assumptions": [], "unknowns": unknowns}
    if not sources:
        content["unknowns"].append(f"нет прочитанных источников по нише «{niche}»: факты {UNKNOWN}")
        ctx.note("", "unknown", f"{niche}: нет источников")
        return RoleOutput(content, ctx.models_used, ctx.tools_used)
    data = ctx.ask_json(SYSTEM_BASE, PROMPTS["researcher"].format(niche=niche, region=_region(ctx),
                                                                   sources=_sources_block(sources)))
    by_id = {s["id"]: s for s in sources}
    for ob in data.get("observations") or []:
        if not isinstance(ob, dict):
            continue
        src = by_id.get(_as_int(ob.get("source_id")))
        quote = strip_pii(str(ob.get("quote") or ""))[:300]
        item = {"url": src["url"] if src else "", "date": src["date"] if src else "", "quote": quote,
                "fact": strip_pii(str(ob.get("fact") or ""))[:300]}
        if _verified_quote(src, quote):
            content["observations"].append(item)
            ctx.note(item["url"], "observation", f"«{quote}» — {item['fact']}")
        else:
            content["unverified"].append(item)
    for kind, key in (("conclusion", "conclusions"), ("assumption", "assumptions")):
        for text in data.get(key) or []:
            text = strip_pii(str(text))[:400]
            content[key].append(text)
            ctx.note(sources[0]["url"], kind, text)
    content["unknowns"].extend(strip_pii(str(u))[:300] for u in data.get("unknowns") or [])
    return RoleOutput(content, ctx.models_used, ctx.tools_used)


def run_competitor(ctx: AgentContext) -> RoleOutput:
    niche, region = ctx.task["niche"], ctx.region
    queries = [f"{niche} прайс стоимость услуги {region}".strip()]
    sources, unknowns = _gather_sources(ctx, queries)
    content = {"niche": niche, "offers": [], "unverified": [], "unknowns": unknowns,
               "disclaimer": "цены конкурентов — не доказательство продаж"}
    if sources:
        data = ctx.ask_json(SYSTEM_BASE, PROMPTS["competitor"].format(niche=niche, region=_region(ctx),
                                                                       sources=_sources_block(sources)))
        by_id = {s["id"]: s for s in sources}
        for of in data.get("offers") or []:
            if not isinstance(of, dict):
                continue
            src = by_id.get(_as_int(of.get("source_id")))
            item = {"url": src["url"] if src else "", "date": src["date"] if src else "",
                    "name": strip_pii(str(of.get("name") or UNKNOWN))[:120],
                    "offer": strip_pii(str(of.get("offer") or UNKNOWN))[:300],
                    "price_text": strip_pii(str(of.get("price_text") or UNKNOWN))[:120],
                    "quote": strip_pii(str(of.get("quote") or ""))[:300]}
            if _verified_quote(src, item["quote"]):
                content["offers"].append(item)
                ctx.note(item["url"], "observation", f"предложение: {item['offer']}; цена: {item['price_text']}")
            else:
                content["unverified"].append(item)
    if len(content["offers"]) < 3:
        content["unknowns"].append(f"подтверждено {len(content['offers'])} публичных предложений из 3 требуемых; "
                                   f"остальное {UNKNOWN}")
    return RoleOutput(content, ctx.models_used, ctx.tools_used)


def _facts_text(prior: dict) -> str:
    obs = (prior.get("researcher") or {}).get("observations") or []
    lines = [f"- {o['fact']} (цитата: «{o['quote']}», {o['url']}, {o['date']})" for o in obs]
    return "\n".join(lines) or f"- проверенных фактов нет ({UNKNOWN})"


def _offers_text(prior: dict) -> str:
    offers = (prior.get("competitor") or {}).get("offers") or []
    lines = [f"- {o['name']}: {o['offer']}; цена: {o['price_text']} ({o['url']})" for o in offers]
    return "\n".join(lines) or f"- подтверждённых предложений нет ({UNKNOWN})"


def run_packager(ctx: AgentContext) -> RoleOutput:
    niche = ctx.task["niche"]
    data = ctx.ask_json(SYSTEM_BASE, PROMPTS["packager"].format(niche=niche, region=_region(ctx),
                                                                 facts=_facts_text(ctx.prior),
                                                                 offers=_offers_text(ctx.prior)))
    raw_offer = data.get("offer") if isinstance(data.get("offer"), dict) else {}
    offer = {k: strip_pii(str(raw_offer.get(k) or UNKNOWN))[:500]
             for k in ("for_whom", "routine_task", "deliverable", "timeframe", "price_hypothesis")}
    content = {"niche": niche, "offer": offer, "scores": clamp_scores(data.get("scores")),
               "score_reasons": {k: strip_pii(str(v))[:300] for k, v in (data.get("score_reasons") or {}).items()
                                 if k in CRITERIA},
               "production_cost": strip_pii(str(data.get("production_cost") or UNKNOWN))[:300]}
    return RoleOutput(content, ctx.models_used, ctx.tools_used)


def run_draft(ctx: AgentContext) -> RoleOutput:
    niche = ctx.task["niche"]
    kind = SAMPLE_KINDS[(int(ctx.task.get("day") or 1) - 1) % len(SAMPLE_KINDS)]
    offer = (ctx.prior.get("packager") or {}).get("offer") or {}
    reply = ctx.ask(SYSTEM_BASE.replace("Отвечай строго в указанном формате, без вступлений.",
                                        "Отвечай Markdown-текстом образца, без вступлений."),
                    PROMPTS["draft"].format(niche=niche, sample_kind=kind,
                                            offer=json.dumps(offer, ensure_ascii=False, indent=1),
                                            facts=_facts_text(ctx.prior)))
    text = strip_pii(reply.text.strip())
    footer = (f"\n\n---\n_Образец ({kind}) для ниши «{niche}». Только проверяемые факты; неизвестное помечено "
              f"«{UNKNOWN}». Это черновик для исследования, а не опубликованный материал. {PAYMENT_DISCLAIMER}._\n")
    md = text + footer
    return RoleOutput({"niche": niche, "sample_kind": kind, "chars": len(text)}, ctx.models_used, ctx.tools_used,
                      sample_markdown=md)


def run_critic(ctx: AgentContext) -> RoleOutput:
    niche = ctx.task["niche"]
    offer = (ctx.prior.get("packager") or {}).get("offer") or {}
    sample = ctx.prior.get("draft_markdown") or UNKNOWN
    data = ctx.ask_json(SYSTEM_BASE, PROMPTS["critic"].format(niche=niche, facts=_facts_text(ctx.prior),
                                                               offer=json.dumps(offer, ensure_ascii=False),
                                                               sample=sample[:3000]))
    lst = lambda key: [strip_pii(str(x))[:400] for x in (data.get(key) or []) if str(x).strip()]  # noqa: E731
    content = {"niche": niche, "unsupported_claims": lst("unsupported_claims"), "weak_points": lst("weak_points"),
               "reasons_wont_pay": lst("reasons_wont_pay"),
               "adjusted_scores": clamp_scores(data.get("adjusted_scores") or
                                               (ctx.prior.get("packager") or {}).get("scores")),
               "verdict": strip_pii(str(data.get("verdict") or UNKNOWN))[:500], "complete": True}
    if len(content["reasons_wont_pay"]) < 3:
        content["complete"] = False
        content["reasons_wont_pay"].append(f"критик дал меньше 3 причин — остальное {UNKNOWN}")
    return RoleOutput(content, ctx.models_used, ctx.tools_used)


def run_weekly(ctx: AgentContext, theme: str, summary: str, other_models: int = 1) -> RoleOutput:
    """Ask up to 1 + ``other_models`` different models for a ranking, to compare outputs across models."""
    prompt = PROMPTS["weekly"].format(theme=theme, summary=summary)
    rankings: dict[str, list] = {}
    exclude: set[str] = set()
    for _ in range(1 + other_models):
        try:
            reply = ctx.ask(SYSTEM_BASE, prompt, exclude=set(exclude))
        except Exception:  # noqa: BLE001 - fewer models available is a valid outcome, recorded below
            break
        label = f"{reply.provider}:{reply.model}"
        exclude.add(label)
        data = extract_json(reply.text) or {}
        rankings[label] = [strip_pii(str(x))[:120] for x in (data.get("ranking") or [])]
    return RoleOutput({"rankings": rankings, "models_compared": len(rankings)}, ctx.models_used, ctx.tools_used)


def clamp_scores(scores) -> dict:
    scores = scores if isinstance(scores, dict) else {}
    out = {}
    for key in CRITERIA:
        v = _as_int(scores.get(key))
        out[key] = max(1, min(5, v)) if v else 1
    return out


def _as_int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


ROLE_FUNCS: dict[str, Callable[[AgentContext], RoleOutput]] = {
    "researcher": run_researcher,
    "competitor": run_competitor,
    "packager": run_packager,
    "draft": run_draft,
    "critic": run_critic,
}
