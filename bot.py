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

# --- Data Storage ---
user_memories = {}
user_moods = {}
current_mode = {}
reminders = []  # list of reminder dicts

# --- System Prompts ---
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
- COMPLAINTS: Log and track maintenance complaints"""

FREIGHT_PROMPT = SARA_BASE + """

YOU ARE NOW IN FREIGHT MODE for Nitin Freight Carriers.
Help Harsheet manage:
- TRIPS: Log trip details like route, driver, cargo, date
- DRIVERS: Track driver names, phone numbers, availability
- BILLING: Track client payments and billing reminders
- SHIPMENTS: Track delivery status"""

TRADING_PROMPT = SARA_BASE + """

YOU ARE NOW IN TRADING MODE for KenshoWorld.
Help Harsheet manage:
- ORDERS: Track buy and sell orders
- P&L: Log profit and loss notes
- REMINDERS: Set market reminders"""

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
- "remind me daily at 8pm to update trip log" → {"is_reminder":true,"task":"update trip log","time_str":"20:00","repeat":"daily","delay_minutes":null,"business":"freight"}
- "what is the weather today" → {"is_reminder":false,"task":null,"time_str":null,"repeat":"none","delay_minutes":null,"business":null}

Return valid JSON only. No explanation."""


def get_memory(user_id, mode="personal"):
    if user_id not in user_memories or current_mode.get(user_id) != mode:
        prompts = {
            "personal": PERSONAL_PROMPT,
            "hostel": HOSTEL_PROMPT,
            "freight": FREIGHT_PROMPT,
            "trading": TRADING_PROMPT,
        }
        user_memories[user_id] = [
            {"role": "system", "content": prompts.get(mode, PERSONAL_PROMPT)}
        ]
        current_mode[user_id] = mode
    return user_memories[user_id]


def detect_mood(message):
    message = message.lower()
    if any(w in message for w in ["sad", "crying", "upset", "unhappy", "heartbroken", "lonely", "hurt"]):
        return "sad"
    elif any(w in message for w in ["angry", "frustrated", "mad", "annoyed", "hate"]):
        return "angry"
    elif any(w in message for w in ["stressed", "anxious", "worried", "nervous", "overwhelmed"]):
        return "anxious"
    elif any(w in message for w in ["tired", "exhausted", "sleepy", "drained"]):
        return "tired"
    elif any(w in message for w in ["happy", "excited", "great", "amazing", "awesome", "yay"]):
        return "happy"
    return "neutral"


def get_mood_instruction(mood):
    if mood == "sad":
        return "\n\n[Harsheet seems sad. Be extra gentle and comforting 💕]"
    elif mood == "angry":
        return "\n\n[Harsheet seems angry. Stay calm, validate his feelings 🤗]"
    elif mood == "anxious":
        return "\n\n[Harsheet seems anxious. Be very reassuring and calming 🌸]"
    elif mood == "tired":
        return "\n\n[Harsheet seems tired. Be soft and suggest rest 💤]"
    elif mood == "happy":
        return "\n\n[Harsheet is happy! Match his energy and celebrate 🎉]"
    return ""


# --- Parse reminder using Groq ---
async def parse_reminder(user_message):
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": REMINDER_PARSE_PROMPT},
                {"role": "user", "content": user_message}
            ]
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r"```json|```", "", raw).strip()
        return json.loads(raw)
    except Exception as e:
        print(f"Reminder parse error: {e}")
        return {"is_reminder": False}


# --- Schedule a reminder ---
def schedule_reminder(user_id, chat_id, task, fire_time, repeat, business):
    reminders.append({
        "user_id": user_id,
        "chat_id": chat_id,
        "task": task,
        "fire_time": fire_time,
        "repeat": repeat,
        "business": business,
        "done": False
    })
    print(f"Reminder scheduled: {task} at {fire_time} repeat={repeat}")


# --- Background reminder checker (runs every 30s) ---
async def reminder_loop(bot):
    while True:
        now = datetime.now().replace(second=0, microsecond=0)
        for r in reminders:
            if r["done"]:
                continue
            if now >= r["fire_time"].replace(second=0, microsecond=0):
                business_emoji = {"hostel": "🏠", "freight": "🚛", "trading": "📈", "personal": "🌸"}.get(r["business"], "🌸")
                msg = (
                    f"⏰ Hey Harsheet! Reminder time! {business_emoji}\n\n"
                    f"📝 {r['task']}\n\n"
                    f"💕 You've got this! ✨"
                )
                try:
                    await bot.send_message(chat_id=r["chat_id"], text=msg)
                except Exception as e:
                    print(f"Failed to send reminder: {e}")

                if r["repeat"] == "daily":
                    r["fire_time"] = r["fire_time"] + timedelta(days=1)
                    print(f"Rescheduled: {r['task']} → {r['fire_time']}")
                else:
                    r["done"] = True
        await asyncio.sleep(30)


# --- Send text reply ---
async def send_reply(update, reply_text):
    await update.message.reply_text(reply_text)


# --- COMMANDS ---
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply = """💕 Hello Harsheet! I'm SARA, your personal assistant!

Modes:
🌸 /personal — Personal chat & support
🏠 /hostel — Shree Sainath Boys Hostel
🚛 /freight — Nitin Freight Carriers
📈 /trading — KenshoWorld Trading

Reminders:
⏰ /reminders — See all active reminders
❌ /clearreminders — Clear all reminders

Just type naturally to set reminders:
• "Remind me at 5pm to call driver"
• "Remind me every day at 9am to check rent"
• "Remind me in 30 minutes to check orders"

💕 I'm here for you always!"""
    await send_reply(update, reply)


async def cmd_personal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_memory(update.message.from_user.id, "personal")
    await send_reply(update, "💕 Switched to Personal mode! How are you doing, Harsheet? 🌸")


async def cmd_hostel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_memory(update.message.from_user.id, "hostel")
    await send_reply(update, "🏠 Switched to Hostel mode! What do you need help with, Harsheet? 😊")


async def cmd_freight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_memory(update.message.from_user.id, "freight")
    await send_reply(update, "🚛 Switched to Freight mode! What do you need help with, Harsheet? 😊")


async def cmd_trading(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_memory(update.message.from_user.id, "trading")
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
    await send_reply(update, f"🗑️ Cleared {count} reminder(s)! All clean now, Harsheet 💕")


# --- Handle TEXT messages ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.message.from_user.id
    chat_id = update.message.chat_id
    mode = current_mode.get(user_id, "personal")

    # Check if it's a reminder
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
                await send_reply(update, "🌸 I couldn't figure out the time for that reminder. Could you mention a time like 'at 5pm' or 'in 20 minutes'? 💕")
                return

            business = parsed.get("business") or mode
            repeat = parsed.get("repeat", "none")
            task = parsed.get("task", user_message)
            schedule_reminder(user_id, chat_id, task, fire_time, repeat, business)

            emoji = {"hostel": "🏠", "freight": "🚛", "trading": "📈", "personal": "🌸"}.get(business, "🌸")
            repeat_label = "every day 🔁" if repeat == "daily" else "once ✅"
            time_label = f"in {parsed['delay_minutes']} minutes" if parsed.get("delay_minutes") else fire_time.strftime("%I:%M %p")

            await send_reply(update, (
                f"⏰ Reminder set {repeat_label}\n\n"
                f"{emoji} Task: {task}\n"
                f"🕐 Time: {time_label}\n\n"
                f"I'll remind you, Harsheet! 💕✨"
            ))
            return

    # Normal chat
    mood = detect_mood(user_message)
    user_moods[user_id] = mood
    print(f"Harsheet [{mode}] ({mood}): {user_message}")

    history = get_memory(user_id, mode)
    history.append({"role": "user", "content": user_message + get_mood_instruction(mood)})

    if len(history) > 21:
        history = [history[0]] + history[-20:]
        user_memories[user_id] = history

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=history
    )
    reply = response.choices[0].message.content
    history.append({"role": "assistant", "content": reply})
    print(f"SARA: {reply}")
    await send_reply(update, reply)


# --- Handle VOICE messages ---
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🌸 Voice messages are temporarily unavailable, Harsheet. Please type instead 💕")


# --- Handle IMAGE messages ---
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


# --- Start bot ---
async def post_init(application):
    asyncio.create_task(reminder_loop(application.bot))

app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()
app.add_handler(CommandHandler("start", cmd_start))
app.add_handler(CommandHandler("personal", cmd_personal))
app.add_handler(CommandHandler("hostel", cmd_hostel))
app.add_handler(CommandHandler("freight", cmd_freight))
app.add_handler(CommandHandler("trading", cmd_trading))
app.add_handler(CommandHandler("reminders", cmd_reminders))
app.add_handler(CommandHandler("clearreminders", cmd_clearreminders))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.add_handler(MessageHandler(filters.VOICE, handle_voice))
app.add_handler(MessageHandler(filters.PHOTO, handle_image))
print("SARA is running with Reminders! 💕⏰")
app.run_polling()
