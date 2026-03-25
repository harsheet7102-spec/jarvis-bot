from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from groq import Groq
import os

# --- KEYS FROM ENVIRONMENT VARIABLES (safe!) ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

# --- Setup Groq ---
client = Groq(api_key=GROQ_API_KEY)

# --- Per-user memory stored in RAM ---
user_memories = {}

def get_memory(user_id):
    if user_id not in user_memories:
        user_memories[user_id] = [
            {"role": "system", "content": "You are JARVIS, a highly intelligent personal AI assistant. Remember everything the user tells you across the conversation."}
        ]
    return user_memories[user_id]

# --- Handle messages ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.message.from_user.id
    print(f"User {user_id}: {user_message}")

    history = get_memory(user_id)
    history.append({"role": "user", "content": user_message})

    # Keep only last 20 messages to avoid overflow
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

# --- Start bot ---
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
print("JARVIS is running...")
app.run_polling()
```

---

## Now fix your Railway Environment Variables:

Go to Railway → **just-embrace** → **Variables** and add:
```
TELEGRAM_TOKEN = 8666756705:AAGf9EolzwKoAGu4UXho-aLkXBxmZepUVQc
GROQ_API_KEY = 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
