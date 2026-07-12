import logging
import time
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, Optional

from aiogram import BaseMiddleware
from aiogram.types import Message
from sqlalchemy import select

from config import ADMIN_IDS
from database.connection import AsyncSessionLocal
from database.models import User


logger = logging.getLogger("LazyAlice.Middlewares")


def _normalize_datetime(value: Optional[datetime]) -> Optional[datetime]:
    """
    Convert timezone-aware database datetimes to naive UTC-compatible
    datetimes used by the current project.
    """
    if value is None:
        return None

    if value.tzinfo is not None:
        return value.replace(tzinfo=None)

    return value


class SubscriptionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[
            [Message, Dict[str, Any]],
            Awaitable[Any],
        ],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        """
        Load subscription information for PDF messages.

        Payment service messages must always bypass subscription checks.
        """

        # Successful Telegram Stars payments must never be blocked.
        if event.successful_payment:
            return await handler(event, data)

        # Commands such as /start and /plans must remain accessible.
        if event.text and event.text.startswith("/"):
            return await handler(event, data)

        # Subscription lookup is only needed for PDF processing.
        if not (
            event.document
            and event.document.mime_type == "application/pdf"
        ):
            return await handler(event, data)

        if not event.from_user:
            return await handler(event, data)

        user_id = event.from_user.id

        # Administrators bypass subscription database checks.
        if user_id in ADMIN_IDS:
            return await handler(event, data)

        async with AsyncSessionLocal() as session:
            try:
                result = await session.execute(
                    select(User).where(
                        User.tg_id == user_id
                    )
                )

                user = result.scalar_one_or_none()

                if user:
                    expiry = _normalize_datetime(
                        user.expiry_date
                    )

                    if (
                        user.is_pro
                        and expiry
                        and expiry <= datetime.utcnow()
                    ):
                        user.is_pro = False

                        await session.commit()

                        logger.info(
                            "Expired Pro subscription disabled "
                            "for user %s.",
                            user_id,
                        )

                    # The PDF handler can reuse this object if needed.
                    data["db_user"] = user

            except Exception:
                logger.exception(
                    "Subscription lookup failed for user %s.",
                    user_id,
                )

        return await handler(event, data)


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(
        self,
        limit: float = 1.0,
        cache_ttl: float = 3600.0,
    ):
        super().__init__()

        self.limit = max(
            float(limit),
            0.0,
        )

        self.cache_ttl = max(
            float(cache_ttl),
            60.0,
        )

        self.caches: Dict[int, float] = {}
        self._last_cleanup = time.monotonic()

    async def __call__(
        self,
        handler: Callable[
            [Message, Dict[str, Any]],
            Awaitable[Any],
        ],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        """
        Apply basic anti-spam throttling to ordinary messages.

        PDFs and successful payments bypass throttling because PDFs have
        their own queue and payments must always reach the billing handler.
        """

        # This is critical for Telegram Stars activation.
        if event.successful_payment:
            logger.info(
                "Successful payment bypassed throttling for user %s.",
                event.from_user.id
                if event.from_user
                else "unknown",
            )

            return await handler(event, data)

        if not event.from_user:
            return await handler(event, data)

        user_id = event.from_user.id

        # Administrators are never throttled.
        if user_id in ADMIN_IDS:
            return await handler(event, data)

        # PDFs use the per-user queue and must not be silently discarded.
        if (
            event.document
            and event.document.mime_type == "application/pdf"
        ):
            return await handler(event, data)

        current_time = time.monotonic()

        # Periodically remove inactive users from the in-memory cache.
        if (
            current_time - self._last_cleanup
            >= self.cache_ttl
        ):
            cutoff = current_time - self.cache_ttl

            self.caches = {
                cached_user_id: timestamp
                for cached_user_id, timestamp
                in self.caches.items()
                if timestamp >= cutoff
            }

            self._last_cleanup = current_time

        previous_time = self.caches.get(
            user_id
        )

        if (
            previous_time is not None
            and current_time - previous_time < self.limit
        ):
            logger.debug(
                "Message throttled for user %s.",
                user_id,
            )

            return None

        self.caches[user_id] = current_time

        return await handler(event, data)
