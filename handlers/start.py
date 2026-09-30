import html
import logging
from datetime import date, datetime

from aiogram import Router, types, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, update

from database.models import User
from database.connection import AsyncSessionLocal
from utils.states import TemplateStates
from services.extractor import extract_template_structure


router = Router()
logger = logging.getLogger("LazyAlice.Start")


# ============================================================
# Privacy policy
# ============================================================

PRIVACY_TEXT = (
    "🔒 <b>Lazy Alice Privacy & Data Policy</b>\n\n"

    "1. <b>No Data Retention:</b> "
    "We do not store the content of your Rate Confirmations. "
    "Once the text is extracted and processed, the document data "
    "is not saved as part of your account.\n\n"

    "2. <b>Temporary PDF Processing:</b> "
    "PDF files are used only while Alice processes your request "
    "and are removed from temporary processing storage afterward.\n\n"

    "3. <b>Security:</b> "
    "Your load data is never sold. "
    "Your Telegram ID is used for account, subscription, "
    "settings, and usage management.\n\n"

    "4. <b>AI Processing:</b> "
    "Alice uses an external AI service to analyze Rate Confirmations "
    "and create structured load information.\n\n"

    "<i>By using Alice, you agree to these terms.</i> 💅🥱"
)


# ============================================================
# Helpers
# ============================================================

async def _get_or_create_user(
    message: types.Message,
) -> User:
    """
    Make sure a Telegram user exists in the database.

    This allows commands such as /set_template to work even if,
    for some reason, the user did not complete /start first.
    """
    tg_id = message.from_user.id

    async with AsyncSessionLocal() as session:
        stmt = select(User).where(
            User.tg_id == tg_id
        )

        result = await session.execute(
            stmt
        )

        user = result.scalar_one_or_none()

        if user:
            # Keep Telegram username reasonably fresh.
            current_username = message.from_user.username

            if (
                current_username
                and getattr(user, "username", None)
                != current_username
            ):
                user.username = current_username
                await session.commit()

            return user

        user = User(
            tg_id=tg_id,
            username=message.from_user.username,
            weekly_requests=0,
            last_request_date=date.today(),
        )

        session.add(user)
        await session.commit()
        await session.refresh(user)

        return user


# ============================================================
# /start
# ============================================================

@router.message(CommandStart())
async def cmd_start(
    message: types.Message,
):
    """
    Alice greets the user and checks subscription status.
    """
    if not message.from_user:
        return

    tg_id = message.from_user.id

    full_name = html.escape(
        message.from_user.full_name or "there",
        quote=False,
    )

    status_msg = await message.answer(
        "⏳ <b>Checking your access...</b>",
        parse_mode=ParseMode.HTML,
    )

    async with AsyncSessionLocal() as session:
        stmt = select(User).where(
            User.tg_id == tg_id
        )

        result = await session.execute(
            stmt
        )

        user = result.scalar_one_or_none()

        # ----------------------------------------------------
        # Register brand-new user
        # ----------------------------------------------------

        if not user:
            user = User(
                tg_id=tg_id,
                username=message.from_user.username,
                weekly_requests=0,
                last_request_date=date.today(),
            )

            session.add(user)

            await session.commit()
            await session.refresh(user)

        # ----------------------------------------------------
        # Keep username updated
        # ----------------------------------------------------

        elif (
            message.from_user.username
            and user.username != message.from_user.username
        ):
            user.username = message.from_user.username
            await session.commit()

        # ----------------------------------------------------
        # Check Pro subscription
        # ----------------------------------------------------

        now = datetime.utcnow()

        is_pro = False

        if (
            user.is_pro
            and user.expiry_date
        ):
            expiry = user.expiry_date

            if getattr(
                expiry,
                "tzinfo",
                None,
            ):
                expiry = expiry.replace(
                    tzinfo=None
                )

            is_pro = expiry > now

        # ----------------------------------------------------
        # Welcome message
        # ----------------------------------------------------

        if is_pro:
            welcome_text = (
                f"❤️ <b>Welcome back, {full_name}!</b>\n\n"

                "Status: <b>Pro ✅ (Unlimited)</b>\n\n"

                "Ready to work? Just drop the "
                "<b>Rate Confirmation PDF</b> here. 🥱"
            )

        else:
            welcome_text = (
                f"👋 <b>Welcome to Lazy Alice, {full_name}!</b>\n\n"

                "I turn messy US logistics Rate Confirmations "
                "into clean, ready-to-send load messages.\n\n"

                "⚠️ <b>Access Restricted</b>\n"
                "Alice is currently in <b>Premium-Only Mode</b>.\n\n"

                "Use /plans to activate your account. 💅"
            )

        # ----------------------------------------------------
        # Buttons
        # ----------------------------------------------------

        builder = InlineKeyboardBuilder()

        builder.row(
            types.InlineKeyboardButton(
                text="🔒 Privacy Policy",
                callback_data="view_privacy",
            )
        )

        if not is_pro:
            builder.row(
                types.InlineKeyboardButton(
                    text="✨ View Pro Plans",
                    callback_data="view_plans",
                )
            )

        await status_msg.edit_text(
            welcome_text,
            reply_markup=builder.as_markup(),
            parse_mode=ParseMode.HTML,
        )


# ============================================================
# Privacy callbacks
# ============================================================

@router.callback_query(
    F.data == "view_privacy"
)
async def callback_privacy(
    callback: types.CallbackQuery,
):
    if callback.message:
        await callback.message.answer(
            PRIVACY_TEXT,
            parse_mode=ParseMode.HTML,
        )

    await callback.answer()


@router.callback_query(
    F.data == "view_plans"
)
async def callback_plans(
    callback: types.CallbackQuery,
):
    """
    Open the same plans screen used by /plans.
    """
    if callback.message:
        from handlers.billing import show_plans

        await show_plans(
            callback.message
        )

    await callback.answer()


@router.message(
    Command("privacy")
)
async def cmd_privacy(
    message: types.Message,
):
    await message.answer(
        PRIVACY_TEXT,
        parse_mode=ParseMode.HTML,
    )


# ============================================================
# Template management
# ============================================================

@router.message(
    Command("set_template")
)
async def cmd_set_template(
    message: types.Message,
    state: FSMContext,
):
    """
    Begin custom-template setup.
    """
    if not message.from_user:
        return

    await _get_or_create_user(
        message
    )

    # Clear an unfinished previous setup first.
    await state.clear()

    builder = InlineKeyboardBuilder()

    builder.row(
        types.InlineKeyboardButton(
            text="❌ Cancel Setup",
            callback_data="cancel_template",
        )
    )

    guide = (
        "⚙️ <b>Teach Me Your Style!</b>\n\n"

        "Paste one previous load message formatted exactly "
        "the way you want Alice to send future loads.\n\n"

        "Alice will learn the structure, spacing, emojis, "
        "labels, and notes automatically.\n\n"

        "🌡 <b>Reefer loads are automatic.</b>\n"
        "You do not need to manually add temperature fields. "
        "If the Rate Confirmation contains a real temperature "
        "requirement, Alice will add it automatically. "
        "Dry loads stay clean.\n\n"

        "⚠️ <i>Your example must be at least 20 characters long.</i>"
    )

    await message.answer(
        guide,
        parse_mode=ParseMode.HTML,
        reply_markup=builder.as_markup(),
    )

    await state.set_state(
        TemplateStates.waiting_for_template
    )


@router.message(
    TemplateStates.waiting_for_template,
    F.text,
)
async def process_template(
    message: types.Message,
    state: FSMContext,
):
    """
    Convert the user's example into a reusable Jinja template.
    """
    if (
        not message.from_user
        or not message.text
    ):
        return

    example_text = message.text.strip()

    # --------------------------------------------------------
    # Validation
    # --------------------------------------------------------

    if (
        len(example_text) < 20
        or example_text.startswith("/")
    ):
        await message.answer(
            "🙄 I need a real example.\n\n"
            "Paste a full formatted load message."
        )

        return

    await _get_or_create_user(
        message
    )

    status_msg = await message.answer(
        "🧠 <b>Processing your template...</b>\n"
        "<i>Alice is learning your style.</i> 💅",
        parse_mode=ParseMode.HTML,
    )

    # --------------------------------------------------------
    # Template-learning instructions
    # --------------------------------------------------------

    system_prompt = (
        "Convert the user's formatted US logistics load message "
        "into a reusable Jinja2 template.\n\n"

        "AVAILABLE VARIABLES:\n"
        "{{ broker }}\n"
        "{{ load_number }}\n"
        "{{ ref_number }}\n"
        "{{ bol_number }}\n"
        "{{ pu_number }}\n"
        "{{ del_number }}\n"
        "{{ stops_info }}\n"
        "{{ weight }}\n"
        "{{ total_miles }}\n"
        "{{ rate }}\n"
        "{{ per_mile }}\n"
        "{{ duration }}\n\n"

        "STRICT RULES:\n"

        "1. Preserve the user's wording, emojis, separators, "
        "spacing, notes, and general structure.\n"

        "2. Replace all pickup and delivery sections with "
        "{{ stops_info }}. Do NOT use pickup_info or delivery_info.\n"

        "3. Replace actual broker, load number, references, weight, "
        "miles, rate, per-mile price, and duration with the "
        "appropriate variables.\n"

        "4. Do not put '$' before {{ rate }} because the renderer "
        "already formats the currency.\n"

        "5. Do not put 'mi', 'mile', or 'miles' after "
        "{{ total_miles }} because the renderer already supplies "
        "the mileage unit.\n"

        "6. Do not put 'lbs' after {{ weight }} because the renderer "
        "already supplies the weight unit.\n"

        "7. Do not invent information or sections that were not "
        "present in the user's example.\n"

        "8. Temperature support is handled automatically by Alice. "
        "Do not invent a temperature section merely because one is "
        "not present in the example.\n"

        "9. If the user's example already explicitly contains a "
        "temperature or reefer section, preserve that structure.\n"

        "10. Output only the Jinja2 template."
    )

    # --------------------------------------------------------
    # Learn and save
    # --------------------------------------------------------

    try:
        skeleton_template = (
            await extract_template_structure(
                system_prompt,
                example_text,
            )
        )

        if not skeleton_template:
            raise ValueError(
                "Template learner returned an empty template."
            )

        async with AsyncSessionLocal() as session:
            await session.execute(
                update(User)
                .where(
                    User.tg_id
                    == message.from_user.id
                )
                .values(
                    template_text=skeleton_template
                )
            )

            await session.commit()

        await status_msg.edit_text(
            "✅ <b>Template saved!</b>\n\n"

            "Alice learned your format. "
            "Future Rate Confirmations will follow your style.\n\n"

            "🌡 Reefer temperature will appear automatically "
            "when the load actually requires it.",

            parse_mode=ParseMode.HTML,
        )

    except Exception:
        logger.exception(
            "Failed to create custom template for user %s.",
            message.from_user.id,
        )

        await status_msg.edit_text(
            "🙄 <b>Something went wrong.</b>\n\n"
            "Your template was not changed. "
            "Please try /set_template again.",

            parse_mode=ParseMode.HTML,
        )

    finally:
        await state.clear()


# ============================================================
# Cancel template setup
# ============================================================

@router.callback_query(
    F.data == "cancel_template"
)
async def cancel_template(
    callback: types.CallbackQuery,
    state: FSMContext,
):
    await state.clear()

    if callback.message:
        try:
            await callback.message.edit_text(
                "🔄 <b>Template setup cancelled.</b>",
                parse_mode=ParseMode.HTML,
            )

        except Exception:
            logger.exception(
                "Failed to edit cancelled template message."
            )

    await callback.answer()


# ============================================================
# /my_template
# ============================================================

@router.message(
    Command("my_template")
)
async def cmd_my_template(
    message: types.Message,
):
    if not message.from_user:
        return

    await _get_or_create_user(
        message
    )

    async with AsyncSessionLocal() as session:
        stmt = select(
            User.template_text
        ).where(
            User.tg_id == message.from_user.id
        )

        result = await session.execute(
            stmt
        )

        current_template = (
            result.scalar_one_or_none()
        )

    if not current_template:
        await message.answer(
            "📋 <b>Your Current Format</b>\n\n"
            "You are using <b>Alice's Default Style</b>.",
            parse_mode=ParseMode.HTML,
        )

        return

    # Escape Jinja/HTML code before putting it inside Telegram <pre>.
    safe_template = html.escape(
        str(current_template),
        quote=False,
    )

    # Telegram messages have a maximum size, so avoid accidentally
    # breaking the command with an unusually large stored template.
    if len(safe_template) > 3500:
        safe_template = (
            safe_template[:3500]
            + "\n\n...template shortened..."
        )

    await message.answer(
        "📋 <b>Your Current Format:</b>\n\n"
        f"<pre>{safe_template}</pre>",
        parse_mode=ParseMode.HTML,
    )


# ============================================================
# /reset_template
# ============================================================

@router.message(
    Command("reset_template")
)
async def cmd_reset_template(
    message: types.Message,
):
    if not message.from_user:
        return

    await _get_or_create_user(
        message
    )

    async with AsyncSessionLocal() as session:
        await session.execute(
            update(User)
            .where(
                User.tg_id == message.from_user.id
            )
            .values(
                template_text=None
            )
        )

        await session.commit()

    await message.answer(
        "🔄 <b>Template reset!</b>\n\n"
        "You're back to Alice's default format. 💅",
        parse_mode=ParseMode.HTML,
    )


# ============================================================
# /help
# ============================================================

@router.message(
    Command("help")
)
async def cmd_help(
    message: types.Message,
):
    help_text = (
        "❓ <b>Lazy Alice Help</b>\n\n"

        "📄 Send a Rate Confirmation PDF\n"
        "Alice extracts the load and formats it for you.\n\n"

        "⚙️ <b>Custom Format</b>\n"
        "/set_template — Teach Alice your format\n"
        "/my_template — View your active format\n"
        "/reset_template — Return to default\n\n"

        "🌡 <b>Reefer Loads</b>\n"
        "Temperature information is detected automatically "
        "when it is actually stated in the Rate Confirmation.\n\n"

        "💎 <b>Account</b>\n"
        "/plans — View Pro plans\n"
        "/privacy — Privacy information\n\n"

        "Support: @lazyalice_admin"
    )

    await message.answer(
        help_text,
        parse_mode=ParseMode.HTML,
    )
