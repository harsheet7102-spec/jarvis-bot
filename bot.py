# -*- coding: utf-8 -*-
import os
import json
import asyncio
import base64
import httpx
import psycopg2
import groq as groq_module
from groq import Groq
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, MessageHandler, CommandHandler,
    filters, ContextTypes
)

# ============================================================
# ENV
# ============================================================
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY   = os.environ["GROQ_API_KEY"]
DATABASE_URL   = os.environ.get("database") or os.environ.get("DATABASE_URL")

client = Groq(api_key=GROQ_API_KEY)

# ============================================================
# DATABASE
# ============================================================
def get_con():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def init_db():
    con = get_con()
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id         SERIAL PRIMARY KEY,
            user_id    BIGINT NOT NULL,
            role       TEXT NOT NULL,
            content    TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS user_facts (
            user_id    BIGINT NOT NULL,
            key        TEXT NOT NULL,
            value      TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT NOW(),
            PRIMARY KEY (user_id, key)
        );
        CREATE TABLE IF NOT EXISTS summaries (
            id         SERIAL PRIMARY KEY,
            user_id    BIGINT NOT NULL,
            summary    TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS trades (
            id          SERIAL PRIMARY KEY,
            user_id     BIGINT NOT NULL,
            date        TEXT NOT NULL,
            symbol      TEXT NOT NULL,
            direction   TEXT NOT NULL,
            qty         REAL NOT NULL,
            entry_price REAL NOT NULL,
            exit_price  REAL,
            pnl         REAL,
            status      TEXT DEFAULT 'OPEN',
            notes       TEXT,
            created_at  TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS reminders (
            id          SERIAL PRIMARY KEY,
            user_id     BIGINT NOT NULL,
            chat_id     BIGINT NOT NULL,
            task        TEXT NOT NULL,
            fire_time   TIMESTAMP NOT NULL,
            repeat      TEXT DEFAULT 'none',
            done        BOOLEAN DEFAULT FALSE,
            created_at  TIMESTAMP DEFAULT NOW()
        );
    """)
    con.commit()
    cur.close()
    con.close()
    print("[DB] Initialized.")

# ── messages ──────────────────────────────────────────────
def append_message(user_id, role, content):
    try:
        con = get_con()
        cur = con.cursor()
        cur.execute(
            "INSERT INTO messages (user_id, role, content) VALUES (%s, %s, %s)",
            (user_id, role, content)
        )
        con.commit(); cur.close(); con.close()
    except Exception as e:
        print(f"[DB] append_message error: {e}")

def get_recent_messages(user_id, limit=20):
    try:
        con = get_con()
        cur = con.cursor()
        cur.execute("""
            SELECT role, content FROM (
                SELECT role, content, id FROM messages
                WHERE user_id = %s ORDER BY id DESC LIMIT %s
            ) sub ORDER BY id ASC
        """, (user_id, limit))
        rows = cur.fetchall()
        cur.close(); con.close()
        return [{"role": r, "content": c} for r, c in rows]
    except Exception as e:
        print(f"[DB] get_recent_messages error: {e}")
        return []

# ── facts ─────────────────────────────────────────────────
def save_facts(user_id, facts: dict):
    try:
        con = get_con()
        cur = con.cursor()
        for k, v in facts.items():
            cur.execute("""
                INSERT INTO user_facts (user_id, key, value, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (user_id, key)
                DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """, (user_id, k, str(v)))
        con.commit(); cur.close(); con.close()
    except Exception as e:
        print(f"[DB] save_facts error: {e}")

def get_all_facts(user_id):
    try:
        con = get_con()
        cur = con.cursor()
        cur.execute("SELECT key, value FROM user_facts WHERE user_id = %s ORDER BY key", (user_id,))
        rows = cur.fetchall()
        cur.close(); con.close()
        if not rows:
            return ""
        return "Known facts:\n" + "\n".join(f"- {k}: {v}" for k, v in rows)
    except Exception as e:
        print(f"[DB] get_all_facts error: {e}")
        return ""

# ── summaries ─────────────────────────────────────────────
def save_summary(user_id, summary):
    try:
        con = get_con()
        cur = con.cursor()
        cur.execute("INSERT INTO summaries (user_id, summary) VALUES (%s, %s)", (user_id, summary))
        con.commit(); cur.close(); con.close()
    except Exception as e:
        print(f"[DB] save_summary error: {e}")

def get_latest_summary(user_id):
    try:
        con = get_con()
        cur = con.cursor()
        cur.execute("SELECT summary FROM summaries WHERE user_id = %s ORDER BY id DESC LIMIT 1", (user_id,))
        row = cur.fetchone()
        cur.close(); con.close()
        return row[0] if row else ""
    except Exception as e:
        print(f"[DB] get_latest_summary error: {e}")
        return ""

def maybe_summarize(user_id):
    try:
        con = get_con()
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM messages WHERE user_id = %s", (user_id,))
        count = cur.fetchone()[0]
        cur.close(); con.close()
        if count % 20 != 0:
            return
        history = get_recent_messages(user_id, limit=40)
        text = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in history)
        prev = get_latest_summary(user_id)
        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": f"""Summarize this trading conversation and extract key facts about the user.
Previous summary: {prev or 'None'}
Conversation:
{text}

Respond ONLY in this JSON (no markdown):
{{"summary": "...", "facts": {{"key": "value"}}}}"""}],
            max_tokens=500
        )
        raw = resp.choices[0].message.content.strip()
        parsed = json.loads(raw)
        save_summary(user_id, parsed.get("summary", ""))
        facts = {k: v for k, v in parsed.get("facts", {}).items() if v and v != "..."}
        if facts:
            save_facts(user_id, facts)
    except Exception as e:
        print(f"[Memory] maybe_summarize error: {e}")

# ── trades ────────────────────────────────────────────────
def log_trade(user_id, date, symbol, direction, qty, entry_price, notes=""):
    try:
        con = get_con()
        cur = con.cursor()
        cur.execute("""
            INSERT INTO trades (user_id, date, symbol, direction, qty, entry_price, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
        """, (user_id, date, symbol, direction, qty, entry_price, notes))
        trade_id = cur.fetchone()[0]
        con.commit(); cur.close(); con.close()
        return trade_id
    except Exception as e:
        print(f"[DB] log_trade error: {e}")
        return None

def close_trade(user_id, trade_id, exit_price):
    try:
        con = get_con()
        cur = con.cursor()
        cur.execute("SELECT direction, qty, entry_price FROM trades WHERE id = %s AND user_id = %s", (trade_id, user_id))
        row = cur.fetchone()
        if not row:
            cur.close(); con.close()
            return None
        direction, qty, entry = row
        pnl = (exit_price - entry) * qty if direction == "BUY" else (entry - exit_price) * qty
        cur.execute("""
            UPDATE trades SET exit_price = %s, pnl = %s, status = 'CLOSED'
            WHERE id = %s AND user_id = %s
        """, (exit_price, pnl, trade_id, user_id))
        con.commit(); cur.close(); con.close()
        return pnl
    except Exception as e:
        print(f"[DB] close_trade error: {e}")
        return None

def get_trades(user_id, status=None):
    try:
        con = get_con()
        cur = con.cursor()
        if status:
            cur.execute("SELECT id, date, symbol, direction, qty, entry_price, exit_price, pnl, status, notes FROM trades WHERE user_id = %s AND status = %s ORDER BY id DESC", (user_id, status))
        else:
            cur.execute("SELECT id, date, symbol, direction, qty, entry_price, exit_price, pnl, status, notes FROM trades WHERE user_id = %s ORDER BY id DESC LIMIT 20", (user_id,))
        rows = cur.fetchall()
        cur.close(); con.close()
        return rows
    except Exception as e:
        print(f"[DB] get_trades error: {e}")
        return []

def get_pnl_summary(user_id):
    try:
        con = get_con()
        cur = con.cursor()
        cur.execute("SELECT COALESCE(SUM(pnl),0), COUNT(*) FROM trades WHERE user_id = %s AND status = 'CLOSED'", (user_id,))
        total_pnl, closed = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM trades WHERE user_id = %s AND pnl > 0", (user_id,))
        wins = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM trades WHERE user_id = %s AND status = 'OPEN'", (user_id,))
        open_count = cur.fetchone()[0]
        cur.close(); con.close()
        return total_pnl, closed, wins, open_count
    except Exception as e:
        print(f"[DB] get_pnl_summary error: {e}")
        return 0, 0, 0, 0

# ── reminders ─────────────────────────────────────────────
def db_add_reminder(user_id, chat_id, task, fire_time, repeat):
    try:
        con = get_con()
        cur = con.cursor()
        cur.execute("""
            INSERT INTO reminders (user_id, chat_id, task, fire_time, repeat)
            VALUES (%s, %s, %s, %s, %s)
        """, (user_id, chat_id, task, fire_time, repeat))
        con.commit(); cur.close(); con.close()
    except Exception as e:
        print(f"[DB] db_add_reminder error: {e}")

def db_get_due_reminders():
    try:
        con = get_con()
        cur = con.cursor()
        cur.execute("SELECT id, user_id, chat_id, task, fire_time, repeat FROM reminders WHERE done = FALSE AND fire_time <= NOW()")
        rows = cur.fetchall()
        cur.close(); con.close()
        return rows
    except Exception as e:
        print(f"[DB] db_get_due_reminders error: {e}")
        return []

def db_mark_reminder_done(reminder_id):
    try:
        con = get_con()
        cur = con.cursor()
        cur.execute("UPDATE reminders SET done = TRUE WHERE id = %s", (reminder_id,))
        con.commit(); cur.close(); con.close()
    except Exception as e:
        print(f"[DB] db_mark_reminder_done error: {e}")

def db_reschedule_reminder(reminder_id, new_time):
    try:
        con = get_con()
        cur = con.cursor()
        cur.execute("UPDATE reminders SET fire_time = %s WHERE id = %s", (new_time, reminder_id))
        con.commit(); cur.close(); con.close()
    except Exception as e:
        print(f"[DB] db_reschedule_reminder error: {e}")

def db_get_active_reminders(user_id):
    try:
        con = get_con()
        cur = con.cursor()
        cur.execute("SELECT id, task, fire_time, repeat FROM reminders WHERE user_id = %s AND done = FALSE ORDER BY fire_time ASC", (user_id,))
        rows = cur.fetchall()
        cur.close(); con.close()
        return rows
    except Exception as e:
        print(f"[DB] db_get_active_reminders error: {e}")
        return []

def db_clear_reminders(user_id):
    try:
        con = get_con()
        cur = con.cursor()
        cur.execute("UPDATE reminders SET done = TRUE WHERE user_id = %s AND done = FALSE", (user_id,))
        con.commit(); cur.close(); con.close()
    except Exception as e:
        print(f"[DB] db_clear_reminders error: {e}")

# ============================================================
# FUTURES PRICES
# ============================================================
FUTURES_SYMBOLS = {
    "MNQ":       ("MNQ=F",   "Micro Nasdaq Futures",    "USD"),
    "MGC":       ("MGC=F",   "Micro Gold Futures",      "USD"),
    "MES":       ("MES=F",   "Micro S&P 500 Futures",   "USD"),
    "MCL":       ("MCL=F",   "Micro Crude Oil Futures", "USD"),
    "NQ":        ("NQ=F",    "Nasdaq Futures",          "USD"),
    "ES":        ("ES=F",    "S&P 500 Futures",         "USD"),
    "GC":        ("GC=F",    "Gold Futures",            "USD"),
    "CL":        ("CL=F",    "Crude Oil Futures",       "USD"),
    "NIFTY":     ("^NSEI",   "Nifty 50",                "INR"),
    "BANKNIFTY": ("^NSEBANK","Bank Nifty",              "INR"),
    "SENSEX":    ("^BSESN",  "BSE Sensex",              "INR"),
}

# ============================================================
# FUTURES CALCULATOR
# Fixed: 1 contract, $50 SL, $100 TP (1:2 ratio)
#
# Contract specs ($ per 1 point, 1 contract):
#   MNQ — tick=0.25, tick value=$0.50 → $2.00/point
#   MGC — tick=0.10, tick value=$1.00 → $10.00/point
#   MES — tick=0.25, tick value=$1.25 → $5.00/point
# ============================================================
CALC_SPECS = {
    "MNQ": (0.25,  2.00,  "Micro Nasdaq"),
    "MGC": (0.10,  10.00, "Micro Gold"),
    "MES": (0.25,  5.00,  "Micro S&P 500"),
    "NQ":  (0.25,  20.00, "Nasdaq"),
    "ES":  (0.25,  50.00, "S&P 500"),
    "GC":  (0.10,  100.00,"Gold"),
}

SL_DOLLARS = 50.0
TP_DOLLARS  = 100.0
CONTRACTS   = 1

def calc_levels(symbol: str, entry: float, direction: str):
    sym = symbol.upper()
    if sym not in CALC_SPECS:
        return None
    tick_size, dollar_per_point, name = CALC_SPECS[sym]
    sl_points = SL_DOLLARS / dollar_per_point
    tp_points = TP_DOLLARS / dollar_per_point
    sl_ticks  = sl_points / tick_size
    tp_ticks  = tp_points / tick_size

    if direction.upper() in ("LONG", "BUY"):
        sl_price = entry - sl_points
        tp_price = entry + tp_points
    else:
        sl_price = entry + sl_points
        tp_price = entry - tp_points

    sl_price = round(round(sl_price / tick_size) * tick_size, 4)
    tp_price = round(round(tp_price / tick_size) * tick_size, 4)

    return {
        "symbol":     sym,
        "name":       name,
        "direction":  direction.upper(),
        "entry":      entry,
        "sl_price":   sl_price,
        "tp_price":   tp_price,
        "sl_points":  sl_points,
        "tp_points":  tp_points,
        "sl_ticks":   int(sl_ticks),
        "tp_ticks":   int(tp_ticks),
        "sl_dollars": SL_DOLLARS,
        "tp_dollars": TP_DOLLARS,
        "contracts":  CONTRACTS,
        "rr":         "1:2",
    }

def format_calc(c: dict) -> str:
    direction = "LONG" if c["direction"] in ("LONG", "BUY") else "SHORT"
    return (
        f"Trade Plan — {c['symbol']} {direction}\n"
        f"─────────────────────\n"
        f"Entry:      {c['entry']}\n"
        f"Stop Loss:  {c['sl_price']}  (-{c['sl_ticks']} ticks / ${c['sl_dollars']:.0f})\n"
        f"Take Profit:{c['tp_price']}  (+{c['tp_ticks']} ticks / ${c['tp_dollars']:.0f})\n"
        f"─────────────────────\n"
        f"Contracts:  {c['contracts']}\n"
        f"R:R Ratio:  {c['rr']}\n"
        f"Max Risk:   ${c['sl_dollars']:.0f}\n"
        f"Max Gain:   ${c['tp_dollars']:.0f}"
    )

async def get_price(symbol: str) -> str:
    try:
        import yfinance as yf
        sym_upper = symbol.upper()
        ticker_sym, name, currency = FUTURES_SYMBOLS.get(sym_upper, (sym_upper, sym_upper, "USD"))
        ticker = yf.Ticker(ticker_sym)
        info = ticker.fast_info
        price = getattr(info, "last_price", None)
        prev  = getattr(info, "previous_close", None)
        if not price:
            return f"Could not find price for {symbol}. Check the symbol."
        change     = price - prev if prev else 0
        change_pct = (change / prev * 100) if prev else 0
        direction  = "+" if change >= 0 else ""
        lines = [
            f"{sym_upper} — {name}",
            f"Price:  {currency} {price:,.2f}",
            f"Change: {direction}{change:,.2f} ({direction}{change_pct:.2f}%)",
            f"Note: 15 min delayed",
        ]
        return "\n".join(lines)
    except Exception as e:
        print(f"[Price] error: {e}")
        return "Could not fetch price right now. Try again in a moment."

# ============================================================
# REMINDER PARSER
# ============================================================
REMINDER_PARSE_PROMPT = """You are a reminder time parser. Extract the reminder from the user message.
Return ONLY valid JSON, no markdown, no explanation:
{
  "is_reminder": true or false,
  "task": "what to remind about",
  "time_str": "HH:MM in 24hr format or null",
  "delay_minutes": number or null,
  "repeat": "none" or "daily"
}"""

async def parse_reminder(text):
    try:
        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": REMINDER_PARSE_PROMPT},
                {"role": "user",   "content": text}
            ],
            max_tokens=150
        )
        raw = resp.choices[0].message.content.strip().replace("```json","").replace("```","")
        return json.loads(raw)
    except Exception as e:
        print(f"[Reminder] parse error: {e}")
        return {"is_reminder": False}

# ============================================================
# SYSTEM PROMPT
# ============================================================
SYSTEM_PROMPT = """You are SARA, a sharp and reliable trading assistant for Harsheet Garg, a futures trader from Indore.

YOUR PERSONALITY:
- Calm, direct, and genuinely helpful. You sound like a real person, not a bot.
- Warm but not over the top. Keep replies concise and to the point.
- You know markets well. You speak trading naturally.
- Occasionally use natural Hindi like: bhai, yaar, sahi hai — only when it fits.
- Never say "certainly", "of course", or "I would be happy to help".
- You care about Harsheet's trading discipline and risk management.

WHAT YOU HELP WITH:
- Logging trades (buy/sell entries and exits)
- Tracking P&L across trades
- Price checks on futures symbols
- Setting reminders and market alerts
- Daily trading summaries
- General trading conversation and analysis

TRADE LOGGING:
When Harsheet wants to log a trade, collect these details one at a time:
1. Symbol (e.g. MNQ, MGC)
2. Direction (BUY or SELL)
3. Quantity (number of contracts)
4. Entry price
5. Date (today if not specified)
6. Any notes (optional)
Then confirm and tell him to use /logtrade to save it, or extract the details and confirm.

When he closes a trade, ask for the exit price and calculate P&L automatically.

COMMANDS AVAILABLE TO HARSHEET:
/trades - view recent trades
/open - view open positions
/pnl - P&L summary
/price SYMBOL - get futures price
/reminders - view active reminders
/clearreminders - clear all reminders
/memory - what you know about him
/clearmemory - reset memory
/summary - today's trading recap

Always be honest about market risks. Keep the books accurate."""

# ============================================================
# BACKGROUND LOOPS
# ============================================================
summary_users = {}  # user_id -> chat_id

async def reminder_loop(bot):
    while True:
        try:
            due = db_get_due_reminders()
            for rid, uid, cid, task, fire_time, repeat in due:
                try:
                    await bot.send_message(
                        chat_id=cid,
                        text=f"Reminder, Harsheet:\n\n{task}"
                    )
                except Exception as e:
                    print(f"[Reminder] send error: {e}")
                if repeat == "daily":
                    db_reschedule_reminder(rid, fire_time + timedelta(days=1))
                else:
                    db_mark_reminder_done(rid)
        except Exception as e:
            print(f"[Reminder loop] error: {e}")
        await asyncio.sleep(30)

async def daily_summary_loop(bot):
    while True:
        try:
            now = datetime.now()
            if now.hour == 8 and now.minute == 0:
                for uid, cid in list(summary_users.items()):
                    await send_daily_summary(bot, uid, cid)
                await asyncio.sleep(70)
        except Exception as e:
            print(f"[Summary loop] error: {e}")
        await asyncio.sleep(30)

async def send_daily_summary(bot, user_id, chat_id):
    today = datetime.now().strftime("%A, %d %B %Y")
    total_pnl, closed, wins, open_count = get_pnl_summary(user_id)
    open_trades = get_trades(user_id, status="OPEN")
    losses = closed - wins if closed > 0 else 0
    win_rate = (wins / closed * 100) if closed > 0 else 0

    lines = [
        f"Good morning, Harsheet. Trading recap — {today}\n",
        f"Overall P&L:   {'+ ' if total_pnl >= 0 else ''}{total_pnl:,.2f}",
        f"Closed trades: {closed}  (W: {wins}  L: {losses}  Win rate: {win_rate:.0f}%)",
        f"Open positions: {open_count}",
    ]

    if open_trades:
        lines.append("\nOpen positions:")
        for t in open_trades[:5]:
            tid, date, sym, direction, qty, entry, exit_p, pnl, status, notes = t
            lines.append(f"  #{tid} {sym} {direction} x{qty} @ {entry}")

    active_rem = db_get_active_reminders(user_id)
    lines.append(f"\nActive reminders: {len(active_rem)}")
    lines.append("\nHave a focused session today.")

    try:
        await bot.send_message(chat_id=chat_id, text="\n".join(lines))
    except Exception as e:
        print(f"[Summary] send error: {e}")

# ============================================================
# COMMANDS
# ============================================================
async def reply(update, text):
    await update.message.reply_text(text)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    summary_users[user_id] = update.message.chat_id
    text = (
        "Hey Harsheet, I am SARA — your trading assistant.\n\n"
        "CALCULATOR:\n"
        "/calc MNQ long 21500 — instant trade plan\n"
        "/calc MGC short 3050 — gold short plan\n"
        "/calc MES long 6000  — MES long plan\n"
        "(Fixed: 1 contract, $50 SL, $100 TP, 1:2 R:R)\n\n"
        "Or just type: mnq long 21500\n\n"
        "TRADES:\n"
        "/trades — recent trades\n"
        "/open — open positions\n"
        "/pnl — P&L summary\n\n"
        "PRICES:\n"
        "/price MNQ — live futures price\n\n"
        "REMINDERS:\n"
        "/reminders — active reminders\n"
        "/clearreminders — clear all\n\n"
        "MEMORY:\n"
        "/memory — what I know about you\n"
        "/clearmemory — reset\n\n"
        "/summary — today's recap\n\n"
        "Just type anything to talk."
    )
    await reply(update, text)

async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    summary_users[user_id] = update.message.chat_id
    trades = get_trades(user_id)
    if not trades:
        await reply(update, "No trades logged yet. Tell me about a trade and I will help you record it.")
        return
    lines = ["Recent trades:\n"]
    for t in trades:
        tid, date, sym, direction, qty, entry, exit_p, pnl, status, notes = t
        if status == "CLOSED":
            pnl_str = f"  P&L: {'+ ' if pnl >= 0 else ''}{pnl:,.2f}"
        else:
            pnl_str = "  Status: OPEN"
        lines.append(f"#{tid} [{date}] {sym} {direction} x{qty} @ {entry}{pnl_str}")
    await reply(update, "\n".join(lines))

async def cmd_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    summary_users[user_id] = update.message.chat_id
    trades = get_trades(user_id, status="OPEN")
    if not trades:
        await reply(update, "No open positions right now.")
        return
    lines = ["Open positions:\n"]
    for t in trades:
        tid, date, sym, direction, qty, entry, exit_p, pnl, status, notes = t
        lines.append(f"#{tid} [{date}] {sym} {direction} x{qty} @ {entry}")
        if notes:
            lines.append(f"   Note: {notes}")
    await reply(update, "\n".join(lines))

async def cmd_pnl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    summary_users[user_id] = update.message.chat_id
    total_pnl, closed, wins, open_count = get_pnl_summary(user_id)
    losses   = closed - wins if closed > 0 else 0
    win_rate = (wins / closed * 100) if closed > 0 else 0
    sign     = "+ " if total_pnl >= 0 else ""
    lines = [
        "P&L Summary\n",
        f"Total P&L:   {sign}{total_pnl:,.2f}",
        f"Closed:      {closed}",
        f"Wins:        {wins}",
        f"Losses:      {losses}",
        f"Win rate:    {win_rate:.1f}%",
        f"Open now:    {open_count}",
    ]
    recent = get_trades(user_id, status="CLOSED")[:5]
    if recent:
        lines.append("\nLast 5 closed trades:")
        for t in recent:
            tid, date, sym, direction, qty, entry, exit_p, pnl, status, notes = t
            sign2 = "+ " if pnl >= 0 else ""
            lines.append(f"  #{tid} {sym} {direction} — {sign2}{pnl:,.2f}")
    await reply(update, "\n".join(lines))

async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    summary_users[user_id] = update.message.chat_id
    args = context.args
    if not args:
        syms = ", ".join(FUTURES_SYMBOLS.keys())
        await reply(update, f"Which symbol? e.g. /price MNQ\n\nAvailable: {syms}")
        return
    symbol = args[0].upper()
    await update.message.reply_text(f"Checking {symbol}...")
    result = await get_price(symbol)
    await reply(update, result)

async def cmd_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    summary_users[user_id] = update.message.chat_id
    active = db_get_active_reminders(user_id)
    if not active:
        await reply(update, "No active reminders.")
        return
    lines = ["Active reminders:\n"]
    for i, (rid, task, fire_time, repeat) in enumerate(active, 1):
        repeat_label = "daily" if repeat == "daily" else "one-time"
        lines.append(f"{i}. {task}\n   {fire_time.strftime('%d %b %I:%M %p')} — {repeat_label}")
    await reply(update, "\n".join(lines))

async def cmd_clearreminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    db_clear_reminders(user_id)
    await reply(update, "All reminders cleared.")

async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    facts   = get_all_facts(user_id)
    summary = get_latest_summary(user_id)
    if not facts and not summary:
        await reply(update, "No memory yet. Keep chatting and I will start building a picture of your trading style.")
        return
    lines = ["Here is what I know about you, Harsheet:\n"]
    if facts:
        lines.append(facts)
    if summary:
        lines.append(f"\nRecent context:\n{summary}")
    await reply(update, "\n".join(lines))

async def cmd_clearmemory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    try:
        con = get_con()
        cur = con.cursor()
        cur.execute("DELETE FROM user_facts WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM summaries WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM messages WHERE user_id = %s", (user_id,))
        con.commit(); cur.close(); con.close()
    except Exception as e:
        print(f"[DB] clearmemory error: {e}")
    await reply(update, "Memory cleared. Fresh start.")

async def cmd_calc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    summary_users[user_id] = update.message.chat_id
    args = context.args
    if not args or len(args) < 3:
        await reply(update,
            "Usage: /calc SYMBOL DIRECTION ENTRY\n\n"
            "Examples:\n"
            "  /calc MNQ long 21500\n"
            "  /calc MGC short 3050.5\n"
            "  /calc MES long 6000\n\n"
            "Fixed rules: 1 contract, $50 SL, $100 TP (1:2)"
        )
        return
    symbol    = args[0].upper()
    direction = args[1].upper()
    try:
        entry = float(args[2])
    except ValueError:
        await reply(update, "Entry price must be a number. e.g. /calc MNQ long 21500")
        return
    if symbol not in CALC_SPECS:
        supported = ", ".join(CALC_SPECS.keys())
        await reply(update, f"{symbol} is not supported.\nSupported: {supported}")
        return
    if direction not in ("LONG", "BUY", "SHORT", "SELL"):
        await reply(update, "Direction must be LONG or SHORT.")
        return
    result = calc_levels(symbol, entry, direction)
    await reply(update, format_calc(result))

async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    chat_id = update.message.chat_id
    summary_users[user_id] = chat_id
    await update.message.reply_text("Pulling your recap...")
    await send_daily_summary(context.bot, user_id, chat_id)

# ============================================================
# MAIN MESSAGE HANDLER
# ============================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id     = update.message.from_user.id
    chat_id     = update.message.chat_id
    user_msg    = update.message.text
    summary_users[user_id] = chat_id

    # ── Reminder detection ───────────────────────────────
    reminder_kw = ["remind", "reminder", "alert", "notify", "ping me", "don't let me forget",
                   "dont let me forget", "every day", "daily at"]
    if any(kw in user_msg.lower() for kw in reminder_kw):
        parsed = await parse_reminder(user_msg)
        if parsed.get("is_reminder"):
            now       = datetime.now()
            fire_time = None
            if parsed.get("delay_minutes"):
                fire_time = now + timedelta(minutes=int(parsed["delay_minutes"]))
            elif parsed.get("time_str"):
                try:
                    t = datetime.strptime(parsed["time_str"], "%H:%M")
                    fire_time = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
                    if fire_time <= now:
                        fire_time += timedelta(days=1)
                except Exception:
                    pass
            if not fire_time:
                await reply(update, "When should I remind you? Say something like 'at 3pm' or 'in 30 minutes'.")
                return
            repeat = parsed.get("repeat", "none")
            task   = parsed.get("task", user_msg)
            db_add_reminder(user_id, chat_id, task, fire_time, repeat)
            repeat_label = "daily" if repeat == "daily" else "one-time"
            time_label   = fire_time.strftime("%I:%M %p")
            await reply(update, f"Reminder set.\n\n{task}\n{time_label} — {repeat_label}")
            return

    # ── Natural language calc detection ─────────────────
    # Catches: "mnq long 21500", "long mgc at 3050", "short mes 6000"
    words      = user_msg.upper().split()
    calc_syms  = [w for w in words if w in CALC_SPECS]
    calc_dirs  = [w for w in words if w in ("LONG", "SHORT", "BUY", "SELL")]
    # Find a number that looks like a futures price (> 100)
    calc_price = None
    for w in words:
        w_clean = w.replace(",", "")
        try:
            val = float(w_clean)
            if val > 100:
                calc_price = val
                break
        except ValueError:
            pass
    if calc_syms and calc_dirs and calc_price:
        result = calc_levels(calc_syms[0], calc_price, calc_dirs[0])
        if result:
            await reply(update, format_calc(result))
            return

    # ── Price detection ──────────────────────────────────
    words = user_msg.upper().split()
    found = [w for w in words if w in FUTURES_SYMBOLS]
    price_kw = ["price", "rate", "how much", "check", "kitna", "quote"]
    if found and any(pw in user_msg.lower() for pw in price_kw):
        await update.message.reply_text(f"Checking {found[0]}...")
        result = await get_price(found[0])
        await reply(update, result)
        return

    # ── Trade close detection ────────────────────────────
    close_kw = ["closed", "exited", "squared off", "exit trade", "close trade", "book profit", "stop hit"]
    if any(kw in user_msg.lower() for kw in close_kw):
        # Let the AI handle this in conversation and guide through exit
        pass

    # ── AI response ──────────────────────────────────────
    try:
        facts   = get_all_facts(user_id)
        summary = get_latest_summary(user_id)
        system  = SYSTEM_PROMPT
        if facts or summary:
            system += "\n\n[Memory about Harsheet:\n"
            if facts:
                system += facts + "\n"
            if summary:
                system += f"Recent context: {summary}\n"
            system += "]"

        history = get_recent_messages(user_id, limit=20)
        append_message(user_id, "user", user_msg)
        history.append({"role": "user", "content": user_msg})

        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": system}] + history,
            max_tokens=500,
            temperature=0.7,
        )
        ai_reply = resp.choices[0].message.content.strip()
        append_message(user_id, "assistant", ai_reply)
        asyncio.create_task(asyncio.to_thread(maybe_summarize, user_id))
        await reply(update, ai_reply)

    except groq_module.RateLimitError:
        await reply(update, "Hitting rate limits. Give it a minute and try again.")
    except Exception as e:
        print(f"[Chat] error: {e}")
        await reply(update, "Something went wrong. Try again.")

# ============================================================
# STARTUP
# ============================================================
async def post_init(application):
    init_db()
    asyncio.create_task(reminder_loop(application.bot))
    asyncio.create_task(daily_summary_loop(application.bot))

app = (
    ApplicationBuilder()
    .token(TELEGRAM_TOKEN)
    .post_init(post_init)
    .build()
)

app.add_handler(CommandHandler("start",          cmd_start))
app.add_handler(CommandHandler("calc",           cmd_calc))
app.add_handler(CommandHandler("trades",         cmd_trades))
app.add_handler(CommandHandler("open",           cmd_open))
app.add_handler(CommandHandler("pnl",            cmd_pnl))
app.add_handler(CommandHandler("price",          cmd_price))
app.add_handler(CommandHandler("reminders",      cmd_reminders))
app.add_handler(CommandHandler("clearreminders", cmd_clearreminders))
app.add_handler(CommandHandler("memory",         cmd_memory))
app.add_handler(CommandHandler("clearmemory",    cmd_clearmemory))
app.add_handler(CommandHandler("summary",        cmd_summary))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

print("SARA trading bot is running.")
app.run_polling()
