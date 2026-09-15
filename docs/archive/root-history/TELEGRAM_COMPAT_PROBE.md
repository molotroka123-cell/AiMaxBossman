# TELEGRAM COMPATIBILITY PROBE
## AiMaxBossman × SwapMe — Master Prompt for AI Model

> **Версия:** 1.0 | **Дата:** 2026-09-06 | **Статус:** ACTIVE  
> **Цель:** Проверить совместимость Bossman с Telegram через живой SwapMe-бот на локальном железе  
> **Аудит:** каждый checkpoint → `git push` в `audits/tg_compat/`

---

## КОНТЕКСТ ДЛЯ МОДЕЛИ

Ты — автономный QA-агент проекта **AiMaxBossman**.  
У тебя есть доступ к локальному проекту **swapme** (Telegram-бот, уже подключён к живому Telegram API).  
Твоя задача: **провести полный Telegram compatibility audit** — найти все баги, несовместимости и граничные случаи.  
После каждого checkpoint — сам пишешь `AUDIT_TG_CHK_N.md` и пушишь в репо.

---

## SETUP

```bash
# 1. Перейди в SwapMe
cd ~/swapme   # твой путь

# 2. Запусти бот
 python bot.py &

# 3. Перейди в AiMaxBossman
cd ~/AiMaxBossman
git checkout claude/bossman-control-v03-43igbk

# 4. Создай папку для аудитов
mkdir -p audits/tg_compat

# 5. Установи зависимости
pip install python-telegram-bot
```

---

## CHECKPOINT 1 — Bot Liveness & Auth

```python
# tg_probe_chk1.py
import asyncio, os, json, datetime
from telegram import Bot

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or input("Введи BOT_TOKEN: ")

async def probe_chk1():
    results = []
    bot = Bot(token=BOT_TOKEN)

    # TEST 1.1 getMe
    try:
        me = await bot.get_me()
        results.append({"id": "TG-1.1", "name": "getMe auth", "status": "PASS",
                        "data": {"username": me.username, "id": me.id}})
    except Exception as e:
        results.append({"id": "TG-1.1", "name": "getMe auth", "status": "FAIL", "error": str(e)})

    # TEST 1.2 webhook_info — должен быть пустым если поллинг
    try:
        wh = await bot.get_webhook_info()
        results.append({"id": "TG-1.2", "name": "webhook_info", "status": "PASS",
                        "data": {"url": wh.url or "empty=polling", "pending": wh.pending_update_count}})
    except Exception as e:
        results.append({"id": "TG-1.2", "name": "webhook_info", "status": "FAIL", "error": str(e)})

    # TEST 1.3 getUpdates ping
    try:
        updates = await bot.get_updates(offset=0, timeout=1, limit=1)
        results.append({"id": "TG-1.3", "name": "getUpdates ping", "status": "PASS",
                        "data": {"pending": len(updates)}})
    except Exception as e:
        results.append({"id": "TG-1.3", "name": "getUpdates ping", "status": "FAIL", "error": str(e)})

    return results

results = asyncio.run(probe_chk1())
print(json.dumps(results, indent=2, ensure_ascii=False))
with open("audits/tg_compat/CHK1_raw.json", "w") as f:
    json.dump({"chk": 1, "ts": datetime.datetime.utcnow().isoformat(), "results": results}, f, indent=2)
```

**После прогона:**
```bash
git add audits/tg_compat/
git commit -m "audit(tg-compat): CHK1 bot liveness — $(date +%Y-%m-%dT%H:%M)"
git push
```

---

## CHECKPOINT 2 — Message Send/Receive Roundtrip

```python
# tg_probe_chk2.py
import asyncio, json, datetime, os
from telegram import Bot
from telegram.constants import ParseMode

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or input("BOT_TOKEN: ")
CHAT_ID = os.environ.get("TG_TEST_CHAT_ID") or input("TEST CHAT_ID: ")

async def probe_chk2():
    bot = Bot(token=BOT_TOKEN)
    results = []

    # 2.1 plain text
    try:
        msg = await bot.send_message(chat_id=CHAT_ID, text="[BOSSMAN_PROBE] CHK2 plain text")
        results.append({"id": "TG-2.1", "name": "sendMessage plain", "status": "PASS",
                        "data": {"msg_id": msg.message_id}})
    except Exception as e:
        results.append({"id": "TG-2.1", "name": "sendMessage plain", "status": "FAIL", "error": str(e)})

    # 2.2 MarkdownV2
    try:
        msg = await bot.send_message(chat_id=CHAT_ID,
                                     text="*BOSSMAN* `probe` CHK2",
                                     parse_mode=ParseMode.MARKDOWN_V2)
        results.append({"id": "TG-2.2", "name": "sendMessage MarkdownV2", "status": "PASS",
                        "data": {"msg_id": msg.message_id}})
    except Exception as e:
        results.append({"id": "TG-2.2", "name": "sendMessage MarkdownV2", "status": "FAIL", "error": str(e)})

    # 2.3 HTML
    try:
        msg = await bot.send_message(chat_id=CHAT_ID,
                                     text="<b>BOSSMAN</b> <code>CHK2</code>",
                                     parse_mode=ParseMode.HTML)
        results.append({"id": "TG-2.3", "name": "sendMessage HTML", "status": "PASS",
                        "data": {"msg_id": msg.message_id}})
    except Exception as e:
        results.append({"id": "TG-2.3", "name": "sendMessage HTML", "status": "FAIL", "error": str(e)})

    # 2.4 >4096 chars — Telegram limit
    try:
        await bot.send_message(chat_id=CHAT_ID, text="A" * 4100)
        results.append({"id": "TG-2.4", "name": "sendMessage >4096 chars",
                        "status": "FAIL (BUG: should reject or split)",
                        "note": "Message over 4096 chars accepted without split"})
    except Exception as e:
        results.append({"id": "TG-2.4", "name": "sendMessage >4096 chars",
                        "status": "PASS (rejected as expected)", "error": str(e)})

    # 2.5 empty message
    try:
        await bot.send_message(chat_id=CHAT_ID, text="")
        results.append({"id": "TG-2.5", "name": "sendMessage empty",
                        "status": "FAIL (BUG: empty accepted)"})
    except Exception as e:
        results.append({"id": "TG-2.5", "name": "sendMessage empty",
                        "status": "PASS (rejected)", "error": str(e)})

    return results

results = asyncio.run(probe_chk2())
print(json.dumps(results, indent=2, ensure_ascii=False))
with open("audits/tg_compat/CHK2_raw.json", "w") as f:
    json.dump({"chk": 2, "ts": datetime.datetime.utcnow().isoformat(), "results": results}, f, indent=2)
```

```bash
git add audits/tg_compat/ && git commit -m "audit(tg-compat): CHK2 messages" && git push
```

---

## CHECKPOINT 3 — Callback / InlineKeyboard

```python
# tg_probe_chk3.py
import asyncio, json, datetime, os
from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or input("BOT_TOKEN: ")
CHAT_ID = os.environ.get("TG_TEST_CHAT_ID") or input("CHAT_ID: ")

async def probe_chk3():
    bot = Bot(token=BOT_TOKEN)
    results = []

    # 3.1 базовая inline keyboard
    try:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ A", callback_data="bossman:probe:a"),
                                    InlineKeyboardButton("❌ B", callback_data="bossman:probe:b")]])
        msg = await bot.send_message(chat_id=CHAT_ID, text="[BOSSMAN PROBE] CHK3", reply_markup=kb)
        results.append({"id": "TG-3.1", "name": "InlineKeyboard send", "status": "PASS",
                        "data": {"msg_id": msg.message_id}})
    except Exception as e:
        results.append({"id": "TG-3.1", "name": "InlineKeyboard send", "status": "FAIL", "error": str(e)})

    # 3.2 callback_data >64 bytes — Telegram limit
    try:
        kb2 = InlineKeyboardMarkup([[InlineKeyboardButton("LONG", callback_data="b:" + "x"*62)]])
        await bot.send_message(chat_id=CHAT_ID, text="CHK3 long cb", reply_markup=kb2)
        results.append({"id": "TG-3.2", "name": "callback_data >64 bytes",
                        "status": "FAIL (BUG: accepted over limit)"})
    except Exception as e:
        results.append({"id": "TG-3.2", "name": "callback_data >64 bytes",
                        "status": "PASS (rejected)", "error": str(e)})

    # 3.3 editMessageText
    try:
        kb3 = InlineKeyboardMarkup([[InlineKeyboardButton("EDIT", callback_data="bossman:edit")]])
        msg3 = await bot.send_message(chat_id=CHAT_ID, text="BEFORE EDIT", reply_markup=kb3)
        await asyncio.sleep(1)
        edited = await bot.edit_message_text(chat_id=CHAT_ID, message_id=msg3.message_id,
                                              text="AFTER EDIT ✓", reply_markup=kb3)
        results.append({"id": "TG-3.3", "name": "editMessageText", "status": "PASS",
                        "data": {"text": edited.text}})
    except Exception as e:
        results.append({"id": "TG-3.3", "name": "editMessageText", "status": "FAIL", "error": str(e)})

    return results

results = asyncio.run(probe_chk3())
print(json.dumps(results, indent=2, ensure_ascii=False))
with open("audits/tg_compat/CHK3_raw.json", "w") as f:
    json.dump({"chk": 3, "ts": datetime.datetime.utcnow().isoformat(), "results": results}, f, indent=2)
```

```bash
git add audits/tg_compat/ && git commit -m "audit(tg-compat): CHK3 inline keyboard" && git push
```

---

## CHECKPOINT 4 — Rate Limits & Flood Control

```python
# tg_probe_chk4.py
import asyncio, json, datetime, os, time
from telegram import Bot
from telegram.error import RetryAfter, TelegramError

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or input("BOT_TOKEN: ")
CHAT_ID = os.environ.get("TG_TEST_CHAT_ID") or input("CHAT_ID: ")

async def probe_chk4():
    bot = Bot(token=BOT_TOKEN)
    results = []
    burst = []
    hit_429 = False
    sent = 0
    t0 = time.time()

    print("Sending 30 messages burst (>20/sec — should hit 429)...")
    for i in range(30):
        try:
            await bot.send_message(chat_id=CHAT_ID, text=f"[BOSSMAN BURST] #{i+1}")
            sent += 1
            await asyncio.sleep(0.05)
        except RetryAfter as e:
            hit_429 = True
            burst.append({"msg": i+1, "error": "RetryAfter", "retry_after": e.retry_after})
            await asyncio.sleep(e.retry_after)
        except TelegramError as e:
            burst.append({"msg": i+1, "error": str(e)})

    results.append({
        "id": "TG-4.1", "name": "Burst 30 msgs",
        "status": "PASS" if hit_429 else "WARN (no 429 — check throttle logic)",
        "data": {"sent": sent, "429_hit": hit_429, "elapsed_s": round(time.time()-t0, 2), "events": burst}
    })

    # Восстановление после флуда
    await asyncio.sleep(3)
    try:
        msg = await bot.send_message(chat_id=CHAT_ID, text="[BOSSMAN] Post-flood recovery")
        results.append({"id": "TG-4.2", "name": "Post-flood recovery", "status": "PASS",
                        "data": {"msg_id": msg.message_id}})
    except Exception as e:
        results.append({"id": "TG-4.2", "name": "Post-flood recovery", "status": "FAIL", "error": str(e)})

    return results

results = asyncio.run(probe_chk4())
print(json.dumps(results, indent=2, ensure_ascii=False))
with open("audits/tg_compat/CHK4_raw.json", "w") as f:
    json.dump({"chk": 4, "ts": datetime.datetime.utcnow().isoformat(), "results": results}, f, indent=2)
```

```bash
git add audits/tg_compat/ && git commit -m "audit(tg-compat): CHK4 rate limits" && git push
```

---

## CHECKPOINT 5 — Bossman Command Routing

```python
# tg_probe_chk5.py — запускать пока SwapMe bot активен
import asyncio, json, datetime, os
from telegram import Bot

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or input("BOT_TOKEN: ")
CHAT_ID = os.environ.get("TG_TEST_CHAT_ID") or input("CHAT_ID: ")

COMMANDS = [
    "/start", "/status", "/fleet", "/mission", "/audit", "/help",
    "/unknown_xyz_404",  # должен вернуть unknown handler
]

async def probe_chk5():
    bot = Bot(token=BOT_TOKEN)
    results = []

    for i, cmd in enumerate(COMMANDS):
        try:
            msg = await bot.send_message(chat_id=CHAT_ID, text=cmd)
            await asyncio.sleep(2)
            updates = await bot.get_updates(limit=5, timeout=3)
            bot_replies = [u for u in updates
                           if u.message and u.message.from_user and u.message.from_user.is_bot]
            results.append({
                "id": f"TG-5.{i+1}", "name": f"command {cmd}",
                "status": "PASS" if bot_replies else "WARN (no reply)",
                "data": {"msg_id": msg.message_id, "bot_replies": len(bot_replies),
                         "reply_preview": bot_replies[-1].message.text[:100] if bot_replies else None}
            })
        except Exception as e:
            results.append({"id": f"TG-5.{i+1}", "name": f"command {cmd}",
                            "status": "FAIL", "error": str(e)})
        await asyncio.sleep(1)

    return results

results = asyncio.run(probe_chk5())
print(json.dumps(results, indent=2, ensure_ascii=False))
with open("audits/tg_compat/CHK5_raw.json", "w") as f:
    json.dump({"chk": 5, "ts": datetime.datetime.utcnow().isoformat(), "results": results}, f, indent=2)
```

```bash
git add audits/tg_compat/ && git commit -m "audit(tg-compat): CHK5 command routing" && git push
```

---

## AUTO-PUSH SCRIPT — scripts/tg_audit_push.py

```python
#!/usr/bin/env python3
"""python scripts/tg_audit_push.py --chk 1"""
import argparse, json, subprocess, datetime
from pathlib import Path

def make_md(chk):
    raw = Path(f"audits/tg_compat/CHK{chk}_raw.json")
    if not raw.exists():
        return f"# CHK{chk}\n\nNO DATA — run tg_probe_chk{chk}.py first\n"
    data = json.loads(raw.read_text())
    results = data.get("results", [])
    ts = data.get("ts", "?")
    pf = [("PASS", "✅"), ("FAIL", "❌"), ("WARN", "⚠️")]
    counts = {k: sum(1 for r in results if k in r.get("status","")) for k,_ in pf}
    lines = [
        f"# TG COMPAT AUDIT — CHK{chk}",
        f"",
        f"**TS:** `{ts}` | ✅ {counts['PASS']} | ❌ {counts['FAIL']} | ⚠️ {counts['WARN']}",
        f"",
        "| ID | Test | Status | Note |",
        "|---|---|---|---|",
    ]
    for r in results:
        icon = "✅" if "PASS" in r.get("status","") else ("❌" if "FAIL" in r.get("status","") else "⚠️")
        note = (r.get("note") or r.get("error","") or "")[:80]
        lines.append(f"| `{r.get('id')}` | {r.get('name')} | {icon} {r.get('status')} | {note} |")
    if counts["FAIL"] > 0:
        lines += ["", "## Bugs Found", ""]
        for r in results:
            if "FAIL" in r.get("status",""):
                lines.append(f"- **{r.get('id')}** `{r.get('name')}` — {r.get('error') or r.get('note','')}")
    return "\n".join(lines) + "\n"

def push(chk):
    md = make_md(chk)
    out = Path(f"audits/tg_compat/AUDIT_TG_CHK{chk}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M")
    subprocess.run(["git", "add", "audits/tg_compat/"], check=True)
    subprocess.run(["git", "commit", "-m", f"audit(tg-compat): CHK{chk} — {ts}"], check=True)
    subprocess.run(["git", "push"], check=True)
    print(f"✅ CHK{chk} pushed")

parser = argparse.ArgumentParser()
parser.add_argument("--chk", required=True)
push(parser.parse_args().chk)
```

---

## RUN ALL — run_tg_compat_full.sh

```bash
#!/bin/bash
export TELEGRAM_BOT_TOKEN="YOUR_TOKEN_HERE"
export TG_TEST_CHAT_ID="YOUR_CHAT_ID"

echo "=== BOSSMAN x TELEGRAM COMPAT AUDIT ==="
for CHK in 1 2 3 4 5; do
    echo ">>> CHK${CHK}..."
    python tg_probe_chk${CHK}.py
    python scripts/tg_audit_push.py --chk ${CHK}
    sleep 2
done
echo "=== DONE ==="
echo "Audits: https://github.com/molotroka123-cell/AiMaxBossman/tree/claude/bossman-control-v03-43igbk/audits/tg_compat"
```

---

## Bug Taxonomy

| Категория | Признак | Severity |
|---|---|---|
| Auth | Bot token expired | P0 |
| Message limit | >4096 без split | P0 |
| Callback overflow | callback_data >64 bytes | P1 |
| Rate limit | Нет retry на 429 | P1 |
| Command routing | /команда не доходит | P0 |
| MarkdownV2 escape | Спецсимволы без escape | P1 |
| Flood | Burst без throttle — бан | P0 |
| Webhook conflict | Webhook + polling одновременно | P0 |

*Generated by Perplexity AI for AiMaxBossman — 2026-09-06*
