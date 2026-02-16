import os
from aiogram import Router, types, F, Bot
from aiogram.filters import Command
from aiogram.types import LabeledPrice, PreCheckoutQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import update, select
from datetime import datetime, timedelta

from database.connection import AsyncSessionLocal
from database.models import User

router = Router()

@router.message(Command("plans"))
async def show_plans(message: types.Message):
    # 250 Stars for the global automated plan
    prices = [LabeledPrice(label="Pro Plan (30 days)", amount=250)]
    
    description = (
        "🚀 **Alice's Pro Subscription Perks:**\n\n"
        "✅ Unlimited RC extractions for 30 days\n"
        "✅ Custom output formats (Templates)\n"
        "✅ OCR support (I'll read your messy scans)\n"
        "✅ Premium DeepSeek AI priority processing\n\n"
        "<i>Pay with Stars below or use the card option if you are in Uzbekistan.</i>"
    )

    # Manual payment button for card transfers
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Pay via Card (UZS/USD)", url="https://t.me/lazyalice_admin")]
    ])

    await message.answer_invoice(
        title="Lazy Alice Pro Subscription",
        description=description,
        payload="pro_sub_30d",
        provider_token="", # Empty for Telegram Stars
        currency="XTR",
        prices=prices,
        start_parameter="pro-sub",
        reply_markup=kb, # Added the card link button here
        protect_content=True
    )

@router.pre_checkout_query()
async def process_pre_checkout(query: PreCheckoutQuery):
    """Alice confirms the transaction instantly 💅"""
    await query.answer(ok=True)

@router.message(F.successful_payment)
async def on_successful_payment(message: types.Message):
    """Alice notices you paid with Stars and reluctantly starts working. 🥱"""
    tg_id = message.from_user.id
    expire_at = datetime.utcnow() + timedelta(days=30)

    async with AsyncSessionLocal() as session:
        stmt = (
            update(User)
            .where(User.tg_id == tg_id)
            .values(is_pro=True, expiry_date=expire_at)
        )
        await session.execute(stmt)
        await session.commit()

    success_text = (
        "✨ **Alice is impressed! Payment Successful!**\n\n"
        "Your Pro status is now active.\n"
        f"Valid until: ` {expire_at.strftime('%d.%m.%Y')} `\n\n"
        "Now go ahead and spam me with those PDFs. I'm ready (I guess). 💅"
    )
    await message.answer(success_text, parse_mode="HTML")

@router.message(Command("status"))
async def check_status(message: types.Message):
    """Check if Alice still thinks you're a VIP 🥱"""
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.tg_id == message.from_user.id)
        res = await session.execute(stmt)
        user = res.scalar_one_or_none()

    if user and user.is_pro:
        # Check if expired
        if user.expiry_date and user.expiry_date < datetime.utcnow():
            status = "🚫 Expired (Time to pay again, honey 💅)"
        else:
            status = f"✅ Pro (Until: {user.expiry_date.strftime('%d.%m.%Y')})"
    else:
        status = f"🆓 Free ({user.free_uses if user else 0} left)"

    await message.answer(f"❤️ <b>Current Status:</b> {status}", parse_mode="HTML")