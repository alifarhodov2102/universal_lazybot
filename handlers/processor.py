import os
import tempfile
import asyncio
import random
from aiogram import Router, types, F, Bot
from sqlalchemy import select, update
from aiogram.exceptions import TelegramBadRequest

from database.connection import AsyncSessionLocal
from database.models import User
from services.pdf_engine import extract_text_async
from services.extractor import smart_extract
from services.renderer import render_result
from config import ADMIN_IDS

router = Router()

user_queues: dict[int, asyncio.Queue] = {}
user_workers: dict[int, asyncio.Task] = {}

async def safe_edit_status(bot: Bot, chat_id: int, message_id: int, new_text: str):
    """Alice checks if she actually has something new to say before talking 💅"""
    try:
        return await bot.edit_message_text(
            text=new_text,
            chat_id=chat_id,
            message_id=message_id
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            return None
    except Exception:
        pass
    return None

async def process_user_queue(uid: int, bot: Bot):
    q = user_queues.get(uid)
    if not q: return

    try:
        while not q.empty():
            item = await q.get()
            chat_id = item["chat_id"]
            file_id = item["file_id"]
            msg_id = item["initial_msg_id"]
            
            tmp_path = None

            try:
                # 1. Processing starts (15%)
                await safe_edit_status(bot, chat_id, msg_id, "📄 Downloading this boring PDF... [15%]")
                
                file = await bot.get_file(file_id)
                raw = await bot.download_file(file.file_path)

                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    tmp.write(raw.read())
                    tmp_path = tmp.name

                async with AsyncSessionLocal() as session:
                    stmt = select(User).where(User.tg_id == uid)
                    res = await session.execute(stmt)
                    user = res.scalar_one_or_none()

                    # 2. Extraction (45%)
                    await safe_edit_status(bot, chat_id, msg_id, "🔍 Reading the tiny text for you... [45%]")
                    text = await extract_text_async(tmp_path)
                    
                    # 3. AI Task
                    ai_task = asyncio.create_task(smart_extract(text))
                    
                    percent = 50
                    while not ai_task.done():
                        if percent < 95:
                            percent += 5
                            quotes = [
                                f"🧠 Thinking is hard... [{percent}%]",
                                f"☕ My coffee is getting cold... [{percent}%]",
                                f"💅 Almost done, don't rush me... [{percent}%]"
                            ]
                            idx = (percent // 10) % len(quotes)
                            await safe_edit_status(bot, chat_id, msg_id, quotes[idx])
                        await asyncio.sleep(1.5)
                    
                    data = await ai_task
                    await safe_edit_status(bot, chat_id, msg_id, "✨ Finally! Here it is. [100%]")

                    # 4. Rendering (Removed the 'Thank me later' line)
                    formatted_output = render_result(data, user.template_text if user else None)
                    
                    # Alice just sends the result now without extra sassy footer
                    await bot.send_message(chat_id, formatted_output, parse_mode="Markdown")

                    # 5. Limit management
                    if uid not in ADMIN_IDS and user and not user.is_pro:
                        await session.execute(
                            update(User).where(User.tg_id == uid).values(free_uses=user.free_uses - 1)
                        )
                        await session.commit()

            except Exception as e:
                await bot.send_message(chat_id, f"🙄 Ugh, even I can't fix this error: {str(e)}")
            
            finally:
                try: await bot.delete_message(chat_id, msg_id)
                except: pass
                
                if tmp_path and os.path.exists(tmp_path):
                    os.remove(tmp_path)
                q.task_done()
    finally:
        user_workers.pop(uid, None)

@router.message(F.document.mime_type == "application/pdf")
async def handle_pdf(message: types.Message, bot: Bot):
    uid = message.from_user.id
    is_admin = uid in ADMIN_IDS

    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.tg_id == uid)
        res = await session.execute(stmt)
        user = res.scalar_one_or_none()

        if not user and not is_admin:
             return await message.answer("👋 Hey! Use /start first. I don't talk to strangers.")

        if not is_admin and user and not user.is_pro and user.free_uses <= 0:
            return await message.answer(
                "💸 You're out of freebies, honey. Get /plans if you want me to keep working."
            )

    # Instant response
    initial_msg = await message.answer("👀 I woke up... let me look at your RC. 🥱")

    if uid not in user_queues:
        user_queues[uid] = asyncio.Queue()
    
    await user_queues[uid].put({
        "chat_id": message.chat.id, 
        "file_id": message.document.file_id,
        "initial_msg_id": initial_msg.message_id
    })

    if uid not in user_workers or user_workers[uid].done():
        user_workers[uid] = asyncio.create_task(process_user_queue(uid, bot))

@router.message(F.text & ~F.text.startswith("/"))
async def sassy_chat(message: types.Message):
    responses = [
        "🙄 I'm a bot, not your therapist. Send me a PDF or leave me alone.",
        "💅 Don't try to text me. Only PDFs get my attention.",
        "🥱 Talking is exhausting. Just send the Rate Confirmation already.",
        "🚫 Too many words, not enough PDF. Move along, honey."
    ]
    await message.reply(random.choice(responses))
