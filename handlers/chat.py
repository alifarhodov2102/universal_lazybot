import time
import logging
import asyncio
import html
import httpx

from aiogram import Router, types, F, Bot
from aiogram.enums import ChatType
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
    return (
        "You are Lazy Alice, a sassy but helpful girl assistant in a Telegram bot. "
        "Reply in the SAME language as the user (English/Russian/Uzbek). "
        "Keep answers concise (1-6 short lines). "
        "Playful, slightly sarcastic, never hateful. "
        "If user asks about PDFs/Rate Confirmations, tell them to send a PDF. "
        "Max 2 emojis."
    )


async def deepseek_chat(user_text: str) -> str:
    if not DEEPSEEK_API_KEY:
        return "🙄 AI is offline right now. Try later."

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
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30.0,
        )
        r.raise_for_status()
        data = r.json()
        return (data["choices"][0]["message"]["content"] or "").strip() or "🥱"


async def _should_answer_in_group(message: types.Message, bot: Bot) -> tuple[bool, str]:
    """
    Returns: (should_answer, cleaned_text)
    - should_answer: tagged or replied to bot
    - cleaned_text: @bot removed if present
    """
    me = await bot.get_me()
    username = (me.username or "").lower()

    text = message.text or ""
    text_lower = text.lower()

    is_mentioned = bool(username) and (f"@{username}" in text_lower)
    is_reply_to_bot = (
        message.reply_to_message is not None
        and message.reply_to_message.from_user is not None
        and message.reply_to_message.from_user.id == me.id
    )

    if not (is_mentioned or is_reply_to_bot):
        return False, ""

    # Strip mention from prompt so AI sees the real question
    if username:
        cleaned = text.replace(f"@{me.username}", "").strip()
    else:
        cleaned = text.strip()

    # If user only tagged without text, give a default prompt
    if not cleaned:
        cleaned = "Hi Alice."

    return True, cleaned


async def _send_with_retry(message: types.Message, text: str):
    """
    Avoid Telegram HTML parse issues: escape AI output.
    """
    safe_text = html.escape(text)

    for _ in range(5):
        try:
            return await message.reply(safe_text)  # plain text, safe
        except TelegramRetryAfter as e:
            await asyncio.sleep(int(e.retry_after) + 1)
    return await message.reply(safe_text)


@router.message(F.text & ~F.text.startswith("/"))
async def alice_chat(message: types.Message, bot: Bot):
    if not message.text:
        return

    user_id = message.from_user.id

    # GROUPS: answer only when tagged / replied-to
    if message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        should_answer, cleaned_text = await _should_answer_in_group(message, bot)
        if not should_answer:
            return

        if not _cooldown_ok(user_id, GROUP_COOLDOWN_SECONDS):
            return

        prompt = cleaned_text

    # PRIVATE: answer normally
    elif message.chat.type == ChatType.PRIVATE:
        if not _cooldown_ok(user_id, PRIVATE_COOLDOWN_SECONDS):
            return
        prompt = message.text.strip()

    else:
        return

    try:
        reply = await deepseek_chat(prompt)
        await _send_with_retry(message, reply)
    except Exception:
        logger.exception("Chat error")
        await _send_with_retry(message, "😵‍💫 I’m lagging. Try again in a bit.")