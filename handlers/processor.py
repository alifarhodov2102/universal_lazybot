import os
import tempfile
import asyncio
import random
from datetime import date
from aiogram import Router, types, F, Bot
from sqlalchemy import select, update
from aiogram.exceptions import TelegramBadRequest
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext

from database.connection import AsyncSessionLocal
from database.models import User
from services.pdf_engine import extract_text_async
from services.extractor import smart_extract
from services.renderer import render_result
from config import ADMIN_IDS

router = Router()

# Global trackers for queue management
user_queues: dict[int, asyncio.Queue] = {}
user_workers: dict[int, asyncio.Task] = {}
media_group_tracker: dict[str, int] = {} # Tracks files per batch/media_group_id

async def check_and_update_limit(uid: int) -> tuple[bool, int]:
    """Checks if a user has daily quota left. Resets daily if needed. 🥱💅"""
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.tg_id == uid)
        res = await session.execute(stmt)
        user = res.scalar_one_or_none()
        
        if not user:
            return False, 0
            
        if uid in ADMIN_IDS or user.is_pro:
            return True, 999

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

async def safe_edit_status(bot: Bot, chat_id: int, message_id: int, new_text: str):
    """Safely updates status messages without crashing on 'not modified' errors. 💅"""
    try:
        return await bot.edit_message_text(
            text=new_text,
            chat_id=chat_id,
            message_id=message_id,
            parse_mode=ParseMode.HTML
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            return None
    except Exception:
        pass
    return None

async def process_user_queue(uid: int, bot: Bot):
    """Worker logic: Processes the user's personal queue one by one. ☕"""
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
                # 1. Download (15%)
                await safe_edit_status(bot, chat_id, status_msg_id, "📄 <b>Downloading...</b> [15%]")
                file = await bot.get_file(file_id)
                raw = await bot.download_file(file.file_path)

                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    tmp.write(raw.read())
                    tmp_path = tmp.name

                # 2. Extract & AI (45-95%)
                await safe_edit_status(bot, chat_id, status_msg_id, "🔍 <b>Reading tiny text...</b> [45%]")
                text = await extract_text_async(tmp_path)
                
                ai_task = asyncio.create_task(smart_extract(text))
                percent = 50
                while not ai_task.done():
                    if percent < 95:
                        percent += 5
                        await safe_edit_status(bot, chat_id, status_msg_id, f"🧠 <b>Thinking...</b> [{percent}%]")
                    await asyncio.sleep(1.2)
                
                data = await ai_task
                
                # 3. Render and Reply
                async with AsyncSessionLocal() as session:
                    stmt = select(User).where(User.tg_id == uid)
                    res = await session.execute(stmt)
                    user = res.scalar_one_or_none()
                    template = user.template_text if user else None

                formatted_output = render_result(data, template)
                
                # IMPORTANT: Reply directly to the specific PDF message that was sent
                await bot.send_message(
                    chat_id, 
                    formatted_output, 
                    reply_to_message_id=reply_to_id,
                    parse_mode=ParseMode.HTML
                )

            except Exception as e:
                await bot.send_message(
                    chat_id, 
                    f"🙄 <b>Ugh, error:</b>\n<code>{str(e)}</code>", 
                    reply_to_message_id=reply_to_id
                )
            
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

    # 1. Media Group Limit (Max 5 PDFs at once)
    if mg_id:
        if mg_id not in media_group_tracker:
            media_group_tracker[mg_id] = 0
        media_group_tracker[mg_id] += 1
        
        # If this is the 6th or higher file in the same batch, ignore it.
        if media_group_tracker[mg_id] > 5:
            if media_group_tracker[mg_id] == 6:
                await message.reply("💅 <b>Honey, stop!</b> My limit is 5 PDFs per batch. I'm ignoring the rest.")
            return

    # 2. Daily Quota Check
    allowed, left = await check_and_update_limit(uid)
    if not allowed:
        return await message.answer(
            "💸 <b>Daily Limit Reached!</b>\n\nYou've used your 10 free RCs. "
            "Wait until tomorrow or upgrade to /plans now. 💅"
        )

    # 3. Add to User Queue
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
        "reply_to_id": message.message_id  # Save this to reply to the correct PDF later
    })

    # 4. Start Worker if not already active for this user
    if uid not in user_workers or user_workers[uid].done():
        user_workers[uid] = asyncio.create_task(process_user_queue(uid, bot))

@router.message(F.text & ~F.text.startswith("/"))
async def sassy_chat(message: types.Message, state: FSMContext):
    if await state.get_state() is not None: return
    responses = [
        "🙄 I'm a bot, not your therapist. Send me a PDF or leave me alone.",
        "💅 Don't try to text me. Only PDFs get my attention.",
        "🥱 Talking is exhausting. Just send the Rate Confirmation already.",
        "🚫 Too many words, not enough PDF. Move along, honey."
    ]
    await message.reply(random.choice(responses), parse_mode=ParseMode.HTML)