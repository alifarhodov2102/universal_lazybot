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
    """Alice presents the toll for her services 💅"""
    # 1. Info message with updated pricing ($5)
    plan_details = (
        "✨ <b>Alice's Premium Access</b> ✨\n\n"
        "💰 <b>Price:</b> 250 Stars OR <b>$5</b> / month\n\n"
        "✅ <b>Unlimited</b> daily RC extractions\n"
        "✅ AI-Learned Custom Templates\n"
        "✅ Full OCR & Priority AI processing\n\n"
        "💳 <b>Manual Card Payment (Visa):</b>\n"
        "<code>4231200092181873</code> (Click to copy)\n\n"
        "👤 <b>Name on card:</b> Ali Farhodov (Alice's Creator 👨‍💻)\n\n"
        "🎀 <b>Customer Support:</b> Alice (Female Admin 💅)\n"
        "⚠️ <i>Send the receipt to @lazyalice_admin after paying for manual activation.</i>"
    )
    
    await message.answer(plan_details, parse_mode="HTML")

    # 2. Automated invoice for Telegram Stars
    # 250 Stars = ~ $5.00 roughly matching your UZS price
    prices = [LabeledPrice(label="Pro Plan (30 days)", amount=250)]
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        # Pay button MUST be first and have pay=True
        [InlineKeyboardButton(text="✨ Pay with 250 Stars", pay=True)],
        [InlineKeyboardButton(text="📩 Send Receipt to Admin", url="https://t.me/lazyalice_admin")]
    ])

    await message.answer_invoice(
        title="Lazy Alice Pro Access",
        description="Instant activation for 30 days of unlimited use.",
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
        # Update user to Pro status immediately upon successful Star payment
        stmt = (
            update(User)
            .where(User.tg_id == tg_id)
            .values(is_pro=True, expiry_date=expire_at)
        )
        await session.execute(stmt)
        await session.commit()

    success_text = (
        "❤️ <b>Alice is impressed!</b> ❤️\n\n"
        "Your automated payment was successful. Pro status is active.\n"
        "Valid until: <b>" + expire_at.strftime('%d.%m.%Y') + "</b> 💅"
    )
    await message.answer(success_text, parse_mode="HTML")

@router.message(Command("status"))
async def check_status(message: types.Message):
    """Checking your subscription status and daily usage 🥱"""
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.tg_id == message.from_user.id)
        res = await session.execute(stmt)
        user = res.scalar_one_or_none()

    if not user:
        return await message.answer("🙄 Use /start first, honey.")

    today = date.today()
    
    if user.is_pro:
        # 1. Check if Pro has expired
        if user.expiry_date and user.expiry_date < datetime.utcnow():
            status = "🚫 <b>Expired</b> (Time to pay Alice again, honey 💅)"
        else:
            status = f"✅ <b>Pro</b> (Unlimited RCs until: <b>{user.expiry_date.strftime('%d.%m.%Y')}</b>)"
    else:
        # 2. Free User: Calculate daily limit remaining
        # Reset visual counter in status if it's a new day
        current_daily = user.daily_requests if user.last_request_date == today else 0
        left = max(0, 10 - current_daily)
        status = f"🆓 <b>Free Plan</b> (<b>{left}/10</b> left today)"

    await message.answer(f"❤️ <b>Current Status:</b>\n{status}", parse_mode="HTML")
