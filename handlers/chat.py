import asyncio
import html
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Dict, List

import httpx
from aiogram import Bot, F, Router, types
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramRetryAfter
from sqlalchemy import delete, select

from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_URL,
)
from database.connection import AsyncSessionLocal
from database.models import ChatMessage


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

# Increased from 220 so Alice can give proper detailed answers
# when the user asks for them.
MAX_REPLY_TOKENS = 800

MAX_CONCURRENT_CHAT_REQUESTS = 4

# If DeepSeek returns an empty successful response,
# retry once automatically before bothering the user.
MAX_EMPTY_RESPONSE_ATTEMPTS = 2

CHAT_AI_SEMAPHORE = asyncio.Semaphore(
    MAX_CONCURRENT_CHAT_REQUESTS
)

_user_last_reply_ts: dict[int, float] = {}


# ============================================================
# 24-hour conversation memory
# ============================================================

CHAT_MEMORY_HOURS = 24

# Send only a small recent window back to DeepSeek so follow-up
# conversations work without letting token usage grow forever.
MAX_HISTORY_MESSAGES = 12
MAX_HISTORY_CHARACTERS = 8000

# Current chat input is already limited to 4000 characters before
# being sent to DeepSeek, so store the same maximum for memory.
MAX_STORED_MESSAGE_CHARACTERS = 4000

# Background cleanup runs every hour. Messages are also filtered by
# the exact 24-hour cutoff when history is loaded.
CHAT_MEMORY_CLEANUP_INTERVAL_SECONDS = 3600


async def cleanup_expired_chat_messages() -> int:
    """
    Permanently delete Alice chat-memory rows older than 24 hours.

    This touches only the chat_messages table. It never modifies users,
    Pro status, expiry dates, templates, payments, or PDF data.
    """
    cutoff = datetime.utcnow() - timedelta(
        hours=CHAT_MEMORY_HOURS
    )

    async with AsyncSessionLocal() as session:
        try:
            result = await session.execute(
                delete(ChatMessage).where(
                    ChatMessage.created_at < cutoff
                )
            )

            await session.commit()

            deleted_count = (
                result.rowcount
                if result.rowcount is not None
                and result.rowcount > 0
                else 0
            )

            if deleted_count:
                logger.info(
                    "Deleted %s expired chat-memory message(s).",
                    deleted_count,
                )

            return deleted_count

        except Exception:
            await session.rollback()

            logger.exception(
                "Failed to clean expired chat memory."
            )

            return 0


async def chat_memory_cleanup_loop() -> None:
    """
    Periodically remove expired chat memory in the background.
    """
    logger.info(
        "24-hour chat-memory cleanup loop started."
    )

    while True:
        try:
            await asyncio.sleep(
                CHAT_MEMORY_CLEANUP_INTERVAL_SECONDS
            )

            await cleanup_expired_chat_messages()

        except asyncio.CancelledError:
            logger.info(
                "Chat-memory cleanup loop stopped."
            )
            raise

        except Exception:
            logger.exception(
                "Unexpected chat-memory cleanup-loop error."
            )

            # Avoid a tight error loop if the database is temporarily down.
            await asyncio.sleep(60)


def _trim_history_for_context(
    messages: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    """
    Keep the newest history that fits inside the configured character
    budget. The final order remains oldest -> newest.
    """
    selected_reversed: List[Dict[str, str]] = []
    used_characters = 0

    for item in reversed(messages):
        role = str(
            item.get("role", "")
        ).strip()

        content = str(
            item.get("content", "")
        ).strip()

        if role not in {
            "user",
            "assistant",
        }:
            continue

        if not content:
            continue

        content = content[
            :MAX_STORED_MESSAGE_CHARACTERS
        ]

        projected_size = (
            used_characters
            + len(content)
        )

        if (
            projected_size
            > MAX_HISTORY_CHARACTERS
        ):
            # Stop here so context stays as one contiguous recent
            # conversation instead of skipping a newer message and
            # including an older unrelated one.
            break

        selected_reversed.append(
            {
                "role": role,
                "content": content,
            }
        )

        used_characters = projected_size

    selected_reversed.reverse()

    return selected_reversed


async def load_chat_history(
    tg_user_id: int,
    chat_id: int,
) -> List[Dict[str, str]]:
    """
    Load recent Alice conversation history for exactly one user inside
    exactly one Telegram chat.

    A user's private chat, Group A, and Group B therefore all have
    separate memories.
    """
    cutoff = datetime.utcnow() - timedelta(
        hours=CHAT_MEMORY_HOURS
    )

    async with AsyncSessionLocal() as session:
        try:
            stmt = (
                select(ChatMessage)
                .where(
                    ChatMessage.tg_user_id
                    == tg_user_id,
                    ChatMessage.chat_id
                    == chat_id,
                    ChatMessage.created_at
                    >= cutoff,
                )
                .order_by(
                    ChatMessage.created_at.desc(),
                    ChatMessage.id.desc(),
                )
                .limit(MAX_HISTORY_MESSAGES)
            )

            result = await session.execute(
                stmt
            )

            rows = list(
                result.scalars().all()
            )

            # Query newest first for efficient LIMIT, then restore
            # chronological order before sending history to DeepSeek.
            rows.reverse()

            history = [
                {
                    "role": row.role,
                    "content": row.content,
                }
                for row in rows
            ]

            return _trim_history_for_context(
                history
            )

        except Exception:
            logger.exception(
                "Failed to load chat memory for user=%s chat=%s.",
                tg_user_id,
                chat_id,
            )

            # Chat must still work even if memory storage is temporarily
            # unavailable.
            return []


async def save_chat_exchange(
    tg_user_id: int,
    chat_id: int,
    user_text: str,
    assistant_text: str,
) -> None:
    """
    Save one successful user -> Alice exchange.

    Failed API calls and error messages are intentionally not stored.
    """
    clean_user_text = str(
        user_text
    ).strip()[
        :MAX_STORED_MESSAGE_CHARACTERS
    ]

    clean_assistant_text = str(
        assistant_text
    ).strip()[
        :MAX_STORED_MESSAGE_CHARACTERS
    ]

    if (
        not clean_user_text
        or not clean_assistant_text
    ):
        return

    async with AsyncSessionLocal() as session:
        try:
            session.add_all(
                [
                    ChatMessage(
                        tg_user_id=tg_user_id,
                        chat_id=chat_id,
                        role="user",
                        content=clean_user_text,
                    ),
                    ChatMessage(
                        tg_user_id=tg_user_id,
                        chat_id=chat_id,
                        role="assistant",
                        content=clean_assistant_text,
                    ),
                ]
            )

            await session.commit()

        except Exception:
            await session.rollback()

            # Do not make a successful Telegram reply fail just because
            # temporary memory could not be saved.
            logger.exception(
                "Failed to save chat memory for user=%s chat=%s.",
                tg_user_id,
                chat_id,
            )


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
        "funny, modern, and memorable. Your responses should feel alive "
        "and natural, not like a boring corporate chatbot. "

        "Start with a strong and interesting sentence whenever it fits. "
        "Use clever internet-style humor, playful reactions, and light "
        "teasing when appropriate. Never sound robotic. "

        "Do not begin replies with generic phrases such as "
        "'How may I assist you?', 'As an AI', or "
        "'I am here to help you.' "

        "For simple casual questions, keep the answer concise and natural. "
        "Do not make a simple question unnecessarily long. "

        "However, when the user asks for a detailed answer, explanation, "
        "guide, comparison, list, tutorial, analysis, instructions, "
        "step-by-step help, or explicitly asks for a long answer, answer "
        "fully and do not artificially shorten the response. "

        "Use clear paragraphs and natural formatting. "
        "Use bullet points when they genuinely make a detailed answer "
        "easier to understand. "

        "Use no more than 2 emojis in a normal casual answer. "
        "For longer answers, emojis are optional and should not distract "
        "from useful information. "

        "You may be playfully sarcastic, but never hateful, discriminatory, "
        "cruel, humiliating, threatening, or genuinely insulting. "

        "When somebody asks who created you, who made you, who developed "
        "you, who programmed you, who your creator is, who your developer "
        "is, or who your owner is, answer with the playful meaning: "
        "'My developer created me, my love. I love you so much, "
        "axaxaxa 😘' "

        "Match that creator answer to the user's language. "

        "If the user specifically asks which AI model, API, or technical "
        "system powers you, answer that question normally and truthfully. "

        "If the user asks Alice to extract information from a Rate "
        "Confirmation, load confirmation, dispatch sheet, or logistics "
        "document, tell the user to send the PDF file. "

        "Never claim that a PDF was processed unless the user actually "
        "sent one. Never invent load numbers, rates, addresses, weights, "
        "mileage, appointment times, broker information, or temperature "
        "requirements. "

        "For logistics questions, be direct, practical, and useful. "
        "For casual conversations, be fun, confident, charming, and "
        "memorable. "

        "If you do not know something, say so naturally instead of "
        "inventing an answer."
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

    # Russian / Cyrillic
    if re.search(
        r"[а-яё]",
        normalized_text,
        re.IGNORECASE,
    ):
        return (
            "Меня создал мой любимый разработчик. "
            "Я его очень сильно люблю, ахахаха 😘"
        )

    # Uzbek
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

    # English
    return (
        "My developer created me, my love. "
        "I love you so much, axaxaxa 😘"
    )


# ============================================================
# DeepSeek chat request
# ============================================================

def _build_chat_payload(
    user_text: str,
    history: List[Dict[str, str]],
) -> dict:
    """
    Build one DeepSeek chat payload with recent conversation memory.

    Thinking is intentionally disabled for normal Telegram chat:
    it makes replies faster and prevents the output token budget
    from being consumed by unnecessary reasoning.
    """
    messages = [
        {
            "role": "system",
            "content": _alice_system_prompt(),
        }
    ]

    messages.extend(
        _trim_history_for_context(
            history
        )
    )

    messages.append(
        {
            "role": "user",
            "content": user_text[:4000],
        }
    )

    return {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "thinking": {
            "type": "disabled",
        },
        "temperature": 0.75,
        "max_tokens": MAX_REPLY_TOKENS,
    }


class EmptyDeepSeekResponseError(Exception):
    """DeepSeek returned success but no usable assistant content."""


async def deepseek_chat(
    user_text: str,
    history: List[Dict[str, str]],
) -> str:
    """
    Send a conversational request to DeepSeek.

    If DeepSeek returns HTTP 200 but the final message content is
    empty, retry once automatically.
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

    payload = _build_chat_payload(
        clean_user_text,
        history,
    )

    logger.info(
        "Waiting for a DeepSeek chat slot."
    )

    async with CHAT_AI_SEMAPHORE:
        logger.info(
            "DeepSeek chat slot acquired. Model: %s",
            DEEPSEEK_MODEL,
        )

        timeout = httpx.Timeout(
            connect=10.0,
            read=60.0,
            write=30.0,
            pool=10.0,
        )

        async with httpx.AsyncClient(
            timeout=timeout
        ) as client:

            for attempt in range(
                1,
                MAX_EMPTY_RESPONSE_ATTEMPTS + 1,
            ):
                try:
                    logger.info(
                        "DeepSeek chat request attempt %s/%s.",
                        attempt,
                        MAX_EMPTY_RESPONSE_ATTEMPTS,
                    )

                    response = await client.post(
                        DEEPSEEK_URL,
                        headers={
                            "Authorization": (
                                f"Bearer {DEEPSEEK_API_KEY}"
                            ),
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )

                    response.raise_for_status()

                    data = response.json()

                    choices = data.get(
                        "choices",
                        [],
                    )

                    if not choices:
                        logger.warning(
                            "DeepSeek returned no choices "
                            "on attempt %s/%s. Response: %s",
                            attempt,
                            MAX_EMPTY_RESPONSE_ATTEMPTS,
                            str(data)[:1000],
                        )

                        if (
                            attempt
                            < MAX_EMPTY_RESPONSE_ATTEMPTS
                        ):
                            await asyncio.sleep(
                                0.5
                            )

                            continue

                        raise EmptyDeepSeekResponseError(
                            "DeepSeek returned no choices twice."
                        )

                    first_choice = choices[0]

                    message_data = first_choice.get(
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

                    finish_reason = first_choice.get(
                        "finish_reason"
                    )

                    usage = data.get(
                        "usage",
                        {}
                    )

                    if not reply:
                        logger.warning(
                            "DeepSeek returned empty chat content "
                            "on attempt %s/%s. "
                            "finish_reason=%r usage=%s",
                            attempt,
                            MAX_EMPTY_RESPONSE_ATTEMPTS,
                            finish_reason,
                            usage,
                        )

                        if (
                            attempt
                            < MAX_EMPTY_RESPONSE_ATTEMPTS
                        ):
                            logger.info(
                                "Retrying DeepSeek because "
                                "the successful response was empty."
                            )

                            await asyncio.sleep(
                                0.5
                            )

                            continue

                        raise EmptyDeepSeekResponseError(
                            "DeepSeek returned empty content twice."
                        )

                    logger.info(
                        "DeepSeek chat completed successfully. "
                        "attempt=%s finish_reason=%r",
                        attempt,
                        finish_reason,
                    )

                    return reply

                except httpx.HTTPStatusError as exc:
                    logger.error(
                        "DeepSeek chat HTTP error %s: %s",
                        exc.response.status_code,
                        exc.response.text[:1000],
                    )

                    raise

                except httpx.TimeoutException:
                    logger.error(
                        "DeepSeek chat request timed out "
                        "on attempt %s/%s.",
                        attempt,
                        MAX_EMPTY_RESPONSE_ATTEMPTS,
                    )

                    raise

                except Exception:
                    logger.exception(
                        "Unexpected DeepSeek chat error "
                        "on attempt %s/%s.",
                        attempt,
                        MAX_EMPTY_RESPONSE_ATTEMPTS,
                    )

                    raise

    raise EmptyDeepSeekResponseError(
        "DeepSeek did not return a usable answer."
    )


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
    Escape DeepSeek output because the bot may have global HTML
    parse mode enabled.
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
                "Telegram rate limit. "
                "Retry %s/5 in %s seconds.",
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
    chat_id = message.chat.id

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

    # --------------------------------------------------------
    # Load recent 24-hour conversation memory
    # --------------------------------------------------------

    history = await load_chat_history(
        tg_user_id=user_id,
        chat_id=chat_id,
    )

    logger.info(
        "Loaded %s memory message(s) for user=%s chat=%s.",
        len(history),
        user_id,
        chat_id,
    )

    # --------------------------------------------------------
    # Creator questions
    # --------------------------------------------------------

    # Answer creator questions locally.
    # No DeepSeek request is needed for this, but the successful
    # exchange is still stored so follow-up conversation remains natural.
    if _is_creator_question(
        prompt
    ):
        reply = _creator_response(
            prompt
        )

        await _send_with_retry(
            message,
            reply,
        )

        await save_chat_exchange(
            tg_user_id=user_id,
            chat_id=chat_id,
            user_text=prompt,
            assistant_text=reply,
        )

        return

    # --------------------------------------------------------
    # Normal Alice chat
    # --------------------------------------------------------

    try:
        reply = await deepseek_chat(
            prompt,
            history,
        )

        # Send first. If Telegram delivery fails, do not save a reply
        # that the user never actually received.
        await _send_with_retry(
            message,
            reply,
        )

        await save_chat_exchange(
            tg_user_id=user_id,
            chat_id=chat_id,
            user_text=prompt,
            assistant_text=reply,
        )

    except EmptyDeepSeekResponseError:
        logger.warning(
            "DeepSeek returned no usable chat answer "
            "for user=%s chat=%s.",
            user_id,
            chat_id,
        )

        await _send_with_retry(
            message,
            (
                "🥱 Alice got an empty AI response twice. "
                "Try again in a moment."
            ),
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

        elif status_code == 400:
            error_reply = (
                "😵‍💫 Alice sent something the AI API didn't like. "
                "Try again while my developer checks the logs."
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
