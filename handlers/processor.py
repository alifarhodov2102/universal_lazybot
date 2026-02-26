import os
import tempfile
import asyncio
import random
import logging
from datetime import date, datetime
from typing import Dict, Any, Callable, Awaitable, Optional

from aiogram import Router, types, F, Bot
from aiogram.enums import ParseMode, ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
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
FREE_DAILY_LIMIT = 10

GLOBAL_PROCESS_SEM = asyncio.Semaphore(2)  # limit heavy OCR/AI
TG_SEND_SEM = asyncio.Semaphore(1)         # limit telegram sends


# ================= TELEGRAM SAFE CALL =================
async def tg_call_with_retry(
    factory: Callable[[], Awaitable[Any]],
    *,
    max_retries: int = 5,
) -> Any:
    for _ in range(max_retries):
        try:
            async with TG_SEND_SEM:
                return await factory()
        except TelegramRetryAfter as e:
            await asyncio.sleep(int(e.retry_after) + 1)
        except Exception:
            await asyncio.sleep(1)
    return await factory()


async def safe_send(bot: Bot, **kwargs):
    return await tg_call_with_retry(lambda: bot.send_message(**kwargs))


async def safe_edit(bot: Bot, chat_id: int, message_id: int, text: str):
    try:
        return await tg_call_with_retry(
            lambda: bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode=ParseMode.HTML,
            )
        )
    except TelegramBadRequest:
        return None


async def safe_delete(bot: Bot, chat_id: int, message_id: int):
    try:
        return await tg_call_with_retry(
            lambda: bot.delete_message(chat_id=chat_id, message_id=message_id)
        )
    except Exception:
        return None


# ================= LIMIT CHECK =================
async def check_and_update_limit(uid: int) -> tuple[bool, int]:
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(User).where(User.tg_id == uid))
        user = res.scalar_one_or_none()
        if not user:
            return False, 0

        is_admin = uid in ADMIN_IDS
        now = datetime.utcnow()

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

        today = date.today()
        if user.last_request_date != today:
            user.daily_requests = 0
            user.last_request_date = today
            await session.commit()

        if user.daily_requests >= FREE_DAILY_LIMIT:
            return False, 0

        user.daily_requests += 1
        await session.commit()
        return True, FREE_DAILY_LIMIT - user.daily_requests


# ================= WORKER =================
async def process_user_queue(uid: int, bot: Bot):
    q = user_queues.get(uid)
    if not q:
        return

    logger.info("Worker started for %s", uid)

    try:
        while True:
            try:
                item = await asyncio.wait_for(q.get(), timeout=10)
            except asyncio.TimeoutError:
                if q.empty():
                    break
                continue

            chat_id = item["chat_id"]
            file_id = item["file_id"]
            status_id = item["status_msg_id"]
            reply_id = item["reply_to_id"]

            tmp_path = None

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
                logger.exception("Worker error")
                await safe_send(
                    bot,
                    chat_id=chat_id,
                    text=f"⚠️ <b>Error:</b>\n<code>{str(e)}</code>",
                    reply_to_message_id=reply_id,
                    parse_mode=ParseMode.HTML,
                )

            finally:
                await safe_delete(bot, chat_id, status_id)
                if tmp_path and os.path.exists(tmp_path):
                    os.remove(tmp_path)
                q.task_done()

    finally:
        user_workers.pop(uid, None)
        logger.info("Worker stopped for %s", uid)


# ================= PDF HANDLER =================
@router.message(F.document.mime_type == "application/pdf")
async def handle_pdf(message: types.Message, bot: Bot):
    uid = message.from_user.id
    allowed, left = await check_and_update_limit(uid)

    if not allowed:
        return await message.answer("💸 Daily limit reached. Use /plans.")

    user_queues.setdefault(uid, asyncio.Queue())
    pos = user_queues[uid].qsize()

    msg = await message.reply(
        f"👀 Processing... ({'Unlimited' if left==999 else f'{left} left'})"
        + (f"\n📥 Queue position: {pos+1}" if pos > 0 else ""),
        parse_mode=ParseMode.HTML
    )

    await user_queues[uid].put({
        "chat_id": message.chat.id,
        "file_id": message.document.file_id,
        "status_msg_id": msg.message_id,
        "reply_to_id": message.message_id,
    })

    if uid not in user_workers or user_workers[uid].done():
        user_workers[uid] = asyncio.create_task(process_user_queue(uid, bot))


# ================= SASSY CHAT =================
@router.message(F.text & ~F.text.startswith("/"))
async def sassy_chat(message: types.Message, state: FSMContext, bot: Bot):
    if await state.get_state():
        return

    if message.chat.type == ChatType.PRIVATE:
        return await message.reply("🥱 Send a PDF.", parse_mode=ParseMode.HTML)

    # In groups: only reply if mentioned or replied to
    me = await bot.get_me()
    username = (me.username or "").lower()

    text = (message.text or "").lower()
    mentioned = username and f"@{username}" in text

    replied = (
        message.reply_to_message
        and message.reply_to_message.from_user
        and message.reply_to_message.from_user.id == me.id
    )

    if mentioned or replied:
        return await message.reply("💅 Send a PDF.", parse_mode=ParseMode.HTML)