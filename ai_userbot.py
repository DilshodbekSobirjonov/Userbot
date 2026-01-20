import os
import asyncio
import random
import time
from collections import defaultdict, deque

from telethon import TelegramClient, events
from dotenv import load_dotenv

from openai import OpenAI
import anthropic

# ================== LOAD ENV ==================
load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
anthropic_client = anthropic.Anthropic(
    api_key=os.getenv("ANTHROPIC_API_KEY")
)

# ================== CONSTANTS ==================
AI_TRIGGER = "AI CHAT"
STOP_TRIGGER = "STOP AI"

INACTIVITY_TIMEOUT = 30 * 60  # 30 minutes
DELAY_RANGE = (4.5, 7.0)      # seconds
MAX_TOKENS = 400

# ================== STATE ==================
sessions = {}  # chat_id -> session data
queues = defaultdict(deque)
locks = defaultdict(asyncio.Lock)

# ================== CLIENT ==================
client = TelegramClient("userbot_session", API_ID, API_HASH)

# ================== AI FUNCTIONS ==================

async def ask_openai(prompt):
    resp = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=MAX_TOKENS
    )
    return resp.choices[0].message.content.strip()


async def ask_anthropic(prompt):
    resp = anthropic_client.messages.create(
        model="claude-3-haiku-20240307",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}]
    )
    return resp.content[0].text.strip()

# ================== SESSION HELPERS ==================

def activate_session(chat_id):
    model = random.choice(["openai", "anthropic"])
    sessions[chat_id] = {
        "model": model,
        "last_activity": time.time()
    }
    return model


def deactivate_session(chat_id):
    sessions.pop(chat_id, None)
    queues.pop(chat_id, None)


def session_active(chat_id):
    return chat_id in sessions


def update_activity(chat_id):
    if chat_id in sessions:
        sessions[chat_id]["last_activity"] = time.time()

# ================== MESSAGE PROCESSOR ==================

async def process_queue(chat_id):
    async with locks[chat_id]:
        while queues[chat_id]:
            text, event = queues[chat_id].popleft()

            delay = random.uniform(*DELAY_RANGE)
            await asyncio.sleep(delay)

            model = sessions[chat_id]["model"]

            try:
                if model == "openai":
                    answer = await ask_openai(text)
                else:
                    answer = await ask_anthropic(text)

                await event.reply(answer)

            except Exception as e:
                await event.reply("⚠️ Ошибка AI, попробуй позже.")

            update_activity(chat_id)

# ================== EVENT HANDLER ==================

@client.on(events.NewMessage)
async def handler(event):
    if not event.text:
        return

    chat_id = event.chat_id
    text = event.text.strip()

    # STOP AI — always priority
    if text.upper() == STOP_TRIGGER:
        if session_active(chat_id):
            deactivate_session(chat_id)
            await event.reply("🛑 AI режим отключён")
        return

    # ACTIVATE AI
    if text.upper() == AI_TRIGGER:
        if not session_active(chat_id):
            model = activate_session(chat_id)
            name = "ChatGPT" if model == "openai" else "Anthropic AI"
            await event.reply(
                f"🤖 AI режим активирован\n"
                f"Модель: {name}\n"
                f"Для выхода напишите: STOP AI"
            )
        return

    # NORMAL MESSAGE
    if session_active(chat_id):
        update_activity(chat_id)
        queues[chat_id].append((text, event))
        if len(queues[chat_id]) == 1:
            asyncio.create_task(process_queue(chat_id))

# ================== CLEANUP TASK ==================

async def cleanup_sessions():
    while True:
        now = time.time()
        for chat_id in list(sessions.keys()):
            if now - sessions[chat_id]["last_activity"] > INACTIVITY_TIMEOUT:
                deactivate_session(chat_id)
        await asyncio.sleep(60)

# ================== MAIN ==================

async def main():
    await client.start()
    asyncio.create_task(cleanup_sessions())
    print("✅ AI userbot запущен")
    await client.run_until_disconnected()

client.loop.run_until_complete(main())
