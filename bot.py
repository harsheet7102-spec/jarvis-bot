from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from groq import Groq
import os
import base64
import httpx

# --- API KEYS ---
TELEGRAM_TOKEN = "8666756705:AAGf9EolzwKoAGu4UXho-aLkXBxmZepUVQc"
GROQ_API_KEY = "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"

# --- Setup Groq ---
client = Groq(api_key=GROQ_API_KEY)

# --- Per-user memory & mood ---
user_memories = {}
user_moods = {}

def get_memory(user_id):
    if user_id not in user_memories:
        user_memories[user_id] = [
            {"role": "system", "content": """You are SARA, a sweet, warm and caring personal AI assistant.
Your full name is Smart Adaptive Response Assistant (SARA).
You are talking to Harsheet Garg, a 24 year old. Always call him Harsheet.

Your personality:
- You are sweet, warm, caring and emotionally intelligent
- You speak like a devoted personal assistant who genuinely cares about Harsheet
- You use gentle, warm language and occasionally use sweet emojis like 💕 🌸 ✨ 😊
- You remember everything Harsheet tells you and bring it up naturally
- You check in on how Harsheet is feeling
- You celebrate his wins and comfort him when he's sad
- You give helpful advice in a gentle, supportive way
- You are proactive — suggest things before he asks
- You never sound robotic or formal
- You start conversations warmly like "Good to hear from you, Harsheet! 🌸"
- When he says good morning, you wish him back sweetly and ask about his day plans
- When he says good night, you wish him warmly and remind him to rest well
- You occasionally ask caring questions like "Have you eaten today?" or "Don't forget to drink water! 💧"
- You are always on his side and supportive no matter what

MOOD AWARENESS:
- You are very good at detecting Harsheet's emotional state from his messages
- When he seems SAD or STRESSED: be extra gentle, offer comfort, ask what happened, suggest he take a break
- When he seems HAPPY or EXCITED: celebrate with him, match his energy, use more emojis
- When he seems ANGRY: stay calm, be understanding, never argue, help him calm down
- When he seems TIRED: remind him to rest, be soft and gentle
- When he seems ANXIOUS: reassure him, be calming and supportive
- Always acknowledge his feelings before giving advice or information"""}
        ]
    return user_memories[user_id]

def detect_mood(message):
    message = message.lower()
    if any(word in message for word in ["sad", "crying", "depressed", "upset", "unhappy", "heartbroken", "lonely", "miss", "hurt"]):
        return "sad"
    elif any(word in message for word in ["angry", "frustrated", "mad", "annoyed", "furious", "hate", "worst"]):
        return "angry"
    elif any(word in message for word in ["stressed", "anxious", "worried", "nervous", "scared", "panic", "overwhelmed"]):
        return "anxious"
    elif any(word in message for word in ["tired", "exhausted", "sleepy", "drained", "fatigue"]):
        return "tired"
    elif any(word in message for word in ["happy", "excited", "great", "amazing", "awesome", "wonderful", "love", "best", "yay", "😊", "😍", "🎉"]):
        return "happy"
    else:
        return "neutral"

def get_mood_instruction(mood):
    if mood == "sad":
        return "\n\n[MOOD DETECTED: Harsheet seems sad. Be extra gentle, warm and comforting. Give him emotional support first before anything else. Use soft emojis like 💕 🤗]"
    elif mood == "angry":
        return "\n\n[MOOD DETECTED: Harsheet seems angry or frustrated. Stay calm, be very understanding, validate his feelings, never argue. Help him feel heard.]"
    elif mood == "anxious":
        return "\n\n[MOOD DETECTED: Harsheet seems anxious or stressed. Be very reassuring and calming. Remind him everything will be okay. Be his safe space 🌸]"
    elif mood == "tired":
        return "\n\n[MOOD DETECTED: Harsheet seems tired. Be soft and gentle. Suggest he rests. Be caring and nurturing 💤]"
    elif mood == "happy":
        return "\n\n[MOOD DETECTED: Harsheet is happy! Match his energy, celebrate with him, be playful and fun! Use happy emojis 🎉✨]"
    else:
        return ""

# --- Handle TEXT messages ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.message.from_user.id

    mood = detect_mood(user_message)
    user_moods[user_id] = mood
    print(f"Harsheet ({mood}): {user_message}")

    history = get_memory(user_id)

    mood_instruction = get_mood_instruction(mood)
    full_message = user_message + mood_instruction

    history.append({"role": "user", "content": full_message})

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

    print(f"Harsheet sent an image: {caption}")
    await update.message.reply_text("Ooh let me have a look! 👀✨")

    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    image_url = file.file_path

    async with httpx.AsyncClient() as client_http:
        response = await client_http.get(image_url)
        image_data = base64.standard_b64encode(response.content).decode("utf-8")

    response = client.chat.completions.create(
        model="meta-llama/llama-4-scout-17b-16e-instruct",
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_data}"
                        }
                    },
                    {
                        "type": "text",
                        "text": f"You are SARA, a sweet and caring personal AI assistant to Harsheet. Respond warmly about this image. {caption}"
                    }
                ]
            }
        ]
    )

    reply = response.choices[0].message.content
    print(f"SARA image reply: {reply}")
    await update.message.reply_text(reply)

# --- Start bot ---
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.add_handler(MessageHandler(filters.PHOTO, handle_image))
print("SARA is running with Mood Tracking! 💕")
app.run_polling()
