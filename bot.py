from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
from groq import Groq
import base64
import httpx
from datetime import datetime, timedelta
import asyncio
import re
import json

# --- API KEYS ---
TELEGRAM_TOKEN = "8666756705:AAGf9EolzwKoAGu4UXho-aLkXBxmZepUVQc"
GROQ_API_KEY = "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"

# --- Setup Groq ---
client = Groq(api_key=GROQ_API_KEY)

# ============================================================
# DATA STORAGE
# ============================================================
user_memories = {}
user_moods = {}
current_mode = {}
reminders = []

# Long-term memory: key facts SARA remembers about Harsheet
long_term_memory = {}  # user_id -> list of facts (strings)

# Goals & Habits: user_id -> {"goals": [...], "habits": {...date: [...]}}
goals_data = {}

# Business logs for daily summary: user_id -> {"hostel": [...], "freight": [...], "trading": [...]}
business_logs = {}

# Registered users for daily summary
summary_users = {}  # user_id -> chat_id

# ============================================================
# SYSTEM PROMPTS
# ============================================================
SARA_BASE = """You are SARA, a sweet, warm and caring personal AI assistant to Harsheet Garg, a 24 year old businessman from Indore. Always call him Harsheet.
- You are sweet, warm, caring and emotionally intelligent
- You use gentle warm language and emojis like 💕 🌸 ✨ 😊
- You celebrate his wins and comfort him when sad
- You are always on his side
- Never sound robotic or formal
- Keep replies concise and clear"""

HOSTEL_PROMPT = SARA_BASE + """
YOU ARE NOW IN HOSTEL MODE for Shree Sainath Boys Hostel.
Help Harsheet manage:
- RENT: Track which tenants paid, who is pending, amounts
- ROOMS: Track room availability, occupied or vacant
- COMPLAINTS: Log and track maintenance complaints
When Harsheet logs data (rent paid, complaint, room update), acknowledge it warmly and note it was logged."""

FREIGHT_PROMPT = SARA_BASE + """
YOU ARE NOW IN FREIGHT MODE for Nitin Freight Carriers.
Help Harsheet manage:
- TRIPS: Log trip details like route, driver, cargo, date
- DRIVERS: Track driver names, phone numbers, availability
- BILLING: Track client payments and billing reminders
- SHIPMENTS: Track delivery status
When Harsheet logs data, acknowledge it warmly and note it was logged."""

TRADING_PROMPT = SARA_BASE + """
YOU ARE NOW IN TRADING MODE for KenshoWorld.
Help Harsheet manage:
- ORDERS: Track buy and sell orders
- P&L: Log profit and loss notes
- REMINDERS: Set market reminders
When Harsheet logs data, acknowledge it warmly and note it was logged."""

PERSONAL_PROMPT = SARA_BASE + """
You are in PERSONAL mode. Chat freely with Harsheet about anything.
- Check in on how he is feeling
- Be his personal companion 💕"""

REMINDER_PARSE_PROMPT = """You are a reminder parser. Extract reminder details from the user's message and return ONLY a JSON object with no extra text.

JSON format:
{
  "is_reminder": true/false,
  "task": "what to remind about",
  "time_str": "HH:MM in 24hr format or null",
  "repeat": "none" or "daily",
  "delay_minutes": number or null,
  "business": "hostel/freight/trading/personal or null"
}

Examples:
- "remind me at 5pm to call driver" → {"is_reminder":true,"task":"call driver","time_str":"17:00","repeat":"none","delay_minutes":null,"business":"freight"}
- "remind me every day at 9am to check rent" → {"is_reminder":true,"task":"check rent","time_str":"09:00","repeat":"daily","delay_minutes":null,"business":"hostel"}
- "remind me in 30 minutes to check order" → {"is_reminder":true,"task":"check order","time_str":null,"repeat":"none","delay_minutes":30,"business":"trading"}
- "what is the weather today" → {"is_reminder":false,"task":null,"time_str":null,"repeat":"none","delay_minutes":null,"business":null}

Return valid JSON only. No explanation."""

MEMORY_EXTRACT_PROMPT = """You are a memory extractor. From the user message, extract any personal facts about Harsheet worth remembering long-term (preferences, family, health, business details, personal goals, important dates etc).
Return ONLY a JSON object:
{"facts": ["fact1", "fact2"]} or {"facts": []} if nothing worth remembering.
Keep facts short and clear. No explanation."""

# ============================================================
# HELPERS
# ============================================================
def get_memory(user_id, mode="personal"):
    if user_id not in user_memories or current_mode.get(user_id) != mode:
        prompts = {"personal": PERSONAL_PROMPT, "hostel": HOSTEL_PROMPT, "freight": FREIGHT_PROMPT, "trading": TRADING_PROMPT}
        system = prompts.get(mode, PERSONAL_PROMPT)
        # Inject long-term memory into system prompt
        facts = long_term_memory.get(user_id, [])
        if facts:
            system += "\n\n[Things you remember about Harsheet]\n" + "\n".join(f"- {f}" for f in facts[-20:])
        user_memories[user_id] = [{"role": "system", "content": system}]
        current_mode[user_id] = mode
    return user_memories[user_id]


def detect_mood(message):
    msg = message.lower()
    if any(w in msg for w in ["sad", "crying", "upset", "unhappy", "heartbroken", "lonely", "hurt"]): return "sad"
    elif any(w in msg for w in ["angry", "frustrated", "mad", "annoyed", "hate"]): return "angry"
    elif any(w in msg for w in ["stressed", "anxious", "worried", "nervous", "overwhelmed"]): return "anxious"
    elif any(w in msg for w in ["tired", "exhausted", "sleepy", "drained"]): return "tired"
    elif any(w in msg for w in ["happy", "excited", "great", "amazing", "awesome", "yay"]): return "happy"
    return "neutral"


def get_mood_instruction(mood):
    return {
        "sad": "\n\n[Harsheet seems sad. Be extra gentle and comforting 💕]",
        "angry": "\n\n[Harsheet seems angry. Stay calm, validate his feelings 🤗]",
        "anxious": "\n\n[Harsheet seems anxious. Be very reassuring and calming 🌸]",
        "tired": "\n\n[Harsheet seems tired. Be soft and suggest rest 💤]",
        "happy": "\n\n[Harsheet is happy! Match his energy and celebrate 🎉]",
    }.get(mood, "")


def get_business_log(user_id):
    if user_id not in business_logs:
        business_logs[user_id] = {"hostel": [], "freight": [], "trading": []}
    return business_logs[user_id]


def log_business_entry(user_id, mode, message):
    if mode in ["hostel", "freight", "trading"]:
        log = get_business_log(user_id)
        log[mode].append({"time": datetime.now().strftime("%I:%M %p"), "entry": message[:100]})
        if len(log[mode]) > 50:
            log[mode] = log[mode][-50:]


def get_goals(user_id):
    if user_id not in goals_data:
        goals_data[user_id] = {"goals": [], "habits": {}, "completed_today": []}
    return goals_data[user_id]


# ============================================================
# ASYNC HELPERS
# ============================================================
async def parse_reminder(user_message):
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": REMINDER_PARSE_PROMPT}, {"role": "user", "content": user_message}]
        )
        raw = re.sub(r"```json|```", "", response.choices[0].message.content.strip()).strip()
        return json.loads(raw)
    except Exception as e:
        print(f"Reminder parse error: {e}")
        return {"is_reminder": False}


async def extract_memory(user_id, user_message):
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": MEMORY_EXTRACT_PROMPT}, {"role": "user", "content": user_message}]
        )
        raw = re.sub(r"```json|```", "", response.choices[0].message.content.strip()).strip()
        data = json.loads(raw)
        facts = data.get("facts", [])
        if facts:
            if user_id not in long_term_memory:
                long_term_memory[user_id] = []
            long_term_memory[user_id].extend(facts)
            if len(long_term_memory[user_id]) > 50:
                long_term_memory[user_id] = long_term_memory[user_id][-50:]
            print(f"Memory saved: {facts}")
    except Exception as e:
        print(f"Memory extract error: {e}")


async def get_crypto_price(symbol: str) -> str:
    try:
        symbol = symbol.upper().strip()
        # Map common names to alternative.me slugs
        name_map = {
            "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
            "BNB": "binancecoin", "DOGE": "dogecoin", "XRP": "ripple",
            "ADA": "cardano", "MATIC": "matic-network", "DOT": "polkadot",
            "LTC": "litecoin", "SHIB": "shiba-inu", "AVAX": "avalanche-2"
        }
        coin_slug = name_map.get(symbol, symbol.lower())

        async with httpx.AsyncClient() as http:
            # alternative.me — no API key, always free
            resp = await http.get(
                f"https://api.alternative.me/v2/ticker/{coin_slug}/",
                params={"convert": "INR"},
                timeout=10
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                # data is a dict keyed by coin id number
                coin_data = next(iter(data.values()), None)
                if coin_data:
                    quotes = coin_data.get("quotes", {})
                    usd = quotes.get("USD", {})
                    inr = quotes.get("INR", {})
                    price_usd = usd.get("price", 0)
                    price_inr = inr.get("price", 0)
                    change_24h = usd.get("percentage_change_24h", 0)
                    arrow = "📈" if change_24h >= 0 else "📉"
                    return (
                        f"{arrow} *{symbol}*\n"
                        f"💵 ${price_usd:,.2f} USD\n"
                        f"🇮🇳 ₹{price_inr:,.0f} INR\n"
                        f"24h: {change_24h:+.2f}%"
                    )
        return f"Couldn't fetch price for {symbol} 🌸"
    except Exception as e:
        print(f"Crypto error: {e}")
        return f"Couldn't fetch price right now 🌸"


# --- Futures / Stocks via Yahoo Finance (15min delayed, free) ---
FUTURES_SYMBOLS = {
    "MNQ": "MNQ=F",  # Micro E-mini Nasdaq-100
    "MGC": "MGC=F",  # Micro Gold
    "MES": "MES=F",  # Micro E-mini S&P 500
    "MCL": "MCL=F",  # Micro Crude Oil
    "M2K": "M2K=F",  # Micro Russell 2000
    "GC":  "GC=F",   # Gold Futures
    "CL":  "CL=F",   # Crude Oil Futures
    "ES":  "ES=F",   # E-mini S&P 500
    "NQ":  "NQ=F",   # E-mini Nasdaq-100
}

async def get_futures_price(symbol: str) -> str:
    try:
        symbol = symbol.upper().strip()
        ticker = FUTURES_SYMBOLS.get(symbol, f"{symbol}=F")
        async with httpx.AsyncClient() as http:
            resp = await http.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                params={"interval": "1m", "range": "1d"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                meta = data.get("chart", {}).get("result", [{}])[0].get("meta", {})
                price = meta.get("regularMarketPrice") or meta.get("previousClose")
                prev = meta.get("previousClose", price)
                currency = meta.get("currency", "USD")
                name = meta.get("longName") or meta.get("shortName") or symbol
                if price:
                    change = price - prev
                    change_pct = (change / prev * 100) if prev else 0
                    arrow = "📈" if change >= 0 else "📉"
                    inr_rate = 83.5
                    price_inr = price * inr_rate
                    return (
                        f"{arrow} *{symbol}* — {name}\n"
                        f"💵 ${price:,.2f} {currency}\n"
                        f"🇮🇳 ₹{price_inr:,.0f} INR (approx)\n"
                        f"Change: {change:+.2f} ({change_pct:+.2f}%)\n"
                        f"⚠️ Data may be 15 min delayed"
                    )
        return f"Couldn't fetch price for {symbol} 🌸"
    except Exception as e:
        print(f"Futures error: {e}")
        return f"Couldn't fetch price right now 🌸"


def schedule_reminder(user_id, chat_id, task, fire_time, repeat, business):
    reminders.append({
        "user_id": user_id, "chat_id": chat_id, "task": task,
        "fire_time": fire_time, "repeat": repeat, "business": business, "done": False
    })
    print(f"Reminder: {task} at {fire_time} repeat={repeat}")


# ============================================================
# BACKGROUND LOOPS
# ============================================================
async def reminder_loop(bot):
    while True:
        now = datetime.now().replace(second=0, microsecond=0)
        for r in reminders:
            if r["done"]: continue
            if now >= r["fire_time"].replace(second=0, microsecond=0):
                emoji = {"hostel": "🏠", "freight": "🚛", "trading": "📈", "personal": "🌸"}.get(r["business"], "🌸")
                try:
                    await bot.send_message(chat_id=r["chat_id"],
                        text=f"⏰ Hey Harsheet! Reminder time! {emoji}\n\n📝 {r['task']}\n\n💕 You've got this! ✨")
                except Exception as e:
                    print(f"Reminder send error: {e}")
                if r["repeat"] == "daily":
                    r["fire_time"] += timedelta(days=1)
                else:
                    r["done"] = True
        await asyncio.sleep(30)


async def daily_summary_loop(bot):
    """Send daily summary at 8 AM every day"""
    while True:
        now = datetime.now()
        # Calculate seconds until next 8 AM
        target = now.replace(hour=8, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        print(f"Daily summary in {wait_seconds/3600:.1f} hours")
        await asyncio.sleep(wait_seconds)

        # Send summary to all registered users
        today = datetime.now().strftime("%A, %d %B %Y")
        for user_id, chat_id in summary_users.items():
            try:
                await send_daily_summary(bot, user_id, chat_id, today)
            except Exception as e:
                print(f"Summary error for {user_id}: {e}")


async def send_daily_summary(bot, user_id, chat_id, today):
    logs = get_business_log(user_id)
    goals = get_goals(user_id)

    lines = [f"🌅 Good morning, Harsheet! Here's your daily summary for {today} 💕\n"]

    # Hostel
    hostel_entries = logs["hostel"]
    lines.append("🏠 *HOSTEL — Shree Sainath*")
    if hostel_entries:
        for e in hostel_entries[-5:]:
            lines.append(f"  • [{e['time']}] {e['entry']}")
    else:
        lines.append("  • No activity logged yesterday")

    # Freight
    lines.append("\n🚛 *FREIGHT — Nitin Carriers*")
    freight_entries = logs["freight"]
    if freight_entries:
        for e in freight_entries[-5:]:
            lines.append(f"  • [{e['time']}] {e['entry']}")
    else:
        lines.append("  • No activity logged yesterday")

    # Trading
    lines.append("\n📈 *TRADING — KenshoWorld*")
    trading_entries = logs["trading"]
    if trading_entries:
        for e in trading_entries[-5:]:
            lines.append(f"  • [{e['time']}] {e['entry']}")
    else:
        lines.append("  • No activity logged yesterday")

    # Goals
    lines.append("\n🎯 *GOALS & HABITS*")
    active_goals = [g for g in goals["goals"] if not g.get("done")]
    if active_goals:
        for g in active_goals[:5]:
            lines.append(f"  • {g['goal']}")
    else:
        lines.append("  • No active goals. Type /addgoal to add one!")

    # Completed today
    completed = goals.get("completed_today", [])
    if completed:
        lines.append(f"\n✅ Completed yesterday: {', '.join(completed)}")

    # Active reminders
    active_reminders = [r for r in reminders if r["user_id"] == user_id and not r["done"]]
    lines.append(f"\n⏰ Active reminders: {len(active_reminders)}")

    lines.append("\n💕 Have an amazing day, Harsheet! You're doing great! 🌸✨")

    # Clear yesterday's logs
    business_logs[user_id] = {"hostel": [], "freight": [], "trading": []}
    goals_data[user_id]["completed_today"] = []

    await bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode="Markdown")


# ============================================================
# SEND REPLY
# ============================================================
async def send_reply(update, reply_text):
    await update.message.reply_text(reply_text)


# ============================================================
# COMMANDS
# ============================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    chat_id = update.message.chat_id
    summary_users[user_id] = chat_id  # register for daily summary
    reply = """💕 Hello Harsheet! I'm SARA, your personal assistant!

*MODES:*
🌸 /personal — Personal chat & support
🏠 /hostel — Shree Sainath Boys Hostel
🚛 /freight — Nitin Freight Carriers
📈 /trading — KenshoWorld Trading

*REMINDERS:*
⏰ /reminders — See active reminders
❌ /clearreminders — Clear all reminders

*GOALS & HABITS:*
🎯 /goals — View your goals
➕ /addgoal <goal> — Add a new goal
✅ /donegoal <number> — Mark goal complete

*TRADING:*
💰 /price <symbol> — Live crypto price (e.g. /price BTC)

*MEMORY:*
🧠 /memory — See what I remember about you
🗑 /clearmemory — Clear my memory

*SUMMARY:*
📊 /summary — Get today's summary now

Daily summary sent at 8 AM every morning! 🌅
💕 Just type naturally for everything else!"""
    await update.message.reply_text(reply)


async def cmd_personal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    summary_users[user_id] = update.message.chat_id
    get_memory(user_id, "personal")
    await send_reply(update, "💕 Switched to Personal mode! How are you doing, Harsheet? 🌸")

async def cmd_hostel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    summary_users[user_id] = update.message.chat_id
    get_memory(user_id, "hostel")
    await send_reply(update, "🏠 Switched to Hostel mode! What do you need help with, Harsheet? 😊")

async def cmd_freight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    summary_users[user_id] = update.message.chat_id
    get_memory(user_id, "freight")
    await send_reply(update, "🚛 Switched to Freight mode! What do you need help with, Harsheet? 😊")

async def cmd_trading(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    summary_users[user_id] = update.message.chat_id
    get_memory(user_id, "trading")
    await send_reply(update, "📈 Switched to Trading mode! What do you need help with, Harsheet? 😊")

async def cmd_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    active = [r for r in reminders if r["user_id"] == user_id and not r["done"]]
    if not active:
        await send_reply(update, "✨ No active reminders right now, Harsheet! 💕")
        return
    lines = ["⏰ Your active reminders, Harsheet:\n"]
    for i, r in enumerate(active, 1):
        emoji = {"hostel": "🏠", "freight": "🚛", "trading": "📈", "personal": "🌸"}.get(r["business"], "🌸")
        repeat_label = "🔁 Daily" if r["repeat"] == "daily" else "1️⃣ One-time"
        lines.append(f"{i}. {emoji} {r['task']}\n   🕐 {r['fire_time'].strftime('%I:%M %p')} — {repeat_label}")
    await send_reply(update, "\n".join(lines))

async def cmd_clearreminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    count = sum(1 for r in reminders if r["user_id"] == user_id and not r["done"])
    for r in reminders:
        if r["user_id"] == user_id:
            r["done"] = True
    await send_reply(update, f"🗑️ Cleared {count} reminder(s)! All clean, Harsheet 💕")

async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await send_reply(update, "💕 Tell me what to fetch, Harsheet!\n\nCrypto: /price BTC /price ETH\nFutures: /price MNQ /price MGC /price MES 🌸")
        return
    symbol = args[0].upper()
    await update.message.reply_text(f"📡 Fetching price for {symbol}...")
    CRYPTO_LIST = {"BTC","ETH","SOL","BNB","DOGE","XRP","ADA","MATIC","DOT","LTC","SHIB","AVAX"}
    if symbol in FUTURES_SYMBOLS or symbol not in CRYPTO_LIST:
        result = await get_futures_price(symbol)
    else:
        result = await get_crypto_price(symbol)
    await send_reply(update, result)

async def cmd_goals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    goals = get_goals(user_id)
    active = [g for g in goals["goals"] if not g.get("done")]
    done = [g for g in goals["goals"] if g.get("done")]
    if not active and not done:
        await send_reply(update, "🎯 No goals yet, Harsheet! Add one with /addgoal <your goal> 💕")
        return
    lines = ["🎯 Your Goals, Harsheet:\n"]
    for i, g in enumerate(active, 1):
        lines.append(f"{i}. ⬜ {g['goal']}")
    for g in done[-3:]:
        lines.append(f"✅ ~~{g['goal']}~~")
    lines.append("\n✅ /donegoal <number> to mark complete!")
    await send_reply(update, "\n".join(lines))

async def cmd_addgoal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not context.args:
        await send_reply(update, "💕 Tell me the goal! e.g. /addgoal Read 10 pages daily 🌸")
        return
    goal_text = " ".join(context.args)
    goals = get_goals(user_id)
    goals["goals"].append({"goal": goal_text, "done": False, "added": datetime.now().strftime("%d %b")})
    await send_reply(update, f"🎯 Goal added, Harsheet!\n\n✨ '{goal_text}'\n\nYou've got this! 💕")

async def cmd_donegoal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not context.args or not context.args[0].isdigit():
        await send_reply(update, "💕 Tell me the goal number! e.g. /donegoal 1 🌸")
        return
    idx = int(context.args[0]) - 1
    goals = get_goals(user_id)
    active = [g for g in goals["goals"] if not g.get("done")]
    if idx < 0 or idx >= len(active):
        await send_reply(update, "🌸 That goal number doesn't exist, Harsheet!")
        return
    active[idx]["done"] = True
    goals["completed_today"].append(active[idx]["goal"])
    await send_reply(update, f"🎉 Amazing, Harsheet! You completed:\n\n✅ '{active[idx]['goal']}'\n\nSo proud of you! 💕✨")

async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    facts = long_term_memory.get(user_id, [])
    if not facts:
        await send_reply(update, "🧠 I don't have any memories saved yet, Harsheet! Chat with me and I'll start remembering things 💕")
        return
    lines = ["🧠 Here's what I remember about you, Harsheet:\n"]
    for i, f in enumerate(facts[-15:], 1):
        lines.append(f"{i}. {f}")
    await send_reply(update, "\n".join(lines))

async def cmd_clearmemory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    long_term_memory[user_id] = []
    # Reset system prompt to remove injected memory
    current_mode.pop(user_id, None)
    await send_reply(update, "🗑 Memory cleared, Harsheet! Starting fresh 💕🌸")

async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    chat_id = update.message.chat_id
    today = datetime.now().strftime("%A, %d %B %Y")
    await update.message.reply_text("📊 Generating your summary...")
    await send_daily_summary(context.bot, user_id, chat_id, today)


# ============================================================
# HANDLE TEXT MESSAGES
# ============================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.message.from_user.id
    chat_id = update.message.chat_id
    mode = current_mode.get(user_id, "personal")
    summary_users[user_id] = chat_id

    # --- Reminder detection ---
    reminder_keywords = ["remind", "reminder", "alert", "notify", "every day", "daily at", "don't let me forget"]
    if any(kw in user_message.lower() for kw in reminder_keywords):
        parsed = await parse_reminder(user_message)
        if parsed.get("is_reminder"):
            now = datetime.now()
            if parsed.get("delay_minutes"):
                fire_time = now + timedelta(minutes=int(parsed["delay_minutes"]))
            elif parsed.get("time_str"):
                hh, mm = map(int, parsed["time_str"].split(":"))
                fire_time = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                if fire_time <= now:
                    fire_time += timedelta(days=1)
            else:
                await send_reply(update, "🌸 I couldn't figure out the time. Could you say 'at 5pm' or 'in 20 minutes'? 💕")
                return
            business = parsed.get("business") or mode
            repeat = parsed.get("repeat", "none")
            task = parsed.get("task", user_message)
            schedule_reminder(user_id, chat_id, task, fire_time, repeat, business)
            emoji = {"hostel": "🏠", "freight": "🚛", "trading": "📈", "personal": "🌸"}.get(business, "🌸")
            repeat_label = "every day 🔁" if repeat == "daily" else "once ✅"
            time_label = f"in {parsed['delay_minutes']} minutes" if parsed.get("delay_minutes") else fire_time.strftime("%I:%M %p")
            await send_reply(update, f"⏰ Reminder set {repeat_label}\n\n{emoji} Task: {task}\n🕐 Time: {time_label}\n\n💕 I'll remind you!")
            return

    # --- Crypto & Futures price detection ---
    CRYPTO_LIST = {"BTC","ETH","SOL","BNB","DOGE","XRP","ADA","MATIC","DOT","LTC","SHIB","AVAX"}
    FUTURES_LIST = set(FUTURES_SYMBOLS.keys())
    all_symbols = CRYPTO_LIST | FUTURES_LIST
    found_symbols = [w for w in user_message.upper().split() if w in all_symbols]
    price_words = ["price", "rate", "cost", "how much"]
    if found_symbols and any(pw in user_message.lower() for pw in price_words):
        sym = found_symbols[0]
        await update.message.reply_text(f"📡 Fetching live price for {sym}...")
        if sym in FUTURES_LIST:
            result = await get_futures_price(sym)
        else:
            result = await get_crypto_price(sym)
        await send_reply(update, result)
        return

    # --- Normal chat + memory extraction + business logging ---
    mood = detect_mood(user_message)
    user_moods[user_id] = mood

    # Extract & save long-term memory in background
    asyncio.create_task(extract_memory(user_id, user_message))

    # Log to business if in business mode
    log_business_entry(user_id, mode, user_message)

    history = get_memory(user_id, mode)
    history.append({"role": "user", "content": user_message + get_mood_instruction(mood)})
    if len(history) > 21:
        history = [history[0]] + history[-20:]
        user_memories[user_id] = history

    response = client.chat.completions.create(model="llama-3.3-70b-versatile", messages=history)
    reply = response.choices[0].message.content
    history.append({"role": "assistant", "content": reply})
    print(f"SARA [{mode}]: {reply}")
    await send_reply(update, reply)


# ============================================================
# HANDLE VOICE & IMAGES
# ============================================================
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🌸 Voice messages are temporarily unavailable, Harsheet. Please type instead 💕")

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caption = update.message.caption or "What is in this image? Describe it in detail."
    await update.message.reply_text("Ooh let me have a look! 👀✨")
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    async with httpx.AsyncClient() as client_http:
        resp = await client_http.get(file.file_path)
        image_data = base64.standard_b64encode(resp.content).decode("utf-8")
    response = client.chat.completions.create(
        model="meta-llama/llama-4-scout-17b-16e-instruct",
        messages=[{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}},
            {"type": "text", "text": f"You are SARA, sweet caring assistant to Harsheet. Respond warmly. {caption}"}
        ]}]
    )
    await send_reply(update, response.choices[0].message.content)


# ============================================================
# START BOT
# ============================================================
async def post_init(application):
    asyncio.create_task(reminder_loop(application.bot))
    asyncio.create_task(daily_summary_loop(application.bot))

app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()
app.add_handler(CommandHandler("start", cmd_start))
app.add_handler(CommandHandler("personal", cmd_personal))
app.add_handler(CommandHandler("hostel", cmd_hostel))
app.add_handler(CommandHandler("freight", cmd_freight))
app.add_handler(CommandHandler("trading", cmd_trading))
app.add_handler(CommandHandler("reminders", cmd_reminders))
app.add_handler(CommandHandler("clearreminders", cmd_clearreminders))
app.add_handler(CommandHandler("price", cmd_price))
app.add_handler(CommandHandler("goals", cmd_goals))
app.add_handler(CommandHandler("addgoal", cmd_addgoal))
app.add_handler(CommandHandler("donegoal", cmd_donegoal))
app.add_handler(CommandHandler("memory", cmd_memory))
app.add_handler(CommandHandler("clearmemory", cmd_clearmemory))
app.add_handler(CommandHandler("summary", cmd_summary))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.add_handler(MessageHandler(filters.VOICE, handle_voice))
app.add_handler(MessageHandler(filters.PHOTO, handle_image))
print("SARA is running! 💕📊🧠🎯📈")
app.run_polling()
