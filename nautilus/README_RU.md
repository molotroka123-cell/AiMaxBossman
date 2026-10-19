# NAUTILUS / NAUTILUS_BTC — Аналитический контур и генератор карточек (Read-Only / Shadow Account)

Модуль `nautilus/` реализует в составе **Bossman** аналитическое ядро стратегии **NAUTILUS** (а также крипто-рукава **NAUTILUS_BTC** и сравнительного профиля **Silent-Killer**) с рендерингом тёмной дашборд-карточки из 6 плиток и отправкой отчёта в **Telegram-пульт владельца**.

---

## 🔒 Соблюдение Вечного запрета №1 (Safety Gate)

Модуль работает **строго в режиме чтения и модельного расчёта (`READ_ONLY_SHADOW_ANALYTICS`)**:
1. **Никаких переводов средств** (`money_transfers = PROHIBITED`).
2. **Никакого выставления живых ордеров у брокеров или на биржах** (`live_order_execution = DISABLED`).
3. **Никаких приватных ключей, сид-фраз или API-ключей торговых счетов** (`private_keys_loaded = False`).
4. Отправка в Telegram-пульт использует штатный однопользовательский гейт `bcc.direct_gen.pult.load_target` (отправка разрешена только если в `config.json` ровно один получатель с ролью `owner`).

---

## 📦 Структура пакета `nautilus/`

| Файл | Назначение |
|---|---|
| `nautilus/__init__.py` | Публичный API пакета `nautilus` |
| `nautilus/metrics.py` | Расчёт 6 ключевых метрик (`CAGR`, `SHARPE`, `SORTINO`, `MAX DRAWDOWN`, `CALMAR`, `$1 BECOMES`), беты, волатильности, серии прибыльных недель и мульти-оконных сводок (`YTD`, `Since Inception`, `Last 26 Trading Sessions`) |
| `nautilus/portfolio.py` | 5 операционных блоков NAUTILUS: `market_regime`, `leverage_exposure`, `trade_ticket`, `portfolio_comparison` (Long + APEX Short Hedge vs `Silent-Killer`) и `stress_audit` |
| `nautilus/card.py` | Генератор тёмной PNG-карточки дашборда (`1200x680`, 6 плиток в сетке `3x2`, оранжевый акцент `#f97316` + серое сравнение с бенчмарком `SPY`/`BTC` под каждой метрикой) |
| `nautilus/telegram_report.py` | Форматирование HTML-отчёта для Telegram-пульта, CLI-интерфейс и безопасная отправка карточки + сводки владельцу |
| `nautilus/fixtures/nautilus_2026_sample.json` | Эталонная фикстура мульти-оконных метрик и модельного портфеля NAUTILUS (2026) |

---

## 📊 Мульти-оконные показатели в эталонной фикстуре

1. **6-плиточная карточка дашборда (`NAUTILUS vs SPY`):**
   - `CAGR`: **42.19%** (`SPY: 11.49%`)
   - `SHARPE`: **1.62** (`SPY: 0.69`)
   - `SORTINO`: **2.41** (`SPY: 0.94`)
   - `MAX DRAWDOWN`: **-23.62%** (`SPY: -33.72%`)
   - `CALMAR`: **1.79** (`SPY: 0.34`)
   - `$1 BECOMES`: **$13.34** (`SPY: $2.21`)
2. **YTD (`NAUTILUS_BTC vs BTC`):**
   - Доходность YTD: **+49.27%** против **-6.89% BTC** (Excess **+56.16%**)
   - Sharpe: **2.18** против **0.03 BTC**
   - Max DD: **-11.36%** против **-39.53% BTC**
   - Переход экспозиции: **`30% -> 100% long BTC`**
3. **Since Inception (`NAUTILUS vs SPY`):**
   - Доходность: **+22.41%** против **+14.43% SPY** (Excess Return **+7.98%**)
   - Sharpe: **2.86**, Volatility: **14.16%**, Max DD: **-6.75%**, Beta: **0.293**
4. **Last 26 Trading Sessions:**
   - Sharpe: **4.79** против **1.06 SPY**
   - Volatility: **8.48%** против **9.66% SPY**
   - Max DD: **-1.18%** против **-2.1% SPY**
   - Beta: **0.013**
5. **Winning Weeks Streak:**
   - **13+ weeks in a row** (14 подряд положительных недель в хвосте ряда)

---

## 🚀 Использование из командной строки

### 1. Вывести полный аналитический отчёт в консоль и сохранить PNG-карточку:
```powershell
python -m nautilus.telegram_report --card-out artifacts/nautilus_card.png
```

### 2. Отправить карточку и отчёт в Telegram-пульт владельца:
```powershell
python -m nautilus.telegram_report --card-out artifacts/nautilus_card.png --send-pult
```

### 3. Запуск тестов:
```powershell
python -m pytest tests/test_nautilus.py -q
```
