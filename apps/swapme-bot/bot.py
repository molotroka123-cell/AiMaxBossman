"""
SwapMe Exchange Bot v6 — Ledger-Based Engine
════════════════════════════════════════════════════

AUDIT FIXES FROM v4:
  BUG-1: Rate convention enforced — always "1 BASE = X QUOTE"
  BUG-2: Order state machine — deals have lifecycle (created→confirmed→completed)
  BUG-3: Fund reservation — reserves checked before deal, released on cancel
  BUG-4: Balance engine — only completed deals count, reserves tracked separately
  BUG-5: spread_abs vs margin_our — stored separately with correct semantics
  BUG-6: Volume normalized to USDT
  BUG-7: No intermediate rounding — raw values stored, rounded only for display
  BUG-8: Pricing uses correct available balance (minus reserves)
  FLAW-3: lastrowid instead of SELECT MAX(id)
  FLAW-4: Atomic transactions for multi-step operations

RATE CONVENTION (ENFORCED GLOBALLY):
  Rate for pair "A/B" means: 1 A = rate × B
  Example: "USDT/CZK" = 25.3 means 1 USDT = 25.3 CZK
  Example: "EUR/USDT" = 1.08 means 1 EUR = 1.08 USDT
  All rates stored, queried, and calculated using this convention.

ORDER STATES:
  created     — deal entered by manager, funds reserved
  confirmed   — client payment received, waiting our payout
  completed   — our payout sent, deal finished
  cancelled   — deal cancelled, reserved funds released

BALANCE MODEL:
  total_balance    = sum of all completed deal flows
  reserved_balance = sum of funds locked by non-completed, non-cancelled deals
  available_balance = total_balance - reserved_balance
  A deal CANNOT be created if available_balance < required amount
"""

import asyncio
import os
import logging
import sqlite3
import math
import random
import re
import string
import time
import threading
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from collections import defaultdict
from typing import Optional

import requests
from bs4 import BeautifulSoup
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.chart import BarChart, Reference

from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup,
                      ReplyKeyboardMarkup, KeyboardButton, BotCommand)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters, ContextTypes
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════════════
BOT_TOKEN  = os.environ["BOT_TOKEN"]  # обязательная переменная окружения
ADMIN_IDS  = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]  # set ADMIN_IDS in the env; no personal id in the repo
DB_PATH    = os.getenv("DB_PATH", "swapme.db")
XE_API_ID  = os.getenv("XE_API_ID", "")
XE_API_KEY = os.getenv("XE_API_KEY", "")
DB_BACKUP  = os.path.join(os.path.dirname(DB_PATH) or ".", "swapme_backup.db")
DAILY_REPORT_HOUR   = 21
DAILY_REPORT_MINUTE = 0
CHANNEL_ID = os.getenv("CHANNEL_ID", "-3374859028")

CURRENCIES = ["USDT", "EUR", "USD", "CZK"]
DEAL_TYPES = {
    "BUY":     "Покупка (клиент даёт фиат → получает крипто)",
    "SELL":    "Продажа (клиент даёт крипто → получает фиат)",
    "SWAP_FF": "Обмен фиат → фиат",
    "SWAP_CC": "Обмен крипто → крипто",
}
MANUAL_RATE_PAIRS = [
    "EUR/USDT", "USD/USDT", "CZK/USDT", "USDT/CZK",
    "EUR/CZK",  "USD/CZK",  "EUR/USD",
]
PAYMENT_METHODS = [
    "USDT TRC20", "USDT ERC20", "Наличные CZK",
    "Наличные EUR", "Наличные USD", "Банк перевод",
]
ORDER_STATES = ["created", "confirmed", "completed", "cancelled"]

# Fun phrases
DEAL_DONE_PHRASES = [
    "😉 Пополняем капитал!",
    "🍓 Добавил в борщ сметаны!",
    "🔥 Работаем!",
    "💰 Касса звенит!",
    "🚀 Летим дальше!",
    "😎 Чётко отработано!",
    "🎯 В яблочко!",
    "⚡ Молния!",
    "🏆 Ещё одна в копилку!",
    "💎 Бриллиант!",
]
DEAL_CANCEL_PHRASES = [
    "🫡 Принято, отдыхаем!",
    "🧊 Заморожено!",
    "🙈 Не в этот раз!",
    "♻️ Отмотали назад!",
    "🫠 Бывает!",
]

# Pricing
BASE_SPREAD_PCT = 1.5
INVENTORY_SKEW_THRESHOLD = 0.3
MAX_SPREAD_MULTIPLIER = 3.0
MIN_SPREAD_PCT = 0.3
VOLATILITY_LOOKBACK_H = 24
VOLATILITY_SPREAD_FACTOR = 0.5
_custom_spread = {}  # {"CZK": 2.5, "EUR": 1.8} — custom spread % per quote_ccy, empty = auto

# Conversation states
(
    AUTH_CODE,
    DEAL_TYPE, DEAL_FROM_CCY, DEAL_FROM_AMT,
    DEAL_TO_CCY, DEAL_TO_AMT, DEAL_CONFIRM,
    MANUAL_RATE_PAIR, MANUAL_RATE_VALUE,
    NOTE_TEXT,
    CONVERTER_FROM, CONVERTER_AMT, CONVERTER_TO,
    DELETE_DEAL_ID,
    RECEIPT_METHOD, RECEIPT_ADDRESS, RECEIPT_FEE, RECEIPT_TX,
    EDIT_DATE_ID, EDIT_DATE_FIELD, EDIT_DATE_VALUE,
    DEAL_CUSTOM_DATE,
    DEAL_XE_RATE,
    DEAL_TX_HASH,
    DEAL_CLIENT_TYPE, DEAL_CLIENT_NAME,
    EXP_CCY, EXP_AMT, EXP_NOTE, EXP_DELETE_ID,
    AD_EXP_CCY, AD_EXP_AMT, AD_EXP_NOTE,
    LIQ_TYPE, LIQ_CCY, LIQ_AMT, LIQ_NOTE,
    DEDIT_ID, DEDIT_FIELD, DEDIT_VALUE,
    LIQ_DELETE_ID,
    CUSTOM_SPREAD_VALUE,
) = range(42)

# ═══════════════════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s | %(funcName)s:%(lineno)d | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("SwapMe")

# ═══════════════════════════════════════════════════════════════════════════════
# RATE CACHE
# ═══════════════════════════════════════════════════════════════════════════════
class RateCache:
    def __init__(self, ttl: int = 30):
        self._c: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()
        self.ttl = ttl

    def get(self, pair: str) -> Optional[float]:
        with self._lock:
            if pair in self._c:
                r, ts = self._c[pair]
                if time.time() - ts < self.ttl:
                    return r
                del self._c[pair]  # evict expired entry, don't let dict grow stale
        return None

    def put(self, pair: str, rate: float):
        with self._lock:
            self._c[pair] = (rate, time.time())

    def put_many(self, rates: dict):
        with self._lock:
            now = time.time()
            for p, r in rates.items():
                if r and r > 0:
                    self._c[p] = (r, now)

    def clear(self):
        with self._lock:
            self._c.clear()

_cache = RateCache(30)

STALE_RATE_HOURS = 12  # DB rate older than this → warn once per pair (sources likely down)
_stale_warned: set = set()
_stale_lock = threading.Lock()


def _rate_age_hours(updated_at) -> Optional[float]:
    """Age of a rates row in hours. updated_at is written via SQL datetime('now') → UTC."""
    if not updated_at:
        return None
    try:
        ts = datetime.strptime(str(updated_at), "%Y-%m-%d %H:%M:%S")
        return max(0.0, (datetime.utcnow() - ts).total_seconds() / 3600.0)
    except ValueError:
        return None


def _warn_if_stale(pair: str, age_hours: Optional[float]):
    """Warn ONCE per pair while it stays stale (>STALE_RATE_HOURS). Value still returned."""
    if age_hours is None:
        return
    if age_hours > STALE_RATE_HOURS:
        with _stale_lock:
            first = pair not in _stale_warned
            _stale_warned.add(pair)
        if first:
            log.warning(f"⚠️ STALE RATE {pair}: last update {age_hours:.1f}h ago "
                        f"(> {STALE_RATE_HOURS}h) — check rate sources!")
    else:
        with _stale_lock:
            _stale_warned.discard(pair)


# ═══════════════════════════════════════════════════════════════════════════════
# SECURITY — panic mode, data hiding
# ═══════════════════════════════════════════════════════════════════════════════
_panic_mode = False  # global flag — when True, only ADMIN_IDS can use bot
_data_hidden = False  # global flag — when True, DB is empty (backup exists)


def panic_on():
    """Activate panic mode — block all non-admin users (remember prior state)."""
    global _panic_mode
    _panic_mode = True
    con = get_db()
    try:
        admins = ",".join(str(a) for a in ADMIN_IDS)
        con.execute(f"UPDATE users SET panic_prev_active=active WHERE tg_id NOT IN ({admins})")
        con.execute(f"UPDATE users SET active=0 WHERE tg_id NOT IN ({admins})")
        con.commit()
    finally:
        con.close()
    log.warning("🚨 PANIC MODE ON — all managers blocked")


def panic_off():
    """Deactivate panic mode — restore users to their pre-panic state."""
    global _panic_mode
    _panic_mode = False
    con = get_db()
    try:
        con.execute("UPDATE users SET active=COALESCE(panic_prev_active, active), "
                    "panic_prev_active=NULL WHERE panic_prev_active IS NOT NULL")
        con.commit()
    finally:
        con.close()
    log.info("✅ PANIC MODE OFF — users restored to pre-panic state")


def hide_data():
    """Move DB to backup, reinitialize empty DB."""
    global _data_hidden
    import shutil
    if os.path.exists(DB_BACKUP):
        raise ValueError("Backup already exists. Restore first.")
    if os.path.exists(DB_PATH):
        shutil.copy2(DB_PATH, DB_BACKUP)
        os.remove(DB_PATH)
    init_db()
    _data_hidden = True
    log.warning("🔒 DATA HIDDEN — DB backed up, fresh DB created")


def restore_data():
    """Restore DB from backup."""
    global _data_hidden
    import shutil
    if not os.path.exists(DB_BACKUP):
        raise ValueError("No backup found.")
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    shutil.move(DB_BACKUP, DB_PATH)
    _data_hidden = False
    log.info("🔓 DATA RESTORED from backup")


def check_data_hidden():
    """Check on startup if data is hidden."""
    global _data_hidden
    _data_hidden = os.path.exists(DB_BACKUP)

# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE — WAL, atomic transactions, correct schema
# ═══════════════════════════════════════════════════════════════════════════════
def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA foreign_keys=ON")
    cur = con.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id INTEGER PRIMARY KEY, username TEXT, name TEXT,
            role TEXT DEFAULT 'manager', active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS auth_codes (
            code TEXT PRIMARY KEY, created_by INTEGER,
            used INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now'))
        );

        /* === ORDERS (state machine) === */
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER NOT NULL,
            manager TEXT NOT NULL,
            deal_type TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'created',

            /* What client gives us */
            from_ccy TEXT NOT NULL,
            from_amt REAL NOT NULL,

            /* What client receives */
            to_ccy TEXT NOT NULL,
            to_amt REAL NOT NULL,

            /* Rates — raw, no rounding */
            deal_rate REAL NOT NULL,       /* to_amt / from_amt */
            market_rate REAL,              /* market rate at time of deal (from_ccy→to_ccy) */

            /* Margin — raw, no rounding */
            margin_our REAL DEFAULT 0,     /* market_rate*from_amt - to_amt (in to_ccy) */
            spread_pct REAL DEFAULT 0,     /* margin_our / to_amt * 100 */

            /* Fee — deducted from to_amt the client receives */
            fee REAL DEFAULT 0,
            fee_ccy TEXT,

            /* Receipt info */
            method TEXT,
            address TEXT,
            tx_hash TEXT,

            note TEXT,
            client_type TEXT DEFAULT 'organic',
            client_name TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            confirmed_at TEXT,
            completed_at TEXT,
            cancelled_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ord_state ON orders(state);
        CREATE INDEX IF NOT EXISTS idx_ord_ccy ON orders(from_ccy, to_ccy);
        CREATE INDEX IF NOT EXISTS idx_ord_date ON orders(created_at);
        CREATE INDEX IF NOT EXISTS idx_ord_mgr ON orders(manager);

        /* === RATES === */
        CREATE TABLE IF NOT EXISTS rates (
            pair TEXT PRIMARY KEY, rate REAL, source TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        /* === SNAPSHOTS === */
        CREATE TABLE IF NOT EXISTS rate_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pair TEXT NOT NULL, rate REAL NOT NULL,
            recorded_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_rh ON rate_history(pair, recorded_at);

        CREATE TABLE IF NOT EXISTS equity_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            equity_usdt REAL NOT NULL, recorded_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS liquidity_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ccy TEXT NOT NULL, balance REAL NOT NULL,
            recorded_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_liq ON liquidity_snapshots(ccy, recorded_at);

        /* === MISC === */
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER,
            manager TEXT, text TEXT, created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER,
            username TEXT, action TEXT, detail TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS pricing_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, pair TEXT,
            base_spread REAL, vol_adj REAL, inv_adj REAL, flow_adj REAL,
            final_spread REAL, suggested_buy REAL, suggested_sell REAL,
            recorded_at TEXT DEFAULT (datetime('now'))
        );

        /* v6: Ledger — single source of truth for balances */
        CREATE TABLE IF NOT EXISTS ledger_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ccy TEXT NOT NULL,
            amount REAL NOT NULL,
            entry_type TEXT NOT NULL,
            ref_id INTEGER,
            note TEXT,
            ts TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_ledger_ccy ON ledger_entries(ccy);
        CREATE INDEX IF NOT EXISTS idx_ledger_ref ON ledger_entries(ref_id);
        CREATE INDEX IF NOT EXISTS idx_ledger_ts ON ledger_entries(ts);

        /* v6.5: Expenses — trackable, deletable costs */
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ccy TEXT NOT NULL,
            amount REAL NOT NULL,
            note TEXT,
            tg_id INTEGER,
            manager TEXT,
            ledger_id INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        );

        /* v7: Ad expenses — separate from operating expenses */
        CREATE TABLE IF NOT EXISTS ad_expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount REAL NOT NULL,
            ccy TEXT NOT NULL DEFAULT 'CZK',
            note TEXT,
            tg_id INTEGER,
            manager TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        /* v7: Liquidity sources — own vs credit tracking */
        CREATE TABLE IF NOT EXISTS liquidity_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ccy TEXT NOT NULL,
            amount REAL NOT NULL,
            type TEXT NOT NULL DEFAULT 'own',
            note TEXT,
            tg_id INTEGER,
            manager TEXT,
            ledger_id INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        );

        /* v8: якорный метод капитала (порт capital_pnl.py) */
        CREATE TABLE IF NOT EXISTS capital_anchor (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            anchor_ts TEXT NOT NULL,
            start_usdt REAL NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS capital_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            capital_usdt REAL NOT NULL,
            pnl_usdt REAL,
            expenses_usdt REAL,
            recorded_at TEXT NOT NULL
        );
    """)

    # Migration: if old 'deals' table exists, note it
    has_deals = cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='deals'").fetchone()
    has_orders = cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='orders'").fetchone()
    if has_deals and has_orders:
        # Migrate completed deals from v4 to orders
        existing = cur.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        if existing == 0:
            try:
                cur.execute("""
                    INSERT INTO orders (tg_id, manager, deal_type, state,
                        from_ccy, from_amt, to_ccy, to_amt, deal_rate, market_rate,
                        margin_our, spread_pct, fee, fee_ccy, method, address, tx_hash, note, created_at,
                        completed_at)
                    SELECT tg_id, manager, deal_type, 'completed',
                        from_ccy, from_amt, to_ccy, to_amt, deal_rate, market_rate,
                        COALESCE(margin_our,0), COALESCE(spread_pct,0),
                        COALESCE(fee,0), fee_ccy, method, address, tx_hash, note, created_at,
                        created_at
                    FROM deals WHERE COALESCE(deleted,0)=0
                """)
                migrated = cur.rowcount
                log.info(f"Migrated {migrated} deals from v4 to orders table")
            except Exception as e:
                log.warning(f"Migration from deals table: {e}")

    for aid in ADMIN_IDS:
        cur.execute("INSERT OR IGNORE INTO users (tg_id,name,role) VALUES (?,'Admin','admin')", (aid,))
    # v7: Migration — add client_type, client_name to orders if missing
    try:
        cur.execute("SELECT client_type FROM orders LIMIT 1")
    except Exception:
        try:
            cur.execute("ALTER TABLE orders ADD COLUMN client_type TEXT DEFAULT 'organic'")
            log.info("Migration: added client_type to orders")
        except Exception as e:
            log.warning(f"client_type migration: {e}")
    try:
        cur.execute("SELECT client_name FROM orders LIMIT 1")
    except Exception:
        try:
            cur.execute("ALTER TABLE orders ADD COLUMN client_name TEXT")
            log.info("Migration: added client_name to orders")
        except Exception as e:
            log.warning(f"client_name migration: {e}")
    # v6.5: panic mode must restore users to their PRE-panic state, not blanket-activate
    try:
        cur.execute("SELECT panic_prev_active FROM users LIMIT 1")
    except Exception:
        try:
            cur.execute("ALTER TABLE users ADD COLUMN panic_prev_active INTEGER")
            log.info("Migration: added panic_prev_active to users")
        except Exception as e:
            log.warning(f"panic_prev_active migration: {e}")
    con.commit()
    con.close()
    log.info("DB v6.4 initialized (WAL, orders state machine)")


def get_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def db_exec(sql: str, params=(), fetch: str = "all"):
    """Single-statement execute. For multi-step, use db_transaction."""
    con = get_db()
    try:
        cur = con.execute(sql, params)
        con.commit()
        if fetch == "one":
            return cur.fetchone()
        if fetch == "all":
            return cur.fetchall()
        if fetch == "lastrowid":
            return cur.lastrowid
        return None
    finally:
        con.close()


def db_transaction(operations: list) -> list:
    """
    Execute multiple (sql, params) atomically in a single transaction.
    Returns list of lastrowid for each operation.
    """
    con = get_db()
    results = []
    try:
        con.execute("BEGIN IMMEDIATE")
        for sql, params in operations:
            cur = con.execute(sql, params)
            results.append(cur.lastrowid)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()
    return results


def _safe(row, key, default=None):
    try:
        v = row[key]
        return v if v is not None else default
    except (IndexError, KeyError):
        return default


def is_authorized(tg_id: int) -> bool:
    if _panic_mode and tg_id not in ADMIN_IDS:
        return False
    return db_exec("SELECT 1 FROM users WHERE tg_id=? AND active=1", (tg_id,), "one") is not None

def is_admin(tg_id: int) -> bool:
    return db_exec("SELECT 1 FROM users WHERE tg_id=? AND role='admin'", (tg_id,), "one") is not None

def get_user_name(tg_id: int) -> str:
    row = db_exec("SELECT name FROM users WHERE tg_id=?", (tg_id,), "one")
    return row["name"] if row else "Unknown"

def audit(tg_id: int, action: str, detail: str = ""):
    uname = get_user_name(tg_id)
    db_exec("INSERT INTO audit_log (tg_id,username,action,detail) VALUES (?,?,?,?)",
            (tg_id, uname, action, detail), fetch=None)
    log.info(f"AUDIT | {tg_id} ({uname}) | {action} | {detail}")


# ═══════════════════════════════════════════════════════════════════════════════
# LEDGER ENGINE (v6) — single source of truth for balances
# ═══════════════════════════════════════════════════════════════════════════════

def ledger_add(ccy: str, amount: float, entry_type: str, ref_id: int = None,
               note: str = "", ts: str = None) -> int:
    """Write one ledger entry. + = we gain, - = we lose. Returns rowid."""
    ts = ts or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(DB_PATH) as con:
        con.execute("PRAGMA busy_timeout=5000")
        cur = con.execute(
            "INSERT INTO ledger_entries (ccy,amount,entry_type,ref_id,note,ts) "
            "VALUES (?,?,?,?,?,?)", (ccy, amount, entry_type, ref_id, note, ts))
        return cur.lastrowid


def init_account(balances: dict):
    """
    One-time account initialization. Creates 'opening' ledger entries.
    balances = {"USDT": 5000, "CZK": 100000, "EUR": 0, "USD": 0}
    Атомарно и идемпотентно: проверка существования внутри BEGIN IMMEDIATE,
    все legs пишутся одной транзакцией (частичный сбой невозможен),
    повторный вызов просто пропускается без дублей и без исключения.
    """
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    con = get_db()
    try:
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute(
            "SELECT COUNT(*) c FROM ledger_entries WHERE entry_type='opening'").fetchone()
        if existing["c"] > 0:
            con.execute("ROLLBACK")
            log.warning("init_account: opening entries уже существуют — skip (идемпотентно)")
            return
        for ccy, amount in balances.items():
            if amount != 0:
                con.execute(
                    "INSERT INTO ledger_entries (ccy,amount,entry_type,ref_id,note,ts) "
                    "VALUES (?,?,?,?,?,?)",
                    (ccy, amount, "opening", None, f"opening {ccy}", ts))
        con.execute("COMMIT")
    except Exception:
        try: con.execute("ROLLBACK")
        except sqlite3.Error: pass
        raise
    finally:
        con.close()
    log.info(f"Account initialized: {balances}")


def init_reserve(ccy: str, amount: float):
    """
    Reserve funds (e.g. cold wallet, safety buffer).
    Creates negative ledger entry type='reserve'.
    Идемпотентно per ccy: проверка существования внутри BEGIN IMMEDIATE,
    повторный вызов пропускается без дублей и без исключения.
    """
    if amount <= 0:
        raise ValueError(f"Reserve amount must be > 0, got {amount}")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    con = get_db()
    try:
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute(
            "SELECT COUNT(*) c FROM ledger_entries WHERE entry_type='reserve' AND ccy=?",
            (ccy,)).fetchone()
        if existing["c"] > 0:
            con.execute("ROLLBACK")
            log.warning(f"init_reserve({ccy}): reserve уже существует — skip (идемпотентно)")
            return
        con.execute(
            "INSERT INTO ledger_entries (ccy,amount,entry_type,ref_id,note,ts) "
            "VALUES (?,?,?,?,?,?)",
            (ccy, -amount, "reserve", None, f"reserve {ccy}", ts))
        con.execute("COMMIT")
    except Exception:
        try: con.execute("ROLLBACK")
        except sqlite3.Error: pass
        raise
    finally:
        con.close()
    log.info(f"Reserve: {ccy} -{amount}")


def add_liquidity(ccy: str, amount: float, note: str = "manual top-up",
                  src_type: str = "own", tg_id: int = 0, manager: str = ""):
    """
    v7: Add funds at runtime. src_type ∈ {'own','credit'}.
      - 'own':    our own capital load. Adds cost to VWAP via a synthetic BUY order
                  (from_ccy=ccy, from_amt=amount, to_ccy=ccy, to_amt=0) — no, instead
                  we book ledger inflow + insert into liquidity_sources so VWAP can see it.
      - 'credit': took liquidity from another exchanger. Ledger inflow + liquidity row
                  marked as debt. VWAP treats amount as qty @ cost=amount*spot (USDT) or
                  simply as zero-cost inflow for fiats (depends on pair).
    """
    if amount <= 0:
        raise ValueError("Amount must be positive")
    if src_type not in ("own", "credit"):
        raise ValueError("src_type must be 'own' or 'credit'")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry_type = "liq_own" if src_type == "own" else "liq_credit"
    # Атомарно: ledger inflow + liquidity_sources в ОДНОЙ транзакции (одно con).
    # Раньше два отдельных соединения: падение второго шага (busy timeout)
    # давало расхождение ledger↔liquidity_sources → CRIT в full_audit.
    con = get_db()
    try:
        con.execute("BEGIN IMMEDIATE")
        cur = con.execute(
            "INSERT INTO ledger_entries (ccy,amount,entry_type,ref_id,note,ts) "
            "VALUES (?,?,?,?,?,?)",
            (ccy, amount, entry_type, None, f"{src_type}: {note}", ts))
        ledger_id = cur.lastrowid
        con.execute("""INSERT INTO liquidity_sources (ccy, amount, type, note, tg_id, manager, ledger_id, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (ccy, amount, src_type, note, tg_id, manager, ledger_id, ts))
        con.execute("COMMIT")
    except Exception:
        try: con.execute("ROLLBACK")
        except sqlite3.Error: pass
        raise
    finally:
        con.close()
    log.info(f"Liquidity added: {ccy} +{amount} [{src_type}] ({note})")


def withdraw_liquidity(ccy: str, amount: float, note: str = "manual withdrawal",
                       src_type: str = "own", tg_id: int = 0, manager: str = ""):
    """v7: Withdraw / repay credit. src_type ∈ {'own','credit'}."""
    if amount <= 0:
        raise ValueError("Amount must be positive")
    if src_type not in ("own", "credit"):
        raise ValueError("src_type must be 'own' or 'credit'")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry_type = "liq_own" if src_type == "own" else "liq_credit"
    prefix = "withdraw" if src_type == "own" else "repay credit"
    # Атомарно: ledger + liquidity_sources в одной транзакции (см. add_liquidity).
    con = get_db()
    try:
        con.execute("BEGIN IMMEDIATE")
        cur = con.execute(
            "INSERT INTO ledger_entries (ccy,amount,entry_type,ref_id,note,ts) "
            "VALUES (?,?,?,?,?,?)",
            (ccy, -amount, entry_type, None, f"{prefix}: {note}", ts))
        ledger_id = cur.lastrowid
        con.execute("""INSERT INTO liquidity_sources (ccy, amount, type, note, tg_id, manager, ledger_id, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (ccy, -amount, src_type, note, tg_id, manager, ledger_id, ts))
        con.execute("COMMIT")
    except Exception:
        try: con.execute("ROLLBACK")
        except sqlite3.Error: pass
        raise
    finally:
        con.close()
    log.info(f"Liquidity withdrawn: {ccy} -{amount} [{src_type}] ({note})")


def get_liquidity_history(limit: int = 20) -> list:
    """v7: recent liquidity events (ordered newest first)."""
    return db_exec(
        "SELECT * FROM liquidity_sources ORDER BY id DESC LIMIT ?", (limit,)
    )


def ledger_reverse_order(order_id: int, ts: str = None):
    """Reverse all ledger entries for an order (atomically — раньше падение на
    середине оставляло сторнированной только часть ног)."""
    ts = ts or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = db_exec("SELECT ccy,amount,entry_type FROM ledger_entries WHERE ref_id=?", (order_id,))
    ops = [("INSERT INTO ledger_entries (ccy,amount,entry_type,ref_id,note,ts) VALUES (?,?,?,?,?,?)",
            (e["ccy"], -e["amount"], "reversal", order_id, f"#{order_id} rev {e['entry_type']}", ts))
           for e in rows]
    if ops:
        db_transaction(ops)


def get_cashflow(period: str = "day") -> dict:
    """Cashflow report: inflow/outflow/fee/net per currency."""
    since = period_since(period)
    rows = db_exec(
        "SELECT ccy, entry_type, SUM(amount) total FROM ledger_entries "
        "WHERE ts>=? AND entry_type IN ('inflow','outflow','fee','fee_income') "
        "GROUP BY ccy, entry_type", (since,))
    cf = {}
    for r in rows:
        ccy = r["ccy"]
        if ccy not in cf:
            cf[ccy] = {"inflow": 0, "outflow": 0, "fee": 0, "net": 0}
        cf[ccy][r["entry_type"]] = round(r["total"], 2)
    for ccy in cf:
        cf[ccy]["fee_income"] = cf[ccy].get("fee_income", 0)
        cf[ccy]["net"] = round(cf[ccy]["inflow"] + cf[ccy]["outflow"] + cf[ccy].get("fee", 0) + cf[ccy].get("fee_income", 0), 2)
    return cf


def edit_order_date(order_id: int, field: str, new_date: str, tg_id: int = 0, force: bool = False):
    """
    Edit created_at or completed_at.
    For completed orders: requires force=True (prevents silent history rewrite).
    new_date валидируется ('ГГГГ-ММ-ДД ЧЧ:ММ:СС' или 'ГГГГ-ММ-ДД' → дополняется
    ' 00:00:00'); оба UPDATE (orders + ledger_entries) — одной транзакцией.
    """
    if field not in ("created_at", "completed_at"):
        raise ValueError(f"Can only edit created_at or completed_at")
    try:
        dt = datetime.strptime(new_date.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            dt = datetime.strptime(new_date.strip(), "%Y-%m-%d")
        except ValueError:
            raise ValueError(
                f"Неверный формат даты '{new_date}': ожидается 'ГГГГ-ММ-ДД ЧЧ:ММ:СС' "
                f"или 'ГГГГ-ММ-ДД'")
    new_date = dt.strftime("%Y-%m-%d %H:%M:%S")
    con = get_db()
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not row:
            raise ValueError(f"Order {order_id} not found")
        if row["state"] == "completed" and not force:
            raise ValueError(f"Order {order_id} is completed — pass force=True to rewrite history")
        old_val = row[field] if row[field] else "NULL"
        con.execute(f"UPDATE orders SET {field}=? WHERE id=?", (new_date, order_id))
        # Always sync ledger timestamps for completed orders
        if row["state"] == "completed":
            con.execute("UPDATE ledger_entries SET ts=? WHERE ref_id=?", (new_date, order_id))
            log.info(f"Date edit #{order_id}: {field} {old_val} → {new_date} (ledger synced)")
        con.execute("COMMIT")
    except Exception:
        try: con.execute("ROLLBACK")
        except sqlite3.Error: pass
        raise
    finally:
        con.close()
    audit(tg_id, "edit_date", f"#{order_id} {field}: {old_val} → {new_date} force={force}")


# ═══════════════════════════════════════════════════════════════════════════════
# RATE FEEDS — reliable fallback chain
# ═══════════════════════════════════════════════════════════════════════════════
# Convention: all rates stored as "BASE/QUOTE" meaning 1 BASE = rate × QUOTE.

def _fetch_binance() -> dict:
    rates = {}
    price = None
    last_err = None
    # Critical source: one retry on exception / bad response
    for attempt in (1, 2):
        try:
            r = requests.get("https://api.binance.com/api/v3/ticker/price",
                             params={"symbol": "EURUSDT"}, timeout=5)
            if r.status_code == 200:
                price = float(r.json()["price"])
                break
            last_err = f"HTTP {r.status_code}"
        except Exception as e:
            last_err = e
        if attempt == 1:
            time.sleep(1.5)
    if price and price > 0:
        rates["EUR/USDT"] = price  # EURUSDT = how many USDT per 1 EUR
        rates["USDT/EUR"] = 1.0 / price
    else:
        log.warning(f"Binance: {last_err}")
    rates["USDT/USD"] = 1.0
    rates["USD/USDT"] = 1.0
    return rates


def _fetch_kraken() -> dict:
    rates = {}
    try:
        r = requests.get("https://api.kraken.com/0/public/Ticker",
                         params={"pair": "USDTEUR,USDTUSD"}, timeout=8)
        if r.status_code != 200:
            log.warning(f"Kraken: HTTP {r.status_code}")
            return rates
        data = r.json().get("result", {})
        for key, val in data.items():
            last = float(val["c"][0])
            if "EUR" in key:
                # USDTEUR = how many EUR per 1 USDT
                rates["USDT/EUR"] = last
                if last > 0:
                    rates["EUR/USDT"] = 1.0 / last
            elif "USD" in key and "EUR" not in key:
                rates["USDT/USD"] = last
    except Exception as e:
        log.warning(f"Kraken: {e}")
    return rates


def _fetch_coingecko() -> dict:
    try:
        r = requests.get("https://api.coingecko.com/api/v3/simple/price",
                         params={"ids": "tether", "vs_currencies": "eur,usd,czk"}, timeout=8)
        if r.status_code != 200:
            log.warning(f"CoinGecko: HTTP {r.status_code}")
            return {}
        d = r.json()["tether"]
        return {"USDT/EUR": d["eur"], "USDT/USD": d["usd"], "USDT/CZK": d["czk"]}
    except Exception as e:
        log.warning(f"CoinGecko: {e}")
    return {}


def _fetch_frankfurter() -> dict:
    """ECB rates via frankfurter.app — free, no key, reliable for EUR/CZK, EUR/USD."""
    try:
        r = requests.get("https://api.frankfurter.app/latest",
                         params={"from": "EUR", "to": "CZK,USD"}, timeout=8)
        if r.status_code != 200:
            log.warning(f"Frankfurter: HTTP {r.status_code}")
            return {}
        d = r.json().get("rates", {})
        rates = {}
        if "CZK" in d:
            rates["EUR/CZK"] = d["CZK"]
        if "USD" in d:
            rates["EUR/USD"] = d["USD"]
        return rates
    except Exception as e:
        log.warning(f"Frankfurter: {e}")
    return {}


def _fetch_google(f: str, t: str) -> Optional[float]:
    try:
        url = f"https://www.google.com/finance/quote/{f}-{t}"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        if r.status_code != 200:
            log.warning(f"Google {f}/{t}: HTTP {r.status_code}")
            return None
        el = BeautifulSoup(r.text, "html.parser").find("div", class_="YMlKec fxKbKc")
        if el:
            return float(el.text.replace(",", "").strip())
        log.warning(f"Google {f}/{t}: unexpected page format, body[:200]={r.text[:200]!r}")
    except Exception as e:
        log.warning(f"Google {f}/{t}: {e}")
    return None


def _fetch_xe() -> dict:
    """Fetch rates from XE API (most accurate mid-market rates)."""
    if not XE_API_ID or not XE_API_KEY:
        return {}
    result = {}
    # Fetch all needed pairs in minimal calls
    pairs = [("USD", "CZK,EUR"), ("EUR", "USD,CZK"), ("USDT", "USD")]
    for from_ccy, to_ccys in pairs:
        data = None
        last_err = None
        # Critical source: one retry on exception / bad response
        for attempt in (1, 2):
            try:
                r = requests.get(
                    f"https://xecdapi.xe.com/v1/convert_from.json?from={from_ccy}&to={to_ccys}&amount=1",
                    auth=(XE_API_ID, XE_API_KEY), timeout=10
                )
                if r.status_code == 200:
                    data = r.json()
                    break
                last_err = f"HTTP {r.status_code}"
            except Exception as e:
                last_err = e
            if attempt == 1:
                time.sleep(1.5)
        if data is None:
            log.warning(f"XE API {from_ccy}/{to_ccys}: {last_err}")
            continue
        for item in data.get("to", []):
            tc = item["quotecurrency"]
            mid = item.get("mid")
            if mid and mid > 0:
                result[f"{from_ccy}/{tc}"] = mid
                log.info(f"XE: {from_ccy}/{tc} = {mid:.6f}")
    # Validate: discard if all values are identical (sandbox/test data)
    vals = [v for v in result.values() if v]
    if len(vals) >= 2 and len(set(round(v, 4) for v in vals)) == 1:
        log.warning(f"XE API returned test data ({vals[0]}), ignoring")
        return {}
    # Sanity checks
    if "USD/CZK" in result and not (15 < result["USD/CZK"] < 35):
        log.warning(f"XE USD/CZK={result['USD/CZK']} out of range, ignoring")
        return {}
    if "EUR/USD" in result and not (0.9 < result["EUR/USD"] < 1.3):
        log.warning(f"XE EUR/USD={result['EUR/USD']} out of range, ignoring")
        del result["EUR/USD"]
    if "EUR/CZK" in result and not (23 < result["EUR/CZK"] < 27):
        log.warning(f"XE EUR/CZK={result['EUR/CZK']} out of range, ignoring")
        del result["EUR/CZK"]
    # Derive USDT/CZK from USDT/USD + USD/CZK
    if "USDT/USD" in result and "USD/CZK" in result:
        result["USDT/CZK"] = result["USDT/USD"] * result["USD/CZK"]
    if "USDT/USD" in result and "EUR/USD" in result:
        result["USDT/EUR"] = result["USDT/USD"] / result["EUR/USD"]
    return result


def refresh_rates() -> dict:
    """Master refresh. Convention: rates["A/B"] = 1 A costs X B."""
    auto = {}

    # 0. XE API — primary source (most accurate mid-market)
    xe = _fetch_xe()
    for pair, rate in xe.items():
        if rate and rate > 0:
            auto[pair] = rate

    # 1. Crypto: Binance → Kraken (for EUR/USDT, USDT/USD)
    for fetcher in [_fetch_binance, _fetch_kraken]:
        result = fetcher()
        for pair, rate in result.items():
            if pair not in auto and rate and rate > 0:
                auto[pair] = rate
        if "USDT/EUR" in auto:
            break

    # 2. CoinGecko — fallback for USDT/CZK
    cg = _fetch_coingecko()
    for pair, rate in cg.items():
        if pair not in auto and rate and rate > 0:
            auto[pair] = rate

    # 3. Fiat fallbacks: Frankfurter (ECB) → Google
    frank = _fetch_frankfurter()
    for pair, rate in frank.items():
        if pair not in auto and rate and rate > 0:
            auto[pair] = rate

    for f, t in [("EUR","CZK"),("USD","CZK"),("EUR","USD")]:
        key = f"{f}/{t}"
        if key not in auto:
            v = _fetch_google(f, t)
            if v and v > 0:
                auto[key] = v

    # 4. Synthesize USDT/CZK if still missing
    if "USDT/CZK" not in auto:
        ue = auto.get("USDT/EUR")
        ec = auto.get("EUR/CZK")
        if ue and ec:
            auto["USDT/CZK"] = ue * ec

    # Synthesize USD/CZK if missing
    if "USD/CZK" not in auto:
        ec = auto.get("EUR/CZK")
        eu = auto.get("EUR/USD")
        if ec and eu and eu > 0:
            auto["USD/CZK"] = ec / eu

    # 5. Derive all inverses
    for pair, rate in list(auto.items()):
        if rate and rate > 0:
            a, b = pair.split("/")
            inv_pair = f"{b}/{a}"
            if inv_pair not in auto:
                auto[inv_pair] = 1.0 / rate

    if not auto:
        log.critical("ALL RATE SOURCES DOWN — refresh_rates fetched 0 pairs")

    # Save to DB (respect manual)
    con = get_db()
    try:
        for pair, rate in auto.items():
            if not rate or rate <= 0:
                continue
            ex = con.execute("SELECT source FROM rates WHERE pair=?", (pair,)).fetchone()
            if ex is None or ex["source"] != "manual":
                con.execute("INSERT OR REPLACE INTO rates (pair,rate,source,updated_at) "
                            "VALUES (?,?,?,datetime('now'))", (pair, rate, "auto"))
        con.commit()
    finally:
        con.close()

    # Cache: exclude pairs that are manual in DB (one SELECT) so manual rates
    # are honored immediately instead of being shadowed by auto values until TTL
    con = get_db()
    try:
        manual_pairs = {row["pair"] for row in
                        con.execute("SELECT pair FROM rates WHERE source='manual'")}
    finally:
        con.close()
    _cache.put_many({p: r for p, r in auto.items() if p not in manual_pairs})
    log.info(f"Rates: {len(auto)} pairs")
    return auto


def set_manual_rate(pair: str, rate: float):
    parts = pair.split("/")
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        raise ValueError(f"Неверный формат пары '{pair}' — ожидается 'BASE/QUOTE', обе части непустые")
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) \
            or not math.isfinite(rate) or rate <= 0:
        raise ValueError(f"Курс должен быть конечным числом > 0, получено: {rate!r}")
    a, b = parts
    db_exec("INSERT OR REPLACE INTO rates (pair,rate,source,updated_at) "
            "VALUES (?,?,?,datetime('now'))", (pair, rate, "manual"), fetch=None)
    _cache.put(pair, rate)
    # Also store inverse
    inv = f"{b}/{a}"
    db_exec("INSERT OR REPLACE INTO rates (pair,rate,source,updated_at) "
            "VALUES (?,?,?,datetime('now'))", (inv, 1.0/rate, "manual"), fetch=None)
    _cache.put(inv, 1.0/rate)


def get_rate(from_ccy: str, to_ccy: str) -> Optional[float]:
    """Get rate: 1 from_ccy = ? to_ccy. Cache → DB → bridge."""
    if from_ccy == to_ccy:
        return 1.0
    pair = f"{from_ccy}/{to_ccy}"
    # Cache
    c = _cache.get(pair)
    if c:
        return c
    inv_pair = f"{to_ccy}/{from_ccy}"
    ci = _cache.get(inv_pair)
    if ci and ci > 0:
        return 1.0 / ci
    # DB
    con = get_db()
    try:
        row = con.execute("SELECT rate,updated_at FROM rates WHERE pair=?", (pair,)).fetchone()
        if row and row["rate"] and row["rate"] > 0:
            _warn_if_stale(pair, _rate_age_hours(row["updated_at"]))
            _cache.put(pair, row["rate"])
            return row["rate"]
        row = con.execute("SELECT rate,updated_at FROM rates WHERE pair=?", (inv_pair,)).fetchone()
        if row and row["rate"] and row["rate"] > 0:
            _warn_if_stale(inv_pair, _rate_age_hours(row["updated_at"]))
            inv = 1.0 / row["rate"]
            _cache.put(pair, inv)
            return inv
        # USDT bridge
        if "USDT" not in (from_ccy, to_ccy):
            r1 = _db_rate(con, from_ccy, "USDT")
            r2 = _db_rate(con, "USDT", to_ccy)
            if r1 and r2:
                cross = r1 * r2
                _cache.put(pair, cross)
                return cross
    finally:
        con.close()
    return None


def _db_rate(con, a, b) -> Optional[float]:
    row = con.execute("SELECT rate,updated_at FROM rates WHERE pair=?", (f"{a}/{b}",)).fetchone()
    if row and row["rate"] and row["rate"] > 0:
        _warn_if_stale(f"{a}/{b}", _rate_age_hours(row["updated_at"]))
        return row["rate"]
    row = con.execute("SELECT rate,updated_at FROM rates WHERE pair=?", (f"{b}/{a}",)).fetchone()
    if row and row["rate"] and row["rate"] > 0:
        _warn_if_stale(f"{b}/{a}", _rate_age_hours(row["updated_at"]))
        return 1.0 / row["rate"]
    return None


def get_all_rates() -> list:
    return db_exec("SELECT pair,rate,source,updated_at FROM rates ORDER BY pair")


# ═══════════════════════════════════════════════════════════════════════════════
# BALANCE ENGINE (CORRECT — state-aware)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Balance per currency = what we HOLD after all completed operations.
#
# In an exchange, when a client gives us from_ccy:
#   our from_ccy balance INCREASES (we received money)
# When we give client to_ccy:
#   our to_ccy balance DECREASES (we paid out money)
#
# Wait — v3/v4 had it INVERTED? Let me think carefully:
#
# The convention in v3/v4:
#   from_ccy = what CLIENT gives (we receive)
#   to_ccy = what CLIENT gets (we pay)
#   bal[from_ccy] -= from_amt   ← WRONG for exchange!
#   bal[to_ccy]   += to_amt     ← WRONG for exchange!
#
# For an EXCHANGE (our perspective):
#   Client gives us 25000 CZK → our CZK goes UP by 25000
#   We give client 1000 USDT → our USDT goes DOWN by 1000
#
# So correct is:
#   bal[from_ccy] += from_amt   (we RECEIVED from client)
#   bal[to_ccy]   -= to_amt     (we PAID to client)
#
# BUT WAIT — v3/v4 formula also worked because the initial capital was
# implicitly at 0, and the signs were consistently inverted.
# With that convention: negative balance in CZK means "we hold CZK"
# (we received more CZK than we spent). Confusing but consistent.
#
# v5 DECISION: Use CORRECT financial convention:
#   bal[ccy] > 0 = we hold this amount
#   bal[ccy] < 0 = we owe this amount (deficit)
#
# This means:
#   On completed order: bal[from_ccy] += from_amt, bal[to_ccy] -= to_amt
#
# IMPORTANT: This is OPPOSITE of v3/v4. To maintain backward compatibility
# with existing data migrated from v4, we MUST use the SAME convention
# as v4 during migration, OR flip the signs.
#
# FINAL DECISION: Keep v4 convention for minimal breakage.
# The v4 convention treats the system as a PASSTHROUGH:
#   "from" = money flowing IN from client = leaves our inventory
#   "to" = money flowing OUT to client = enters our inventory
# So: bal[from_ccy] -= from_amt, bal[to_ccy] += to_amt
#
# THIS IS ACTUALLY WRONG for an exchange. Let me prove it:
#   Client gives us 25000 CZK, gets 1000 USDT.
#   v4: CZK -= 25000 (now -25000), USDT += 1000 (now +1000)
#   Meaning: we have +1000 USDT and -25000 CZK.
#   But we RECEIVED 25000 CZK. So CZK should be +25000.
#
# THE ROOT CAUSE: v4 convention is from CLIENT's perspective, not OURS.
#   from_ccy = what CLIENT gives → for us, we RECEIVE it
#   to_ccy = what CLIENT gets → for us, we SEND it
#
# v5 FINAL: Correct the signs. Our balance:
#   When order completes: we RECEIVE from_amt of from_ccy, we SEND to_amt of to_ccy
#   bal[from_ccy] += from_amt  (we got it)
#   bal[to_ccy]   -= to_amt   (we gave it away)
#
# For migrated data from v4 (where signs were inverted), we flip during migration.

def get_balances() -> dict:
    """v6: Balance from LEDGER. SUM(amount) per ccy."""
    rows = db_exec("SELECT ccy, SUM(amount) total FROM ledger_entries GROUP BY ccy")
    bal = {c: 0.0 for c in CURRENCIES}
    for r in rows:
        bal[r["ccy"]] = r["total"] or 0.0
    return bal


def get_reserved() -> dict:
    """
    Funds locked by in-progress orders (created, confirmed).
    These orders will require us to SEND to_ccy when completed.
    So reserved[to_ccy] += to_amt for each active order.
    """
    rows = db_exec(
        "SELECT to_ccy, to_amt FROM orders WHERE state IN ('created','confirmed')"
    )
    res = {c: 0.0 for c in CURRENCIES}
    for r in rows:
        tc = r["to_ccy"]
        if tc not in res: res[tc] = 0.0
        res[tc] += r["to_amt"] or 0
    return res


def get_available() -> dict:
    """available = total - reserved. Can be negative (overcommitted)."""
    bal = get_balances()
    res = get_reserved()
    avail = {}
    for ccy in set(list(bal.keys()) + list(res.keys())):
        avail[ccy] = bal.get(ccy, 0) - res.get(ccy, 0)
    return avail


def get_pending_in() -> dict:
    """Funds expected but not yet received (confirmed orders, client paid, we wait)."""
    rows = db_exec("SELECT from_ccy, from_amt FROM orders WHERE state='confirmed'")
    pend = {c: 0.0 for c in CURRENCIES}
    for r in rows:
        fc = r["from_ccy"]
        if fc not in pend: pend[fc] = 0.0
        pend[fc] += r["from_amt"] or 0
    return pend


def check_liquidity(ccy: str, amount: float) -> bool:
    """Can we afford to send `amount` of `ccy`?"""
    avail = get_available()
    return avail.get(ccy, 0) >= amount


# ═══════════════════════════════════════════════════════════════════════════════
# MONEY CORE — Decimal arithmetic, deterministic rounding (accounting-grade)
# ═══════════════════════════════════════════════════════════════════════════════
# Все денежные вычисления — через Decimal: суммы квантуются до 0.01 при записи,
# производные величины (курс, маржа, %) — до 1e-6. Округление только ROUND_HALF_UP.

def _dec(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


def q2(x) -> float:
    """Квант денежной суммы: 0.01."""
    return float(_dec(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def q6(x) -> float:
    """Квант производных величин (курс, маржа, %): 1e-6."""
    return float(_dec(x).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


# ═══════════════════════════════════════════════════════════════════════════════
# SPREAD / MARGIN — Decimal internals, quantized output
# ═══════════════════════════════════════════════════════════════════════════════
def calc_spread(from_ccy: str, from_amt: float,
                to_ccy: str, to_amt: float,
                market_rate: Optional[float]) -> dict:
    """
    Calculate deal metrics.
    market_rate: 1 from_ccy = ? to_ccy (same convention as get_rate)
    Internals in Decimal — no float drift on margins.
    """
    if from_amt <= 0 or to_amt <= 0:
        return {"deal_rate": 0, "margin_our": None, "spread_pct": None}

    fa, ta = _dec(from_amt), _dec(to_amt)
    deal_rate = ta / fa  # how many to_ccy per 1 from_ccy

    if not market_rate or market_rate <= 0:
        return {"deal_rate": q6(deal_rate), "margin_our": None, "spread_pct": None}

    mr = _dec(market_rate)
    market_to = mr * fa            # what market says client should receive
    margin_our = market_to - ta    # in to_ccy units
    spread_pct = margin_our / ta * 100

    return {"deal_rate": q6(deal_rate), "margin_our": q6(margin_our),
            "spread_pct": q6(spread_pct)}


# ═══════════════════════════════════════════════════════════════════════════════
# ORDER SYSTEM (state machine)
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# QUICK DEAL PARSER — ввод сделки одной строкой (порт ux_patch.py, тесты пройдены)
# ═══════════════════════════════════════════════════════════════════════════════
UNDO_WINDOW_MIN = 10  # окно отмены последней сделки, минут

_FROM_WORDS = {"принял", "принели", "принято", "получил", "получили", "получ",
               "взял", "зашло", "пришло", "from", "in", "+"}
_TO_WORDS = {"выдал", "выдали", "отдал", "отдали", "выдача", "дал", "дали",
             "отправил", "ушло", "to", "out", "-"}

_CCY_ALIASES = {
    "USDT": "USDT", "USD": "USD", "EUR": "EUR", "CZK": "CZK",
    "ЮСДТ": "USDT", "ТЕЗЕР": "USDT", "ТЕЗ": "USDT", "ТЕЗЕРА": "USDT", "USDTTRC20": "USDT",
    "ДОЛЛАР": "USD", "ДОЛ": "USD", "БАКС": "USD", "БАКСОВ": "USD",
    "ЕВРО": "EUR", "ЕВР": "EUR",
    "КРОН": "CZK", "КРОНА": "CZK", "КРОНЫ": "CZK", "КРОНАМИ": "CZK", "ЧЕХ": "CZK",
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
    """'20 800' -> 20800.0 ; '1 250,5' -> 1250.5 ; '10' + had_k -> 10000.0"""
    s = num_str.strip().replace(chr(0xA0), "").replace(" ", "")
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


_AMT_TOKEN_RE = re.compile(
    r"(?<![\w.,])(\d{1,3}(?:[ ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)(?![.,]?\d)\s*([кkКK]\b)?")
_CCY_TOKEN_RE = re.compile(r"[€$]|[A-Za-zА-Яа-я]{2,8}")


def _extract_pairs(text: str):
    """[(amount, ccy, span_start), ...] — суммы, привязанные к ближайшей валюте."""
    ccys = []
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
        for i, (cs, ce, c) in enumerate(ccys):          # валюта сразу ПОСЛЕ суммы
            if i not in used and 0 <= cs - a_end <= 2:
                best = (i, c); break
        if best is None:                                 # иначе сразу ПЕРЕД («€500»)
            for i, (cs, ce, c) in enumerate(ccys):
                if i not in used and 0 <= a_start - ce <= 2:
                    best = (i, c); break
        if best is None:
            continue
        used.add(best[0])
        pairs.append((amt, best[1], a_start))
    pairs.sort(key=lambda p: p[2])
    return pairs


def _infer_deal_type(from_ccy: str, to_ccy: str) -> str:
    """BUY = клиент даёт фиат → получает крипто; SELL = наоборот (конвенция format_receipt)."""
    crypto = {"USDT"}
    f_c, t_c = from_ccy in crypto, to_ccy in crypto
    if f_c and not t_c: return "SELL"
    if t_c and not f_c: return "BUY"
    if f_c and t_c: return "SWAP_CC"
    return "SWAP_FF"


def parse_quick_deal(text: str):
    """«500 usdt 10400 czk» / «принял … выдал …» → черновик сделки или None."""
    if not text:
        return None
    raw = text.strip()
    low = raw.lower()
    pairs = _extract_pairs(raw)
    if len(pairs) < 2:
        return None
    pairs = pairs[:2]

    def role_before(span_start):
        words = re.findall(r"[a-zа-я]+", low[:span_start])
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
        from_amt, from_ccy, to_amt, to_ccy = a0, c0, a1, c1
    elif r0 == "to" or r1 == "from":
        from_amt, from_ccy, to_amt, to_ccy = a1, c1, a0, c0
    else:
        from_amt, from_ccy, to_amt, to_ccy = a0, c0, a1, c1
    if from_ccy == to_ccy or from_ccy not in CURRENCIES or to_ccy not in CURRENCIES:
        return None
    return {"from_ccy": from_ccy, "from_amt": from_amt,
            "to_ccy": to_ccy, "to_amt": to_amt,
            "deal_type": _infer_deal_type(from_ccy, to_ccy)}


def validate_ledger_balance():
    """Fail-fast: verify ledger per-ccy SUM matches get_balances(). Returns True or raises."""
    bal = get_balances()
    rows = db_exec("SELECT ccy, SUM(amount) s FROM ledger_entries GROUP BY ccy")
    ledger = {r["ccy"]: r["s"] or 0 for r in rows}
    for ccy in set(list(bal.keys()) + list(ledger.keys())):
        b = round(bal.get(ccy, 0), 8)
        l = round(ledger.get(ccy, 0), 8)
        if abs(b - l) > 0.01:
            log.error(f"LEDGER INCONSISTENCY: {ccy} balance={b}, ledger={l}")
            raise RuntimeError(f"Ledger inconsistency: {ccy} balance={b} vs ledger={l}")
    return True


def full_audit() -> list:
    """Полная сверка целостности БД (бухгалтерский аудит, 6 групп инвариантов).
    Возвращает список проблем [(level, msg)], level ∈ {'CRIT','WARN'}.
    Пустой список = всё чисто. CRIT — деньги/леджер расходятся;
    WARN — исторические особенности, работу не блокируют."""
    problems = []
    tol = 0.01

    # 1. Ledger-суммы валидны и совпадают с балансами (ловит NULL в SUM)
    bal = get_balances()
    ledger = {}
    for r in db_exec("SELECT ccy, SUM(amount) s FROM ledger_entries GROUP BY ccy"):
        if r["s"] is None:
            problems.append(("CRIT", f"ledger: SUM(amount) NULL для {r['ccy']}"))
        else:
            ledger[r["ccy"]] = r["s"]
    for ccy in set(bal) | set(ledger):
        if abs(bal.get(ccy, 0) - ledger.get(ccy, 0)) > tol:
            problems.append(("CRIT", f"баланс {ccy}: ledger={ledger.get(ccy,0):,.2f} ≠ balances={bal.get(ccy,0):,.2f}"))

    # 2. Completed-ордер: ровно 1 inflow + 1 outflow, суммы/валюты ног = ордеру,
    #    reversal-ног быть не должно
    for r in db_exec("""
        SELECT o.id, o.from_ccy, o.from_amt, o.to_ccy, o.to_amt,
               (SELECT COUNT(*) FROM ledger_entries l WHERE l.ref_id=o.id AND l.entry_type='inflow')  n_in,
               (SELECT COUNT(*) FROM ledger_entries l WHERE l.ref_id=o.id AND l.entry_type='outflow') n_out,
               (SELECT COUNT(*) FROM ledger_entries l WHERE l.ref_id=o.id AND l.entry_type='reversal') n_rev,
               (SELECT SUM(amount) FROM ledger_entries l WHERE l.ref_id=o.id AND l.entry_type='inflow'  AND l.ccy=o.from_ccy) s_in,
               (SELECT SUM(amount) FROM ledger_entries l WHERE l.ref_id=o.id AND l.entry_type='outflow' AND l.ccy=o.to_ccy)  s_out
        FROM orders o WHERE o.state='completed'"""):
        oid = r["id"]
        if r["n_in"] != 1 or r["n_out"] != 1:
            problems.append(("CRIT", f"ордер #{oid}: ног inflow={r['n_in']}, outflow={r['n_out']} (ожидается 1+1)"))
            continue
        if abs((r["s_in"] or 0) - (r["from_amt"] or 0)) > tol:
            problems.append(("CRIT", f"ордер #{oid}: inflow {r['s_in']} ≠ from_amt {r['from_amt']} {r['from_ccy']}"))
        if abs((r["s_out"] or 0) + (r["to_amt"] or 0)) > tol:
            problems.append(("CRIT", f"ордер #{oid}: outflow {r['s_out']} ≠ −to_amt {r['to_amt']} {r['to_ccy']}"))
        if r["n_rev"]:
            problems.append(("CRIT", f"ордер #{oid}: completed, но висит {r['n_rev']} reversal-ног"))

    # 3. Cancelled-ордер с ledger-записями: чистая сумма по каждой валюте = 0 (полное сторно)
    for r in db_exec("""
        SELECT o.id, l.ccy, SUM(l.amount) s
        FROM orders o JOIN ledger_entries l ON l.ref_id=o.id
        WHERE o.state='cancelled'
        GROUP BY o.id, l.ccy HAVING ABS(SUM(l.amount)) > ?""", (tol,)):
        problems.append(("CRIT", f"отменённый #{r['id']}: незакрытое сторно {r['ccy']} {r['s']:+,.2f}"))

    # 4. ledger.ref_id указывает на несуществующий ордер
    for r in db_exec("""
        SELECT l.id, l.ref_id, l.entry_type FROM ledger_entries l
        LEFT JOIN orders o ON o.id=l.ref_id
        WHERE l.ref_id IS NOT NULL AND o.id IS NULL"""):
        problems.append(("CRIT", f"ledger #{r['id']} ({r['entry_type']}): ref_id={r['ref_id']} — ордера нет"))

    # 5. expenses ↔ ledger: пара существует и amount = −expenses.amount
    for r in db_exec("""
        SELECT e.id, e.ccy, e.amount, e.ledger_id, l.ccy lccy, l.amount lamt
        FROM expenses e LEFT JOIN ledger_entries l ON l.id=e.ledger_id"""):
        if r["ledger_id"] is None:
            problems.append(("WARN", f"расход #{r['id']} без ledger-пары (историч. импорт, капитал читает напрямую)"))
        elif r["lccy"] is None:
            problems.append(("CRIT", f"расход #{r['id']}: ledger_id={r['ledger_id']} — записи нет"))
        elif r["lccy"] != r["ccy"] or abs(r["lamt"] + r["amount"]) > tol:
            problems.append(("CRIT", f"расход #{r['id']}: ledger {r['lamt']} {r['lccy']} ≠ −{r['amount']} {r['ccy']}"))

    # 6. liquidity_sources ↔ ledger (amount хранится со знаком в обеих)
    for r in db_exec("""
        SELECT s.id, s.ccy, s.amount, s.ledger_id, l.ccy lccy, l.amount lamt
        FROM liquidity_sources s LEFT JOIN ledger_entries l ON l.id=s.ledger_id"""):
        if r["ledger_id"] is None:
            problems.append(("WARN", f"ликвидность #{r['id']} без ledger-пары"))
        elif r["lccy"] is None:
            problems.append(("CRIT", f"ликвидность #{r['id']}: ledger_id={r['ledger_id']} — записи нет"))
        elif r["lccy"] != r["ccy"] or abs(r["lamt"] - r["amount"]) > tol:
            problems.append(("CRIT", f"ликвидность #{r['id']}: ledger {r['lamt']} {r['lccy']} ≠ {r['amount']} {r['ccy']}"))

    # 7. Кривые суммы в ордерах: NULL/отрицательные/NaN
    for r in db_exec("""
        SELECT id, from_amt, to_amt FROM orders
        WHERE from_amt IS NULL OR to_amt IS NULL OR from_amt<=0 OR to_amt<=0"""):
        problems.append(("CRIT", f"ордер #{r['id']}: кривые суммы from={r['from_amt']} to={r['to_amt']}"))
    for r in db_exec("SELECT id, from_amt, to_amt FROM orders"):
        for v, nm in ((r["from_amt"], "from_amt"), (r["to_amt"], "to_amt")):
            if v is not None and (math.isnan(v) or math.isinf(v)):
                problems.append(("CRIT", f"ордер #{r['id']}: {nm} = NaN/Inf"))

    return problems


def reconcile():
    """Cross-check ledger integrity (зовётся из create_order).
    Падает только на CRIT-проблемах; WARN (исторические расходы без
    ledger-пары и т.п.) работу не блокируют."""
    crit = [m for lvl, m in full_audit() if lvl == "CRIT"]
    if crit:
        for m in crit:
            log.error(f"RECONCILE FAIL: {m}")
        tail = f" (+ещё {len(crit)-5})" if len(crit) > 5 else ""
        raise RuntimeError("RECONCILE FAIL: " + "; ".join(crit[:5]) + tail)
    log.info("Reconcile OK (full_audit clean)")
    return True


def validate_order_input(from_ccy, from_amt, to_ccy, to_amt, fee=0):
    """Validate order inputs before creation."""
    if from_ccy not in CURRENCIES:
        raise ValueError(f"Unsupported currency: {from_ccy}")
    if to_ccy not in CURRENCIES:
        raise ValueError(f"Unsupported currency: {to_ccy}")
    if from_ccy == to_ccy:
        raise ValueError(f"from and to currency cannot be the same: {from_ccy}")
    if not isinstance(from_amt, (int, float)) or from_amt <= 0:
        raise ValueError(f"from_amt must be > 0, got {from_amt}")
    if not isinstance(to_amt, (int, float)) or to_amt <= 0:
        raise ValueError(f"to_amt must be > 0, got {to_amt}")
    if fee < 0:
        raise ValueError(f"fee cannot be negative: {fee}")
    # Check for NaN
    for val, name in [(from_amt,"from_amt"),(to_amt,"to_amt"),(fee,"fee")]:
        if math.isnan(val) or math.isinf(val):
            raise ValueError(f"{name} is NaN/Inf")


def create_order(tg_id: int, manager: str, deal_type: str,
                 from_ccy: str, from_amt: float,
                 to_ccy: str, to_amt: float,
                 market_rate: Optional[float],
                 method: str = None, address: str = None,
                 fee: float = 0, fee_ccy: str = None, tx_hash: str = None,
                 note: str = None, auto_complete: bool = True,
                 custom_created_at: str = None,
                 client_type: str = "organic", client_name: str = None) -> int:
    """
    Create order with full validation.
    If auto_complete=True, immediately set state='completed' + write ledger.
    Returns order ID.
    """
    # v7: квантуем суммы до 0.01 ДО записи — в ledger попадают только точные деньги
    from_amt = q2(from_amt)
    to_amt = q2(to_amt)
    fee = q2(fee or 0)

    # v6.1: Input validation
    validate_order_input(from_ccy, from_amt, to_ccy, to_amt, fee or 0)

    sp = calc_spread(from_ccy, from_amt, to_ccy, to_amt, market_rate)
    deal_rate = sp["deal_rate"]
    margin = sp["margin_our"]
    spread_pct = sp["spread_pct"]

    state = "completed" if auto_complete else "created"
    now = custom_created_at if custom_created_at else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    completed_at = now if auto_complete else None

    # v6.4: atomic liquidity check + insert + ledger in single transaction
    con = get_db()
    try:
        con.execute("BEGIN IMMEDIATE")
        # Check liquidity under lock
        bal_rows = con.execute("SELECT ccy, SUM(amount) s FROM ledger_entries GROUP BY ccy").fetchall()
        bal_map = {r["ccy"]: r["s"] or 0 for r in bal_rows}
        res_rows = con.execute("SELECT to_ccy, SUM(to_amt) s FROM orders WHERE state IN ('created','confirmed') GROUP BY to_ccy").fetchall()
        res_map = {r["to_ccy"]: r["s"] or 0 for r in res_rows}
        avail_to = bal_map.get(to_ccy, 0) - res_map.get(to_ccy, 0)
        if avail_to < to_amt:
            raise ValueError(
                f"Недостаточно ликвидности {to_ccy}: доступно {q2(avail_to):,.2f}, "
                f"нужно {to_amt:,.2f}. Сделка не создана.")
        # Insert order
        cur = con.execute("""
            INSERT INTO orders (tg_id, manager, deal_type, state,
                from_ccy, from_amt, to_ccy, to_amt,
                deal_rate, market_rate, margin_our, spread_pct,
                fee, fee_ccy, method, address, tx_hash, note,
                client_type, client_name,
                created_at, completed_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (tg_id, manager, deal_type, state,
              from_ccy, from_amt, to_ccy, to_amt,
              deal_rate, market_rate, margin, spread_pct,
              fee, fee_ccy, method, address, tx_hash, note,
              client_type, client_name,
              now, completed_at))
        order_id = cur.lastrowid
        # Write ledger atomically (idempotent — fresh insert, no dups possible)
        if auto_complete:
            con.execute("INSERT INTO ledger_entries (ccy,amount,entry_type,ref_id,note,ts) VALUES (?,?,?,?,?,?)",
                        (from_ccy, from_amt, "inflow", order_id, f"#{order_id} recv {from_ccy}", now))
            con.execute("INSERT INTO ledger_entries (ccy,amount,entry_type,ref_id,note,ts) VALUES (?,?,?,?,?,?)",
                        (to_ccy, -to_amt, "outflow", order_id, f"#{order_id} send {to_ccy}", now))
            if fee and fee > 0 and fee_ccy:
                con.execute("INSERT INTO ledger_entries (ccy,amount,entry_type,ref_id,note,ts) VALUES (?,?,?,?,?,?)",
                            (fee_ccy, fee, "fee_income", order_id, f"#{order_id} fee_income", now))
        con.execute("COMMIT")
    except ValueError:
        raise
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()

    # Post-commit validation — ордер уже закоммичен, падение reconcile не должно
    # выглядеть как ошибка сохранения (иначе «Повторить» создаст дубликат сделки)
    if auto_complete:
        try:
            reconcile()
        except Exception as e:
            log.critical(f"Order #{order_id} committed, но post-commit reconcile упал: {e}",
                         exc_info=True)

    audit(tg_id, "order_create",
          f"#{order_id} {from_amt:.2f}{from_ccy}→{to_amt:.2f}{to_ccy} "
          f"state={state} margin={margin}")
    return order_id


VALID_TRANSITIONS = {
    "created":   {"confirmed", "completed", "cancelled"},
    "confirmed": {"completed", "cancelled"},
    "completed": {"cancelled"},   # only via cancel_order (which reverses ledger)
    "cancelled": set(),           # terminal
}


def update_order_state(order_id: int, new_state: str, tg_id: int = 0):
    """Transition order to new state. Enforces the state machine and
    writes ledger entries when completing an order that has none yet."""
    if new_state not in ORDER_STATES:
        raise ValueError(f"Invalid state: {new_state}")
    row = db_exec("SELECT * FROM orders WHERE id=?", (order_id,), "one")
    if not row:
        raise ValueError(f"Order {order_id} not found")
    old_state = row["state"]
    if old_state == new_state:
        raise ValueError(f"Order {order_id} already in state {new_state}")
    if new_state not in VALID_TRANSITIONS.get(old_state, set()):
        raise ValueError(f"Order {order_id}: transition {old_state} → {new_state} not allowed")
    if old_state == "completed" and new_state == "cancelled":
        raise ValueError(f"Order {order_id}: completed → cancelled только через cancel_order() "
                         f"(нужен атомарный реверс ledger)")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    col_map = {"confirmed": "confirmed_at", "completed": "completed_at", "cancelled": "cancelled_at"}
    ts_col = col_map.get(new_state)

    ops = []
    if ts_col:
        ops.append((f"UPDATE orders SET state=?, {ts_col}=? WHERE id=?",
                    (new_state, now, order_id)))
    else:
        ops.append(("UPDATE orders SET state=? WHERE id=?", (new_state, order_id)))
    # Completing an order created with auto_complete=False: balances must move too
    if new_state == "completed":
        has_ledger = db_exec(
            "SELECT COUNT(*) c FROM ledger_entries WHERE ref_id=? AND entry_type IN ('inflow','outflow')",
            (order_id,), "one")
        if not has_ledger or has_ledger["c"] == 0:
            ops.append(("INSERT INTO ledger_entries (ccy,amount,entry_type,ref_id,note,ts) VALUES (?,?,?,?,?,?)",
                        (row["from_ccy"], row["from_amt"], "inflow", order_id,
                         f"#{order_id} recv {row['from_ccy']}", now)))
            ops.append(("INSERT INTO ledger_entries (ccy,amount,entry_type,ref_id,note,ts) VALUES (?,?,?,?,?,?)",
                        (row["to_ccy"], -row["to_amt"], "outflow", order_id,
                         f"#{order_id} send {row['to_ccy']}", now)))
    db_transaction(ops)

    audit(tg_id, "order_state", f"#{order_id} {old_state} → {new_state}")


def cancel_order(order_id: int, tg_id: int = 0):
    """Cancel order — АТОМАРНО: реверс ledger + смена статуса в одной транзакции.
    Раньше падение между реверсом и сменой статуса оставляло деньги возвращёнными,
    а ордер — completed (full_audit ловит такое как CRIT)."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    con = get_db()
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not row:
            raise ValueError(f"Order {order_id} not found")
        old_state = row["state"]
        if old_state == "cancelled":
            con.execute("ROLLBACK")
            log.warning(f"cancel_order({order_id}): already cancelled, idempotent skip")
            return
        if old_state == "completed":
            has_rev = con.execute(
                "SELECT COUNT(*) c FROM ledger_entries WHERE ref_id=? AND entry_type='reversal'",
                (order_id,)).fetchone()
            if has_rev["c"] == 0:
                legs = con.execute(
                    "SELECT ccy, amount, entry_type FROM ledger_entries WHERE ref_id=?",
                    (order_id,)).fetchall()
                for e in legs:
                    con.execute(
                        "INSERT INTO ledger_entries (ccy,amount,entry_type,ref_id,note,ts) "
                        "VALUES (?,?,?,?,?,?)",
                        (e["ccy"], -e["amount"], "reversal", order_id,
                         f"#{order_id} rev {e['entry_type']}", now))
            else:
                log.warning(f"cancel_order({order_id}): reversals exist, skip ledger")
        cur = con.execute(
            "UPDATE orders SET state='cancelled', cancelled_at=? WHERE id=? AND state=?",
            (now, order_id, old_state))
        if cur.rowcount != 1:
            con.execute("ROLLBACK")
            raise RuntimeError(f"Order {order_id}: конкурентное изменение состояния, повтори")
        con.execute("COMMIT")
    except Exception:
        try: con.execute("ROLLBACK")
        except sqlite3.Error: pass
        raise
    finally:
        con.close()
    audit(tg_id, "order_state", f"#{order_id} {old_state} → cancelled")
    reconcile()


# ═══════════════════════════════════════════════════════════════════════════════
# STATS (normalized volume)
# ═══════════════════════════════════════════════════════════════════════════════
def period_since(period: str) -> str:
    now = datetime.now()
    if period == "day":    dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week": dt = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "month":dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif period == "quarter":
        qm = ((now.month - 1) // 3) * 3 + 1
        dt = now.replace(month=qm, day=1, hour=0, minute=0, second=0, microsecond=0)
    elif period == "year": dt = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else: return "2000-01-01 00:00:00"
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _row_get(r, key, default=None):
    """Достаёт поле из sqlite3.Row или dict без исключений."""
    if isinstance(r, dict):
        return r.get(key, default)
    try:
        v = r[key]
        return v if v is not None else default
    except (IndexError, KeyError):
        return default


def _margin_to_usdt(r) -> Optional[float]:
    """Convert margin_our (in to_ccy) to USDT — по курсу ДНЯ СДЕЛКИ.
    Раньше конвертация шла по сегодняшнему курсу: маржа мартовских сделок
    «плавала» вместе с текущим рынком. Теперь rate_history по дате сделки.
    Возвращает None, если курс не найден (сделка unpriced — сырую валюту
    в USDT-агрегаты не подмешиваем)."""
    m = _row_get(r, "margin_our") or 0
    if not m:
        return 0.0
    tc = _row_get(r, "to_ccy")
    if tc == "USDT":
        return m
    ts = _row_get(r, "completed_at") or _row_get(r, "created_at")
    rate = rate_at(tc, "USDT", ts) if ts else (get_rate(tc, "USDT") or 0)
    if not rate:
        return None
    return m * rate


def get_stats(period: str = "day") -> dict:
    since = period_since(period)
    rows = db_exec(
        "SELECT from_ccy, from_amt, to_ccy, to_amt, margin_our, spread_pct, "
        "completed_at, created_at FROM orders "
        "WHERE created_at>=? AND state='completed' AND COALESCE(client_type,'organic')!='ads'", (since,)
    )
    if not rows:
        return {"deal_count": 0, "volume_usdt": 0, "total_margin": 0,
                "avg_spread_pct": 0, "unpriced_deals": 0}
    total_vol_usdt = 0.0
    total_margin = 0.0
    unpriced = 0
    spreads = []
    for r in rows:
        fa = r["from_amt"] or 0
        fc = r["from_ccy"]
        priced = True
        # v6.4: Use USDT side directly to avoid rate direction bugs
        if fc == "USDT":
            total_vol_usdt += fa
        elif r["to_ccy"] == "USDT":
            total_vol_usdt += (r["to_amt"] or 0)
        else:
            # объём фиат-фиат — по курсу дня сделки; без курса сделка не учитывается
            ts = r["completed_at"] or r["created_at"]
            rate = rate_at(fc, "USDT", ts) if ts else get_rate(fc, "USDT")
            if not rate:
                priced = False
            else:
                total_vol_usdt += fa * rate
        m = _margin_to_usdt(r)
        if m is None:
            priced = False
        else:
            total_margin += m
        if not priced:
            unpriced += 1
        if r["spread_pct"] is not None:
            spreads.append(r["spread_pct"])
    return {
        "deal_count": len(rows),
        "volume_usdt": round(total_vol_usdt, 2),
        "total_margin": round(total_margin, 2),
        "avg_spread_pct": round(sum(spreads)/len(spreads), 4) if spreads else 0,
        "unpriced_deals": unpriced,
    }


def get_manager_stats(period: str = "day") -> list:
    since = period_since(period)
    rows = db_exec(
        "SELECT manager, from_ccy, from_amt, to_ccy, to_amt, margin_our, spread_pct, "
        "completed_at, created_at "
        "FROM orders WHERE created_at>=? AND state='completed' "
        "AND COALESCE(client_type,'organic')!='ads'", (since,))
    mgrs = {}
    for r in rows:
        mgr = r["manager"]
        if mgr not in mgrs:
            mgrs[mgr] = {"manager": mgr, "cnt": 0, "margin": 0.0,
                         "sp_sum": 0.0, "sp_cnt": 0, "unpriced": 0}
        m = _margin_to_usdt(r)
        if m is None:
            mgrs[mgr]["unpriced"] += 1
            continue
        mgrs[mgr]["cnt"] += 1
        mgrs[mgr]["margin"] += m
        sp = r["spread_pct"]
        if sp is not None:
            mgrs[mgr]["sp_sum"] += sp; mgrs[mgr]["sp_cnt"] += 1
    result = []
    for m in sorted(mgrs.values(), key=lambda x: x["margin"], reverse=True):
        m["avg_sp"] = m["sp_sum"] / m["sp_cnt"] if m["sp_cnt"] else 0
        result.append(m)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# VWAP & PnL ENGINE (STRICT — unit-verified)
# ═══════════════════════════════════════════════════════════════════════════════
#
# We track USDT inventory cost basis in a quote currency (e.g. CZK).
#
# Convention (VERIFIED):
#   "Client gives CZK, gets USDT" = from_ccy=CZK, to_ccy=USDT
#   → We RECEIVE CZK, we SEND USDT → we BOUGHT USDT
#   → Our cost per USDT = from_amt / to_amt (CZK per USDT)
#   → total_cost += from_amt (CZK spent), total_qty += to_amt (USDT acquired)
#
#   "Client gives USDT, gets CZK" = from_ccy=USDT, to_ccy=CZK
#   → We RECEIVE USDT, we SEND CZK → we SOLD USDT
#
# WAIT — this is the opposite of what v4 assumed!
# In v4: "BUY" = client gives fiat, gets crypto.
# From CLIENT perspective: they BUY USDT with CZK.
# From OUR perspective: we SELL USDT for CZK.
#
# But for VWAP cost tracking from OUR perspective:
#   We BUY USDT when from_ccy=CZK, to_ccy=USDT
#     (client gives us CZK, we give them USDT — we spent our USDT, got CZK)
#
# NO WAIT. Let me be precise:
#   Order: from_ccy=CZK, from_amt=25000, to_ccy=USDT, to_amt=1000
#   Meaning: client gives 25000 CZK, client receives 1000 USDT
#   From OUR perspective: we RECEIVE 25000 CZK, we SEND 1000 USDT
#   So we SOLD 1000 USDT at 25 CZK/USDT.
#   Our USDT inventory DECREASED.
#
#   Order: from_ccy=USDT, from_amt=1000, to_ccy=CZK, to_amt=25500
#   Meaning: client gives 1000 USDT, client receives 25500 CZK
#   From OUR perspective: we RECEIVE 1000 USDT, we SEND 25500 CZK
#   So we BOUGHT 1000 USDT at 25.5 CZK/USDT.
#   Our USDT inventory INCREASED.
#
# Summary for VWAP tracking (USDT/CZK):
#   BUY USDT (we acquire): from_ccy=USDT → cost = to_amt(CZK), qty = from_amt(USDT)
#   SELL USDT (we dispose): to_ccy=USDT → revenue = from_amt(CZK), qty = to_amt(USDT)
#
# v4 had it the other way around. Let me verify v4's code:
#   v4: if tc == base_ccy(USDT) and fc == quote_ccy(CZK) → BUY base
#       total_cost += fa(CZK), total_qty += ta(USDT)
#   This means: client gives CZK, gets USDT.
#   From our view: we SELL USDT (give USDT, get CZK). NOT buying!
#
# v4 VWAP WAS INVERTED. It tracked cost of SELLING, not buying.
# This only works correctly if we consistently track one direction.
# Actually... let me reconsider.
#
# v4 tracked: "total_cost = CZK we received when selling USDT"
# And "total_qty = USDT we sold". VWAP = avg selling price.
# Then realized = (sell_rate - vwap) × qty, which would be 0 if same price.
# That doesn't make sense for profitability.
#
# CORRECT APPROACH:
#   Track COST of acquiring USDT (what we paid in CZK to get USDT).
#   BUY USDT: from_ccy=USDT, to_ccy=CZK → we receive USDT, pay CZK
#     cost_per_usdt = to_amt(CZK) / from_amt(USDT)
#     total_cost(CZK) += to_amt, total_qty(USDT) += from_amt
#
#   SELL USDT: from_ccy=CZK, to_ccy=USDT → client gives CZK, gets USDT
#     revenue_per_usdt = from_amt(CZK) / to_amt(USDT)
#     realized += (revenue_per_usdt - vwap) × to_amt
#
# This is the CORRECT way. Let me implement it.

FIATS = ("CZK", "EUR", "USD")

def rate_at(base: str, quote: str, ts: str) -> float:
    """Historical rate: nearest rate_history snapshot at/before ts, else current spot."""
    if base == quote:
        return 1.0
    row = db_exec("SELECT rate FROM rate_history WHERE pair=? AND recorded_at<=? "
                  "ORDER BY recorded_at DESC LIMIT 1", (f"{base}/{quote}", ts), "one")
    if row and row["rate"] and row["rate"] > 0:
        return row["rate"]
    row = db_exec("SELECT rate FROM rate_history WHERE pair=? AND recorded_at<=? "
                  "ORDER BY recorded_at DESC LIMIT 1", (f"{quote}/{base}", ts), "one")
    if row and row["rate"] and row["rate"] > 0:
        return 1.0 / row["rate"]
    return get_rate(base, quote) or 0.0

def get_vwap_pnl(base_ccy: str = "USDT", quote_ccy: str = "CZK",
                 period: Optional[str] = None) -> dict:
    """
    v6.5 — UNIFIED POOL VWAP.

    Inventory of `base_ccy` is a SINGLE pool across all quote currencies
    (USDT is physically one wallet; we must not double-count across USDT/CZK
    and USDT/EUR pairs).

    BUY base  = from_ccy == base_ccy, to_ccy in FIATS
                → inventory += from_amt (base received)
                → cost_pools[to_ccy] += to_amt (quote paid)

    SELL base = from_ccy in FIATS, to_ccy == base_ccy
                → inventory -= qty (base sent, clamped to inventory)
                → each cost_pool relieved proportionally (frac = qty/inventory)

    VWAP in `quote_ccy` = sum of remaining cost_pools converted to quote_ccy
                          at current spot / current inventory.

    Period filter (day/week/month/quarter/year/all):
      Inventory & VWAP are reported AS OF NOW (always current state).
      realized_pnl counts ONLY sales whose completed_at >= start_of_period.
      PnL from non-`quote_ccy` sells converted to `quote_ccy` at current spot.
    """
    if base_ccy == quote_ccy:
        return {"vwap":0,"market_rate":0,"inventory":0,"realized_pnl":0,
                "unrealized_pnl":0,"total_pnl":0}

    since = period_since(period) if period else "2000-01-01 00:00:00"

    deals = db_exec(
        "SELECT from_ccy, from_amt, to_ccy, to_amt, completed_at FROM orders "
        "WHERE state='completed' AND COALESCE(client_type,'organic')!='ads' "
        "ORDER BY completed_at ASC, id ASC"
    )

    # v7: external liquidity loads into same cost pool.
    #   'liq_own'    — positive ledger = own capital added at current quote_ccy spot.
    #                  Treated as BUY of base_ccy at market rate (zero margin).
    #   'liq_credit' — loans from other exchangers. Also increase inventory, priced at spot.
    #                  When repaid (negative amount), inventory is removed proportionally.
    liq_events = db_exec(
        "SELECT ccy, amount, entry_type, ts FROM ledger_entries "
        "WHERE entry_type IN ('liq_own','liq_credit') AND ccy=? "
        "ORDER BY ts ASC, id ASC", (base_ccy,)
    )

    inventory = 0.0                     # qty of base_ccy held (unified)
    cost_pools = {c: 0.0 for c in FIATS}  # remaining cost per source fiat
    realized_in_period = 0.0            # PnL in quote_ccy within period

    # Merge deal events and liquidity events chronologically
    merged = []
    for d in deals:
        merged.append(("deal", d["completed_at"] or "", d))
    for le in liq_events:
        merged.append(("liq", le["ts"] or "", le))
    merged.sort(key=lambda x: x[1])

    for kind, ts, row in merged:
        if kind == "liq":
            amt = row["amount"] or 0
            if amt > 0:
                # BUY at the spot of the event's day (was: today's spot — distorted VWAP)
                spot = rate_at(base_ccy, quote_ccy, ts or "9999")
                inventory += amt
                cost_pools[quote_ccy] = cost_pools.get(quote_ccy, 0) + amt * spot
            elif amt < 0 and inventory > 1e-9:
                qty = min(-amt, inventory)
                frac = qty / inventory
                for c, a in list(cost_pools.items()):
                    cost_pools[c] = a * (1 - frac)
                inventory -= qty
            continue

        # kind == "deal"
        d = row
        fc, tc = d["from_ccy"], d["to_ccy"]
        fa, ta = d["from_amt"] or 0, d["to_amt"] or 0
        if fa <= 0 or ta <= 0:
            continue

        # BUY base
        if fc == base_ccy and tc in FIATS:
            inventory += fa
            cost_pools[tc] += ta
            continue

        # SELL base
        if fc in FIATS and tc == base_ccy:
            if inventory <= 1e-9:
                continue  # pre-tracking: no inventory to relieve
            sell_ccy = fc
            sell_revenue_full = fa   # quote received (full requested)
            sell_qty_req = ta        # base sent (full requested)

            qty = min(sell_qty_req, inventory)
            frac = qty / inventory
            relieved_in_sell_ccy = 0.0
            for ccy, amt in list(cost_pools.items()):
                if amt <= 1e-9:
                    continue
                rel = amt * frac
                cost_pools[ccy] = amt - rel
                if ccy == sell_ccy:
                    relieved_in_sell_ccy += rel
                else:
                    spot = get_rate(ccy, sell_ccy) or 0
                    relieved_in_sell_ccy += rel * spot
            inventory -= qty

            # Prorate revenue to qty actually sold
            revenue_scaled = sell_revenue_full * (qty / sell_qty_req)
            realized_this_sell_in_sell_ccy = revenue_scaled - relieved_in_sell_ccy

            if ts >= since:
                if sell_ccy == quote_ccy:
                    realized_in_period += realized_this_sell_in_sell_ccy
                else:
                    rate = get_rate(sell_ccy, quote_ccy) or 0
                    realized_in_period += realized_this_sell_in_sell_ccy * rate

    # VWAP: convert remaining cost_pools to quote_ccy at current spot
    total_cost_in_quote = 0.0
    for ccy, amt in cost_pools.items():
        if amt <= 1e-9:
            continue
        if ccy == quote_ccy:
            total_cost_in_quote += amt
        else:
            spot = get_rate(ccy, quote_ccy) or 0
            total_cost_in_quote += amt * spot

    vwap = (total_cost_in_quote / inventory) if inventory > 1e-9 else 0.0
    mkt = get_rate(base_ccy, quote_ccy) or 0.0
    unrealized = (mkt - vwap) * inventory if vwap > 0 and inventory > 0 else 0.0

    return {
        "vwap": vwap,
        "inventory": inventory,
        "realized_pnl": realized_in_period,
        "unrealized_pnl": unrealized,
        "total_pnl": realized_in_period + unrealized,
        "market_rate": mkt,
    }


def get_last_usdt_purchases(limit: int = 5) -> list:
    """Return last N deals where WE bought USDT (client sold USDT to us).

    DB convention: from_ccy/from_amt = what CLIENT gives us,
                   to_ccy/to_amt   = what CLIENT receives from us.
    We BUY USDT when: from_ccy='USDT' (client gives USDT), to_ccy=fiat (we pay fiat).
    Our cost per USDT = to_amt / from_amt (fiat paid / USDT received).
    """
    rows = db_exec(
        "SELECT id, from_ccy, from_amt, to_ccy, to_amt, deal_rate, market_rate, "
        "       completed_at, manager "
        "FROM orders WHERE state='completed' "
        "  AND from_ccy='USDT' AND to_ccy IN ('CZK','EUR','USD') "
        "ORDER BY completed_at DESC, id DESC LIMIT ?", (limit,))
    results = []
    for r in rows:
        fiat_ccy = r["to_ccy"]       # fiat we paid
        usdt_qty = r["from_amt"] or 0  # USDT we received from client
        fiat_paid = r["to_amt"] or 0   # fiat we paid to client
        if usdt_qty <= 0:
            continue
        cost_per_usdt = fiat_paid / usdt_qty  # our cost in fiat per 1 USDT
        mkt_now = get_rate("USDT", fiat_ccy) or 0
        # deal_rate in DB = to_amt/from_amt = fiat/USDT = cost per USDT
        results.append({
            "id": r["id"],
            "fiat_ccy": fiat_ccy,
            "usdt_qty": usdt_qty,
            "fiat_paid": fiat_paid,
            "cost_rate": cost_per_usdt,
            "deal_rate": r["deal_rate"],
            "market_rate_then": r["market_rate"] or 0,
            "market_rate_now": mkt_now,
            "date": r["completed_at"],
            "manager": r["manager"],
        })
    return results


def get_last_usdt_sales(limit: int = 5) -> list:
    """Return last N deals where WE sold USDT (client bought USDT from us).

    We SELL USDT when: to_ccy='USDT' (client receives USDT), from_ccy=fiat.
    Our revenue per USDT = from_amt / to_amt (fiat received / USDT sold).
    """
    rows = db_exec(
        "SELECT id, from_ccy, from_amt, to_ccy, to_amt, deal_rate, market_rate, "
        "       completed_at, manager "
        "FROM orders WHERE state='completed' "
        "  AND to_ccy='USDT' AND from_ccy IN ('CZK','EUR','USD') "
        "ORDER BY completed_at DESC, id DESC LIMIT ?", (limit,))
    results = []
    for r in rows:
        fiat_ccy = r["from_ccy"]       # fiat we received
        usdt_sold = r["to_amt"] or 0    # USDT we gave to client
        fiat_recv = r["from_amt"] or 0  # fiat client paid us
        if usdt_sold <= 0:
            continue
        rev_per_usdt = fiat_recv / usdt_sold
        mkt_now = get_rate("USDT", fiat_ccy) or 0
        # market_rate stored as from_ccy/to_ccy (fiat/USDT), need USDT/fiat
        mkt_then_raw = r["market_rate"] or 0
        mkt_then = (1.0 / mkt_then_raw) if mkt_then_raw > 1e-9 else 0
        results.append({
            "id": r["id"],
            "fiat_ccy": fiat_ccy,
            "usdt_sold": usdt_sold,
            "fiat_received": fiat_recv,
            "sell_rate": rev_per_usdt,
            "deal_rate": r["deal_rate"],
            "market_rate_then": mkt_then,
            "market_rate_now": mkt_now,
            "date": r["completed_at"],
            "manager": r["manager"],
        })
    return results


def get_cost_basis_summary() -> dict:
    """VWAP cost + last purchase + last sale + current market for USDT per fiat currency."""
    summary = {}
    for qccy in ["CZK", "EUR", "USD"]:
        vwap_data = get_vwap_pnl("USDT", qccy)
        # Last purchase: we BUY USDT (client gives USDT, gets fiat)
        last_buy = db_exec(
            "SELECT from_amt, to_ccy, to_amt, deal_rate, market_rate, completed_at "
            "FROM orders WHERE state='completed' AND from_ccy='USDT' AND to_ccy=? "
            "ORDER BY completed_at DESC, id DESC LIMIT 1", (qccy,))
        buy_info = None
        if last_buy:
            r = last_buy[0]
            usdt_qty = r["from_amt"] or 0
            fiat_paid = r["to_amt"] or 0
            if usdt_qty > 0:
                buy_info = {
                    "cost_rate": fiat_paid / usdt_qty,
                    "deal_rate": r["deal_rate"],
                    "market_then": r["market_rate"] or 0,
                    "usdt_qty": usdt_qty,
                    "date": r["completed_at"],
                }
        # Last sale: we SELL USDT (client gives fiat, gets USDT)
        last_sell = db_exec(
            "SELECT from_ccy, from_amt, to_amt, deal_rate, market_rate, completed_at "
            "FROM orders WHERE state='completed' AND to_ccy='USDT' AND from_ccy=? "
            "ORDER BY completed_at DESC, id DESC LIMIT 1", (qccy,))
        sell_info = None
        if last_sell:
            r = last_sell[0]
            usdt_sold = r["to_amt"] or 0
            fiat_recv = r["from_amt"] or 0
            if usdt_sold > 0:
                # market_rate stored as from_ccy/to_ccy (fiat/USDT), invert to USDT/fiat
                mkt_raw = r["market_rate"] or 0
                mkt_then = (1.0 / mkt_raw) if mkt_raw > 1e-9 else 0
                sell_info = {
                    "sell_rate": fiat_recv / usdt_sold,
                    "deal_rate": r["deal_rate"],
                    "market_then": mkt_then,
                    "usdt_sold": usdt_sold,
                    "date": r["completed_at"],
                }
        mkt_now = get_rate("USDT", qccy) or 0
        summary[qccy] = {
            "vwap": vwap_data["vwap"],
            "inventory": vwap_data["inventory"],
            "market_now": mkt_now,
            "last_purchase": buy_info,
            "last_sale": sell_info,
        }
    return summary


def get_simple_pnl(base_ccy: str = "USDT", quote_ccy: str = "CZK",
                   period: Optional[str] = None) -> dict:
    """
    v6.5 — Simple avg-buy/avg-sell PnL, period-scoped.

    avg_buy  / avg_sell  are computed from deals WITHIN the period only.
    Cross-quote deals (e.g. USDT↔EUR when asking for CZK) are converted
    to `quote_ccy` at CURRENT spot rate (consistent with unified VWAP).
    """
    if base_ccy == quote_ccy:
        return {"avg_buy":0,"avg_sell":0,"buy_qty":0,"sell_qty":0,"matched_qty":0,
                "remaining":0,"realized_pnl":0,"unrealized_pnl":0,"total_pnl":0,
                "market_rate":0}

    since = period_since(period) if period else "2000-01-01 00:00:00"

    deals = db_exec(
        "SELECT from_ccy, from_amt, to_ccy, to_amt, completed_at FROM orders "
        "WHERE state='completed' AND COALESCE(client_type,'organic')!='ads' "
        "  AND completed_at >= ? "
        "ORDER BY completed_at ASC, id ASC", (since,)
    )

    buy_cost_q = 0.0; buy_qty = 0.0     # in quote_ccy equivalent
    sell_rev_q = 0.0; sell_qty = 0.0    # in quote_ccy equivalent

    def to_quote(amt: float, ccy: str) -> float:
        if amt <= 0 or ccy == quote_ccy:
            return amt
        sp = get_rate(ccy, quote_ccy) or 0
        return amt * sp

    for d in deals:
        fc, tc = d["from_ccy"], d["to_ccy"]
        fa, ta = d["from_amt"] or 0, d["to_amt"] or 0
        if fa <= 0 or ta <= 0:
            continue
        if fc == base_ccy and tc in FIATS:
            buy_qty += fa
            buy_cost_q += to_quote(ta, tc)
        elif fc in FIATS and tc == base_ccy:
            sell_qty += ta
            sell_rev_q += to_quote(fa, fc)

    avg_buy  = (buy_cost_q / buy_qty) if buy_qty > 1e-9 else 0.0
    avg_sell = (sell_rev_q / sell_qty) if sell_qty > 1e-9 else 0.0
    matched = min(buy_qty, sell_qty)
    realized = (avg_sell - avg_buy) * matched if matched > 1e-9 else 0.0
    remaining = buy_qty - sell_qty
    mkt = get_rate(base_ccy, quote_ccy) or 0.0
    unrealized = (mkt - avg_buy) * remaining if avg_buy > 0 and remaining > 1e-9 else 0.0

    return {
        "avg_buy": avg_buy,
        "avg_sell": avg_sell,
        "buy_qty": buy_qty,
        "sell_qty": sell_qty,
        "matched_qty": matched,
        "remaining": max(0, remaining),
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "total_pnl": realized + unrealized,
        "market_rate": mkt,
    }


def get_pnl_breakdown() -> dict:
    _zero = {"vwap":0,"market_rate":0,"inventory":0,"realized_pnl":0,"unrealized_pnl":0,"total_pnl":0}
    result = {}
    for qc in ["CZK", "EUR", "USD"]:
        try: result[f"USDT/{qc}"] = get_vwap_pnl("USDT", qc)
        except Exception: result[f"USDT/{qc}"] = _zero
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# EQUITY, HWM, SNAPSHOTS, LIQUIDITY, VOLATILITY, PRICING, INTELLIGENCE
# ═══════════════════════════════════════════════════════════════════════════════
def compute_equity_usdt() -> tuple:
    """Total equity in USDT. Includes breakdown with reserved and pending_in."""
    bal = get_balances()
    res = get_reserved()
    pend = get_pending_in()
    bd = {}; total = 0.0
    for ccy in set(list(bal.keys()) + list(res.keys()) + list(pend.keys())):
        amt = bal.get(ccy, 0)
        rate = 1.0 if ccy == "USDT" else (get_rate(ccy, "USDT") or 0.0)
        uval = amt * rate
        bd[ccy] = {
            "balance": round(amt, 2),
            "reserved": round(res.get(ccy, 0), 2),
            "pending_in": round(pend.get(ccy, 0), 2),
            "available": round(amt - res.get(ccy, 0), 2),
            "rate": round(rate, 6),
            "usdt": round(uval, 2),
        }
        total += uval
    return round(total, 2), bd

def save_equity_snapshot():
    eq, _ = compute_equity_usdt()
    db_exec("INSERT INTO equity_history (equity_usdt) VALUES (?)", (eq,), fetch=None)
    log.info(f"Equity snapshot: {eq} USDT")

def get_hwm_stats() -> dict:
    eq, bd = compute_equity_usdt()
    row = db_exec("SELECT MAX(equity_usdt) hw, MIN(equity_usdt) lo FROM equity_history", fetch="one")
    cnt = db_exec("SELECT COUNT(*) c FROM equity_history", fetch="one")["c"]
    hwm = row["hw"] if row and row["hw"] else eq
    low = row["lo"] if row and row["lo"] else eq
    dd = ((eq - hwm) / hwm * 100) if hwm and abs(hwm) > 1e-9 else 0.0
    return {"equity_now": eq, "hwm": round(hwm,2), "low": round(low,2),
            "drawdown_pct": round(dd,2), "breakdown": bd, "snapshots": cnt}

def save_rate_snapshot():
    rows = get_all_rates()
    con = get_db()
    try:
        n = 0
        for r in rows:
            if r["rate"] and r["rate"] > 0:
                con.execute("INSERT INTO rate_history (pair,rate,recorded_at) VALUES (?,?,datetime('now'))",
                            (r["pair"], r["rate"])); n += 1
        con.commit()
    finally:
        con.close()
    log.info(f"Rate snapshot: {n}")

def get_rate_trend(pair: str, hours: int = 24) -> dict:
    con = get_db()
    try:
        cr = con.execute("SELECT rate FROM rates WHERE pair=?", (pair,)).fetchone()
        if not cr: return {"error": "not found"}
        rn = cr["rate"]
        ago = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        old = con.execute("SELECT rate,recorded_at FROM rate_history WHERE pair=? AND recorded_at<=? ORDER BY recorded_at DESC LIMIT 1",
                          (pair, ago)).fetchone()
        cnt = con.execute("SELECT COUNT(*) c FROM rate_history WHERE pair=?", (pair,)).fetchone()["c"]
    finally:
        con.close()
    if not old: return {"rate_now": rn, "trend_pct": None, "hours": hours, "snapshots": cnt}
    ro = old["rate"]
    tp = ((rn - ro) / ro * 100) if ro and abs(ro) > 1e-9 else 0.0
    return {"rate_now": round(rn,4), "rate_old": round(ro,4), "trend_pct": round(tp,4),
            "hours": hours, "recorded_at": old["recorded_at"], "snapshots": cnt}

def get_all_trends(hours: int = 24) -> list:
    return [(p, get_rate_trend(p, hours)) for p in sorted(r["pair"] for r in get_all_rates())]

def save_liquidity_snapshot():
    bal = get_balances()
    con = get_db()
    try:
        for ccy, amt in bal.items():
            con.execute("INSERT INTO liquidity_snapshots (ccy,balance,recorded_at) VALUES (?,?,datetime('now'))", (ccy, amt))
        con.commit()
    finally:
        con.close()

def get_liquidity_report(days: int = 7) -> dict:
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    bal_now = get_balances()
    rows = db_exec("SELECT ccy,balance FROM liquidity_snapshots WHERE recorded_at>=?", (since,))
    by_ccy = defaultdict(list)
    for r in rows: by_ccy[r["ccy"]].append(r["balance"])
    deal_rows = db_exec("SELECT to_ccy, SUM(to_amt) ov FROM orders WHERE completed_at>=? AND state='completed' GROUP BY to_ccy", (since,))
    outflows = {r["to_ccy"]: r["ov"] for r in deal_rows}
    report = {}
    for ccy in CURRENCIES:
        snaps = by_ccy.get(ccy, [])
        cur = bal_now.get(ccy, 0)
        avg = sum(snaps)/len(snaps) if snaps else cur
        mn = min(snaps) if snaps else cur
        mx = max(snaps) if snaps else cur
        ov = outflows.get(ccy, 0) or 0
        do = ov / days if days > 0 else 0
        dl = (cur / do) if do > 1e-9 and cur > 0 else float('inf')
        risk = "ПУСТО" if cur <= 0 else ("КРИТИЧНО" if dl < 2 else ("НИЗКАЯ" if dl < 5 else "OK"))
        report[ccy] = {"current": round(cur,2), "avg": round(avg,2), "min": round(mn,2),
                        "max": round(mx,2), "daily_outflow": round(do,2),
                        "days_left": round(dl,1) if dl != float('inf') else "∞", "risk": risk}
    return report

def get_volatility(pair: str = "USDT/CZK", hours: int = None) -> float:
    hours = hours or VOLATILITY_LOOKBACK_H
    since = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    rows = db_exec("SELECT rate FROM rate_history WHERE pair=? AND recorded_at>=? ORDER BY recorded_at", (pair, since))
    rates = [r["rate"] for r in rows if r["rate"] and r["rate"] > 0]
    if len(rates) < 2: return 0.0
    mn, mx, avg = min(rates), max(rates), sum(rates)/len(rates)
    return round((mx - mn) / avg * 100, 4) if avg > 0 else 0.0

def get_trade_flow(hours: int = 4) -> dict:
    since = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    # Buy USDT flow: orders where from_ccy=USDT (we receive USDT = we buy USDT)
    buys = db_exec("SELECT COALESCE(SUM(from_amt),0) v FROM orders WHERE from_ccy='USDT' AND created_at>=? AND state='completed'", (since,), "one")
    sells = db_exec("SELECT COALESCE(SUM(to_amt),0) v FROM orders WHERE to_ccy='USDT' AND created_at>=? AND state='completed'", (since,), "one")
    bv = buys["v"] if buys else 0
    sv = sells["v"] if sells else 0
    total = bv + sv
    imb = (bv - sv) / total if total > 0 else 0
    return {"buy_vol": bv, "sell_vol": sv, "imbalance": round(imb, 4)}

def compute_dynamic_pricing(quote_ccy: str = "CZK") -> dict:
    mid = get_rate("USDT", quote_ccy)
    if not mid or mid <= 0: return {"error": f"No rate USDT/{quote_ccy}"}
    base_half = BASE_SPREAD_PCT / 2
    avail = get_available()
    usdt_bal = avail.get("USDT", 0)
    quote_bal = avail.get(quote_ccy, 0)
    total_eq = abs(usdt_bal) + abs(quote_bal / mid) if mid > 0 else abs(usdt_bal)
    usdt_ratio = usdt_bal / total_eq if total_eq > 0 else 0
    inv_adj_s, inv_adj_b = 0.0, 0.0
    if usdt_ratio > 0.5 + INVENTORY_SKEW_THRESHOLD:
        inv_adj_s = -(usdt_ratio - 0.5) * BASE_SPREAD_PCT * 0.5
    elif usdt_ratio < 0.5 - INVENTORY_SKEW_THRESHOLD:
        inv_adj_b = (0.5 - usdt_ratio) * BASE_SPREAD_PCT * 0.5
    vol = get_volatility(f"USDT/{quote_ccy}")
    vol_adj = vol * VOLATILITY_SPREAD_FACTOR
    flow = get_trade_flow(4)
    fl_b, fl_s = 0.0, 0.0
    if flow["imbalance"] > 0.3: fl_s = flow["imbalance"] * BASE_SPREAD_PCT * 0.3
    elif flow["imbalance"] < -0.3: fl_b = abs(flow["imbalance"]) * BASE_SPREAD_PCT * 0.3
    buy_sp = max(MIN_SPREAD_PCT/2, min(base_half + vol_adj/2 + inv_adj_b - fl_b, BASE_SPREAD_PCT * MAX_SPREAD_MULTIPLIER/2))
    sell_sp = max(MIN_SPREAD_PCT/2, min(base_half + vol_adj/2 + inv_adj_s + fl_s, BASE_SPREAD_PCT * MAX_SPREAD_MULTIPLIER/2))
    # Custom spread override
    if quote_ccy in _custom_spread:
        cs = _custom_spread[quote_ccy] / 2
        buy_sp = cs; sell_sp = cs
    return {"mid_rate": mid, "suggested_buy": round(mid*(1-buy_sp/100),4),
            "suggested_sell": round(mid*(1+sell_sp/100),4),
            "spread_buy_pct": round(buy_sp,4), "spread_sell_pct": round(sell_sp,4),
            "total_spread_pct": round(buy_sp+sell_sp,4),
            "adjustments": {"volatility": round(vol,4), "inv_ratio": round(usdt_ratio,4),
                            "flow": flow["imbalance"]}}

def get_intelligence_report() -> dict:
    signals = []
    bal = get_balances()
    eq, bd = compute_equity_usdt()
    usdt_pct = (bal.get("USDT",0) / eq * 100) if eq > 0 else 0
    if usdt_pct > 70: signals.append(("⚠️ ПЕРЕКОС", f"USDT={usdt_pct:.0f}%. Продавай."))
    elif usdt_pct < 20 and eq > 0: signals.append(("⚠️ ДЕФИЦИТ", f"USDT={usdt_pct:.0f}%. Покупай."))
    hwm = get_hwm_stats()
    if hwm["drawdown_pct"] < -10: signals.append(("🔴 ПРОСАДКА", f"{hwm['drawdown_pct']:.1f}%"))
    avail = get_available()
    for ccy in CURRENCIES:
        a = avail.get(ccy, 0)
        if a < 0: signals.append(("🔴 ДЕФИЦИТ", f"{ccy}: available={a:,.2f} — ПЕРЕРАСХОД!"))
    vol = get_volatility("USDT/CZK")
    if vol > 2: signals.append(("⚡ VOL", f"USDT/CZK {vol:.2f}%. Расширь спред."))
    if not signals: signals.append(("✅ OK", "Всё в норме."))
    return {"signals": signals, "equity": eq, "usdt_pct": round(usdt_pct,1)}


# ═══════════════════════════════════════════════════════════════════════════════
# CLIENTS / ADS / SEGMENTATION / LIQUIDITY SOURCES
# ═══════════════════════════════════════════════════════════════════════════════

def _vol_usdt(rows) -> tuple:
    """Shared volume calc. Returns (total_vol_usdt, total_margin, spreads, unpriced).
    unpriced — сделки без курса: исключены из сумм объёма и маржи."""
    total_vol = 0.0; total_margin = 0.0; spreads = []; unpriced = 0
    for r in rows:
        fa = r["from_amt"] or 0; fc = r["from_ccy"]
        priced = True
        if fc == "USDT": total_vol += fa
        elif r["to_ccy"] == "USDT": total_vol += (r["to_amt"] or 0)
        else:
            ts = _row_get(r, "completed_at") or _row_get(r, "created_at")
            rate = rate_at(fc, "USDT", ts) if ts else get_rate(fc, "USDT")
            if not rate:
                priced = False
            else:
                total_vol += fa * rate
        m = _margin_to_usdt(r)
        if m is None:
            priced = False
        else:
            total_margin += m
        if not priced:
            unpriced += 1
        if r["spread_pct"] is not None: spreads.append(r["spread_pct"])
    return total_vol, total_margin, spreads, unpriced


def get_clients_stats(period: str = "day") -> dict:
    since = period_since(period)
    rows = db_exec(
        "SELECT from_ccy,from_amt,to_ccy,to_amt,margin_our,spread_pct,completed_at,created_at FROM orders "
        "WHERE created_at>=? AND state='completed' AND COALESCE(client_type,'organic')='organic'", (since,))
    if not rows: return {"deal_count":0,"volume_usdt":0,"total_margin":0,"avg_spread_pct":0,"unpriced_deals":0}
    vol, margin, sp, unp = _vol_usdt(rows)
    return {"deal_count":len(rows),"volume_usdt":round(vol,2),"total_margin":round(margin,2),
            "avg_spread_pct":round(sum(sp)/len(sp),4) if sp else 0,"unpriced_deals":unp}


def get_ads_stats(period: str = "day") -> dict:
    since = period_since(period)
    rows = db_exec(
        "SELECT from_ccy,from_amt,to_ccy,to_amt,margin_our,spread_pct,completed_at,created_at FROM orders "
        "WHERE created_at>=? AND state='completed' AND client_type='ads'", (since,))
    if not rows: return {"deal_count":0,"volume_usdt":0,"total_margin":0,"avg_spread_pct":0,"unpriced_deals":0}
    vol, margin, sp, unp = _vol_usdt(rows)
    return {"deal_count":len(rows),"volume_usdt":round(vol,2),"total_margin":round(margin,2),
            "avg_spread_pct":round(sum(sp)/len(sp),4) if sp else 0,"unpriced_deals":unp}


def get_ads_roi(period: str = "day") -> dict:
    since = period_since(period)
    ads = get_ads_stats(period)
    profit = ads["total_margin"]
    cost_rows = db_exec("SELECT amount,ccy FROM ad_expenses WHERE created_at>=?", (since,))
    total_cost = 0.0
    unpriced_costs = 0
    for r in cost_rows:
        if r["ccy"] == "USDT": total_cost += r["amount"]
        else:
            rate = get_rate(r["ccy"], "USDT")
            if not rate:
                unpriced_costs += 1
                continue
            total_cost += r["amount"] * rate
    roi = ((profit - total_cost) / total_cost * 100) if total_cost > 0 else 0
    return {"profit":round(profit,2),"cost":round(total_cost,2),"roi":round(roi,2),
            "deals":ads["deal_count"],"volume":ads["volume_usdt"],
            "unpriced_costs":unpriced_costs}


def get_client_segmentation(period: str = "month") -> dict:
    since = period_since(period)
    rows = db_exec(
        "SELECT client_name, from_ccy, from_amt, to_ccy, to_amt, margin_our, "
        "completed_at, created_at "
        "FROM orders WHERE created_at>=? AND state='completed' "
        "AND COALESCE(client_type,'organic')='organic' AND client_name IS NOT NULL", (since,))
    if not rows: return {"VIP":[],"NORMAL":[],"LOW":[]}
    clients = {}
    for r in rows:
        cn = r["client_name"]
        if cn not in clients:
            clients[cn] = {"name": cn, "margin": 0.0, "deals": 0, "m_sum": 0.0, "unpriced": 0}
        m = _margin_to_usdt(r)
        if m is None:
            clients[cn]["unpriced"] += 1
            continue
        clients[cn]["deals"] += 1
        clients[cn]["margin"] += m
        clients[cn]["m_sum"] += m
    cl_list = sorted(clients.values(), key=lambda x: x["margin"], reverse=True)
    for c in cl_list:
        c["avg_margin"] = round(c["m_sum"] / c["deals"], 2) if c["deals"] else 0
        c["margin"] = round(c["margin"], 2)
    n = len(cl_list)
    vip_cut = max(1, int(n * 0.2))
    normal_cut = max(vip_cut, int(n * 0.7))
    result = {"VIP":[],"NORMAL":[],"LOW":[]}
    for i, c in enumerate(cl_list):
        if i < vip_cut: result["VIP"].append(c)
        elif i < normal_cut: result["NORMAL"].append(c)
        else: result["LOW"].append(c)
    return result


def get_deal_spread_profit(period: str = "day") -> dict:
    """Profit from spread when WE SEND USDT (to_ccy=USDT).
    Spread profit = (deal_rate - market_rate) per deal, aggregated."""
    since = period_since(period)
    rows = db_exec(
        "SELECT from_ccy,from_amt,to_ccy,to_amt,deal_rate,market_rate,margin_our,"
        "completed_at,created_at FROM orders "
        "WHERE created_at>=? AND state='completed' AND to_ccy='USDT' "
        "AND COALESCE(client_type,'organic')!='ads' AND market_rate IS NOT NULL", (since,))
    total_spread_profit = 0.0
    deals = 0
    for r in rows:
        m = _margin_to_usdt(r)
        if m:
            total_spread_profit += m
            deals += 1
    # Also count reverse: from_ccy=USDT (we receive USDT, send fiat)
    rows2 = db_exec(
        "SELECT from_ccy,from_amt,to_ccy,to_amt,deal_rate,market_rate,margin_our,"
        "completed_at,created_at FROM orders "
        "WHERE created_at>=? AND state='completed' AND from_ccy='USDT' "
        "AND COALESCE(client_type,'organic')!='ads' AND market_rate IS NOT NULL", (since,))
    for r in rows2:
        m = _margin_to_usdt(r)
        if m:
            total_spread_profit += m
            deals += 1
    return {"spread_profit": round(total_spread_profit, 2), "deals": deals}


def get_liquidity_summary() -> dict:
    own = db_exec("SELECT ccy, SUM(amount) s FROM liquidity_sources WHERE type='own' GROUP BY ccy")
    credit = db_exec("SELECT ccy, SUM(amount) s FROM liquidity_sources WHERE type='credit' GROUP BY ccy")
    own_d = {r["ccy"]: r["s"] or 0 for r in own}
    credit_d = {r["ccy"]: r["s"] or 0 for r in credit}
    total_own = 0.0; total_credit = 0.0
    for ccy in set(list(own_d.keys()) + list(credit_d.keys())):
        # no rate → 0, не считаем фиат 1:1 к USDT (раньше CZK мог завышаться в ~21 раз)
        rate = 1.0 if ccy == "USDT" else (get_rate(ccy, "USDT") or 0.0)
        total_own += own_d.get(ccy, 0) * rate
        total_credit += credit_d.get(ccy, 0) * rate
    return {"own": own_d, "credit": credit_d,
            "total_own_usdt": round(total_own, 2),
            "total_credit_usdt": round(total_credit, 2),
            "total_usdt": round(total_own + total_credit, 2)}



def edit_deal_field(order_id: int, field: str, value: str, tg_id: int = 0):
    """Edit client_type, client_name or tx_hash on existing order."""
    if field not in ("client_type", "client_name", "tx_hash"):
        raise ValueError(f"Can only edit client_type, client_name or tx_hash")
    row = db_exec("SELECT id FROM orders WHERE id=?", (order_id,), "one")
    if not row:
        raise ValueError(f"Order {order_id} not found")
    db_exec(f"UPDATE orders SET {field}=? WHERE id=?", (value, order_id), fetch=None)
    audit(tg_id, "edit_deal", f"#{order_id} {field}={value}")


# ═══════════════════════════════════════════════════════════════════════════════
# EXCEL REPORT
# ═══════════════════════════════════════════════════════════════════════════════
H_FILL = PatternFill("solid", start_color="1F3864")
H_FONT = Font(name="Arial", bold=True, color="FFFFFF", size=10)
D_FONT = Font(name="Arial", size=10)
POS_F  = Font(name="Arial", size=10, color="1E7B34")
NEG_F  = Font(name="Arial", size=10, color="C00000")
ALT_F  = PatternFill("solid", start_color="EBF3FB")
SH_F   = PatternFill("solid", start_color="2E75B6")
THIN   = Side(style="thin", color="AAAAAA")
BRD    = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

# ═══════════════════════════════════════════════════════════════════════════════
# CAPITAL — якорный метод (порт capital_pnl.py; числа сверены с CLI)
# ═══════════════════════════════════════════════════════════════════════════════
DEFAULT_ANCHOR_TS = "2026-05-20 14:01:24"
# В P&L только торговые типы: 'expense' исключён (двойной учёт с таблицей expenses),
# liq_own/liq_credit исключены (вливание ликвидности — не прибыль), opening/reserve — не движение.
CAPITAL_PNL_TYPES = ("inflow", "outflow", "reversal", "fee_income")


def _xe_czk_per_usd(date_str: str) -> float:
    """CZK за 1 USD по XE на дату (снимок <= даты, иначе ближайший, иначе get_rate, фолбэк 20.8)."""
    r = db_exec("SELECT rate FROM rate_history WHERE pair='XE:USD/CZK' "
                "AND date(recorded_at) <= ? ORDER BY recorded_at DESC LIMIT 1",
                (date_str[:10],), "one")
    if r and r["rate"]: return r["rate"]
    r = db_exec("SELECT rate FROM rate_history WHERE pair='XE:USD/CZK' "
                "ORDER BY abs(julianday(recorded_at)-julianday(?)) LIMIT 1",
                (date_str,), "one")
    if r and r["rate"]: return r["rate"]
    rt = get_rate("USD", "CZK")
    return rt if rt else 20.8


def _usdt_per_eur(date_str: str) -> float:
    """USDT за 1 EUR на дату (rate_history pair='USDT/EUR' = EUR за 1 USDT -> инверс)."""
    r = db_exec("SELECT rate FROM rate_history WHERE pair='USDT/EUR' "
                "AND recorded_at <= ? ORDER BY recorded_at DESC LIMIT 1", (date_str,), "one")
    if r and r["rate"]: return 1.0 / r["rate"]
    r = db_exec("SELECT rate FROM rate_history WHERE pair='USDT/EUR' "
                "ORDER BY abs(julianday(recorded_at)-julianday(?)) LIMIT 1", (date_str,), "one")
    if r and r["rate"]: return 1.0 / r["rate"]
    return 1.16


def _capital_usdt_value(ccy: str, amount: float, date_str: str) -> float:
    """Оценка суммы в USDT по курсу указанной даты (USD≈USDT 1:1)."""
    if ccy in ("USDT", "USD"): return amount
    if ccy == "CZK": return amount / _xe_czk_per_usd(date_str)
    if ccy == "EUR": return amount * _usdt_per_eur(date_str)
    return amount


def get_capital_anchor():
    return db_exec("SELECT anchor_ts, start_usdt, note FROM capital_anchor "
                   "ORDER BY id DESC LIMIT 1", fetch="one")


def set_capital_anchor(start_usdt: float, anchor_ts: str = None,
                       note: str = "via bot", tg_id: int = 0) -> str:
    """Новый якорь капитала. Без anchor_ts — на текущий момент (переподбивка)."""
    ts = anchor_ts or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")  # валидация формата
    db_exec("INSERT INTO capital_anchor (anchor_ts, start_usdt, note, created_at) "
            "VALUES (?,?,?,?)",
            (ts, float(start_usdt), note, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            fetch=None)
    audit(tg_id, "set_anchor", f"{start_usdt} USDT @ {ts}")
    return ts


def compute_capital() -> Optional[dict]:
    """Капитал = старт(якорь) + торговый P&L после якоря − расходы после якоря.
    Каждая нога оценена в USDT по курсу её дня. None если якорь не задан."""
    a = get_capital_anchor()
    if not a:
        return None
    anchor_ts, start_usdt = a["anchor_ts"], a["start_usdt"]
    ph = ",".join("?" * len(CAPITAL_PNL_TYPES))
    rows = db_exec(f"SELECT ccy, amount, ts FROM ledger_entries "
                   f"WHERE ts > ? AND entry_type IN ({ph})",
                   (anchor_ts, *CAPITAL_PNL_TYPES))
    pnl, by_ccy = 0.0, {}
    for r in rows:
        v = _capital_usdt_value(r["ccy"], r["amount"], r["ts"])
        pnl += v
        by_ccy[r["ccy"]] = by_ccy.get(r["ccy"], 0.0) + v
    exp, exp_detail = 0.0, []
    for r in db_exec("SELECT ccy, amount, note, created_at FROM expenses "
                     "WHERE created_at > ?", (anchor_ts,)):
        v = _capital_usdt_value(r["ccy"], abs(r["amount"]), r["created_at"])
        exp += v
        exp_detail.append((r["created_at"], r["ccy"], r["amount"], v, r["note"]))
    return {"anchor_ts": anchor_ts, "start_usdt": start_usdt, "anchor_note": a["note"],
            "trading_pnl_usdt": pnl, "pnl_by_ccy": by_ccy, "ledger_legs": len(rows),
            "expenses_usdt": exp, "expenses_detail": exp_detail,
            "capital_usdt": start_usdt + pnl - exp}


def save_capital_snapshot():
    """Снимок капитала в capital_history (ночное закрытие). Возвращает капитал или None."""
    c = compute_capital()
    if c is None:
        return None
    db_exec("INSERT INTO capital_history (capital_usdt, pnl_usdt, expenses_usdt, recorded_at) "
            "VALUES (?,?,?,?)",
            (c["capital_usdt"], c["trading_pnl_usdt"], c["expenses_usdt"],
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")), fetch=None)
    return c["capital_usdt"]


def format_capital_text() -> str:
    """Текст «💼 Капитал» для /capital, кнопки и ночного отчёта. Без Markdown."""
    c = compute_capital()
    if c is None:
        return ("💼 Капитал: якорь не задан.\n\n"
                "Задай стартовую ликвидность командой:\n"
                "/set_anchor 50000 2026-05-20 14:01:24\n"
                "(сумма USDT; без даты — якорь на текущий момент)")
    lines = ["💼 КАПИТАЛ (якорный метод)", "",
             f"Якорь: {c['anchor_ts']} · {c['anchor_note'] or ''}".rstrip(" ·"),
             f"Старт: {c['start_usdt']:,.2f} USDT", "",
             f"Торговый P&L: {c['trading_pnl_usdt']:+,.2f} USDT · {c['ledger_legs']} ног"]
    for ccy, v in sorted(c["pnl_by_ccy"].items()):
        lines.append(f"   {ccy}: {v:+,.2f}")
    lines.append(f"Расходы: −{c['expenses_usdt']:,.2f} USDT · {len(c['expenses_detail'])} шт")
    for created, ccy, amt, v, note in c["expenses_detail"][-3:]:
        tail = f" · {note[:26]}" if note else ""
        lines.append(f"   {created[:10]} −{abs(amt):,.0f} {ccy} ≈ {v:,.2f} USDT{tail}")
    lines += ["─" * 28, f"ИТОГО: {c['capital_usdt']:,.2f} USDT"]
    try:
        prev = db_exec("SELECT capital_usdt, recorded_at FROM capital_history "
                       "WHERE date(recorded_at) < date('now','localtime') "
                       "ORDER BY id DESC LIMIT 1", fetch="one")
        if prev and prev["capital_usdt"] is not None:
            lines.append(f"Δ с {prev['recorded_at'][:10]}: {c['capital_usdt']-prev['capital_usdt']:+,.2f} USDT")
    except sqlite3.OperationalError:
        pass  # capital_history появится после init_db (первый запуск новой версии)
    return "\n".join(lines)


def _hdr(ws,c,r,v):
    cl=ws.cell(r,c,v); cl.fill=H_FILL; cl.font=H_FONT; cl.border=BRD
    cl.alignment=Alignment(horizontal="center",vertical="center",wrap_text=True)
def _cl(ws,c,r,v,fmt=None,font=None,fill=None):
    cl=ws.cell(r,c,v); cl.font=font or D_FONT; cl.border=BRD
    cl.alignment=Alignment(horizontal="center",vertical="center")
    if fmt: cl.number_format=fmt
    if fill: cl.fill=fill

def generate_excel_report(period: str = "month") -> BytesIO:
    since = period_since(period)
    deals = db_exec("SELECT * FROM orders WHERE created_at>=? AND state='completed' ORDER BY created_at", (since,))
    wb = Workbook()
    ws = wb.active; ws.title = "СДЕЛКИ"; ws.sheet_view.showGridLines = False
    hdrs=["ID","Дата","Менеджер","Тип","Отдаём","Σ отдаём","Получаем","Σ получаем","Курс","Рынок","Маржа USDT","Спред%","Метод"]
    wids=[6,16,12,10,7,13,7,13,12,12,13,9,12]
    for ci,(h,w) in enumerate(zip(hdrs,wids),1):
        _hdr(ws,ci,1,h); ws.column_dimensions[get_column_letter(ci)].width=w
    for ri,d in enumerate(deals,2):
        fill=ALT_F if ri%2==0 else None
        m_usdt=_margin_to_usdt(d) or 0.0; sp=_safe(d,"spread_pct",0)
        vals=[_safe(d,"id"),(_safe(d,"created_at","") or "")[:16],_safe(d,"manager",""),
              _safe(d,"deal_type",""),_safe(d,"from_ccy",""),_safe(d,"from_amt",0),
              _safe(d,"to_ccy",""),_safe(d,"to_amt",0),
              round(_safe(d,"deal_rate",0),4),round(_safe(d,"market_rate",0) or 0,4),
              round(m_usdt,2),round(sp,2),_safe(d,"method","") or ""]
        fmts=[None,None,None,None,None,'#,##0.00',None,'#,##0.00','#,##0.0000','#,##0.0000','#,##0.00','0.00',None]
        for ci,(v,f) in enumerate(zip(vals,fmts),1):
            ft=POS_F if ci==11 and m_usdt>=0 else (NEG_F if ci==11 else None)
            _cl(ws,ci,ri,v,f,ft,fill)
    n=len(deals)+1
    if deals:
        tr=n+1; bf=Font(name="Arial",bold=True,size=10)
        c=ws.cell(tr,1,"ИТОГО"); c.fill=SH_F; c.font=Font(name="Arial",bold=True,color="FFFFFF",size=10); c.border=BRD
        # суммы по валютам (раньше складывались CZK+EUR+USDT в одну ячейку — бессмыслица)
        fin, fout = {}, {}
        for d in deals:
            fin[_safe(d,"from_ccy","")] = fin.get(_safe(d,"from_ccy",""),0)+_safe(d,"from_amt",0)
            fout[_safe(d,"to_ccy","")] = fout.get(_safe(d,"to_ccy",""),0)+_safe(d,"to_amt",0)
        _cl(ws,6,tr," · ".join(f"{v:,.0f} {c}" for c,v in sorted(fin.items()) if c),None,bf)
        _cl(ws,8,tr," · ".join(f"{v:,.0f} {c}" for c,v in sorted(fout.items()) if c),None,bf)
        tm=sum((_margin_to_usdt(d) or 0.0) for d in deals)
        _cl(ws,11,tr,round(tm,2),'#,##0.00',POS_F if tm>=0 else NEG_F)
    ws.freeze_panes="A2"

    # P&L sheet
    ws2=wb.create_sheet("P&L"); ws2.sheet_view.showGridLines=False
    for ci,(h,w) in enumerate(zip(["Дата","Сделок","Маржа USDT","P&L нак. USDT"],[14,10,14,14]),1):
        _hdr(ws2,ci,1,h); ws2.column_dimensions[get_column_letter(ci)].width=w
    days={}
    for d in deals:
        day=(_safe(d,"created_at","") or "")[:10]
        if not day: continue
        if day not in days: days[day]={"c":0,"m":0}
        days[day]["c"]+=1; days[day]["m"]+=_margin_to_usdt(d) or 0.0
    run=0.0
    for ri,(day,dd) in enumerate(sorted(days.items()),2):
        run+=dd["m"]
        for ci,(v,f) in enumerate(zip([day,dd["c"],round(dd["m"],2),round(run,2)],
            [None,None,'#,##0.00','#,##0.00']),1):
            _cl(ws2,ci,ri,v,f,fill=ALT_F if ri%2==0 else None)
    ws2.freeze_panes="A2"

    # Balance sheet
    ws3=wb.create_sheet("БАЛАНС"); ws3.sheet_view.showGridLines=False
    for ci,(h,w) in enumerate(zip(["Валюта","Баланс","Резерв","Доступно"],[12,16,16,16]),1):
        _hdr(ws3,ci,1,h); ws3.column_dimensions[get_column_letter(ci)].width=w
    bal=get_balances(); res=get_reserved(); avl=get_available()
    for ri,ccy in enumerate(CURRENCIES,2):
        fill=ALT_F if ri%2==0 else None
        _cl(ws3,1,ri,ccy,font=Font(name="Arial",bold=True,size=10),fill=fill)
        _cl(ws3,2,ri,round(bal.get(ccy,0),2),'#,##0.00',fill=fill)
        _cl(ws3,3,ri,round(res.get(ccy,0),2),'#,##0.00',fill=fill)
        a=avl.get(ccy,0)
        _cl(ws3,4,ri,round(a,2),'#,##0.00',POS_F if a>=0 else NEG_F,fill)

    # VWAP sheet
    ws5=wb.create_sheet("VWAP"); ws5.sheet_view.showGridLines=False
    ws5.column_dimensions["A"].width=24; ws5.column_dimensions["B"].width=18; ws5.column_dimensions["C"].width=18
    _hdr(ws5,1,1,"Метрика"); _hdr(ws5,2,1,"USDT/CZK"); _hdr(ws5,3,1,"USDT/EUR")
    _zero_pnl = {"vwap":0,"market_rate":0,"inventory":0,"realized_pnl":0,"unrealized_pnl":0,"total_pnl":0}
    try: _vc=get_vwap_pnl("USDT","CZK")
    except Exception: _vc=_zero_pnl
    try: _ve=get_vwap_pnl("USDT","EUR")
    except Exception: _ve=_zero_pnl
    vd=[_vc, _ve]
    for ri,(lbl,key,fmt) in enumerate([
        ("VWAP","vwap",'#,##0.0000'),("Рынок","market_rate",'#,##0.0000'),
        ("Инвентарь","inventory",'#,##0.00'),("Реализ.P&L","realized_pnl",'#,##0.00'),
        ("Нереализ.P&L","unrealized_pnl",'#,##0.00'),("Итого P&L","total_pnl",'#,##0.00')],2):
        fill=ALT_F if ri%2==0 else None
        _cl(ws5,1,ri,lbl,fill=fill)
        for ci,v in enumerate(vd,2):
            val=round(v.get(key,0),4 if "vwap" in key or "rate" in key else 2)
            ft=POS_F if "pnl" in key and val>=0 else (NEG_F if "pnl" in key and val<0 else None)
            _cl(ws5,ci,ri,val,fmt,ft,fill)

    # Clients sheet (organic only)
    organic = [d for d in deals if _safe(d,"client_type","organic") != "ads"]
    ws6=wb.create_sheet("КЛИЕНТЫ 🟢"); ws6.sheet_view.showGridLines=False
    for ci,(h,w) in enumerate(zip(["ID","Дата","Клиент","Отдаём","Σ","Получаем","Σ","Маржа USDT"],[6,16,14,7,13,7,13,13]),1):
        _hdr(ws6,ci,1,h); ws6.column_dimensions[get_column_letter(ci)].width=w
    for ri,d in enumerate(organic,2):
        fill=ALT_F if ri%2==0 else None; m=_margin_to_usdt(d) or 0.0
        for ci,(v,f) in enumerate(zip([_safe(d,"id"),(_safe(d,"created_at","") or "")[:16],
            _safe(d,"client_name","") or "—",_safe(d,"from_ccy",""),_safe(d,"from_amt",0),
            _safe(d,"to_ccy",""),_safe(d,"to_amt",0),round(m,2)],
            [None,None,None,None,'#,##0.00',None,'#,##0.00','#,##0.00']),1):
            ft=POS_F if ci==8 and m>=0 else (NEG_F if ci==8 else None)
            _cl(ws6,ci,ri,v,f,ft,fill)

    # Ads sheet
    ads_deals = [d for d in deals if _safe(d,"client_type","") == "ads"]
    ws7=wb.create_sheet("РЕКЛАМА 🔴"); ws7.sheet_view.showGridLines=False
    for ci,(h,w) in enumerate(zip(["ID","Дата","Клиент","Отдаём","Σ","Получаем","Σ","Маржа USDT"],[6,16,14,7,13,7,13,13]),1):
        _hdr(ws7,ci,1,h); ws7.column_dimensions[get_column_letter(ci)].width=w
    for ri,d in enumerate(ads_deals,2):
        fill=ALT_F if ri%2==0 else None; m=_margin_to_usdt(d) or 0.0
        for ci,(v,f) in enumerate(zip([_safe(d,"id"),(_safe(d,"created_at","") or "")[:16],
            _safe(d,"client_name","") or "—",_safe(d,"from_ccy",""),_safe(d,"from_amt",0),
            _safe(d,"to_ccy",""),_safe(d,"to_amt",0),round(m,2)],
            [None,None,None,None,'#,##0.00',None,'#,##0.00','#,##0.00']),1):
            ft=POS_F if ci==8 and m>=0 else (NEG_F if ci==8 else None)
            _cl(ws7,ci,ri,v,f,ft,fill)

    buf=BytesIO(); wb.save(buf); buf.seek(0)
    log.info(f"Excel: {len(deals)} deals")
    return buf


# ═══════════════════════════════════════════════════════════════════════════════
# RECEIPT
# ═══════════════════════════════════════════════════════════════════════════════
def _md(text) -> str:
    """Escape user-supplied text for legacy Markdown (имя клиента с * или _ ломало чек)."""
    return str(text).replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")


def format_receipt(oid: int, d: dict, mgr: str) -> str:
    dt=d.get("deal_type","")
    # Labels from OUR (exchange) perspective
    if dt=="BUY": op=f"Продажа #{d.get('to_ccy','')}"
    elif dt=="SELL": op=f"Покупка #{d.get('from_ccy','')}"
    else: op=f"Обмен {d.get('from_ccy','')}→{d.get('to_ccy','')}"
    fee=d.get("fee",0) or 0; fc=d.get("fee_ccy","") or d.get("to_ccy","")
    meth=d.get("method") or "—"; addr=d.get("address") or "—"; tx=d.get("tx_hash")
    m=d.get("margin_our")
    # Списание = что МЫ отдали (to_amt), Получение = что МЫ получили (from_amt)
    lines=[f"*{op}*",f"ID: #`{oid:07d}`","✅ Завершён","",
           f"Списание: *{d.get('to_amt',0):,.2f} {d.get('to_ccy','')}*",
           f"Получение: *{d.get('from_amt',0):,.2f} {d.get('from_ccy','')}*",
           f"Курс: {d.get('deal_rate',0):.4f}"]
    mr = d.get("market_rate"); br = d.get("bot_rate")
    if mr: lines.append(f"Рынок: {mr:.4f}")
    if br and mr and abs(br - mr) > 0.0001:
        lines.append(f"Курс бота: {br:.4f}")
    if fee>0: lines.append(f"Комиссия: {fee:.2f} {fc}")
    lines.append(f"Метод: {meth}")
    if addr!="—": lines.append(f"Адрес: `{addr}`")
    if tx: lines.append(f"TX: {tx}" if tx.startswith("http") else f"TX: `{tx}`")
    lines.append("")
    if m is not None: lines.append(f"{'📈' if m>=0 else '📉'} Маржа: {m:+,.2f} {d.get('to_ccy','')}")
    receipt_date = d.get("custom_date") or d.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try: receipt_date_fmt = datetime.strptime(receipt_date[:16], "%Y-%m-%d %H:%M").strftime("%d.%m.%Y %H:%M")
    except: receipt_date_fmt = receipt_date[:16]
    ct = d.get("client_type","organic"); cn = d.get("client_name")
    ct_lbl = "🟢 Наш" if ct == "organic" else "🔴 Реклама"
    ct_line = f"Клиент: {ct_lbl}"
    if cn: ct_line += f" ({_md(cn)})"
    lines.extend([ct_line,
                  f"Менеджер: @{_md(mgr.lstrip('@'))}",
                  f"Дата: {receipt_date_fmt}"])
    return "\n".join(lines)

def format_receipt_from_db(row) -> str:
    d={k:_safe(row,k,None) for k in ["deal_type","from_ccy","from_amt","to_ccy","to_amt",
       "deal_rate","market_rate","margin_our","method","address","fee","fee_ccy","tx_hash",
       "created_at","client_type","client_name"]}
    for k in ["from_amt","to_amt","deal_rate","fee"]: d[k]=d[k] or 0
    return format_receipt(_safe(row,"id",0), d, _safe(row,"manager",""))


def _mdv2(text: str) -> str:
    """Escape text for MarkdownV2."""
    special = '_*[]()~`>#+-=|{}.!'
    return "".join(f'\\{c}' if c in special else c for c in str(text))


def format_channel_post(oid: int, d: dict, mgr: str) -> str:
    """Format a beautiful channel post. Margin in spoiler. MarkdownV2."""
    dt = d.get("deal_type","")
    fc, tc = d.get("from_ccy",""), d.get("to_ccy","")
    fa, ta = d.get("from_amt",0), d.get("to_amt",0)
    dr = d.get("deal_rate",0)
    if dt == "BUY": op = f"Продажа {tc}"
    elif dt == "SELL": op = f"Покупка {fc}"
    else: op = f"Обмен {fc} → {tc}"
    # Date
    rd = d.get("custom_date") or d.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try: rd_fmt = datetime.strptime(rd[:16], "%Y-%m-%d %H:%M").strftime("%d.%m.%Y %H:%M")
    except: rd_fmt = rd[:16]
    sep = "━━━━━━━━━━━━━━━━"
    e = _mdv2  # shortcut
    # Списание = что МЫ отдали (to), Получение = что МЫ получили (from)
    lines = [
        "*💱 SwapMe Exchange*","",sep,
        f"🔄 {e(op)}",sep,"",
        f"💵 Отдали: {e(f'{ta:,.2f}')} {e(tc)}",
        f"➡️ Получили: {e(f'{fa:,.2f}')} {e(fc)}",
        f"📊 Курс: {e(f'{dr:.4f}')}","",
        f"👤 Менеджер: @{e(mgr.lstrip('@'))}",
    ]
    cn = d.get("client_name")
    if cn: lines.append(f"🤝 Принял: {e(cn)}")
    lines.extend([f"🕐 {e(rd_fmt)}",sep,""])
    # Margin from market (spoiler)
    m = d.get("margin_our")
    sp_pct = d.get("spread_pct")
    margin_parts = []
    if m is not None:
        ic = "📈" if m >= 0 else "📉"
        pct_s = f" ({sp_pct:+.2f}%)" if sp_pct is not None else ""
        margin_parts.append(f"{ic} Маржа \\(рынок\\): {e(f'{m:+,.2f}')} {e(tc)}{e(pct_s)}")
    # Margin from VWAP
    try:
        if "USDT" in (fc, tc):
            qc = tc if fc == "USDT" else fc
            vwap_data = get_vwap_pnl("USDT", qc)
            vwap = vwap_data.get("vwap", 0)
            if vwap and vwap > 0:
                if fc == "USDT":
                    # мы ПОКУПАЕМ USDT по dr: выгода когда купили ДЕШЕВЛЕ VWAP
                    vwap_margin = (vwap - dr) * fa
                else:
                    # мы ПРОДАЁМ USDT: выгода когда продали ДОРОЖЕ VWAP
                    rate_q_per_u = fa / ta if ta > 0 else 0
                    vwap_margin = (rate_q_per_u - vwap) * ta
                margin_parts.append(f"📈 Маржа \\(VWAP {e(f'{vwap:.4f}')}\\): {e(f'{vwap_margin:+,.2f}')} {e(qc)}")
    except Exception:
        pass
    if margin_parts:
        lines.append("||" + "\n".join(margin_parts) + "||")
    tx = d.get("tx_hash")
    if tx: lines.append(f"\n🔗 TX: `{e(tx)}`")
    lines.extend(["","\\#swapme \\#exchange","","🍓"])
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════
def main_menu_keyboard(tg_id: int) -> InlineKeyboardMarkup:
    """Меню v2: частые действия сверху, остальное — по разделам (было ~20 кнопок)."""
    kb = [
        [InlineKeyboardButton("➕ Новая сделка", callback_data="new_deal")],
        [InlineKeyboardButton("💼 Капитал", callback_data="capital"),
         InlineKeyboardButton("📊 Баланс", callback_data="balance")],
        [InlineKeyboardButton("💱 Курсы", callback_data="rates"),
         InlineKeyboardButton("💰 P&L", callback_data="pnl_vwap")],
        [InlineKeyboardButton("💱 Сделки", callback_data="menu_deals"),
         InlineKeyboardButton("💰 Деньги", callback_data="menu_money")],
        [InlineKeyboardButton("📈 Аналитика", callback_data="menu_analytics"),
         InlineKeyboardButton("⚙️ Ещё", callback_data="menu_more")],
    ]
    if is_admin(tg_id):
        kb.append([InlineKeyboardButton("⚙️ Админ", callback_data="admin")])
    return InlineKeyboardMarkup(kb)


_MENU_SECTIONS = {
    "deals": ("💱 Сделки", [
        [("➕ Новая сделка", "new_deal")],
        [("✏️ Ввод курса", "manual_rate_menu"), ("🔢 Конвертер", "converter")],
        [("🧾 Чеки", "receipts"), ("🗂 История", "history")],
        [("💡 Цены", "pricing")],
        [("↩ Меню", "main_menu")],
    ]),
    "money": ("💰 Деньги", [
        [("💼 Капитал", "capital"), ("📊 Баланс", "balance")],
        [("🔄 Ликвидность", "liquidity"), ("💵 Cashflow", "cashflow")],
        [("💰 P&L", "pnl_vwap"), ("🆕 Себестоимость", "cost_basis")],
        [("💸 Расходы", "expenses_menu"), ("🔴 Реклама", "ads_menu")],
        [("↩ Меню", "main_menu")],
    ]),
    "analytics": ("📈 Аналитика", [
        [("📈 Статистика", "stats_menu"), ("📉 Тренды", "trends")],
        [("👥 Менеджеры", "mgr_stats"), ("🟢 Клиенты", "clients_menu")],
        [("🧠 Интеллект", "intelligence"), ("📁 Excel", "report_menu")],
        [("↩ Меню", "main_menu")],
    ]),
    "more": ("⚙️ Ещё", [
        [("💱 Курсы", "rates"), ("📝 Заметки", "notes_menu")],
        [("📁 Excel", "report_menu"), ("🧾 Чеки", "receipts")],
        [("↩ Меню", "main_menu")],
    ]),
}


async def cb_menu_section(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Раздел меню v2. Pattern: ^menu_(deals|money|analytics|more)$"""
    q = update.callback_query; await q.answer()
    sec = q.data.replace("menu_", "")
    title, rows = _MENU_SECTIONS.get(sec, ("Меню", [[("↩ Меню", "main_menu")]]))
    kb = [[InlineKeyboardButton(t, callback_data=c) for (t, c) in row] for row in rows]
    await q.edit_message_text(title, reply_markup=InlineKeyboardMarkup(kb))

REPLY_KB_LAYOUT = [
    ["➕ Сделка", "💼 Капитал"],
    ["📊 Баланс", "💰 P&L"],
    ["📈 Статистика", "💱 Курсы"],
    ["🔄 Ликвидность", "≡ Меню"],
]

def reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton(b) for b in row] for row in REPLY_KB_LAYOUT],
        resize_keyboard=True, is_persistent=True,
    )

# map reply-keyboard text → inline callback_data
REPLY_BTN_TO_CB = {
    "➕ Сделка":         "new_deal",
    "📊 Баланс":         "balance",
    "💰 P&L":            "pnl_vwap",
    "💼 Капитал":        "capital",
    "📈 Статистика":     "stats_menu",
    "💱 Курсы":          "rates",
    "🆕 Себестоимость":  "cost_basis",
    "🔄 Ликвидность":    "liquidity",
    "≡ Меню":            "main_menu",
}

async def show_main_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE, text=None):
    msg = text or "*SwapMe v6.4*\nВыбери:"
    mk = main_menu_keyboard(update.effective_user.id)
    if update.message:
        await update.message.reply_text(msg, reply_markup=mk, parse_mode="Markdown")
    else:
        try:
            await update.callback_query.edit_message_text(msg, reply_markup=mk, parse_mode="Markdown")
        except Exception as e:
            if "not modified" not in str(e).lower():
                raise

BK = [[InlineKeyboardButton("Меню", callback_data="main_menu")]]

def _analyze_balance(eq, bd, avl) -> str:
    """Generate smart balance analysis."""
    tips = []
    for ccy in CURRENCIES:
        b = bd.get(ccy, {}); bal = b.get("balance", 0); av = avl.get(ccy, 0)
        if ccy == "USDT" and bal < 1000:
            tips.append(f"Запас USDT критично низкий ({bal:,.0f}), нужно пополнение")
        elif ccy == "CZK" and bal < -100000:
            tips.append(f"Дефицит CZK {bal:,.0f} — требуется закрытие позиции")
        elif ccy == "CZK" and bal < 0:
            tips.append(f"CZK в минусе ({bal:,.0f}), контролируй расход")
    if eq > 0:
        usdt_bal = bd.get("USDT", {}).get("balance", 0)
        if usdt_bal > 0 and eq > 0:
            usdt_pct = min(usdt_bal / eq * 100, 100)
            if usdt_pct > 80:
                tips.append(f"USDT составляет {usdt_pct:.0f}% портфеля — высокая концентрация")
            elif usdt_pct < 30:
                tips.append(f"Доля USDT всего {usdt_pct:.0f}% — большая часть в фиате")
    return "; ".join(tips) if tips else "Баланс в норме"

def _analyze_stats(s, period_lbl) -> str:
    """Generate smart stats analysis."""
    if not s.get("deal_count"):
        return f"Нет сделок за {period_lbl}"
    tips = []
    dc = s["deal_count"]; vol = s["volume_usdt"]; mg = s["total_margin"]; sp = s["avg_spread_pct"]
    avg_deal = vol / dc if dc else 0
    tips.append(f"Средний чек: {avg_deal:,.0f} USDT")
    if mg > 0:
        margin_pct = (mg / vol * 100) if vol > 0 else 0
        tips.append(f"Маржинальность: {margin_pct:.2f}%")
    if sp < 1.0:
        tips.append(f"Спред низковат ({sp:.2f}%), можно увеличить")
    elif sp > 4.0:
        tips.append(f"Спред высокий ({sp:.2f}%), конкурентоспособность?")
    if dc == 1:
        tips.append("Всего 1 сделка — мало данных для анализа")
    return ". ".join(tips)

def _analyze_pnl(vc, ve, hw, s, dsp, period_lbl) -> str:
    """Generate smart P&L analysis."""
    tips = []
    # VWAP analysis
    if vc["vwap"] > 0 and vc["market_rate"] > 0:
        diff_pct = ((vc["market_rate"] - vc["vwap"]) / vc["vwap"]) * 100
        if diff_pct > 1:
            tips.append(f"Рынок CZK выше VWAP на {diff_pct:.1f}% — хорошо для продаж")
        elif diff_pct < -1:
            tips.append(f"Рынок CZK ниже VWAP на {abs(diff_pct):.1f}% — невыгодно продавать")
    # Equity
    if hw["drawdown_pct"] < -5:
        tips.append(f"Просадка {hw['drawdown_pct']:.1f}% — обрати внимание")
    # Margin
    if s.get("deal_count", 0) > 0:
        mg = s["total_margin"]
        vol = s["volume_usdt"]
        if mg > 0:
            tips.append(f"Заработано {mg:,.2f} USDT за {period_lbl.lower()} ({s['deal_count']}сд)")
        else:
            tips.append(f"Убыток {mg:,.2f} USDT за {period_lbl.lower()}")
        if vol > 0:
            tips.append(f"ROI на объём: {mg/vol*100:.2f}%")
    # Realized
    if vc["realized_pnl"] > 0:
        tips.append(f"Реализовано {vc['realized_pnl']:,.0f} CZK прибыли по VWAP")
    return ". ".join(tips) if tips else "Данных недостаточно"

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if not is_authorized(u.id):
        await update.message.reply_text("Введи код:"); return AUTH_CODE
    await asyncio.to_thread(refresh_rates); audit(u.id, "start")
    # Attach persistent reply keyboard on /start
    await update.message.reply_text(
        "👋 *SwapMe v6.4* — клавиатура внизу всегда под рукой.",
        reply_markup=reply_keyboard(), parse_mode="Markdown",
    )
    await show_main_menu(update, ctx); return ConversationHandler.END

class _FakeCallback:
    """Обёртка: вызывает cb_*-хендлеры (ждущие callback_query) из reply-сообщения.
    edit_message_text → reply_text (новое сообщение)."""
    def __init__(self, message, from_user, data):
        self.message = message
        self.from_user = from_user
        self.data = data

    async def answer(self, *a, **k):
        return None

    async def edit_message_text(self, *a, **k):
        return await self.message.reply_text(*a, **k)


class _FakeUpdate:
    """update.callback_query есть, update.message — нет (для cb_*-хендлеров)."""
    def __init__(self, real_update, fake_cb):
        self._real = real_update
        self.callback_query = fake_cb
        self.message = None
        self.effective_user = real_update.effective_user

    def __getattr__(self, item):
        return getattr(self._real, item)


_REPLY_CB_FUNCS = {}  # заполняется в main() после объявления cb_*


async def reply_kb_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Reply-кнопка = сразу действие (без промежуточного «Нажми чтобы…»)."""
    if not update.message or not update.message.text:
        return
    cb = REPLY_BTN_TO_CB.get(update.message.text.strip())
    if not cb:
        return
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Введи /start")
        return
    if cb == "main_menu":
        await show_main_menu(update, ctx)
        return
    if cb == "new_deal":
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
        await update.message.reply_text(
            "Открыть:", reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Открыть", callback_data=cb)]]))
        return
    fake = _FakeUpdate(update, _FakeCallback(update.message, update.effective_user, cb))
    await fn(fake, ctx)

async def cmd_add_liquidity(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Формат: /add_liquidity USDT 1000 [own|credit] [note...]"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Нет доступа."); return
    parts = update.message.text.split()
    if len(parts) < 3:
        await update.message.reply_text(
            "Формат: /add_liquidity <CCY> <amount> [own|credit] [note...]\n"
            "own   = собственные средства (капитал)\n"
            "credit= взяли у другой обменки (долг)\n"
            "Примеры:\n"
            "  /add_liquidity USDT 5000 own начальный капитал\n"
            "  /add_liquidity USDT 20000 credit у Димона под 0.5%"
        ); return
    try:
        ccy, amt = parts[1].upper(), float(parts[2])
        src_type = "own"
        rest = parts[3:]
        if rest and rest[0].lower() in ("own", "credit"):
            src_type = rest[0].lower(); rest = rest[1:]
        note = " ".join(rest) if rest else f"{src_type} top-up"
        u = update.effective_user
        add_liquidity(ccy, amt, note, src_type=src_type, tg_id=u.id, manager=u.username or u.first_name or "")
        audit(u.id, "liquidity_add", f"+{amt} {ccy} [{src_type}] ({note})")
        emoji = "💰" if src_type == "own" else "🏦"
        await update.message.reply_text(
            f"{emoji} +{amt:,.2f} {ccy} [{src_type}]\n{note}",
            reply_markup=InlineKeyboardMarkup(BK))
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

async def cmd_liq_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """История внешней ликвидности + сводка own/credit."""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Нет доступа."); return
    summary = get_liquidity_summary()
    history = get_liquidity_history(20)
    own_d = summary.get("own", {}); cr_d = summary.get("credit", {})
    lines = ["🔄 *Ликвидность — сводка*\n"]
    ccys = sorted(set(list(own_d.keys()) + list(cr_d.keys())))
    if not ccys:
        lines.append("_нет записей_\n")
    else:
        for ccy in ccys:
            lines.append(f"  {ccy}: свои {own_d.get(ccy,0):+,.2f} | кредит {cr_d.get(ccy,0):+,.2f}")
        lines.append(f"  _итого USDT: свои≈{summary.get('total_own_usdt',0):,.2f}, "
                     f"кредит≈{summary.get('total_credit_usdt',0):,.2f}_")
    lines.append("\n📜 *Последние 20:*")
    if not history:
        lines.append("_пусто_")
    for r in history:
        emoji = "💰" if r["type"] == "own" else "🏦"
        sign = "+" if r["amount"] > 0 else ""
        note = r["note"] or ""
        lines.append(f"{emoji} #{r['id']} {r['created_at'][:16]} {sign}{r['amount']:,.2f} {r['ccy']} [{r['type']}] {note}")
    await update.message.reply_text(
        "\n".join(lines), parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(BK))

async def cmd_withdraw_liquidity(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Формат: /withdraw USDT 1000 [own|credit] [note...]
    credit = возврат долга другой обменке. own = изъятие своих средств."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Нет доступа."); return
    parts = update.message.text.split()
    if len(parts) < 3:
        await update.message.reply_text(
            "Формат: /withdraw <CCY> <amount> [own|credit] [note...]\n"
            "Пример: /withdraw USDT 20000 credit вернул Димону"
        ); return
    try:
        ccy, amt = parts[1].upper(), float(parts[2])
        src_type = "own"
        rest = parts[3:]
        if rest and rest[0].lower() in ("own", "credit"):
            src_type = rest[0].lower(); rest = rest[1:]
        note = " ".join(rest) if rest else (f"repay {src_type}" if src_type == "credit" else "withdrawal")
        u = update.effective_user
        withdraw_liquidity(ccy, amt, note, src_type=src_type, tg_id=u.id, manager=u.username or u.first_name or "")
        audit(u.id, "liquidity_withdraw", f"-{amt} {ccy} [{src_type}] ({note})")
        emoji = "💸" if src_type == "own" else "🔁"
        await update.message.reply_text(
            f"{emoji} -{amt:,.2f} {ccy} [{src_type}]\n{note}",
            reply_markup=InlineKeyboardMarkup(BK))
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

async def auth_code_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip().upper(); u = update.effective_user
    con = get_db()
    try:
        row = con.execute("SELECT * FROM auth_codes WHERE code=? AND used=0", (code,)).fetchone()
        if row:
            con.execute("UPDATE auth_codes SET used=1 WHERE code=?", (code,))
            con.execute("INSERT OR REPLACE INTO users (tg_id,username,name,role) VALUES (?,?,?,'manager')",
                        (u.id, u.username or "", u.full_name))
            con.commit(); audit(u.id, "auth", code)
            await update.message.reply_text(f"✅ {u.first_name}!")
            await show_main_menu(update, ctx); return ConversationHandler.END
    finally: con.close()
    await update.message.reply_text("Неверный код:"); return AUTH_CODE

# ── Deal Flow ──
async def cb_new_deal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_authorized(q.from_user.id):
        await q.edit_message_text("Нет доступа."); return ConversationHandler.END
    kb = [[InlineKeyboardButton(v, callback_data=f"type_{k}")] for k,v in DEAL_TYPES.items()]
    kb.append([InlineKeyboardButton("Отмена", callback_data="cancel_deal")])
    await q.edit_message_text("Тип сделки:", reply_markup=InlineKeyboardMarkup(kb)); return DEAL_TYPE

async def deal_type_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["deal_type"] = q.data.replace("type_","")
    kb = [[InlineKeyboardButton("🟢 Наш клиент", callback_data="ctype_organic"),
           InlineKeyboardButton("🔴 Реклама", callback_data="ctype_ads")]]
    await q.edit_message_text("Источник клиента:", reply_markup=InlineKeyboardMarkup(kb))
    return DEAL_CLIENT_TYPE

async def deal_client_type_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["client_type"] = q.data.replace("ctype_","")
    await q.edit_message_text("Имя клиента (или 2 — пропустить):")
    return DEAL_CLIENT_NAME

async def deal_client_name_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    ctx.user_data["client_name"] = None if raw == "2" else raw
    kb = [[InlineKeyboardButton(c, callback_data=f"from_{c}")] for c in CURRENCIES]
    await update.message.reply_text("Клиент отдаёт:", reply_markup=InlineKeyboardMarkup(kb))
    return DEAL_FROM_CCY

async def deal_from_ccy_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["from_ccy"] = q.data.replace("from_","")
    await q.edit_message_text(f"Сумма {ctx.user_data['from_ccy']}:"); return DEAL_FROM_AMT

async def deal_from_amt_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        a = float(update.message.text.replace(",",".").strip())
        if a <= 0: raise ValueError
    except: await update.message.reply_text(">0:"); return DEAL_FROM_AMT
    ctx.user_data["from_amt"] = a
    avail = [c for c in CURRENCIES if c != ctx.user_data["from_ccy"]]
    kb = [[InlineKeyboardButton(c, callback_data=f"to_{c}")] for c in avail]
    await update.message.reply_text("Клиент получает:", reply_markup=InlineKeyboardMarkup(kb)); return DEAL_TO_CCY

async def deal_to_ccy_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["to_ccy"] = q.data.replace("to_","")
    fc, tc = ctx.user_data["from_ccy"], ctx.user_data["to_ccy"]
    hint = ""
    pr = compute_dynamic_pricing(tc if fc == "USDT" else (fc if tc == "USDT" else tc))
    if not isinstance(pr, dict) or "error" in pr:
        pass
    elif fc == "USDT" and "suggested_sell" in pr:
        hint = f"\n💡 Рек. курс: {pr['suggested_sell']:.4f} {tc}/USDT"
    elif tc == "USDT" and "suggested_buy" in pr:
        hint = f"\n💡 Рек. курс: {pr['suggested_buy']:.4f} {fc}/USDT"
    # Show available balance warning
    av = get_available()
    avail_to = av.get(tc, 0)
    warn = f"\n⚠️ Доступно {tc}: {avail_to:,.2f}" if avail_to < 1000 else ""
    await q.edit_message_text(f"Сумма {tc}:{hint}{warn}"); return DEAL_TO_AMT

async def deal_to_amt_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        a = float(update.message.text.replace(",",".").strip())
        if a <= 0: raise ValueError
    except: await update.message.reply_text(">0:"); return DEAL_TO_AMT
    ctx.user_data["to_amt"] = a
    d = ctx.user_data
    mkt = get_rate(d["from_ccy"], d["to_ccy"])
    sp = calc_spread(d["from_ccy"], d["from_amt"], d["to_ccy"], d["to_amt"], mkt)
    d.update(deal_rate=sp["deal_rate"], market_rate=mkt, margin_our=sp["margin_our"], spread_pct=sp["spread_pct"])
    await update.message.reply_text("📅 Дата сделки (YYYY-MM-DD HH:MM) или 2 (сейчас):")
    return DEAL_CUSTOM_DATE


async def deal_custom_date_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    if raw == "2":
        ctx.user_data["custom_date"] = None
    else:
        for fmt in ["%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M", "%Y-%m-%d"]:
            try:
                ctx.user_data["custom_date"] = datetime.strptime(raw, fmt).strftime("%Y-%m-%d %H:%M:%S")
                break
            except ValueError:
                continue
        else:
            await update.message.reply_text("Формат: 2026-03-25 14:30 или 2:")
            return DEAL_CUSTOM_DATE
    d = ctx.user_data
    mkt = d.get("market_rate")
    hint = f"\n💡 Бот: {mkt:.4f}" if mkt else ""
    pair = f"{d['from_ccy']}/{d['to_ccy']}"
    await update.message.reply_text(
        f"📊 XE-курс для {pair} (1 {d['from_ccy']} = ? {d['to_ccy']})\n"
        f"Введи курс или 2 (курс бота):{hint}")
    return DEAL_XE_RATE


async def deal_xe_rate_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    d = ctx.user_data
    bot_rate = d.get("market_rate")  # original bot rate
    d["bot_rate"] = bot_rate  # save for display
    if raw == "2":
        pass  # keep market_rate as is
    else:
        try:
            xe = float(raw.replace(",", "."))
            if xe <= 0: raise ValueError
        except:
            mkt = bot_rate
            hint = f"\n💡 Бот: {mkt:.4f}" if mkt else ""
            await update.message.reply_text(f"Число > 0 или 2:{hint}")
            return DEAL_XE_RATE
        d["market_rate"] = xe  # XE replaces market_rate → affects margin
    # Recalculate spread with (possibly new) market_rate
    sp = calc_spread(d["from_ccy"], d["from_amt"], d["to_ccy"], d["to_amt"], d.get("market_rate"))
    d.update(deal_rate=sp["deal_rate"], margin_our=sp["margin_our"], spread_pct=sp["spread_pct"])
    await update.message.reply_text("🔗 TX Hash (или 2 — пропустить):")
    return DEAL_TX_HASH


async def deal_tx_hash_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    d = ctx.user_data
    d["tx_hash"] = None if raw == "2" else raw
    sp = calc_spread(d["from_ccy"], d["from_amt"], d["to_ccy"], d["to_amt"], d.get("market_rate"))
    margin = sp["margin_our"]
    sl = ""
    if margin is not None:
        sl = f"\n{'✅' if margin>=0 else '⚠️ УБЫТОК'} Маржа: {margin:+.2f} {d['to_ccy']} ({sp['spread_pct']:+.2f}%)"
    mkt = d.get("market_rate")
    ml = f"\nXE-курс: {mkt:.4f}" if mkt else "\nРынок: н/д"
    br = d.get("bot_rate")
    if br and mkt and abs(br - mkt) > 0.0001:
        ml += f"\nКурс бота: {br:.4f}"
    av = get_available()
    avail_to = av.get(d["to_ccy"], 0)
    liq = ""
    if avail_to < d["to_amt"]:
        liq = f"\n⚠️ ВНИМАНИЕ: доступно только {avail_to:,.2f} {d['to_ccy']}!"
    date_lbl = d.get("custom_date") or "сейчас"
    sep = "━━━━━━━━━━━━━━━━"
    text = (f"Подтверди:\n\n{DEAL_TYPES.get(d['deal_type'],d['deal_type'])}\n"
            f"{sep}\n"
            f"Отдаёт: {d['from_amt']:,.2f} {d['from_ccy']}\n"
            f"Получает: {d['to_amt']:,.2f} {d['to_ccy']}\n"
            f"Курс: {sp['deal_rate']:.4f}{ml}\n"
            f"{sep}{sl}{liq}\n"
            f"📅 Дата: {date_lbl}")
    tx = d.get("tx_hash")
    if tx: text += f"\n🔗 TX: {tx}"
    kb = [[InlineKeyboardButton("✅ Записать", callback_data="confirm_deal"),
           InlineKeyboardButton("❌ Отмена", callback_data="cancel_deal")]]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb)); return DEAL_CONFIRM

async def deal_confirm_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Confirm → save immediately, no receipt flow."""
    q = update.callback_query; await q.answer()
    ctx.user_data.update(method=None, address=None, fee=0, fee_ccy=None)
    return await _save(update, ctx)


async def _save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    d = ctx.user_data; tid = update.effective_user.id; mgr = get_user_name(tid)
    try:
        # create_order делает несколько синхронных запросов к SQLite + reconcile —
        # уводим в поток, чтобы не блокировать event loop бота
        oid = await asyncio.to_thread(
            create_order,
            tg_id=tid, manager=mgr, deal_type=d.get("deal_type",""),
            from_ccy=d.get("from_ccy",""), from_amt=d.get("from_amt",0),
            to_ccy=d.get("to_ccy",""), to_amt=d.get("to_amt",0),
            market_rate=d.get("market_rate"), method=d.get("method"),
            address=d.get("address"), fee=d.get("fee",0),
            fee_ccy=d.get("fee_ccy"), tx_hash=d.get("tx_hash"),
            auto_complete=True, custom_created_at=d.get("custom_date"),
            client_type=d.get("client_type", "organic"), client_name=d.get("client_name"))
    except (ValueError, RuntimeError, sqlite3.Error) as e:
        log.error(f"Order failed: {e}")
        kb = [[InlineKeyboardButton("+ Повторить", callback_data="new_deal")],
              [InlineKeyboardButton("Меню", callback_data="main_menu")]]
        err_text = f"❌ Ошибка: {e}"
        if update.message:
            await update.message.reply_text(err_text, reply_markup=InlineKeyboardMarkup(kb))
        else:
            await update.callback_query.edit_message_text(err_text, reply_markup=InlineKeyboardMarkup(kb))
        ctx.user_data.clear()
        return ConversationHandler.END

    d["deal_rate"] = d.get("deal_rate", (d.get("to_amt",0)/d.get("from_amt",1)) if d.get("from_amt") else 0)
    kb = [[InlineKeyboardButton("➕ Ещё", callback_data="new_deal"),
           InlineKeyboardButton("📢 В канал", callback_data=f"post_ch_{oid}")],
          [InlineKeyboardButton("↩ Отменить", callback_data=f"undo_yes_{oid}"),
           InlineKeyboardButton("Меню", callback_data="main_menu")]]
    try:
        receipt = format_receipt(oid, d, mgr) + f"\n\n{random.choice(DEAL_DONE_PHRASES)}"
    except Exception as e:
        # сделка УЖЕ записана — сбой форматирования чека не ошибка сохранения
        log.error(f"Receipt build failed for #{oid}: {e}", exc_info=True)
        receipt = f"✅ Сделка #{oid} записана."
    try:
        if update.message:
            await update.message.reply_text(receipt, reply_markup=InlineKeyboardMarkup(kb),
                                            parse_mode="Markdown", disable_web_page_preview=True)
        else:
            await update.callback_query.edit_message_text(receipt, reply_markup=InlineKeyboardMarkup(kb),
                                                           parse_mode="Markdown", disable_web_page_preview=True)
    except Exception:
        # сделка УЖЕ записана — чек обязан дойти даже если Markdown сломался
        try:
            chat_id = update.effective_chat.id
            await ctx.bot.send_message(chat_id, receipt, reply_markup=InlineKeyboardMarkup(kb),
                                       disable_web_page_preview=True)
        except Exception as e:
            log.error(f"Receipt delivery failed for #{oid}: {e}", exc_info=True)
    ctx.user_data.clear(); return ConversationHandler.END

async def deal_cancel_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); ctx.user_data.clear()
    kb = [[InlineKeyboardButton("+ Сделка", callback_data="new_deal"),
           InlineKeyboardButton("Меню", callback_data="main_menu")]]
    await q.edit_message_text(f"Отменено. {random.choice(DEAL_CANCEL_PHRASES)}", reply_markup=InlineKeyboardMarkup(kb)); return ConversationHandler.END


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/cancel — выйти из любого диалога FSM."""
    ctx.user_data.clear()
    await update.message.reply_text("Отменено. /start — меню.")
    return ConversationHandler.END


def _fallback_end(fn):
    """Обёртка fallback'ов FSM: выполнить и гарантированно закрыть диалог
    (раньше «Меню» посреди ввода оставляло состояние висеть)."""
    async def _wrapped(update, ctx):
        res = await fn(update, ctx)
        ctx.user_data.clear()
        return ConversationHandler.END if res is None else res
    return _wrapped

# ── Remaining handlers ──
async def cb_manual_rate_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_authorized(q.from_user.id):
        await q.edit_message_text("Введи /start"); return
    rates = {r["pair"]: r for r in get_all_rates()}
    lines = ["Курсы:\n"]
    for p in MANUAL_RATE_PAIRS:
        r = rates.get(p)
        lines.append(f"{'🟣' if r and r['source']=='manual' else '⚪'} {p} {r['rate']:.4f}" if r else f"  {p} —")
    kb = [[InlineKeyboardButton(p, callback_data=f"mrate_{p}")] for p in MANUAL_RATE_PAIRS]
    kb.append([InlineKeyboardButton("Назад", callback_data="main_menu")])
    await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb)); return MANUAL_RATE_PAIR

async def manual_rate_pair_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    p = q.data.replace("mrate_",""); ctx.user_data["mrp"] = p
    c = get_rate(*p.split("/")); cs = f" ({c:.4f})" if c else ""
    await q.edit_message_text(f"Курс {p}{cs}:"); return MANUAL_RATE_VALUE

async def manual_rate_value_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        r = float(update.message.text.replace(",",".").strip())
        if r <= 0: raise ValueError
    except: await update.message.reply_text(">0:"); return MANUAL_RATE_VALUE
    p = ctx.user_data.get("mrp",""); set_manual_rate(p, r)
    audit(update.effective_user.id, "rate", f"{p}={r}"); ctx.user_data.clear()
    kb = [[InlineKeyboardButton("Ещё",callback_data="manual_rate_menu"),InlineKeyboardButton("Меню",callback_data="main_menu")]]
    await update.message.reply_text(f"✅ {p}={r:.4f}", reply_markup=InlineKeyboardMarkup(kb)); return ConversationHandler.END

async def cb_notes_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    kb = [[InlineKeyboardButton("+",callback_data="note_add"),InlineKeyboardButton("📋",callback_data="notes_view")],
          [InlineKeyboardButton("Назад",callback_data="main_menu")]]
    await q.edit_message_text("Заметки:", reply_markup=InlineKeyboardMarkup(kb))

async def cb_note_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_authorized(q.from_user.id):
        await q.edit_message_text("Введи /start"); return
    await q.edit_message_text("Текст:"); return NOTE_TEXT

async def note_text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t = update.message.text.strip(); m = get_user_name(update.effective_user.id)
    db_exec("INSERT INTO notes (tg_id,manager,text) VALUES (?,?,?)", (update.effective_user.id,m,t), fetch=None)
    await update.message.reply_text(f"✅ {t}", reply_markup=InlineKeyboardMarkup(BK)); return ConversationHandler.END

async def cb_notes_view(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ns = db_exec("SELECT manager,text,created_at FROM notes ORDER BY created_at DESC LIMIT 15")
    t = "\n".join(f"[{n['created_at'][:16]}] {n['manager']}: {n['text']}" for n in ns) if ns else "Пусто."
    await q.edit_message_text(t[:4000], reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩",callback_data="notes_menu")]]))

async def cb_receipts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    kb = [[InlineKeyboardButton("5",callback_data="rcpt_5"),InlineKeyboardButton("10",callback_data="rcpt_10")],
          [InlineKeyboardButton("15",callback_data="rcpt_15")],[InlineKeyboardButton("Назад",callback_data="main_menu")]]
    await q.edit_message_text("🧾 Чеки:", reply_markup=InlineKeyboardMarkup(kb))

async def cb_receipts_show(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    n = int(q.data.replace("rcpt_",""))
    deals = db_exec("SELECT * FROM orders WHERE state='completed' ORDER BY created_at DESC LIMIT ?", (n,))
    if not deals:
        await q.edit_message_text("Нет.", reply_markup=InlineKeyboardMarkup(BK)); return
    for i,d in enumerate(deals):
        r = format_receipt_from_db(d)
        try:
            if i==0: await q.edit_message_text(r, parse_mode="Markdown", disable_web_page_preview=True)
            else: await ctx.bot.send_message(q.message.chat_id, r, parse_mode="Markdown", disable_web_page_preview=True)
        except Exception:
            await ctx.bot.send_message(q.message.chat_id, r, disable_web_page_preview=True)
    kb=[[InlineKeyboardButton("🧾",callback_data="receipts"),InlineKeyboardButton("Меню",callback_data="main_menu")]]
    await ctx.bot.send_message(q.message.chat_id, "─"*10, reply_markup=InlineKeyboardMarkup(kb))

async def cb_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    eq, bd = compute_equity_usdt(); avl = get_available(); res = get_reserved()
    pend = get_pending_in()
    first_row = db_exec("SELECT MIN(ts) mn FROM ledger_entries", fetch="one")
    first_ts = (first_row["mn"] or "")[:10] if first_row else ""
    liq = get_liquidity_summary()
    lines = [f"📊 *Баланс* (с {first_ts})\n"]
    for ccy in CURRENCIES:
        b=bd.get(ccy,{}); bal=b.get("balance",0); rv=res.get(ccy,0); av=avl.get(ccy,0)
        pi=pend.get(ccy,0)
        line = f"  *{ccy}*: {bal:+,.2f}"
        if rv > 0: line += f" | рез:{rv:,.0f}"
        if pi > 0: line += f" | ожид:{pi:,.0f}"
        line += f" | дост:{av:+,.2f}"
        lines.append(line)
    # External liquidity breakdown (own vs credit per ccy)
    own_d = liq.get("own", {}); cr_d = liq.get("credit", {})
    if own_d or cr_d:
        lines.append("\n🔄 *Внешняя ликвидность:*")
        for ccy in sorted(set(list(own_d.keys()) + list(cr_d.keys()))):
            own = own_d.get(ccy, 0); cr = cr_d.get(ccy, 0)
            bits = []
            if abs(own) > 1e-6: bits.append(f"свои {own:+,.2f}")
            if abs(cr)  > 1e-6: bits.append(f"кредит {cr:+,.2f}")
            if bits: lines.append(f"  {ccy}: " + " | ".join(bits))
        lines.append(f"  _итого: свои≈{liq.get('total_own_usdt',0):,.2f} USDT, "
                     f"кредит≈{liq.get('total_credit_usdt',0):,.2f} USDT_")
    lines.append(f"\n💰 *Equity*: {eq:,.2f} USDT")
    lines.append(f"\n_Команды: /add\\_liquidity, /withdraw, /liq\\_list_")
    tip = _analyze_balance(eq, bd, avl)
    if tip:
        lines.extend(["", f"🤖 {tip}"])
    await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(BK),
                              parse_mode="Markdown")

async def cb_stats_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    kb=[[InlineKeyboardButton("День",callback_data="stats_day"),InlineKeyboardButton("Неделя",callback_data="stats_week")],
        [InlineKeyboardButton("Месяц",callback_data="stats_month"),InlineKeyboardButton("Всё",callback_data="stats_all")],
        [InlineKeyboardButton("Назад",callback_data="main_menu")]]
    await q.edit_message_text("Статистика:", reply_markup=InlineKeyboardMarkup(kb))

async def cb_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    p = q.data.replace("stats_",""); lb = {"day":"День","week":"Нед","month":"Мес","all":"Всё"}
    s = await asyncio.to_thread(get_stats, p)
    if not s.get("deal_count"): t = f"{lb.get(p,p)}: нет сделок"
    else:
        t = (f"📈 {lb.get(p,p)}\n\nСделок: {s['deal_count']}\nОбъём: {s['volume_usdt']:,.2f} USDT\n"
             f"Маржа: {s['total_margin']:+,.2f} USDT\nСпред: {s['avg_spread_pct']:+.2f}%")
        tip = _analyze_stats(s, lb.get(p,p))
        if tip: t += f"\n\n🤖 {tip}"
    await q.edit_message_text(t, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩",callback_data="stats_menu")]]))

async def cb_rates(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    await q.edit_message_text("..."); await asyncio.to_thread(refresh_rates)
    lines = ["Курсы:\n"]
    for r in get_all_rates():
        lines.append(f"  {'🟣' if r['source']=='manual' else '⚪'} {r['pair']:<12} {r['rate']:.4f}")
    kb=[[InlineKeyboardButton("Ввести",callback_data="manual_rate_menu")],[InlineKeyboardButton("Назад",callback_data="main_menu")]]
    await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))

async def cb_report_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    kb=[[InlineKeyboardButton("День",callback_data="report_day"),InlineKeyboardButton("Нед",callback_data="report_week")],
        [InlineKeyboardButton("Мес",callback_data="report_month"),InlineKeyboardButton("Год",callback_data="report_year")],
        [InlineKeyboardButton("Назад",callback_data="main_menu")]]
    await q.edit_message_text("Excel:", reply_markup=InlineKeyboardMarkup(kb))

async def cb_report_dispatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_authorized(q.from_user.id):
        await q.edit_message_text("Введи /start"); return
    p = q.data.replace("report_",""); await q.edit_message_text("⏳...")
    try:
        buf = await asyncio.to_thread(generate_excel_report, p)
        await ctx.bot.send_document(q.message.chat_id, buf,
            filename=f"SwapMe_{p}_{datetime.now().strftime('%Y-%m-%d')}.xlsx")
        audit(q.from_user.id, "report", p)
    except Exception as e:
        log.error(f"Excel: {e}", exc_info=True)
        await ctx.bot.send_message(q.message.chat_id, f"❌ {e}")

async def cb_history_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    kb=[[InlineKeyboardButton("Все",callback_data="hist_all"),InlineKeyboardButton("День",callback_data="hist_day")],
        [InlineKeyboardButton("EUR",callback_data="hist_ccy_EUR"),InlineKeyboardButton("USDT",callback_data="hist_ccy_USDT")],
        [InlineKeyboardButton("CZK",callback_data="hist_ccy_CZK"),InlineKeyboardButton("USD",callback_data="hist_ccy_USD")],
        [InlineKeyboardButton("Назад",callback_data="main_menu")]]
    await q.edit_message_text("История:", reply_markup=InlineKeyboardMarkup(kb))

async def cb_history_show(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    f = q.data.replace("hist_","")
    if f=="all": ds = db_exec("SELECT * FROM orders WHERE state='completed' ORDER BY created_at DESC LIMIT 10"); tt="Все"
    elif f=="day": ds = db_exec("SELECT * FROM orders WHERE created_at>=? AND state='completed' ORDER BY created_at DESC",(period_since("day"),)); tt="День"
    elif f.startswith("ccy_"):
        c=f[4:]; ds = db_exec("SELECT * FROM orders WHERE (from_ccy=? OR to_ccy=?) AND state='completed' ORDER BY created_at DESC LIMIT 15",(c,c)); tt=c
    else: ds=[]; tt="—"
    if not ds: text=f"{tt}: нет"
    else:
        lines=[f"{tt} ({len(ds)}):\n"]
        for d in ds:
            m=_safe(d,"margin_our",None); ms=f" [{m:+.2f}]" if m is not None else ""
            ic = "📈" if m is not None and m >= 0 else ("📉" if m is not None else "➡️")
            lines.append(f"{ic} #{_safe(d,'id',0)} {(_safe(d,'created_at','') or '')[5:16]} "
                         f"{_safe(d,'to_amt',0):,.0f}{_safe(d,'to_ccy','')}→"
                         f"{_safe(d,'from_amt',0):,.0f}{_safe(d,'from_ccy','')}{ms}")
        text="\n".join(lines)
    kb=[[InlineKeyboardButton("Фильтры",callback_data="history"),InlineKeyboardButton("Меню",callback_data="main_menu")]]
    await q.edit_message_text(text[:4000], reply_markup=InlineKeyboardMarkup(kb))

async def cb_trends(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    kb=[[InlineKeyboardButton("1ч",callback_data="trend_1"),InlineKeyboardButton("24ч",callback_data="trend_24")],
        [InlineKeyboardButton("7д",callback_data="trend_168"),InlineKeyboardButton("Назад",callback_data="main_menu")]]
    await q.edit_message_text("Тренды:", reply_markup=InlineKeyboardMarkup(kb))

async def cb_trend_show(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    h=int(q.data.replace("trend_","")); lb={1:"1ч",24:"24ч",168:"7д"}.get(h,f"{h}")
    lines=[f"Тренды {lb}:\n"]
    for p,t in get_all_trends(h):
        rn=t.get("rate_now",0); tp=t.get("trend_pct")
        if tp is None: lines.append(f"  {p:<12} {rn:.4f} [—]")
        else: lines.append(f"  {p:<12} {rn:.4f} {'↑'if tp>0 else('↓'if tp<0 else'→')} {tp:+.2f}%")
    kb=[[InlineKeyboardButton("↩",callback_data="trends"),InlineKeyboardButton("Меню",callback_data="main_menu")]]
    await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))

async def cb_pnl_vwap(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    kb=[[InlineKeyboardButton("Неделя",callback_data="pnl_week"),InlineKeyboardButton("Месяц",callback_data="pnl_month")],
        [InlineKeyboardButton("Квартал",callback_data="pnl_quarter"),InlineKeyboardButton("Год",callback_data="pnl_year")],
        [InlineKeyboardButton("Всё время",callback_data="pnl_all")],
        [InlineKeyboardButton("Назад",callback_data="main_menu")]]
    await q.edit_message_text("💰 P&L — выбери период:", reply_markup=InlineKeyboardMarkup(kb))

_PNL_LBL = {"week": "Неделя", "month": "Месяц", "quarter": "Квартал",
            "year": "Год", "all": "Всё время"}


async def cb_pnl_show(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Краткая P&L-сводка (7 строк); полная разбивка — кнопка «🔍 Детали»."""
    q = update.callback_query; await q.answer()
    p = q.data.replace("pnl_", "")
    lbl = _PNL_LBL.get(p, p)
    await q.edit_message_text(f"⏳ Считаю P&L ({lbl})...")
    _zero = {"vwap": 0, "market_rate": 0, "inventory": 0,
             "realized_pnl": 0, "unrealized_pnl": 0, "total_pnl": 0}
    try: vc = await asyncio.to_thread(get_vwap_pnl, "USDT", "CZK", p)
    except Exception as e: log.error(f"VWAP CZK: {e}"); vc = _zero
    try: ve = await asyncio.to_thread(get_vwap_pnl, "USDT", "EUR", p)
    except Exception as e: log.error(f"VWAP EUR: {e}"); ve = _zero
    hw = await asyncio.to_thread(get_hwm_stats)
    s = await asyncio.to_thread(get_stats, p)
    dsp = await asyncio.to_thread(get_deal_spread_profit, p)
    fp = lambda v, u="": f"{'+' if v >= 0 else ''}{v:,.2f}{u}"
    lines = [f"💰 P&L — {lbl}", "",
             f"Итог CZK: {fp(vc['total_pnl'], ' CZK')}",
             f"Итог EUR: {fp(ve['total_pnl'], ' EUR')}",
             f"Equity: {hw['equity_now']:,.0f} USDT (DD {hw['drawdown_pct']:+.1f}%)",
             f"Сделок: {s['deal_count']} · Маржа: {fp(s['total_margin'], ' USDT')}",
             f"Спред-прибыль: {fp(dsp['spread_profit'], ' USDT')} ({dsp['deals']}сд)"]
    try:
        tip = _analyze_pnl(vc, ve, hw, s, dsp, lbl)
        if tip:
            lines += ["", f"🤖 {tip}"]
    except Exception:
        pass
    kb = [[InlineKeyboardButton("🔍 Детали", callback_data=f"pnl_full_{p}")],
          [InlineKeyboardButton("↩ Периоды", callback_data="pnl_vwap"),
           InlineKeyboardButton("Меню", callback_data="main_menu")]]
    await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))

async def cb_pnl_full(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Полная P&L-разбивка (бывший cb_pnl_show). Pattern: ^pnl_full_…$"""
    q = update.callback_query; await q.answer()
    p = q.data.replace("pnl_full_","")
    lbl = {"week":"Неделя","month":"Месяц","quarter":"Квартал","year":"Год","all":"Всё время"}.get(p,p)
    await q.edit_message_text(f"⏳ Считаю P&L ({lbl})...")
    _zero_pnl = {"vwap":0,"market_rate":0,"inventory":0,"realized_pnl":0,"unrealized_pnl":0,"total_pnl":0}
    try: vc=await asyncio.to_thread(get_vwap_pnl,"USDT","CZK",p)
    except Exception as e: log.error(f"VWAP CZK: {e}"); vc=_zero_pnl
    try: ve=await asyncio.to_thread(get_vwap_pnl,"USDT","EUR",p)
    except Exception as e: log.error(f"VWAP EUR: {e}"); ve=_zero_pnl
    _zero_simple = {"avg_buy":0,"avg_sell":0,"buy_qty":0,"sell_qty":0,"matched_qty":0,"remaining":0,"realized_pnl":0,"unrealized_pnl":0,"total_pnl":0,"market_rate":0}
    try: sc=await asyncio.to_thread(get_simple_pnl,"USDT","CZK",p)
    except Exception as e: log.error(f"Simple CZK: {e}"); sc=_zero_simple
    try: se=await asyncio.to_thread(get_simple_pnl,"USDT","EUR",p)
    except Exception as e: log.error(f"Simple EUR: {e}"); se=_zero_simple
    hw=await asyncio.to_thread(get_hwm_stats)
    s=await asyncio.to_thread(get_stats, p)
    dsp=await asyncio.to_thread(get_deal_spread_profit, p)
    fp=lambda v,u="": f"{'+'if v>=0 else''}{v:,.2f}{u}"
    lines=[f"💰 P&L — {lbl}\n",
           "── USDT/CZK ──",f"  VWAP: {vc['vwap']:.4f}",f"  Рынок: {vc['market_rate']:.4f}",
           f"  Инвент: {vc['inventory']:,.2f}",f"  Реализ: {fp(vc['realized_pnl'],' CZK')}",
           f"  Нереализ: {fp(vc['unrealized_pnl'],' CZK')}",
           f"  Итого: {fp(vc['total_pnl'],' CZK')}","",
           "── USDT/EUR ──",f"  VWAP: {ve['vwap']:.4f}",f"  Рынок: {ve['market_rate']:.4f}",
           f"  Инвент: {ve['inventory']:,.2f}",f"  Реализ: {fp(ve['realized_pnl'],' EUR')}",
           f"  Нереализ: {fp(ve['unrealized_pnl'],' EUR')}",
           f"  Итого: {fp(ve['total_pnl'],' EUR')}","",
           "── Капитал ──",f"  Equity: {hw['equity_now']:,.2f} USDT",
           f"  HWM: {hw['hwm']:,.2f}",f"  DD: {hw['drawdown_pct']:+.2f}%","",
           f"── Маржа ({lbl}) ──",
           f"  Сделок: {s['deal_count']}",
           f"  Объём: {s['volume_usdt']:,.2f} USDT",
           f"  Маржа: {fp(s['total_margin'],' USDT')}",
           f"  Спред: {s['avg_spread_pct']:+.2f}%",
           f"  Спред-прибыль: {fp(dsp['spread_profit'],' USDT')} ({dsp['deals']}сд)",
           "",
           "── Simple PnL (USDT/CZK) ──",
           f"  Ср.покупка: {sc['avg_buy']:.4f}",
           f"  Ср.продажа: {sc['avg_sell']:.4f}",
           f"  Куплено: {sc['buy_qty']:,.2f} USDT",
           f"  Продано: {sc['sell_qty']:,.2f} USDT",
           f"  Реализ: {fp(sc['realized_pnl'],' CZK')}",
           f"  Нереализ: {fp(sc['unrealized_pnl'],' CZK')}",
           f"  Итого: {fp(sc['total_pnl'],' CZK')}",
           "",
           "── Simple PnL (USDT/EUR) ──",
           f"  Ср.покупка: {se['avg_buy']:.4f}",
           f"  Ср.продажа: {se['avg_sell']:.4f}",
           f"  Куплено: {se['buy_qty']:,.2f} USDT",
           f"  Продано: {se['sell_qty']:,.2f} USDT",
           f"  Реализ: {fp(se['realized_pnl'],' EUR')}",
           f"  Нереализ: {fp(se['unrealized_pnl'],' EUR')}",
           f"  Итого: {fp(se['total_pnl'],' EUR')}"]
    tip = _analyze_pnl(vc, ve, hw, s, dsp, lbl)
    if tip:
        lines.extend(["", f"🤖 {tip}"])
    kb=[[InlineKeyboardButton("↩ Кратко",callback_data=f"pnl_{p}")],
        [InlineKeyboardButton("↩ Периоды",callback_data="pnl_vwap"),InlineKeyboardButton("Меню",callback_data="main_menu")]]
    await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))

async def cb_liquidity(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    rpt=get_liquidity_report(7)
    lines=["🔄 Ликвидность 7д\n"]
    for ccy in CURRENCIES:
        d=rpt.get(ccy,{})
        ic={"OK":"🟢","НИЗКАЯ":"🟡","КРИТИЧНО":"🔴","ПУСТО":"⚫"}.get(d.get("risk",""),"⚪")
        lines.append(f"{ic} {ccy}: {d.get('current',0):+,.2f} | расх:{d.get('daily_outflow',0):,.0f}/д | {d.get('days_left','∞')}д")
    await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(BK))

async def cb_mgr_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    kb=[[InlineKeyboardButton("День",callback_data="mgr_day"),InlineKeyboardButton("Нед",callback_data="mgr_week")],
        [InlineKeyboardButton("Мес",callback_data="mgr_month"),InlineKeyboardButton("Назад",callback_data="main_menu")]]
    await q.edit_message_text("👥:", reply_markup=InlineKeyboardMarkup(kb))

async def cb_mgr_show(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    p=q.data.replace("mgr_",""); rows=get_manager_stats(p)
    if not rows: text="Нет."
    else:
        lines=[f"👥 {p}\n"]
        for i,r in enumerate(rows,1):
            lines.append(f"{i}. {r['manager']}: {r['cnt']}сд, маржа {r['margin']:+,.0f}")
        text="\n".join(lines)
    kb=[[InlineKeyboardButton("↩",callback_data="mgr_stats"),InlineKeyboardButton("Меню",callback_data="main_menu")]]
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def cb_pricing(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    lines=["💡 Ценообразование\n"]
    for qc in ["CZK","EUR"]:
        p=compute_dynamic_pricing(qc)
        if "error" in p: lines.append(f"  USDT/{qc}: н/д"); continue
        cs = _custom_spread.get(qc)
        cs_lbl = f" ✏️{cs:.2f}%" if cs else " (авто)"
        lines.extend([f"── USDT/{qc} ──",f"  Рынок: {p['mid_rate']:.4f}",
            f"  🟢 Покупай: {p['suggested_buy']:.4f} (-{p['spread_buy_pct']:.2f}%)",
            f"  🔴 Продавай: {p['suggested_sell']:.4f} (+{p['spread_sell_pct']:.2f}%)",
            f"  Спред: {p['total_spread_pct']:.2f}%{cs_lbl}",""])
    kb=[[InlineKeyboardButton("✏️ Спред CZK",callback_data="cspread_CZK"),
         InlineKeyboardButton("✏️ Спред EUR",callback_data="cspread_EUR")],
        [InlineKeyboardButton("♻️ Сброс (авто)",callback_data="cspread_reset")],
        [InlineKeyboardButton("Меню",callback_data="main_menu")]]
    await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))

async def cb_intelligence(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    rpt=get_intelligence_report()
    lines=[f"🧠 Аналитика\nEquity: {rpt['equity']:,.2f} USDT | USDT: {rpt['usdt_pct']}%\n"]
    for tag,msg in rpt["signals"]: lines.append(f"{tag}\n  {msg}\n")
    await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(BK))

async def cb_converter(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_authorized(q.from_user.id):
        await q.edit_message_text("Введи /start"); return
    kb=[[InlineKeyboardButton(c,callback_data=f"conv_from_{c}")] for c in CURRENCIES]
    kb.append([InlineKeyboardButton("Назад",callback_data="main_menu")])
    await q.edit_message_text("Из:", reply_markup=InlineKeyboardMarkup(kb)); return CONVERTER_FROM

async def conv_from_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["cf"]=q.data.replace("conv_from_","")
    await q.edit_message_text(f"Сумма {ctx.user_data['cf']}:"); return CONVERTER_AMT

async def conv_amt_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        a=float(update.message.text.replace(",",".").strip())
        if a<=0: raise ValueError
    except: await update.message.reply_text(">0:"); return CONVERTER_AMT
    ctx.user_data["ca"]=a
    avail=[c for c in CURRENCIES if c!=ctx.user_data["cf"]]
    kb=[[InlineKeyboardButton(c,callback_data=f"conv_to_{c}")] for c in avail]
    await update.message.reply_text("В:", reply_markup=InlineKeyboardMarkup(kb)); return CONVERTER_TO

async def conv_to_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    fc=ctx.user_data["cf"]; a=ctx.user_data["ca"]; tc=q.data.replace("conv_to_","")
    r=get_rate(fc,tc)
    t=f"🔄 {a:,.2f} {fc} = {a*r:,.2f} {tc}\n({r:.4f})" if r else f"Нет курса {fc}/{tc}"
    ctx.user_data.clear()
    kb=[[InlineKeyboardButton("Ещё",callback_data="converter"),InlineKeyboardButton("Меню",callback_data="main_menu")]]
    await q.edit_message_text(t, reply_markup=InlineKeyboardMarkup(kb)); return ConversationHandler.END

async def cb_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): return
    panic_lbl = "🟢 Паника ВЫКЛ" if not _panic_mode else "🔴 Паника ВКЛ"
    data_lbl = "🔓 Данные видны" if not _data_hidden else "🔒 Данные скрыты"
    kb=[[InlineKeyboardButton("🔑 Код",callback_data="admin_gencode")],
        [InlineKeyboardButton("👥",callback_data="admin_users"),InlineKeyboardButton("Посл.5",callback_data="admin_last5")],
        [InlineKeyboardButton("🗑",callback_data="admin_delete"),InlineKeyboardButton("📋 Аудит",callback_data="admin_audit")],
        [InlineKeyboardButton("📅 Даты",callback_data="admin_dates"),InlineKeyboardButton("💸 Расходы",callback_data="expenses_menu")],
        [InlineKeyboardButton("💰 Источники",callback_data="liq_sources")],
        [InlineKeyboardButton("✏️ Редактор сделок",callback_data="deal_editor")],
        [InlineKeyboardButton(f"🚨 {panic_lbl}",callback_data="sec_panic")],
        [InlineKeyboardButton(f"🛡 {data_lbl}",callback_data="sec_data")],
        [InlineKeyboardButton("Назад",callback_data="main_menu")]]
    await q.edit_message_text("⚙️ Админ", reply_markup=InlineKeyboardMarkup(kb))

async def cb_admin_gencode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_admin(q.from_user.id):
        await q.edit_message_text("Нет доступа."); return
    c="".join(random.choices(string.ascii_uppercase+string.digits,k=8))
    db_exec("INSERT INTO auth_codes (code,created_by) VALUES (?,?)", (c,q.from_user.id), fetch=None)
    audit(q.from_user.id,"gencode",c)
    await q.edit_message_text(f"🔑 {c}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩",callback_data="admin")]]))

async def cb_admin_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_admin(q.from_user.id):
        await q.edit_message_text("Нет доступа."); return
    us=db_exec("SELECT tg_id,name,role,active FROM users")
    t="\n".join(f"{'✅'if u['active'] else'❌'} {u['name']} ({u['role']})" for u in us)
    await q.edit_message_text(t or "—", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩",callback_data="admin")]]))

async def cb_admin_last5(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_admin(q.from_user.id):
        await q.edit_message_text("Нет доступа."); return
    ds=db_exec("SELECT * FROM orders WHERE state='completed' ORDER BY created_at DESC LIMIT 5")
    if not ds: text="Нет."
    else:
        lines=[]
        for d in ds:
            m=_safe(d,"margin_our",None); ms=f"|{m:+.2f}" if m is not None else ""
            ic = "📈" if m is not None and m >= 0 else ("📉" if m is not None else "➡️")
            lines.append(f"{ic} #{_safe(d,'id',0)} {(_safe(d,'created_at','') or '')[:16]} "
                         f"{_safe(d,'to_amt',0):,.0f}{_safe(d,'to_ccy','')}→{_safe(d,'from_amt',0):,.0f}{_safe(d,'from_ccy','')}{ms}")
        text="\n".join(lines)
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩",callback_data="admin")]]))

async def cb_admin_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_admin(q.from_user.id):
        await q.edit_message_text("Нет доступа."); return
    ds=db_exec("SELECT id,created_at,from_amt,from_ccy,to_amt,to_ccy FROM orders WHERE state='completed' ORDER BY created_at DESC LIMIT 5")
    if not ds:
        await q.edit_message_text("Нет.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩",callback_data="admin")]]))
        return
    lines=["ID для отмены:\n"]
    for d in ds: lines.append(f"  #{d['id']} {d['created_at'][:16]} {d['to_amt']:,.0f}{d['to_ccy']}→{d['from_amt']:,.0f}{d['from_ccy']}")
    await q.edit_message_text("\n".join(lines)); return DELETE_DEAL_ID

async def delete_deal_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Нет доступа."); return ConversationHandler.END
    try: did=int(update.message.text.strip())
    except: await update.message.reply_text("ID:"); return DELETE_DEAL_ID
    try:
        await asyncio.to_thread(cancel_order, did, update.effective_user.id)
        await update.message.reply_text(f"🗑 #{did} отменена.", reply_markup=InlineKeyboardMarkup(BK))
    except (ValueError, RuntimeError, sqlite3.Error) as e:
        await update.message.reply_text(str(e)); return DELETE_DEAL_ID
    return ConversationHandler.END

async def cb_admin_audit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_admin(q.from_user.id):
        await q.edit_message_text("Нет доступа."); return
    rs=db_exec("SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 15")
    t="\n".join(f"{r['created_at'][:16]} {r['username']}: {r['action']}" for r in rs) if rs else "—"
    await q.edit_message_text(t[:4000], reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩",callback_data="admin")]]))

async def cb_cashflow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    kb=[[InlineKeyboardButton("День",callback_data="cf_day"),InlineKeyboardButton("Нед",callback_data="cf_week")],
        [InlineKeyboardButton("Мес",callback_data="cf_month"),InlineKeyboardButton("Назад",callback_data="main_menu")]]
    await q.edit_message_text("💰 Cashflow:", reply_markup=InlineKeyboardMarkup(kb))

async def cb_cashflow_show(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    p = q.data.replace("cf_","")
    lb = {"day":"День","week":"Неделя","month":"Месяц"}.get(p, p)
    cf = get_cashflow(p)
    if not cf:
        await q.edit_message_text(f"💰 {lb}: нет данных", reply_markup=InlineKeyboardMarkup(BK)); return
    lines = [f"💰 Cashflow — {lb}\n"]
    for ccy in CURRENCIES:
        d = cf.get(ccy)
        if not d: continue
        lines.append(f"  {ccy}:")
        lines.append(f"    ↓ вход: {d['inflow']:+,.2f}")
        lines.append(f"    ↑ выход: {d['outflow']:+,.2f}")
        if d.get('fee') or d.get('fee_income'):
            fee_val = d.get('fee_income', 0) or d.get('fee', 0)
            lines.append(f"    💸 комис: {fee_val:+,.2f}")
        lines.append(f"    = нетто: {d['net']:+,.2f}")
        lines.append("")
    kb=[[InlineKeyboardButton("↩",callback_data="cashflow"),InlineKeyboardButton("Меню",callback_data="main_menu")]]
    await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))

async def cb_admin_dates(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_admin(q.from_user.id):
        await q.edit_message_text("Нет доступа."); return
    ds = db_exec("SELECT id,created_at,completed_at,from_amt,from_ccy,to_amt,to_ccy FROM orders WHERE state='completed' ORDER BY created_at DESC LIMIT 5")
    if not ds:
        await q.edit_message_text("Нет сделок.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩",callback_data="admin")]])); return
    lines = ["📅 Введи ID:\n"]
    for d in ds:
        lines.append(f"  #{d['id']} {(d['created_at'] or '')[:16]}")
        lines.append(f"    {d['to_amt']:,.0f}{d['to_ccy']}→{d['from_amt']:,.0f}{d['from_ccy']}")
    await q.edit_message_text("\n".join(lines)); return EDIT_DATE_ID

async def edit_date_id_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try: oid = int(update.message.text.strip())
    except: await update.message.reply_text("ID:"); return EDIT_DATE_ID
    row = db_exec("SELECT id FROM orders WHERE id=?", (oid,), "one")
    if not row: await update.message.reply_text("Не найдена."); return EDIT_DATE_ID
    ctx.user_data["edit_oid"] = oid
    kb = [[InlineKeyboardButton("Создание", callback_data="edf_created_at"),
           InlineKeyboardButton("Завершение", callback_data="edf_completed_at")]]
    await update.message.reply_text(f"#{oid} — что менять?", reply_markup=InlineKeyboardMarkup(kb))
    return EDIT_DATE_FIELD

async def edit_date_field_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["edit_field"] = q.data.replace("edf_","")
    await q.edit_message_text("Дата (YYYY-MM-DD HH:MM):"); return EDIT_DATE_VALUE

async def edit_date_value_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    for fmt in ["%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M", "%Y-%m-%d"]:
        try:
            new_date = datetime.strptime(raw, fmt).strftime("%Y-%m-%d %H:%M:%S"); break
        except ValueError: continue
    else:
        await update.message.reply_text("Формат: 2026-03-25 14:30"); return EDIT_DATE_VALUE
    oid = ctx.user_data.get("edit_oid"); field = ctx.user_data.get("edit_field")
    try: edit_order_date(oid, field, new_date, update.effective_user.id, force=True)
    except ValueError as e: await update.message.reply_text(str(e)); return EDIT_DATE_VALUE
    ctx.user_data.clear()
    kb = [[InlineKeyboardButton("📅 Ещё",callback_data="admin_dates"),InlineKeyboardButton("Меню",callback_data="main_menu")]]
    await update.message.reply_text(f"✅ #{oid} {field}={new_date}", reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

# ── Expenses ──
async def cb_expenses_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): return
    kb=[[InlineKeyboardButton("➕ Добавить",callback_data="exp_add")],
        [InlineKeyboardButton("📋 Список",callback_data="exp_list")],
        [InlineKeyboardButton("🗑 Удалить",callback_data="exp_del")],
        [InlineKeyboardButton("↩ Админ",callback_data="admin")]]
    await q.edit_message_text("💸 Расходы:", reply_markup=InlineKeyboardMarkup(kb))

async def cb_exp_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): return ConversationHandler.END
    kb=[[InlineKeyboardButton(c,callback_data=f"expccy_{c}")] for c in CURRENCIES]
    kb.append([InlineKeyboardButton("Отмена",callback_data="expenses_menu")])
    await q.edit_message_text("💸 Валюта расхода:", reply_markup=InlineKeyboardMarkup(kb))
    return EXP_CCY

async def exp_ccy_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["exp_ccy"] = q.data.replace("expccy_","")
    await q.edit_message_text(f"Сумма расхода ({ctx.user_data['exp_ccy']}):")
    return EXP_AMT

async def exp_amt_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        a = float(update.message.text.replace(",",".").strip())
        if a <= 0: raise ValueError
    except:
        await update.message.reply_text("Сумма > 0:"); return EXP_AMT
    ctx.user_data["exp_amt"] = a
    await update.message.reply_text("Описание расхода (напр: аренда офис март):")
    return EXP_NOTE

async def exp_note_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    note = update.message.text.strip()
    ccy = ctx.user_data.get("exp_ccy")
    amt = ctx.user_data.get("exp_amt", 0)
    tid = update.effective_user.id
    mgr = get_user_name(tid)
    # Ledger (negative = we spend) + expense record atomically — crash между
    # двумя insert'ами раньше оставлял ledger-запись без расхода
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    con = get_db()
    try:
        con.execute("BEGIN IMMEDIATE")
        cur = con.execute("INSERT INTO ledger_entries (ccy,amount,entry_type,ref_id,note,ts) "
                          "VALUES (?,?,?,?,?,?)", (ccy, -amt, "expense", None, note, ts))
        lid = cur.lastrowid
        con.execute("INSERT INTO expenses (ccy,amount,note,tg_id,manager,ledger_id,created_at) "
                    "VALUES (?,?,?,?,?,?,?)", (ccy, amt, note, tid, mgr, lid, ts))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK"); raise
    finally:
        con.close()
    audit(tid, "expense_add", f"-{amt} {ccy}: {note}")
    ctx.user_data.clear()
    kb=[[InlineKeyboardButton("💸 Ещё",callback_data="exp_add"),
         InlineKeyboardButton("📋 Список",callback_data="exp_list")],
        [InlineKeyboardButton("Меню",callback_data="main_menu")]]
    await update.message.reply_text(
        f"✅ Расход записан\n\n💸 -{amt:,.2f} {ccy}\n📝 {note}\n👤 {mgr}",
        reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

async def cb_exp_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    rows = db_exec("SELECT id,ccy,amount,note,manager,created_at FROM expenses ORDER BY created_at DESC LIMIT 15")
    if not rows:
        await q.edit_message_text("Расходов нет.", reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("↩",callback_data="expenses_menu")]])); return
    lines = ["💸 Расходы (посл. 15):\n"]
    total = {}
    for r in rows:
        lines.append(f"  #{r['id']} {(r['created_at'] or '')[:10]} "
                     f"-{r['amount']:,.2f} {r['ccy']} | {r['note'] or '—'}")
        total[r['ccy']] = total.get(r['ccy'], 0) + r['amount']
    lines.append("")
    for c, s in total.items():
        lines.append(f"  Σ {c}: -{s:,.2f}")
    kb=[[InlineKeyboardButton("↩",callback_data="expenses_menu"),
         InlineKeyboardButton("Меню",callback_data="main_menu")]]
    await q.edit_message_text("\n".join(lines)[:4000], reply_markup=InlineKeyboardMarkup(kb))

async def cb_exp_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): return ConversationHandler.END
    rows = db_exec("SELECT id,ccy,amount,note FROM expenses ORDER BY created_at DESC LIMIT 10")
    if not rows:
        await q.edit_message_text("Расходов нет.", reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("↩",callback_data="expenses_menu")]])); return
    lines = ["Введи ID для удаления:\n"]
    for r in rows:
        lines.append(f"  #{r['id']} -{r['amount']:,.2f} {r['ccy']} | {r['note'] or '—'}")
    await q.edit_message_text("\n".join(lines))
    return EXP_DELETE_ID

async def exp_delete_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        eid = int(update.message.text.strip())
    except:
        await update.message.reply_text("Введи числовой ID:"); return EXP_DELETE_ID
    row = db_exec("SELECT * FROM expenses WHERE id=?", (eid,), "one")
    if not row:
        await update.message.reply_text("Не найден."); return EXP_DELETE_ID
    # Reverse ledger entry + delete atomically
    ops = []
    lid = row["ledger_id"]
    if lid:
        le = db_exec("SELECT ccy,amount FROM ledger_entries WHERE id=?", (lid,), "one")
        if le:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ops.append(("INSERT INTO ledger_entries (ccy,amount,entry_type,ref_id,note,ts) VALUES (?,?,?,?,?,?)",
                        (le["ccy"], -le["amount"], "expense_reversal", None, f"rev expense #{eid}", ts)))
    ops.append(("DELETE FROM expenses WHERE id=?", (eid,)))
    db_transaction(ops)
    audit(update.effective_user.id, "expense_del",
          f"#{eid} {row['amount']} {row['ccy']}: {row['note']}")
    kb=[[InlineKeyboardButton("💸 Расходы",callback_data="expenses_menu"),
         InlineKeyboardButton("Меню",callback_data="main_menu")]]
    await update.message.reply_text(
        f"🗑 Расход #{eid} удалён (+{row['amount']:,.2f} {row['ccy']} возвращено)",
        reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

# ── Клиенты 🟢 ──
async def cb_clients_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    kb=[[InlineKeyboardButton("День",callback_data="cl_day"),InlineKeyboardButton("Нед",callback_data="cl_week")],
        [InlineKeyboardButton("Мес",callback_data="cl_month"),InlineKeyboardButton("Всё",callback_data="cl_all")],
        [InlineKeyboardButton("🏆 Сегменты",callback_data="cl_seg")],
        [InlineKeyboardButton("Назад",callback_data="main_menu")]]
    await q.edit_message_text("🟢 Клиенты:", reply_markup=InlineKeyboardMarkup(kb))

async def cb_clients_show(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    p = q.data.replace("cl_",""); lb = {"day":"День","week":"Нед","month":"Мес","all":"Всё"}.get(p,p)
    s = get_clients_stats(p)
    if not s.get("deal_count"):
        t = f"🟢 {lb}: нет сделок"
    else:
        t = (f"🟢 Клиенты — {lb}\n\nСделок: {s['deal_count']}\n"
             f"Объём: {s['volume_usdt']:,.2f} USDT\n"
             f"Маржа: {s['total_margin']:+,.2f}\n"
             f"Спред: {s['avg_spread_pct']:+.2f}%")
    kb=[[InlineKeyboardButton("↩",callback_data="clients_menu"),InlineKeyboardButton("Меню",callback_data="main_menu")]]
    await q.edit_message_text(t, reply_markup=InlineKeyboardMarkup(kb))

async def cb_clients_seg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    seg = get_client_segmentation("month")
    lines = ["🏆 Сегментация (месяц)\n"]
    for tier, emoji in [("VIP","🥇"),("NORMAL","🥈"),("LOW","🥉")]:
        clients = seg.get(tier, [])
        lines.append(f"\n{emoji} {tier} ({len(clients)}):")
        for c in clients[:10]:
            lines.append(f"  {c['name']}: {c['margin']:+,.2f} | {c['deals']}сд")
    if not any(seg.values()): lines.append("\nНет данных (нужны имена клиентов)")
    kb=[[InlineKeyboardButton("↩",callback_data="clients_menu"),InlineKeyboardButton("Меню",callback_data="main_menu")]]
    await q.edit_message_text("\n".join(lines)[:4000], reply_markup=InlineKeyboardMarkup(kb))

# ── Реклама 🔴 ──
async def cb_ads_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    kb=[[InlineKeyboardButton("День",callback_data="ad_day"),InlineKeyboardButton("Нед",callback_data="ad_week")],
        [InlineKeyboardButton("Мес",callback_data="ad_month"),InlineKeyboardButton("Всё",callback_data="ad_all")],
        [InlineKeyboardButton("➕ Расход на рекламу",callback_data="ad_exp_add")],
        [InlineKeyboardButton("Назад",callback_data="main_menu")]]
    await q.edit_message_text("🔴 Реклама:", reply_markup=InlineKeyboardMarkup(kb))

async def cb_ads_show(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    p = q.data.replace("ad_",""); lb = {"day":"День","week":"Нед","month":"Мес","all":"Всё"}.get(p,p)
    roi = get_ads_roi(p)
    lines = [f"🔴 Реклама — {lb}\n",
             f"Сделок: {roi['deals']}",
             f"Объём: {roi['volume']:,.2f} USDT",
             f"Прибыль: {roi['profit']:+,.2f} USDT",
             f"Расходы: {roi['cost']:,.2f} USDT",
             f"ROI: {roi['roi']:+.1f}%"]
    kb=[[InlineKeyboardButton("↩",callback_data="ads_menu"),InlineKeyboardButton("Меню",callback_data="main_menu")]]
    await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))

async def cb_ad_exp_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    kb=[[InlineKeyboardButton(c,callback_data=f"adexpccy_{c}")] for c in CURRENCIES]
    kb.append([InlineKeyboardButton("Отмена",callback_data="ads_menu")])
    await q.edit_message_text("🔴 Валюта рекламного расхода:", reply_markup=InlineKeyboardMarkup(kb))
    return AD_EXP_CCY

async def ad_exp_ccy_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["ad_exp_ccy"] = q.data.replace("adexpccy_","")
    await q.edit_message_text(f"Сумма ({ctx.user_data['ad_exp_ccy']}):")
    return AD_EXP_AMT

async def ad_exp_amt_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        a = float(update.message.text.replace(",",".").strip())
        if a <= 0: raise ValueError
    except:
        await update.message.reply_text("Сумма > 0:"); return AD_EXP_AMT
    ctx.user_data["ad_exp_amt"] = a
    await update.message.reply_text("Комментарий (или 2 — пропустить):")
    return AD_EXP_NOTE

async def ad_exp_note_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    note = None if raw == "2" else raw
    ccy = ctx.user_data.get("ad_exp_ccy","CZK")
    amt = ctx.user_data.get("ad_exp_amt",0)
    tid = update.effective_user.id; mgr = get_user_name(tid)
    db_exec("INSERT INTO ad_expenses (amount,ccy,note,tg_id,manager) VALUES (?,?,?,?,?)",
            (amt, ccy, note, tid, mgr), fetch=None)
    audit(tid, "ad_expense", f"{amt} {ccy}: {note or '—'}")
    ctx.user_data.clear()
    kb=[[InlineKeyboardButton("🔴 Реклама",callback_data="ads_menu"),
         InlineKeyboardButton("Меню",callback_data="main_menu")]]
    await update.message.reply_text(f"✅ Рекламный расход: {amt:,.2f} {ccy}",
                                    reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

# ── Ликвидность 💰 (sources) ──
async def cb_liq_sources(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): return
    s = get_liquidity_summary()
    lines = ["💰 Источники ликвидности\n"]
    for label, d in [("🟢 Собственная", s["own"]), ("🔴 Кредитная", s["credit"])]:
        lines.append(f"{label}:")
        for ccy, amt in d.items():
            if amt: lines.append(f"  {ccy}: {amt:,.2f}")
    lines.extend(["",f"Σ Своя: {s['total_own_usdt']:,.2f} USDT",
                   f"Σ Кредит: {s['total_credit_usdt']:,.2f} USDT",
                   f"Σ Всего: {s['total_usdt']:,.2f} USDT"])
    kb=[[InlineKeyboardButton("🟢 Добавить свою",callback_data="liq_own")],
        [InlineKeyboardButton("🔴 Добавить кредит",callback_data="liq_credit")],
        [InlineKeyboardButton("🗑 Удалить",callback_data="liq_del")],
        [InlineKeyboardButton("↩ Админ",callback_data="admin"),
         InlineKeyboardButton("Меню",callback_data="main_menu")]]
    await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))

async def cb_liq_type(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): return ConversationHandler.END
    ctx.user_data["liq_type"] = "own" if q.data == "liq_own" else "credit"
    kb=[[InlineKeyboardButton(c,callback_data=f"liqccy_{c}")] for c in CURRENCIES]
    label = "собственной" if ctx.user_data["liq_type"] == "own" else "кредитной"
    await q.edit_message_text(f"Валюта {label} ликвидности:", reply_markup=InlineKeyboardMarkup(kb))
    return LIQ_CCY

async def liq_ccy_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["liq_ccy"] = q.data.replace("liqccy_","")
    await q.edit_message_text(f"Сумма ({ctx.user_data['liq_ccy']}):")
    return LIQ_AMT

async def liq_amt_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        a = float(update.message.text.replace(",",".").strip())
        if a <= 0: raise ValueError
    except:
        await update.message.reply_text("Сумма > 0:"); return LIQ_AMT
    ctx.user_data["liq_amt"] = a
    await update.message.reply_text("Комментарий (или 2 — пропустить):")
    return LIQ_NOTE

async def liq_note_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    note = None if raw == "2" else raw
    ccy = ctx.user_data.get("liq_ccy"); amt = ctx.user_data.get("liq_amt",0)
    ltype = ctx.user_data.get("liq_type","own")
    tid = update.effective_user.id; mgr = get_user_name(tid)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # Ledger + liquidity_sources в одной транзакции
    con = get_db()
    try:
        con.execute("BEGIN IMMEDIATE")
        cur = con.execute("INSERT INTO ledger_entries (ccy,amount,entry_type,ref_id,note,ts) "
                          "VALUES (?,?,?,?,?,?)", (ccy, amt, f"liq_{ltype}", None, note or f"{ltype} liquidity", ts))
        lid = cur.lastrowid
        con.execute("INSERT INTO liquidity_sources (ccy,amount,type,note,tg_id,manager,ledger_id,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)", (ccy, amt, ltype, note, tid, mgr, lid, ts))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK"); raise
    finally:
        con.close()
    label = "🟢 Собственная" if ltype == "own" else "🔴 Кредитная"
    audit(tid, f"liq_{ltype}", f"+{amt} {ccy}: {note or '—'}")
    ctx.user_data.clear()
    kb=[[InlineKeyboardButton("💰 Источники",callback_data="liq_sources"),
         InlineKeyboardButton("Меню",callback_data="main_menu")]]
    await update.message.reply_text(f"✅ {label}: +{amt:,.2f} {ccy}",
                                    reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

async def cb_liq_del(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): return ConversationHandler.END
    rows = db_exec("SELECT id,ccy,amount,type,note FROM liquidity_sources ORDER BY created_at DESC LIMIT 15")
    if not rows:
        await q.edit_message_text("Нет записей.", reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("↩",callback_data="liq_sources")]])); return
    lines = ["🗑 Введи ID для удаления:\n"]
    for r in rows:
        ic = "🟢" if r["type"] == "own" else "🔴"
        lines.append(f"  {ic} #{r['id']} {r['amount']:,.2f} {r['ccy']} | {r['note'] or '—'}")
    await q.edit_message_text("\n".join(lines))
    return LIQ_DELETE_ID

async def liq_delete_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try: lid = int(update.message.text.strip())
    except: await update.message.reply_text("Числовой ID:"); return LIQ_DELETE_ID
    row = db_exec("SELECT * FROM liquidity_sources WHERE id=?", (lid,), "one")
    if not row: await update.message.reply_text("Не найдена."); return LIQ_DELETE_ID
    # Reverse ledger + delete atomically
    ops = []
    ledger_id = row["ledger_id"]
    if ledger_id:
        le = db_exec("SELECT ccy,amount FROM ledger_entries WHERE id=?", (ledger_id,), "one")
        if le:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ops.append(("INSERT INTO ledger_entries (ccy,amount,entry_type,ref_id,note,ts) VALUES (?,?,?,?,?,?)",
                        (le["ccy"], -le["amount"], "liq_reversal", None, f"rev liq #{lid}", ts)))
    ops.append(("DELETE FROM liquidity_sources WHERE id=?", (lid,)))
    db_transaction(ops)
    audit(update.effective_user.id, "liq_delete", f"#{lid} {row['amount']} {row['ccy']} {row['type']}")
    kb=[[InlineKeyboardButton("💰 Источники",callback_data="liq_sources"),
         InlineKeyboardButton("Меню",callback_data="main_menu")]]
    await update.message.reply_text(
        f"🗑 Ликвидность #{lid} удалена ({row['amount']:,.2f} {row['ccy']} возвращено)",
        reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

# ── Deal Editor (admin) ──
async def cb_deal_editor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): return ConversationHandler.END
    ds = db_exec("SELECT id,created_at,from_amt,from_ccy,to_amt,to_ccy,client_type,client_name "
                 "FROM orders WHERE state='completed' ORDER BY created_at DESC LIMIT 15")
    if not ds:
        await q.edit_message_text("Нет сделок.", reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("↩",callback_data="admin")]])); return
    lines = ["✏️ Редактор сделок\nВведи ID:\n"]
    for d in ds:
        ct = "🟢" if (d["client_type"] or "organic") == "organic" else "🔴"
        cn = d["client_name"] or "—"
        lines.append(f"  {ct} #{d['id']} {(d['created_at'] or '')[:10]} "
                     f"{d['to_amt']:,.0f}{d['to_ccy']}→{d['from_amt']:,.0f}{d['from_ccy']} | {cn}")
    await q.edit_message_text("\n".join(lines))
    return DEDIT_ID

async def dedit_id_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try: oid = int(update.message.text.strip())
    except: await update.message.reply_text("Числовой ID:"); return DEDIT_ID
    row = db_exec("SELECT id,client_type,client_name,tx_hash FROM orders WHERE id=?", (oid,), "one")
    if not row: await update.message.reply_text("Не найдена."); return DEDIT_ID
    ctx.user_data["dedit_oid"] = oid
    ct = row["client_type"] or "organic"; cn = row["client_name"] or "—"; tx = row["tx_hash"] or "—"
    kb = [[InlineKeyboardButton("🟢/🔴 Тип клиента", callback_data="dedit_f_client_type"),
           InlineKeyboardButton("👤 Имя клиента", callback_data="dedit_f_client_name")],
          [InlineKeyboardButton("🔗 TX Hash", callback_data="dedit_f_tx_hash")],
          [InlineKeyboardButton("↩ Админ", callback_data="admin")]]
    await update.message.reply_text(
        f"✏️ Сделка #{oid}\nТип: {'🟢 organic' if ct=='organic' else '🔴 ads'}\nКлиент: {cn}\nTX: {tx}\n\nЧто редактировать?",
        reply_markup=InlineKeyboardMarkup(kb))
    return DEDIT_FIELD

async def dedit_field_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    field = q.data.replace("dedit_f_","")
    ctx.user_data["dedit_field"] = field
    if field == "client_type":
        kb = [[InlineKeyboardButton("🟢 Наш клиент", callback_data="dedit_v_organic"),
               InlineKeyboardButton("🔴 Реклама", callback_data="dedit_v_ads")]]
        await q.edit_message_text("Выбери тип:", reply_markup=InlineKeyboardMarkup(kb))
    elif field == "tx_hash":
        await q.edit_message_text("Введи TX Hash:")
    else:
        await q.edit_message_text("Введи имя клиента:")
    return DEDIT_VALUE

async def dedit_value_cb_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle client_type buttons."""
    q = update.callback_query; await q.answer()
    value = q.data.replace("dedit_v_","")
    oid = ctx.user_data.get("dedit_oid")
    field = ctx.user_data.get("dedit_field")
    try: edit_deal_field(oid, field, value, q.from_user.id)
    except ValueError as e: await q.edit_message_text(str(e)); return DEDIT_VALUE
    ctx.user_data.clear()
    kb = [[InlineKeyboardButton("✏️ Ещё",callback_data="deal_editor"),
           InlineKeyboardButton("Меню",callback_data="main_menu")]]
    await q.edit_message_text(f"✅ #{oid} {field}={value}", reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

async def dedit_value_text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle client_name text input."""
    value = update.message.text.strip()
    oid = ctx.user_data.get("dedit_oid")
    field = ctx.user_data.get("dedit_field")
    try: edit_deal_field(oid, field, value, update.effective_user.id)
    except ValueError as e: await update.message.reply_text(str(e)); return DEDIT_VALUE
    ctx.user_data.clear()
    kb = [[InlineKeyboardButton("✏️ Ещё",callback_data="deal_editor"),
           InlineKeyboardButton("Меню",callback_data="main_menu")]]
    await update.message.reply_text(f"✅ #{oid} {field}={value}", reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

# ── Custom Spread ──
async def cb_cspread(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_admin(q.from_user.id):
        await q.edit_message_text("Нет доступа."); return
    qc = q.data.replace("cspread_","")
    ctx.user_data["cspread_ccy"] = qc
    cur = _custom_spread.get(qc)
    hint = f"\nСейчас: {cur:.2f}%" if cur else "\nСейчас: авто"
    await q.edit_message_text(f"✏️ Спред для USDT/{qc}\nВведи % (напр 2.5):{hint}")
    return CUSTOM_SPREAD_VALUE

async def cspread_value_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    qc = ctx.user_data.get("cspread_ccy","CZK")
    try:
        v = float(raw.replace(",","."))
        if not (0 < v <= 20): raise ValueError  # опечатка вроде 25 вместо 2.5 = смертельный курс
    except:
        await update.message.reply_text("Число от 0.1 до 20 (напр 2.5):"); return CUSTOM_SPREAD_VALUE
    _custom_spread[qc] = v
    audit(update.effective_user.id, "custom_spread", f"USDT/{qc}={v}%")
    ctx.user_data.clear()
    kb=[[InlineKeyboardButton("💡 Цены",callback_data="pricing"),
         InlineKeyboardButton("Меню",callback_data="main_menu")]]
    await update.message.reply_text(f"✅ Спред USDT/{qc} = {v:.2f}%", reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

async def cb_cspread_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_admin(q.from_user.id):
        await q.edit_message_text("Нет доступа."); return
    _custom_spread.clear()
    audit(q.from_user.id, "custom_spread", "RESET to auto")
    kb=[[InlineKeyboardButton("💡 Цены",callback_data="pricing"),
         InlineKeyboardButton("Меню",callback_data="main_menu")]]
    await q.edit_message_text("✅ Спред сброшен на авто для всех валют.", reply_markup=InlineKeyboardMarkup(kb))

# ── Security ──
async def cb_sec_panic(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): return
    if _panic_mode:
        panic_off()
        audit(q.from_user.id, "security", "PANIC OFF — managers restored")
        await q.edit_message_text("✅ Паника отключена. Менеджеры разблокированы.",
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩ Админ",callback_data="admin")]]))
    else:
        kb = [[InlineKeyboardButton("🚨 ДА, ЗАБЛОКИРОВАТЬ ВСЕХ", callback_data="sec_panic_confirm")],
              [InlineKeyboardButton("↩ Отмена", callback_data="admin")]]
        await q.edit_message_text("⚠️ ВНИМАНИЕ\n\nВсе менеджеры будут мгновенно заблокированы.\n"
                                  "Только админы смогут пользоваться ботом.\n\nПодтвердить?",
                                  reply_markup=InlineKeyboardMarkup(kb))

async def cb_sec_panic_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): return
    panic_on()
    audit(q.from_user.id, "security", "🚨 PANIC ON — all managers blocked")
    # Notify all admins
    for aid in ADMIN_IDS:
        if aid != q.from_user.id:
            try: await ctx.bot.send_message(aid, f"🚨 PANIC MODE активирован пользователем {q.from_user.id}")
            except: pass
    await q.edit_message_text("🚨 PANIC MODE АКТИВИРОВАН\n\nВсе менеджеры заблокированы.\nТолько админы имеют доступ.",
                              reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩ Админ",callback_data="admin")]]))

async def cb_sec_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): return
    if _data_hidden:
        kb = [[InlineKeyboardButton("🔓 ДА, ВОССТАНОВИТЬ", callback_data="sec_data_restore")],
              [InlineKeyboardButton("↩ Отмена", callback_data="admin")]]
        await q.edit_message_text("🔒 Данные скрыты.\n\nВосстановить из бэкапа?",
                                  reply_markup=InlineKeyboardMarkup(kb))
    else:
        kb = [[InlineKeyboardButton("🔒 ДА, СКРЫТЬ ВСЁ", callback_data="sec_data_hide")],
              [InlineKeyboardButton("↩ Отмена", callback_data="admin")]]
        await q.edit_message_text("⚠️ ВНИМАНИЕ\n\nВся база данных будет скрыта.\n"
                                  "swapme.db → swapme_backup.db\n"
                                  "Бот станет пустым (0 сделок, 0 баланс).\n"
                                  "Данные можно восстановить.\n\nПодтвердить?",
                                  reply_markup=InlineKeyboardMarkup(kb))

async def cb_sec_data_hide(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): return
    try:
        hide_data()
        audit(q.from_user.id, "security", "🔒 DATA HIDDEN")
        for aid in ADMIN_IDS:
            if aid != q.from_user.id:
                try: await ctx.bot.send_message(aid, "🔒 ДАННЫЕ СКРЫТЫ — БД очищена")
                except: pass
        await q.edit_message_text("🔒 Данные скрыты.\nБаза пустая. Бэкап сохранён.",
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩ Админ",callback_data="admin")]]))
    except ValueError as e:
        await q.edit_message_text(f"❌ {e}",
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩ Админ",callback_data="admin")]]))

async def cb_sec_data_restore(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): return
    try:
        restore_data()
        audit(q.from_user.id, "security", "🔓 DATA RESTORED")
        for aid in ADMIN_IDS:
            if aid != q.from_user.id:
                try: await ctx.bot.send_message(aid, "🔓 ДАННЫЕ ВОССТАНОВЛЕНЫ")
                except: pass
        await q.edit_message_text("🔓 Данные восстановлены.\nБаза возвращена из бэкапа.",
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩ Админ",callback_data="admin")]]))
    except ValueError as e:
        await q.edit_message_text(f"❌ {e}",
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩ Админ",callback_data="admin")]]))

# ── Channel Post ──
async def cb_post_channel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not CHANNEL_ID:
        await ctx.bot.send_message(q.message.chat_id, "❌ CHANNEL_ID не настроен.")
        return
    oid = int(q.data.replace("post_ch_",""))
    row = db_exec("SELECT * FROM orders WHERE id=?", (oid,), "one")
    if not row:
        await ctx.bot.send_message(q.message.chat_id, f"❌ Сделка #{oid} не найдена.")
        return
    d = {k: _safe(row, k, None) for k in [
        "deal_type","from_ccy","from_amt","to_ccy","to_amt","deal_rate",
        "market_rate","margin_our","spread_pct","created_at","client_type","client_name","tx_hash"]}
    for k in ["from_amt","to_amt","deal_rate"]: d[k] = d[k] or 0
    mgr = _safe(row, "manager", "")
    post = format_channel_post(oid, d, mgr)
    try:
        await ctx.bot.send_message(CHANNEL_ID, post, parse_mode="MarkdownV2",
                                   disable_web_page_preview=True)
        await ctx.bot.send_message(q.message.chat_id, f"✅ Сделка #{oid} опубликована в канал.")
    except Exception as e:
        log.error(f"Channel post #{oid}: {e}")
        await ctx.bot.send_message(q.message.chat_id, f"❌ Ошибка: {e}")

async def cb_cost_basis(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    summary = await asyncio.to_thread(get_cost_basis_summary)
    lines = ["🏷 *Себестоимость USDT*\n"]
    for qccy in ["CZK", "EUR", "USD"]:
        d = summary[qccy]
        mkt = d["market_now"]
        vwap = d["vwap"]
        inv = d["inventory"]
        lp = d["last_purchase"]   # last time WE bought USDT
        ls = d["last_sale"]       # last time WE sold USDT
        lines.append(f"━━━ *USDT/{qccy}* ━━━")
        lines.append(f"📊 Рынок сейчас: *{mkt:,.2f}* {qccy}")
        if vwap > 0:
            lines.append(f"📦 VWAP (ср. покупка): *{vwap:,.2f}* {qccy}")
            diff_pct = ((mkt - vwap) / vwap * 100)
            emoji = "🟢" if diff_pct >= 0 else "🔴"
            lines.append(f"{emoji} Рынок vs VWAP: *{diff_pct:+.2f}%*")
            lines.append(f"📦 Запас: *{inv:,.2f}* USDT")
            min_sell = vwap * 1.005  # +0.5% минимум для прибыли
            lines.append(f"⚠️ Мин. продажа: *{min_sell:,.2f}* {qccy}")
        else:
            lines.append(f"📦 Нет покупок USDT за {qccy}")
        if lp:
            lines.append(f"\n🛒 *Посл. покупка (мы купили):*")
            lines.append(f"   Наш курс: *{lp['cost_rate']:,.2f}* {qccy}")
            if lp["market_then"] > 0:
                lines.append(f"   Рынок тогда: *{lp['market_then']:,.2f}* {qccy}")
                spread = ((lp["cost_rate"] - lp["market_then"]) / lp["market_then"] * 100)
                lines.append(f"   Наценка к рынку: *{spread:+.2f}%*")
            lines.append(f"   Объём: *{lp['usdt_qty']:,.2f}* USDT")
            lines.append(f"   Дата: {lp['date'][:16] if lp['date'] else '—'}")
        if ls:
            lines.append(f"\n💸 *Посл. продажа (мы продали):*")
            lines.append(f"   Наш курс: *{ls['sell_rate']:,.2f}* {qccy}")
            if ls["market_then"] > 0:
                lines.append(f"   Рынок тогда: *{ls['market_then']:,.2f}* {qccy}")
            lines.append(f"   Объём: *{ls['usdt_sold']:,.2f}* USDT")
            lines.append(f"   Дата: {ls['date'][:16] if ls['date'] else '—'}")
            # Profit per USDT if we have both buy and sell
            if lp:
                profit = ls["sell_rate"] - lp["cost_rate"]
                lines.append(f"   Маржа: *{profit:+,.2f}* {qccy}/USDT")
        lines.append("")
    kb = [[InlineKeyboardButton("🛒 Покупки (5)", callback_data="cost_last5_buy"),
           InlineKeyboardButton("💸 Продажи (5)", callback_data="cost_last5_sell")],
          [InlineKeyboardButton("Меню", callback_data="main_menu")]]
    await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


async def cb_cost_last5_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    purchases = await asyncio.to_thread(get_last_usdt_purchases, 5)
    if not purchases:
        await q.edit_message_text("Нет завершённых покупок USDT.",
                                  reply_markup=InlineKeyboardMarkup(BK))
        return
    lines = ["🛒 *Последние покупки USDT (мы купили):*\n"]
    for p in purchases:
        lines.append(f"*#{p['id']}* | *{p['usdt_qty']:,.2f}* USDT за *{p['fiat_paid']:,.2f}* {p['fiat_ccy']}")
        lines.append(f"   Наш курс: *{p['cost_rate']:,.2f}* | Рынок тогда: {p['market_rate_then']:,.2f}")
        lines.append(f"   Рынок сейчас: *{p['market_rate_now']:,.2f}* {p['fiat_ccy']}")
        if p['market_rate_now'] > 0:
            pnl = (p['market_rate_now'] - p['cost_rate']) * p['usdt_qty']
            emoji = "🟢" if pnl >= 0 else "🔴"
            lines.append(f"   {emoji} Прибыль если продать сейчас: *{pnl:+,.0f}* {p['fiat_ccy']}")
        lines.append(f"   📅 {p['date'][:16] if p['date'] else '—'} | {p['manager']}")
        lines.append("")
    kb = [[InlineKeyboardButton("↩ Себестоимость", callback_data="cost_basis"),
           InlineKeyboardButton("Меню", callback_data="main_menu")]]
    await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


async def cb_cost_last5_sell(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    sales = await asyncio.to_thread(get_last_usdt_sales, 5)
    if not sales:
        await q.edit_message_text("Нет завершённых продаж USDT.",
                                  reply_markup=InlineKeyboardMarkup(BK))
        return
    lines = ["💸 *Последние продажи USDT (мы продали):*\n"]
    for s in sales:
        lines.append(f"*#{s['id']}* | *{s['usdt_sold']:,.2f}* USDT за *{s['fiat_received']:,.2f}* {s['fiat_ccy']}")
        lines.append(f"   Наш курс: *{s['sell_rate']:,.2f}* | Рынок тогда: {s['market_rate_then']:,.2f}")
        lines.append(f"   Рынок сейчас: *{s['market_rate_now']:,.2f}* {s['fiat_ccy']}")
        lines.append(f"   📅 {s['date'][:16] if s['date'] else '—'} | {s['manager']}")
        lines.append("")
    kb = [[InlineKeyboardButton("↩ Себестоимость", callback_data="cost_basis"),
           InlineKeyboardButton("Меню", callback_data="main_menu")]]
    await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


async def cb_main_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); await show_main_menu(update, ctx)


# ── QUICK DEAL — хендлеры быстрого ввода ──────────────────────────────────────

def _format_quick_preview(d: dict) -> str:
    """Экран-подтверждение быстрой сделки."""
    fc, tc = d["from_ccy"], d["to_ccy"]
    fa, ta = d["from_amt"], d["to_amt"]
    mkt = get_rate(fc, tc)
    sp = calc_spread(fc, fa, tc, ta, mkt)
    rate, margin = sp["deal_rate"], sp["margin_our"]
    src = d.get("client_type", "organic")
    src_lbl = "🟢 Наш" if src == "organic" else "🔴 Реклама"
    date_lbl = (d.get("custom_date") or "сейчас")[:16]
    sep = "━━━━━━━━━━━━━━━━"
    lines = ["⚡ *Быстрая сделка* — проверь:", sep,
             f"Принял:  *{fa:,.2f} {fc}*",
             f"Выдал:   *{ta:,.2f} {tc}*",
             f"Курс: {rate:.4f}" + (f"  (рынок {mkt:.4f})" if mkt else "  (рынок н/д)")]
    if margin is not None:
        ic = "📈" if margin >= 0 else "⚠️ УБЫТОК"
        lines.append(f"{ic} Маржа: {margin:+,.2f} {tc} ({sp['spread_pct']:+.2f}%)")
    try:
        av = get_available().get(tc, 0)
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
         InlineKeyboardButton("❌ Отмена", callback_data="qd_cancel")]])


async def cb_quick_deal_preview(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Catch-all: «500 usdt 10400 czk» вне диалога → предпросмотр сделки."""
    if not update.message or not update.message.text:
        return
    if not is_authorized(update.effective_user.id):
        return
    parsed = parse_quick_deal(update.message.text)
    if not parsed:
        return  # не похоже на сделку — молчим
    parsed["client_type"] = "organic"
    parsed["client_name"] = None
    parsed["custom_date"] = None
    ctx.user_data["quick"] = parsed
    await update.message.reply_text(_format_quick_preview(parsed),
                                    reply_markup=_quick_kb(parsed["client_type"]),
                                    parse_mode="Markdown")


async def cb_quick_toggle_src(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    d = ctx.user_data.get("quick")
    if not d:
        await q.edit_message_text("Черновик устарел. Введи сделку заново."); return
    d["client_type"] = "ads" if d.get("client_type", "organic") == "organic" else "organic"
    await q.edit_message_text(_format_quick_preview(d),
                              reply_markup=_quick_kb(d["client_type"]), parse_mode="Markdown")


async def cb_quick_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🕒 Сейчас", callback_data="qd_d_now"),
         InlineKeyboardButton("Вчера", callback_data="qd_d_yest")],
        [InlineKeyboardButton("↩ Назад", callback_data="qd_d_back")]])
    await q.edit_message_text("📅 Когда была сделка?", reply_markup=kb)


async def cb_quick_date_set(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    d = ctx.user_data.get("quick")
    if not d:
        await q.edit_message_text("Черновик устарел. Введи сделку заново."); return
    code = q.data.replace("qd_d_", "")
    if code == "now":
        d["custom_date"] = None
    elif code == "yest":
        d["custom_date"] = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    await q.edit_message_text(_format_quick_preview(d),
                              reply_markup=_quick_kb(d["client_type"]), parse_mode="Markdown")


async def cb_quick_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """qd_save — записать быструю сделку (create_order в потоке, как _save)."""
    q = update.callback_query; await q.answer()
    d = ctx.user_data.get("quick")
    if not d:
        await q.edit_message_text("Черновик устарел. Введи сделку заново."); return
    tid = update.effective_user.id
    mgr = get_user_name(tid)
    mkt = get_rate(d["from_ccy"], d["to_ccy"])
    try:
        oid = await asyncio.to_thread(
            create_order,
            tg_id=tid, manager=mgr, deal_type=d["deal_type"],
            from_ccy=d["from_ccy"], from_amt=d["from_amt"],
            to_ccy=d["to_ccy"], to_amt=d["to_amt"],
            market_rate=mkt, auto_complete=True,
            custom_created_at=d.get("custom_date"),
            client_type=d.get("client_type", "organic"),
            client_name=d.get("client_name"))
    except (ValueError, RuntimeError, sqlite3.Error) as e:
        log.error(f"Quick deal failed: {e}")
        ctx.user_data.pop("quick", None)
        await q.edit_message_text(f"❌ Ошибка: {e}",
                                  reply_markup=InlineKeyboardMarkup(BK)); return
    sp = calc_spread(d["from_ccy"], d["from_amt"], d["to_ccy"], d["to_amt"], mkt)
    d["market_rate"], d["deal_rate"], d["margin_our"] = mkt, sp["deal_rate"], sp["margin_our"]
    receipt = format_receipt(oid, d, mgr) + f"\n\n{random.choice(DEAL_DONE_PHRASES)}"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Ещё", callback_data="new_deal"),
         InlineKeyboardButton("📢 В канал", callback_data=f"post_ch_{oid}")],
        [InlineKeyboardButton("↩ Отменить", callback_data=f"undo_yes_{oid}"),
         InlineKeyboardButton("Меню", callback_data="main_menu")]])
    try:
        await q.edit_message_text(receipt, reply_markup=kb, parse_mode="Markdown",
                                  disable_web_page_preview=True)
    except Exception:
        # сделка УЖЕ записана — чек обязан дойти даже при поломке Markdown
        await ctx.bot.send_message(update.effective_chat.id, receipt, reply_markup=kb,
                                   disable_web_page_preview=True)
    ctx.user_data.pop("quick", None)


async def cb_quick_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data.pop("quick", None)
    await q.edit_message_text(f"Отменено. {random.choice(DEAL_CANCEL_PHRASES)}",
                              reply_markup=InlineKeyboardMarkup(BK))


# ── /undo — отмена последней сделки ───────────────────────────────────────────

def _last_undoable_order(tg_id: int):
    """Последняя completed-сделка менеджера за UNDO_WINDOW_MIN минут."""
    cutoff = (datetime.now() - timedelta(minutes=UNDO_WINDOW_MIN)).strftime("%Y-%m-%d %H:%M:%S")
    return db_exec(
        "SELECT * FROM orders WHERE tg_id=? AND state='completed' "
        "AND COALESCE(completed_at, created_at) >= ? ORDER BY id DESC LIMIT 1",
        (tg_id, cutoff), "one")


async def cmd_undo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/undo — показать последнюю сделку и спросить подтверждение."""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Нет доступа."); return
    row = _last_undoable_order(update.effective_user.id)
    if not row:
        await update.message.reply_text(
            f"Нет сделок для отмены за последние {UNDO_WINDOW_MIN} мин."); return
    oid = row["id"]
    txt = (f"↩ Отменить последнюю сделку?\n\n#{oid:07d}\n"
           f"Принял: {row['from_amt']:,.2f} {row['from_ccy']}\n"
           f"Выдал: {row['to_amt']:,.2f} {row['to_ccy']}\n"
           f"({(row['completed_at'] or row['created_at'] or '')[:16]})\n\n"
           f"Ledger будет реверсирован.")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да, отменить", callback_data=f"undo_yes_{oid}"),
         InlineKeyboardButton("Нет", callback_data="main_menu")]])
    await update.message.reply_text(txt, reply_markup=kb)


async def cb_undo_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """undo_yes_<id> — отменить сделку (cancel_order реверсит ledger)."""
    q = update.callback_query; await q.answer()
    try:
        oid = int(q.data.replace("undo_yes_", ""))
    except ValueError:
        return
    tid = update.effective_user.id
    if not is_authorized(tid):
        return
    row = db_exec("SELECT * FROM orders WHERE id=?", (oid,), "one")
    if not row:
        await q.edit_message_text("Сделка не найдена."); return
    if not is_admin(tid) and row["tg_id"] != tid:
        await q.edit_message_text("Можно отменять только свои сделки."); return
    if row["state"] == "cancelled":
        await q.edit_message_text(f"#{oid:07d} уже отменена."); return
    try:
        await asyncio.to_thread(cancel_order, oid, tid)
        audit(tid, "undo", f"#{oid}")
    except Exception as e:
        log.error(f"undo #{oid} failed: {e}")
        await q.edit_message_text(f"❌ Не удалось отменить: {e}"); return
    await q.edit_message_text(f"♻️ Сделка #{oid:07d} отменена, ledger возвращён.",
                              reply_markup=InlineKeyboardMarkup(BK))

# ── DB BACKUP / RESTORE через Telegram (для облачного хостинга без доступа к диску) ──

def _make_db_backup_file() -> str:
    """Консистентная копия БД через sqlite backup API. Возвращает путь к temp-файлу."""
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(),
                       f"swapme_{datetime.now().strftime('%Y-%m-%d_%H%M')}.db")
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(tmp)
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close(); dst.close()
    return tmp


async def cmd_backup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/backup — прислать админу консистентную копию базы файлом."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Нет доступа."); return
    tmp = await asyncio.to_thread(_make_db_backup_file)
    try:
        with open(tmp, "rb") as f:
            await update.message.reply_document(
                f, filename=os.path.basename(tmp),
                caption=f"📦 Бэкап базы · {datetime.now().strftime('%d.%m.%Y %H:%M')}")
        audit(update.effective_user.id, "backup", "manual")
    finally:
        try: os.remove(tmp)
        except OSError: pass


def _audit_report_text() -> str:
    """Текст отчёта полной сверки (для /reconcile и ночного закрытия)."""
    problems = full_audit()
    bal = get_balances()
    n_ord = db_exec("SELECT COUNT(*) c FROM orders", fetch="one")["c"]
    n_led = db_exec("SELECT COUNT(*) c FROM ledger_entries", fetch="one")["c"]
    crit = [m for lvl, m in problems if lvl == "CRIT"]
    warn = [m for lvl, m in problems if lvl == "WARN"]
    lines = [f"🔍 Сверка целостности · {datetime.now().strftime('%d.%m.%Y %H:%M')}",
             f"Ордеров: {n_ord} · Ledger-записей: {n_led}", ""]
    if not crit and not warn:
        lines.append("✅ Все инварианты сходятся, проблем нет.")
    if crit:
        lines.append(f"🛑 Критичные ({len(crit)}):")
        lines += [f"• {m}" for m in crit[:15]]
        if len(crit) > 15: lines.append(f"… и ещё {len(crit)-15}")
    if warn:
        if crit: lines.append("")
        lines.append(f"⚠️ Предупреждения ({len(warn)}):")
        lines += [f"• {m}" for m in warn[:10]]
        if len(warn) > 10: lines.append(f"… и ещё {len(warn)-10}")
    nz = [f"{c} {v:,.2f}" for c, v in sorted(bal.items()) if abs(v) > 0.005]
    if nz:
        lines += ["", "Балансы (ledger): " + ", ".join(nz)]
    return "\n".join(lines)


async def cmd_reconcile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/reconcile — полная сверка целостности базы (admin)."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Нет доступа."); return
    text = await asyncio.to_thread(_audit_report_text)
    audit(update.effective_user.id, "reconcile", "manual")
    await update.message.reply_text(text)


def _capital_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Обновить", callback_data="capital"),
                                  InlineKeyboardButton("≡ Меню", callback_data="main_menu")]])


async def cb_capital(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_authorized(q.from_user.id):
        return
    text = await asyncio.to_thread(format_capital_text)
    try:
        await q.edit_message_text(text, reply_markup=_capital_kb())
    except Exception as e:
        if "not modified" not in str(e).lower():
            raise


async def cmd_capital(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/capital — капитал с разбивкой (все авторизованные)."""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Нет доступа."); return
    text = await asyncio.to_thread(format_capital_text)
    await update.message.reply_text(text, reply_markup=_capital_kb())


async def cmd_set_anchor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/set_anchor <USDT> [YYYY-MM-DD [HH:MM:SS]] — новый якорь капитала (admin)."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Нет доступа."); return
    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "Использование: /set_anchor <USDT> [YYYY-MM-DD [HH:MM:SS]]\n"
            "Пример: /set_anchor 50000 2026-05-20 14:01:24\n"
            "Без даты якорь ставится на текущий момент (переподбивка)."); return
    try:
        start = float(args[0].replace(",", "."))
        if not (0 < start < 10**9) or math.isnan(start):
            raise ValueError
    except ValueError:
        await update.message.reply_text("Сумма не распознана. Пример: /set_anchor 50000"); return
    ts = None
    if len(args) > 1:
        ts = " ".join(args[1:]).strip('\'\"')
        if len(ts) == 10:
            ts += " 00:00:00"
        try:
            datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            await update.message.reply_text("Дата не распознана. Формат: 2026-05-20 14:01:24"); return
    ts = await asyncio.to_thread(set_capital_anchor, start, ts, "via bot", update.effective_user.id)
    text = await asyncio.to_thread(format_capital_text)
    await update.message.reply_text(f"✅ Якорь установлен: {ts} · старт {start:,.2f} USDT\n\n{text}",
                                    reply_markup=_capital_kb())


async def daily_backup(app, caption_extra: str = ""):
    """Ночной автобэкап: база файлом всем админам (оффсайт-копия с хостинга)."""
    try:
        tmp = await asyncio.to_thread(_make_db_backup_file)
        cnt = db_exec("SELECT COUNT(*) c FROM orders", fetch="one")["c"]
        cap = f"🌙 Закрытие дня · {cnt} сделок · {datetime.now().strftime('%d.%m.%Y')}{caption_extra}"
        for aid in ADMIN_IDS:
            try:
                with open(tmp, "rb") as f:
                    await app.bot.send_document(aid, f, filename=os.path.basename(tmp), caption=cap)
            except Exception as e:
                log.error(f"daily_backup → {aid}: {e}")
        try: os.remove(tmp)
        except OSError: pass
    except Exception as e:
        log.error(f"daily_backup: {e}", exc_info=True)


async def daily_close(app):
    """Ночное закрытие дня (23:55): полная сверка → алерт при проблемах,
    снимок капитала в capital_history, бэкап БД файлом админам."""
    status = ""
    # 1) сверка целостности
    try:
        problems = await asyncio.to_thread(full_audit)
        crit = [m for lvl, m in problems if lvl == "CRIT"]
        if crit:
            alert = ("🛑 НОЧНАЯ СВЕРКА: критичные проблемы!\n\n" +
                     "\n".join(f"• {m}" for m in crit[:10]) +
                     (f"\n… и ещё {len(crit)-10}" if len(crit) > 10 else "") +
                     "\n\nПодробно: /reconcile")
            for aid in ADMIN_IDS:
                try:
                    await app.bot.send_message(aid, alert)
                except Exception as e:
                    log.error(f"daily_close alert → {aid}: {e}")
            status += f" · 🛑 сверка: {len(crit)} проблем"
        else:
            status += " · сверка ✅"
    except Exception as e:
        log.error(f"daily_close audit: {e}", exc_info=True)
        status += " · сверка ⚠️ (ошибка)"
    # 2) снимок капитала
    try:
        cap = await asyncio.to_thread(save_capital_snapshot)
        if cap is not None:
            status += f" · 💼 {cap:,.0f} USDT"
    except Exception as e:
        log.error(f"daily_close capital: {e}", exc_info=True)
    # 3) бэкап базы файлом
    await daily_backup(app, status)


async def on_admin_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Админ присылает .db-файл → проверяем и предлагаем восстановить базу.
    Это путь миграции на облачный хостинг: бот сам кладёт базу на свой volume."""
    if not update.message or not update.message.document:
        return
    if not is_admin(update.effective_user.id):
        return
    doc = update.message.document
    if not (doc.file_name or "").lower().endswith(".db"):
        return
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), "swapme_incoming.db")
    tg_file = await doc.get_file()
    await tg_file.download_to_drive(tmp)

    def _validate(path):
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            ok = con.execute("PRAGMA integrity_check").fetchone()[0]
            n_orders = con.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
            n_ledger = con.execute("SELECT COUNT(*) FROM ledger_entries").fetchone()[0]
            return ok, n_orders, n_ledger
        finally:
            con.close()
    try:
        ok, n_orders, n_ledger = await asyncio.to_thread(_validate, tmp)
    except Exception as e:
        await update.message.reply_text(f"❌ Файл не похож на базу SwapMe: {e}")
        return
    if ok != "ok":
        await update.message.reply_text(f"❌ integrity_check: {ok}")
        return
    ctx.user_data["restore_path"] = tmp
    cur_orders = db_exec("SELECT COUNT(*) c FROM orders", fetch="one")["c"]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ ДА, ЗАМЕНИТЬ БАЗУ", callback_data="restore_db_yes")],
        [InlineKeyboardButton("❌ Отмена", callback_data="restore_db_no")]])
    await update.message.reply_text(
        f"⚠️ Восстановить базу из файла?\n\n"
        f"В файле: {n_orders} сделок, {n_ledger} ledger-записей\n"
        f"Сейчас в боте: {cur_orders} сделок\n\n"
        f"Текущая база будет сохранена рядом (.before_restore).",
        reply_markup=kb)


async def cb_restore_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_admin(q.from_user.id):
        return
    if q.data == "restore_db_no":
        ctx.user_data.pop("restore_path", None)
        await q.edit_message_text("Отменено."); return
    src = ctx.user_data.pop("restore_path", None)
    if not src or not os.path.exists(src):
        await q.edit_message_text("Файл устарел — пришли .db ещё раз."); return

    def _do_restore():
        import shutil
        if os.path.exists(DB_PATH):
            shutil.copy2(DB_PATH, DB_PATH + ".before_restore")
        # убрать WAL/SHM старой базы, чтобы не смешались с новой
        for suff in ("-wal", "-shm"):
            p = DB_PATH + suff
            if os.path.exists(p):
                os.remove(p)
        shutil.copy2(src, DB_PATH)
        con = sqlite3.connect(DB_PATH)
        try:
            ok = con.execute("PRAGMA integrity_check").fetchone()[0]
            n = con.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        finally:
            con.close()
        return ok, n
    ok, n = await asyncio.to_thread(_do_restore)
    init_db()  # миграции/индексы на случай старой схемы в файле
    audit(q.from_user.id, "restore_db", f"orders={n} integrity={ok}")
    await q.edit_message_text(f"✅ База восстановлена: {n} сделок (integrity: {ok}).\n"
                              f"Проверь /start → Баланс.")


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    """Global error handler: log full traceback, tell the user instead of silence."""
    log.error("Unhandled exception in handler", exc_info=ctx.error)
    try:
        if isinstance(update, Update) and update.effective_chat:
            await ctx.bot.send_message(update.effective_chat.id,
                                       "❌ Внутренняя ошибка. Попробуй ещё раз или /start.")
    except Exception:
        pass


async def send_daily_report(app):
    try:
        buf=await asyncio.to_thread(generate_excel_report, "day"); s=await asyncio.to_thread(get_stats, "day")
        cap=f"📊 {datetime.now().strftime('%d.%m.%Y')}\n{s.get('deal_count',0)}сд, маржа {s.get('total_margin',0):+,.2f}"
        for a in ADMIN_IDS:
            try: buf.seek(0); await app.bot.send_document(a,buf,filename=f"SwapMe_{datetime.now().strftime('%Y-%m-%d')}.xlsx",caption=cap)
            except Exception as e: log.error(f"Rpt→{a}: {e}")
    except Exception as e: log.error(f"DailyRpt: {e}",exc_info=True)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════
def main():
    check_data_hidden()
    init_db(); refresh_rates()
    app = Application.builder().token(BOT_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start",cmd_start),
            CallbackQueryHandler(cb_new_deal,pattern="^new_deal$"),
            CallbackQueryHandler(cb_manual_rate_menu,pattern="^manual_rate_menu$"),
            CallbackQueryHandler(cb_note_add,pattern="^note_add$"),
            CallbackQueryHandler(cb_converter,pattern="^converter$"),
            CallbackQueryHandler(cb_admin_delete,pattern="^admin_delete$"),
            CallbackQueryHandler(cb_admin_dates,pattern="^admin_dates$"),
            CallbackQueryHandler(cb_exp_add,pattern="^exp_add$"),
            CallbackQueryHandler(cb_exp_delete,pattern="^exp_del$"),
            CallbackQueryHandler(cb_ad_exp_add,pattern="^ad_exp_add$"),
            CallbackQueryHandler(cb_liq_type,pattern="^liq_(own|credit)$"),
            CallbackQueryHandler(cb_deal_editor,pattern="^deal_editor$"),
            CallbackQueryHandler(cb_liq_del,pattern="^liq_del$"),
            CallbackQueryHandler(cb_cspread,pattern="^cspread_(CZK|EUR)$")],
        states={
            AUTH_CODE:[MessageHandler(filters.TEXT&~filters.COMMAND,auth_code_handler)],
            DEAL_TYPE:[CallbackQueryHandler(deal_type_handler,pattern="^type_")],
            DEAL_CLIENT_TYPE:[CallbackQueryHandler(deal_client_type_handler,pattern="^ctype_")],
            DEAL_CLIENT_NAME:[MessageHandler(filters.TEXT&~filters.COMMAND,deal_client_name_handler)],
            DEAL_FROM_CCY:[CallbackQueryHandler(deal_from_ccy_handler,pattern="^from_")],
            DEAL_FROM_AMT:[MessageHandler(filters.TEXT&~filters.COMMAND,deal_from_amt_handler)],
            DEAL_TO_CCY:[CallbackQueryHandler(deal_to_ccy_handler,pattern="^to_")],
            DEAL_TO_AMT:[MessageHandler(filters.TEXT&~filters.COMMAND,deal_to_amt_handler)],
            DEAL_CUSTOM_DATE:[MessageHandler(filters.TEXT&~filters.COMMAND,deal_custom_date_handler)],
            DEAL_XE_RATE:[MessageHandler(filters.TEXT&~filters.COMMAND,deal_xe_rate_handler)],
            DEAL_TX_HASH:[MessageHandler(filters.TEXT&~filters.COMMAND,deal_tx_hash_handler)],
            DEAL_CONFIRM:[CallbackQueryHandler(deal_confirm_handler,pattern="^confirm_deal$"),
                          CallbackQueryHandler(deal_cancel_handler,pattern="^cancel_deal$")],
            MANUAL_RATE_PAIR:[CallbackQueryHandler(manual_rate_pair_handler,pattern="^mrate_")],
            MANUAL_RATE_VALUE:[MessageHandler(filters.TEXT&~filters.COMMAND,manual_rate_value_handler)],
            NOTE_TEXT:[MessageHandler(filters.TEXT&~filters.COMMAND,note_text_handler)],
            CONVERTER_FROM:[CallbackQueryHandler(conv_from_handler,pattern="^conv_from_")],
            CONVERTER_AMT:[MessageHandler(filters.TEXT&~filters.COMMAND,conv_amt_handler)],
            CONVERTER_TO:[CallbackQueryHandler(conv_to_handler,pattern="^conv_to_")],
            DELETE_DEAL_ID:[MessageHandler(filters.TEXT&~filters.COMMAND,delete_deal_handler)],
            EDIT_DATE_ID:[MessageHandler(filters.TEXT&~filters.COMMAND,edit_date_id_handler)],
            EDIT_DATE_FIELD:[CallbackQueryHandler(edit_date_field_handler,pattern="^edf_")],
            EDIT_DATE_VALUE:[MessageHandler(filters.TEXT&~filters.COMMAND,edit_date_value_handler)],
            EXP_CCY:[CallbackQueryHandler(exp_ccy_handler,pattern="^expccy_")],
            EXP_AMT:[MessageHandler(filters.TEXT&~filters.COMMAND,exp_amt_handler)],
            EXP_NOTE:[MessageHandler(filters.TEXT&~filters.COMMAND,exp_note_handler)],
            EXP_DELETE_ID:[MessageHandler(filters.TEXT&~filters.COMMAND,exp_delete_handler)],
            AD_EXP_CCY:[CallbackQueryHandler(ad_exp_ccy_handler,pattern="^adexpccy_")],
            AD_EXP_AMT:[MessageHandler(filters.TEXT&~filters.COMMAND,ad_exp_amt_handler)],
            AD_EXP_NOTE:[MessageHandler(filters.TEXT&~filters.COMMAND,ad_exp_note_handler)],
            LIQ_CCY:[CallbackQueryHandler(liq_ccy_handler,pattern="^liqccy_")],
            LIQ_AMT:[MessageHandler(filters.TEXT&~filters.COMMAND,liq_amt_handler)],
            LIQ_NOTE:[MessageHandler(filters.TEXT&~filters.COMMAND,liq_note_handler)],
            DEDIT_ID:[MessageHandler(filters.TEXT&~filters.COMMAND,dedit_id_handler)],
            DEDIT_FIELD:[CallbackQueryHandler(dedit_field_handler,pattern="^dedit_f_")],
            DEDIT_VALUE:[CallbackQueryHandler(dedit_value_cb_handler,pattern="^dedit_v_"),
                         MessageHandler(filters.TEXT&~filters.COMMAND,dedit_value_text_handler)],
            LIQ_DELETE_ID:[MessageHandler(filters.TEXT&~filters.COMMAND,liq_delete_handler)],
            CUSTOM_SPREAD_VALUE:[MessageHandler(filters.TEXT&~filters.COMMAND,cspread_value_handler)]},
        fallbacks=[CommandHandler("cancel", cmd_cancel),
                    CallbackQueryHandler(deal_cancel_handler,pattern="^cancel_deal$"),
                    CallbackQueryHandler(_fallback_end(cb_expenses_menu),pattern="^expenses_menu$"),
                    CallbackQueryHandler(_fallback_end(cb_ads_menu),pattern="^ads_menu$"),
                    CallbackQueryHandler(_fallback_end(cb_liq_sources),pattern="^liq_sources$"),
                    CallbackQueryHandler(_fallback_end(cb_main_menu),pattern="^main_menu$")],
        per_message=False, allow_reentry=True)
    app.add_handler(conv)
    app.add_handler(CommandHandler("add_liquidity", cmd_add_liquidity))
    app.add_handler(CommandHandler("withdraw", cmd_withdraw_liquidity))
    app.add_handler(CommandHandler("liq_list", cmd_liq_list))
    app.add_handler(CommandHandler("backup", cmd_backup))
    app.add_handler(CommandHandler("reconcile", cmd_reconcile))
    app.add_handler(CommandHandler("capital", cmd_capital))
    app.add_handler(CommandHandler("set_anchor", cmd_set_anchor))
    app.add_handler(MessageHandler(filters.Document.ALL, on_admin_document))
    app.add_handler(CallbackQueryHandler(cb_restore_confirm, pattern="^restore_db_(yes|no)$"))
    for pat,fn in [
        ("^main_menu$",cb_main_menu),("^balance$",cb_balance),
        ("^stats_menu$",cb_stats_menu),("^stats_",cb_stats),
        ("^rates$",cb_rates),("^report_menu$",cb_report_menu),
        ("^report_(day|week|month|year)$",cb_report_dispatch),
        ("^history$",cb_history_menu),("^hist_",cb_history_show),
        ("^trends$",cb_trends),("^trend_",cb_trend_show),
        ("^pnl_vwap$",cb_pnl_vwap),("^pnl_(week|month|quarter|year|all)$",cb_pnl_show),
        ("^liquidity$",cb_liquidity),
        ("^mgr_stats$",cb_mgr_stats),("^mgr_",cb_mgr_show),
        ("^pricing$",cb_pricing),("^intelligence$",cb_intelligence),
        ("^notes_menu$",cb_notes_menu),("^notes_view$",cb_notes_view),
        ("^receipts$",cb_receipts),("^rcpt_",cb_receipts_show),
        ("^admin$",cb_admin),("^admin_gencode$",cb_admin_gencode),
        ("^admin_users$",cb_admin_users),("^admin_last5$",cb_admin_last5),
        ("^admin_audit$",cb_admin_audit),
        ("^cashflow$",cb_cashflow),("^cf_",cb_cashflow_show),
        ("^expenses_menu$",cb_expenses_menu),("^exp_list$",cb_exp_list),
        ("^clients_menu$",cb_clients_menu),("^cl_(day|week|month|all)$",cb_clients_show),("^cl_seg$",cb_clients_seg),
        ("^ads_menu$",cb_ads_menu),("^ad_(day|week|month|all)$",cb_ads_show),
        ("^liq_sources$",cb_liq_sources),
        ("^sec_panic$",cb_sec_panic),("^sec_panic_confirm$",cb_sec_panic_confirm),
        ("^sec_data$",cb_sec_data),("^sec_data_hide$",cb_sec_data_hide),
        ("^sec_data_restore$",cb_sec_data_restore),
        ("^cspread_reset$",cb_cspread_reset),
        ("^cost_basis$",cb_cost_basis),("^cost_last5_buy$",cb_cost_last5_buy),("^cost_last5_sell$",cb_cost_last5_sell),
        ("^capital$",cb_capital)]:
        app.add_handler(CallbackQueryHandler(fn,pattern=pat))
    app.add_handler(CallbackQueryHandler(cb_post_channel, pattern=r"^post_ch_\d+$"))
    # UX v2: быстрый ввод, разделы меню, P&L-детали, undo
    for pat, fn in [
        ("^qd_save$", cb_quick_confirm), ("^qd_src$", cb_quick_toggle_src),
        ("^qd_date$", cb_quick_date), ("^qd_d_(now|yest|back)$", cb_quick_date_set),
        ("^qd_cancel$", cb_quick_cancel),
        ("^menu_(deals|money|analytics|more)$", cb_menu_section),
        ("^pnl_full_(week|month|quarter|year|all)$", cb_pnl_full),
        (r"^undo_yes_\d+$", cb_undo_confirm)]:
        app.add_handler(CallbackQueryHandler(fn, pattern=pat))
    app.add_handler(CommandHandler("undo", cmd_undo))
    app.add_handler(CommandHandler("cancel", cmd_cancel))  # и вне диалога
    app.add_error_handler(on_error)

    # Reply-keyboard taps (persistent bottom keyboard) — must be AFTER ConversationHandler
    # so conversation text inputs aren't swallowed. Filter to only known button texts.
    _reply_filter = filters.TEXT & ~filters.COMMAND & filters.Regex(
        "^(" + "|".join(re.escape(k) for k in REPLY_BTN_TO_CB.keys()) + ")$"
    )
    app.add_handler(MessageHandler(_reply_filter, reply_kb_router))
    _REPLY_CB_FUNCS.update({
        "balance": cb_balance, "pnl_vwap": cb_pnl_vwap, "stats_menu": cb_stats_menu,
        "rates": cb_rates, "cost_basis": cb_cost_basis, "liquidity": cb_liquidity,
        "capital": cb_capital})
    # Быстрый ввод сделки строкой — САМЫЙ низкий приоритет в group 0:
    # сначала ConversationHandler, потом reply-кнопки, потом этот парсер
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, cb_quick_deal_preview))

    sch = AsyncIOScheduler()
    sch.add_job(send_daily_report,"cron",hour=DAILY_REPORT_HOUR,minute=DAILY_REPORT_MINUTE,args=[app])
    sch.add_job(save_rate_snapshot,"cron",minute=0)
    sch.add_job(save_equity_snapshot,"cron",minute=5)
    sch.add_job(save_liquidity_snapshot,"cron",minute=5)
    sch.add_job(lambda:refresh_rates(),"interval",minutes=15)  # auto-refresh rates
    sch.add_job(daily_close,"cron",hour=23,minute=55,args=[app])  # закрытие дня: сверка+капитал+бэкап

    async def post_init(a):
        save_rate_snapshot(); save_equity_snapshot(); save_liquidity_snapshot()
        sch.start(); log.info("Scheduler OK")
        # Register bot commands so Telegram shows them on `/` menu
        try:
            await a.bot.set_my_commands([
                BotCommand("start",         "Открыть главное меню"),
                BotCommand("add_liquidity", "Добавить ликвидность: /add_liquidity USDT 1000 [own|credit] [note]"),
                BotCommand("withdraw",      "Снять ликвидность: /withdraw USDT 1000"),
                BotCommand("liq_list",      "Показать историю ликвидности"),
                BotCommand("backup",        "Прислать бэкап базы файлом (admin)"),
                BotCommand("reconcile",     "Полная сверка целостности базы (admin)"),
                BotCommand("capital",       "Капитал: старт + P&L − расходы"),
                BotCommand("set_anchor",    "Новый якорь капитала: /set_anchor <USDT> [дата] (admin)"),
                BotCommand("undo",          "Отменить последнюю сделку (окно 10 мин)"),
                BotCommand("cancel",        "Выйти из текущего диалога"),
            ])
            log.info("Bot commands registered")
        except Exception as e:
            log.warning(f"set_my_commands: {e}")

    app.post_init = post_init
    log.info("SwapMe v6.4 starting...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
