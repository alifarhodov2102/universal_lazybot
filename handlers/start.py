import os
import random
from datetime import date
from aiogram import Router, types, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, update

from database.models import User
from database.connection import AsyncSessionLocal
from utils.states import TemplateStates
# Assuming you have an AI utility to handle the conversion
# from utils.ai_utils import extract_template_structure 

router = Router()

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    """Simple and clear welcome for drivers 🚛"""
    tg_id = message.from_user.id
    full_name = message.from_user.full_name

    status_msg = await message.answer("⏳ <b>Checking your access...</b>", parse_mode=ParseMode.HTML)

    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.tg_id == tg_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

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
            today = date.today()
            current_reqs = user.daily_requests if user.last_request_date == today else 0
            
            if user.is_pro:
                status = "Pro ✅ (Unlimited)"
            else:
                status = f"Free ({10 - current_reqs}/10 left today) 🆓"

            welcome_text = (
                f"❤️ <b>Welcome back, {full_name}!</b>\n\n"
                f"Status: <b>{status}</b>\n\n"
                "Ready to work? Just drop the <b>PDF</b> here. 🥱💅"
            )

        await status_msg.edit_text(welcome_text, parse_mode=ParseMode.HTML)

# --- TEMPLATE MANAGEMENT --- 💅

@router.message(Command("set_template"))
async def cmd_set_template(message: types.Message, state: FSMContext):
    """Simplified AI template learning with Cancel button"""
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="❌ Cancel Setup", callback_data="cancel_template"))

    guide = (
        "⚙️ <b>Teach Me Your Style!</b>\n\n"
        "Simply <b>Paste</b> a previous load message that you liked.\n\n"
        "Alice will automatically keep your notes and structure, but replace the "
        "specific details with blanks for new PDFs! 🧠\n\n"
        "⚠️ <i>Must be at least 20 characters long.</i> 🥱💅"
    )
    await message.answer(guide, parse_mode=ParseMode.HTML, reply_markup=builder.as_markup())
    await state.set_state(TemplateStates.waiting_for_template)

@router.message(TemplateStates.waiting_for_template, F.text)
async def process_template(message: types.Message, state: FSMContext):
    """Alice extracts the skeleton from the user example 🧠"""
    example_text = message.text
    
    if len(example_text) < 20 or example_text.startswith("/"):
        return await message.answer(
            "🙄 <b>I need a real example, honey.</b>\n\n"
            "Paste a full load message (at least 20 characters) so I can see "
            "where the broker, rate, and notes are. 💅",
            parse_mode=ParseMode.HTML
        )

    status_msg = await message.answer("🧠 <b>Alice is analyzing the structure...</b>", parse_mode="HTML")

    # This prompt tells the AI to create a skeleton while preserving notes
    # You would pass this to your LLM (DeepSeek/OpenAI)
    system_prompt = (
        "Convert this logistics load message into a Jinja2 template. "
        "Replace specific data with: {{ broker }}, {{ load_number }}, {{ rate }}, "
        "{{ total_miles }}, {{ pickup_info }}, {{ delivery_info }}. "
        "STRICT RULE: KEEP all emojis, decorative lines, and fixed policy notes "
        "(like charges or trailer photo rules) EXACTLY as they are. "
        "Output ONLY the template text."
    )

    # placeholder for your AI call: 
    # skeleton_template = await extract_template_structure(system_prompt, example_text)
    
    # For demonstration, we assume the AI returns the 'skeleton' version of their text
    skeleton_template = example_text # Replace this with the actual AI output variable

    async with AsyncSessionLocal() as session:
        await session.execute(
            update(User).where(User.tg_id == message.from_user.id).values(template_text=skeleton_template)
        )
        await session.commit()

    await status_msg.edit_text("✅ <b>Template Learnt!</b>\nI've kept your notes and structure. Send me a PDF to test it! 🥱💅", parse_mode="HTML")
    await state.clear()

@router.callback_query(F.data == "cancel_template")
async def cancel_template(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("🔄 <b>Setup cancelled.</b> I'll keep using my default style. 💅", parse_mode="HTML")
    await callback.answer()

@router.message(Command("my_template"))
async def cmd_my_template(message: types.Message):
    async with AsyncSessionLocal() as session:
        stmt = select(User.template_text).where(User.tg_id == message.from_user.id)
        res = await session.execute(stmt)
        current = res.scalar_one_or_none()

    text = current if current else "Alice's Default Style 💅"
    await message.answer(f"📋 <b>Your Current Format:</b>\n\n<code>{text}</code>", parse_mode="HTML")

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
        "📍 <b>Custom format?</b> Use /set_template and paste a previous load message.\n"
        "📍 <b>Daily Limit:</b> 10 free extractions per day.\n\n"
        "Support: @lazyalice_admin 🥱"
    )
    await message.answer(help_text, parse_mode=ParseMode.HTML)

@router.message(F.text & ~F.text.startswith("/"))
async def sassy_chat(message: types.Message, state: FSMContext):
    # Only trigger sassy chat if we aren't waiting for a template
    if await state.get_state() is not None:
        return

    responses = [
        "🙄 I'm a bot, not your therapist. Send me a PDF.",
        "💅 Only PDFs get my attention, honey.",
        "🥱 Boring. Send the Rate Confirmation already.",
        "🚫 Too many words. Just send the file."
    ]
    await message.reply(random.choice(responses))