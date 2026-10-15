# SwapMe Exchange Bot — Инструкция по запуску

## Требования
- Python 3.11+
- Telegram Bot Token (получить у @BotFather)

---

## 1. Установка зависимостей

```bash
pip install -r requirements.txt
```

---

## 2. Настройка переменных окружения

Создай файл `.env` в папке бота (или задай переменные вручную):

```bash
# Токен бота от @BotFather
BOT_TOKEN=1234567890:ABCDefGhIJKlmNoPQRsTUVwXYZ

# Telegram ID администраторов (через запятую, без пробелов)
# Узнать свой ID: написать @userinfobot
ADMIN_IDS=123456789,987654321
```

Или задай перед запуском:
```bash
export BOT_TOKEN="твой_токен"
export ADMIN_IDS="твой_telegram_id"
```

---

## 3. Запуск

```bash
python bot.py
```

---

## 4. Как добавить менеджера

1. Открой бота в Telegram
2. Нажми **⚙️ Админ-панель**
3. Нажми **👤 Добавить менеджера (код)**
4. Скопируй одноразовый код и передай менеджеру
5. Менеджер открывает бота → вводит код → получает доступ

---

## 5. Команды бота

| Команда | Действие |
|---------|---------|
| `/start` | Запуск / авторизация |
| `/report` | Получить Excel отчёт за месяц |

Все остальные действия — через кнопки меню.

---

## 6. Автозапуск на сервере (systemd)

Создай файл `/etc/systemd/system/swapme.service`:

```ini
[Unit]
Description=SwapMe Exchange Bot
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/swapme_bot
Environment="BOT_TOKEN=твой_токен"
Environment="ADMIN_IDS=твой_id"
ExecStart=/usr/bin/python3 /home/ubuntu/swapme_bot/bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Затем:
```bash
sudo systemctl daemon-reload
sudo systemctl enable swapme
sudo systemctl start swapme
sudo systemctl status swapme
```

---

## 7. Рекомендуемый VPS

**Hetzner Cloud** — дата-центр в Германии, низкий пинг из Праги.
- CPX11: 2 CPU / 2GB RAM / 40GB SSD — ~4€/месяц
- Достаточно для бота + SQLite на десятки тысяч сделок

---

## 8. Резервное копирование БД

```bash
# Добавь в crontab (каждый день в 3:00)
0 3 * * * cp /home/ubuntu/swapme_bot/swapme.db /home/ubuntu/backups/swapme_$(date +%Y%m%d).db
```

---

## Структура данных

```
swapme.db
├── users       — авторизованные пользователи
├── auth_codes  — одноразовые коды для входа
├── deals       — все сделки (основная таблица)
└── rates       — кеш курсов валют
```

## Excel отчёт содержит

- **Лист СДЕЛКИ** — все операции за период с формулами спреда
- **Лист P&L по дням** — аналитика по дням + график
- **Лист БАЛАНСЫ** — остатки по USDT/EUR/USD/CZK (формулы SUMIF)
- **Лист КУРСЫ** — курсы на момент генерации отчёта
