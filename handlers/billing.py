from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.types import LabeledPrice, PreCheckoutQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import update
from datetime import datetime, timedelta

from database.connection import AsyncSessionLocal
from database.models import User

router = Router()

@router.message(Command("plans"))
async def show_plans(message: types.Message):
    # 250 Stars uchun narxni shakllantiramiz
    # Stars uchun 1 unit = 1 star, shuning uchun amount=250
    prices = [LabeledPrice(label="Pro Plan (30 kun)", amount=250)]
    
    # To'lov haqida ma'lumot beruvchi chiroyli matn
    description = (
        "🚀 **LazyBot Pro imkoniyatlari:**\n\n"
        "✅ 30 kun davomida cheksiz RC tahlili\n"
        "✅ Shaxsiy chiqish formati (Template)\n"
        "✅ OCR (Skanerlangan PDF-larni o'qish)\n"
        "✅ DeepSeek AI aqlli extraction"
    )

    await message.answer_invoice(
        title="LazyBot Pro Subscription",
        description=description,
        payload="pro_sub_30d",
        provider_token="", # Stars uchun bo'sh qoldiriladi
        currency="XTR",
        prices=prices,
        start_parameter="pro-sub",
        protect_content=True
    )

@router.pre_checkout_query()
async def process_pre_checkout(query: PreCheckoutQuery):
    """
    To'lov tugmasi bosilganda Telegram 10 soniya ichida javob kutadi.
    Bu yerda biz to'lovni tasdiqlaymiz.
    """
    await query.answer(ok=True)

@router.message(F.successful_payment)
async def on_successful_payment(message: types.Message):
    """
    To'lov muvaffaqiyatli amalga oshirilgandan keyin ishlaydi.
    """
    tg_id = message.from_user.id
    # 30 kunga amal qilish muddatini belgilash
    expire_at = datetime.utcnow() + timedelta(days=30)

    async with AsyncSessionLocal() as session:
        # Userni Pro holatiga o'tkazish
        stmt = (
            update(User)
            .where(User.tg_id == tg_id)
            .values(is_pro=True, expiry_date=expire_at)
        )
        await session.execute(stmt)
        await session.commit()

    success_text = (
        "✅ **To'lov muvaffaqiyatli amalga oshirildi!**\n\n"
        "Sizning Pro obunangiz faollashtirildi.\n"
        f"Amal qilish muddati: `{expire_at.strftime('%d.%m.%Y')}` gacha.\n\n"
        "Endi cheksiz miqdorda RC yuborishingiz mumkin!"
    )
    await message.answer(success_text, parse_mode="Markdown")

@router.message(Command("status"))
async def check_status(message: types.Message):
    """
    Foydalanuvchi o'z balansini va muddatini tekshirishi uchun.
    """
    from sqlalchemy import select
    
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.tg_id == message.from_user.id)
        res = await session.execute(stmt)
        user = res.scalar_one_or_none()

    if user.is_pro:
        status = f"✅ Pro (Muddat: {user.expiry_date.strftime('%d.%m.%Y')})"
    else:
        status = f"🆓 Bepul ({user.free_uses} ta urinish qoldi)"

    await message.answer(f"Sizning joriy holatingiz: **{status}**", parse_mode="Markdown")