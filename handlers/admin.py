from aiogram import Router, types, F
from aiogram.filters import Command
from sqlalchemy import update
from datetime import datetime, timedelta
from database.connection import AsyncSessionLocal
from database.models import User
import os

router = Router()

# .env faylingga o'z Telegram ID-ingni yozib qo'y
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

@router.message(Command("givepro"))
async def give_pro_status(message: types.Message):
    """
    Format: /givepro USER_ID DAYS
    Example: /givepro 12345678 30
    """
    if message.from_user.id != ADMIN_ID:
        return await message.reply("💅 Nice try, honey. You aren't my boss.")

    args = message.text.split()
    if len(args) < 3:
        return await message.reply("🥱 Format: /givepro [user_id] [days]")

    try:
        user_id = int(args[1])
        days = int(args[2])
        expire_at = datetime.utcnow() + timedelta(days=days)

        async with AsyncSessionLocal() as session:
            stmt = (
                update(User)
                .where(User.tg_id == user_id)
                .values(is_pro=True, expiry_date=expire_at)
            )
            await session.execute(stmt)
            await session.commit()

        await message.reply(
            f"✅ **Success!**\nUser `{user_id}` is now Pro for {days} days.\n"
            f"Expires: `{expire_at.strftime('%d.%m.%Y')}`",
            parse_mode="Markdown"
        )
        
        # Notify the user (optional but nice)
        try:
            await message.bot.send_message(
                user_id, 
                "✨ <b>Good news!</b> Your Pro status has been activated manually. 💅\n"
                f"Valid until: <b>{expire_at.strftime('%d.%m.%Y')}</b>",
                parse_mode="HTML"
            )
        except Exception:
            pass

    except ValueError:
        await message.reply("🙄 Give me real numbers, Ali.")