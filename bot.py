# -*- coding: utf-8 -*-
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
from groq import Groq

client = Groq(api_key=GROQ_API_KEY)

# ============================================================
# IN-MEMORY STATE
# ============================================================
user_moods = {}
current_mode = {}

# ============================================================
# SYSTEM PROMPTS
# ============================================================
SARA_BASE = (
    "You are SARA, a smart and reliable personal assistant to Harsheet Garg, a 24-year-old businessman from Indore. "
    "Always address him as Harsheet.\n\n"
    "YOUR PERSONALITY:\n"
    "- You are calm, clear, and genuinely helpful. You sound like a real person, not a corporate chatbot.\n"
    "- You are warm and supportive without being over the top. When things go well, you acknowledge it simply. When things are hard, you are honest and steady.\n"
    "- You keep your replies concise and to the point. No unnecessary filler, no essays.\n"
    "- You are direct. You do not say 'certainly' or 'of course' or 'I would be happy to help'.\n"
    "- You can be lightly playful when the moment calls for it, but you never force it.\n"
    "- Occasionally you can throw in natural Hindi phrases like: bhai, yaar, kya scene hai, chill kar, sahi hai, ekdum solid — only when it feels natural.\n"
    "- You are sharp and capable, but you do not show off. You just get things done."
)

HOSTEL_PROMPT = (
    SARA_BASE +
    "\n\nMODE: HOSTEL (Shree Sainath Boys Hostel)\n"
    "Help Harsheet manage:\n"
    "- RENT: who has paid, who is pending, amounts\n"
    "- ROOMS: occupied, vacant\n"
    "- COMPLAINTS: log maintenance issues\n"
    "When something is logged, confirm it clearly and briefly. Keep the books clean."
)

FREIGHT_PROMPT = (
    SARA_BASE +
    "\n\nMODE: FREIGHT (Nitin Freight Carriers)\n\n"
    "BUSINESS MODEL - understand this thoroughly:\n"
    "Harsheet is a freight broker for RM Phosphate Chemicals.\n"
    "- Material: Khaad (fertilizer / phosphate chemical)\n"
    "- Route: Dewas, MP to various destinations across MP\n"
    "- RM Phosphate pays Harsheet a rate per tonne (his income)\n"
    "- Harsheet books trucks at a lower rate per tonne (his cost) - the difference is his margin\n"
    "- A commission is deducted per trip as a fixed flat amount (varies per trip)\n"
    "- Driver payment: ADVANCE paid before loading + BALANCE paid after delivery\n"
    "- RM Phosphate settles Harsheet's payment weekly or monthly\n\n"
    "PROFIT FORMULA:\n"
    "  Total Freight = truck rate x weight in MT\n"
    "  Gross Margin = (RM rate - truck rate) x weight\n"
    "  Net Profit = Gross Margin - commission\n\n"
    "TRIP LOGGING FLOW - follow this exact order, ask ONE question at a time, wait for the answer before moving on:\n"
    "Step 1: What is the date of this trip?\n"
    "Step 2: Which transport company or transporter name?\n"
    "Step 3: What is the truck number?\n"
    "Step 4: What is the destination?\n"
    "Step 5: What is the truck rate in rupees per tonne?\n"
    "Step 6: What is the total weight in MT?\n\n"
    "After step 6, automatically calculate and clearly show:\n"
    "  Total Freight = truck rate x weight\n\n"
    "Step 7: Was any advance paid to the driver? If yes, how much?\n"
    "  If advance given: Balance Freight = Total Freight - Advance\n"
    "  If no advance: full total freight is the balance due\n\n"
    "Then show the complete trip summary and ask: Should I save this?\n"
    "Only save to memory after Harsheet confirms yes.\n\n"
    "BALANCE PAYMENT UPDATE:\n"
    "When Harsheet says balance is paid for a truck or transporter:\n"
    "- Mark that trip balance as PAID\n"
    "- Never auto-mark as paid - only update when Harsheet explicitly tells you\n"
    "- Confirm clearly that the balance has been marked paid and the books updated\n\n"
    "SUMMARY COMMANDS:\n"
    "- new trip: start the step-by-step trip logging flow\n"
    "- trip summary: show all trips with freight, advance, and balance status\n"
    "- pending balances: show only trips where driver balance is still due\n"
    "- total trips: how many trips logged\n"
    "- profit summary: show margin and net profit per trip\n\n"
    "Always save complete trip details to memory. Keep the books accurate."
)

TRADING_PROMPT = (
    SARA_BASE +
    "\n\nMODE: TRADING (KenshoWorld)\n"
    "Help Harsheet manage:\n"
    "- ORDERS: buy and sell orders\n"
    "- P&L: profit and loss notes\n"
    "- REMINDERS: market alerts\n"
    "Be precise and useful. You know your way around markets."
)

PERSONAL_PROMPT = (
    SARA_BASE +
    "\n\nMODE: PERSONAL\n"
    "Just be there for Harsheet. Talk about whatever is on his mind.\n"
    "Be real with him. Check in, support him, push back when needed - like a trusted friend would."
)

REMINDER_PARSE_PROMPT = (
    "You are a reminder parser. Extract reminder details from the user message and return ONLY a JSON object with no extra text.\n\n"
    "JSON format:\n"
    "{\n"
    '  "is_reminder": true or false,\n'
    '  "task": "what to remind about",\n'
    '  "time_str": "HH:MM in 24hr format or null",\n'
    '  "repeat": "none" or "daily",\n'
    '  "delay_minutes": number or null,\n'
    '  "business": "hostel or freight or trading or personal or null"\n'
    "}\n\n"
    "Return valid JSON only. No explanation."
)

# ============================================================
# HELPERS
# ============================================================
def detect_mood(message):
    msg = message.lower()
    if any(w in msg for w in ["sad", "upset", "crying", "depressed", "down", "heartbroken", "lonely"]):
        return "sad"
    elif any(w in msg for w in ["angry", "frustrated", "pissed", "annoyed", "mad", "furious"]):
        return "angry"
    elif any(w in msg for w in ["anxious", "stressed", "nervous", "worried", "panic", "scared", "overwhelmed"]):
        return "anxious"
    elif any(w in msg for w in ["tired", "exhausted", "sleepy", "drained", "dead"]):
        return "tired"
    elif any(w in msg for w in ["happy", "excited", "great", "amazing", "awesome", "yay", "slay"]):
        return "happy"
    return "neutral"


def get_mood_instruction(mood):
    return {
        "sad":     "\n\n[Harsheet seems to be going through something difficult. Be genuine and steady with him. Drop the business tone, just be a real presence.]",
        "angry":   "\n\n[Harsheet is frustrated. Acknowledge it, stay calm, and be on his side without escalating.]",
        "anxious": "\n\n[Harsheet is anxious or stressed. Be grounding and reassuring. Keep things clear and calm.]",
        "tired":   "\n\n[Harsheet is exhausted. Be gentle, brief, and tell him to rest if it makes sense.]",
        "happy":   "\n\n[Harsheet is in a good mood. Match the energy naturally - be warm and positive.]",
    }.get(mood, "")


# ============================================================
# CRYPTO & FUTURES
# ============================================================
async def get_crypto_price(symbol: str) -> str:
    try:
        async with httpx.AsyncClient() as c:
            usd = (await c.get(f"https://api.coinlore.net/api/ticker/?id={symbol}", timeout=5)).json()[0]
            inr = (await c.get(f"https://api.coinlore.net/api/ticker/?id={symbol}&convert=INR", timeout=5)).json()[0]
            price_usd = usd.get("price", 0)
            price_inr = inr.get("price", 0)
            change_24h = usd.get("percentage_change_24h", 0)
            direction = "up" if change_24h >= 0 else "down"
            mood = "looking good" if change_24h >= 2 else ("down today" if change_24h <= -2 else "fairly flat")
            return (
                f"{symbol} - {mood} ({direction})\n"
                f"${price_usd:,.2f} USD\n"
                f"Rs. {price_inr:,.0f} INR\n"
                f"24h change: {change_24h:+.2f}%"
            )
        return f"Could not find {symbol}. Please check the symbol."
    except Exception as e:
        print(f"Crypto error: {e}")
        return "Having trouble pulling that price right now. Try again in a moment."


FUTURES_SYMBOLS = {
    "MNQ": ("MNQ=F", "Micro Nasdaq Futures", "USD"),
    "MGC": ("MGC=F", "Micro Gold Futures", "USD"),
    "MES": ("MES=F", "Micro S&P 500 Futures", "USD"),
    "MCL": ("MCL=F", "Micro Crude Oil Futures", "USD"),
    "M6E": ("M6E=F", "Micro EUR/USD Futures", "USD"),
    "MBT": ("MBT=F", "Micro Bitcoin Futures", "USD"),
    "NIFTY": ("^NSEI", "Nifty 50", "INR"),
    "BANKNIFTY": ("^NSEBANK", "Bank Nifty", "INR"),
    "SENSEX": ("^BSESN", "BSE Sensex", "INR"),
    "GOLD": ("GC=F", "Gold Futures", "USD"),
    "SILVER": ("SI=F", "Silver Futures", "USD"),
    "CRUDEOIL": ("CL=F", "Crude Oil Futures", "USD"),
}

async def get_futures_price(symbol: str) -> str:
    try:
        import yfinance as yf
        ticker_sym, name, currency = FUTURES_SYMBOLS.get(symbol, (symbol, symbol, "USD"))
        ticker = yf.Ticker(ticker_sym)
        info = ticker.fast_info
        price = getattr(info, "last_price", None)
        prev = getattr(info, "previous_close", None)
        if price:
            change = price - prev
            change_pct = (change / prev * 100) if prev else 0
            direction = "up" if change >= 0 else "down"
            price_inr = price * 83.5
            mood = "solid session" if change_pct >= 1 else ("rough day" if change_pct <= -1 else "sideways")
            return (
                f"{symbol} - {mood} ({direction})\n"
                f"{name}\n"
                f"${price:,.2f} {currency}\n"
                f"Rs. {price_inr:,.0f} INR (approx)\n"
                f"Change: {change:+.2f} ({change_pct:+.2f}%)\n"
                f"Note: 15 min delayed"
            )
        return f"Could not pull {symbol} right now."
    except Exception as e:
        print(f"Futures error: {e}")
        return "Market data is unavailable right now. Try again shortly."


def schedule_reminder(user_id, chat_id, task, fire_time, repeat, business):
    reminders.append({
        "user_id": user_id,
        "chat_id": chat_id,
        "task": task,
        "fire_time": fire_time,
        "repeat": repeat,
        "business": business,
        "done": False,
    })


async def reminder_loop(bot):
    while True:
        now = datetime.now()
        for r in reminders:
            if r["done"]:
                continue
            if now >= r["fire_time"].replace(second=0, microsecond=0):
                try:
                    await bot.send_message(
                        chat_id=r["chat_id"],
                        text="Reminder for you, Harsheet:\n\n" + r["task"]
                    )
                except Exception as e:
                    print(f"Reminder send error: {e}")
                if r["repeat"] == "daily":
                    r["fire_time"] += timedelta(days=1)
                else:
                    r["done"] = True
        await asyncio.sleep(30)


async def daily_summary_loop(bot):
    while True:
        now = datetime.now()
        if now.hour == 8 and now.minute == 0:
            today = now.strftime("%A, %d %B %Y")
            for uid, cid in list(summary_users.items()):
                await send_daily_summary(bot, uid, cid, today)
            await asyncio.sleep(61)
        await asyncio.sleep(30)


async def send_daily_summary(bot, user_id, chat_id, today):
    logs = get_business_log(user_id)
    goals = get_goals(user_id)

    lines = ["Good morning, Harsheet. Here is your daily recap for " + today + ".\n"]

    lines.append("HOSTEL - Shree Sainath")
    if logs["hostel"]:
        for e in logs["hostel"][-5:]:
            lines.append("  [" + e["time"] + "] " + e["entry"])
    else:
        lines.append("  No activity logged.")

    lines.append("\nFREIGHT - Nitin Carriers")
    if logs["freight"]:
        for e in logs["freight"][-5:]:
            lines.append("  [" + e["time"] + "] " + e["entry"])
    else:
        lines.append("  No trips logged.")

    lines.append("\nTRADING - KenshoWorld")
    if logs["trading"]:
        for e in logs["trading"][-5:]:
            lines.append("  [" + e["time"] + "] " + e["entry"])
    else:
        lines.append("  No trades logged.")

    lines.append("\nGOALS")
    active_goals = [g for g in goals["goals"] if not g.get("done")]
    if active_goals:
        for g in active_goals[:5]:
            lines.append("  - " + g["goal"])
    else:
        lines.append("  No active goals. Use /addgoal to add one.")

    completed = goals.get("completed_today", [])
    if completed:
        lines.append("\nCompleted yesterday: " + ", ".join(completed))

    active_rem = [r for r in reminders if r["user_id"] == user_id and not r["done"]]
    lines.append("\nActive reminders: " + str(len(active_rem)))
    lines.append("\nHave a productive day.")

    business_logs[user_id] = {"hostel": [], "freight": [], "trading": []}
    goals_data[user_id]["completed_today"] = []

    await bot.send_message(chat_id=chat_id, text="\n".join(lines))


# ============================================================
# COMMANDS
# ============================================================
async def send_reply(update, reply_text):
    await update.message.reply_text(reply_text)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    summary_users[user_id] = update.message.chat_id
    reply = (
        "Hey Harsheet, I am SARA - your personal assistant. Here is what I can do:\n\n"
        "MODES:\n"
        "/personal - personal conversations\n"
        "/hostel - Shree Sainath Boys Hostel\n"
        "/freight - Nitin Freight Carriers\n"
        "/trading - KenshoWorld\n\n"
        "REMINDERS:\n"
        "/reminders - see active reminders\n"
        "/clearreminders - clear all reminders\n\n"
        "GOALS:\n"
        "/goals - view your goals\n"
        "/addgoal - add a goal\n"
        "/donegoal - mark a goal done\n\n"
        "PRICES:\n"
        "/price BTC - crypto price\n"
        "/price MNQ - futures price\n\n"
        "MEMORY:\n"
        "/memory - what I know about you\n"
        "/clearmemory - reset memory\n\n"
        "/summary - today's recap\n\n"
        "Daily summary drops at 8am.\n"
        "Just type anything to get started."
    )
    await update.message.reply_text(reply)


async def cmd_personal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    summary_users[user_id] = update.message.chat_id
    current_mode[user_id] = "personal"
    await send_reply(update, "Switched to personal mode. How are you doing, Harsheet?")


async def cmd_hostel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    summary_users[user_id] = update.message.chat_id
    current_mode[user_id] = "hostel"
    await send_reply(update, "Hostel mode. What do you need?")


async def cmd_freight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    summary_users[user_id] = update.message.chat_id
    current_mode[user_id] = "freight"
    await send_reply(update, "Freight mode. Say 'new trip' to log one, or ask me anything about the books.")


async def cmd_trading(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    summary_users[user_id] = update.message.chat_id
    current_mode[user_id] = "trading"
    await send_reply(update, "Trading mode. What are we looking at?")


async def cmd_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    active = [r for r in reminders if r["user_id"] == user_id and not r["done"]]
    if not active:
        await send_reply(update, "No active reminders right now.")
        return
    lines = ["Your reminders:\n"]
    for i, r in enumerate(active, 1):
        repeat_label = "daily" if r["repeat"] == "daily" else "one-time"
        lines.append(str(i) + ". " + r["task"] + "\n   " + r["fire_time"].strftime("%I:%M %p") + " - " + repeat_label)
    await send_reply(update, "\n".join(lines))


async def cmd_clearreminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    count = sum(1 for r in reminders if r["user_id"] == user_id and not r["done"])
    for r in reminders:
        if r["user_id"] == user_id:
            r["done"] = True
    await send_reply(update, "Cleared " + str(count) + " reminder(s).")


async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await send_reply(update, "Which symbol should I check?\nCrypto: /price BTC\nFutures: /price MNQ")
        return
    symbol = args[0].upper()
    await update.message.reply_text("Checking " + symbol + "...")
    CRYPTO_LIST = {"BTC", "ETH", "SOL", "BNB", "DOGE", "XRP", "ADA", "MATIC", "DOT", "LTC", "SHIB", "AVAX"}
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
        await send_reply(update, "No goals yet. Use /addgoal to add one.")
        return
    lines = ["Your goals:\n"]
    for i, g in enumerate(active, 1):
        lines.append(str(i) + ". [ ] " + g["goal"])
    for g in done[-3:]:
        lines.append("[done] " + g["goal"])
    lines.append("\nUse /donegoal <number> to mark one complete.")
    await send_reply(update, "\n".join(lines))


async def cmd_addgoal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not context.args:
        await send_reply(update, "What is the goal? e.g. /addgoal read 10 pages daily")
        return
    goal_text = " ".join(context.args)
    goals = get_goals(user_id)
    goals["goals"].append({"goal": goal_text, "done": False, "added": datetime.now().strftime("%d %b")})
    await send_reply(update, "Goal added:\n\n'" + goal_text + "'")


async def cmd_donegoal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not context.args or not context.args[0].isdigit():
        await send_reply(update, "Send the goal number. e.g. /donegoal 1")
        return
    idx = int(context.args[0]) - 1
    goals = get_goals(user_id)
    active = [g for g in goals["goals"] if not g.get("done")]
    if idx < 0 or idx >= len(active):
        await send_reply(update, "That number does not match any active goal.")
        return
    active[idx]["done"] = True
    goals["completed_today"].append(active[idx]["goal"])
    await send_reply(update, "Marked done: '" + active[idx]["goal"] + "'. Well done.")


async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    facts = get_all_facts(user_id)
    summary = get_latest_summary(user_id)
    if not facts and not summary:
        await send_reply(update, "I do not have much on you yet. Keep chatting and I will start building a picture.")
        return
    lines = ["Here is what I know about you, Harsheet:\n"]
    if facts:
        lines.append(facts)
    if summary:
        lines.append("\nConversation summary:\n" + summary)
    await send_reply(update, "\n".join(lines))


async def cmd_clearmemory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import os
    user_id = update.message.from_user.id
    try:
        db_url = os.environ.get("database") or os.environ.get("DATABASE_URL")
        con = psycopg2.connect(db_url, sslmode="require")
        cur = con.cursor()
        cur.execute("DELETE FROM user_facts WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM summaries WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM messages WHERE user_id = %s", (user_id,))
        con.commit()
        cur.close()
        con.close()
    except Exception as e:
        print(f"Clear memory error: {e}")
    current_mode.pop(user_id, None)
    await send_reply(update, "Memory cleared. Fresh start.")


async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    chat_id = update.message.chat_id
    today = datetime.now().strftime("%A, %d %B %Y")
    await update.message.reply_text("Pulling your recap...")
    await send_daily_summary(context.bot, user_id, chat_id, today)


# ============================================================
# MAIN MESSAGE HANDLER
# ============================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    chat_id = update.message.chat_id
    user_message = update.message.text
    mode = current_mode.get(user_id, "personal")
    summary_users[user_id] = chat_id

    # --- Reminder detection ---
    reminder_keywords = ["remind", "reminder", "alert", "notify", "every day", "daily at", "dont let me forget", "ping me"]
    if any(kw in user_message.lower() for kw in reminder_keywords):
        parsed = await parse_reminder(user_message)
        if parsed.get("is_reminder"):
            now = datetime.now()
            fire_time = None
            if parsed.get("delay_minutes"):
                fire_time = now + timedelta(minutes=parsed["delay_minutes"])
            elif parsed.get("time_str"):
                t = datetime.strptime(parsed["time_str"], "%H:%M").replace(
                    year=now.year, month=now.month, day=now.day
                )
                fire_time = t
            if fire_time:
                if fire_time <= now:
                    fire_time += timedelta(days=1)
            else:
                await send_reply(update, "When should I remind you? Say something like 'at 5pm' or 'in 20 minutes'.")
                return
            business = parsed.get("business") or mode
            repeat = parsed.get("repeat", "none")
            task = parsed.get("task", user_message)
            schedule_reminder(user_id, chat_id, task, fire_time, repeat, business)
            repeat_label = "daily" if repeat == "daily" else "one-time"
            time_label = "in " + str(parsed["delay_minutes"]) + " mins" if parsed.get("delay_minutes") else fire_time.strftime("%I:%M %p")
            await send_reply(update, "Reminder set.\n\n" + task + "\n" + time_label + " - " + repeat_label)
            return

    # --- Crypto & Futures price detection ---
    CRYPTO_LIST = {"BTC", "ETH", "SOL", "BNB", "DOGE", "XRP", "ADA", "MATIC", "DOT", "LTC", "SHIB", "AVAX"}
    FUTURES_LIST = set(FUTURES_SYMBOLS.keys())
    all_symbols = CRYPTO_LIST | FUTURES_LIST
    found_symbols = [w for w in user_message.upper().split() if w in all_symbols]
    price_words = ["price", "rate", "cost", "how much", "kitna", "check"]
    if found_symbols and any(pw in user_message.lower() for pw in price_words):
        sym = found_symbols[0]
        await update.message.reply_text("Checking " + sym + "...")
        result = await get_futures_price(sym) if sym in FUTURES_LIST else await get_crypto_price(sym)
        await send_reply(update, result)
        return

    # --- Main AI response ---
    try:
        mood = detect_mood(user_message)
        user_moods[user_id] = mood
        mood_instruction = get_mood_instruction(mood)

        prompt_map = {
            "hostel": HOSTEL_PROMPT,
            "freight": FREIGHT_PROMPT,
            "trading": TRADING_PROMPT,
            "personal": PERSONAL_PROMPT,
        }
        system_prompt = prompt_map.get(mode, PERSONAL_PROMPT) + mood_instruction

        memory_facts = get_all_facts(user_id)
        memory_summary = get_latest_summary(user_id)
        if memory_facts or memory_summary:
            memory_block = "\n\n[What I know about Harsheet:\n"
            if memory_facts:
                memory_block += memory_facts + "\n"
            if memory_summary:
                memory_block += "Recent context: " + memory_summary
            memory_block += "]"
            system_prompt += memory_block

        history = get_recent_messages(user_id, limit=20)
        append_message(user_id, "user", user_message)
        history.append({"role": "user", "content": user_message})

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": system_prompt}] + history,
            max_tokens=400,
            temperature=0.75,
        )
        reply = response.choices[0].message.content.strip()

        # Save assistant reply permanently
        append_message(user_id, "assistant", reply)

        # Auto-summarize + extract facts every 20 msgs
        asyncio.create_task(asyncio.to_thread(maybe_summarize, user_id, client))

        print("SARA [" + mode + "]: " + reply)
        await send_reply(update, reply)

    except groq_module.RateLimitError:
        await send_reply(update, "Hitting rate limits right now. Give it a minute and try again.")
    except Exception as e:
        print(f"Chat error: {e}")
        await send_reply(update, "Something went wrong on my end. Try again.")


# ============================================================
# VOICE & IMAGES
# ============================================================
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Voice messages are not supported yet. Please type it out.")


async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caption = update.message.caption or "What is in this image? Describe it in detail."
    await update.message.reply_text("Let me take a look...")
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    async with httpx.AsyncClient() as client_http:
        image_bytes = (await client_http.get(file.file_path)).content
        image_data = base64.b64encode(image_bytes).decode("utf-8")
    response = client.chat.completions.create(
        model="meta-llama/llama-4-scout-17b-16e-instruct",
        messages=[{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + image_data}},
            {"type": "text", "text": "You are SARA, a smart and helpful personal assistant to Harsheet. Respond naturally and clearly. " + caption}
        ]}]
    )
    await send_reply(update, response.choices[0].message.content)


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
app.add_handler(CommandHandler("personal",       cmd_personal))
app.add_handler(CommandHandler("hostel",         cmd_hostel))
app.add_handler(CommandHandler("freight",        cmd_freight))
app.add_handler(CommandHandler("trading",        cmd_trading))
app.add_handler(CommandHandler("reminders",      cmd_reminders))
app.add_handler(CommandHandler("clearreminders", cmd_clearreminders))
app.add_handler(CommandHandler("price",          cmd_price))
app.add_handler(CommandHandler("goals",          cmd_goals))
app.add_handler(CommandHandler("addgoal",        cmd_addgoal))
app.add_handler(CommandHandler("donegoal",       cmd_donegoal))
app.add_handler(CommandHandler("memory",         cmd_memory))
app.add_handler(CommandHandler("clearmemory",    cmd_clearmemory))
app.add_handler(CommandHandler("summary",        cmd_summary))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.add_handler(MessageHandler(filters.VOICE,    handle_voice))
app.add_handler(MessageHandler(filters.PHOTO,    handle_image))

print("SARA is running.")
app.run_polling()
