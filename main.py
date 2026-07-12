import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import text

from config import (
    BOT_TOKEN,
    DATABASE_URL,
    MAX_CONCURRENT_JOBS,
    MAX_CONCURRENT_OCR,
    MAX_CONCURRENT_AI,
)
from database.connection import init_db, AsyncSessionLocal
from handlers import (
    admin,
    billing,
    start,
    settings,
    chat,
    processor,
)
from utils.middlewares import (
    SubscriptionMiddleware,
    ThrottlingMiddleware,
)


# ============================================================
# Logging
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s - %(name)s - "
        "%(levelname)s - %(message)s"
    ),
)

logger = logging.getLogger("LazyAlice")


# ============================================================
# Database synchronization
# ============================================================

async def synchronize_database_columns() -> None:
    """
    Keep compatibility with databases created by older versions.

    This is a temporary migration system. For larger future database
    changes, Alembic migrations should be used.
    """
    async with AsyncSessionLocal() as session:
        try:
            dialect_name = (
                session.bind.dialect.name
                if session.bind
                else "unknown"
            )

            logger.info(
                "Database dialect: %s",
                dialect_name,
            )

            # PostgreSQL supports ADD COLUMN IF NOT EXISTS.
            if dialect_name == "postgresql":
                await session.execute(
                    text(
                        """
                        ALTER TABLE users
                        ADD COLUMN IF NOT EXISTS
                        weekly_requests INTEGER DEFAULT 0;
                        """
                    )
                )

                await session.execute(
                    text(
                        """
                        ALTER TABLE users
                        ADD COLUMN IF NOT EXISTS
                        last_request_date DATE DEFAULT CURRENT_DATE;
                        """
                    )
                )

                await session.execute(
                    text(
                        """
                        ALTER TABLE users
                        ADD COLUMN IF NOT EXISTS
                        daily_requests INTEGER DEFAULT 0;
                        """
                    )
                )

                await session.commit()

                logger.info(
                    "Database columns synchronized successfully."
                )

            else:
                # init_db() already creates all columns for a new
                # SQLite database. The PostgreSQL migration syntax
                # is intentionally not executed on SQLite.
                logger.info(
                    "Automatic legacy column migration skipped "
                    "for database dialect %s.",
                    dialect_name,
                )

        except Exception:
            await session.rollback()

            logger.exception(
                "Database column synchronization failed."
            )


# ============================================================
# Startup and shutdown
# ============================================================

async def on_startup(bot: Bot) -> None:
    logger.info("Starting Lazy Alice.")

    await init_db()
    await synchronize_database_columns()

    database_type = (
        "PostgreSQL"
        if "postgresql" in DATABASE_URL
        else "SQLite"
    )

    logger.info(
        "Database backend: %s",
        database_type,
    )

    logger.info(
        "Processing limits: jobs=%s, OCR=%s, AI=%s",
        MAX_CONCURRENT_JOBS,
        MAX_CONCURRENT_OCR,
        MAX_CONCURRENT_AI,
    )

    bot_info = await bot.get_me()

    logger.info(
        "Bot connected successfully: @%s (%s)",
        bot_info.username,
        bot_info.id,
    )

    logger.info("Lazy Alice is ready.")


async def on_shutdown(bot: Bot) -> None:
    logger.info("Lazy Alice is shutting down.")


# ============================================================
# Main application
# ============================================================

async def main() -> None:
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
        ),
    )

    dispatcher = Dispatcher(
        storage=MemoryStorage(),
    )

    dispatcher.startup.register(
        on_startup
    )

    dispatcher.shutdown.register(
        on_shutdown
    )

    # --------------------------------------------------------
    # Middlewares
    # --------------------------------------------------------

    dispatcher.message.middleware(
        ThrottlingMiddleware()
    )

    dispatcher.message.middleware(
        SubscriptionMiddleware()
    )

    # --------------------------------------------------------
    # Routers
    # --------------------------------------------------------

    # Payment handlers are placed early, although middleware still
    # executes before routers. We must check middlewares next to make
    # sure successful_payment updates are never blocked.
    dispatcher.include_router(
        admin.router
    )

    dispatcher.include_router(
        billing.router
    )

    dispatcher.include_router(
        start.router
    )

    dispatcher.include_router(
        settings.router
    )

    dispatcher.include_router(
        chat.router
    )

    dispatcher.include_router(
        processor.router
    )

    logger.info(
        "Starting Telegram long polling."
    )

    try:
        # Preserve pending updates. This is important for payment
        # confirmations that arrived while Railway was restarting.
        await bot.delete_webhook(
            drop_pending_updates=False
        )

        await dispatcher.start_polling(
            bot,
            allowed_updates=(
                dispatcher.resolve_used_update_types()
            ),
        )

    except asyncio.CancelledError:
        logger.info(
            "Telegram polling was cancelled."
        )
        raise

    except Exception:
        logger.exception(
            "Telegram polling stopped because of an error."
        )

        raise

    finally:
        await bot.session.close()

        logger.info(
            "Telegram bot session closed."
        )


if __name__ == "__main__":
    try:
        asyncio.run(
            main()
        )

    except (KeyboardInterrupt, SystemExit):
        logger.info(
            "Bot stopped manually."
        )
