from typing import Any, Callable, Dict, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message
from sqlalchemy import select
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
        # 1. Tezkor filtr: Agar bu oddiy komanda bo'lsa, DB-ni tekshirmaymiz (Sekinlikni yo'qotadi)
        if event.text and event.text.startswith('/'):
            return await handler(event, data)

        # 2. Faqat PDF kelganda bazani tekshiramiz
        if event.document and event.document.mime_type == "application/pdf":
            user_id = event.from_user.id
            
            # Admin bypass: Bazaga kirmasdan oldin config orqali tekshirish (Tezkor)
            if user_id in ADMIN_IDS:
                return await handler(event, data)

            async with AsyncSessionLocal() as session:
                try:
                    stmt = select(User).where(User.tg_id == user_id)
                    result = await session.execute(stmt)
                    user = result.scalar_one_or_none()

                    if user:
                        # Obuna muddatini tekshirish
                        if user.is_pro and user.expiry_date:
                            if user.expiry_date < datetime.utcnow():
                                user.is_pro = False
                                await session.commit()
                        
                        data["db_user"] = user
                except Exception as e:
                    print(f"Middleware DB Error: {e}")
                
        return await handler(event, data)

class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, limit: float = 1.0): # Limitni 1 soniyaga tushirdik
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
        
        if user_id in ADMIN_IDS:
            return await handler(event, data)

        current_time = datetime.now().timestamp()
        if user_id in self.caches:
            if current_time - self.caches[user_id] < self.limit:
                # Answer o'rniga silent return qilish ham mumkin foydalanuvchini bezovta qilmaslik uchun
                return 
        
        self.caches[user_id] = current_time
        return await handler(event, data)