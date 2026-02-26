import asyncio
import logging
import os
import time
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from sqlalchemy import text

from config import BOT_TOKEN
from database.connection import init_db, AsyncSessionLocal
from handlers import start, settings, billing, processor, admin
from utils.middlewares import SubscriptionMiddleware, ThrottlingMiddleware

# ✅ FIXED: Import the correct global objects from processor
from handlers.processor import extraction_queue, media_group_tracker, global_pdf_worker

# 1. Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("LazyAlice")

async def clear_media_tracker_periodic():
    """Alice cleans her room every hour so she doesn't run out of memory. 💅"""
    while True:
        await asyncio.sleep(3600)  # Every hour
        media_group_tracker.clear()
        logger.info("🧹 Media group tracker cleared. Alice likes it clean.")

async def cleanup_temp_files():
    """Alice throws away old trash (PDFs > 24h) to save space. 🗑️💅"""
    while True:
        now = time.time()
        temp_dir = "temp"
        if os.path.exists(temp_dir):
            for f in os.listdir(temp_dir):
                f_path = os.path.join(temp_dir, f)
                if os.stat(f_path).st_mtime < now - 86400:
                    try:
                        os.remove(f_path)
                        logger.info(f"🗑️ Deleted old temp file: {f}")
                    except Exception as e:
                        logger.error(f"Failed to delete {f}: {e}")
        await asyncio.sleep(43200)  # Runs every 12 hours

async def on_startup(bot: Bot):
    """Alice performs a self-surgery on the database 🏥💅"""
    logger.info("Waking up Alice's memory... 🧠")
    
    # Ensure temporary storage exists
    if not os.path.exists("temp"):
        os.makedirs("temp")
        logger.info("📁 Created 'temp' folder.")

    # Initialize basic database tables
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
            logger.info("✅ Database columns synchronized successfully! 💅")
        except Exception as e:
            await session.rollback()
            logger.warning(f"Database sync note: {e}")
    
    # 🚀 START BACKGROUND TASKS
    asyncio.create_task(clear_media_tracker_periodic())
    asyncio.create_task(cleanup_temp_files())
    
    # 🚀 START THE GLOBAL PDF WORKER
    # This is what actually processes the queue!
    asyncio.create_task(global_pdf_worker(bot))
    
    logger.info("Alice is fully awake and ready to judge your PDFs. 💅")

async def main():
    # 2. Initialize Bot
    bot = Bot(
        token=BOT_TOKEN, 
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    
    dp = Dispatcher(storage=MemoryStorage())

    # 3. Register Startup Hook
    dp.startup.register(on_startup)

    # 4. Register Middlewares
    dp.message.middleware(ThrottlingMiddleware())
    dp.message.middleware(SubscriptionMiddleware())

    # 5. Include Routers
    dp.include_router(admin.router) 
    dp.include_router(start.router)
    dp.include_router(settings.router)
    dp.include_router(billing.router)
    dp.include_router(processor.router)

    # 6. Start Polling
    logger.info("🚀 Lazy Alice: Dispatcher Edition is online!")
    
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Ugh, error: {e}")
    finally:
        logger.info("Alice is going back to sleep. 🥱💤")
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass