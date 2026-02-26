import os
import tempfile
import asyncio
import random
import logging
from datetime import date, datetime
from typing import Dict, Any, Callable, Awaitable, Optional

from aiogram import Router, types, F, Bot
from aiogram.enums import ParseMode
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

# Limit heavy work globally (OCR + AI) to avoid CPU/RAM spikes + Telegram floods
GLOBAL_PROCESS_SEM = asyncio.Semaphore(2)

# Keep Telegram output under control (send/edit/delete)
TG_SEND_SEM = asyncio.Semaphore(1)


# ================= TELEGRAM CALL HELPERS =================
async def tg_call_with_retry(
    call_factory: Callable[[], Awaitable[Any]],
    *,
    max_retries: int = 5,
    tag: str = "tg_call"
) -> Any:
    """
    Retries Telegram API calls when Flood Control triggers (TelegramRetryAfter).
    This prevents worker crashes like in your logs. :contentReference[oaicite:0]{index=0}
    """
    last_exc: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            async with TG_SEND_SEM:
                return await call_factory()
        except TelegramRetryAfter as e:
            last_exc = e
            sleep_s = max(1, int(getattr(e, "retry_after", 1))) + 1
            logger.warning("⏳ Flood control in %s. Retry in %ss (attempt %s/%s)", tag, sleep_s, attempt, max_retries)
            await asyncio.sleep(sleep_s)
        except Exception as e:
            last_exc = e
            if attempt >= max_retries:
                raise
            logger.warning("⚠️ Telegram error in %s: %s (attempt %s/%s)", tag, e, attempt, max_retries)
            await asyncio.sleep(1)

    if last_exc:
        raise last_exc
    return None


async def safe_edit_status(bot: Bot, chat_id: int, message_id: int, new_text: str):
    """
    Safe edit with retry-after handling + ignores harmless 'message is not modified'.
    """
    async def _call():
        return await bot.edit_message_text(
            text=new_text,
            chat_id=chat_id,
            message_id=message_id,
            parse_mode=ParseMode.HTML,
        )

    try:
        return await tg_call_with_retry(_call, tag="edit_message_text")
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            return None
    except Exception as e:
        logger.debug("safe_edit_status failed: %s", e)
    return None


async def safe_delete_message(bot: Bot, chat_id: int, message_id: int):
    async def _call():
        return await bot.delete_message(chat_id=chat_id, message_id=message_id)

    try:
        return await tg_call_with_retry(_call, tag="delete_message")
    except Exception:
        return None


async def safe_send_message(bot: Bot, **kwargs):
    async def _call():
        return await bot.send_message(**kwargs)

    return await tg_call_with_retry(_call, tag="send_message")


# ================= LIMIT CHECK =================
async def check_and_update_limit(uid: int) -> tuple[bool, int]:
    """
    Returns:
      allowed: bool
      left: int  (999 for unlimited)
    """
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(User).where(User.tg_id == uid))
        user = res.scalar_one_or_none()

        if not user:
            return False, 0

        is_admin = uid in ADMIN_IDS
        now = datetime.utcnow()

        # Determine if Pro is currently active
        is_pro_active = False
        if user.is_pro:
            expiry = user.expiry_date
            if expiry is None:
                is_pro_active = True
            else:
                if getattr(expiry, "tzinfo", None) is not None:
                    expiry = expiry.replace(tzinfo=None)
                is_pro_active = expiry > now

        # Unlimited users
        if is_admin or is_pro_active:
            return True, 999

        # Free user logic
        today = date.today()
        if user.last_request_date != today:
            user.daily_requests = 0
            user.last_request_date = today
            await session.commit()
            await session.refresh(user)

        if user.daily_requests >= FREE_DAILY_LIMIT:
            return False, 0

        user.daily_requests += 1
        await session.commit()
        return True, max(0, FREE_DAILY_LIMIT - user.daily_requests)


# ================= WORKER =================
async def process_user_queue(uid: int, bot: Bot):
    """
    Processes this user's queue sequentially.
    Worker stops after being idle for a bit (and queue is still empty).
    """
    q = user_queues.get(uid)
    if not q:
        return

    logger.info("🚀 Worker started for user %s", uid)

    try:
        while True:
            try:
                item = await asyncio.wait_for(q.get(), timeout=10)
            except asyncio.TimeoutError:
                if q.empty():
                    logger.info("🛑 Worker idle timeout for user %s", uid)
                    break
                continue

            chat_id: int = item["chat_id"]
            file_id: str = item["file_id"]
            status_msg_id: int = item["status_msg_id"]
            reply_to_id: int = item["reply_to_id"]

            tmp_path = None

            try:
                # Keep progress updates minimal to avoid flood limits (your logs show RetryAfter) :contentReference[oaicite:1]{index=1}
                await safe_edit_status(bot, chat_id, status_msg_id, "📄 <b>Downloading...</b>")

                tg_file = await bot.get_file(file_id)
                raw = await bot.download_file(tg_file.file_path)

                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    tmp.write(raw.read())
                    tmp_path = tmp.name

                await safe_edit_status(bot, chat_id, status_msg_id, "🔍 <b>Extracting text...</b>")

                # Global semaphore avoids CPU/RAM spikes when user sends many PDFs
                async with GLOBAL_PROCESS_SEM:
                    text = await extract_text_async(tmp_path)
                    await safe_edit_status(bot, chat_id, status_msg_id, "🧠 <b>Analyzing...</b>")
                    data = await smart_extract(text)

                # Fetch user's custom template (if any)
                async with AsyncSessionLocal() as session:
                    res = await session.execute(select(User).where(User.tg_id == uid))
                    user = res.scalar_one_or_none()
                    template = user.template_text if user else None

                formatted_output = render_result(data, template)

                await safe_send_message(
                    bot,
                    chat_id=chat_id,
                    text=formatted_output,
                    reply_to_message_id=reply_to_id,
                    parse_mode=ParseMode.HTML,
                )

            except Exception as e:
                logger.exception("Worker Error for %s", uid)
                try:
                    await safe_send_message(
                        bot,
                        chat_id=chat_id,
                        text=f"🙄 <b>Error:</b>\n<code>{str(e)}</code>",
                        reply_to_message_id=reply_to_id,
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    pass

            finally:
                # Remove progress message
                await safe_delete_message(bot, chat_id, status_msg_id)

                # Cleanup temp file
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass

                q.task_done()

    finally:
        logger.info("💤 Worker stopped for user %s", uid)
        user_workers.pop(uid, None)


# ================= PDF HANDLER =================
@router.message(F.document.mime_type == "application/pdf")
async def handle_pdf(message: types.Message, bot: Bot):
    uid = message.from_user.id
    mg_id = message.media_group_id

    # 1) Limit check
    allowed, left = await check_and_update_limit(uid)
    if not allowed:
        return await message.answer(
            "💸 <b>Daily Limit Reached!</b>\n\nUpgrade to /plans now. 💅",
            parse_mode=ParseMode.HTML,
        )

    # 2) Media group limit + cleanup
    if mg_id:
        media_group_tracker[mg_id] = media_group_tracker.get(mg_id, 0) + 1

        if media_group_tracker[mg_id] > MEDIA_GROUP_LIMIT:
            if media_group_tracker[mg_id] == MEDIA_GROUP_LIMIT + 1:
                await message.reply(
                    "💅 <b>Honey, stop!</b> My limit is 5 PDFs. Ignoring the rest.",
                    parse_mode=ParseMode.HTML,
                )
            return

        asyncio.create_task(_cleanup_media_group(mg_id))

    # 3) Ensure queue exists
    user_queues.setdefault(uid, asyncio.Queue())
    q_pos = user_queues[uid].qsize()

    left_text = "Unlimited" if left == 999 else f"{left} left today"

    status_text = f"👀 <b>I woke up...</b> ({left_text})"
    if q_pos > 0:
        status_text += f"\n📥 <i>Position in queue: {q_pos + 1}</i>"

    initial_msg = await message.reply(status_text, parse_mode=ParseMode.HTML)

    await user_queues[uid].put(
        {
            "chat_id": message.chat.id,
            "file_id": message.document.file_id,
            "status_msg_id": initial_msg.message_id,
            "reply_to_id": message.message_id,
        }
    )

    logger.info("📥 User %s queue size: %s", uid, user_queues[uid].qsize())

    # 4) Start worker if needed
    if uid not in user_workers or user_workers[uid].done():
        user_workers[uid] = asyncio.create_task(process_user_queue(uid, bot))


async def _cleanup_media_group(mg_id: str):
    await asyncio.sleep(120)
    media_group_tracker.pop(mg_id, None)


# ================= ADMIN =================
@router.message(Command("check_user"))
async def admin_check_user(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    args = message.text.split()
    if len(args) < 2:
        return await message.answer("Usage: <code>/check_user [tg_id]</code>", parse_mode=ParseMode.HTML)

    try:
        target_id = int(args[1])
    except ValueError:
        return await message.answer("Invalid ID.", parse_mode=ParseMode.HTML)

    async with AsyncSessionLocal() as session:
        res = await session.execute(select(User).where(User.tg_id == target_id))
        user = res.scalar_one_or_none()

    if not user:
        return await message.answer("User not found.", parse_mode=ParseMode.HTML)

    status = "👑 PRO" if user.is_pro else "🆓 FREE"
    info = (
        f"👤 <b>User:</b> <code>{target_id}</code>\n"
        f"📊 <b>Status:</b> {status}\n"
        f"📅 <b>Expiry:</b> {user.expiry_date if user.expiry_date else 'N/A'}\n"
        f"📈 <b>Used:</b> {user.daily_requests}/{FREE_DAILY_LIMIT}"
    )
    await message.answer(info, parse_mode=ParseMode.HTML)


# ================= SASSY CHAT =================
@router.message(F.text & ~F.text.startswith("/"))
async def sassy_chat(message: types.Message, state: FSMContext):
    if await state.get_state() is not None:
        return
    await message.reply(
        random.choice(["🙄 Send a PDF.", "💅 Only PDFs.", "🥱 Send the RC."]),
        parse_mode=ParseMode.HTML,
    )