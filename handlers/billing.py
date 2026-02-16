import os
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.types import LabeledPrice, PreCheckoutQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import update, select
from datetime import datetime, timedelta

from database.connection import AsyncSessionLocal
from database.models import User

router = Router()

@router.message(Command("plans"))
async def show_plans(message: types.Message):
    # 1. Alice sends a regular message first — formatting here ALWAYS works 💅
    plan_details = (
        "✨ <b>Alice's Premium Access</b> ✨\n\n"
        "💰 <b>Price:</b> 250 Stars OR <b>59,999 UZS</b> / month\n\n"
        "✅ Unlimited RC extractions\n"
        "✅ Custom output templates\n"
        "✅ Full OCR & AI priority support\n\n"
        "💳 <b>Manual Card Payment:</b>\n"
        "<code>5614682203258662</code> (Click to copy)\n\n"
        "⚠️ <i>Send the receipt to @lazyalice_admin after paying.</i>"
    )
    
    await message.answer(plan_details, parse_mode="HTML")

    # 2. Then Alice sends the actual invoice with a clean, plain description 🥱
    prices = [LabeledPrice(label="Pro Plan (30 days)", amount=250)]
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        # Pay button MUST be first and have pay=True
        [InlineKeyboardButton(text="✨ Pay with 250 Stars", pay=True)],
        [InlineKeyboardButton(text="📩 Send Receipt to Admin", url="https://t.me/lazyalice_admin")]
    ])

    await message.answer_invoice(
        title="Lazy Alice Pro Access",
        description="Instant activation via Telegram Stars (250 XTR)",
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
    """Checking your subscription status 🥱"""
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.tg_id == message.from_user.id)
        res = await session.execute(stmt)
        user = res.scalar_one_or_none()

    if user and user.is_pro:
        if user.expiry_date and user.expiry_date < datetime.utcnow():
            status = "🚫 <b>Expired</b> (Time to pay Alice again, honey 💅)"
        else:
            status = "✅ <b>Pro</b> (Until: " + user.expiry_date.strftime('%d.%m.%Y') + ")"
    else:
        status = "🆓 <b>Free</b> (" + str(user.free_uses if user else 0) + " left)"

    await message.answer(f"❤️ <b>Current Status:</b> {status}", parse_mode="HTML")
    