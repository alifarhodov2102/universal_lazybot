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

# ================= GLOBAL STATE =================
# Single queue to rule them all. No more dictionary mess. 💅
extraction_queue = asyncio.Queue()
media_group_tracker: dict[str, int] = {}

MEDIA_GROUP_LIMIT = 5
FREE_DAILY_LIMIT = 10

# ================= PERMISSION HELPER =================
async def get_user_context(uid: int) -> tuple[bool, int, str]:
    """Fetches everything Alice needs in one DB hit. 🏥"""
    async with AsyncSessionLocal() as session:
        # 🟢 CRITICAL: Clear session cache to see 'is_pro' updates immediately.
        await session.execute(text("COMMIT"))
        
        stmt = select(User).where(User.tg_id == uid)
        res = await session.execute(stmt)
        user = res.scalar_one_or_none()
        
        if not user:
            return False, 0, ""

        is_admin = uid in ADMIN_IDS
        now = datetime.utcnow()
        
        # Robust Pro check
        is_pro_active = False
        if user.is_pro:
            expiry = user.expiry_date
            if expiry is None or (expiry.replace(tzinfo=None) if expiry.tzinfo else expiry) > now:
                is_pro_active = True
        
        # Capture template now so worker doesn't need to touch DB
        template = str(user.template_text or "")
        
        logger.info(f"🔍 [DB CHECK] UID: {uid} | Pro: {is_pro_active} | Admin: {is_admin}")

        if is_admin or is_pro_active:
            return True, 999, template

        # Free daily logic
        today = date.today()
        if user.last_request_date != today:
            user.daily_requests = 0
            user.last_request_date = today
            await session.commit()
        
        if user.daily_requests >= FREE_DAILY_LIMIT:
            return False, 0, template
            
        user.daily_requests += 1
        await session.commit()
        return True, (FREE_DAILY_LIMIT - user.daily_requests), template

# ================= GLOBAL WORKER =================
async def global_pdf_worker(bot: Bot):
    """The core engine. Processes the global queue one by one. ☕💅"""
    logger.info("⚙️ Global PDF Worker is now ONLINE.")
    while True:
        # Wait for next PDF in line
        item = await extraction_queue.get()
        
        uid = item["uid"]
        chat_id = item["chat_id"]
        file_id = item["file_id"]
        status_id = item["status_msg_id"]
        reply_id = item["reply_to_id"]
        template = item["template"]
        
        tmp_path = None
        try:
            # 1. Download
            await bot.edit_message_text("📄 <b>Downloading...</b> [15%]", chat_id, status_id)
            file = await bot.get_file(file_id)
            raw = await bot.download_file(file.file_path)

            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(raw.read())
                tmp_path = tmp.name

            # 2. OCR Extraction
            await bot.edit_message_text("🔍 <b>Reading PDF...</b> [45%]", chat_id, status_id)
            text = await extract_text_async(tmp_path)
            
            # 3. AI Smart Extract
            ai_task = asyncio.create_task(smart_extract(text))
            percent = 50
            while not ai_task.done():
                if percent < 95:
                    percent += 5
                    try:
                        await bot.edit_message_text(f"🧠 <b>Thinking...</b> [{percent}%]", chat_id, status_id)
                    except: pass
                await asyncio.sleep(1.5)
            
            data = await ai_task
            
            # 4. Final Render
            formatted_output = render_result(data, template)
            await bot.send_message(chat_id, formatted_output, reply_to_message_id=reply_id)

        except Exception as e:
            logger.error(f"❌ Worker error for {uid}: {e}")
            try:
                await bot.send_message(chat_id, f"🙄 <b>Error:</b> <code>{e}</code>", reply_to_message_id=reply_id)
            except: pass
        finally:
            # Cleanup
            try: await bot.delete_message(chat_id, status_id)
            except: pass
            if tmp_path and os.path.exists(tmp_path): os.remove(tmp_path)
            extraction_queue.task_done()

# ================= HANDLERS =================
@router.message(F.document.mime_type == "application/pdf")
async def handle_pdf(message: types.Message, bot: Bot):
    uid = message.from_user.id
    mg_id = message.media_group_id

    # 1. Permission check and data pre-fetching
    allowed, left, template = await get_user_context(uid)
    if not allowed:
        return await message.answer("💸 <b>Daily Limit Reached!</b> Upgrade to /plans. 💅")

    # 2. Batching limit
    if mg_id:
        media_group_tracker[mg_id] = media_group_tracker.get(mg_id, 0) + 1
        if media_group_tracker[mg_id] > MEDIA_GROUP_LIMIT:
            if media_group_tracker[mg_id] == MEDIA_GROUP_LIMIT + 1:
                await message.reply("💅 <b>Honey, stop!</b> Limit 5 PDFs per batch.")
            return
        # Background cleanup for the tracker
        asyncio.create_task(_cleanup_mg(mg_id))

    # 3. Queue Notification
    left_text = "Unlimited" if left == 999 else f"{left} left today"
    q_pos = extraction_queue.qsize()
    
    status_text = f"👀 <b>I woke up...</b> ({left_text})"
    if q_pos > 0:
        status_text += f"\n📥 <i>Position in queue: {q_pos + 1}</i>"
        
    initial_msg = await message.reply(status_text)

    # 4. Add to Global Queue
    await extraction_queue.put({
        "uid": uid,
        "chat_id": message.chat.id, 
        "file_id": message.document.file_id,
        "status_msg_id": initial_msg.message_id, 
        "reply_to_id": message.message_id,
        "template": template
    })

async def _cleanup_mg(mg_id: str):
    await asyncio.sleep(120)
    media_group_tracker.pop(mg_id, None)

@router.message(Command("check_user"))
async def admin_check_user(message: types.Message):
    if message.from_user.id not in ADMIN_IDS: return
    args = message.text.split()
    if len(args) < 2: return await message.answer("Usage: <code>/check_user ID</code>")
    
    # Simple reuse of context helper
    _, left, _ = await get_user_context(int(args[1]))
    status = "👑 PRO" if left == 999 else "🆓 FREE"
    await message.answer(f"👤 User: {args[1]}\n📊 Status: {status}\n📈 Left: {left if left != 999 else 'Inf'}")

@router.message(F.text & ~F.text.startswith("/"))
async def sassy_chat(message: types.Message, state: FSMContext):
    if await state.get_state() is not None: return
    await message.reply(random.choice(["🙄 Send a PDF.", "💅 Only PDFs.", "🥱 Send the RC."]))