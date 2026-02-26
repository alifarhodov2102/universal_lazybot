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

async def check_and_update_limit(uid: int) -> tuple[bool, int, str]:
    """Alice checks the user's soul and returns (IsAllowed, LeftCount, TemplateText) 💅"""
    async with AsyncSessionLocal() as session:
        # 🟢 CRITICAL: Force a COMMIT to clear the transaction cache
        # This fixes the issue where the bot doesn't see Pro updates from other sessions.
        await session.execute(text("COMMIT"))
        
        stmt = select(User).where(User.tg_id == uid)
        res = await session.execute(stmt)
        user = res.scalar_one_or_none()
        
        if not user:
            return False, 0, ""
            
        # 1. Status Logic
        is_admin = uid in ADMIN_IDS
        current_time = datetime.utcnow()
        
        # Determine if Pro is actually active
        is_pro_active = False
        if user.is_pro:
            if user.expiry_date is None or user.expiry_date > current_time:
                is_pro_active = True
        
        # Pre-capture template as a string to avoid lazy-loading issues
        user_template = str(user.template_text or "")
        
        logger.info(f"🔍 [DB CHECK] UID: {uid} | Pro: {is_pro_active} | Admin: {is_admin}")

        if is_admin or is_pro_active:
            return True, 999, user_template

        # 2. Free User Logic
        today = date.today()
        if user.last_request_date < today:
            user.daily_requests = 0
            user.last_request_date = today
            await session.commit()
        
        if user.daily_requests >= 10:
            return False, 0, user_template
            
        user.daily_requests += 1
        await session.commit()
        
        return True, (10 - user.daily_requests), user_template

async def safe_edit_status(bot: Bot, chat_id: int, message_id: int, new_text: str):
    """Alice speaks carefully to avoid crashing. 💅"""
    try:
        return await bot.edit_message_text(
            text=new_text,
            chat_id=chat_id,
            message_id=message_id,
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass
    return None

async def process_user_queue(uid: int, bot: Bot, template: str):
    """Worker logic: Processes the user's personal queue. Template is passed in safely. ☕"""
    q = user_queues.get(uid)
    if not q: return

    try:
        while not q.empty():
            item = await q.get()
            chat_id = item["chat_id"]
            file_id = item["file_id"]
            status_msg_id = item["status_msg_id"]
            reply_to_id = item["reply_to_id"]
            
            tmp_path = None
            try:
                await safe_edit_status(bot, chat_id, status_msg_id, "📄 <b>Downloading...</b> [15%]")
                file = await bot.get_file(file_id)
                raw = await bot.download_file(file.file_path)

                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    tmp.write(raw.read())
                    tmp_path = tmp.name

                await safe_edit_status(bot, chat_id, status_msg_id, "🔍 <b>Reading PDF...</b> [45%]")
                text = await extract_text_async(tmp_path)
                
                # DeepSeek AI Logic
                data = await smart_extract(text)
                
                # Render using the template we grabbed at the start
                formatted_output = render_result(data, template)
                
                await bot.send_message(
                    chat_id, 
                    formatted_output, 
                    reply_to_message_id=reply_to_id,
                    parse_mode=ParseMode.HTML
                )

            except Exception as e:
                logger.error(f"❌ Worker Error for {uid}: {e}")
                await bot.send_message(chat_id, f"🙄 <b>Error:</b> <code>{e}</code>", reply_to_message_id=reply_to_id)
            
            finally:
                try: await bot.delete_message(chat_id, status_msg_id)
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

    # 1. Fetch permissions and template
    allowed, left, template = await check_and_update_limit(uid)
    if not allowed:
        return await message.answer(
            "💸 <b>Daily Limit Reached!</b>\n\nUpgrade to /plans now. 💅"
        )

    # 2. Batch Limit (Max 5 PDFs)
    if mg_id:
        if mg_id not in media_group_tracker:
            media_group_tracker[mg_id] = 0
        media_group_tracker[mg_id] += 1
        
        if media_group_tracker[mg_id] > 5:
            if media_group_tracker[mg_id] == 6:
                await message.reply("💅 <b>Honey, stop!</b> My limit is 5 PDFs. Ignoring the rest.")
            return

    # 3. Queue Notification
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

    # 4. Start Worker
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
    except: await message.answer("Invalid ID.")

@router.message(F.text & ~F.text.startswith("/"))
async def sassy_chat(message: types.Message, state: FSMContext):
    if await state.get_state() is not None: return
    responses = ["🙄 Send a PDF.", "💅 Only PDFs.", "🥱 Send the RC.", "🚫 Move along, honey."]
    await message.reply(random.choice(responses), parse_mode=ParseMode.HTML)