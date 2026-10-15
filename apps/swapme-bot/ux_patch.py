# -*- coding: utf-8 -*-
"""
ux_patch.py — UX-улучшения для SwapMe bot.py (v6.4, python-telegram-bot 20.7).

НЕ запускается сам по себе как бот. Это модуль-донор: функции отсюда переносятся
в bot.py. У каждой функции в docstring помечено «ЗАМЕНЯЕТ X» или «ДОБАВИТЬ после Y».

Зависимости из bot.py, которые должны быть в области видимости после переноса:
  CURRENCIES, DEAL_TYPES, is_admin, is_authorized, get_user_name,
  get_rate, calc_spread, get_available, create_order, cancel_order,
  format_receipt, audit, db_exec, log, BK, DEAL_DONE_PHRASES, DEAL_CANCEL_PHRASES,
  состояния ConversationHandler.END, классы InlineKeyboardButton/Markup, datetime, random.

В этом файле они «заглушены» через try/except, чтобы файл компилировался и
прогонял unit-тесты парсера автономно (python -X utf8 ux_patch.py).
"""
from __future__ import annotations
import re
from datetime import datetime, timedelta

# ── Заглушки на случай автономного запуска (в bot.py НЕ переносить) ──────────────
try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
    from telegram.ext import ContextTypes, ConversationHandler
except Exception:  # автономный режим тестов
    InlineKeyboardButton = InlineKeyboardMarkup = Update = None
    class ContextTypes:  # noqa
        DEFAULT_TYPE = object
    class ConversationHandler:  # noqa
        END = -1

CURRENCIES = ["USDT", "EUR", "USD", "CZK"]
UNDO_WINDOW_MIN = 10  # окно отмены последней сделки, минут


# ════════════════════════════════════════════════════════════════════════════════
# 1. БЫСТРЫЙ ВВОД СДЕЛКИ — ПАРСЕР
#    ДОБАВИТЬ в bot.py рядом с calc_spread (~стр. 1115).
# ════════════════════════════════════════════════════════════════════════════════

# слова, помечающие «что МЫ приняли» (= from, клиент отдаёт нам)
_FROM_WORDS = {"принял", "принели", "принято", "получил", "получили", "получ",
               "взял", "зашло", "пришло", "from", "in", "+"}
# слова, помечающие «что МЫ выдали» (= to, клиент получает)
_TO_WORDS = {"выдал", "выдали", "отдал", "отдали", "выдача", "дал", "дали",
             "отправил", "ушло", "to", "out", "-"}

_CCY_ALIASES = {
    "USDT": "USDT", "USD": "USD", "EUR": "EUR", "CZK": "CZK",
    "ЮСДТ": "USDT", "ТЕЗЕР": "USDT", "ТЕЗ": "USDT", "USDTTRC20": "USDT",
    "ДОЛЛАР": "USD", "ДОЛ": "USD", "БАКС": "USD", "БАКСОВ": "USD",
    "ЕВРО": "EUR", "ЕВР": "EUR",
    "КРОН": "CZK", "КРОНА": "CZK", "КРОНЫ": "CZK", "КРОН.": "CZK", "ЧЕХ": "CZK",
    "€": "EUR", "$": "USD",
}


def _norm_ccy(token: str):
    """Нормализует валютный токен → код из CURRENCIES или None."""
    if not token:
        return None
    t = token.strip().upper().replace("$", "USD").replace("€", "EUR")
    t = re.sub(r"[.,;:!?()]+$", "", t)
    if t in CURRENCIES:
        return t
    return _CCY_ALIASES.get(t)


def _parse_amount(num_str: str, ccy: str = None, had_k: bool = False):
    """
    '20 800' -> 20800.0 ; '1 250,5' -> 1250.5 ; '10' + had_k -> 10000.0
    Пробелы = разделитель тысяч. Запятая = десятичная. Точка = десятичная.
    """
    s = num_str.strip().replace(chr(0xA0),"").replace(" ", "")  # пробелы-тысячи уже валидированы
    # если есть и точка и запятая — запятая десятичная, точки тысячные
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        val = float(s)
    except ValueError:
        return None
    if had_k:
        val *= 1000.0
    return val


# токен суммы: число с пробелами-тысячами/запятой/точкой + опциональный суффикс к/k
# число = 1-3 цифры + группы « 999» (ровно 3 цифры) ИЛИ слитные цифры, затем дробь.
# Пробел трактуется как разделитель тысяч только перед группой ровно из 3 цифр.
_AMT_TOKEN_RE = re.compile(
    r"(?<![\w.,])(\d{1,3}(?:[ ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)(?![.,]?\d)\s*([кkКK]\b)?")
# токен валюты: символ €/$ или слово 2-8 букв (лат/кир)
_CCY_TOKEN_RE = re.compile(r"[€$]|[A-Za-zА-Яа-я]{2,8}")


def _extract_pairs(text: str):
    """
    Находит суммы и валюты независимо, затем привязывает каждую сумму к
    ближайшему валидному валютному токену (сначала стоящему сразу ПОСЛЕ суммы,
    иначе — сразу ПЕРЕД, как «€500»).
    Возвращает [(amount, ccy, span_start), ...] в порядке появления суммы.
    """
    ccys = []  # (start, end, ccy)
    for m in _CCY_TOKEN_RE.finditer(text):
        c = _norm_ccy(m.group(0))
        if c:
            ccys.append((m.start(), m.end(), c))
    if not ccys:
        return []
    pairs = []
    used = set()
    for am in _AMT_TOKEN_RE.finditer(text):
        had_k = bool(am.group(2))
        amt = _parse_amount(am.group(1), None, had_k)
        if amt is None or amt <= 0:
            continue
        a_start, a_end = am.start(1), am.end()
        best = None
        # 1) валюта сразу ПОСЛЕ суммы (gap <=2)
        for i, (cs, ce, c) in enumerate(ccys):
            if i in used:
                continue
            if 0 <= cs - a_end <= 2:
                best = (i, c)
                break
        # 2) иначе валюта сразу ПЕРЕД суммой (gap <=2), напр. «€500»
        if best is None:
            for i, (cs, ce, c) in enumerate(ccys):
                if i in used:
                    continue
                if 0 <= a_start - ce <= 2:
                    best = (i, c)
                    break
        if best is None:
            continue
        used.add(best[0])
        pairs.append((amt, best[1], a_start))
    pairs.sort(key=lambda p: p[2])
    return pairs


def _infer_deal_type(from_ccy: str, to_ccy: str) -> str:
    """
    Определяет deal_type по паре (конвенция format_receipt в bot.py):
      BUY  = клиент даёт фиат → получает крипто (from=фиат, to=USDT)
      SELL = клиент даёт крипто → получает фиат (from=USDT, to=фиат)
    """
    crypto = {"USDT"}
    f_crypto = from_ccy in crypto
    t_crypto = to_ccy in crypto
    if f_crypto and not t_crypto:
        return "SELL"
    if t_crypto and not f_crypto:
        return "BUY"
    if f_crypto and t_crypto:
        return "SWAP_CC"
    return "SWAP_FF"


def parse_quick_deal(text: str):
    """
    Парсит сообщение менеджера в черновик сделки.
    Возвращает dict {from_ccy, from_amt, to_ccy, to_amt, deal_type} или None.

    Правила направления:
      - если рядом с парой есть слово из _FROM_WORDS → это from (приняли);
        из _TO_WORDS → это to (выдали);
      - иначе по порядку: первая пара = from (приняли), вторая = to (выдали).
    """
    if not text:
        return None
    raw = text.strip()
    low = raw.lower()
    pairs = _extract_pairs(raw)
    if len(pairs) < 2:
        return None
    # берём первые две валидные пары
    pairs = pairs[:2]

    # пытаемся определить роль по ключевым словам перед каждой парой
    def role_before(span_start):
        prefix = low[:span_start]
        # последнее значащее слово перед суммой
        words = re.findall(r"[a-zа-я]+", prefix)
        tail = words[-1] if words else ""
        for w in _FROM_WORDS:
            if tail == w or tail.startswith(w):
                return "from"
        for w in _TO_WORDS:
            if tail == w or tail.startswith(w):
                return "to"
        return None

    (a0, c0, s0), (a1, c1, s1) = pairs[0], pairs[1]
    r0, r1 = role_before(s0), role_before(s1)

    if r0 == "from" or r1 == "to":
        from_amt, from_ccy = a0, c0
        to_amt, to_ccy = a1, c1
    elif r0 == "to" or r1 == "from":
        from_amt, from_ccy = a1, c1
        to_amt, to_ccy = a0, c0
    else:
        # порядок по умолчанию: первое = приняли (from)
        from_amt, from_ccy = a0, c0
        to_amt, to_ccy = a1, c1

    if from_ccy == to_ccy:
        return None
    if from_ccy not in CURRENCIES or to_ccy not in CURRENCIES:
        return None
    return {
        "from_ccy": from_ccy, "from_amt": from_amt,
        "to_ccy": to_ccy, "to_amt": to_amt,
        "deal_type": _infer_deal_type(from_ccy, to_ccy),
    }


# ════════════════════════════════════════════════════════════════════════════════
# 2. БЫСТРЫЙ ВВОД — ХЕНДЛЕРЫ (preview + confirm)
#    ДОБАВИТЬ в секцию TELEGRAM HANDLERS. Регистрация — см. блок REGISTRATION внизу.
# ════════════════════════════════════════════════════════════════════════════════

def _format_quick_preview(d: dict) -> str:
    """Один экран-подтверждение для быстрой сделки."""
    fc, tc = d["from_ccy"], d["to_ccy"]
    fa, ta = d["from_amt"], d["to_amt"]
    mkt = get_rate(fc, tc)  # noqa: F821 (из bot.py)
    sp = calc_spread(fc, fa, tc, ta, mkt)  # noqa: F821
    rate = sp["deal_rate"]
    margin = sp["margin_our"]
    src = d.get("client_type", "organic")
    src_lbl = "🟢 Наш" if src == "organic" else "🔴 Реклама"
    date_lbl = d.get("custom_date") or "сейчас"
    sep = "━━━━━━━━━━━━━━━━"
    lines = [
        "⚡ *Быстрая сделка* — проверь:",
        sep,
        f"Принял:  *{fa:,.2f} {fc}*",
        f"Выдал:   *{ta:,.2f} {tc}*",
        f"Курс: {rate:.4f}" + (f"  (рынок {mkt:.4f})" if mkt else "  (рынок н/д)"),
    ]
    if margin is not None:
        ic = "📈" if margin >= 0 else "⚠️ УБЫТОК"
        lines.append(f"{ic} Маржа: {margin:+,.2f} {tc} ({sp['spread_pct']:+.2f}%)")
    # предупреждение о ликвидности
    try:
        av = get_available().get(tc, 0)  # noqa: F821
        if av < ta:
            lines.append(f"⚠️ Доступно только {av:,.2f} {tc}!")
    except Exception:
        pass
    lines += [sep, f"Источник: {src_lbl}   📅 {date_lbl}"]
    return "\n".join(lines)


def _quick_kb(src: str = "organic"):
    toggle = "🔴 Сделать рекламной" if src == "organic" else "🟢 Сделать нашей"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Записать", callback_data="qd_save")],
        [InlineKeyboardButton(toggle, callback_data="qd_src"),
         InlineKeyboardButton("📅 Дата", callback_data="qd_date")],
        [InlineKeyboardButton("🧙 По шагам", callback_data="new_deal"),
         InlineKeyboardButton("❌ Отмена", callback_data="qd_cancel")],
    ])


async def cb_quick_deal_preview(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE"):
    """
    Глобальный MessageHandler: ловит сообщение вида «500 usdt 10400 czk».
    ДОБАВИТЬ; зарегистрировать ПЕРЕД reply_kb_router, ПОСЛЕ ConversationHandler.
    Срабатывает только вне диалога (ConversationHandler перехватывает свои состояния).
    """
    if not update.message or not update.message.text:
        return
    if not is_authorized(update.effective_user.id):  # noqa: F821
        return
    parsed = parse_quick_deal(update.message.text)
    if not parsed:
        return  # не похоже на сделку — молча игнорируем (пусть ловят другие handler'ы)
    parsed["client_type"] = "organic"
    parsed["client_name"] = None
    parsed["custom_date"] = None
    ctx.user_data["quick"] = parsed
    await update.message.reply_text(
        _format_quick_preview(parsed),
        reply_markup=_quick_kb(parsed["client_type"]),
        parse_mode="Markdown",
    )


async def cb_quick_toggle_src(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE"):
    """qd_src — переключить источник клиента на экране подтверждения."""
    q = update.callback_query
    await q.answer()
    d = ctx.user_data.get("quick")
    if not d:
        await q.edit_message_text("Черновик устарел. Введи сделку заново.")
        return
    d["client_type"] = "ads" if d.get("client_type", "organic") == "organic" else "organic"
    await q.edit_message_text(_format_quick_preview(d),
                              reply_markup=_quick_kb(d["client_type"]),
                              parse_mode="Markdown")


async def cb_quick_date(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE"):
    """qd_date — мини-меню выбора даты без ввода текста."""
    q = update.callback_query
    await q.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🕒 Сейчас", callback_data="qd_d_now"),
         InlineKeyboardButton("Вчера", callback_data="qd_d_yest")],
        [InlineKeyboardButton("↩ Назад", callback_data="qd_d_back")],
    ])
    await q.edit_message_text("📅 Когда была сделка?", reply_markup=kb)


async def cb_quick_date_set(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE"):
    """qd_d_now / qd_d_yest / qd_d_back."""
    q = update.callback_query
    await q.answer()
    d = ctx.user_data.get("quick")
    if not d:
        await q.edit_message_text("Черновик устарел. Введи сделку заново.")
        return
    code = q.data.replace("qd_d_", "")
    if code == "now":
        d["custom_date"] = None
    elif code == "yest":
        d["custom_date"] = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    # back / now / yest — все возвращают на экран подтверждения
    await q.edit_message_text(_format_quick_preview(d),
                              reply_markup=_quick_kb(d["client_type"]),
                              parse_mode="Markdown")


async def cb_quick_confirm(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE"):
    """qd_save — записать быструю сделку в БД (использует существующий create_order)."""
    q = update.callback_query
    await q.answer()
    d = ctx.user_data.get("quick")
    if not d:
        await q.edit_message_text("Черновик устарел. Введи сделку заново.")
        return
    tid = update.effective_user.id
    mgr = get_user_name(tid)  # noqa: F821
    mkt = get_rate(d["from_ccy"], d["to_ccy"])  # noqa: F821
    try:
        oid = create_order(  # noqa: F821
            tg_id=tid, manager=mgr, deal_type=d["deal_type"],
            from_ccy=d["from_ccy"], from_amt=d["from_amt"],
            to_ccy=d["to_ccy"], to_amt=d["to_amt"],
            market_rate=mkt, auto_complete=True,
            custom_created_at=d.get("custom_date"),
            client_type=d.get("client_type", "organic"),
            client_name=d.get("client_name"),
        )
    except (ValueError, RuntimeError) as e:
        log.error(f"Quick deal failed: {e}")  # noqa: F821
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Меню", callback_data="main_menu")]])
        await q.edit_message_text(f"❌ Ошибка: {e}", reply_markup=kb)
        ctx.user_data.pop("quick", None)
        return
    sp = calc_spread(d["from_ccy"], d["from_amt"], d["to_ccy"], d["to_amt"], mkt)  # noqa: F821
    d["market_rate"] = mkt
    d["deal_rate"] = sp["deal_rate"]
    d["margin_our"] = sp["margin_our"]
    receipt = format_receipt(oid, d, mgr)  # noqa: F821
    receipt += f"\n\n{random.choice(DEAL_DONE_PHRASES)}"  # noqa: F821
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Ещё", callback_data="new_deal"),
         InlineKeyboardButton("📢 В канал", callback_data=f"post_ch_{oid}")],
        [InlineKeyboardButton("↩ Отменить", callback_data=f"undo_yes_{oid}"),
         InlineKeyboardButton("Меню", callback_data="main_menu")],
    ])
    await q.edit_message_text(receipt, reply_markup=kb, parse_mode="Markdown",
                              disable_web_page_preview=True)
    ctx.user_data.pop("quick", None)


async def cb_quick_cancel(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE"):
    """qd_cancel."""
    q = update.callback_query
    await q.answer()
    ctx.user_data.pop("quick", None)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Меню", callback_data="main_menu")]])
    await q.edit_message_text(f"Отменено. {random.choice(DEAL_CANCEL_PHRASES)}",  # noqa: F821
                              reply_markup=kb)


# ════════════════════════════════════════════════════════════════════════════════
# 3. МЕНЮ v2 — сгруппированное
#    main_menu_keyboard_v2 ЗАМЕНЯЕТ main_menu_keyboard (стр. 2390).
#    cb_menu_section — ДОБАВИТЬ; зарегистрировать ^menu_(deals|money|analytics|more)$.
# ════════════════════════════════════════════════════════════════════════════════

def main_menu_keyboard_v2(tg_id: int):
    """ЗАМЕНЯЕТ main_menu_keyboard. 8 кнопок вместо ~20."""
    kb = [
        [InlineKeyboardButton("➕ Новая сделка", callback_data="new_deal")],
        [InlineKeyboardButton("📊 Баланс", callback_data="balance"),
         InlineKeyboardButton("💱 Курсы", callback_data="rates"),
         InlineKeyboardButton("💰 P&L", callback_data="pnl_vwap")],
        [InlineKeyboardButton("💱 Сделки", callback_data="menu_deals"),
         InlineKeyboardButton("💰 Деньги", callback_data="menu_money")],
        [InlineKeyboardButton("📈 Аналитика", callback_data="menu_analytics"),
         InlineKeyboardButton("⚙️ Ещё", callback_data="menu_more")],
    ]
    if is_admin(tg_id):  # noqa: F821
        kb.append([InlineKeyboardButton("⚙️ Админ", callback_data="admin")])
    return InlineKeyboardMarkup(kb)


_MENU_SECTIONS = {
    "deals": ("💱 *Сделки*", [
        [("➕ Новая сделка", "new_deal")],
        [("✏️ Ввод курса", "manual_rate_menu"), ("🔢 Конвертер", "converter")],
        [("🧾 Чеки", "receipts"), ("💡 Цены", "pricing")],
        [("↩ Меню", "main_menu")],
    ]),
    "money": ("💰 *Деньги*", [
        [("📊 Баланс", "balance"), ("🔄 Ликвидность", "liquidity")],
        [("💰 P&L", "pnl_vwap"), ("💵 Cashflow", "cashflow")],
        [("🆕 Себестоимость", "cost_basis"), ("🔴 Реклама", "ads_menu")],
        [("↩ Меню", "main_menu")],
    ]),
    "analytics": ("📈 *Аналитика*", [
        [("📈 Статистика", "stats_menu"), ("📉 Тренды", "trends")],
        [("👥 Менеджеры", "mgr_stats"), ("🟢 Клиенты", "clients_menu")],
        [("🧠 Аналитика", "intelligence"), ("📁 Excel", "report_menu")],
        [("↩ Меню", "main_menu")],
    ]),
    "more": ("⚙️ *Ещё*", [
        [("💱 Курсы", "rates"), ("📝 Заметки", "notes_menu")],
        [("🧾 Чеки", "receipts"), ("📁 Excel", "report_menu")],
        [("↩ Меню", "main_menu")],
    ]),
}


async def cb_menu_section(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE"):
    """Раскрывает раздел меню. Pattern: ^menu_(deals|money|analytics|more)$"""
    q = update.callback_query
    await q.answer()
    sec = q.data.replace("menu_", "")
    title, rows = _MENU_SECTIONS.get(sec, ("Меню", [[("↩ Меню", "main_menu")]]))
    kb = [[InlineKeyboardButton(t, callback_data=c) for (t, c) in row] for row in rows]
    await q.edit_message_text(title, reply_markup=InlineKeyboardMarkup(kb),
                              parse_mode="Markdown")


# ════════════════════════════════════════════════════════════════════════════════
# 4. REPLY-КЛАВИАТУРА БЕЗ ДВОЙНОГО НАЖАТИЯ
#    reply_kb_router_v2 ЗАМЕНЯЕТ reply_kb_router (стр. 2531).
#    Требует REPLY_BTN_TO_CB и целевые cb_* из bot.py.
# ════════════════════════════════════════════════════════════════════════════════

class _FakeCallback:
    """
    Лёгкая обёртка: позволяет вызвать cb_*-хендлеры (которые ждут callback_query)
    из обычного сообщения. edit_message_text → reply_text (новое сообщение).
    """
    def __init__(self, message, from_user, data):
        self.message = message
        self.from_user = from_user
        self.data = data

    async def answer(self, *a, **k):
        return None

    async def edit_message_text(self, *a, **k):
        # рендерим как новое сообщение, а не правим reply-клавиатурное
        return await self.message.reply_text(*a, **k)


class _FakeUpdate:
    """Подменяет Update так, что update.callback_query есть, а update.message — нет."""
    def __init__(self, real_update, fake_cb):
        self._real = real_update
        self.callback_query = fake_cb
        self.message = None
        self.effective_user = real_update.effective_user

    def __getattr__(self, item):
        return getattr(self._real, item)


# словарь: reply-текст → целевая cb-функция. ЗАПОЛНИТЬ ссылками на реальные cb_* в bot.py.
# Пример (в bot.py): _REPLY_CB_FUNCS = {"balance": cb_balance, "pnl_vwap": cb_pnl_show_v2, ...}
_REPLY_CB_FUNCS = {}  # переопределить в bot.py после объявления cb_*


async def reply_kb_router_v2(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE"):
    """
    ЗАМЕНЯЕТ reply_kb_router. Один тап = действие (без промежуточной inline-кнопки).
    """
    if not update.message or not update.message.text:
        return
    txt = update.message.text.strip()
    cb = REPLY_BTN_TO_CB.get(txt)  # noqa: F821 (из bot.py)
    if not cb:
        return
    if not is_authorized(update.effective_user.id):  # noqa: F821
        await update.message.reply_text("Введи /start")
        return
    if cb == "main_menu":
        await show_main_menu(update, ctx)  # noqa: F821
        return
    if cb == "new_deal":
        # «Сделка» теперь подсказывает быстрый ввод (а не открывает мастер)
        await update.message.reply_text(
            "⚡ Напиши сделку одной строкой, напр.:\n"
            "`500 usdt 10400 czk`\n"
            "или `принял 500 usdt выдал 10400 czk`\n\n"
            "Или жми «🧙 По шагам».",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🧙 По шагам", callback_data="new_deal")]]),
            parse_mode="Markdown")
        return
    fn = _REPLY_CB_FUNCS.get(cb)
    if fn is None:
        # запасной путь: одна inline-кнопка (как раньше)
        await update.message.reply_text(
            "Открыть:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Открыть", callback_data=cb)]]))
        return
    fake_cb = _FakeCallback(update.message, update.effective_user, cb)
    fake_update = _FakeUpdate(update, fake_cb)
    await fn(fake_update, ctx)


# ════════════════════════════════════════════════════════════════════════════════
# 5. P&L — КРАТКАЯ СВОДКА + ДЕТАЛИ
#    cb_pnl_show_v2 ЗАМЕНЯЕТ cb_pnl_show (стр. 3072).
#    cb_pnl_full — ДОБАВИТЬ; зарегистрировать ^pnl_full_(week|month|quarter|year|all)$.
#    Внутри использует те же get_vwap_pnl/get_hwm_stats/get_stats/get_deal_spread_profit.
# ════════════════════════════════════════════════════════════════════════════════

_PNL_LBL = {"week": "Неделя", "month": "Месяц", "quarter": "Квартал",
            "year": "Год", "all": "Всё время"}


async def cb_pnl_show_v2(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE"):
    """ЗАМЕНЯЕТ cb_pnl_show — краткая сводка (8-10 строк) + кнопка Детали."""
    import asyncio
    q = update.callback_query
    await q.answer()
    p = q.data.replace("pnl_", "")
    lbl = _PNL_LBL.get(p, p)
    await q.edit_message_text(f"⏳ Считаю P&L ({lbl})...")
    _zero = {"vwap": 0, "market_rate": 0, "inventory": 0, "realized_pnl": 0,
             "unrealized_pnl": 0, "total_pnl": 0}
    try:
        vc = await asyncio.to_thread(get_vwap_pnl, "USDT", "CZK", p)  # noqa: F821
    except Exception:
        vc = _zero
    try:
        ve = await asyncio.to_thread(get_vwap_pnl, "USDT", "EUR", p)  # noqa: F821
    except Exception:
        ve = _zero
    hw = await asyncio.to_thread(get_hwm_stats)  # noqa: F821
    s = await asyncio.to_thread(get_stats, p)  # noqa: F821
    fp = lambda v, u="": f"{'+' if v >= 0 else ''}{v:,.2f}{u}"
    dc = s.get("deal_count", 0)
    mg = s.get("total_margin", 0)
    lines = [
        f"💰 *P&L — {lbl}*",
        "",
        f"Итог CZK: *{fp(vc['total_pnl'], ' CZK')}*",
        f"Итог EUR: *{fp(ve['total_pnl'], ' EUR')}*",
        f"Equity: {hw['equity_now']:,.0f} USDT  (DD {hw['drawdown_pct']:+.1f}%)",
        f"Сделок: {dc}   Маржа: {fp(mg, ' USDT')}",
    ]
    try:
        tip = _analyze_pnl(vc, ve, hw, s,  # noqa: F821
                           {"spread_profit": 0, "deals": dc}, lbl)
        if tip:
            lines += ["", f"🤖 {tip}"]
    except Exception:
        pass
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Детали", callback_data=f"pnl_full_{p}")],
        [InlineKeyboardButton("↩ Периоды", callback_data="pnl_vwap"),
         InlineKeyboardButton("Меню", callback_data="main_menu")],
    ])
    await q.edit_message_text("\n".join(lines), reply_markup=kb, parse_mode="Markdown")


async def cb_pnl_full(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE"):
    """
    ДОБАВИТЬ. Полная развёрнутая версия (старый вывод cb_pnl_show).
    Pattern: ^pnl_full_(week|month|quarter|year|all)$
    Реализация: переименовать сюда тело старого cb_pnl_show, заменив
    q.data.replace('pnl_','') → q.data.replace('pnl_full_',''), а кнопку
    '↩ Периоды' оставить + добавить '↩ Кратко' → callback f'pnl_{p}'.
    Тело опущено намеренно — это копия существующего кода bot.py.
    """
    raise NotImplementedError(
        "Перенести тело старого cb_pnl_show сюда, заменив префикс 'pnl_full_'.")


# ════════════════════════════════════════════════════════════════════════════════
# 6. /undo — отмена последней сделки
#    cmd_undo — ДОБАВИТЬ CommandHandler("undo", cmd_undo) рядом со стр. 4063.
#    cb_undo_confirm — ДОБАВИТЬ CallbackQueryHandler(pattern=r"^undo_yes_\d+$").
# ════════════════════════════════════════════════════════════════════════════════

def _last_undoable_order(tg_id: int):
    """Последняя completed-сделка менеджера за UNDO_WINDOW_MIN минут."""
    cutoff = (datetime.now() - timedelta(minutes=UNDO_WINDOW_MIN)).strftime("%Y-%m-%d %H:%M:%S")
    return db_exec(  # noqa: F821
        "SELECT * FROM orders WHERE tg_id=? AND state='completed' "
        "AND COALESCE(completed_at, created_at) >= ? "
        "ORDER BY id DESC LIMIT 1",
        (tg_id, cutoff), "one")


async def cmd_undo(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE"):
    """ДОБАВИТЬ. /undo — показать последнюю сделку и спросить подтверждение."""
    if not is_authorized(update.effective_user.id):  # noqa: F821
        await update.message.reply_text("Нет доступа.")
        return
    row = _last_undoable_order(update.effective_user.id)
    if not row:
        await update.message.reply_text(
            f"Нет сделок для отмены за последние {UNDO_WINDOW_MIN} мин.")
        return
    oid = row["id"]
    txt = (f"↩ Отменить последнюю сделку?\n\n"
           f"#{oid:07d}\n"
           f"Принял: {row['from_amt']:,.2f} {row['from_ccy']}\n"
           f"Выдал: {row['to_amt']:,.2f} {row['to_ccy']}\n"
           f"({(row['completed_at'] or row['created_at'] or '')[:16]})\n\n"
           f"Ledger будет реверсирован.")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да, отменить", callback_data=f"undo_yes_{oid}"),
         InlineKeyboardButton("Нет", callback_data="main_menu")],
    ])
    await update.message.reply_text(txt, reply_markup=kb)


async def cb_undo_confirm(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE"):
    """ДОБАВИТЬ. undo_yes_<id> — выполнить отмену (cancel_order реверсит ledger)."""
    q = update.callback_query
    await q.answer()
    try:
        oid = int(q.data.replace("undo_yes_", ""))
    except ValueError:
        return
    tid = update.effective_user.id
    row = db_exec("SELECT * FROM orders WHERE id=?", (oid,), "one")  # noqa: F821
    if not row:
        await q.edit_message_text("Сделка не найдена.")
        return
    if not is_admin(tid) and row["tg_id"] != tid:  # noqa: F821
        await q.edit_message_text("Можно отменять только свои сделки.")
        return
    if row["state"] == "cancelled":
        await q.edit_message_text(f"#{oid:07d} уже отменена.")
        return
    try:
        cancel_order(oid, tid)  # noqa: F821
        audit(tid, "undo", f"#{oid}")  # noqa: F821
    except Exception as e:
        log.error(f"undo #{oid} failed: {e}")  # noqa: F821
        await q.edit_message_text(f"❌ Не удалось отменить: {e}")
        return
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Меню", callback_data="main_menu")]])
    await q.edit_message_text(f"♻️ Сделка #{oid:07d} отменена, ledger возвращён.",
                              reply_markup=kb)


# ════════════════════════════════════════════════════════════════════════════════
# 7. КЛАВИАТУРЫ ДЛЯ ПОЛНОГО МАСТЕРА (заменяют ввод «или 2»)
#    Опционально использовать в deal_custom_date_handler / deal_xe_rate_handler /
#    deal_tx_hash_handler — заменить text-prompt на reply_markup=<эти клавиатуры>,
#    и добавить ветки CallbackQueryHandler в соответствующие состояния.
# ════════════════════════════════════════════════════════════════════════════════

def deal_date_kb():
    """Кнопки вместо ввода даты '2'. callback: dealdate_now / dealdate_yest / dealdate_type"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🕒 Сейчас", callback_data="dealdate_now"),
         InlineKeyboardButton("Вчера", callback_data="dealdate_yest")],
        [InlineKeyboardButton("⌨️ Ввести дату", callback_data="dealdate_type")],
    ])


def deal_xe_kb():
    """callback: dealxe_bot (= курс бота) / dealxe_type (ввести)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 Курс бота", callback_data="dealxe_bot"),
         InlineKeyboardButton("⌨️ Ввести XE", callback_data="dealxe_type")],
    ])


def deal_skip_kb():
    """callback: dealskip (пропустить, напр. TX hash) / dealtx_type (ввести)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Пропустить", callback_data="dealskip"),
         InlineKeyboardButton("⌨️ Ввести", callback_data="dealtx_type")],
    ])


# ════════════════════════════════════════════════════════════════════════════════
# REGISTRATION — что добавить в build/main() bot.py (после ConversationHandler):
# ────────────────────────────────────────────────────────────────────────────────
#   # быстрый ввод сделки — ПЕРЕД reply_kb_router, ПОСЛЕ conv:
#   for pat, fn in [
#       ("^qd_save$", cb_quick_confirm), ("^qd_src$", cb_quick_toggle_src),
#       ("^qd_date$", cb_quick_date), ("^qd_d_(now|yest|back)$", cb_quick_date_set),
#       ("^qd_cancel$", cb_quick_cancel),
#       ("^menu_(deals|money|analytics|more)$", cb_menu_section),
#       ("^pnl_full_(week|month|quarter|year|all)$", cb_pnl_full),
#       ("^undo_yes_\\d+$", cb_undo_confirm),
#   ]:
#       app.add_handler(CallbackQueryHandler(fn, pattern=pat))
#   app.add_handler(CommandHandler("undo", cmd_undo))
#   # глобальный текстовый перехват сделки (НИЗКИЙ приоритет, после reply_kb_router):
#   app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, cb_quick_deal_preview))
#   # заполнить ссылки для reply_kb_router_v2:
#   _REPLY_CB_FUNCS.update({
#       "balance": cb_balance, "pnl_vwap": cb_pnl_show_v2 (или cb_pnl_vwap),
#       "stats_menu": cb_stats_menu, "rates": cb_rates,
#       "cost_basis": cb_cost_basis, "liquidity": cb_liquidity,
#   })
# ════════════════════════════════════════════════════════════════════════════════


# ════════════════════════════════════════════════════════════════════════════════
# UNIT-ТЕСТЫ ПАРСЕРА  ·  python -X utf8 ux_patch.py
# ════════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    def chk(text, exp_from, exp_to):
        r = parse_quick_deal(text)
        assert r is not None, f"FAIL parse None: {text!r}"
        got = (r["from_amt"], r["from_ccy"], r["to_amt"], r["to_ccy"])
        assert got == exp_from + exp_to, f"FAIL {text!r}\n  exp from={exp_from} to={exp_to}\n  got from=({got[0]},{got[1]}) to=({got[2]},{got[3]})"
        print(f"OK  {text!r:<48} -> приняли {r['from_amt']:g} {r['from_ccy']}, выдали {r['to_amt']:g} {r['to_ccy']}  [{r['deal_type']}]")

    # 1. простой порядок: первая = приняли (from), вторая = выдали (to)
    chk("500 usdt 10400 czk", (500.0, "USDT"), (10400.0, "CZK"))
    # 2. словами «принял … выдал …»
    chk("принял 500 usdt выдал 10400 czk", (500.0, "USDT"), (10400.0, "CZK"))
    # 3. обратный порядок словами «выдал … принял …»
    chk("выдал 10400 czk принял 500 usdt", (500.0, "USDT"), (10400.0, "CZK"))
    # 4. стрелка
    chk("500 USDT -> 10400 CZK", (500.0, "USDT"), (10400.0, "CZK"))
    chk("500 USDT = 13000 CZK", (500.0, "USDT"), (13000.0, "CZK"))
    # 5. пробелы-тысячи
    chk("20 800 czk 800 eur", (20800.0, "CZK"), (800.0, "EUR"))
    # 6. символы валют € / $
    chk("€500 = 13000 czk", (500.0, "EUR"), (13000.0, "CZK"))
    chk("$1000 27000 czk", (1000.0, "USD"), (27000.0, "CZK"))
    # 7. запятая-десятичная
    chk("принял 1 250,5 usdt выдал 26000 czk", (1250.5, "USDT"), (26000.0, "CZK"))
    # 8. суффикс к/k = тысячи
    chk("10к czk 400 usdt", (10000.0, "CZK"), (400.0, "USDT"))
    chk("500 usdt 10k czk", (500.0, "USDT"), (10000.0, "CZK"))
    # 9. регистр + русские алиасы
    chk("принял 500 тезер выдал 13000 крон", (500.0, "USDT"), (13000.0, "CZK"))
    chk("ПРИНЯЛ 800 ЕВРО ВЫДАЛ 20000 CZK", (800.0, "EUR"), (20000.0, "CZK"))
    # 10. фиат→фиат и крипто-направление
    chk("принял 1000 eur выдал 25 000 czk", (1000.0, "EUR"), (25000.0, "CZK"))
    chk("отдал 27000 czk получил 1000 usd", (1000.0, "USD"), (27000.0, "CZK"))
    # 11. лишний текст вокруг
    chk("сделка: 500usdt 10400czk готово", (500.0, "USDT"), (10400.0, "CZK"))

    # deal_type инференс
    assert _infer_deal_type("USDT", "CZK") == "SELL"
    assert _infer_deal_type("CZK", "USDT") == "BUY"
    assert _infer_deal_type("EUR", "CZK") == "SWAP_FF"

    # негативные: не парсятся
    for bad in ["просто текст", "500 usdt", "баланс", "1000 рублей 50 гривен",
                "500 usdt 600 usdt", ""]:
        assert parse_quick_deal(bad) is None, f"FAIL should be None: {bad!r}"
    print("\nNEG OK: мусор и одиночные суммы не парсятся в сделку")

    print("\n✅ Все проверки парсера пройдены.")
