import os
import time
import random
import asyncio
from collections import defaultdict, deque

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    filters
)

from openai import OpenAI
import anthropic

# ================= ENV =================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
anthropic_client = anthropic.Anthropic(
    api_key=os.getenv("ANTHROPIC_API_KEY")
)

# ================= SETTINGS =================
AI_TRIGGER = "AI CHAT"
STOP_TRIGGER = "STOP AI"

DELAY_RANGE = (5.0, 7.0)
MAX_TOKENS = 400
TIMEOUT = 30 * 60  # 30 min

# ================= STATE =================
sessions = {}
queues = defaultdict(deque)
locks = defaultdict(asyncio.Lock)

# ================= AI =================
async def ask_openai(text):
    r = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": text}],
        max_tokens=MAX_TOKENS
    )
    return r.choices[0].message.content.strip()

async def ask_anthropic(text):
    r = anthropic_client.messages.create(
        model="claude-3-haiku-20240307",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": text}]
    )
    return r.content[0].text.strip()

# ================= SESSION =================
def activate(chat_id):
    model = random.choice(["openai", "anthropic"])
    sessions[chat_id] = {
        "model": model,
        "last": time.time()
    }
    return model

def deactivate(chat_id):
    sessions.pop(chat_id, None)
    queues.pop(chat_id, None)

def active(chat_id):
    return chat_id in sessions

# ================= QUEUE =================
async def process(chat_id, context):
    async with locks[chat_id]:
        while queues[chat_id]:
            text = queues[chat_id].popleft()
            await asyncio.sleep(random.uniform(*DELAY_RANGE))

            try:
                model = sessions[chat_id]["model"]
                if model == "openai":
                    reply = await ask_openai(text)
                else:
                    reply = await ask_anthropic(text)

                await context.bot.send_message(chat_id, reply)

            except Exception:
                await context.bot.send_message(chat_id, "⚠️ Ошибка AI")

            sessions[chat_id]["last"] = time.time()

# ================= HANDLER =================
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    chat_id = update.message.chat_id

    if text.upper() == STOP_TRIGGER:
        if active(chat_id):
            deactivate(chat_id)
            await update.message.reply_text("🛑 AI режим отключён")
        return

    if text.upper() == AI_TRIGGER:
        if not active(chat_id):
            model = activate(chat_id)
            name = "ChatGPT" if model == "openai" else "Anthropic AI"
            await update.message.reply_text(
                f"🤖 AI режим активирован\n"
                f"Модель: {name}\n"
                f"Для выхода: STOP AI"
            )
        return

    if active(chat_id):
        sessions[chat_id]["last"] = time.time()
        queues[chat_id].append(text)
        if len(queues[chat_id]) == 1:
            asyncio.create_task(process(chat_id, context))

# ================= CLEANER =================
async def cleaner():
    while True:
        now = time.time()
        for cid in list(sessions.keys()):
            if now - sessions[cid]["last"] > TIMEOUT:
                deactivate(cid)
        await asyncio.sleep(60)

# ================= MAIN =================
async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    asyncio.create_task(cleaner())
    print("✅ Telegram Business AI Bot запущен")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
