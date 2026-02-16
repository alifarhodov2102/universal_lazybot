import os
import random
from aiogram import Router, types, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, update
from database.models import User
from database.connection import AsyncSessionLocal
from utils.states import TemplateStates # State-larni import qilamiz 💅

router = Router()

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    tg_id = message.from_user.id
    full_name = message.from_user.full_name

    # 1. IMMEDIATE FEEDBACK 🚀
    status_msg = await message.answer("❤️ <b>👀 I woke up... let me check who you are.</b> 🥱", parse_mode="HTML")

    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.tg_id == tg_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            # User profile creation
            new_user = User(
                tg_id=tg_id,
                username=message.from_user.username,
                free_uses=2
            )
            session.add(new_user)
            await session.commit()
            
            welcome_text = (
                f"👋 <b>Welcome to Lazy Alice, {full_name}!</b>\n\n"
                "I'm here to extract data from your Rate Confirmations (RC) in seconds. 🥱\n\n"
                "📜 <b>My Commands:</b>\n"
                "🚀 /start - Show this welcome message\n"
                "💎 /status - Check your subscription and free uses\n"
                "💳 /plans - Upgrade to Pro (Stars or Card)\n"
                "⚙️ /set_template - Customize your output format, just send me your example load info and I will become your structure forever\n"
                "❓ /help - If you get stuck\n\n"
                "💡 <b>How to use:</b>\n"
                "1. Send me a <b>PDF</b> document.\n"
                "2. Copy the result and send it to your dispatcher! 💅\n\n"
                "<i>I'll give you 2 free extractions. Use them wisely.</i> 🥱"
            )
        else:
            status = "Pro ✅" if user.is_pro else f"Free ({user.free_uses} left) 🆓"
            welcome_text = (
                f"❤️ <b>Back again, {full_name}?</b> ❤️\n\n"
                f"Status: <b>{status}</b>\n\n"
                f"Drop the PDF here. I'm ready (I guess). 🥱💅"
            )

        await status_msg.edit_text(welcome_text, parse_mode="HTML")

@router.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = (
        "❓ <b>Need help, honey?</b>\n\n"
        "<b>1. Formatting:</b> If you want to change how the text looks, use /set_template. 💅\n"
        "<b>2. Card Payment:</b> Use /plans and click the link to send the receipt here:\n"
        "<code>5614682203258662</code> (Click to copy)\n\n"
        "<b>3. Not reading PDF:</b> Make sure it's a real RC, not a blurry photo of your screen. 🙄\n\n"
        "Contact @lazyalice_admin for manual activation. 💅"
    )
    await message.answer(help_text, parse_mode="HTML")

# --- CUSTOM TEMPLATE LOGIC --- 💅
@router.message(Command("set_template"))
async def cmd_set_template(message: types.Message, state: FSMContext):
    """Start the template customization process"""
    guide = (
        "⚙️ <b>Custom Template Editor</b>\n\n"
        "Send me your new format. Use these tags:\n"
        "<code>{{ broker }}</code>, <code>{{ load_number }}</code>, "
        "<code>{{ rate }}</code>, <code>{{ total_miles }}</code>\n\n"
        "Example:\n"
        "<i>Broker: {{ broker }}\nLoad: {{ load_number }}\nPay: {{ rate }}</i>\n\n"
        "Send your template now or /cancel. 🥱"
    )
    await message.answer(guide, parse_mode="HTML")
    await state.set_state(TemplateStates.waiting_for_template)

@router.message(TemplateStates.waiting_for_template)
async def process_template(message: types.Message, state: FSMContext):
    """Save the user's custom template to DB"""
    new_tmpl = message.text
    tg_id = message.from_user.id

    async with AsyncSessionLocal() as session:
        await session.execute(
            update(User).where(User.tg_id == tg_id).values(template_text=new_tmpl)
        )
        await session.commit()

    await message.answer("✅ <b>Template saved!</b>\nI'll use this for your next PDF. 🥱💅", parse_mode="HTML")
    await state.clear()

@router.message(F.text & ~F.text.startswith("/"))
async def sassy_chat(message: types.Message):
    responses = [
        "🙄 I'm a bot, not your therapist. Send me a PDF or leave me alone.",
        "💅 Don't try to text me. Only PDFs get my attention.",
        "🥱 Talking is exhausting. Just send the Rate Confirmation already.",
        "🚫 Too many words, not enough PDF. Move along, honey."
    ]
    await message.reply(random.choice(responses))