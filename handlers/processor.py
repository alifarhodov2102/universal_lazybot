import os
import tempfile
import asyncio
import random
import logging
from datetime import date, datetime
from aiogram import Router, types, F, Bot
from sqlalchemy import select, text
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

async def get_user_permissions(uid: int) -> tuple[bool, int, str]:
    """
    One-stop shop for user data. 
    Fetches Pro status, resets limits if it's a new day, and gets the template.
    """
    async with AsyncSessionLocal() as session:
        # 🟢 CRITICAL: Force a COMMIT to clear session cache 
        # This ensures we see 'is_pro' updates made by other processes
        await session.execute(text("COMMIT"))
        
        stmt = select(User).where(User.tg_id == uid)
        res = await session.execute(stmt)
        user = res.scalar_one_or_none()
        
        if not user:
            return False, 0, ""

        # 1. Admin/Pro Logic
        is_admin = uid in ADMIN_IDS
        is_pro = user.is_pro and (user.expiry_date is None or user.expiry_date > datetime.utcnow())
        
        # Pre-capture template as a safe string
        template = str(user.template_text or "")
        
        logger.info(f"🔍 [DB CHECK] UID: {uid} | Pro: {is_pro} | Admin: {is_admin}")

        if is_admin or is_pro:
            return True, 999, template

        # 2. Free Daily Limit Logic
        today = date.today()
        if user.last_request_date < today:
            user.daily_requests = 0
            user.last_request_date = today
            await session.commit()
        
        if user.daily_requests >= 10:
            return False, 0, template
            
        user.daily_requests += 1
        await session.commit()
        return True, (10 - user.daily_requests), template

async def safe_edit_status(bot: Bot, chat_id: int, message_id: int, new_text: str):
    """Updates status without crashing on Telegram API hiccups. 💅"""
    try:
        await bot.edit_message_text(
            text=new_text,
            chat_id=chat_id,
            message_id=message_id,
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass

async def process_user_queue(uid: int, bot: Bot, template: str):
    """
    The Worker: Independent of the Database.
    Processes PDFs one by one for a specific user.
    """
    q = user_queues.get(uid)
    if not q: return

    try:
        while not q.empty():
            item = await q.get()
            chat_id = item["chat_id"]
            file_id = item["file_id"]
            status_id = item["status_msg_id"]
            reply_id = item["reply_to_id"]
            
            tmp_path = None
            try:
                await safe_edit_status(bot, chat_id, status_id, "📄 <b>Downloading...</b> [15%]")
                file = await bot.get_file(file_id)
                raw = await bot.download_file(file.file_path)

                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    tmp.write(raw.read())
                    tmp_path = tmp.name

                await safe_edit_status(bot, chat_id, status_id, "🔍 <b>Reading PDF...</b> [45%]")
                text = await extract_text_async(tmp_path)
                
                # AI Logic (DeepSeek/OCR)
                data = await smart_extract(text)
                
                # Render using the template we grabbed before the worker started
                formatted_output = render_result(data, template)
                
                await bot.send_message(
                    chat_id, 
                    formatted_output, 
                    reply_to_message_id=reply_id,
                    parse_mode=ParseMode.HTML
                )

            except Exception as e:
                logger.error(f"❌ Worker Error for {uid}: {e}")
                await bot.send_message(chat_id, f"🙄 <b>Error:</b> <code>{e}</code>", reply_to_message_id=reply_id)
            
            finally:
                try: await bot.delete_message(chat_id, status_id)
                except: pass
                if tmp_path and os.path.exists(tmp_path): 
                    os.remove(tmp_path)
                q.task_done()
    finally:
        user_workers.pop(uid, None)

@router.message(F.document.mime_type == "application/pdf")
async def handle_pdf(message: types.Message, bot: Bot):
    uid = message.from_user.id
    mg_id = message.media_group_id

    # 1. Fetch EVERYTHING from the DB immediately 🏃‍♂️
    allowed, left, template = await get_user_permissions(uid)
    if not allowed:
        return await message.answer(
            "💸 <b>Daily Limit Reached!</b>\n\nUpgrade to /plans now. 💅"
        )

    # 2. Batch Limit (Max 5 PDFs in one go)
    if mg_id:
        if mg_id not in media_group_tracker:
            media_group_tracker[mg_id] = 0
        media_group_tracker[mg_id] += 1
        
        if media_group_tracker[mg_id] > 5:
            if media_group_tracker[mg_id] == 6:
                await message.reply("💅 <b>Honey, stop!</b> My limit is 5 PDFs per batch.")
            return

    # 3. Queue Logic
    if uid not in user_queues:
        user_queues[uid] = asyncio.Queue()
    
    left_text = "Unlimited" if left == 999 else f"{left} left today"
    q_pos = user_queues[uid].qsize()
    
    status_text = f"👀 <b>I woke up...</b> ({left_text})"
    if q_pos > 0:
        status_text += f"\n📥 <i>Position: {q_pos + 1}</i>"
        
    initial_msg = await message.reply(status_text)

    await user_queues[uid].put({
        "chat_id": message.chat.id, 
        "file_id": message.document.file_id,
        "status_msg_id": initial_msg.message_id,
        "reply_to_id": message.message_id 
    })

    # 4. Start Worker (Pass the template into the worker so it doesn't need the DB)
    if uid not in user_workers or user_workers[uid].done():
        user_workers[uid] = asyncio.create_task(process_user_queue(uid, bot, template))

@router.message(Command("check_user"))
async def admin_check_user(message: types.Message):
    if message.from_user.id not in ADMIN_IDS: return
    args = message.text.split()
    if len(args) < 2: return await message.answer("Usage: <code>/check_user [tg_id]</code>")

    try:
        target_id = int(args[1])
        async with AsyncSessionLocal() as session:
            stmt = select(User).where(User.tg_id == target_id)
            res = await session.execute(stmt)
            user = res.scalar_one_or_none()

        if not user: return await message.answer("User not found.")

        status = "👑 PRO" if user.is_pro else "🆓 FREE"
        info = (
            f"👤 <b>User:</b> <code>{target_id}</code>\n"
            f"📊 <b>Status:</b> {status}\n"
            f"📅 <b>Expiry:</b> {user.expiry_date if user.expiry_date else 'N/A'}\n"
            f"📈 <b>Used:</b> {user.daily_requests}/10"
        )
        await message.answer(info)
    except Exception:
        await message.answer("Invalid ID format.")

@router.message(F.text & ~F.text.startswith("/"))
async def sassy_chat(message: types.Message, state: FSMContext):
    if await state.get_state() is not None: return
    responses = ["🙄 Send a PDF.", "💅 Only PDFs.", "🥱 Send the RC."]
    await message.reply(random.choice(responses))