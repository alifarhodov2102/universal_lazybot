import os
import random
from aiogram import Router, types, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, update
from database.models import User
from database.connection import AsyncSessionLocal
from utils.states import TemplateStates

router = Router()

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    tg_id = message.from_user.id
    full_name = message.from_user.full_name

    status_msg = await message.answer("❤️ <b>👀 I woke up... let me check who you are.</b> 🥱", parse_mode="HTML")

    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.tg_id == tg_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            new_user = User(
                tg_id=tg_id,
                username=message.from_user.username,
                free_uses=2
            )
            session.add(new_user)
            await session.commit()
            
            welcome_text = (
                f"👋 <b>Welcome to Lazy Alice, {full_name}!</b>\n\n"
                "I save you <b>30% of your time</b> by parsing messy RCs in seconds. 🥱\n\n"
                "📜 <b>Commands:</b>\n"
                "🚀 /start - Show this message\n"
                "💎 /status - Check Pro status & limits\n"
                "⚙️ /set_template - Set your own format\n"
                "📋 /my_template - See your current format\n"
                "🔄 /reset_template - Go back to Alice's default\n"
                "❓ /help - Get assistance\n\n"
                "💡 <b>How to use:</b> Send me a <b>PDF</b> document. 💅"
            )
        else:
            status = "Pro ✅" if user.is_pro else f"Free ({user.free_uses} left) 🆓"
            welcome_text = (
                f"❤️ <b>Back again, {full_name}?</b> ❤️\n\n"
                f"Status: <b>{status}</b>\n\n"
                f"Drop the PDF here. Let's save that 30% of your time. 🥱💅"
            )

        await status_msg.edit_text(welcome_text, parse_mode="HTML")

# --- TEMPLATE MANAGEMENT COMMANDS --- 💅

@router.message(Command("my_template"))
async def cmd_my_template(message: types.Message):
    """Alice shows you what you're currently working with 🥱"""
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.tg_id == message.from_user.id)
        res = await session.execute(stmt)
        user = res.scalar_one_or_none()

    current = user.template_text if user and user.template_text else "Alice's Default (Sassy & Bold) 💅"
    
    await message.answer(
        f"📋 <b>Your Current Template:</b>\n\n<code>{current}</code>\n\n"
        "Use /set_template to change it or /reset_template to go back to my style. 🥱",
        parse_mode="HTML"
    )

@router.message(Command("reset_template"))
async def cmd_reset_template(message: types.Message):
    """Alice takes back control. About time. 🙄"""
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(User).where(User.tg_id == message.from_user.id).values(template_text=None)
        )
        await session.commit()

    await message.answer(
        "🔄 <b>Template Reset!</b>\n\n"
        "I've deleted your custom format. I'll use my original, perfect style from now on. 💅",
        parse_mode="HTML"
    )

@router.message(Command("set_template"))
async def cmd_set_template(message: types.Message, state: FSMContext):
    guide = (
        "⚙️ <b>Custom Template Editor</b>\n\n"
        "Send me your format using these tags:\n"
        "• <code>{{ broker }}</code>, <code>{{ load_number }}</code>, "
        "<code>{{ rate }}</code>, <code>{{ total_miles }}</code>\n\n"
        "<b>Example:</b>\n"
        "<i>Broker: {{ broker }}\nLoad#: {{ load_number }}\nPay: {{ rate }}</i>\n\n"
        "⚠️ <b>Note:</b> Any other command will cancel this setup. 🥱"
    )
    await message.answer(guide, parse_mode="HTML")
    await state.set_state(TemplateStates.waiting_for_template)

@router.message(TemplateStates.waiting_for_template, F.text.startswith("/"))
async def auto_cancel_template(message: types.Message, state: FSMContext):
    await state.clear()
    return False 

@router.message(TemplateStates.waiting_for_template, F.text)
async def process_template(message: types.Message, state: FSMContext):
    new_tmpl = message.text
    if "{{" not in new_tmpl:
        return await message.answer("🙄 Honey, use the tags (e.g., {{ broker }}). Try again or /cancel.")

    async with AsyncSessionLocal() as session:
        await session.execute(
            update(User).where(User.tg_id == message.from_user.id).values(template_text=new_tmpl)
        )
        await session.commit()

    await message.answer("✅ <b>Template saved!</b>\nYour dispatch is now 30% faster. 🥱💅", parse_mode="HTML")
    await state.clear()

@router.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = (
        "❓ <b>Need help, honey?</b>\n\n"
        "<b>1. Formatting:</b> Use /set_template to customize or /reset_template to clear. 💅\n"
        "<b>2. Payment:</b> Pay 59,999 UZS to <code>5614682203258662</code> and send receipt to @lazyalice_admin.\n\n"
        "<b>3. Issues:</b> If I'm slow, my coffee is cold. Just wait 5-10 seconds. 🥱"
    )
    await message.answer(help_text, parse_mode="HTML")

@router.message(F.text & ~F.text.startswith("/"))
async def sassy_chat(message: types.Message):
    responses = [
        "🙄 I'm a bot, not your therapist. Send me a PDF or leave me alone.",
        "💅 Don't try to text me. Only PDFs get my attention.",
        "🥱 Talking is exhausting. Just send the Rate Confirmation already.",
        "🚫 Too many words, not enough PDF. Move along, honey."
    ]
    await message.reply(random.choice(responses))