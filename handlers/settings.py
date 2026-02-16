from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from sqlalchemy import update, select
import httpx
import json
import os

from database.connection import AsyncSessionLocal
from database.models import User
from utils.states import SettingsStates
from config import DEEPSEEK_API_KEY, DEEPSEEK_URL

router = Router()

async def ai_parse_template(example_text: str) -> str:
    """
    Foydalanuvchi yuborgan oddiy namunani DeepSeek orqali 
    Jinja2 shabloniga aylantirib beradi.
    """
    if not DEEPSEEK_API_KEY:
        return None

    prompt = f"""
Siz logistika botining yordamchisisiz. Foydalanuvchi o'ziga kerakli formatda namuna yubordi. 
Ushbu namunani bot uchun Jinja2 shabloniga aylantirib bering.

Qoidalari:
1. Broker nomi o'rniga {{ broker }} qo'ying.
2. Load ID o'rniga {{ load_number }} qo'ying.
3. Narx (Rate) o'rniga {{ rate }} qo'ying.
4. Jami masofa o'rniga {{ total_miles }} qo'ying.
5. PU/DEL bloklari uchun faqat bitta namunani oling va uni tsikl ichiga joylang:
   Pickup uchun: {{% for p in pickups %}} ... {{% endfor %}}
   Delivery uchun: {{% for d in deliveries %}} ... {{% endfor %}}
6. Har bir stop ichida {{ p.facility }}, {{ p.address }}, {{ p.time }} o'zgaruvchilaridan foydalaning.
7. Pastdagi o'zgarmas qoidalar va belgilarga tegmang.

USER NAMUNASI:
{example_text}

FAQAT TAYYOR JINJA2 KODINI QAYTARING:
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
        except:
            return None

@router.message(Command("settings"))
async def show_settings(message: types.Message):
    text = (
        "⚙️ **Sozlamalar bo'limi**\n\n"
        "Quyidagi buyruqlardan foydalaning:\n"
        "/set_template - O'z namunangizni tashlash\n"
        "/my_template - Hozirgi formatni ko'rish\n"
        "/reset_template - Standartga qaytish"
    )
    await message.answer(text, parse_mode="Markdown")

@router.message(Command("set_template"))
async def start_set_template(message: types.Message, state: FSMContext):
    text = (
        "📝 **O'zingizga yoqadigan namunani (Example) yuboring.**\n\n"
        "Masalan, avval tayyorlab qo'ygan RC matningizni shunchaki nusxalab tashlang. "
        "AI uni avtomatik ravishda shablonga aylantiradi.\n\n"
        "**Hech qanday maxsus kod yozish shart emas!**"
    )
    await message.answer(text, parse_mode="Markdown")
    await state.set_state(SettingsStates.waiting_for_template)

@router.message(SettingsStates.waiting_for_template, F.text)
async def save_template(message: types.Message, state: FSMContext):
    user_example = message.text
    tg_id = message.from_user.id
    
    wait_msg = await message.answer("🔄 Namuna tahlil qilinmoqda...")

    # AI orqali namunani shablonga o'giramiz
    smart_template = await ai_parse_template(user_example)
    
    # Agar AI xato qilsa yoki balans bo'lmasa, matnni o'zini saqlaymiz (yoki xato beramiz)
    final_template = smart_template if smart_template else user_example

    async with AsyncSessionLocal() as session:
        stmt = update(User).where(User.tg_id == tg_id).values(template_text=final_template)
        await session.execute(stmt)
        await session.commit()

    await wait_msg.delete()
    await message.answer(
        "✅ **Ajoyib!**\n\n"
        "Siz yuborgan namuna asosida aqlli shablon yaratildi. "
        "Endi barcha PDF hujjatlar aynan shu ko'rinishda chiqariladi."
    )
    await state.clear()

@router.message(Command("my_template"))
async def show_current_template(message: types.Message):
    async with AsyncSessionLocal() as session:
        stmt = select(User.template_text).where(User.tg_id == message.from_user.id)
        result = await session.execute(stmt)
        current_tmpl = result.scalar_one_or_none()
        
        if current_tmpl:
            await message.answer(f"Sizning joriy shabloningiz:\n\n`{current_tmpl}`", parse_mode="Markdown")
        else:
            await message.answer("Siz hali shaxsiy shablon o'rnatmagansiz (standart rejim).")