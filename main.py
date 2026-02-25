import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from config import BOT_TOKEN
from database.connection import init_db
# Import admin alongside your other handlers 💅
from handlers import start, settings, billing, processor, admin
from utils.middlewares import SubscriptionMiddleware, ThrottlingMiddleware

# 1. Configure logging to see what Alice is grumbling about
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("LazyAlice")

async def on_startup(bot: Bot):
    """Actions to perform when Alice finally wakes up 🥱"""
    logger.info("Waking up Alice's memory (Database)... 🧠")
    # This ensures your new daily limit columns (daily_requests, last_request_date) 
    # are created in the database automatically.
    await init_db()
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
    # Order matters: Admin panel and Commands first, Processor last!
    dp.include_router(admin.router) # Catch admin panel interactions 🛠️
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