import os
import time
import asyncio
from collections import defaultdict, deque

import httpx
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

# ============== ENV ==============
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

if not BOT_TOKEN or not ANTHROPIC_API_KEY:
    raise RuntimeError("❌ Проверь .env файл")

# ============== SETTINGS ==============
MODEL_ID = "claude-3-haiku-20240307"
MODEL_LABEL = "Claude 3 Haiku"
SIGNATURE = "by: @elyyxs"

MAX_TOKENS = 400
MEMORY_LIMIT = 6
SESSION_TIMEOUT = 30 * 60

DELAY_MIN = 4.5
DELAY_MAX = 6.5

# 💰 ЛИМИТ (грубо, но надёжно)
MAX_TOKENS_PER_DAY = 10000
tokens_used_today = 0
last_day = time.strftime("%Y-%m-%d")

# ============== STATE ==============
sessions = {}
queues = defaultdict(deque)
locks = defaultdict(asyncio.Lock)

# ============== HELPERS ==============
def reset_daily_limit():
    global tokens_used_today, last_day
    today = time.strftime("%Y-%m-%d")
    if today != last_day:
        tokens_used_today = 0
        last_day = today

def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)

def ensure_session(chat_id):
    if chat_id not in sessions:
        sessions[chat_id] = {
            "history": [],
            "last_activity": time.time(),
        }

def cleanup_sessions():
    now = time.time()
    for cid in list(sessions.keys()):
        if now - sessions[cid]["last_activity"] > SESSION_TIMEOUT:
            sessions.pop(cid, None)
            queues.pop(cid, None)

# ============== ANTHROPIC HTTP ==============
async def ask_claude(messages):
    global tokens_used_today

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    payload = {
        "model": MODEL_ID,
        "max_tokens": MAX_TOKENS,
        "messages": messages,
    }

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=payload,
        )
        r.raise_for_status()
        data = r.json()

    text = data["content"][0]["text"]
    tokens_used_today += estimate_tokens(text)
    return text.strip()

# ============== QUEUE PROCESSOR ==============
async def process_queue(chat_id, context):
    async with locks[chat_id]:
        while queues[chat_id]:
            text = queues[chat_id].popleft()
            await asyncio.sleep(
                DELAY_MIN + (DELAY_MAX - DELAY_MIN) * 0.5
            )

            try:
                reset_daily_limit()
                if tokens_used_today >= MAX_TOKENS_PER_DAY:
                    await context.bot.send_message(
                        chat_id,
                        f"💰 Дневной лимит исчерпан\n\n{SIGNATURE}"
                    )
                    continue

                ensure_session(chat_id)
                session = sessions[chat_id]

                messages = [
                    {"role": "user", "content": text}
                ]

                if session["history"]:
                    messages = session["history"] + messages

                answer = await ask_claude(messages)

                session["history"].append({"role": "user", "content": text})
                session["history"].append({"role": "assistant", "content": answer})
                session["history"] = session["history"][-MEMORY_LIMIT * 2:]
                session["last_activity"] = time.time()

                final_text = (
                    f"🤖 {MODEL_LABEL}\n"
                    f"{SIGNATURE}\n\n"
                    f"{answer}"
                )

                await context.bot.send_message(chat_id, final_text)

            except httpx.HTTPStatusError as e:
                await context.bot.send_message(
                    chat_id,
                    f"⚠️ Claude HTTP {e.response.status_code}\n\n{SIGNATURE}"
                )
            except Exception as e:
                await context.bot.send_message(
                    chat_id,
                    f"⚠️ Ошибка: {str(e)}\n\n{SIGNATURE}"
                )

# ============== HANDLER ==============
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    chat_id = update.message.chat_id
    text = update.message.text.strip()

    ensure_session(chat_id)
    sessions[chat_id]["last_activity"] = time.time()

    queues[chat_id].append(text)
    if len(queues[chat_id]) == 1:
        asyncio.create_task(process_queue(chat_id, context))

# ============== MAIN ==============
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    app.job_queue.run_repeating(
        lambda _: cleanup_sessions(),
        interval=60,
        first=60,
    )

    print("✅ Elyyxs Claude AI Replier запущен")
    app.run_polling()

if __name__ == "__main__":
    main()