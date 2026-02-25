import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from sqlalchemy import text # Required for the auto-fix migration 🛠️

from config import BOT_TOKEN
from database.connection import init_db, AsyncSessionLocal # Added SessionLocal for migration
from handlers import start, settings, billing, processor, admin
from utils.middlewares import SubscriptionMiddleware, ThrottlingMiddleware

# 1. Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("LazyAlice")

async def on_startup(bot: Bot):
    """Alice performs a self-surgery on the database 🏥💅"""
    logger.info("Waking up Alice's memory... 🧠")
    
    # Initialize basic tables
    await init_db()
    
    # --- AUTO-MIGRATION LOGIC ---
    # This fixes the 'UndefinedColumnError' automatically on Railway
    async with AsyncSessionLocal() as session:
        try:
            logger.info("Synchronizing database columns... ⚙️")
            # Postgres-specific 'IF NOT EXISTS' equivalent logic
            await session.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_requests INTEGER DEFAULT 0;"
            ))
            await session.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_request_date DATE DEFAULT CURRENT_DATE;"
            ))
            await session.commit()
            logger.info("✅ Database columns synchronized successfully! 💅")
        except Exception as e:
            await session.rollback()
            logger.warning(f"Database sync note (might already be fixed): {e}")
    
    logger.info("Alice is fully awake and ready to judge your PDFs. 💅")

async def main():
    # 2. Initialize Bot with Default HTML formatting
    bot = Bot(
        token=BOT_TOKEN, 
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    
    # Alice needs MemoryStorage to remember states (Broadcasts, Searches, etc.) ☕
    dp = Dispatcher(storage=MemoryStorage())

    # 3. Register Startup Hook
    dp.startup.register(on_startup)

    # 4. Register Middlewares
    dp.message.middleware(ThrottlingMiddleware())
    dp.message.middleware(SubscriptionMiddleware())

    # 5. Include Routers
    # Admin first to catch panel interactions, Processor last for heavy lifting.
    dp.include_router(admin.router) 
    dp.include_router(start.router)
    dp.include_router(settings.router)
    dp.include_router(billing.router)
    dp.include_router(processor.router)

    # 6. Start Polling
    logger.info("🚀 Lazy Alice: Dispatcher Edition is now ONLINE!")
    
    try:
        # Clear any pending updates so Alice doesn't get overwhelmed 🥱
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Ugh, even Alice can't ignore this error: {e}")
    finally:
        logger.info("Alice is going back to sleep. Don't wake her up. 🥱💤")
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass