import asyncio
import logging
import os
import time

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import text

from config import BOT_TOKEN
from database.connection import init_db, AsyncSessionLocal
from handlers import admin, start, settings, billing, chat, processor
from utils.middlewares import SubscriptionMiddleware, ThrottlingMiddleware

# Only what we actually use from processor
from handlers.processor import media_group_tracker

# 1. Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("LazyAlice")


async def clear_media_tracker_periodic():
    """Clears media group counters so memory doesn't grow forever."""
    while True:
        await asyncio.sleep(3600)  # every hour
        media_group_tracker.clear()
        logger.info("🧹 Media group tracker cleared.")


async def cleanup_temp_files():
    """Deletes PDFs older than 24h inside ./temp (runs every 12h)."""
    while True:
        now = time.time()
        temp_dir = "temp"
        if os.path.exists(temp_dir):
            for f in os.listdir(temp_dir):
                f_path = os.path.join(temp_dir, f)
                try:
                    if os.stat(f_path).st_mtime < now - 86400:
                        os.remove(f_path)
                        logger.info("🗑️ Deleted old temp file: %s", f)
                except Exception as e:
                    logger.warning("Temp cleanup failed for %s: %s", f, e)

        await asyncio.sleep(43200)  # 12 hours


async def on_startup(bot: Bot):
    logger.info("Waking up Alice's memory... 🧠")

    # Ensure temporary storage exists for PDFs
    os.makedirs("temp", exist_ok=True)

    # Create base tables
    await init_db()

    # --- AUTO-MIGRATION LOGIC ---
    async with AsyncSessionLocal() as session:
        try:
            logger.info("Synchronizing database columns... ⚙️")
            await session.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_requests INTEGER DEFAULT 0;"
            ))
            await session.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_request_date DATE DEFAULT CURRENT_DATE;"
            ))
            await session.commit()
            logger.info("✅ Database columns synchronized successfully!")
        except Exception as e:
            await session.rollback()
            logger.warning("Database sync note: %s", e)

    # Background tasks
    asyncio.create_task(clear_media_tracker_periodic())
    asyncio.create_task(cleanup_temp_files())

    logger.info("Alice is fully awake. 💅")


async def main():
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    dp = Dispatcher(storage=MemoryStorage())

    dp.startup.register(on_startup)

    # Middlewares
    dp.message.middleware(ThrottlingMiddleware())
    dp.message.middleware(SubscriptionMiddleware())

    # Routers order matters:
    # chat BEFORE processor so text goes to chat, PDFs go to processor.
    dp.include_router(admin.router)
    dp.include_router(start.router)
    dp.include_router(settings.router)
    dp.include_router(billing.router)
    dp.include_router(chat.router)
    dp.include_router(processor.router)

    logger.info("🚀 Lazy Alice is ONLINE!")

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except Exception as e:
        logger.error("Polling error: %s", e)
    finally:
        await bot.session.close()
        logger.info("Alice is going back to sleep. 🥱💤")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass