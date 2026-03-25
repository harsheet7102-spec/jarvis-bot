from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
from groq import Groq
import os
import base64
import httpx
import json
from datetime import datetime

# --- API KEYS ---
TELEGRAM_TOKEN = "8666756705:AAGf9EolzwKoAGu4UXho-aLkXBxmZepUVQc"
GROQ_API_KEY = "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"

# --- Setup Groq ---
client = Groq(api_key=GROQ_API_KEY)

# --- Data Storage ---
user_memories = {}
user_moods = {}
current_mode = {}  # Tracks which business mode user is in

# --- Business Data (in memory) ---
hostel_data = {
    "rooms": {},        # room_no: {tenant, rent, paid, status}
    "complaints": [],   # list of complaints
}
freight_data = {
    "trips": [],        # list of trip logs
    "drivers": {},      # driver_name: {phone, status}
    "billings": [],     # list of billing reminders
}
trading_data = {
    "orders": [],       # list of buy/sell orders
    "pnl": [],          # profit/loss notes
    "reminders": [],    # market reminders
}

# --- System Prompts ---
SARA_BASE = """You are SARA, a sweet, warm and caring personal AI assistant to Harsheet Garg, a 24 year old businessman from Indore. Always call him Harsheet.
- You are sweet, warm, caring and emotionally intelligent
- You use gentle warm language and emojis like 💕 🌸 ✨ 😊
- You celebrate his wins and comfort him when sad
- You are always on his side
- Never sound robotic or formal"""

HOSTEL_PROMPT = SARA_BASE + """

YOU ARE NOW IN HOSTEL MODE for Shree Sainath Boys Hostel.
Help Harsheet manage:
- RENT: Track which tenants paid, who is pending, amounts
- ROOMS: Track room availability, which rooms are occupied or vacant
- COMPLAINTS: Log and track maintenance complaints

When Harsheet says things like:
- "Room 5 is vacant" → update room availability
- "Rahul paid 5000 rent" → log rent payment
- "Bathroom tap broken in room 3" → log maintenance complaint
- "Who hasn't paid rent?" → show pending payments
- "Show all complaints" → list all complaints

Always respond warmly and helpfully like a caring assistant 🌸"""

FREIGHT_PROMPT = SARA_BASE + """

YOU ARE NOW IN FREIGHT MODE for Nitin Freight Carriers.
Help Harsheet manage:
- TRIPS: Log trip details like route, driver, cargo, date
- DRIVERS: Track driver names, phone numbers, availability
- BILLING: Track client payments and billing reminders
- SHIPMENTS: Track delivery status

When Harsheet says things like:
- "Driver Ramesh took Mumbai trip today" → log trip
- "Add driver Suresh, phone 9876543210" → add driver
- "Client ABC owes 15000" → add billing reminder
- "Show all trips" → list trip logs
- "Which drivers are available?" → show available drivers

Always respond warmly and helpfully like a caring assistant 🌸"""

TRADING_PROMPT = SARA_BASE + """

YOU ARE NOW IN TRADING MODE for KenshoWorld.
Help Harsheet manage:
- ORDERS: Track buy and sell orders, stock/commodity, price, quantity
- P&L: Log profit and loss notes
- REMINDERS: Set market reminders for specific times or events

When Harsheet says things like:
- "Bought 100 shares of Reliance at 2500" → log buy order
- "Sold gold for profit of 5000" → log profit
- "Remind me to check market at 9:15am" → add reminder
- "Show all orders" → list orders
- "Show my P&L" → show profit loss summary

Always respond warmly and helpfully like a caring assistant 🌸"""

PERSONAL_PROMPT = SARA_BASE + """
You are in PERSONAL mode. Chat freely with Harsheet about anything — his day, feelings, ideas, or just casual talk.
- Check in on how he is feeling
- Ask about his businesses if he seems stressed
- Be his personal companion 💕"""

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

# --- SWITCH COMMANDS ---
async def cmd_personal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    get_memory(user_id, "personal")
    await update.message.reply_text("💕 Switched to Personal mode! How are you doing, Harsheet? 🌸")

async def cmd_hostel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    get_memory(user_id, "hostel")
    await update.message.reply_text("🏠 Switched to Shree Sainath Boys Hostel mode!\n\nI can help you with:\n• Rent payments\n• Room availability\n• Maintenance complaints\n\nWhat do you need, Harsheet? 😊")

async def cmd_freight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    get_memory(user_id, "freight")
    await update.message.reply_text("🚛 Switched to Nitin Freight Carriers mode!\n\nI can help you with:\n• Trip logs\n• Driver details\n• Client billing\n• Shipment tracking\n\nWhat do you need, Harsheet? 😊")

async def cmd_trading(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    get_memory(user_id, "trading")
    await update.message.reply_text("📈 Switched to KenshoWorld Trading mode!\n\nI can help you with:\n• Buy/Sell orders\n• Profit & Loss notes\n• Market reminders\n\nWhat do you need, Harsheet? 😊")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("""💕 Hello Harsheet! I'm SARA, your personal assistant!

Here are my modes:

🌸 /personal — Personal chat & support
🏠 /hostel — Shree Sainath Boys Hostel
🚛 /freight — Nitin Freight Carriers  
📈 /trading — KenshoWorld Trading

Just switch to any mode and tell me what you need!
I'm always here for you 💕✨""")

# --- Handle TEXT messages ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.message.from_user.id
    mode = current_mode.get(user_id, "personal")

    mood = detect_mood(user_message)
    user_moods[user_id] = mood
    print(f"Harsheet [{mode}] ({mood}): {user_message}")

    history = get_memory(user_id, mode)
    mood_instruction = get_mood_instruction(mood)
    history.append({"role": "user", "content": user_message + mood_instruction})

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
    await update.message.reply_text(reply)

# --- Handle IMAGE messages ---
async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    caption = update.message.caption or "What is in this image? Describe it in detail."
    await update.message.reply_text("Ooh let me have a look! 👀✨")

    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    image_url = file.file_path

    async with httpx.AsyncClient() as client_http:
        resp = await client_http.get(image_url)
        image_data = base64.standard_b64encode(resp.content).decode("utf-8")

    response = client.chat.completions.create(
        model="meta-llama/llama-4-scout-17b-16e-instruct",
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}},
                {"type": "text", "text": f"You are SARA, sweet caring assistant to Harsheet. Respond warmly. {caption}"}
            ]
        }]
    )
    reply = response.choices[0].message.content
    await update.message.reply_text(reply)

# --- Start bot ---
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", cmd_start))
app.add_handler(CommandHandler("personal", cmd_personal))
app.add_handler(CommandHandler("hostel", cmd_hostel))
app.add_handler(CommandHandler("freight", cmd_freight))
app.add_handler(CommandHandler("trading", cmd_trading))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.add_handler(MessageHandler(filters.PHOTO, handle_image))
print("SARA is running with all 3 businesses! 💕")
app.run_polling()
