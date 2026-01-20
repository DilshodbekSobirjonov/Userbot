import os
import time
import asyncio
from collections import defaultdict, deque

import openai
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    filters,
)

# ================= ENV =================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not BOT_TOKEN or not OPENAI_API_KEY:
    raise RuntimeError("❌ Проверь .env файл")

openai.api_key = OPENAI_API_KEY

# ================= SETTINGS =================
AI_TRIGGER = "AI CHAT"
STOP_TRIGGER = "STOP AI"

MAX_TOKENS = 400
MEMORY_LIMIT = 6              # сколько сообщений помнить
SESSION_TIMEOUT = 30 * 60     # 30 минут
DELAY = (4.5, 6.5)

# 💰 ЛИМИТ РАСХОДОВ (очень грубо, но надёжно)
MAX_TOKENS_PER_DAY = 8000     # ~ $0.01–0.02 на gpt-3.5
tokens_used_today = 0
last_reset_day = time.strftime("%Y-%m-%d")

# ================= STATE =================
sessions = {}                 # chat_id -> session
queues = defaultdict(deque)
locks = defaultdict(asyncio.Lock)

# ================= HELPERS =================
def reset_daily_limit():
    global tokens_used_today, last_reset_day
    today = time.strftime("%Y-%m-%d")
    if today != last_reset_day:
        tokens_used_today = 0
        last_reset_day = today

def estimate_tokens(text: str) -> int:
    # грубая оценка: 1 токен ~ 4 символа
    return max(1, len(text) // 4)

# ================= OPENAI =================
def ask_openai(messages):
    global tokens_used_today

    resp = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=messages,
        max_tokens=MAX_TOKENS,
    )

    content = resp.choices[0].message["content"].strip()

    used = estimate_tokens(content)
    tokens_used_today += used

    return content

# ================= SESSION =================
def activate_session(chat_id):
    sessions[chat_id] = {
        "history": [],
        "last_activity": time.time(),
    }

def deactivate_session(chat_id):
    sessions.pop(chat_id, None)
    queues.pop(chat_id, None)

def session_active(chat_id):
    return chat_id in sessions

# ================= QUEUE =================
async def process_queue(chat_id, context):
    async with locks[chat_id]:
        while queues[chat_id]:
            text = queues[chat_id].popleft()
            await asyncio.sleep(
                (DELAY[0] + (DELAY[1] - DELAY[0]) * 0.5)
            )

            try:
                reset_daily_limit()

                if tokens_used_today >= MAX_TOKENS_PER_DAY:
                    await context.bot.send_message(
                        chat_id,
                        "💰 Дневной лимит AI исчерпан. Попробуй завтра."
                    )
                    continue

                session = sessions[chat_id]

                # формируем контекст
                messages = [{"role": "system", "content": "Ты полезный и краткий AI помощник."}]
                messages += session["history"]
                messages.append({"role": "user", "content": text})

                answer = ask_openai(messages)

                # сохраняем память
                session["history"].append({"role": "user", "content": text})
                session["history"].append({"role": "assistant", "content": answer})
                session["history"] = session["history"][-MEMORY_LIMIT * 2 :]

                await context.bot.send_message(chat_id, answer)

                session["last_activity"] = time.time()

            except Exception as e:
                await context.bot.send_message(
                    chat_id,
                    f"⚠️ AI ошибка:\n{str(e)}"
                )

# ================= HANDLER =================
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    chat_id = update.message.chat_id

    # STOP
    if text.upper() == STOP_TRIGGER:
        if session_active(chat_id):
            deactivate_session(chat_id)
            await update.message.reply_text("🛑 AI режим отключён")
        return

    # START AI
    if text.upper() == AI_TRIGGER:
        if not session_active(chat_id):
            activate_session(chat_id)
            await update.message.reply_text(
                "🤖 AI режим активирован\n"
                "Память: включена\n"
                "Лимит: включён\n"
                "Для выхода: STOP AI"
            )
        return

    # NORMAL MESSAGE
    if session_active(chat_id):
        queues[chat_id].append(text)
        if len(queues[chat_id]) == 1:
            asyncio.create_task(process_queue(chat_id, context))

# ================= CLEANUP =================
async def cleanup(context):
    now = time.time()
    for cid in list(sessions.keys()):
        if now - sessions[cid]["last_activity"] > SESSION_TIMEOUT:
            deactivate_session(cid)

# ================= MAIN =================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_message)
    )

    app.job_queue.run_repeating(cleanup, interval=60, first=60)

    print("✅ AI Bot запущен (память + лимит)")
    app.run_polling()

if __name__ == "__main__":
    main()