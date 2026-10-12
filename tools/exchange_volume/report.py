"""Human-readable report over an existing ``exchange_volume`` run (no recalculation, no new numbers).

Input: ``volume_result.json`` + ``deals.csv`` written by ``python -m exchange_volume.cli run``.
Output: ``report.md`` (periods, what is NOT in the total and why, disputed rows with message references,
limits of the figure) and ``disputed.csv``. Every number in the report is copied from the calculator's output.

Rules this file obeys (owner brief 12.10, Reports direction):
* turnover is one side of the deal (the USDT leg) and is never called profit; commission is shown as absent when
  the chat has none;
* an incomplete input is stated as incomplete: the total is "of the recognised deals", a lower bound, not exact;
* duplicates and unconfirmed amounts are listed separately with the message reference (``ref``) of the export.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

SCHEMA = "swapme.exchange-volume/2"
_STATUS = {
    "counted": "учтена",
    "excluded_duplicate": "дубль (исключена)",
    "provisional": "предварительно, вне итога",
    "excluded_internal": "внутренняя (исключена)",
}


_RULES_RU = {
    "business_time_zone": "время Праги (правило перехода ЕС), подтверждено владельцем",
    "period": "бизнес-месяц с 21-го по 20-е включительно, назван по дате закрытия",
    "volume_definition": ("оборот USDT = сумма USDT-ноги каждой учтённой сделки (одна сторона, не вход плюс выход); "
                          "сделки без USDT-ноги без внешнего курса не оцениваются"),
    "duplicate_rule": "одинаковые суммы входа и выхода в коротком окне после уже учтённой сделки",
    "internal_turnover": "исключаются только сообщения из списка оператора; зеркальные пары помечаются, не удаляются",
    "rates": "рыночных курсов в чате нет; исполненный курс = сумма выхода / сумма входа по паре; внешнего курса нет",
    "commission": "в сообщениях не указана; не рассчитывается, пока она не названа в USDT в каждой учтённой сделке",
}


def _num(value) -> str:
    if value in (None, ""):
        return "—"
    text = str(value)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _refs(values) -> str:
    return ", ".join(f"#{v}" for v in values) if values else "нет"


def load(result_path: Path, deals_path: Path) -> tuple[dict, list[dict]]:
    result = json.loads(Path(result_path).read_text(encoding="utf-8"))
    if result.get("schema") != SCHEMA:
        raise ValueError(f"unsupported result schema {result.get('schema')!r}, expected {SCHEMA}")
    with open(deals_path, encoding="utf-8", newline="") as handle:
        deals = list(csv.DictReader(handle))
    return result, deals


def disputed_rows(result: dict, deals: list[dict]) -> list[dict]:
    """Rows a human must look at: duplicates, provisional/complex, flagged, unpriced, plus unparsed messages."""
    rows: list[dict] = []
    for d in deals:
        reason = []
        if d.get("status") == "excluded_duplicate":
            reason.append(f"дубль сделки #{d.get('dup_of')} (одинаковые суммы в окне)")
        if d.get("status") == "provisional":
            reason.append("сложное сообщение: учтены первые две пары, вне итога")
        if d.get("flags"):
            reason.append(f"флаги: {d['flags']}")
        if d.get("status") == "counted" and not d.get("usdt_volume"):
            reason.append("нет USDT-ноги: оборот в USDT не оценён без внешнего курса")
        if reason:
            rows.append({"ref": d["ref"], "time_prague": d.get("ts_prague", ""),
                         "in": f"{_num(d.get('in_amt'))} {d.get('in_ccy', '')}".strip(),
                         "out": f"{_num(d.get('out_amt'))} {d.get('out_ccy', '')}".strip(),
                         "status": _STATUS.get(d.get("status", ""), d.get("status", "")),
                         "reason": "; ".join(reason)})
    cls = result.get("message_classification", {})
    for key, text in (("complex_refs", "сложное сообщение (несколько сделок/пар), разбор неоднозначен"),
                      ("incomplete_refs", "неполное сообщение: нет одной из сторон сделки"),
                      ("ambiguous_amount_refs", "неоднозначная сумма: несколько кандидатов")):
        for ref in cls.get(key, []):
            if not any(r["ref"] == str(ref) for r in rows):
                rows.append({"ref": str(ref), "time_prague": "", "in": "—", "out": "—",
                             "status": "не разобрано", "reason": text})
    rows.sort(key=lambda r: (int(r["ref"]) if str(r["ref"]).isdigit() else 10**9, r["reason"]))
    return rows


def render(result: dict, deals: list[dict]) -> str:
    src, cls, assume = result.get("source", {}), result.get("message_classification", {}), result.get("assumptions", {})
    periods = result.get("periods", {})
    unresolved = (len(cls.get("complex_refs", [])) + len(cls.get("incomplete_refs", []))
                  + len(cls.get("ambiguous_amount_refs", [])))
    out = ["# Отчёт по обороту", "",
           f"Источник: Telegram-экспорт (`{src.get('type', '?')}`), сообщений: {src.get('messages', '?')}, "
           f"данные с {src.get('first_ts_export_local', '?')} по {src.get('last_ts_export_local', '?')} "
           f"(время экспорта). Контрольная сумма файла: `{str(src.get('sha256', ''))[:16]}…`.",
           f"Расчёт на дату (Прага): {result.get('as_of_prague_date', '?')}. Схема результата: `{result.get('schema')}`.", "",
           "## Главное", ""]
    closed = [k for k, p in periods.items() if p.get("status") == "closed"]
    open_p = [k for k, p in periods.items() if p.get("status") != "closed"]
    out.append(f"- Периодов в данных: {len(periods)} (закрытых: {len(closed)}, незакрытых на дату экспорта: {len(open_p)}).")
    if unresolved:
        out.append("- **Итоги ниже — по распознанным сделкам и могут быть занижены.** Не разобрано автоматически "
                   f"(сложные, неполные, с неоднозначной суммой): {unresolved}; они перечислены ниже и в итоговые "
                   "суммы не входят.")
    else:
        out.append("- Все сообщения экспорта классифицированы; неразобранных нет (это не доказывает полноту самого экспорта).")
    out.append("- Оборот — это сумма USDT-ноги учтённых сделок (одна сторона сделки). **Это не прибыль и не доход.**")
    out += ["", "## Итог по периодам (21-е — 20-е, время Праги)", "",
            "| Период (закрытие) | Границы | Статус | Сделок учтено | Оборот USDT, одна сторона | Получено USDT | "
            "Выплачено USDT | Дубли | Без USDT-ноги | Комиссия |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for key, p in periods.items():
        status = "закрыт" if p.get("status") == "closed" else "не закрыт на дату экспорта"
        out.append(f"| {key} | {p.get('from_prague')} … {p.get('to_prague')} | {status} | {p.get('deals_counted')} | "
                   f"**{_num(p.get('gross_usdt_direct'))}** | {_num(p.get('usdt_received_from_clients'))} | "
                   f"{_num(p.get('usdt_paid_to_clients'))} | {p.get('excluded_duplicates')} | "
                   f"{p.get('unpriced_deals')} ({', '.join(p.get('unpriced_pairs', [])) or '—'}) | "
                   f"{_num(p.get('commission_usdt')) if p.get('commission_usdt') is not None else 'не найдена в данных'} |")
    out += ["", "Колонка «Без USDT-ноги»: сделки, где в сообщении нет USDT; их оборот в USDT не оценивается без "
            "внешнего курса, поэтому они **не входят** в итог.", ""]

    out += ["## Что не входит в итог и почему", "",
            f"- Дубли (одинаковые суммы в окне {result.get('duplicates', {}).get('window_min', '?')} мин): "
            f"{result.get('duplicates', {}).get('excluded', 0)} шт.",
            f"- Сложные сообщения: {_refs(cls.get('complex_refs'))}"
            + (f" — ориентировочно {_num(cls.get('complex_provisional_usdt_leg_sum_not_in_totals'))} USDT вне итога"
               if cls.get("complex_provisional_usdt_leg_sum_not_in_totals") not in (None, "0") else ""),
            f"- Неполные сообщения: {_refs(cls.get('incomplete_refs'))}",
            f"- Сообщения с неоднозначной суммой (неподтверждённые): {_refs(cls.get('ambiguous_amount_refs'))}",
            f"- Внутренние сделки, исключённые по списку оператора: {result.get('internal_excluded', 0)}", ""]

    rows = disputed_rows(result, deals)
    out += ["## Спорные строки (проверить вручную)", ""]
    if rows:
        out += ["| Ссылка (сообщение) | Время (Прага) | Вход | Выход | Статус | Почему спорная |", "|---|---|---|---|---|---|"]
        out += [f"| #{r['ref']} | {r['time_prague'] or '—'} | {r['in']} | {r['out']} | {r['status']} | {r['reason']} |"
                for r in rows]
    else:
        out.append("Спорных строк нет.")
    sens = result.get("sensitivity_export_wallclock_date_instead_of_prague", {})
    if result.get("deals_that_change_business_period_with_zone_choice"):
        out += ["", f"Сделок, у которых период зависит от выбора часового пояса: "
                    f"{result['deals_that_change_business_period_with_zone_choice']} (граница 20-го числа около полуночи). "
                    f"Расчёт идёт по времени Праги; при другом поясе итог периода изменится: {json.dumps(sens, ensure_ascii=False)}."]

    out += ["", "## Правила расчёта и границы", ""]
    for key, label in (("business_time_zone", "Часовой пояс"), ("period", "Период"), ("volume_definition", "Оборот"),
                       ("duplicate_rule", "Дубли"), ("internal_turnover", "Внутренние сделки"), ("rates", "Курсы"),
                       ("commission", "Комиссия")):
        if assume.get(key):
            # Russian gloss first; the calculator's own wording stays next to it so nothing is paraphrased away.
            out.append(f"- **{label}:** {_RULES_RU[key]} _(в расчёте: {assume[key]})_")
    out += ["", "## Чем этот отчёт не является", "",
            "- Не прибыль и не доход: комиссия, расходы и налоги здесь не рассчитываются.",
            "- Не точная бухгалтерская сверка: источник — переписка; полнота экспорта не доказана.",
            "- Фото и вложения без текста не читаются (см. «Спорные строки», если они были распознаны как сообщения).",
            "- Каждая сумма сопоставима с сообщением по ссылке `#ref` в исходном экспорте.", ""]
    return "\n".join(out)


def write(result_path: Path, deals_path: Path, out_dir: Path) -> dict:
    result, deals = load(result_path, deals_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / "report.md"
    report.write_text(render(result, deals), encoding="utf-8")
    rows = disputed_rows(result, deals)
    disputed = out_dir / "disputed.csv"
    with open(disputed, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["ref", "time_prague", "in", "out", "status", "reason"])
        writer.writeheader()
        writer.writerows(rows)
    return {"report": str(report), "disputed": str(disputed), "disputed_rows": len(rows)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="exchange_volume.report")
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--deals", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(write(args.result, args.deals, args.out), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
