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
            {"role": "system", "content": "You are JARVIS, a highly intelligent personal AI assistant. Remember everything the user tells you across the conversation."}
        ]
    return user_memories[user_id]

# --- Handle TEXT messages ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.message.from_user.id
    print(f"User {user_id}: {user_message}")

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
    print(f"JARVIS: {reply}")

    await update.message.reply_text(reply)

# --- Handle IMAGE messages ---
async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    caption = update.message.caption or "What is in this image? Describe it in detail."
    
    print(f"User {user_id} sent an image with caption: {caption}")
    await update.message.reply_text("Analyzing image, please wait... 🔍")

    # Get image file from Telegram
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    image_url = file.file_path

    # Download image and convert to base64
    async with httpx.AsyncClient() as client_http:
        response = await client_http.get(image_url)
        image_data = base64.standard_b64encode(response.content).decode("utf-8")

    # Send to Groq vision model
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
                        "text": f"You are JARVIS, a personal AI assistant. {caption}"
                    }
                ]
            }
        ]
    )

    reply = response.choices[0].message.content
    print(f"JARVIS image reply: {reply}")
    await update.message.reply_text(reply)

# --- Start bot ---
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.add_handler(MessageHandler(filters.PHOTO, handle_image))
print("JARVIS is running with Image Understanding...")
app.run_polling()
