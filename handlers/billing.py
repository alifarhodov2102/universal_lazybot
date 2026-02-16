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
    # Prices for the automated Telegram Stars plan
    prices = [LabeledPrice(label="Pro Plan (30 days)", amount=250)]
    
    # Updated description with proper HTML bolding and copyable card code
    description = (
        "✨ <b>Alice's Premium Access</b> ✨\n\n"
        "Choose your lazy way to pay:\n"
        "💰 <b>Price:</b> 250 Stars OR <b>59,999 UZS</b> / month\n\n"
        "✅ Unlimited RC extractions\n"
        "✅ Custom output templates\n"
        "✅ Full OCR & AI priority support\n\n"
        "💳 <b>Manual Card Payment:</b>\n"
        "<code>5614682203258662</code> (Click to copy)\n"
        "⚠️ <i>Send the receipt to @lazyalice_admin after paying.</i>\n\n"
        "Click 'Pay with Stars' for instant activation, or follow the card instructions. 🥱💅"
    )

    # Hybrid Keyboard: Automated Stars + Manual Card Link
    kb = InlineKeyboardMarkup(inline_keyboard=[
        # Pay button MUST be first and have pay=True for invoice to work
        [InlineKeyboardButton(text="✨ Pay with 250 Stars", pay=True)],
        [InlineKeyboardButton(text="💳 Pay 59,999 UZS (Via Card)", url="https://t.me/lazyalice_admin")]
    ])

    # Alice added the internal parser to make sure your bolding actually works 🥱
    await message.answer_invoice(
        title="Lazy Alice Pro Access",
        description=description,
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
    """Alice confirms you have enough stars... reluctantly. 🥱"""
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
        f"Valid until: <b>{expire_at.strftime('%d.%m.%Y')}</b> 💅"
    )
    # Ensure parse_mode is set to HTML here too!
    await message.answer(success_text, parse_mode="HTML")

@router.message(Command("status"))
async def check_status(message: types.Message):
    """Checking if Alice still considers you a VIP 🥱"""
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.tg_id == message.from_user.id)
        res = await session.execute(stmt)
        user = res.scalar_one_or_none()

    if user and user.is_pro:
        if user.expiry_date and user.expiry_date < datetime.utcnow():
            status = "🚫 <b>Expired</b> (Time to pay Alice again, honey 💅)"
        else:
            status = f"✅ <b>Pro</b> (Until: {user.expiry_date.strftime('%d.%m.%Y')})"
    else:
        status = f"🆓 <b>Free</b> ({user.free_uses if user else 0} remaining)"

    await message.answer(f"❤️ <b>Current Status:</b> {status}", parse_mode="HTML")