import os
import tempfile
import asyncio
import random
import logging
from datetime import date, datetime, timezone
from aiogram import Router, types, F, Bot
from sqlalchemy import select
from aiogram.exceptions import TelegramBadRequest
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command

from database.connection import AsyncSessionLocal
from database.models import User
from services.pdf_engine import extract_text_async
from services.extractor import smart_extract
from services.renderer import render_result
from config import ADMIN_IDS

logger = logging.getLogger("LazyAlice.Processor")
router = Router()

# Global trackers for queue management
user_queues: dict[int, asyncio.Queue] = {}
user_workers: dict[int, asyncio.Task] = {}
media_group_tracker: dict[str, int] = {}


# ================= LIMIT CHECK =================
async def check_and_update_limit(uid: int) -> tuple[bool, int]:
    """Alice performs a deep check of the user's status. 💅"""
    async with AsyncSessionLocal() as session:
        session.expire_all()

        stmt = select(User).where(User.tg_id == uid)
        res = await session.execute(stmt)
        user = res.scalar_one_or_none()

        if not user:
            return False, 0

        is_admin = uid in ADMIN_IDS

        # 🔥 timezone-safe current time
        current_time = datetime.now(timezone.utc)

        is_pro_active = False
        if user.is_pro:
            if user.expiry_date is None or user.expiry_date > current_time:
                is_pro_active = True

        logger.info(
            f"User {uid} check: Admin={is_admin}, "
            f"Pro={is_pro_active}, Used={user.daily_requests}"
        )

        # ✅ Unlimited for admin/pro
        if is_admin or is_pro_active:
            return True, 999

        # ===== Free user logic =====
        today = date.today()

        if user.last_request_date < today:
            user.daily_requests = 0
            user.last_request_date = today
            await session.commit()
            await session.refresh(user)

        if user.daily_requests >= 10:
            return False, 0

        user.daily_requests += 1
        await session.commit()

        return True, (10 - user.daily_requests)


# ================= SAFE EDIT =================
async def safe_edit_status(bot: Bot, chat_id: int, message_id: int, new_text: str):
    try:
        return await bot.edit_message_text(
            text=new_text,
            chat_id=chat_id,
            message_id=message_id,
            parse_mode=ParseMode.HTML,
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            return None
    except Exception:
        pass
    return None


# ================= WORKER (FIXED) =================
async def process_user_queue(uid: int, bot: Bot):
    """Worker logic: Processes the user's personal queue safely. ☕"""
    q = user_queues.get(uid)
    if not q:
        return

    logger.info(f"🚀 Worker started for user {uid}")

    try:
        while True:
            try:
                # 🔥 KEY FIX: wait for items instead of checking empty()
                item = await asyncio.wait_for(q.get(), timeout=5)
            except asyncio.TimeoutError:
                logger.info(f"🛑 Worker idle timeout for user {uid}")
                break

            chat_id = item["chat_id"]
            file_id = item["file_id"]
            status_msg_id = item["status_msg_id"]
            reply_to_id = item["reply_to_id"]

            tmp_path = None

            try:
                await safe_edit_status(
                    bot, chat_id, status_msg_id,
                    "📄 <b>Downloading...</b> [15%]"
                )

                file = await bot.get_file(file_id)
                raw = await bot.download_file(file.file_path)

                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    tmp.write(raw.read())
                    tmp_path = tmp.name

                await safe_edit_status(
                    bot, chat_id, status_msg_id,
                    "🔍 <b>Reading tiny text...</b> [45%]"
                )

                text = await extract_text_async(tmp_path)

                ai_task = asyncio.create_task(smart_extract(text))
                percent = 50

                while not ai_task.done():
                    if percent < 95:
                        percent += 5
                        await safe_edit_status(
                            bot,
                            chat_id,
                            status_msg_id,
                            f"🧠 <b>Thinking...</b> [{percent}%]"
                        )
                    await asyncio.sleep(1.2)

                data = await ai_task

                async with AsyncSessionLocal() as session:
                    stmt = select(User).where(User.tg_id == uid)
                    res = await session.execute(stmt)
                    user = res.scalar_one_or_none()
                    template = user.template_text if user else None

                formatted_output = render_result(data, template)

                await bot.send_message(
                    chat_id,
                    formatted_output,
                    reply_to_message_id=reply_to_id,
                    parse_mode=ParseMode.HTML,
                )

            except Exception as e:
                logger.error(f"Worker Error for {uid}: {e}")
                await bot.send_message(
                    chat_id,
                    f"🙄 <b>Error:</b>\n<code>{str(e)}</code>",
                    reply_to_message_id=reply_to_id,
                )

            finally:
                try:
                    await bot.delete_message(chat_id, status_msg_id)
                except Exception:
                    pass

                if tmp_path and os.path.exists(tmp_path):
                    os.remove(tmp_path)

                q.task_done()

    finally:
        logger.info(f"💤 Worker stopped for user {uid}")
        user_workers.pop(uid, None)


# ================= PDF HANDLER =================
@router.message(F.document.mime_type == "application/pdf")
async def handle_pdf(message: types.Message, bot: Bot):
    uid = message.from_user.id
    mg_id = message.media_group_id

    # 1. Limit check
    allowed, left = await check_and_update_limit(uid)
    if not allowed:
        return await message.answer(
            "💸 <b>Daily Limit Reached!</b>\n\nUpgrade to /plans now. 💅"
        )

    # 2. Media group limit
    if mg_id:
        if mg_id not in media_group_tracker:
            media_group_tracker[mg_id] = 0
        media_group_tracker[mg_id] += 1

        if media_group_tracker[mg_id] > 5:
            if media_group_tracker[mg_id] == 6:
                await message.reply(
                    "💅 <b>Honey, stop!</b> My limit is 5 PDFs. Ignoring the rest."
                )
            return

    # 3. Ensure queue exists
    if uid not in user_queues:
        user_queues[uid] = asyncio.Queue()

    left_text = "Unlimited" if left == 999 else f"{left} left today"
    q_pos = user_queues[uid].qsize()

    status_text = f"👀 <b>I woke up...</b> ({left_text})"
    if q_pos > 0:
        status_text += f"\n📥 <i>Position in queue: {q_pos + 1}</i>"

    initial_msg = await message.reply(status_text)

    await user_queues[uid].put({
        "chat_id": message.chat.id,
        "file_id": message.document.file_id,
        "status_msg_id": initial_msg.message_id,
        "reply_to_id": message.message_id,
    })

    logger.info(f"📥 User {uid} queue size: {user_queues[uid].qsize()}")

    # 4. Start worker if needed
    if uid not in user_workers or user_workers[uid].done():
        user_workers[uid] = asyncio.create_task(process_user_queue(uid, bot))


# ================= ADMIN =================
@router.message(Command("check_user"))
async def admin_check_user(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    args = message.text.split()
    if len(args) < 2:
        return await message.answer("Usage: <code>/check_user [tg_id]</code>")

    try:
        target_id = int(args[1])

        async with AsyncSessionLocal() as session:
            stmt = select(User).where(User.tg_id == target_id)
            res = await session.execute(stmt)
            user = res.scalar_one_or_none()

        if not user:
            return await message.answer("User not found.")

        status = "👑 PRO" if user.is_pro else "🆓 FREE"

        info = (
            f"👤 <b>User:</b> <code>{target_id}</code>\n"
            f"📊 <b>Status:</b> {status}\n"
            f"📅 <b>Expiry:</b> {user.expiry_date if user.expiry_date else 'N/A'}\n"
            f"📈 <b>Used:</b> {user.daily_requests}/10"
        )

        await message.answer(info)

    except Exception:
        await message.answer("Invalid ID.")


# ================= SASSY CHAT =================
@router.message(F.text & ~F.text.startswith("/"))
async def sassy_chat(message: types.Message, state: FSMContext):
    if await state.get_state() is not None:
        return

    responses = ["🙄 Send a PDF.", "💅 Only PDFs.", "🥱 Send the RC."]
    await message.reply(random.choice(responses), parse_mode=ParseMode.HTML)