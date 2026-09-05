"""Генератор сайтов визуального веб-дизайнера: шаблоны, палитры, пошаговая сборка.

Принцип честности: генерация детерминирована. Один и тот же запрос даёт один и
тот же сайт — тест может это проверить, а владелец получает предсказуемый
результат без лотереи. «Живая» сборка в UI — это список кумулятивных шагов
(steps): каждый шаг — валидный самостоятельный HTML, UI показывает их по очереди
в iframe, и сайт растёт секция за секцией, как при стриминге у моделей.

Выбор шаблона и палитры — по ключевым словам запроса (русский и английский).
Ничего не подошло — берутся разумные дефолты: лендинг и индиго-палитра.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- палитры

# Каждый цвет в одном месте: bg/surface — фон и карточки, ink/muted — текст,
# accent/accent2 — акценты, hero — градиент акцентных секций.
PALETTES: dict[str, dict[str, str]] = {
    "indigo": {
        "bg": "#f6f7fb", "surface": "#ffffff", "ink": "#12141f", "muted": "#5d6478",
        "accent": "#4f46e5", "accent2": "#8b5cf6",
        "hero": "linear-gradient(135deg, #4f46e5 0%, #8b5cf6 60%, #a78bfa 100%)",
    },
    "green": {
        "bg": "#f5faf6", "surface": "#ffffff", "ink": "#0f1f16", "muted": "#52685c",
        "accent": "#0f9d58", "accent2": "#34d399",
        "hero": "linear-gradient(135deg, #065f46 0%, #0f9d58 65%, #34d399 100%)",
    },
    "blue": {
        "bg": "#f4f8fd", "surface": "#ffffff", "ink": "#0e1a28", "muted": "#4f6478",
        "accent": "#1d6fe0", "accent2": "#38bdf8",
        "hero": "linear-gradient(135deg, #0b3d7a 0%, #1d6fe0 60%, #38bdf8 100%)",
    },
    "rose": {
        "bg": "#fdf5f7", "surface": "#ffffff", "ink": "#251016", "muted": "#7a5260",
        "accent": "#e11d48", "accent2": "#fb7185",
        "hero": "linear-gradient(135deg, #881337 0%, #e11d48 60%, #fb7185 100%)",
    },
    "orange": {
        "bg": "#fdf8f3", "surface": "#ffffff", "ink": "#241609", "muted": "#7a6350",
        "accent": "#ea7317", "accent2": "#fbbf24",
        "hero": "linear-gradient(135deg, #7c2d12 0%, #ea7317 60%, #fbbf24 100%)",
    },
    "gold": {
        "bg": "#fbf9f2", "surface": "#ffffff", "ink": "#201a08", "muted": "#6f6544",
        "accent": "#b8860b", "accent2": "#eab308",
        "hero": "linear-gradient(135deg, #4a3802 0%, #b8860b 60%, #eab308 100%)",
    },
    "violet": {
        "bg": "#f9f6fd", "surface": "#ffffff", "ink": "#181025", "muted": "#5f5478",
        "accent": "#7c3aed", "accent2": "#c084fc",
        "hero": "linear-gradient(135deg, #3b0764 0%, #7c3aed 60%, #c084fc 100%)",
    },
    "teal": {
        "bg": "#f2fafb", "surface": "#ffffff", "ink": "#0b2024", "muted": "#4a6a70",
        "accent": "#0d9488", "accent2": "#2dd4bf",
        "hero": "linear-gradient(135deg, #134e4a 0%, #0d9488 60%, #2dd4bf 100%)",
    },
    "dark": {
        "bg": "#0b0e17", "surface": "#141a2b", "ink": "#e7eaf3", "muted": "#9aa3bd",
        "accent": "#22d3ee", "accent2": "#818cf8",
        "hero": "linear-gradient(135deg, #0b0e17 0%, #1e2a4a 60%, #312e81 100%)",
    },
}

PALETTE_WORDS: tuple[tuple[str, str], ...] = (
    (r"зелён|изумруд|green|emerald|эколог", "green"),
    (r"син|голуб|blue|azure|небес", "blue"),
    (r"красн|розов|алый|бордо|rose|pink", "rose"),
    (r"оранж|янтарн|orange|персик", "orange"),
    (r"золот|жёлт|желт|gold|yellow|премиум|люкс", "gold"),
    (r"фиолет|сирен|лаванд|violet|purple", "violet"),
    (r"бирюз|мятн|teal|mint|аква", "teal"),
    (r"тёмн|темн|черн|dark|black|ноч|night|неон", "dark"),
)

TEMPLATE_WORDS: dict[str, str] = {
    "portfolio": r"портфолио|portfolio|фотограф|дизайнер|художник|работы|кейсы|галере",
    "cafe": r"кафе|ресторан|кофе|coffee|пекарн|кондитер|food|бар\b|столов",
    "shop": r"магазин|shop|store|товар|продав|ecommerce|бутик|доставк|заказ",
    "blog": r"блог|blog|статьи|новости|журнал|автор|заметк|публикац",
    "agency": r"агентств|agency|студия|маркетинг|брендинг|команда",
}

TEMPLATE_TITLES: dict[str, str] = {
    "portfolio": "Портфолио",
    "cafe": "Кафе / ресторан",
    "shop": "Магазин",
    "blog": "Блог",
    "agency": "Агентство / студия",
    "landing": "Лендинг",
}

TEMPLATE_HINTS: dict[str, str] = {
    "portfolio": "Сетка работ, навыки, обо мне и контакты",
    "cafe": "Меню с ценами, галерея, отзывы и часы работы",
    "shop": "Витрина товаров с ценами, доставка и подписка",
    "blog": "Список статей, рубрики и подписка на рассылку",
    "agency": "Услуги, кейсы, команда и процесс работы",
    "landing": "Герой-блок, преимущества, цифры и форма связи",
}


def detect_template(prompt: str) -> str:
    text = (prompt or "").lower()
    for template_id, pattern in TEMPLATE_WORDS.items():
        if re.search(pattern, text):
            return template_id
    return "landing"


def detect_palette(prompt: str) -> str:
    text = (prompt or "").lower()
    for pattern, palette_id in PALETTE_WORDS:
        if re.search(pattern, text):
            return palette_id
    return "indigo"


# ---------------------------------------------------------------- каркас и CSS

def _head(name: str, pal: dict[str, str]) -> str:
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name}</title>
<style>
:root {{
  --bg: {pal['bg']}; --surface: {pal['surface']}; --ink: {pal['ink']};
  --muted: {pal['muted']}; --accent: {pal['accent']}; --accent-2: {pal['accent2']};
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--ink); line-height: 1.6;
  font-family: 'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif; }}
.container {{ max-width: 1120px; margin: 0 auto; padding: 0 24px; }}
.nav {{ position: sticky; top: 0; z-index: 50; border-bottom: 1px solid rgba(128,128,128,.14);
  background: color-mix(in srgb, var(--bg) 88%, transparent); backdrop-filter: blur(8px); }}
.nav .container {{ display: flex; align-items: center; gap: 20px; height: 64px; }}
.logo {{ font-weight: 800; font-size: 18px; text-decoration: none; color: var(--ink); }}
.logo b {{ color: var(--accent); }}
.links {{ display: flex; gap: 20px; margin-left: auto; flex-wrap: wrap; }}
.links a {{ color: var(--muted); text-decoration: none; font-size: 14px; font-weight: 500; }}
.links a:hover {{ color: var(--accent); }}
.btn {{ display: inline-block; padding: 12px 24px; border-radius: 12px; border: none;
  background: var(--accent); color: #fff; font-weight: 600; font-size: 15px;
  text-decoration: none; cursor: pointer; transition: transform .15s, box-shadow .15s; }}
.btn:hover {{ transform: translateY(-1px);
  box-shadow: 0 10px 26px color-mix(in srgb, var(--accent) 38%, transparent); }}
.btn.ghost {{ background: transparent; color: var(--ink); border: 1px solid rgba(128,128,128,.35); }}
section {{ padding: 72px 0; }}
.kicker {{ text-transform: uppercase; letter-spacing: .12em; font-size: 12px;
  font-weight: 700; color: var(--accent); margin: 0 0 10px; }}
h1 {{ font-size: clamp(32px, 5vw, 52px); line-height: 1.12; margin: 0 0 18px; }}
h2 {{ font-size: clamp(24px, 3.4vw, 34px); line-height: 1.2; margin: 0 0 14px; }}
h3 {{ margin: 0 0 8px; font-size: 18px; }}
.lead {{ color: var(--muted); font-size: 18px; max-width: 640px; }}
.grid {{ display: grid; gap: 22px; }}
.cards-3 {{ grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }}
.cards-4 {{ grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }}
.card {{ background: var(--surface); border: 1px solid rgba(128,128,128,.14);
  border-radius: 18px; padding: 26px; }}
.card p {{ margin: 0; color: var(--muted); font-size: 15px; }}
.badge {{ display: inline-block; padding: 6px 14px; border-radius: 999px;
  background: color-mix(in srgb, var(--accent) 14%, transparent);
  color: var(--accent); font-weight: 600; font-size: 13px; }}
.hero {{ padding: 88px 0; background: {pal['hero']}; color: #fff; }}
.hero .lead {{ color: rgba(255,255,255,.85); }}
.hero .row {{ display: flex; gap: 14px; flex-wrap: wrap; margin-top: 26px; }}
.hero .btn.ghost {{ color: #fff; border-color: rgba(255,255,255,.45); }}
.stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 18px; }}
.stat-n {{ font-size: 40px; font-weight: 800; color: var(--accent); }}
.tile {{ border-radius: 18px; min-height: 170px; padding: 20px; color: #fff; font-weight: 700;
  display: flex; align-items: flex-end; }}
.price {{ font-size: 20px; font-weight: 800; color: var(--accent); }}
.center {{ text-align: center; }} .center .lead {{ margin: 0 auto; }}
form.form {{ display: grid; gap: 12px; max-width: 520px; }}
input, textarea {{ padding: 12px 14px; border-radius: 12px; font: inherit; color: var(--ink);
  border: 1px solid rgba(128,128,128,.3); background: var(--surface); }}
input:focus, textarea:focus {{ outline: 2px solid var(--accent); border-color: transparent; }}
.footer {{ padding: 34px 0; border-top: 1px solid rgba(128,128,128,.14);
  color: var(--muted); font-size: 14px; }}
.footer .container {{ display: flex; gap: 14px; flex-wrap: wrap; align-items: center; }}
.footer a {{ color: var(--muted); }}
ul.list {{ list-style: none; padding: 0; margin: 0; }}
ul.list li {{ display: flex; justify-content: space-between; gap: 16px; padding: 14px 0;
  border-bottom: 1px dashed rgba(128,128,128,.25); }}
.bar {{ height: 10px; border-radius: 6px; background: rgba(128,128,128,.18); }}
.bar i {{ display: block; height: 100%; border-radius: 6px; background: var(--accent); }}
@media (max-width: 720px) {{ .links {{ display: none; }} section {{ padding: 52px 0; }} }}
</style>
</head>
<body>
"""


def _nav(name: str, links: list[tuple[str, str]]) -> str:
    items = "".join(f'<a href="{href}">{label}</a>' for label, href in links)
    return f"""<header class="nav"><div class="container">
<a class="logo" href="#">{name}<b>.</b></a>
<nav class="links">{items}</nav>
<a class="btn" href="#contact" style="padding:9px 18px">Связаться</a>
</div></header>
"""


def _footer(name: str) -> str:
    return f"""<footer class="footer"><div class="container">
<span>© 2026 {name}. Все права защищены.</span>
<span style="margin-left:auto"><a href="#">Telegram</a> · <a href="#">VK</a> · <a href="#">WhatsApp</a></span>
</div></footer>
"""


def _close() -> str:
    return "</body>\n</html>\n"


def _hero(kicker: str, title: str, text: str, primary: str, secondary: str) -> str:
    return f"""<section class="hero"><div class="container">
<span class="badge" style="background:rgba(255,255,255,.16);color:#fff">{kicker}</span>
<h1>{title}</h1>
<p class="lead">{text}</p>
<div class="row">
<a class="btn" href="#contact">{primary}</a>
<a class="btn ghost" href="#features">{secondary}</a>
</div>
</div></section>
"""


def _head_block(kicker: str, title: str, sub: str = "") -> str:
    sub_html = f'\n<p class="lead" style="margin-top:0">{sub}</p>' if sub else ""
    return f'<span class="kicker">{kicker}</span>\n<h2>{title}</h2>{sub_html}'


def _tile(label: str, hue: int, style_extra: str = "") -> str:
    return (f'<div class="tile" style="background:linear-gradient(135deg,'
            f'hsl({hue},70%,45%),hsl({hue + 40},75%,60%)){style_extra}">{label}</div>')


# ---------------------------------------------------------------- секции шаблонов
# Каждая функция получает имя проекта и палитру и возвращает список секций
# <section>…</section> — без шапки, футера и закрывающих тегов.

def _landing_sections(name: str, pal: dict[str, str]) -> list[str]:
    return [
        _hero("Запуск за неделю", f"{name} — сайт, который работает на вас",
              "Современный лендинг: быстрая загрузка, адаптивная вёрстка и понятная "
              "структура, которая ведёт посетителя к заявке.",
              "Оставить заявку", "Смотреть возможности"),
        f"""<section id="features"><div class="container">
{_head_block("Возможности", "Почему с ним удобно", "Три опоры, на которых держится сайт, — скорость, адаптивность и продвижение.")}
<div class="grid cards-3" style="margin-top:26px">
<div class="card"><h3>Быстрый запуск</h3><p>Проект собирается из готовых блоков: первая версия уже завтра, правки — сразу вживую.</p></div>
<div class="card"><h3>Адаптивность</h3><p>Одинаково аккуратно выглядит на телефоне, планшете и большом экране.</p></div>
<div class="card"><h3>SEO-основа</h3><p>Семантическая разметка и быстрые страницы — поисковикам нравится быстрое.</p></div>
</div></div></section>
""",
        """<section id="stats"><div class="container">
""" + _head_block("Цифры", "Немного фактов") + """<div class="stats" style="margin-top:26px">
<div class="card center"><div class="stat-n">120+</div><p>проектов запущено</p></div>
<div class="card center"><div class="stat-n">98%</div><p>клиентов возвращаются</p></div>
<div class="card center"><div class="stat-n">24/7</div><p>поддержка и мониторинг</p></div>
</div></div></section>
""",
        f"""<section style="background:{pal['hero']};color:#fff"><div class="container center">
<h2>Готовы начать?</h2>
<p class="lead" style="color:rgba(255,255,255,.85)">Расскажите о задаче — предложим решение и смету в течение дня.</p>
<p style="margin-top:22px"><a class="btn" href="#contact" style="background:#fff;color:#111">Обсудить проект</a></p>
</div></section>
""",
        """<section id="contact"><div class="container">
""" + _head_block("Контакты", "Оставьте заявку",
                  "Заполните форму — вернёмся с ответом в рабочее время.") + """
<form class="form" style="margin-top:24px" onsubmit="return false">
<input type="text" name="name" placeholder="Ваше имя" required>
<input type="email" name="email" placeholder="Электронная почта" required>
<textarea name="msg" rows="4" placeholder="Пара слов о задаче"></textarea>
<button class="btn" type="submit">Отправить</button>
</form></div></section>
""",
    ]


def _portfolio_sections(name: str, pal: dict[str, str]) -> list[str]:
    works = "".join(_tile(f"Проект {i}", h)
                    for i, h in ((1, 255), (2, 200), (3, 160), (4, 30), (5, 330), (6, 270)))
    skills = "".join(
        f'<div><div class="row" style="justify-content:space-between;margin:0 0 6px">'
        f'<b>{label}</b><span style="color:var(--muted)">{level}%</span></div>'
        f'<div class="bar"><i style="width:{level}%"></i></div></div>'
        for label, level in (("Дизайн интерфейсов", 92), ("Вёрстка", 88), ("Брендинг", 76)))
    return [
        _hero("Портфолио", f"Работы студии «{name}»",
              "Избранные проекты: интерфейсы, айдентика и сайты, которыми пользуются люди.",
              "Смотреть работы", "Обо мне"),
        f"""<section id="works"><div class="container">
{_head_block("Кейсы", "Избранные работы")}
<div class="grid cards-3" style="margin-top:26px">{works}</div>
</div></section>
""",
        f"""<section><div class="container">
{_head_block("Навыки", "Что умею лучше всего")}
<div class="grid" style="grid-template-columns:1fr;max-width:640px;margin-top:26px;gap:18px">{skills}</div>
</div></section>
""",
        """<section id="about"><div class="container grid cards-3">
<div class="card"><h3>Обо мне</h3><p>10 лет в цифровом дизайне. Люблю чистые сетки, живые детали и продукты, которые решают задачу бизнеса.</p></div>
<div class="card"><h3>Как работаем</h3><p>Бриф → концепция → дизайн → вёрстка → поддержка. На каждом шаге показываю результат и слушаю вас.</p></div>
<div class="card"><h3>Сроки</h3><p>Лендинг — 5–7 дней, многостраничный сайт — 2–3 недели, айдентика — от 10 дней.</p></div>
</div></section>
""",
        """<section id="contact"><div class="container">
""" + _head_block("Контакты", "Обсудим ваш проект") + """
<form class="form" style="margin-top:24px" onsubmit="return false">
<input type="text" name="name" placeholder="Как вас зовут">
<input type="email" name="email" placeholder="Почта для ответа">
<textarea name="msg" rows="4" placeholder="Расскажите о проекте"></textarea>
<button class="btn" type="submit">Написать</button>
</form></div></section>
""",
    ]


def _cafe_sections(name: str, pal: dict[str, str]) -> list[str]:
    menu = "".join(
        f'<li><span>{dish}</span><b>{price}</b></li>'
        for dish, price in (
            ("Завтрак «Утро» — яйца бенедикт", "490 ₽"),
            ("Том-ям с креветками", "590 ₽"),
            ("Паста карбонара", "520 ₽"),
            ("Стейк из сёмги", "890 ₽"),
            ("Чизкейк домашний", "350 ₽"),
            ("Капучино на альт-молоке", "260 ₽")))
    return [
        _hero("Открыто ежедневно", f"«{name}» — вкусно, как дома",
              "Свежие продукты, авторское меню и тёплая атмосфера в центре города. "
              "Завтраки — с 8:00, кухня — до 23:00.",
              "Забронировать столик", "Смотреть меню"),
        f"""<section id="menu"><div class="container">
{_head_block("Меню", "Популярное из кухни", "Полное меню — в заведении и на доставке; здесь — бестселлеры сезона.")}
<ul class="list" style="margin-top:20px;max-width:640px">{menu}</ul>
</div></section>
""",
        f"""<section><div class="container">
{_head_block("Атмосфера", "Немного из жизни")}
<div class="grid cards-4" style="margin-top:26px">
{_tile("Зал", 35)}{_tile("Бар", 25)}{_tile("Терраса", 20)}{_tile("Десерты", 15)}
</div></div></section>
""",
        """<section style="background:var(--surface)"><div class="container grid cards-3">
<div class="card"><h3>«Лучшие завтраки в районе»</h3><p>— Анна, постоянный гость</p></div>
<div class="card"><h3>«Уютно и очень вкусно»</h3><p>— Михаил, отзыв в 2ГИС</p></div>
<div class="card"><h3>«Идеально для встреч»</h3><p>— Ольга, фрилансер</p></div>
</div></section>
""",
        """<section id="contact"><div class="container grid cards-3">
<div class="card"><h3>Адрес</h3><p>ул. Примерная, 12, первый этаж</p></div>
<div class="card"><h3>Часы</h3><p>Пн–Чт 8:00–23:00 · Пт–Вс 8:00–01:00</p></div>
<div class="card"><h3>Бронь</h3><p>+7 (900) 000-00-00 · по телефону или в мессенджере</p></div>
</div></section>
""",
    ]


def _shop_sections(name: str, pal: dict[str, str]) -> list[str]:
    products = "".join(
        f"""<div class="card center">{_tile(title, h, ';align-items:center;justify-content:center;min-height:130px;font-size:22px')}
<h3 style="margin:14px 0 4px">{title}</h3><div class="price">{price}</div>
<p style="margin:10px 0 0"><a class="btn" href="#contact" style="padding:9px 18px">В корзину</a></p></div>"""
        for title, price, h in (
            ("Футболка oversize", "1 990 ₽", 220),
            ("Худи премиум", "3 990 ₽", 260),
            ("Кепка с логотипом", "1 290 ₽", 200),
            ("Рюкзак городской", "4 590 ₽", 180),
            ("Кружка керамика", "790 ₽", 320),
            ("Стикерпак", "390 ₽", 280)))
    return [
        _hero("Новое поступление", f"Магазин «{name}»",
              "Фирменные вещи и аксессуары с доставкой по городу за день и по стране за 3 дня.",
              "Перейти к каталогу", "Условия доставки"),
        f"""<section id="catalog"><div class="container">
{_head_block("Каталог", "Хиты продаж")}
<div class="grid cards-3" style="margin-top:26px">{products}</div>
</div></section>
""",
        """<section><div class="container grid cards-3">
<div class="card"><h3>Быстрая доставка</h3><p>По городу — за день, по России — 3 дня, от 3 000 ₽ бесплатно.</p></div>
<div class="card"><h3>Лёгкий возврат</h3><p>14 дней на решение, возврат без лишних вопросов.</p></div>
<div class="card"><h3>Бонусы</h3><p>Кэшбэк баллами на каждый заказ и подарок к первому.</p></div>
</div></section>
""",
        f"""<section id="contact" style="background:{pal['hero']};color:#fff"><div class="container center">
<h2>Скидка 10% на первый заказ</h2>
<p class="lead" style="color:rgba(255,255,255,.85)">Подпишитесь — промокод придёт сразу.</p>
<form class="form" style="margin:24px auto 0" onsubmit="return false">
<input type="email" name="email" placeholder="Ваша почта" required>
<button class="btn" type="submit" style="background:#fff;color:#111">Подписаться</button>
</form></div></section>
""",
    ]


def _blog_sections(name: str, pal: dict[str, str]) -> list[str]:
    posts = "".join(
        f"""<article class="card"><span class="badge">{tag}</span>
<h3 style="margin-top:12px">{title}</h3><p>{text}</p>
<p style="margin-top:14px"><a href="#" style="color:var(--accent);font-weight:600;text-decoration:none">Читать →</a></p></article>"""
        for tag, title, text in (
            ("Гайд", "Как начать проект за выходные",
             "Пошаговый план: от идеи до первой версии, которую не стыдно показать людям."),
            ("Опыт", "Три ошибки в первой версии сайта",
             "Перфекционизм, лишние страницы и главная, которая обо всём и ни о чём."),
            ("Инструменты", "Наш минимальный набор для веба",
             "Что реально экономит время: редактор, прототипы, метрики и немного дисциплины.")))
    return [
        _hero("Новые записи каждую неделю", f"Блог «{name}»",
              "Заметки о дизайне, разработке и запуске продуктов — коротко и по делу.",
              "Читать свежее", "Все рубрики"),
        f"""<section id="posts"><div class="container">
{_head_block("Статьи", "Свежее в блоге")}
<div class="grid cards-3" style="margin-top:26px">{posts}</div>
</div></section>
""",
        """<section><div class="container">
""" + _head_block("Рубрики", "О чём пишем") + """
<p style="margin-top:18px;display:flex;gap:10px;flex-wrap:wrap">
<span class="badge">Дизайн</span> <span class="badge">Вёрстка</span>
<span class="badge">Продукт</span> <span class="badge">Опыт</span>
<span class="badge">Инструменты</span> <span class="badge">Мотивация</span></p>
</div></section>
""",
        """<section id="contact" style="background:var(--surface)"><div class="container center">
<h2>Подписка на рассылку</h2>
<p class="lead">Одно письмо в неделю: самое полезное из блога и ничего лишнего.</p>
<form class="form" style="margin:24px auto 0" onsubmit="return false">
<input type="email" name="email" placeholder="Ваша почта" required>
<button class="btn" type="submit">Подписаться</button>
</form></div></section>
""",
    ]


def _agency_sections(name: str, pal: dict[str, str]) -> list[str]:
    services = "".join(
        f'<div class="card"><h3>{emoji} {title}</h3><p>{text}</p></div>'
        for emoji, title, text in (
            ("🎯", "Стратегия", "Исследуем рынок и аудиторию, находим позицию и собираем план запуска."),
            ("🎨", "Дизайн", "Айдентика и интерфейсы, которые одинаково работают в Figma и в продакшене."),
            ("💻", "Разработка", "Сайты и веб-приложения: быстро, аккуратно, с метриками с первого дня."),
            ("📈", "Продвижение", "Аналитика, SEO и реклама — трафик, который превращается в заявки.")))
    team = "".join(
        f'<div class="card center">{_tile(who, h, ";align-items:center;justify-content:center;min-height:120px;font-size:20px")}'
        f'<p style="margin-top:12px;color:var(--muted)">{role}</p></div>'
        for who, role, h in (("Анна", "Арт-директор", 260), ("Игорь", "Техлид", 210),
                             ("Мария", "Продакт-менеджер", 320), ("Пётр", "Разработчик", 160)))
    steps = "".join(
        f'<div class="card"><h3>{i}. {title}</h3><p>{text}</p></div>'
        for i, title, text in (
            (1, "Бриф", "Созваниваемся, фиксируем цель, сроки и бюджет."),
            (2, "Концепция", "Показываем структуру и ключевые экраны до старта вёрстки."),
            (3, "Сборка", "Дизайн, код, контент — итерациями по неделе."),
            (4, "Запуск", "Публикуем, подключаем метрики, передаём инструменты.")))
    return [
        _hero("Полный цикл", f"{name} — digital-агентство",
              "Стратегия, дизайн и разработка под одной крышей. Берём продукт от идеи "
              "до стабильного роста показателей.",
              "Обсудить задачу", "Наши кейсы"),
        f"""<section id="services"><div class="container">
{_head_block("Услуги", "Чем можем помочь")}
<div class="grid cards-4" style="margin-top:26px">{services}</div>
</div></section>
""",
        f"""<section id="cases" style="background:var(--surface)"><div class="container">
{_head_block("Кейсы", "Недавние проекты")}
<div class="grid cards-3" style="margin-top:26px">
{_tile("Финтех-приложение", 220)}{_tile("Сеть кофеен", 150)}{_tile("Онлайн-школа", 25)}
</div></div></section>
""",
        f"""<section><div class="container">
{_head_block("Команда", "Кто будет делать")}
<div class="grid cards-4" style="margin-top:26px">{team}</div>
</div></section>
""",
        f"""<section style="background:var(--surface)"><div class="container">
{_head_block("Процесс", "Как мы работаем")}
<div class="grid cards-4" style="margin-top:26px">{steps}</div>
</div></section>
""",
        """<section id="contact"><div class="container">
""" + _head_block("Контакты", "Расскажите о задаче",
                  "Ответим в течение рабочего дня с планом и вилкой бюджета.") + """
<form class="form" style="margin-top:24px" onsubmit="return false">
<input type="text" name="name" placeholder="Имя и компания">
<input type="email" name="email" placeholder="Рабочая почта">
<textarea name="msg" rows="4" placeholder="Что за продукт и какая цель"></textarea>
<button class="btn" type="submit">Отправить бриф</button>
</form></div></section>
""",
    ]


SECTIONS_BY_TEMPLATE = {
    "landing": _landing_sections,
    "portfolio": _portfolio_sections,
    "cafe": _cafe_sections,
    "shop": _shop_sections,
    "blog": _blog_sections,
    "agency": _agency_sections,
}

NAV_LINKS: dict[str, list[tuple[str, str]]] = {
    "landing": [("Возможности", "#features"), ("Цифры", "#stats"), ("Контакты", "#contact")],
    "portfolio": [("Работы", "#works"), ("Навыки", "#about"), ("Контакты", "#contact")],
    "cafe": [("Меню", "#menu"), ("Атмосфера", "#contact"), ("Контакты", "#contact")],
    "shop": [("Каталог", "#catalog"), ("Доставка", "#contact"), ("Скидка", "#contact")],
    "blog": [("Статьи", "#posts"), ("Рубрики", "#contact"), ("Подписка", "#contact")],
    "agency": [("Услуги", "#services"), ("Кейсы", "#cases"), ("Команда", "#contact")],
}


def templates_catalog() -> list[dict[str, str]]:
    return [{"id": tid, "title": TEMPLATE_TITLES[tid], "hint": TEMPLATE_HINTS[tid]}
            for tid in ("landing", "portfolio", "cafe", "shop", "blog", "agency")]


def _clean_name(name: str) -> str:
    cleaned = " ".join((name or "").split()).strip()
    if len(cleaned) > 80:
        cleaned = cleaned[:77].rstrip() + "…"
    return cleaned or "Мой сайт"


def build_steps(template_id: str, name: str, palette_id: str) -> list[str]:
    """Кумулятивные шаги сборки: каждый — валидный HTML, последний — готовый сайт."""
    pal = PALETTES.get(palette_id, PALETTES["indigo"])
    sections_fn = SECTIONS_BY_TEMPLATE.get(template_id, _landing_sections)
    site_name = _clean_name(name)
    head = _head(site_name, pal)
    nav = _nav(site_name, NAV_LINKS.get(template_id, NAV_LINKS["landing"]))
    body_parts = sections_fn(site_name, pal)
    parts = [head + nav + body_parts[0]] + body_parts[1:] + [_footer(site_name)]
    return ["".join(parts[:k + 1]) + _close() for k in range(len(parts))]


def generate(prompt: str, name: str = "", template: str = "auto",
             palette: str = "auto") -> dict:
    """Определить шаблон и палитру по запросу, собрать шаги. Детерминирован."""
    chosen_template = template if template in SECTIONS_BY_TEMPLATE else detect_template(prompt)
    chosen_palette = palette if palette in PALETTES else detect_palette(prompt)
    project_name = name or (prompt.strip()[:60] if prompt.strip() else "Мой сайт")
    steps = build_steps(chosen_template, project_name, chosen_palette)
    return {
        "template": chosen_template,
        "palette": chosen_palette,
        "name": _clean_name(project_name),
        "steps": steps,
    }
