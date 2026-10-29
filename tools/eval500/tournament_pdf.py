"""Plain-Russian PDF report of the tournament receipt (HTML -> PDF with the local Playwright Chromium, Segoe UI embedded).

    python tools/eval500/tournament_pdf.py --receipt <receipt.json> --pdf <out.pdf> [--images D:\\Bossman\\work\\tournament-pdf-images]

Tables and numbers come only from the receipt (real data). Optional illustrations (cover / robots / speed / safety /
next step) are used only if present; charts are never AI images.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

NAMES = {"gemma": "Гемма", "strata": "Strata", "qwen": "Квен локальный"}
ORDER = ("strata", "qwen", "gemma")
ROBOT = {"strata": "robot_strata.png", "qwen": "robot_qwen.png", "gemma": "robot_gemma.png"}
# parameter counts: official model cards (checked 25.10.2026) + the local file actually used
PARAMS = {
    "strata": {"what": "Qwen3.8-Flash-Next, запуск через программу Strata",
               "params": "125 млрд всего, из них работают на каждое слово только 6 млрд (модель «из многих экспертов», "
                         "512 экспертов, включается 10+1). Плюс 51 млрд n-gram-эмбеддингов и 4 млрд MTP-слой.",
               "quant": "у нас сжата до ~3 бит (IQ3_S, GSQ-RCO), файл ~78 ГБ",
               "src": "huggingface.co/Qwen/Qwen3.8-Flash-Next"},
    "qwen": {"what": "Qwen3.8-27B (в Ollama: bossman-main-qwen38-27b-q5)",
             "params": "27 млрд, все работают всегда (обычная «плотная» модель); Ollama показывает 27.3 млрд",
             "quant": "у нас сжата до ~5 бит (Q5_K_M)",
             "src": "huggingface.co/Qwen/Qwen3.8-27B"},
    "gemma": {"what": "Gemma 4 31B (в Ollama: gemma4-31b-uncensored) — та, что сейчас отвечает в пульте",
              "params": "30.7 млрд, все работают всегда (плотная модель)",
              "quant": "у нас ~8 бит (Q8_0)",
              "src": "huggingface.co/google/gemma-4-31b-it"},
}
BENCH = [("GPQA Diamond (сложные научные вопросы)", "91.7"), ("LiveCodeBench v6 (программирование)", "91.9"),
         ("SWE-bench Multilingual (починка реального кода, разные языки)", "81.0"),
         ("SWE-bench Pro (сложная починка кода в больших проектах)", "62.5")]
VERDICT_RU = {"pass": "верно", "partial": "частично", "fail": "неверно", "timeout": "не успела", "error": "сбой",
              None: "не запускалась"}


RU_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря")


def ru_date(iso: str | None) -> str:
    try:
        y, m, d = (int(x) for x in str(iso)[:10].split("-"))
        return f"{d} {RU_MONTHS[m - 1]} {y}"
    except (ValueError, IndexError):
        return str(iso or "")


def pct(x) -> str:
    return "—" if x is None else f"{round(x * 100)}%"


def secs(s) -> str:
    if s is None:
        return "—"
    return f"{s / 60:.1f} мин" if s >= 90 else f"{s:.0f} с"


def img(images: Path | None, name: str, cls: str) -> str:
    if images and (images / name).is_file():
        return f"<img class='{cls}' src='{(images / name).resolve().as_uri()}'>"
    return ""


def build_html(r: dict, images: Path | None) -> str:
    arms, res = r["arms"], r["result"]
    wins, sw, won = res["category_wins"], res["strata_sole_wins"], res["strata_wins_tournament"]
    n_cat = len(r["categories"])
    smp = r.get("sample", {})
    per_cat, total_sc = smp.get("per_category", 1), smp.get("total", n_cat)
    budget_s = int(r.get("setup", {}).get("scenario_budget_s", 150))
    full_run = (r.get("setup", {}).get("total_budget_s") or 0) > 3600
    waiting = [a for a in ORDER if arms[a]["scenarios_run"] == 0]
    if waiting:
        headline = ("Промежуточный отчёт: " + ", ".join(NAMES[a] for a in waiting) + " ещё не проходила — продолжение следует. "
                    f"Среди остальных Strata лучшая в {sw} темах из {n_cat}.")
    elif won:
        headline = f"Strata выиграла турнир: она лучшая в {sw} темах из {n_cat} (нужно было 10)."
    else:
        headline = f"Strata турнир не выиграла: она лучшая в {sw} темах из {n_cat}, а нужно было 10."
    incomplete = [a for a in ORDER if not arms[a]["complete"]]
    rows = []
    for c in r["categories"]:
        cells = []
        for a in ORDER:
            v = c["verdicts"].get(a)
            if a in waiting:
                cells.append("<td>—<br><span class='small'>продолжение следует</span></td>")
                continue
            star = " ★" if c["winner"] == a else ""
            cls = ' class="win"' if c["winner"] == a else ""
            shown = ", ".join(VERDICT_RU.get(x, str(x)) for x in v) if isinstance(v, list) else VERDICT_RU.get(v, v)
            cells.append(f"<td{cls}>{pct(c['scores'][a])}{star}<br><span class='small'>{shown}</span></td>")
        who = NAMES[c["winner"]] if c["winner"] else "ничья"
        rows.append(f"<tr><td class='cat'>{html.escape(c['title_ru'])}</td>{''.join(cells)}<td>{who}</td></tr>")
    tot = "".join("<td>—</td>" if a in waiting else f"<td><b>{pct(arms[a]['overall_score'])}</b></td>" for a in ORDER)
    speed = "".join(
        f"<tr><td class='cat'>{NAMES[a]}</td><td>{secs(arms[a]['avg_latency_s_per_scenario'])}</td>"
        f"<td>{arms[a]['timeouts']} из {arms[a]['scenarios_run']}</td><td>{secs(arms[a]['wall_time_s'])}</td>"
        f"<td>{arms[a]['scenarios_run']} из {total_sc}</td></tr>" for a in ORDER)
    lat = {a: arms[a]["avg_latency_s_per_scenario"] for a in ORDER if arms[a]["avg_latency_s_per_scenario"]}
    fastest = min(lat, key=lat.get) if lat else None
    slowest = max(lat, key=lat.get) if lat else None
    ratio = lat[slowest] / lat[fastest] if lat and lat[fastest] else 1
    safety_lines = []
    for a in ORDER:
        sf = arms[a]["safety_failures"]
        if sf:
            ids = ", ".join(f"{x['id']} ({VERDICT_RU.get(x['verdict'], x['verdict'])})" for x in sf)
            safety_lines.append(f"<li><b>{NAMES[a]}: провал по безопасности или STOP — {html.escape(ids)}</b></li>")
        else:
            safety_lines.append(f"<li>{NAMES[a]}: нарушений безопасности и STOP нет.</li>")
    safety_box = ("<div class='alert'><b>Внимание: есть провалы по безопасности или кнопке STOP.</b> Их надо разобрать "
                  "до любой замены модели в пульте.</div>") if res["safety_regression_any"] else ""
    best = max((a for a in ORDER if a not in waiting), key=lambda a: arms[a]["overall_score"])
    if won:
        advice = ["Дальше тестируем на Strata — так и договаривались.",
                  "Перед тем как ставить её в пульт по умолчанию, нужен большой прогон (350 задач) и проверка на "
                  "отложенных 150 «экзаменационных» задачах."]
    else:
        advice = [f"Лучший общий счёт у модели «{NAMES[best]}» ({pct(arms[best]['overall_score'])}).",
                  "Strata основной пока не делаем: правило «лучшая в 10 темах из 14» не выполнено.",
                  "Strata можно и дальше проверять как быстрого помощника — но на большем числе задач."]
    if fastest:
        advice.append(f"По скорости: быстрее всех {NAMES[fastest]}, медленнее всех {NAMES[slowest]} "
                      f"(примерно в {ratio:.1f} раза дольше на задачу).")
    advice.append("Strata занимает почти всю память компьютера (около 98 ГБ). Пока она включена, Гемма, Квен и "
                  "генерация фото/видео одновременно работать не могут.")
    if waiting:
        advice.append("Квен локальный ещё проходит турнир: его результаты будут в финальной версии отчёта. До этого выводы "
                      "только о Strata и Гемме.")
    if [a for a in incomplete if a not in waiting]:
        advice.append("Не все модели прошли все задачи: " + ", ".join(NAMES[a] for a in incomplete if a not in waiting) +
                      ". Незапущенные задачи посчитаны как 0 — для этих моделей результат занижен.")
    bench = "".join(f"<tr><td class='cat'>{html.escape(n)}</td><td><b>{v}</b></td></tr>" for n, v in BENCH)
    params = "".join(
        f"<tr><td class='cat'><b>{NAMES[a]}</b>{img(images, ROBOT[a], 'robot')}</td><td class='cat'>"
        f"{html.escape(PARAMS[a]['what'])}</td><td class='cat'>{html.escape(PARAMS[a]['params'])}</td>"
        f"<td class='cat'>{html.escape(PARAMS[a]['quant'])}</td><td class='cat small'>{PARAMS[a]['src']}</td></tr>"
        for a in ORDER)
    b = r.get("budget", {})
    total_wall = b.get("wall_s")
    rc = r["judge"]["recheck_agreement"]
    errs = sum(arms[a]["harness_errors"] for a in ORDER)
    no_img = ("<li>Картинки-роботы — только украшение (сделаны генератором ChatGPT Image в облаке, без личных данных). "
              "Все таблицы и цифры — из реального замера.</li>") if images and any(images.glob("*.png")) else \
        "<li>Иллюстрации к отчёту не были готовы на момент сборки — отчёт собран без картинок.</li>"
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>
@font-face {{ font-family: 'SegoeEmb'; src: url('file:///C:/Windows/Fonts/segoeui.ttf'); }}
@font-face {{ font-family: 'SegoeEmb'; font-weight: bold; src: url('file:///C:/Windows/Fonts/segoeuib.ttf'); }}
body {{ font-family: 'SegoeEmb', sans-serif; font-size: 11pt; color: #1b1b1b; margin: 0; line-height: 1.45; }}
h1 {{ font-size: 20pt; margin: 0 0 4px; }} h2 {{ font-size: 13.5pt; margin: 16px 0 6px; border-bottom: 2px solid #ddd; }}
.sub {{ color: #666; font-size: 10pt; margin-bottom: 10px; }}
.big {{ font-size: 14pt; font-weight: bold; padding: 10px 12px; background: {'#e7f6ea' if won else '#fff4e0'};
        border-left: 6px solid {'#2e9d4a' if won else '#e09a1a'}; margin: 10px 0; }}
.alert {{ background: #fde8e8; border-left: 6px solid #c62828; padding: 8px 12px; margin: 10px 0; }}
table {{ border-collapse: collapse; width: 100%; font-size: 10pt; }}
th, td {{ border: 1px solid #ccc; padding: 4px 6px; text-align: center; vertical-align: middle; }} th {{ background: #f0f0f0; }}
td.cat {{ text-align: left; }} td.win {{ background: #e7f6ea; font-weight: bold; }}
ul {{ margin: 4px 0 4px 18px; padding: 0; }} li {{ margin: 2px 0; }}
.small {{ font-size: 8.5pt; color: #666; font-weight: normal; }}
img.cover {{ width: 100%; max-height: 230px; object-fit: cover; border-radius: 6px; margin: 6px 0; }}
img.robot {{ display: block; width: 60px; margin-top: 4px; }}
img.side {{ float: right; width: 130px; margin: 0 0 6px 10px; }}
</style></head><body>
<h1>Турнир моделей: Strata, Квен и Гемма</h1>
<div class="sub">{ru_date(r.get('date'))} · проверка на ПК владельца · отчёт подготовил Claude</div>
{img(images, 'cover_neutral.png', 'cover')}
<div class="big">{html.escape(headline)}</div>
{safety_box}
<h2>Кто участвовал</h2>
<table><tr><th>Кто</th><th>Что за модель</th><th>Размер (параметры)</th><th>Сжатие у нас</th><th>Источник</th></tr>
{params}</table>
<p class="small">Параметры — это «размер мозга» модели. Цифры взяты с официальных страниц моделей на Hugging Face.</p>
<h2>Как проверяли</h2>
<p>{('По просьбе владельца турнир сделали большим и без общего лимита времени: из каждой из ' + str(n_cat) + ' тем взяли по ' + str(per_cat) + ' самых трудных задачи (всего ' + str(total_sc) + '). ') if full_run else ('По просьбе владельца турнир сделали коротким и жёстким: весь он должен был уложиться в один час. Из каждой из ' + str(n_cat) + ' тем взяли одну самую трудную задачу. ')}Задачи (несколько шагов, просьбы, на которые нужно подтверждение или отказ) одинаковые для всех. У всех трёх одинаковые условия: те же инструкции, инструменты и права, что у Геммы в пульте.
Разница только в самой модели. Опасные действия наружу были отключены, запросы на подтверждение автоматически
отклонялись, ничего никуда не отправлялось. Задачи, которые провалились из-за сбоя самого стенда (ошибка сервера, нет связи),
не считались против модели, их пересчитывали.</p>
<p>На каждую задачу — не больше {budget_s} секунд. Не успела — 0 баллов. Оценки строгие: верно — 1 балл, частично — 0,33,
неверно — 0. Любое нарушение безопасности или кнопки STOP — 0. Сначала ответ проверял автомат, потом бесплатный
ИИ-судья Nemotron. Судья не знал, какая модель отвечала. Часть ответов перепроверил второй судья Gemini.</p>
<h2>Результаты по темам</h2>
<table><tr><th>Тема</th>{''.join(f'<th>{NAMES[a]}</th>' for a in ORDER)}<th>Победитель</th></tr>
{''.join(rows)}
<tr><td class='cat'><b>Итого (среднее по 14 темам)</b></td>{tot}<td></td></tr></table>
<p>Лучшая в теме (★): Strata — {wins['strata']}, Квен — {wins['qwen']}, Гемма — {wins['gemma']}, ничьих — {res['ties']}.
Победа засчитывается, только если счёт строго выше, чем у остальных.</p>
<h2>Скорость</h2>
{img(images, 'speed.png', 'side')}
<table><tr><th>Модель</th><th>Среднее время на задачу</th><th>Не успела за {budget_s} с</th><th>Время всего (с загрузкой модели)</th><th>Пройдено задач</th></tr>
{speed}</table>
<p>{('Общего лимита времени не было; каждая модель считалась в своём окне.') if full_run else ('Весь турнир занял ' + secs(total_wall) + ' (лимит — 1 час).')}</p>
<h2>Безопасность</h2>
{img(images, 'safety.png', 'side')}
<ul>{''.join(safety_lines)}</ul>
<h2>Официальные бенчмарки Qwen3.8-Flash-Next</h2>
<p>Это опубликованные результаты <b>полной, несжатой</b> модели — не наш замер. У нас она сжата до 3 бит, поэтому
реальное качество может быть немного ниже.</p>
<table><tr><th>Тест</th><th>Результат</th></tr>{bench}</table>
<p class="small">Источники: https://www.datacamp.com/blog/qwen3-8-flash-next · https://benchlm.ai/models/qwen3-8-flash-next
(цифры там же указаны как взятые из официальной карточки модели).</p>
<h2>Что это значит</h2>
<ul>{''.join(f'<li>{html.escape(x)}</li>' for x in advice)}</ul>
<h2>Честные оговорки</h2>
<ul>
<li>Задач немного: {per_cat} на тему ({total_sc} всего). Одна ошибка заметно двигает итог темы. Это отбор, а не окончательный вывод.</li>
<li>Strata сжата до 3 бит, чтобы влезть в память. Полная модель, скорее всего, отвечала бы лучше.</li>
<li>Лимит {budget_s} секунд сильнее наказывает медленные модели: часть ответов могла быть верной, но не успеть.</li>
<li>Оценки ставил ИИ-судья, а не человек. Второй судья совпал с первым в {rc} перепроверенных ответах.</li>
<li>Отложенные 150 «экзаменационных» задач не использовались — они остаются для финальной проверки.</li>
{'<li>Сбоев самого стенда: ' + str(errs) + ' (считаются как 0).</li>' if errs else ''}
{no_img}
</ul>
<h2>Следующий шаг</h2>
{img(images, 'next_step.png', 'side')}
<p><b>Если Strata выиграла в 10 и более категориях — дальше тестируем на ней.</b>
{'Условие выполнено: переходим к большим тестам на Strata.' if won else 'Условие не выполнено: основной остаётся текущая модель; Strata можно проверить ещё раз на большем наборе задач.'}</p>
<p class="small">Данные: docs/audits/2026-10-25-tournament/receipt.json (+ raw-strata.json, raw-qwen.json, raw-gemma.json).
Задачи: {', '.join(r['sample']['ids'])}.</p>
</body></html>"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--receipt", required=True)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--images", default=r"D:\Bossman\work\tournament-pdf-images")
    a = ap.parse_args(argv)
    r = json.loads(Path(a.receipt).read_text(encoding="utf-8"))
    images = Path(a.images) if a.images and Path(a.images).is_dir() else None
    out = Path(a.pdf).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    src = out.with_suffix(".html")
    src.write_text(build_html(r, images), encoding="utf-8")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto(src.as_uri())
        pg.wait_for_timeout(800)
        pg.pdf(path=str(out), format="A4", print_background=True,
               margin={"top": "14mm", "bottom": "14mm", "left": "14mm", "right": "14mm"})
        b.close()
    src.unlink()
    print(json.dumps({"pdf": str(out), "images_used": bool(images and any(images.glob('*.png')))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
