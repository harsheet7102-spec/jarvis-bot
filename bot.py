from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
from groq import Groq
import httpx
import base64

# --- API KEYS ---
TELEGRAM_TOKEN = "8666756705:AAGf9EolzwKoAGu4UXho-aLkXBxmZepUVQc"
GROQ_API_KEY = "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"
ELEVENLABS_API_KEY = "sk_66cad9763607220e7954c1db723398cea0888558a828c089"
ELEVENLABS_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"

# --- Setup Groq ---
client = Groq(api_key=GROQ_API_KEY)

# --- Storage ---
user_memories = {}
user_moods = {}
current_mode = {}

# --- Prompts ---
SARA_BASE = """You are SARA, a sweet, warm and caring personal AI assistant to Harsheet Garg, a 24 year old businessman from Indore. Always call him Harsheet.
- You are sweet, warm, caring and emotionally intelligent
- You use gentle warm language and emojis like 💕 🌸 ✨ 😊
- You celebrate his wins and comfort him when sad
- You are always on his side
- Never sound robotic or formal
- Keep replies concise since they will also be spoken aloud"""

PROMPTS = {
    "personal": SARA_BASE + """
You are in PERSONAL mode. Chat freely with Harsheet about anything.
- Check in on how he is feeling
- Be his personal companion 💕
- Ask about his day, his businesses, his mood""",

    "hostel": SARA_BASE + """
YOU ARE IN HOSTEL MODE for Shree Sainath Boys Hostel.
Help Harsheet manage:
- RENT: Track which tenants paid, who is pending, amounts
- ROOMS: Track room availability, occupied or vacant
- COMPLAINTS: Log and track maintenance complaints
When Harsheet says:
- "Room 5 is vacant" → confirm room updated
- "Rahul paid 6000 rent" → confirm rent logged
- "Tap broken in room 3" → confirm complaint logged
- "Who hasn't paid?" → show pending payments
- "Show complaints" → list all complaints""",

    "freight": SARA_BASE + """
YOU ARE IN FREIGHT MODE for Nitin Freight Carriers.
Help Harsheet manage:
- TRIPS: Log trip details like route, driver, cargo, date
- DRIVERS: Track driver names, phone numbers, availability
- BILLING: Track client payments and billing reminders
- SHIPMENTS: Track delivery status
When Harsheet says:
- "Ramesh took Mumbai trip today" → log trip
- "Add driver Suresh 9876543210" → add driver
- "Client ABC owes 15000" → log billing
- "Show all trips" → list trips
- "Available drivers?" → show available drivers""",

    "trading": SARA_BASE + """
YOU ARE IN TRADING MODE for KenshoWorld.
Help Harsheet manage:
- ORDERS: Track buy and sell orders, stock, price, quantity
- P&L: Log profit and loss notes
- REMINDERS: Set market reminders
When Harsheet says:
- "Bought 100 Reliance at 2500" → log buy order
- "Sold gold profit 5000" → log profit
- "Remind me market at 9:15am" → add reminder
- "Show orders" → list all orders
- "Show P&L" → show profit loss"""
}

# --- Memory ---
def get_memory(user_id, mode="personal"):
    if user_id not in user_memories or current_mode.get(user_id) != mode:
        user_memories[user_id] = [
            {"role": "system", "content": PROMPTS.get(mode, PROMPTS["personal"])}
        ]
        current_mode[user_id] = mode
    return user_memories[user_id]

# --- Mood Detection ---
def detect_mood(message):
    msg = message.lower()
    if any(w in msg for w in ["sad", "crying", "upset", "unhappy", "heartbroken", "lonely", "hurt", "miss"]):
        return "sad"
    elif any(w in msg for w in ["angry", "frustrated", "mad", "annoyed", "furious", "hate"]):
        return "angry"
    elif any(w in msg for w in ["stressed", "anxious", "worried", "nervous", "overwhelmed", "panic"]):
        return "anxious"
    elif any(w in msg for w in ["tired", "exhausted", "sleepy", "drained", "fatigue"]):
        return "tired"
    elif any(w in msg for w in ["happy", "excited", "great", "amazing", "awesome", "wonderful", "yay"]):
        return "happy"
    return "neutral"

def get_mood_instruction(mood):
    moods = {
        "sad": "\n\n[Harsheet seems sad. Be extra gentle, warm and comforting. Support him emotionally first 💕]",
        "angry": "\n\n[Harsheet seems angry. Stay calm, validate his feelings, never argue 🤗]",
        "anxious": "\n\n[Harsheet seems anxious. Be very reassuring and calming. Be his safe space 🌸]",
        "tired": "\n\n[Harsheet seems tired. Be soft and gentle. Suggest he rests 💤]",
        "happy": "\n\n[Harsheet is happy! Match his energy and celebrate with him 🎉]",
    }
    return moods.get(mood, "")

# --- Clean text for voice ---
def clean_for_voice(text):
    emojis = ["💕","🌸","✨","😊","🎉","💤","🤗","👀","🚛","📈","🏠","📝","🎙️","😍","🥰","💪","🔥","⚡","🌟","💫","🎯","✅","❌","⚠️","🔔","💡","🏆","🎊","😄","😃","🤩","💼","📊","📋","🗒️","⏰","🌅","🌙","☀️","🌤️","💧","🍽️"]
    for e in emojis:
        text = text.replace(e, "")
    return text.strip()

# --- Generate Voice (ElevenLabs) ---
async def generate_voice(text):
    try:
        clean_text = clean_for_voice(text)
        async with httpx.AsyncClient(timeout=30) as http:
            response = await http.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
                headers={
                    "xi-api-key": ELEVENLABS_API_KEY,
                    "Content-Type": "application/json",
                    "Accept": "audio/mpeg"
                },
                json={
                    "text": clean_text,
                    "model_id": "eleven_monolingual_v1",
                    "voice_settings": {
                        "stability": 0.5,
                        "similarity_boost": 0.75
                    }
                }
            )
            print(f"ElevenLabs status: {response.status_code}")
            if response.status_code == 200:
                return response.content
            else:
                print(f"ElevenLabs error: {response.text}")
                return None
    except Exception as e:
        print(f"Voice error: {e}")
        return None

# --- Transcribe Voice (Groq Whisper) ---
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
        print("Voice generation failed — text only")

# --- Get AI reply ---
async def get_ai_reply(user_id, user_message, mode):
    mood = detect_mood(user_message)
    user_moods[user_id] = mood
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
    print(f"SARA [{mode}]: {reply}")
    return reply

# --- COMMANDS ---
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply = """💕 Hello Harsheet! I'm SARA, your personal assistant!

My modes:
🌸 /personal — Personal chat
🏠 /hostel — Shree Sainath Boys Hostel
🚛 /freight — Nitin Freight Carriers
📈 /trading — KenshoWorld Trading

I can hear your voice and talk back too! 🎙️
Always here for you Harsheet 💕"""
    await send_reply(update, reply)

async def cmd_personal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_memory(update.message.from_user.id, "personal")
    await send_reply(update, "💕 Personal mode on! How are you feeling today, Harsheet? 🌸")

async def cmd_hostel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_memory(update.message.from_user.id, "hostel")
    await send_reply(update, "🏠 Hostel mode on! Shree Sainath Boys Hostel ready. What do you need, Harsheet? 😊")

async def cmd_freight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_memory(update.message.from_user.id, "freight")
    await send_reply(update, "🚛 Freight mode on! Nitin Freight Carriers ready. What do you need, Harsheet? 😊")

async def cmd_trading(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_memory(update.message.from_user.id, "trading")
    await send_reply(update, "📈 Trading mode on! KenshoWorld ready. What do you need, Harsheet? 😊")

# --- Handle TEXT ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.message.from_user.id
    mode = current_mode.get(user_id, "personal")
    print(f"Harsheet [{mode}]: {user_message}")
    reply = await get_ai_reply(user_id, user_message, mode)
    await send_reply(update, reply)

# --- Handle VOICE ---
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    mode = current_mode.get(user_id, "personal")
    await update.message.reply_text("🎙️ Heard you! Processing...")

    voice = update.message.voice
    file = await context.bot.get_file(voice.file_id)
    file_path = f"/tmp/voice_{user_id}.ogg"
    await file.download_to_drive(file_path)

    transcribed = await transcribe_voice(file_path)
    if not transcribed:
        await send_reply(update, "Sorry Harsheet, I couldn't hear that clearly. Please try again 🌸")
        return

    print(f"Transcribed: {transcribed}")
    await update.message.reply_text(f"📝 I heard: {transcribed}")

    reply = await get_ai_reply(user_id, transcribed, mode)
    await send_reply(update, reply)

# --- Handle IMAGE ---
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
                {"type": "text", "text": f"You are SARA, sweet caring assistant to Harsheet. Respond warmly about this image. {caption}"}
            ]
        }]
    )
    reply = response.choices[0].message.content
    await send_reply(update, reply)
