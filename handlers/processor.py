import os
import tempfile
import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, Callable, Awaitable, Optional

from aiogram import Router, types, F, Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from sqlalchemy import select

from config import ADMIN_IDS, MAX_CONCURRENT_JOBS
from database.connection import AsyncSessionLocal
from database.models import User
from services.pdf_engine import extract_text_async
from services.extractor import smart_extract
from services.renderer import render_result


logger = logging.getLogger("LazyAlice.Processor")
router = Router()


# ============================================================
# In-memory queues
# ============================================================

# Each user gets a separate queue.
# This keeps multiple PDFs from the same user in the correct order.
user_queues: Dict[int, asyncio.Queue] = {}

# One worker task per user.
user_workers: Dict[int, asyncio.Task] = {}

# Tracks how many files arrived in one Telegram media group.
media_group_tracker: Dict[str, int] = {}

# Prevents creating several cleanup tasks for the same media group.
media_group_cleanup_tasks: Dict[str, asyncio.Task] = {}

MEDIA_GROUP_LIMIT = 5


# ============================================================
# Concurrency controls
# ============================================================

# Several different users can now process PDFs simultaneously.
#
# Example:
# MAX_CONCURRENT_JOBS=3
#
# User A: PDF processing
# User B: PDF processing
# User C: PDF processing
# User D: waits until one processing slot becomes available
GLOBAL_JOB_SEM = asyncio.Semaphore(MAX_CONCURRENT_JOBS)

# Telegram messages are still sent sequentially to reduce flood-limit errors.
TG_SEND_SEM = asyncio.Semaphore(1)


# ============================================================
# Telegram safe calls
# ============================================================

async def tg_call_with_retry(
    factory: Callable[[], Awaitable[Any]],
    *,
    max_retries: int = 6,
) -> Any:
    """
    Execute a Telegram API request with retry handling.

    TelegramRetryAfter is respected using Telegram's retry_after value.
    Other temporary errors are retried with a short delay.
    """
    last_exc: Optional[Exception] = None

    for attempt in range(max_retries):
        try:
            async with TG_SEND_SEM:
                return await factory()

        except TelegramRetryAfter as exc:
            last_exc = exc

            retry_after = int(
                getattr(exc, "retry_after", 1)
            )

            logger.warning(
                "Telegram flood limit. Waiting %s seconds.",
                retry_after,
            )

            await asyncio.sleep(retry_after + 1)

        except Exception as exc:
            last_exc = exc

            logger.warning(
                "Telegram request failed on attempt %s/%s: %s",
                attempt + 1,
                max_retries,
                exc,
            )

            await asyncio.sleep(1)

    if last_exc:
        raise last_exc

    return await factory()


async def safe_send(bot: Bot, **kwargs):
    return await tg_call_with_retry(
        lambda: bot.send_message(**kwargs)
    )


async def safe_edit(
    bot: Bot,
    chat_id: int,
    message_id: int,
    text: str,
):
    async def _call():
        return await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode=ParseMode.HTML,
        )

    try:
        return await tg_call_with_retry(_call)

    except TelegramBadRequest as exc:
        error_text = str(exc).lower()

        if "message is not modified" in error_text:
            return None

        logger.warning(
            "Could not edit Telegram message %s: %s",
            message_id,
            exc,
        )

        return None

    except Exception as exc:
        logger.warning(
            "Could not edit Telegram message %s: %s",
            message_id,
            exc,
        )

        return None


async def safe_delete(
    bot: Bot,
    chat_id: int,
    message_id: int,
):
    try:
        return await tg_call_with_retry(
            lambda: bot.delete_message(
                chat_id=chat_id,
                message_id=message_id,
            )
        )

    except Exception:
        return None


# ============================================================
# Subscription access
# ============================================================

async def check_is_paid_user(uid: int) -> bool:
    """
    Allow access only to administrators or users with an active
    Pro subscription.
    """
    # Administrators should not depend on having a database record.
    if uid in ADMIN_IDS:
        return True

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.tg_id == uid)
        )

        user = result.scalar_one_or_none()

        if not user:
            return False

        if not user.is_pro or not user.expiry_date:
            return False

        expiry = user.expiry_date

        # Database drivers may return timezone-aware datetimes.
        # The current project stores subscription times as naive UTC.
        if getattr(expiry, "tzinfo", None):
            expiry = expiry.replace(tzinfo=None)

        return expiry > datetime.utcnow()


# ============================================================
# User queue worker
# ============================================================

async def process_user_queue(
    uid: int,
    bot: Bot,
):
    """
    Process one user's PDFs sequentially.

    Different users may run simultaneously through GLOBAL_JOB_SEM.
    """
    queue = user_queues.get(uid)

    if queue is None:
        return

    logger.info("Worker started for user %s", uid)

    try:
        while True:
            try:
                item = await asyncio.wait_for(
                    queue.get(),
                    timeout=10,
                )

            except asyncio.TimeoutError:
                if queue.empty():
                    break

                continue

            chat_id: int = item["chat_id"]
            file_id: str = item["file_id"]
            status_id: int = item["status_msg_id"]
            reply_id: int = item["reply_to_id"]

            tmp_path: Optional[str] = None

            try:
                # Show a waiting status only when every processing
                # slot is currently occupied.
                if GLOBAL_JOB_SEM.locked():
                    await safe_edit(
                        bot,
                        chat_id,
                        status_id,
                        "⏳ <b>Waiting for a processing slot...</b>",
                    )

                # This semaphore allows several users to process
                # PDFs together while respecting the configured limit.
                async with GLOBAL_JOB_SEM:
                    logger.info(
                        "Processing started for user %s. "
                        "Available job slots after acquisition: %s",
                        uid,
                        GLOBAL_JOB_SEM._value,
                    )

                    await safe_edit(
                        bot,
                        chat_id,
                        status_id,
                        "📄 <b>Downloading PDF...</b>",
                    )

                    telegram_file = await bot.get_file(file_id)

                    downloaded_file = await bot.download_file(
                        telegram_file.file_path
                    )

                    with tempfile.NamedTemporaryFile(
                        delete=False,
                        suffix=".pdf",
                    ) as tmp:
                        tmp.write(downloaded_file.read())
                        tmp_path = tmp.name

                    await safe_edit(
                        bot,
                        chat_id,
                        status_id,
                        "🔍 <b>Extracting PDF text...</b>",
                    )

                    text = await extract_text_async(tmp_path)

                    if not text or not text.strip():
                        raise ValueError(
                            "No readable text was found in this PDF."
                        )

                    await safe_edit(
                        bot,
                        chat_id,
                        status_id,
                        "🧠 <b>Analyzing load information...</b>",
                    )

                    data = await smart_extract(text)

                    async with AsyncSessionLocal() as session:
                        result = await session.execute(
                            select(User).where(
                                User.tg_id == uid
                            )
                        )

                        user = result.scalar_one_or_none()

                        template = (
                            user.template_text
                            if user
                            else None
                        )

                    formatted = render_result(
                        data,
                        template,
                    )

                    await safe_edit(
                        bot,
                        chat_id,
                        status_id,
                        "📤 <b>Sending result...</b>",
                    )

                    await safe_send(
                        bot,
                        chat_id=chat_id,
                        text=formatted,
                        reply_to_message_id=reply_id,
                        parse_mode=ParseMode.HTML,
                    )

                    logger.info(
                        "Processing completed for user %s",
                        uid,
                    )

            except asyncio.CancelledError:
                logger.info(
                    "Worker was cancelled for user %s",
                    uid,
                )
                raise

            except Exception as exc:
                logger.exception(
                    "PDF processing error for user %s",
                    uid,
                )

                try:
                    await safe_send(
                        bot,
                        chat_id=chat_id,
                        text=(
                            "⚠️ <b>Processing error</b>\n\n"
                            f"<code>{str(exc)}</code>"
                        ),
                        reply_to_message_id=reply_id,
                        parse_mode=ParseMode.HTML,
                    )

                except Exception:
                    logger.exception(
                        "Could not send error message to user %s",
                        uid,
                    )

            finally:
                await safe_delete(
                    bot,
                    chat_id,
                    status_id,
                )

                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)

                    except Exception as exc:
                        logger.warning(
                            "Could not remove temporary PDF %s: %s",
                            tmp_path,
                            exc,
                        )

                queue.task_done()

    finally:
        user_workers.pop(uid, None)

        # Remove empty queues so memory does not grow indefinitely.
        current_queue = user_queues.get(uid)

        if current_queue is not None and current_queue.empty():
            user_queues.pop(uid, None)

        logger.info("Worker stopped for user %s", uid)


# ============================================================
# PDF message handler
# ============================================================

@router.message(F.document.mime_type == "application/pdf")
async def handle_pdf(
    message: types.Message,
    bot: Bot,
):
    if not message.from_user or not message.document:
        return

    uid = message.from_user.id
    media_group_id = message.media_group_id

    is_allowed = await check_is_paid_user(uid)

    if not is_allowed:
        return await message.answer(
            "🔒 <b>Premium Access Only</b>\n\n"
            "Alice has entered <b>Fully Paid Mode</b>. "
            "Free trials are no longer available.\n\n"
            "To process Rate Confirmations, please subscribe "
            "to a Pro plan. 💅\n\n"
            "Use /plans to get instant access.",
            parse_mode=ParseMode.HTML,
        )

    # Limit the number of PDFs sent inside one Telegram album.
    if media_group_id:
        media_group_tracker[media_group_id] = (
            media_group_tracker.get(media_group_id, 0) + 1
        )

        current_count = media_group_tracker[media_group_id]

        if current_count > MEDIA_GROUP_LIMIT:
            if current_count == MEDIA_GROUP_LIMIT + 1:
                await message.reply(
                    "💅 Maximum 5 PDFs can be sent at once. "
                    "The remaining files were ignored.",
                    parse_mode=ParseMode.HTML,
                )

            return

        # Create only one cleanup task per media group.
        if media_group_id not in media_group_cleanup_tasks:
            media_group_cleanup_tasks[media_group_id] = (
                asyncio.create_task(
                    _cleanup_media_group(media_group_id)
                )
            )

    if uid not in user_queues:
        user_queues[uid] = asyncio.Queue()

    queue = user_queues[uid]

    # qsize() does not include the item currently being processed,
    # so add one when a worker is already active.
    waiting_count = queue.qsize()

    worker_is_active = (
        uid in user_workers
        and not user_workers[uid].done()
    )

    queue_position = waiting_count + (
        1 if worker_is_active else 0
    )

    status_text = "👀 <b>Queued — Pro Access</b>"

    if queue_position > 0:
        status_text += (
            f"\n📥 <i>Queue position: "
            f"{queue_position + 1}</i>"
        )

    status_message = await message.reply(
        status_text,
        parse_mode=ParseMode.HTML,
    )

    await queue.put(
        {
            "chat_id": message.chat.id,
            "file_id": message.document.file_id,
            "status_msg_id": status_message.message_id,
            "reply_to_id": message.message_id,
        }
    )

    if (
        uid not in user_workers
        or user_workers[uid].done()
    ):
        user_workers[uid] = asyncio.create_task(
            process_user_queue(uid, bot)
        )


# ============================================================
# Media group cleanup
# ============================================================

async def _cleanup_media_group(
    media_group_id: str,
):
    try:
        await asyncio.sleep(120)

    finally:
        media_group_tracker.pop(
            media_group_id,
            None,
        )

        media_group_cleanup_tasks.pop(
            media_group_id,
            None,
        )
