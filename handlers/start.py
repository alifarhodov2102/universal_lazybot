import os
import random
from aiogram import Router, types, F, Bot
from aiogram.filters import CommandStart, Command
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database.models import User
from database.connection import AsyncSessionLocal

router = Router()

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    tg_id = message.from_user.id
    full_name = message.from_user.full_name

    # 1. IMMEDIATE FEEDBACK 🚀
    # User 20 soniya kutmaydi, darhol Alice uyg'onganini ko'radi.
    # Bu DB latency (sekinlikni) yashiradi.
    status_msg = await message.answer("❤️ <b>👀 I woke up... let me check who you are.</b> 🥱", parse_mode="HTML")

    async with AsyncSessionLocal() as session:
        # 2. Check Alice's memory (Database)
        stmt = select(User).where(User.tg_id == tg_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            # New person? Alice reluctantly creates a profile.
            new_user = User(
                tg_id=tg_id,
                username=message.from_user.username,
                free_uses=2
            )
            session.add(new_user)
            await session.commit()
            
            welcome_text = (
                f"❤️ <b>Oh, hi {full_name}... I guess.</b> ❤️\n\n"
                f"I'm <b>Alice</b>, and I parse your messy logistics PDFs because you clearly can't be bothered to do it yourself. 💅\n\n"
                f"I'll give you <b>2 free</b> extractions. After that, no coffee = no work. ☕\n\n"
                f"<i>Just send me a PDF. Or don't. I'm taking a nap either way.</i> 🥱"
            )
        else:
            # Welcome back.
            status = "Pro ✅ (My favorite ✨)" if user.is_pro else f"Freebie ({user.free_uses} left) 🆓"
            welcome_text = (
                f"❤️ <b>Back again, {full_name}?</b> ❤️\n\n"
                f"Status: <b>{status}</b>\n\n"
                f"Drop the PDF here. I'll look at it when I feel like it... maybe. 🥱💅"
            )

        # 3. EDIT INITIAL MESSAGE
        # Yangi xabar yubormasdan, eskisini tahrirlaymiz. Bu yanada professional ko'rinadi.
        await status_msg.edit_text(welcome_text, parse_mode="HTML")

@router.message(Command("help"))
async def cmd_help(message: types.Message):
    # Alice explains the rules in her own sassy way.
    help_text = (
        "💅 <b>Alice's Guide to Not Annoying Me:</b>\n\n"
        "1. <b>Send a PDF</b>: Only Rate Confirmations. I don't care about your memes. 🙄\n"
        "2. <b>Wait</b>: I'm slow, and thinking is hard. The progress bar will move eventually. 🥱\n"
        "3. <b>Settings</b>: Use /settings if you want to customize how I work for you.\n\n"
        "<i>Now leave me alone unless you have an RC to process.</i> 💅"
    )
    await message.answer(help_text, parse_mode="HTML")

@router.message(F.text & ~F.text.startswith("/"))
async def sassy_chat(message: types.Message):
    # Sassy responses for non-PDF/non-command text
    responses = [
        "🙄 I'm a bot, not your therapist. Send me a PDF or leave me alone.",
        "💅 Don't try to text me. Only PDFs get my attention.",
        "🥱 Talking is exhausting. Just send the Rate Confirmation already.",
        "🚫 Too many words, not enough PDF. Move along, honey."
    ]
    await message.reply(random.choice(responses))