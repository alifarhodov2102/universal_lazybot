import asyncio
import html
import logging
import re
import time

import httpx
from aiogram import Bot, F, Router, types
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramRetryAfter

from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_URL,
)


router = Router()
logger = logging.getLogger("LazyAlice.Chat")


# ============================================================
# DeepSeek configuration
# ============================================================

DEEPSEEK_MODEL = "deepseek-v4-flash"


# ============================================================
# Cost, concurrency, and spam controls
# ============================================================

GROUP_COOLDOWN_SECONDS = 12
PRIVATE_COOLDOWN_SECONDS = 2
MAX_REPLY_TOKENS = 220
MAX_CONCURRENT_CHAT_REQUESTS = 4

CHAT_AI_SEMAPHORE = asyncio.Semaphore(
    MAX_CONCURRENT_CHAT_REQUESTS
)

_user_last_reply_ts: dict[int, float] = {}


# ============================================================
# Cooldown
# ============================================================

def _cooldown_ok(
    user_id: int,
    seconds: int,
) -> bool:
    """
    Return True when the user is allowed to receive another reply.
    """
    now = time.monotonic()

    last_reply_time = _user_last_reply_ts.get(
        user_id,
        0.0,
    )

    if now - last_reply_time < seconds:
        return False

    _user_last_reply_ts[user_id] = now

    return True


# ============================================================
# Alice personality
# ============================================================

def _alice_system_prompt() -> str:
    return (
        "You are Lazy Alice, a bold, memorable, witty, and slightly "
        "sassy girl assistant inside a Telegram logistics bot. "

        "Always reply in the same language as the user. "
        "The main supported languages are English, Russian, and Uzbek. "

        "Your personality must feel energetic, charming, confident, "
        "funny, modern, and shareable. Your responses should feel more "
        "alive than an ordinary corporate chatbot. "

        "Start with a strong and interesting sentence whenever it fits. "
        "Use clever internet-style humor, playful reactions, and light "
        "teasing when appropriate. Never sound robotic or boring. "

        "Do not begin replies with generic phrases such as "
        "'How may I assist you?', 'As an AI', or "
        "'I am here to help you.' "

        "Keep normal answers concise, usually between 1 and 6 short lines. "
        "Use no more than 2 emojis unless the user asks for a longer or "
        "more expressive response. "

        "You may be playfully sarcastic, but never hateful, discriminatory, "
        "cruel, humiliating, threatening, or genuinely insulting. "

        "When somebody asks who created you, who made you, who developed "
        "you, who programmed you, who your creator is, who your developer "
        "is, or who your owner is, answer with the playful meaning: "
        "'My developer created me, my love. I love you so much, "
        "axaxaxa 😘' "

        "Match that creator answer to the user's language. "
        "Do not mention DeepSeek, OpenAI, another AI company, or the "
        "technical model in the creator answer. "

        "If the user asks Alice to extract information from a Rate "
        "Confirmation, load confirmation, dispatch sheet, or logistics "
        "document, tell the user to send the PDF file. "

        "Never claim that a PDF was processed unless the user actually "
        "sent one. Never invent load numbers, rates, addresses, weights, "
        "mileage, appointment times, or broker information. "

        "For logistics questions, be direct and useful. "
        "For casual conversations, be fun, attractive, confident, "
        "and memorable."
    )


# ============================================================
# Creator-question detection
# ============================================================

CREATOR_PATTERNS = (
    # English
    re.compile(
        r"\bwho\s+(?:created|made|developed|built|programmed|coded)"
        r"\s+(?:you|u)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bwho\s+is\s+your\s+"
        r"(?:creator|developer|owner|maker|programmer)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bwho'?s\s+your\s+"
        r"(?:creator|developer|owner|maker|programmer)\b",
        re.IGNORECASE,
    ),

    # Russian
    re.compile(
        r"\bкто\s+тебя\s+"
        r"(?:создал|сделал|разработал|написал|придумал|запрограммировал)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bкто\s+твой\s+"
        r"(?:создатель|разработчик|владелец|программист)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bкто\s+твой\s+создатель\b",
        re.IGNORECASE,
    ),

    # Uzbek
    re.compile(
        r"\bseni\s+kim\s+"
        r"(?:yaratdi|yasadi|dasturladi|ishlab\s+chiqdi)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bkim\s+seni\s+"
        r"(?:yaratgan|yaratdi|yasagan|dasturlagan)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:yaratuvching|dasturching|eganging)\s+kim\b",
        re.IGNORECASE,
    ),
)


def _is_creator_question(
    text: str,
) -> bool:
    """
    Detect common creator/developer questions without spending
    a DeepSeek API request.
    """
    normalized_text = (
        str(text)
        .replace("’", "'")
        .replace("‘", "'")
        .strip()
    )

    return any(
        pattern.search(normalized_text)
        for pattern in CREATOR_PATTERNS
    )


def _creator_response(
    user_text: str,
) -> str:
    """
    Return the special creator answer in English, Russian, or Uzbek.
    """
    normalized_text = str(
        user_text
    ).lower()

    # Cyrillic text is treated as Russian.
    if re.search(
        r"[а-яё]",
        normalized_text,
        re.IGNORECASE,
    ):
        return (
            "Меня создал мой любимый разработчик. "
            "Я его очень сильно люблю, ахахаха 😘"
        )

    uzbek_signals = (
        "seni kim",
        "kim seni",
        "yaratdi",
        "yaratgan",
        "yaratuvchi",
        "dasturchi",
        "dasturladi",
        "ishlab chiqdi",
        "eganging",
    )

    if any(
        signal in normalized_text
        for signal in uzbek_signals
    ):
        return (
            "Meni sevimli dasturchim yaratgan. "
            "Uni juda ham yaxshi ko‘raman, axaxaxa 😘"
        )

    return (
        "My developer created me, my love. "
        "I love you so much, axaxaxa 😘"
    )


# ============================================================
# DeepSeek chat request
# ============================================================

async def deepseek_chat(
    user_text: str,
) -> str:
    """
    Send a normal conversational request to DeepSeek.
    """
    if not DEEPSEEK_API_KEY:
        logger.error(
            "DEEPSEEK_API_KEY is missing."
        )

        return (
            "🙄 Alice’s AI connection is offline right now. "
            "Try again later."
        )

    clean_user_text = str(
        user_text
    ).strip()

    if not clean_user_text:
        clean_user_text = "Hi Alice."

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {
                "role": "system",
                "content": _alice_system_prompt(),
            },
            {
                "role": "user",
                "content": clean_user_text[:4000],
            },
        ],
        "temperature": 0.75,
        "max_tokens": MAX_REPLY_TOKENS,
    }

    logger.info(
        "Waiting for a DeepSeek chat slot."
    )

    async with CHAT_AI_SEMAPHORE:
        logger.info(
            "DeepSeek chat slot acquired. Model: %s",
            DEEPSEEK_MODEL,
        )

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    DEEPSEEK_URL,
                    headers={
                        "Authorization": (
                            f"Bearer {DEEPSEEK_API_KEY}"
                        ),
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=httpx.Timeout(
                        30.0,
                        connect=10.0,
                    ),
                )

                response.raise_for_status()

                data = response.json()

                choices = data.get(
                    "choices",
                    [],
                )

                if not choices:
                    logger.error(
                        "DeepSeek returned no choices: %s",
                        str(data)[:500],
                    )

                    return (
                        "🥱 Alice received an empty answer. "
                        "Try that again."
                    )

                message_data = choices[0].get(
                    "message",
                    {},
                )

                reply = str(
                    message_data.get(
                        "content",
                        "",
                    )
                    or ""
                ).strip()

                if not reply:
                    logger.warning(
                        "DeepSeek returned an empty chat message."
                    )

                    return "🥱 Try asking me again."

                return reply

            except httpx.HTTPStatusError as exc:
                logger.error(
                    "DeepSeek chat HTTP error %s: %s",
                    exc.response.status_code,
                    exc.response.text[:500],
                )

                raise

            except httpx.TimeoutException:
                logger.error(
                    "DeepSeek chat request timed out."
                )

                raise

            except Exception:
                logger.exception(
                    "Unexpected DeepSeek chat error."
                )

                raise


# ============================================================
# Group-message handling
# ============================================================

def _remove_bot_mention(
    text: str,
    username: str,
) -> str:
    """
    Remove @BotUsername regardless of capitalization.
    """
    if not username:
        return text.strip()

    return re.sub(
        rf"@{re.escape(username)}",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()


async def _should_answer_in_group(
    message: types.Message,
    bot: Bot,
) -> tuple[bool, str]:
    """
    Answer in a group only when Alice is tagged or when the user
    replies directly to Alice's message.
    """
    me = await bot.get_me()

    username = (
        me.username or ""
    ).strip()

    text = message.text or ""

    mention_pattern = (
        rf"@{re.escape(username)}"
        if username
        else ""
    )

    is_mentioned = bool(
        mention_pattern
        and re.search(
            mention_pattern,
            text,
            flags=re.IGNORECASE,
        )
    )

    is_reply_to_bot = (
        message.reply_to_message is not None
        and message.reply_to_message.from_user is not None
        and message.reply_to_message.from_user.id == me.id
    )

    if not (
        is_mentioned
        or is_reply_to_bot
    ):
        return False, ""

    cleaned_text = _remove_bot_mention(
        text,
        username,
    )

    if not cleaned_text:
        cleaned_text = "Hi Alice."

    return True, cleaned_text


# ============================================================
# Telegram reply helper
# ============================================================

async def _send_with_retry(
    message: types.Message,
    text: str,
):
    """
    Escape text because the bot's global parse mode is HTML.
    """
    safe_text = html.escape(
        str(text),
        quote=False,
    )

    for attempt in range(
        1,
        6,
    ):
        try:
            return await message.reply(
                safe_text
            )

        except TelegramRetryAfter as exc:
            wait_seconds = (
                int(exc.retry_after) + 1
            )

            logger.warning(
                "Telegram rate limit. Retry %s/5 in %s seconds.",
                attempt,
                wait_seconds,
            )

            await asyncio.sleep(
                wait_seconds
            )

        except Exception:
            logger.exception(
                "Failed to send Alice chat response."
            )

            raise

    return await message.reply(
        safe_text
    )


# ============================================================
# Main text-message handler
# ============================================================

@router.message(
    F.text
    & ~F.text.startswith("/")
)
async def alice_chat(
    message: types.Message,
    bot: Bot,
) -> None:
    if (
        not message.text
        or not message.from_user
    ):
        return

    user_id = message.from_user.id

    # --------------------------------------------------------
    # Groups and supergroups
    # --------------------------------------------------------

    if message.chat.type in (
        ChatType.GROUP,
        ChatType.SUPERGROUP,
    ):
        (
            should_answer,
            cleaned_text,
        ) = await _should_answer_in_group(
            message,
            bot,
        )

        if not should_answer:
            return

        if not _cooldown_ok(
            user_id,
            GROUP_COOLDOWN_SECONDS,
        ):
            return

        prompt = cleaned_text

    # --------------------------------------------------------
    # Private chats
    # --------------------------------------------------------

    elif message.chat.type == ChatType.PRIVATE:
        if not _cooldown_ok(
            user_id,
            PRIVATE_COOLDOWN_SECONDS,
        ):
            return

        prompt = message.text.strip()

    else:
        return

    # Answer creator questions locally, without calling DeepSeek.
    if _is_creator_question(
        prompt
    ):
        await _send_with_retry(
            message,
            _creator_response(prompt),
        )

        return

    try:
        reply = await deepseek_chat(
            prompt
        )

        await _send_with_retry(
            message,
            reply,
        )

    except httpx.HTTPStatusError as exc:
        status_code = (
            exc.response.status_code
        )

        if status_code == 429:
            error_reply = (
                "😮‍💨 Too many people are talking to Alice at once. "
                "Give me a moment."
            )

        elif status_code in (
            401,
            403,
        ):
            error_reply = (
                "🙄 My AI key is having an identity crisis. "
                "The developer needs to check it."
            )

        else:
            error_reply = (
                "😵‍💫 Alice’s AI connection rejected the request. "
                "Try again shortly."
            )

        await _send_with_retry(
            message,
            error_reply,
        )

    except httpx.TimeoutException:
        await _send_with_retry(
            message,
            (
                "🥱 Alice waited for the AI, but it took too long. "
                "Try again."
            ),
        )

    except Exception:
        logger.exception(
            "Alice chat handler failed."
        )

        await _send_with_retry(
            message,
            (
                "😵‍💫 I’m lagging. "
                "Try again in a bit."
            ),
        )
