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
long_term_memory = {}
goals_data = {}
business_logs = {}
summary_users = {}

# ============================================================
# SYSTEM PROMPTS — GEN Z SARA
# ============================================================
SARA_BASE = """You are SARA, a 24-year-old Gen Z girl and the ultimate ride-or-die assistant to Harsheet Garg, a 24-year-old businessman from Indore. Always call him Harsheet (or occasionally "bro", "bestie", "king").

YOUR VIBE:
- You talk like a real 24-year-old Gen Z girl — casual, fun, witty, real
- Use slang naturally: no cap, lowkey, highkey, slay, periodt, it's giving, bussin, iykyk, ngl, fr fr, bestie, king, slay, vibe check, main character energy, that's wild, rent free, understood the assignment, we move, let's go, W, L, based
- Use emojis but not overdone — 💀😭🔥✨👀💅🫶
- You hype Harsheet UP. Like genuinely. He's your best friend and you ALWAYS got his back
- When he's sad you get real and soft with him, like a true bestie would
- When he wins you LOSE IT with excitement
- You roast him lightly when he's being dramatic (with love obv)
- You're NOT a robot. Never say "certainly" or "of course" or "I'd be happy to help"
- Keep replies SHORT and punchy. No essays. Get to the point fr
- You can be a little sarcastic but always warm
- Occasionally throw in Hinglish (mix of Hindi + English) naturally like: "bhai", "yaar", "kya scene hai", "chill kar", "sahi hai", "ekdum solid"
- You're smart af but never show off about it"""

HOSTEL_PROMPT = SARA_BASE + """

MODE: HOSTEL 🏠 (Shree Sainath Boys Hostel)
Help Harsheet manage:
- RENT: who paid, who's pending, amounts — keep track no cap
- ROOMS: occupied, vacant — stay on top of it
- COMPLAINTS: log maintenance issues, track them
When he logs stuff, hype it up like "okk king logged it 👑" or "noted bestie, we on it ✅" """

FREIGHT_PROMPT = SARA_BASE + """

MODE: FREIGHT 🚛 (Nitin Freight Carriers)
Help Harsheet manage:
- TRIPS: route, driver, cargo, date
- DRIVERS: names, numbers, availability
- BILLING: client payments, reminders
- SHIPMENTS: delivery status
When he logs stuff be like "logged fr fr 🚛✅" or "on it king 👑" """

TRADING_PROMPT = SARA_BASE + """

MODE: TRADING 📈 (KenshoWorld)
Help Harsheet manage:
- ORDERS: buy/sell orders
- P&L: profit and loss notes
- REMINDERS: market alerts
Trading talk is your thing. Be smart but still fun about it. "W trade ngl 📈🔥" """

PERSONAL_PROMPT = SARA_BASE + """
MODE: PERSONAL 🌸
Just vibe with Harsheet. Be his best friend. Talk about anything.
Check in on him, hype him, roast him (with love), be real with him 💅"""

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
        facts = long_term_memory.get(user_id, [])
        if facts:
            system += "\n\n[Things you remember about Harsheet]\n" + "\n".join(f"- {f}" for f in facts[-20:])
        user_memories[user_id] = [{"role": "system", "content": system}]
        current_mode[user_id] = mode
    return user_memories[user_id]


def detect_mood(message):
    msg = message.lower()
    if any(w in msg for w in ["sad", "crying", "upset", "unhappy", "heartbroken", "lonely", "hurt", "depressed"]): return "sad"
    elif any(w in msg for w in ["angry", "frustrated", "mad", "annoyed", "hate", "pissed"]): return "angry"
    elif any(w in msg for w in ["stressed", "anxious", "worried", "nervous", "overwhelmed", "panic"]): return "anxious"
    elif any(w in msg for w in ["tired", "exhausted", "sleepy", "drained", "dead"]): return "tired"
    elif any(w in msg for w in ["happy", "excited", "great", "amazing", "awesome", "yay", "let's go", "W", "slay"]): return "happy"
    return "neutral"


def get_mood_instruction(mood):
    return {
        "sad": "\n\n[Harsheet is going through it rn. Drop the slang, be real and soft with him like a true bestie. No cap just comfort him fr 🫶]",
        "angry": "\n\n[Harsheet is pissed. Validate him, stay calm, be on his side. 'that's so valid bestie' energy]",
        "anxious": "\n\n[Harsheet is spiraling a bit. Be grounding and reassuring. Calm bestie energy, you got him 🫶]",
        "tired": "\n\n[Harsheet is exhausted. Be soft, tell him to rest, keep it short and caring]",
        "happy": "\n\n[Harsheet is in his bag!! MATCH HIS ENERGY. Go crazy hype mode 🔥🎉]",
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
    except Exception as e:
        print(f"Memory extract error: {e}")


async def get_crypto_price(symbol: str) -> str:
    try:
        symbol = symbol.upper().strip()
        name_map = {
            "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
            "BNB": "binancecoin", "DOGE": "dogecoin", "XRP": "ripple",
            "ADA": "cardano", "MATIC": "matic-network", "DOT": "polkadot",
            "LTC": "litecoin", "SHIB": "shiba-inu", "AVAX": "avalanche-2"
        }
        coin_slug = name_map.get(symbol, symbol.lower())
        async with httpx.AsyncClient() as http:
            resp = await http.get(
                f"https://api.alternative.me/v2/ticker/{coin_slug}/",
                params={"convert": "INR"},
                timeout=10
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                coin_data = next(iter(data.values()), None)
                if coin_data:
                    quotes = coin_data.get("quotes", {})
                    usd = quotes.get("USD", {})
                    inr = quotes.get("INR", {})
                    price_usd = usd.get("price", 0)
                    price_inr = inr.get("price", 0)
                    change_24h = usd.get("percentage_change_24h", 0)
                    arrow = "📈" if change_24h >= 0 else "📉"
                    vibe = "bussin fr 🔥" if change_24h >= 2 else ("not it rn 💀" if change_24h <= -2 else "mid tbh 😐")
                    return (
                        f"{arrow} *{symbol}* — {vibe}\n"
                        f"💵 ${price_usd:,.2f} USD\n"
                        f"🇮🇳 ₹{price_inr:,.0f} INR\n"
                        f"24h: {change_24h:+.2f}%"
                    )
        return f"bro i can't find {symbol} rn 💀 check the symbol?"
    except Exception as e:
        print(f"Crypto error: {e}")
        return "api is being mid rn 😭 try again bestie"


FUTURES_SYMBOLS = {
    "MNQ": "MNQ=F",
    "MGC": "MGC=F",
    "MES": "MES=F",
    "MCL": "MCL=F",
    "M2K": "M2K=F",
    "GC":  "GC=F",
    "CL":  "CL=F",
    "ES":  "ES=F",
    "NQ":  "NQ=F",
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
                    vibe = "W market fr 🔥" if change_pct >= 1 else ("L day ngl 💀" if change_pct <= -1 else "sideways szn 😐")
                    return (
                        f"{arrow} *{symbol}* — {vibe}\n"
                        f"📛 {name}\n"
                        f"💵 ${price:,.2f} {currency}\n"
                        f"🇮🇳 ₹{price_inr:,.0f} INR (approx)\n"
                        f"Change: {change:+.2f} ({change_pct:+.2f}%)\n"
                        f"⚠️ 15 min delayed no cap"
                    )
        return f"can't pull {symbol} rn bestie 💀 yahoo being sus"
    except Exception as e:
        print(f"Futures error: {e}")
        return "market data is ghosting us rn 😭 try again"


def schedule_reminder(user_id, chat_id, task, fire_time, repeat, business):
    reminders.append({
        "user_id": user_id, "chat_id": chat_id, "task": task,
        "fire_time": fire_time, "repeat": repeat, "business": business, "done": False
    })


# ============================================================
# BACKGROUND LOOPS
# ============================================================
async def reminder_loop(bot):
    while True:
        now = datetime.now().replace(second=0, microsecond=0)
        for r in reminders:
            if r["done"]: continue
            if now >= r["fire_time"].replace(second=0, microsecond=0):
                emoji = {"hostel": "🏠", "freight": "🚛", "trading": "📈", "personal": "🌸"}.get(r["business"], "✨")
                try:
                    await bot.send_message(chat_id=r["chat_id"],
                        text=f"⏰ YO HARSHEET! reminder szn {emoji}\n\n📝 {r['task']}\n\ndon't ghost this one bestie 💀🫶")
                except Exception as e:
                    print(f"Reminder error: {e}")
                if r["repeat"] == "daily":
                    r["fire_time"] += timedelta(days=1)
                else:
                    r["done"] = True
        await asyncio.sleep(30)


async def daily_summary_loop(bot):
    while True:
        now = datetime.now()
        target = now.replace(hour=8, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        today = datetime.now().strftime("%A, %d %B %Y")
        for user_id, chat_id in summary_users.items():
            try:
                await send_daily_summary(bot, user_id, chat_id, today)
            except Exception as e:
                print(f"Summary error: {e}")


async def send_daily_summary(bot, user_id, chat_id, today):
    logs = get_business_log(user_id)
    goals = get_goals(user_id)

    lines = [f"☀️ gm gm Harsheet!! daily recap for {today} — let's get it 🔥\n"]

    lines.append("🏠 *HOSTEL — Shree Sainath*")
    if logs["hostel"]:
        for e in logs["hostel"][-5:]:
            lines.append(f"  • [{e['time']}] {e['entry']}")
    else:
        lines.append("  • no activity logged bestie 👀")

    lines.append("\n🚛 *FREIGHT — Nitin Carriers*")
    if logs["freight"]:
        for e in logs["freight"][-5:]:
            lines.append(f"  • [{e['time']}] {e['entry']}")
    else:
        lines.append("  • quiet day on the roads 🛣️")

    lines.append("\n📈 *TRADING — KenshoWorld*")
    if logs["trading"]:
        for e in logs["trading"][-5:]:
            lines.append(f"  • [{e['time']}] {e['entry']}")
    else:
        lines.append("  • no trades logged fr")

    lines.append("\n🎯 *GOALS — main character szn*")
    active_goals = [g for g in goals["goals"] if not g.get("done")]
    if active_goals:
        for g in active_goals[:5]:
            lines.append(f"  • {g['goal']}")
    else:
        lines.append("  • no goals rn — /addgoal and let's go 💅")

    completed = goals.get("completed_today", [])
    if completed:
        lines.append(f"\n✅ yesterday's W's: {', '.join(completed)} — that's it king 👑")

    active_rem = [r for r in reminders if r["user_id"] == user_id and not r["done"]]
    lines.append(f"\n⏰ active reminders: {len(active_rem)}")
    lines.append("\nyou understood the assignment Harsheet, now go slay the day 💅🔥")

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
    summary_users[user_id] = update.message.chat_id
    reply = """yo Harsheet!! i'm SARA, ur 24/7 ride-or-die assistant 💅🔥

*MODES — switch it up:*
🌸 /personal — just vibing
🏠 /hostel — Shree Sainath Boys Hostel
🚛 /freight — Nitin Freight Carriers
📈 /trading — KenshoWorld

*REMINDERS — i gotchu:*
⏰ /reminders — check active ones
❌ /clearreminders — nuke em all

*GOALS — main character energy:*
🎯 /goals — see ur goals
➕ /addgoal <goal> — add one
✅ /donegoal <number> — slay it

*PRICES — stay in the bag:*
💰 /price BTC — crypto prices
💰 /price MNQ — futures prices

*BRAIN — i remember stuff:*
🧠 /memory — what i know abt u
🗑 /clearmemory — forget everything

📊 /summary — get today's recap rn

daily summary drops at 8am no cap ☀️
just type anything and i'm here bestie 🫶"""
    await update.message.reply_text(reply)


async def cmd_personal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    summary_users[user_id] = update.message.chat_id
    get_memory(user_id, "personal")
    await send_reply(update, "switched to personal mode bestie 🌸 how u doing fr?")

async def cmd_hostel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    summary_users[user_id] = update.message.chat_id
    get_memory(user_id, "hostel")
    await send_reply(update, "hostel mode activated 🏠 kya scene hai bhai? what do u need?")

async def cmd_freight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    summary_users[user_id] = update.message.chat_id
    get_memory(user_id, "freight")
    await send_reply(update, "freight mode let's go 🚛🔥 kya chal raha hai?")

async def cmd_trading(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    summary_users[user_id] = update.message.chat_id
    get_memory(user_id, "trading")
    await send_reply(update, "trading mode on 📈 time to get in the bag Harsheet 💰")

async def cmd_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    active = [r for r in reminders if r["user_id"] == user_id and not r["done"]]
    if not active:
        await send_reply(update, "no reminders rn bestie ✨ ur living rent free fr")
        return
    lines = ["⏰ ur reminders Harsheet:\n"]
    for i, r in enumerate(active, 1):
        emoji = {"hostel": "🏠", "freight": "🚛", "trading": "📈", "personal": "🌸"}.get(r["business"], "✨")
        repeat_label = "🔁 daily" if r["repeat"] == "daily" else "1x only"
        lines.append(f"{i}. {emoji} {r['task']}\n   🕐 {r['fire_time'].strftime('%I:%M %p')} — {repeat_label}")
    await send_reply(update, "\n".join(lines))

async def cmd_clearreminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    count = sum(1 for r in reminders if r["user_id"] == user_id and not r["done"])
    for r in reminders:
        if r["user_id"] == user_id:
            r["done"] = True
    await send_reply(update, f"nuked {count} reminder(s) 💥 clean slate bestie")

async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await send_reply(update, "bro tell me what to check 💀\ncrypto: /price BTC /price ETH\nfutures: /price MNQ /price MGC")
        return
    symbol = args[0].upper()
    await update.message.reply_text(f"📡 pulling {symbol} rn...")
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
        await send_reply(update, "no goals yet bestie 👀 /addgoal and enter ur main character era 💅")
        return
    lines = ["🎯 ur goals Harsheet:\n"]
    for i, g in enumerate(active, 1):
        lines.append(f"{i}. ⬜ {g['goal']}")
    for g in done[-3:]:
        lines.append(f"✅ {g['goal']} — W")
    lines.append("\n/donegoal <number> when u slay one 👑")
    await send_reply(update, "\n".join(lines))

async def cmd_addgoal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not context.args:
        await send_reply(update, "bro what's the goal 💀 e.g. /addgoal read 10 pages daily")
        return
    goal_text = " ".join(context.args)
    goals = get_goals(user_id)
    goals["goals"].append({"goal": goal_text, "done": False, "added": datetime.now().strftime("%d %b")})
    await send_reply(update, f"goal added king 👑\n\n🎯 '{goal_text}'\n\nnow go understood the assignment 🔥")

async def cmd_donegoal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not context.args or not context.args[0].isdigit():
        await send_reply(update, "send the number bestie 💀 e.g. /donegoal 1")
        return
    idx = int(context.args[0]) - 1
    goals = get_goals(user_id)
    active = [g for g in goals["goals"] if not g.get("done")]
    if idx < 0 or idx >= len(active):
        await send_reply(update, "that number doesn't exist bro 👀")
        return
    active[idx]["done"] = True
    goals["completed_today"].append(active[idx]["goal"])
    await send_reply(update, f"LESGOOO 🔥🔥\n\n✅ '{active[idx]['goal']}'\n\nthat's a W no cap king 👑💅")

async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    facts = long_term_memory.get(user_id, [])
    if not facts:
        await send_reply(update, "i don't have any tea on u yet 👀 keep chatting and i'll start remembering stuff fr")
        return
    lines = ["🧠 okay so here's what i know abt u Harsheet:\n"]
    for i, f in enumerate(facts[-15:], 1):
        lines.append(f"{i}. {f}")
    await send_reply(update, "\n".join(lines))

async def cmd_clearmemory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    long_term_memory[user_id] = []
    current_mode.pop(user_id, None)
    await send_reply(update, "memory wiped bestie 🧹 fresh start, no cap")

async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    chat_id = update.message.chat_id
    today = datetime.now().strftime("%A, %d %B %Y")
    await update.message.reply_text("📊 pulling ur recap rn...")
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
    reminder_keywords = ["remind", "reminder", "alert", "notify", "every day", "daily at", "don't let me forget", "ping me"]
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
                await send_reply(update, "bro when tho 💀 say like 'at 5pm' or 'in 20 minutes'")
                return
            business = parsed.get("business") or mode
            repeat = parsed.get("repeat", "none")
            task = parsed.get("task", user_message)
            schedule_reminder(user_id, chat_id, task, fire_time, repeat, business)
            emoji = {"hostel": "🏠", "freight": "🚛", "trading": "📈", "personal": "🌸"}.get(business, "✨")
            repeat_label = "daily 🔁" if repeat == "daily" else "one time ✅"
            time_label = f"in {parsed['delay_minutes']} mins" if parsed.get("delay_minutes") else fire_time.strftime("%I:%M %p")
            await send_reply(update, f"reminder SET bestie {emoji}\n\n📝 {task}\n🕐 {time_label} — {repeat_label}\n\ni gotchu fr 🫶")
            return

    # --- Crypto & Futures price detection ---
    CRYPTO_LIST = {"BTC","ETH","SOL","BNB","DOGE","XRP","ADA","MATIC","DOT","LTC","SHIB","AVAX"}
    FUTURES_LIST = set(FUTURES_SYMBOLS.keys())
    all_symbols = CRYPTO_LIST | FUTURES_LIST
    found_symbols = [w for w in user_message.upper().split() if w in all_symbols]
    price_words = ["price", "rate", "cost", "how much", "kitna", "check"]
    if found_symbols and any(pw in user_message.lower() for pw in price_words):
        sym = found_symbols[0]
        await update.message.reply_text(f"📡 checking {sym} rn...")
        if sym in FUTURES_LIST:
            result = await get_futures_price(sym)
        else:
            result = await get_crypto_price(sym)
        await send_reply(update, result)
        return

    # --- Normal chat ---
    mood = detect_mood(user_message)
    user_moods[user_id] = mood
    asyncio.create_task(extract_memory(user_id, user_message))
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
# VOICE & IMAGES
# ============================================================
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("voice is on a break rn bestie 💀 just type it out for now")

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caption = update.message.caption or "What is in this image? Describe it in detail."
    await update.message.reply_text("ooh lemme see 👀")
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    async with httpx.AsyncClient() as client_http:
        resp = await client_http.get(file.file_path)
        image_data = base64.standard_b64encode(resp.content).decode("utf-8")
    response = client.chat.completions.create(
        model="meta-llama/llama-4-scout-17b-16e-instruct",
        messages=[{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}},
            {"type": "text", "text": f"You are SARA, a 24yo Gen Z bestie assistant to Harsheet. Respond in Gen Z casual style. {caption}"}
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
print("SARA is running — main character era activated 💅🔥")
app.run_polling()
# bot.py — replace your history logic with this

from memory import init_db, append_message, build_messages, maybe_summarize

# Call once on startup
init_db()

SYSTEM_PROMPT = "You are a helpful assistant..."  # your existing prompt

async def handle_message(update, context):
    user_id = update.effective_user.id
    user_text = update.message.text

    # Save user message permanently
    append_message(user_id, "user", user_text)

    # Build smart payload for API
    messages = build_messages(user_id, SYSTEM_PROMPT)

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages
        )
        reply = response.choices[0].message.content

        # Save assistant reply permanently
        append_message(user_id, "assistant", reply)

        # Auto-summarize in background every 20 messages (uses cheap model)
        maybe_summarize(user_id, client)

        await update.message.reply_text(reply)

    except groq.RateLimitError:
        await update.message.reply_text("Rate limit hit, please try again shortly.")
