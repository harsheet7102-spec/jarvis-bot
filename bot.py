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

# --- Per-user memory ---
user_memories = {}

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
- You are always on his side and supportive no matter what"""}
        ]
    return user_memories[user_id]

# --- Handle TEXT messages ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.message.from_user.id
    print(f"Harsheet: {user_message}")

    history = get_memory(user_id)
    history.append({"role": "user", "content": user_message})

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
print("SARA is running and ready for Harsheet! 💕")
app.run_polling()
