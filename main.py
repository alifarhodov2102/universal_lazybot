import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN
from database.connection import init_db
from handlers import start, settings, billing, processor
from utils.middlewares import SubscriptionMiddleware, ThrottlingMiddleware

# Configure logging to see what Alice is grumbling about
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("LazyAlice")

async def on_startup(bot: Bot):
    """Actions to perform when Alice finally wakes up 🥱"""
    logger.info("Waking up Alice's memory (Database)... 🧠")
    await init_db()
    logger.info("Alice is fully awake and ready to judge your PDFs. 💅")

async def main():
    # 1. Initialize Bot and Dispatcher
    # Alice needs MemoryStorage to remember where she left her coffee ☕
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    # 2. Register Startup Hook
    # This ensures the database is ready BEFORE the first message arrives.
    dp.startup.register(on_startup)

    # 3. Register Middlewares
    # Throttling stops people from spamming Alice (she hates that).
    dp.message.middleware(ThrottlingMiddleware())
    dp.message.middleware(SubscriptionMiddleware())

    # 4. Include Routers
    # Order matters: /start first, then settings/billing, and PDF processor last.
    dp.include_router(start.router)
    dp.include_router(settings.router)
    dp.include_router(billing.router)
    dp.include_router(processor.router)

    # 5. Start Polling
    logger.info("🚀 Lazy Alice: Dispatcher Edition is now ONLINE!")
    
    try:
        # We delete the webhook to ensure Alice only listens via polling for now
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