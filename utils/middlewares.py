from typing import Any, Callable, Dict, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message
from sqlalchemy import select, update
from datetime import datetime

from database.connection import AsyncSessionLocal
from database.models import User
from config import ADMIN_IDS


class SubscriptionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any]
    ) -> Any:
        # 1. Command Bypass: Skip DB check for commands to keep Alice fast 🚀
        if event.text and event.text.startswith('/'):
            return await handler(event, data)

        # 2. PDF Filter: Alice only starts working for PDFs 🥱
        if event.document and event.document.mime_type == "application/pdf":
            user_id = event.from_user.id

            # Admin VIP access: No DB wait for the boss 💅
            if user_id in ADMIN_IDS:
                return await handler(event, data)

            async with AsyncSessionLocal() as session:
                try:
                    stmt = select(User).where(User.tg_id == user_id)
                    result = await session.execute(stmt)
                    user = result.scalar_one_or_none()

                    if user:
                        # 3. Automatic Expiry Logic: 30 days is up? Back to the street.
                        if user.is_pro and user.expiry_date:
                            if user.expiry_date < datetime.utcnow():
                                # Reset Pro status in DB
                                user.is_pro = False
                                await session.commit()
                                # Notify the user briefly if you want
                                await event.answer("🚫 Your Pro plan expired, honey. Back to free limits! 💅")

                        # Pass the user object to the handler to avoid another DB call
                        data["db_user"] = user
                except Exception as e:
                    print(f"❌ Alice's Memory Error: {e}")

        return await handler(event, data)


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, limit: float = 1.0):
        self.limit = limit
        self.caches = {}
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any]
    ) -> Any:
        user_id = event.from_user.id

        # Admins can spam Alice all they want 💅
        if user_id in ADMIN_IDS:
            return await handler(event, data)

        # ✅ Allow PDFs to bypass throttling (queue will handle them)
        if event.document and event.document.mime_type == "application/pdf":
            return await handler(event, data)

        current_time = datetime.now().timestamp()

        # Anti-Spam Check: Don't let them annoy Alice too fast
        if user_id in self.caches:
            if current_time - self.caches[user_id] < self.limit:
                # Alice ignores the request silently 🥱
                return

        self.caches[user_id] = current_time
        return await handler(event, data)