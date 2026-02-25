import os
import random
from aiogram import Router, types, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from sqlalchemy import select, update
from datetime import date

from database.models import User
from database.connection import AsyncSessionLocal
from utils.states import TemplateStates

router = Router()

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    """Simple and clear welcome for drivers 🚛"""
    tg_id = message.from_user.id
    full_name = message.from_user.full_name

    # Initial loading message
    status_msg = await message.answer("⏳ <b>Checking your access...</b>", parse_mode=ParseMode.HTML)

    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.tg_id == tg_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

        # 1. Register new user if not in database
        if not user:
            user = User(
                tg_id=tg_id,
                username=message.from_user.username,
                daily_requests=0,
                last_request_date=date.today()
            )
            session.add(user)
            await session.commit()
            
            welcome_text = (
                f"👋 <b>Welcome to Lazy Alice, {full_name}!</b>\n\n"
                "I turn messy PDFs into clean text for your dispatch groups. 🥱\n\n"
                "🚀 <b>Quick Guide:</b>\n"
                "1️⃣ Send me a <b>PDF</b> (Rate Confirmation).\n"
                "2️⃣ Wait a few seconds.\n"
                "3️⃣ Copy the result and post it. 💅\n\n"
                "💰 <b>Daily Limit:</b> 10 free RCs every day."
            )
        else:
            # 2. Show status for returning users
            # Reset daily requests visually if it's a new day
            today = date.today()
            current_requests = user.daily_requests if user.last_request_date == today else 0
            
            if user.is_pro:
                status = "Pro ✅ (Unlimited)"
            else:
                status = f"Free ({10 - current_requests}/10 left today) 🆓"

            welcome_text = (
                f"❤️ <b>Welcome back, {full_name}!</b>\n\n"
                f"Status: <b>{status}</b>\n\n"
                "Ready to work? Just drop the <b>PDF</b> here. 🥱💅"
            )

        await status_msg.edit_text(welcome_text, parse_mode=ParseMode.HTML)

# --- TEMPLATE MANAGEMENT --- 💅

@router.message(Command("set_template"))
async def cmd_set_template(message: types.Message, state: FSMContext):
    """Simplified AI template learning"""
    guide = (
        "⚙️ <b>Teach Me Your Style!</b>\n\n"
        "Simply <b>Paste</b> a previous load message that you liked.\n\n"
        "My AI will learn the format and apply it to all your future PDFs automatically. "
        "No coding needed! 🥱💅"
    )
    await message.answer(guide, parse_mode=ParseMode.HTML)
    await state.set_state(TemplateStates.waiting_for_template)

@router.message(Command("my_template"))
async def cmd_my_template(message: types.Message):
    async with AsyncSessionLocal() as session:
        stmt = select(User.template_text).where(User.tg_id == message.from_user.id)
        res = await session.execute(stmt)
        current = res.scalar_one_or_none()

    text = current if current else "Alice's Default Style 💅"
    await message.answer(f"📋 <b>Your Current Format:</b>\n\n<code>{text}</code>", parse_mode=ParseMode.HTML)

@router.message(Command("reset_template"))
async def cmd_reset_template(message: types.Message):
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(User).where(User.tg_id == message.from_user.id).values(template_text=None)
        )
        await session.commit()
    await message.answer("🔄 <b>Reset!</b> Back to my original style. 💅")

# --- GENERAL HELP --- ❓

@router.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = (
        "❓ <b>How can I help, honey?</b>\n\n"
        "📍 <b>Bot not working?</b> Make sure you send a PDF, not a photo.\n"
        "📍 <b>Want custom format?</b> Use /set_template.\n"
        "📍 <b>Need more than 10 RCs?</b> Use /plans for Pro.\n\n"
        "Support: @lazyalice_admin 🥱"
    )
    await message.answer(help_text, parse_mode=ParseMode.HTML)

@router.message(F.text & ~F.text.startswith("/"))
async def sassy_chat(message: types.Message):
    responses = [
        "🙄 I'm a bot, not your therapist. Send me a PDF.",
        "💅 Only PDFs get my attention, honey.",
        "🥱 Boring. Send the Rate Confirmation already.",
        "🚫 Too many words. Just send the file."
    ]
    await message.reply(random.choice(responses))