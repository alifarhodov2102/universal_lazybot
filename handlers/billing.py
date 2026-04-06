import os
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.types import LabeledPrice, PreCheckoutQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import update, select
from datetime import datetime, timedelta, date

from database.connection import AsyncSessionLocal
from database.models import User

router = Router()

@router.message(Command("plans"))
async def show_plans(message: types.Message):
    """Alice presents the new budget-friendly toll with FULL POWER 💸💅"""
    # 1. Info message for the new $3 / 150 Stars Plan
    plan_details = (
        "✨ <b>Alice's Premium Access</b> ✨\n\n"
        "💰 <b>Price:</b> 150 Stars OR <b>$3</b> / month\n\n"
        "✅ <b>Unlimited</b> RC extractions (No limits)\n"
        "✅ AI-Learned Custom Templates\n"
        "✅ <b>Full OCR</b> (Alice reads images & scans)\n"
        "✅ Priority AI Analysis\n\n"
        "💳 <b>Manual Card Payment (Visa):</b>\n"
        "<code>4231200092181873</code> (Click to copy)\n\n"
        "👤 <b>Name on card:</b> Ali Farhodov 👨‍💻\n\n"
        "🎀 <b>Customer Support:</b> Alice (Female Admin 💅)\n"
        "⚠️ <i>Send the receipt to @lazyalice_admin after paying for manual activation.</i>"
    )
    
    await message.answer(plan_details, parse_mode="HTML")

    # 2. Automated invoice for Telegram Stars (Updated to 150)
    prices = [LabeledPrice(label="Pro Plan (30 days)", amount=150)]
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✨ Pay with 150 Stars", pay=True)],
        [InlineKeyboardButton(text="📩 Send Receipt to Admin", url="https://t.me/lazyalice_admin")]
    ])

    await message.answer_invoice(
        title="Lazy Alice Pro Access",
        description="Instant activation for 30 days of premium power.",
        payload="pro_sub_30d",
        provider_token="", # Empty for Stars
        currency="XTR",
        prices=prices,
        start_parameter="pro-sub",
        reply_markup=kb,
        protect_content=True
    )

@router.pre_checkout_query()
async def process_pre_checkout(query: PreCheckoutQuery):
    """Alice confirms you have the stars... reluctantly. 🥱"""
    await query.answer(ok=True)

@router.message(F.successful_payment)
async def on_successful_payment(message: types.Message):
    """Automatic activation for Stars payments"""
    tg_id = message.from_user.id
    expire_at = datetime.utcnow() + timedelta(days=30)

    async with AsyncSessionLocal() as session:
        # Update user to Pro status immediately
        stmt = (
            update(User)
            .where(User.tg_id == tg_id)
            .values(is_pro=True, expiry_date=expire_at)
        )
        await session.execute(stmt)
        await session.commit()

    success_text = (
        "❤️ <b>Alice is impressed!</b> ❤️\n\n"
        "Your payment was successful. Pro status is active.\n"
        "Valid until: <b>" + expire_at.strftime('%d.%m.%Y') + "</b> 💅"
    )
    await message.answer(success_text, parse_mode="HTML")

@router.message(Command("status"))
async def check_status(message: types.Message):
    """Checking your subscription status 🥱"""
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.tg_id == message.from_user.id)
        res = await session.execute(stmt)
        user = res.scalar_one_or_none()

    if not user:
        return await message.answer("🙄 Use /start first, honey.")

    if user.is_pro:
        # 1. Check if Pro has expired
        now = datetime.utcnow()
        expiry = user.expiry_date
        
        # Safe timezone handling for comparison
        if expiry and getattr(expiry, "tzinfo", None):
            expiry = expiry.replace(tzinfo=None)

        if expiry and expiry < now:
            status = "🚫 <b>Expired</b> (Time to pay Alice again, honey 💅)"
        else:
            status = f"✅ <b>Pro Active</b> (Unlimited RCs until: <b>{user.expiry_date.strftime('%d.%m.%Y')}</b>)"
    else:
        # 2. Non-Pro User
        status = "🆓 <b>Free Account</b> (Subscription required to process PDFs) 💳"

    await message.answer(f"❤️ <b>Current Status:</b>\n{status}", parse_mode="HTML")
