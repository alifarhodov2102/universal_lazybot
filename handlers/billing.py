import logging
from datetime import date, datetime, timedelta
from typing import Optional

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    PreCheckoutQuery,
)
from sqlalchemy import select

from database.connection import AsyncSessionLocal
from database.models import User


logger = logging.getLogger("LazyAlice.Billing")
router = Router()


# ============================================================
# Plan configuration
# ============================================================

PRO_PLAN_PAYLOAD = "pro_sub_30d"
PRO_PLAN_PRICE_STARS = 150
PRO_PLAN_DAYS = 30


def _normalize_datetime(
    value: Optional[datetime],
) -> Optional[datetime]:
    """
    Convert timezone-aware database values to naive datetime values.

    The current User model stores expiry_date as a normal DateTime.
    """
    if value is None:
        return None

    if value.tzinfo is not None:
        return value.replace(tzinfo=None)

    return value


# ============================================================
# Plans and invoice
# ============================================================

@router.message(Command("plans"))
async def show_plans(
    message: types.Message,
) -> None:
    plan_details = (
        "✨ <b>Alice's Premium Access</b> ✨\n\n"
        "⭐ <b>Telegram Stars:</b> 150 Stars / 30 days\n"
        "💳 <b>Manual card payment:</b> $3 / 30 days\n\n"
        "✅ <b>Unlimited</b> Rate Confirmation extractions\n"
        "✅ AI-learned custom templates\n"
        "✅ Full OCR for images and scanned PDFs\n"
        "✅ Reefer temperature extraction\n"
        "✅ Priority AI analysis\n\n"
        "💳 <b>Manual Card Payment:</b>\n"
        "<code>5614682203258662</code>\n\n"
        "👤 <b>Name on card:</b> Ali Farhodov\n\n"
        "⚠️ <i>For manual activation, send the payment "
        "receipt to @lazyalice_admin.</i>"
    )

    await message.answer(
        plan_details,
        parse_mode="HTML",
    )

    prices = [
        LabeledPrice(
            label="Pro Plan — 30 days",
            amount=PRO_PLAN_PRICE_STARS,
        )
    ]

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✨ Pay with 150 Stars",
                    pay=True,
                )
            ],
            [
                InlineKeyboardButton(
                    text="📩 Send Receipt to Admin",
                    url="https://t.me/lazyalice_admin",
                )
            ],
        ]
    )

    await message.answer_invoice(
        title="Lazy Alice Pro Access",
        description=(
            "30 days of unlimited Rate Confirmation processing."
        ),
        payload=PRO_PLAN_PAYLOAD,

        # Empty provider token is used by aiogram for Stars.
        provider_token="",

        currency="XTR",
        prices=prices,
        start_parameter="pro-sub",
        reply_markup=keyboard,
        protect_content=True,
    )


# ============================================================
# Pre-checkout validation
# ============================================================

@router.pre_checkout_query()
async def process_pre_checkout(
    query: PreCheckoutQuery,
) -> None:
    """
    Validate the invoice before allowing Telegram to complete payment.
    """
    is_valid = (
        query.invoice_payload == PRO_PLAN_PAYLOAD
        and query.currency == "XTR"
        and query.total_amount == PRO_PLAN_PRICE_STARS
    )

    if not is_valid:
        logger.error(
            "Rejected invalid pre-checkout request: "
            "user=%s payload=%r currency=%r amount=%r",
            query.from_user.id,
            query.invoice_payload,
            query.currency,
            query.total_amount,
        )

        await query.answer(
            ok=False,
            error_message=(
                "This invoice is invalid or has expired. "
                "Please return to the bot and use /plans again."
            ),
        )

        return

    logger.info(
        "Pre-checkout approved: user=%s payload=%s amount=%s %s",
        query.from_user.id,
        query.invoice_payload,
        query.total_amount,
        query.currency,
    )

    await query.answer(
        ok=True,
    )


# ============================================================
# Successful Stars payment
# ============================================================

@router.message(F.successful_payment)
async def on_successful_payment(
    message: types.Message,
) -> None:
    """
    Automatically create or update the user after a valid Stars payment.
    """
    payment = message.successful_payment

    if not payment or not message.from_user:
        logger.error(
            "Successful payment handler received incomplete message."
        )
        return

    tg_id = message.from_user.id

    logger.info(
        "Successful Stars payment received: "
        "user=%s payload=%s currency=%s amount=%s charge_id=%s",
        tg_id,
        payment.invoice_payload,
        payment.currency,
        payment.total_amount,
        payment.telegram_payment_charge_id,
    )

    # Never activate Pro for an unexpected invoice.
    is_valid_payment = (
        payment.invoice_payload == PRO_PLAN_PAYLOAD
        and payment.currency == "XTR"
        and payment.total_amount == PRO_PLAN_PRICE_STARS
    )

    if not is_valid_payment:
        logger.error(
            "Payment received with invalid invoice information: "
            "user=%s payload=%r currency=%r amount=%r",
            tg_id,
            payment.invoice_payload,
            payment.currency,
            payment.total_amount,
        )

        await message.answer(
            "⚠️ <b>Payment verification problem</b>\n\n"
            "Your payment reached the bot, but the invoice information "
            "did not match the Pro plan.\n\n"
            "Please contact @lazyalice_admin.",
            parse_mode="HTML",
        )

        return

    now = datetime.utcnow()

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(User)
                    .where(User.tg_id == tg_id)
                    .with_for_update()
                )

                user = result.scalar_one_or_none()

                # Critical fix:
                # Create the user when no database row exists.
                if user is None:
                    user = User(
                        tg_id=tg_id,
                        username=message.from_user.username,
                        weekly_requests=0,
                        last_request_date=date.today(),
                        is_pro=False,
                    )

                    session.add(user)

                    logger.info(
                        "Created missing database user during payment: %s",
                        tg_id,
                    )

                else:
                    # Update the latest Telegram username.
                    user.username = message.from_user.username

                current_expiry = _normalize_datetime(
                    user.expiry_date
                )

                # Extend an active subscription instead of discarding
                # the remaining paid days.
                if (
                    user.is_pro
                    and current_expiry
                    and current_expiry > now
                ):
                    expire_at = (
                        current_expiry
                        + timedelta(days=PRO_PLAN_DAYS)
                    )

                else:
                    expire_at = (
                        now
                        + timedelta(days=PRO_PLAN_DAYS)
                    )

                user.is_pro = True
                user.expiry_date = expire_at

        logger.info(
            "Pro subscription activated: user=%s expiry=%s charge_id=%s",
            tg_id,
            expire_at.isoformat(),
            payment.telegram_payment_charge_id,
        )

    except Exception:
        logger.exception(
            "Database activation failed after successful payment: "
            "user=%s charge_id=%s",
            tg_id,
            payment.telegram_payment_charge_id,
        )

        await message.answer(
            "⚠️ <b>Your payment was successful, but automatic "
            "activation failed.</b>\n\n"
            "Your payment information has been recorded in the bot logs.\n"
            "Please contact @lazyalice_admin for activation.",
            parse_mode="HTML",
        )

        return

    success_text = (
        "❤️ <b>Payment successful!</b>\n\n"
        "Your Pro subscription is now active.\n\n"
        f"⭐ Paid: <b>{payment.total_amount} Stars</b>\n"
        f"📅 Valid until: "
        f"<b>{expire_at.strftime('%d.%m.%Y')}</b>\n\n"
        "You can now send a Rate Confirmation PDF."
    )

    await message.answer(
        success_text,
        parse_mode="HTML",
    )


# ============================================================
# Subscription status
# ============================================================

@router.message(Command("status"))
async def check_status(
    message: types.Message,
) -> None:
    if not message.from_user:
        return

    tg_id = message.from_user.id
    now = datetime.utcnow()

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(
                User.tg_id == tg_id
            )
        )

        user = result.scalar_one_or_none()

        if not user:
            await message.answer(
                "No account was found.\n\n"
                "Use /start first.",
                parse_mode="HTML",
            )
            return

        expiry = _normalize_datetime(
            user.expiry_date
        )

        if (
            user.is_pro
            and expiry
            and expiry > now
        ):
            status = (
                "✅ <b>Pro Active</b>\n"
                f"Valid until: "
                f"<b>{expiry.strftime('%d.%m.%Y')}</b>\n"
                "Access: <b>Unlimited Rate Confirmations</b>"
            )

        elif (
            user.is_pro
            and expiry
            and expiry <= now
        ):
            user.is_pro = False
            await session.commit()

            status = (
                "🚫 <b>Subscription Expired</b>\n"
                "Use /plans to renew your Pro access."
            )

        else:
            status = (
                "🆓 <b>Free Account</b>\n"
                "A Pro subscription is required to process PDFs.\n\n"
                "Use /plans to subscribe."
            )

    await message.answer(
        f"❤️ <b>Current Status</b>\n\n{status}",
        parse_mode="HTML",
    )
