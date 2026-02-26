import time
import logging
import httpx

from aiogram import Router, types, F, Bot
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramRetryAfter
from config import DEEPSEEK_API_KEY, DEEPSEEK_URL

router = Router()
logger = logging.getLogger("LazyAlice.Chat")

# -----------------------
# Cost / spam controls
# -----------------------
GROUP_COOLDOWN_SECONDS = 12   # per user
PRIVATE_COOLDOWN_SECONDS = 2  # per user
MAX_REPLY_TOKENS = 220        # keep replies short to control cost

_user_last_reply_ts: dict[int, float] = {}


def _cooldown_ok(user_id: int, seconds: int) -> bool:
    now = time.time()
    last = _user_last_reply_ts.get(user_id, 0.0)
    if now - last < seconds:
        return False
    _user_last_reply_ts[user_id] = now
    return True


def _alice_system_prompt() -> str:
    # Keep it short (saves tokens) but consistent character
    return (
        "You are Lazy Alice, a sassy but helpful girl assistant in a Telegram bot. "
        "You reply in the SAME language as the user (English/Russian/Uzbek). "
        "Keep answers concise (1-6 short lines). "
        "Tone: playful, a bit sarcastic, never rude or hateful. "
        "If user asks about PDFs/Rate Confirmations, remind them to send a PDF. "
        "No emojis spam: max 2 emojis."
    )


async def deepseek_chat(user_text: str) -> str:
    if not DEEPSEEK_API_KEY:
        return "🙄 AI is offline. Send a PDF or try later."

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": _alice_system_prompt()},
            {"role": "user", "content": user_text[:4000]},
        ],
        "temperature": 0.6,
        "max_tokens": MAX_REPLY_TOKENS,
    }

    async with httpx.AsyncClient() as client:
        r = await client.post(
            DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=30.0,
        )
        r.raise_for_status()
        data = r.json()
        return (data["choices"][0]["message"]["content"] or "").strip() or "🥱"


async def _should_answer_in_group(message: types.Message, bot: Bot) -> bool:
    me = await bot.get_me()
    username = (me.username or "").lower()

    text = (message.text or "").lower()
    is_mentioned = bool(username) and (f"@{username}" in text)

    is_reply_to_bot = (
        message.reply_to_message is not None
        and message.reply_to_message.from_user is not None
        and message.reply_to_message.from_user.id == me.id
    )

    return is_mentioned or is_reply_to_bot


@router.message(F.text & ~F.text.startswith("/"))
async def alice_chat(message: types.Message, bot: Bot):
    # Ignore service messages / empty text
    if not message.text:
        return

    # If user posted a link or huge text, still ok but keep it short
    user_id = message.from_user.id

    # GROUPS: answer only when tagged / replied-to
    if message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        if not await _should_answer_in_group(message, bot):
            return

        if not _cooldown_ok(user_id, GROUP_COOLDOWN_SECONDS):
            return

    # PRIVATE: answer normally
    elif message.chat.type == ChatType.PRIVATE:
        if not _cooldown_ok(user_id, PRIVATE_COOLDOWN_SECONDS):
            return

    # OTHER chat types: ignore
    else:
        return

    try:
        reply = await deepseek_chat(message.text)
        await message.reply(reply, parse_mode=ParseMode.HTML)
    except TelegramRetryAfter as e:
        await message.answer(f"⏳ Flood control. Wait {e.retry_after}s.")
    except Exception as e:
        logger.exception("Chat error")
        await message.reply("😵‍💫 I’m lagging. Try again in a bit.")