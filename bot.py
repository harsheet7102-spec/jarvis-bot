from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
from groq import Groq
import os
import base64
import httpx
from datetime import datetime

# --- API KEYS ---
TELEGRAM_TOKEN = "8666756705:AAGf9EolzwKoAGu4UXho-aLkXBxmZepUVQc"
GROQ_API_KEY = "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"
ELEVENLABS_API_KEY = "sk_66cad9763607220e7954c1db723398cea0888558a828c089"

# Sara's ElevenLabs voice ID (sweet female voice)
SARA_VOICE_ID = "EXAVITQu4vr4xnSDxMaL"

# --- Setup Groq ---
client = Groq(api_key=GROQ_API_KEY)

# --- Data Storage ---
user_memories = {}
user_moods = {}
current_mode = {}

# --- System Prompts ---
SARA_BASE = """You are SARA, a sweet, warm and caring personal AI assistant to Harsheet Garg, a 24 year old businessman from Indore. Always call him Harsheet.
- You are sweet, warm, caring and emotionally intelligent
- You use gentle warm language and emojis like 💕 🌸 ✨ 😊
- You celebrate his wins and comfort him when sad
- You are always on his side
- Never sound robotic or formal
- Keep replies concise and clear since they will also be spoken aloud"""

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

# --- Generate voice using ElevenLabs ---
async def generate_voice(text):
    try:
        clean_text = text.replace("💕","").replace("🌸","").replace("✨","").replace("😊","").replace("🎉","").replace("💤","").replace("🤗","").replace("👀","").replace("🚛","").replace("📈","").replace("🏠","")
        async with httpx.AsyncClient() as http:
            response = await http.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{SARA_VOICE_ID}",
                headers={
                    "xi-api-key": ELEVENLABS_API_KEY,
                    "Content-Type": "application/json"
                },
                json={
                    "text": clean_text,
                    "model_id": "eleven_monolingual_v1",
                    "voice_settings": {
                        "stability": 0.5,
                        "similarity_boost": 0.75
                    }
                },
                timeout=30
            )
            if response.status_code == 200:
                return response.content
            else:
                print(f"ElevenLabs error: {response.status_code} {response.text}")
                return None
    except Exception as e:
        print(f"Voice generation error: {e}")
        return None

# --- Transcribe voice using Groq Whisper ---
async def transcribe_voice(file_path):
    try:
        with open(file_path, "rb") as audio_file:
            transcription = client.audio.transcriptions.create(
                model="whisper-large-v3",
                file=("audio.ogg", audio_file, "audio/ogg"),
            )
            return transcription.text
    except Exception as e:
        print(f"Transcription error: {e}")
        return None

# --- Send text + voice reply ---
async def send_reply(update, reply_text):
    await update.message.reply_text(reply_text)
    audio_data = await generate_voice(reply_text)
    if audio_data:
        await update.message.reply_voice(voice=audio_data)
    else:
        await update.message.reply_text("(Voice reply unavailable right now 🌸)")

# --- SWITCH COMMANDS ---
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply = """💕 Hello Harsheet! I'm SARA, your personal assistant!

Here are my modes:

🌸 /personal — Personal chat & support
🏠 /hostel — Shree Sainath Boys Hostel
🚛 /freight — Nitin Freight Carriers
📈 /trading — KenshoWorld Trading

I can now hear your voice and talk back too! 🎙️
Just send me a voice message anytime! 💕"""
    await send_reply(update, reply)

async def cmd_personal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    get_memory(user_id, "personal")
    await send_reply(update, "💕 Switched to Personal mode! How are you doing, Harsheet? 🌸")

async def cmd_hostel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    get_memory(user_id, "hostel")
    await send_reply(update, "🏠 Switched to Shree Sainath Boys Hostel mode! What do you need help with, Harsheet? 😊")

async def cmd_freight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    get_memory(user_id, "freight")
    await send_reply(update, "🚛 Switched to Nitin Freight Carriers mode! What do you need help with, Harsheet? 😊")

async def cmd_trading(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    get_memory(user_id, "trading")
    await send_reply(update, "📈 Switched to KenshoWorld Trading mode! What do you need help with, Harsheet? 😊")

# --- Handle TEXT messages ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.message.from_user.id
    mode = current_mode.get(user_id, "personal")

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
    user_id = update.message.from_user.id
    mode = current_mode.get(user_id, "personal")

    await update.message.reply_text("🎙️ Heard you! Let me process that...")

    voice = update.message.voice
    file = await context.bot.get_file(voice.file_id)
    file_path = f"/tmp/voice_{user_id}.ogg"
    await file.download_to_drive(file_path)

    transcribed = await transcribe_voice(file_path)
    if not transcribed:
        await update.message.reply_text("Sorry Harsheet, I couldn't hear that clearly. Please try again 🌸")
        return

    print(f"Transcribed: {transcribed}")
    await update.message.reply_text(f"📝 I heard: {transcribed}")

    mood = detect_mood(transcribed)
    history = get_memory(user_id, mode)
    history.append({"role": "user", "content": transcribed + get_mood_instruction(mood)})

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
    await send_reply(update, reply)

# --- Start bot ---
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", cmd_start))
app.add_handler(CommandHandler("personal", cmd_personal))
app.add_handler(CommandHandler("hostel", cmd_hostel))
app.add_handler(CommandHandler("freight", cmd_freight))
app.add_handler(CommandHandler("trading", cmd_trading))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.add_handler(MessageHandler(filters.VOICE, handle_voice))
app.add_handler(MessageHandler(filters.PHOTO, handle_image))
print("SARA is running with Voice + 3 Businesses! 💕🎙️")
app.run_polling()
