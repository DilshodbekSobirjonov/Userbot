import os
import time
import random
import asyncio
from collections import defaultdict, deque

import httpx
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
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

if not BOT_TOKEN or not OPENAI_API_KEY or not ANTHROPIC_API_KEY:
    raise RuntimeError("❌ Проверь .env — отсутствуют ключи")

openai.api_key = OPENAI_API_KEY

# ================= SETTINGS =================
AI_TRIGGER = "AI CHAT"
STOP_TRIGGER = "STOP AI"

DELAY_RANGE = (5.0, 7.0)   # задержка ответа
MAX_TOKENS = 400
TIMEOUT = 30 * 60          # 30 минут

# ================= STATE =================
sessions = {}                   # chat_id -> {model, last_activity}
queues = defaultdict(deque)     # chat_id -> очередь сообщений
locks = defaultdict(asyncio.Lock)

# ================= OPENAI =================
def ask_openai(prompt: str) -> str:
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=MAX_TOKENS
    )
    return response.choices[0].message["content"].strip()

# ================= ANTHROPIC (HTTP) =================
async def ask_anthropic(prompt: str) -> str:
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    payload = {
        "model": "claude-3-haiku-20240307",
        "max_tokens": MAX_TOKENS,
        "messages": [
            {"role": "user", "content": prompt}
        ],
    }

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=payload,
        )
        r.raise_for_status()
        data = r.json()
        return data["content"][0]["text"].strip()

# ================= SESSION =================
def activate_session(chat_id: int) -> str:
    model = random.choice(["openai", "anthropic"])
    sessions[chat_id] = {
        "model": model,
        "last_activity": time.time(),
    }
    return model

def deactivate_session(chat_id: int):
    sessions.pop(chat_id, None)
    queues.pop(chat_id, None)

def session_active(chat_id: int) -> bool:
    return chat_id in sessions

# ================= QUEUE PROCESSOR =================
async def process_queue(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    async with locks[chat_id]:
        while queues[chat_id]:
            text = queues[chat_id].popleft()
            await asyncio.sleep(random.uniform(*DELAY_RANGE))

            try:
                model = sessions[chat_id]["model"]

                if model == "openai":
                    reply = ask_openai(text)
                else:
                    reply = await ask_anthropic(text)

                await context.bot.send_message(chat_id, reply)

            except Exception:
                await context.bot.send_message(
                    chat_id,
                    "⚠️ Ошибка AI, попробуйте позже"
                )

            sessions[chat_id]["last_activity"] = time.time()

# ================= HANDLER =================
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    chat_id = update.message.chat_id

    # STOP AI — приоритет
    if text.upper() == STOP_TRIGGER:
        if session_active(chat_id):
            deactivate_session(chat_id)
            await update.message.reply_text("🛑 AI режим отключён")
        return

    # AI CHAT — активация
    if text.upper() == AI_TRIGGER:
        if not session_active(chat_id):
            model = activate_session(chat_id)
            name = "ChatGPT" if model == "openai" else "Anthropic AI"
            await update.message.reply_text(
                f"🤖 AI режим активирован\n"
                f"Модель: {name}\n"
                f"Для выхода напишите: STOP AI"
            )
        return

    # Обычное сообщение
    if session_active(chat_id):
        sessions[chat_id]["last_activity"] = time.time()
        queues[chat_id].append(text)

        if len(queues[chat_id]) == 1:
            asyncio.create_task(process_queue(chat_id, context))

# ================= CLEANUP JOB =================
async def cleanup_job(context: ContextTypes.DEFAULT_TYPE):
    now = time.time()
    for cid in list(sessions.keys()):
        if now - sessions[cid]["last_activity"] > TIMEOUT:
            deactivate_session(cid)

# ================= MAIN =================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_message)
    )

    # каждые 60 сек чистим сессии
    app.job_queue.run_repeating(cleanup_job, interval=60, first=60)

    print("✅ Telegram Business AI Bot запущен")
    app.run_polling()

if __name__ == "__main__":
    main()