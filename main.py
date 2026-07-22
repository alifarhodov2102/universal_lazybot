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
from database.connection import (
    init_db,
    AsyncSessionLocal,
)
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
# Telegram webhook cleanup
# ============================================================

async def ensure_webhook_disabled(
    bot: Bot,
    max_attempts: int = 3,
) -> None:
    """
    Make sure no Telegram webhook is active before long polling starts.

    Telegram does not allow getUpdates polling while a webhook is
    registered for the same bot token.
    """
    for attempt in range(
        1,
        max_attempts + 1,
    ):
        webhook_info = await bot.get_webhook_info()

        if webhook_info.url:
            logger.warning(
                "Active webhook detected: %s",
                webhook_info.url,
            )

        else:
            logger.info(
                "No active webhook detected."
            )

        deleted = await bot.delete_webhook(
            drop_pending_updates=False,
        )

        logger.info(
            "Webhook deletion attempt %s/%s returned: %s",
            attempt,
            max_attempts,
            deleted,
        )

        await asyncio.sleep(1)

        webhook_info = await bot.get_webhook_info()

        if not webhook_info.url:
            logger.info(
                "Webhook is disabled. Long polling can start."
            )
            return

        logger.warning(
            "Webhook is still active after attempt %s: %s",
            attempt,
            webhook_info.url,
        )

    raise RuntimeError(
        "Telegram webhook is still active after "
        f"{max_attempts} deletion attempts. "
        "Another running service may be setting the webhook."
    )


# ============================================================
# Startup and shutdown
# ============================================================

async def on_startup(
    bot: Bot,
) -> None:
    logger.info(
        "Starting Lazy Alice."
    )

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

    logger.info(
        "Lazy Alice is ready."
    )


async def on_shutdown(
    bot: Bot,
) -> None:
    logger.info(
        "Lazy Alice is shutting down."
    )


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

    try:
        logger.info(
            "Checking Telegram webhook status."
        )

        await ensure_webhook_disabled(
            bot
        )

        logger.info(
            "Starting Telegram long polling."
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
            "Telegram bot stopped because of an error."
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
