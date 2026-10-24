"""Day / week / final reports and improvement proposals (Markdown, Russian).

Every report carries «платёжный спрос не проверен»: research signals (pages, prices, reviews) are not paid
demand, and no niche is ever called proven without a real buyer response or payment. Proposals are files only —
nothing here (or anywhere in the package) applies them, sends, posts or publishes anything.
"""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from niche_research import PAYMENT_DISCLAIMER, RUN_DAYS
from niche_research.plan import CRITERIA, MAX_WEEKLY_HYPOTHESES, WEEKS, deadline_ts, total_score

DISCLAIMER_BLOCK = (f"> **{PAYMENT_DISCLAIMER}.** Ниже — исследовательские сигналы из открытых источников, а не "
                    "доказательство спроса и не обещание дохода. Цены конкурентов не доказывают продаж.\n")
ROLE_RU = {"researcher": "Исследователь", "competitor": "Аналитик конкурентов", "packager": "Упаковщик",
           "draft": "Автор образца", "critic": "Критик-фактчекер", "weekly": "Сравнение недели"}


def _date(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _results_by_role(store, day: int) -> dict[str, dict]:
    return {r["role"]: r for r in store.results(day=day)}


def week_niche_rows(store, week: int) -> list[dict]:
    rows = []
    days = range((week - 1) * 7 + 1, week * 7 + 1)
    for d in days:
        res = _results_by_role(store, d)
        pk = res.get("packager")
        if not pk:
            continue
        cr = res.get("critic")
        scores = (cr["content"].get("adjusted_scores") if cr else None) or pk["content"].get("scores") or {}
        obs = (res.get("researcher") or {}).get("content", {}).get("observations", [])
        rows.append({"day": d, "niche": pk["niche"], "offer": pk["content"].get("offer", {}), "scores": scores,
                     "packager_scores": pk["content"].get("scores", {}), "total": total_score(scores),
                     "critic_done": bool(cr), "critic_complete": bool(cr and cr["content"].get("complete")),
                     "weak": (cr["content"].get("weak_points", []) + cr["content"].get("reasons_wont_pay", []))
                     if cr else ["критика ещё не выполнена"],
                     "observations": obs, "evidence": sorted({o["url"] for o in obs if o.get("url")}),
                     "production_cost": pk["content"].get("production_cost", "неизвестно")})
    return rows


def selected_hypotheses(rows: list[dict]) -> list[dict]:
    eligible = [r for r in rows if r["critic_done"]]
    eligible.sort(key=lambda r: (r["total"], len(r["observations"])), reverse=True)
    return eligible[:MAX_WEEKLY_HYPOTHESES]


def _spend_table(rows: list[dict]) -> str:
    agg: dict[str, dict] = defaultdict(lambda: {"calls": 0, "ok": 0, "tin": 0, "tout": 0, "cost": 0.0, "paid": 0})
    for r in rows:
        a = agg[f"{r['provider']}:{r['model']}"]
        a["calls"] += 1
        a["ok"] += r["ok"]
        a["tin"] += r["tokens_in"]
        a["tout"] += r["tokens_out"]
        a["cost"] += r["cost_usd"]
        a["paid"] = max(a["paid"], r["paid"])
    if not agg:
        return "- вызовов моделей не было\n"
    lines = ["| модель | вызовов | успешно | токены вход/выход | стоимость, $ | платная |", "|---|---|---|---|---|---|"]
    for label, a in sorted(agg.items()):
        lines.append(f"| {label} | {a['calls']} | {a['ok']} | {a['tin']}/{a['tout']} | {a['cost']:.4f} | "
                     f"{'да' if a['paid'] else 'нет'} |")
    paid = sum(r["cost_usd"] for r in rows if r["paid"])
    return "\n".join(lines) + f"\n\nПлатный расход: **${paid:.4f}**.\n"


def _grounding_by_model(results: list[dict]) -> dict[str, list[int]]:
    """model label -> [verified quotes, unverified quotes] for researcher/competitor outputs."""
    out: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in results:
        if r["role"] not in ("researcher", "competitor"):
            continue
        ver = len(r["content"].get("observations" if r["role"] == "researcher" else "offers", []))
        unv = len(r["content"].get("unverified", []))
        for m in r["models"] or ["(без модели)"]:
            out[m][0] += ver
            out[m][1] += unv
    return out


class ReportWriter:
    def __init__(self, store, config, clock: Callable[[], float] = time.time):
        self.store = store
        self.config = config
        self.clock = clock

    @property
    def reports_dir(self) -> Path:
        return self.config.run_dir / "reports"

    def _day_task_ids(self, day: int) -> list[int]:
        return [t["id"] for t in self.store.tasks(day=day)]

    # ---- day -------------------------------------------------------------------------------------------------
    def write_day(self, day: int) -> Path:
        tasks = self.store.tasks(day=day)
        res = _results_by_role(self.store, day)
        week = (day - 1) // 7 + 1
        first = tasks[0] if tasks else None
        date = (first or {}).get("payload", {}).get("date", "")
        is_weekly = bool(first and first["role"] == "weekly")
        title = (f"сравнение недели {week}" if is_weekly else f"ниша «{first['niche']}»") if first else "нет задач"
        L = [f"# День {day:02d} — {date} — {title}", "",
             f"Неделя {week}: {WEEKS.get(week, {}).get('theme', '')}. Отчёт обновлён {_date(self.clock())}.", "",
             DISCLAIMER_BLOCK, "## Что проверено", ""]
        if not tasks:
            L.append("- задачи на этот день ещё не запланированы")
        for t in tasks:
            r = res.get(t["role"])
            models = ", ".join(r["models"]) if r and r["models"] else "—"
            tools = ", ".join(r["tools"]) if r and r["tools"] else "—"
            err = f"; ошибка: {t['error'][:160]}" if t.get("error") and t["status"] != "done" else ""
            L.append(f"- {ROLE_RU.get(t['role'], t['role'])}: **{t['status']}** (попыток {t['attempts']}); "
                     f"модели: {models}; инструменты: {tools}{err}")
        rs = res.get("researcher", {}).get("content", {})
        cp = res.get("competitor", {}).get("content", {})
        if rs.get("sources"):
            L += ["", "Прочитанные источники:"] + [f"- {s['url']} ({s['date']}) {s.get('title', '')}"
                                                   for s in rs["sources"]]
        L += ["", "## Что найдено", ""]
        if is_weekly and res.get("weekly"):
            wc = res["weekly"]["content"]
            L.append(f"- выбрано гипотез (макс. {MAX_WEEKLY_HYPOTHESES}): {', '.join(wc.get('selected') or []) or 'нет'}")
            for label, ranking in (wc.get("rankings") or {}).items():
                L.append(f"- ранжирование {label}: {' > '.join(ranking) or 'неизвестно'}")
        obs = rs.get("observations", [])
        if obs:
            L.append("**Наблюдения (дословные цитаты подтверждены текстом страницы):**")
            L += [f"- {o['fact']} — «{o['quote']}» ({o['url']}, {o['date']})" for o in obs]
        for key, label in (("conclusions", "Выводы"), ("assumptions", "Предположения (не проверены)")):
            if rs.get(key):
                L.append(f"**{label}:**")
                L += [f"- {x}" for x in rs[key]]
        if cp:
            L.append(f"**Публичные предложения конкурентов ({len(cp.get('offers', []))}; "
                     f"{cp.get('disclaimer', '')}):**")
            L += [f"- {o['name']}: {o['offer']}; цена: {o['price_text']} ({o['url']})" for o in cp.get("offers", [])]
        pk = res.get("packager", {}).get("content")
        if pk:
            o = pk.get("offer", {})
            L += ["**Оффер-гипотеза:**", f"- для кого: {o.get('for_whom')}", f"- рутинная задача: {o.get('routine_task')}",
                  f"- результат: {o.get('deliverable')}", f"- срок: {o.get('timeframe')}",
                  f"- гипотеза цены: {o.get('price_hypothesis')} (не проверена покупателями)",
                  "**Оценки 1–5 (упаковщик → критик):**"]
            cs = (res.get("critic", {}).get("content") or {}).get("adjusted_scores", {})
            for k, ru in CRITERIA.items():
                L.append(f"- {ru}: {pk.get('scores', {}).get(k)} → {cs.get(k, '—')}")
        dr = res.get("draft", {}).get("content")
        if dr and dr.get("sample_path"):
            L.append(f"**Образец:** {dr['sample_kind']} — `{dr['sample_path']}`")
        cr = res.get("critic", {}).get("content")
        if cr:
            L.append("**Критика:**")
            L += [f"- не заплатят, потому что: {x}" for x in cr.get("reasons_wont_pay", [])]
            L += [f"- слабое место: {x}" for x in cr.get("weak_points", [])]
            L += [f"- утверждение без опоры: {x}" for x in cr.get("unsupported_claims", [])]
        if len(L) and L[-1] == "":
            L.append("- пока ничего")
        L += ["", "## Что неизвестно", ""]
        unknowns = list(rs.get("unknowns", [])) + list(cp.get("unknowns", []))
        unv = len(rs.get("unverified", [])) + len(cp.get("unverified", []))
        if unv:
            unknowns.append(f"{unv} утверждений модели не подтверждены текстом источника и не учтены как факты")
        unknowns.append("готовность платить: неизвестно — контактов с покупателями не было")
        L += [f"- {u}" for u in unknowns]
        L += ["", "## Расход моделей", "", _spend_table(self.store.spend_rows(task_ids=self._day_task_ids(day))),
              "## Следующий шаг", "", self._next_step(tasks), ""]
        path = self.reports_dir / f"day-{day:02d}.md"
        path.write_text("\n".join(L), encoding="utf-8")
        return path

    @staticmethod
    def _next_step(tasks: list[dict]) -> str:
        pending = [t for t in tasks if t["status"] in ("pending", "running")]
        failed = [t for t in tasks if t["status"] == "failed"]
        if pending:
            return f"- продолжить: {', '.join(ROLE_RU.get(t['role'], t['role']) for t in pending)} (следующий тик)"
        if failed:
            return "- разобрать ошибки в error log; проверить предложения в proposals/"
        return ("- день закрыт; гипотеза ждёт недельного сравнения. Проверка готовности платить — только после "
                "одобрения владельца (план в final.md)")

    # ---- proposals -------------------------------------------------------------------------------------------
    def write_proposals_if_complete(self, day: int) -> Path | None:
        tasks = self.store.tasks(day=day)
        if not tasks or any(t["status"] in ("pending", "running") for t in tasks):
            return None
        path = self.config.run_dir / "proposals" / f"day-{day:02d}-proposals.md"
        if path.exists():
            return path
        res = _results_by_role(self.store, day)
        items: list[tuple[str, str, str]] = []
        rs = res.get("researcher", {}).get("content", {})
        cp = res.get("competitor", {}).get("content", {})
        cr = res.get("critic", {}).get("content", {})
        if len(rs.get("observations", [])) < 3:
            items.append(("Мало подтверждённых наблюдений (< 3).",
                          "Больше фактов на нишу — оценки опираются на источники, а не на догадки модели.",
                          "Добавить в config.json `seed_urls` для ниши публичные каталоги/форумы; сравнить число "
                          "подтверждённых цитат в следующем day-XX.md."))
        if len(cp.get("offers", [])) < 3:
            items.append(("Найдено меньше 3 подтверждённых предложений конкурентов.",
                          "Полная картина цен и пакетов для упаковки оффера.",
                          "Задать `region` и `seed_urls`; критерий — в отчёте >= 3 предложений с цитатами."))
        unv = len(rs.get("unverified", [])) + len(cp.get("unverified", []))
        if unv > len(rs.get("observations", [])):
            items.append(("Модель чаще даёт неподтверждённые цитаты, чем подтверждённые.",
                          "Меньше выдуманных фактов.",
                          "Сравнить долю подтверждённых цитат по моделям в week-N.md; при стабильном отставании "
                          "модели — понизить её приоритет в маршрутизации (решение владельца)."))
        if cr and not cr.get("complete", True):
            items.append(("Критик дал меньше 3 причин отказа.", "Честнее оценка рисков.",
                          "Повторить критику другой моделью; критерий — >= 3 конкретные причины."))
        failed = [t for t in tasks if t["status"] == "failed"]
        if failed:
            items.append((f"Провалены задачи: {', '.join(t['role'] for t in failed)}.",
                          "Полный конвейер на нишу.", "Посмотреть error log (`status`), воспроизвести на тестовом "
                          "fake-model прогоне, затем повторить день."))
        if cr.get("reasons_wont_pay"):
            items.append((f"Главный риск по мнению критика: {cr['reasons_wont_pay'][0]}",
                          "Понять, снимается ли это возражение до разговора с покупателем.",
                          "Только с одобрения владельца: показать образец 3–5 владельцам бизнеса и записать "
                          "дословные ответы; до этого — «платёжный спрос не проверен»."))
        if not items:
            items.append(("Явных проблем конвейера не найдено.", "—",
                          "Сравнить с соседними днями в week-N.md."))
        L = [f"# Предложения по улучшению — день {day:02d}", "",
             "_Только предложения. Ничего не применяется автоматически; решение принимает владелец._", ""]
        for i, (why, effect, verify) in enumerate(items, 1):
            L += [f"## Предложение {i}", f"- Причина: {why}", f"- Ожидаемый эффект: {effect}",
                  f"- Как проверить: {verify}", ""]
        path.write_text("\n".join(L), encoding="utf-8")
        return path

    # ---- week ------------------------------------------------------------------------------------------------
    def write_week(self, week: int) -> Path:
        rows = week_niche_rows(self.store, week)
        sel = selected_hypotheses(rows)
        L = [f"# Неделя {week}: {WEEKS.get(week, {}).get('theme', '')}", "", f"Обновлено {_date(self.clock())}.", "",
             DISCLAIMER_BLOCK, "## Сравнение ниш (1–5, после критики)", ""]
        if rows:
            L.append("| день | ниша | " + " | ".join(CRITERIA.values()) + " | сумма | критика |")
            L.append("|---|---|" + "---|" * len(CRITERIA) + "---|---|")
            for r in sorted(rows, key=lambda r: r["total"], reverse=True):
                L.append(f"| {r['day']:02d} | {r['niche']} | " + " | ".join(str(r["scores"].get(k, "")) for k in CRITERIA)
                         + f" | {r['total']} | {'да' if r['critic_done'] else 'нет'} |")
        else:
            L.append("- ниши этой недели ещё не обработаны")
        L += ["", f"## Выбранные гипотезы (не больше {MAX_WEEKLY_HYPOTHESES})", ""]
        L += [f"- **{r['niche']}** ({r['total']}/25): {r['offer'].get('routine_task', '')} — {PAYMENT_DISCLAIMER}"
              for r in sel] or ["- пока нет (нужна завершённая критика)"]
        L += ["", "## Сравнение моделей", "",
              "Модели не считаются лучше друг друга заранее; ниже — факты этой недели.", ""]
        days = range((week - 1) * 7 + 1, week * 7 + 1)
        tids = [t["id"] for d in days for t in self.store.tasks(day=d)]
        L.append(_spend_table(self.store.spend_rows(task_ids=tids)))
        grounding = _grounding_by_model(self.store.results(week=week))
        if grounding:
            L.append("Подтверждаемость цитат (цитата найдена в тексте страницы):")
            for m, (v, u) in sorted(grounding.items()):
                share = f"{100 * v / (v + u):.0f}%" if v + u else "—"
                L.append(f"- {m}: подтверждено {v}, не подтверждено {u} ({share})")
        for r in self.store.results(week=week, role="weekly"):
            for label, ranking in (r["content"].get("rankings") or {}).items():
                L.append(f"- ранжирование {label} (день {r['day']:02d}): {' > '.join(ranking) or 'неизвестно'}")
        path = self.reports_dir / f"week-{week}.md"
        path.write_text("\n".join(L) + "\n", encoding="utf-8")
        return path

    # ---- final -----------------------------------------------------------------------------------------------
    def write_final(self, reason: str) -> Path:
        start = self.store.get_meta("start_ts")
        rows = [r for w in range(1, 5) for r in week_niche_rows(self.store, w)]
        rows.sort(key=lambda r: (r["critic_done"], r["total"], len(r["observations"])), reverse=True)
        counts = self.store.counts()
        L = ["# Итог 28-дневного исследования ниш", "",
             f"Причина завершения: {reason}. Старт: {_date(start) if start else '—'}; "
             f"дедлайн: {_date(deadline_ts(start)) if start else '—'}; отчёт: {_date(self.clock())}.", "",
             DISCLAIMER_BLOCK,
             "**Ни одна ниша не является доказанной**: ответов покупателей и оплат в этом исследовании не было.", "",
             f"Задачи: выполнено {counts['done']}, провалено {counts['failed']}, не выполнено "
             f"{counts['pending'] + counts['running']} (из плана на {RUN_DAYS} дней).", "",
             "## Рейтинг гипотез", ""]
        if not rows:
            L.append("- гипотез нет: ни одна ниша не прошла упаковку")
        for i, r in enumerate(rows, 1):
            nid = [t["id"] for t in self.store.tasks(day=r["day"])]
            spend = self.store.spend_rows(task_ids=nid)
            o = r["offer"]
            L += [f"### {i}. {r['niche']} — {r['total']}/25 (день {r['day']:02d}){'' if r['critic_done'] else ' — без критики'}",
                  f"- Оффер: для кого — {o.get('for_whom')}; задача — {o.get('routine_task')}; результат — "
                  f"{o.get('deliverable')}; срок — {o.get('timeframe')}; гипотеза цены — {o.get('price_hypothesis')}",
                  "- Доказательства (публичные источники, не спрос): "
                  + (", ".join(r["evidence"][:6]) or "неизвестно"),
                  "- Слабые места: " + ("; ".join(r["weak"][:5]) or "неизвестно"),
                  f"- Себестоимость производства: {r['production_cost']}; расход моделей на исследование: "
                  f"{sum(x['tokens_in'] + x['tokens_out'] for x in spend)} токенов, "
                  f"${sum(x['cost_usd'] for x in spend):.4f}",
                  f"- Статус: {PAYMENT_DISCLAIMER}", ""]
        L += ["## План проверки готовности платить (отдельно; только с одобрения владельца)", "",
              "1. Владелец выбирает не больше 2 гипотез из рейтинга и одобряет каждый шаг контакта.",
              "2. Список 10–20 бизнесов из публичных каталогов; контакт делает владелец, не автоматика.",
              "3. Показ образца из samples/ и вопрос о цене/предоплате; ответы записываются дословно.",
              "4. Критерий сигнала: письменное согласие на платный пилот или предоплата. Лайки/«интересно» — не спрос.",
              "5. До шага 4 статус каждой гипотезы — «платёжный спрос не проверен».", "",
              "## Расход моделей за весь прогон", "", _spend_table(self.store.spend_rows()),
              "Улучшения — только как предложения в proposals/, ничего не применено автоматически.", ""]
        path = self.reports_dir / "final.md"
        path.write_text("\n".join(L), encoding="utf-8")
        return path
