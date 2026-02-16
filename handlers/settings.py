import os
import re
import json
import httpx
import logging
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from sqlalchemy import update, select

from database.connection import AsyncSessionLocal
from database.models import User
from utils.states import SettingsStates
from config import DEEPSEEK_API_KEY, DEEPSEEK_URL

router = Router()
logger = logging.getLogger("Settings")

async def ai_parse_template(example_text: str) -> str:
    """
    Alice uses DeepSeek to turn a user's plain-text example into a 
    fully functional Jinja2 template. 🥱💅
    """
    if not DEEPSEEK_API_KEY:
        return None

    prompt = f"""
You are a US Logistics Bot Assistant. A user provided a text example of how they want their Rate Confirmation data to look. 
Convert this example into a clean Jinja2 template for a bot.

Rules:
1. Replace Broker Name with {{ broker }}
2. Replace Load ID/Number with {{ load_number }}
3. Replace Money/Pay with {{ rate }}
4. Replace Total Miles with {{ total_miles }}
5. For Pickup blocks, use: {{% for p in pickups %}} ... {{ p.facility }}, {{ p.address }}, {{ p.time }} ... {{% endfor %}}
6. For Delivery blocks, use: {{% for d in deliveries %}} ... {{ d.facility }}, {{ d.address }}, {{ d.time }} ... {{% endfor %}}
7. Maintain the user's emojis and spacing exactly.

USER EXAMPLE:
{example_text}

OUTPUT ONLY THE CLEAN JINJA2 CODE:
"""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                DEEPSEEK_URL,
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "system", "content": "You output only clean Jinja2 code based on examples."},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.1
                },
                timeout=30.0
            )
            result = response.json()
            return result['choices'][0]['message']['content'].strip()
        except Exception as e:
            logger.error(f"AI Template Error: {e}")
            return None

@router.message(Command("settings"))
async def show_settings(message: types.Message):
    """Alice shows you the control panel. Try not to break anything. 🥱"""
    text = (
        "⚙️ <b>Alice's Control Panel</b>\n\n"
        "Configure how I work for you:\n\n"
        "🛠 <b>/set_template</b> - Teach me your preferred format\n"
        "📋 <b>/my_template</b> - See your current active format\n"
        "🔄 <b>/reset_template</b> - Go back to Alice's default style\n\n"
        "<i>Save 30% of your time by matching your dispatcher's style perfectly.</i> 💅"
    )
    await message.answer(text, parse_mode="HTML")

@router.message(Command("set_template"))
async def start_set_template(message: types.Message, state: FSMContext):
    """Alice starts the 'copy-paste' learning process 💅"""
    text = (
        "📝 <b>Teach Me Your Style!</b>\n\n"
        "Simply paste an example of a perfectly formatted load message (one you've sent to your group before).\n\n"
        "My AI will analyze it and create a custom template for you automatically. "
        "<b>No coding required!</b> 🥱💅"
    )
    await message.answer(text, parse_mode="HTML")
    await state.set_state(SettingsStates.waiting_for_template)

@router.message(SettingsStates.waiting_for_template, F.text)
async def save_template(message: types.Message, state: FSMContext):
    """Alice processes the example and saves the logic 🧠"""
    user_example = message.text
    tg_id = message.from_user.id
    
    wait_msg = await message.answer("🔄 <i>Alice is thinking... analyzing your style...</i> 💅")

    # AI Magic happens here
    smart_template = await ai_parse_template(user_example)
    
    # Fallback if AI is sleeping or broken
    final_template = smart_template if smart_template else user_example

    async with AsyncSessionLocal() as session:
        stmt = update(User).where(User.tg_id == tg_id).values(template_text=final_template)
        await session.execute(stmt)
        await session.commit()

    await wait_msg.delete()
    await message.answer(
        "✅ <b>Masterpiece Created!</b>\n\n"
        "I've learned your style. All your future PDFs will be formatted exactly like your example. "
        "You're officially <b>30% faster</b> than the other guys. 🥱💅"
    )
    await state.clear()

@router.message(Command("my_template"))
async def show_current_template(message: types.Message):
    """Checking your current setup 🥱"""
    async with AsyncSessionLocal() as session:
        stmt = select(User.template_text).where(User.tg_id == message.from_user.id)
        result = await session.execute(stmt)
        current_tmpl = result.scalar_one_or_none()
        
        if current_tmpl:
            await message.answer(
                f"📋 <b>Your Active Format:</b>\n\n<code>{current_tmpl}</code>", 
                parse_mode="HTML"
            )
        else:
            await message.answer("You are currently using <b>Alice's Default Style</b> (The best one, obviously 💅).")

@router.message(Command("reset_template"))
async def reset_user_template(message: types.Message):
    """Alice takes back the wheel 🙄"""
    async with AsyncSessionLocal() as session:
        stmt = update(User).where(User.tg_id == message.from_user.id).values(template_text=None)
        await session.execute(stmt)
        await session.commit()
    
    await message.answer("🔄 <b>Template Reset!</b>\nI'm back to my original, perfect style. 💅")