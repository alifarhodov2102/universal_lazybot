import logging

from aiogram import Router, types
from aiogram.filters import Command


router = Router()
logger = logging.getLogger("LazyAlice.Settings")


@router.message(Command("settings"))
async def show_settings(message: types.Message):
    """
    Show Alice's settings/control panel.

    Template creation, viewing, and reset commands are handled
    by the main template system in start.py/extractor.py.
    """

    text = (
        "⚙️ <b>Alice's Control Panel</b>\n\n"
        "Configure how I work for you:\n\n"
        "🛠 <b>/set_template</b> — Teach me your preferred load format\n"
        "📋 <b>/my_template</b> — See your current active format\n"
        "🔄 <b>/reset_template</b> — Return to Alice's default style\n\n"
        "🌡 <b>Reefer Support</b>\n"
        "If a Rate Confirmation contains an actual temperature "
        "requirement, Alice will automatically include it in the result.\n"
        "Dry loads stay clean with no empty temperature section.\n\n"
        "<i>Your custom format stays yours — Alice just fills in the data.</i> 💅"
    )

    await message.answer(
        text,
        parse_mode="HTML",
    )
