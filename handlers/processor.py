import os
import tempfile
import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Dict, Any, Callable, Awaitable, Optional

from aiogram import Router, types, F, Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from sqlalchemy import select

from config import ADMIN_IDS
from database.connection import AsyncSessionLocal
from database.models import User
from services.pdf_engine import extract_text_async
from services.extractor import smart_extract
from services.renderer import render_result


logger = logging.getLogger("LazyAlice.Processor")
router = Router()

# ================= GLOBALS =================
user_queues: Dict[int, asyncio.Queue] = {}
user_workers: Dict[int, asyncio.Task] = {}
media_group_tracker: Dict[str, int] = {}

MEDIA_GROUP_LIMIT = 5
FREE_WEEKLY_LIMIT = 5  # 💅 Lowered from 10/day to 5/week to boost sales

GLOBAL_PROCESS_SEM = asyncio.Semaphore(2)
TG_SEND_SEM = asyncio.Semaphore(1)


# ================= TELEGRAM SAFE CALL =================
async def tg_call_with_retry(
    factory: Callable[[], Awaitable[Any]],
    *,
    max_retries: int = 6,
) -> Any:
    last_exc: Optional[Exception] = None

    for _ in range(max_retries):
        try:
            async with TG_SEND_SEM:
                return await factory()
        except TelegramRetryAfter as e:
            last_exc = e
            await asyncio.sleep(int(getattr(e, "retry_after", 1)) + 1)
        except Exception as e:
            last_exc = e
            await asyncio.sleep(1)

    if last_exc:
        raise last_exc
    return await factory()


async def safe_send(bot: Bot, **kwargs):
    return await tg_call_with_retry(lambda: bot.send_message(**kwargs))


async def safe_edit(bot: Bot, chat_id: int, message_id: int, text: str):
    async def _call():
        return await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode=ParseMode.HTML,
        )

    try:
        return await tg_call_with_retry(_call)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            return None
        return None


async def safe_delete(bot: Bot, chat_id: int, message_id: int):
    try:
        return await tg_call_with_retry(
            lambda: bot.delete_message(chat_id=chat_id, message_id=message_id)
        )
    except Exception:
        return None


# ================= LIMIT CHECK (WEEKLY LOGIC) =================
async def check_and_update_limit(uid: int) -> tuple[bool, int]:
    """
    Returns: (allowed, left)
    Uses a 7-day rolling window for free users.
    """
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(User).where(User.tg_id == uid))
        user = res.scalar_one_or_none()
        if not user:
            return False, 0

        is_admin = uid in ADMIN_IDS
        now = datetime.utcnow()

        # 1. Pro Check
        is_pro_active = False
        if user.is_pro:
            expiry = user.expiry_date
            if expiry is None:
                is_pro_active = True
            else:
                if getattr(expiry, "tzinfo", None):
                    expiry = expiry.replace(tzinfo=None)
                is_pro_active = expiry > now

        if is_admin or is_pro_active:
            return True, 999

        # 2. Weekly Reset Logic ⏱️
        today = date.today()
        # If it has been 7 or more days since the start of their last cycle, reset
        if (today - user.last_request_date).days >= 7:
            user.weekly_requests = 0
            user.last_request_date = today
            await session.commit()

        # 3. Limit Check
        if user.weekly_requests >= FREE_WEEKLY_LIMIT:
            return False, 0

        # 4. Increment usage
        user.weekly_requests += 1
        await session.commit()
        
        left = max(0, FREE_WEEKLY_LIMIT - user.weekly_requests)
        return True, left


# ================= WORKER =================
async def process_user_queue(uid: int, bot: Bot):
    q = user_queues.get(uid)
    if not q:
        return

    logger.info("🚀 Worker started for %s", uid)

    try:
        while True:
            try:
                item = await asyncio.wait_for(q.get(), timeout=10)
            except asyncio.TimeoutError:
                if q.empty():
                    break
                continue

            chat_id: int = item["chat_id"]
            file_id: str = item["file_id"]
            status_id: int = item["status_msg_id"]
            reply_id: int = item["reply_to_id"]
            tmp_path: Optional[str] = None

            try:
                await safe_edit(bot, chat_id, status_id, "📄 <b>Downloading...</b>")

                tg_file = await bot.get_file(file_id)
                raw = await bot.download_file(tg_file.file_path)

                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    tmp.write(raw.read())
                    tmp_path = tmp.name

                async with GLOBAL_PROCESS_SEM:
                    await safe_edit(bot, chat_id, status_id, "🔍 <b>Extracting...</b>")
                    text = await extract_text_async(tmp_path)

                    await safe_edit(bot, chat_id, status_id, "🧠 <b>Analyzing...</b>")
                    data = await smart_extract(text)

                async with AsyncSessionLocal() as session:
                    res = await session.execute(select(User).where(User.tg_id == uid))
                    user = res.scalar_one_or_none()
                    template = user.template_text if user else None

                formatted = render_result(data, template)

                await safe_send(
                    bot,
                    chat_id=chat_id,
                    text=formatted,
                    reply_to_message_id=reply_id,
                    parse_mode=ParseMode.HTML,
                )

            except Exception as e:
                logger.exception("Worker error for %s", uid)
                try:
                    await safe_send(
                        bot,
                        chat_id=chat_id,
                        text=f"⚠️ <b>Error:</b>\n<code>{str(e)}</code>",
                        reply_to_message_id=reply_id,
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    pass

            finally:
                await safe_delete(bot, chat_id, status_id)

                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass

                q.task_done()

    finally:
        user_workers.pop(uid, None)
        logger.info("💤 Worker stopped for %s", uid)


# ================= PDF HANDLER =================
@router.message(F.document.mime_type == "application/pdf")
async def handle_pdf(message: types.Message, bot: Bot):
    uid = message.from_user.id
    mg_id = message.media_group_id

    allowed, left = await check_and_update_limit(uid)
    if not allowed:
        # 💅 Sassy business-focused rejection
        return await message.answer(
            "💸 <b>Weekly limit reached!</b>\n\n"
            "You only get 5 free RCs per week. "
            "Upgrade to <b>Pro</b> for unlimited access and beat the competition. 🥱💅\n\n"
            "Use /plans to see details.", 
            parse_mode=ParseMode.HTML
        )

    # Limit “send many at once” in media groups
    if mg_id:
        media_group_tracker[mg_id] = media_group_tracker.get(mg_id, 0) + 1
        if media_group_tracker[mg_id] > MEDIA_GROUP_LIMIT:
            if media_group_tracker[mg_id] == MEDIA_GROUP_LIMIT + 1:
                await message.reply("💅 Max 5 PDFs at once. Ignoring the rest.", parse_mode=ParseMode.HTML)
            return
        asyncio.create_task(_cleanup_media_group(mg_id))

    user_queues.setdefault(uid, asyncio.Queue())
    pos = user_queues[uid].qsize()

    left_text = "Unlimited" if left == 999 else f"{left} left this week"
    status_text = f"👀 <b>Queued</b> ({left_text})"
    if pos > 0:
        status_text += f"\n📥 <i>Queue position: {pos + 1}</i>"

    status_msg = await message.reply(status_text, parse_mode=ParseMode.HTML)

    await user_queues[uid].put({
        "chat_id": message.chat.id,
        "file_id": message.document.file_id,
        "status_msg_id": status_msg.message_id,
        "reply_to_id": message.message_id,
    })

    if uid not in user_workers or user_workers[uid].done():
        user_workers[uid] = asyncio.create_task(process_user_queue(uid, bot))


async def _cleanup_media_group(mg_id: str):
    await asyncio.sleep(120)
    media_group_tracker.pop(mg_id, None)