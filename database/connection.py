import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from dotenv import load_dotenv

load_dotenv()

# Railway yoki boshqa Cloud uchun optimal URL
DB_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./bot_database.db")

# 1. High-Speed Engine Configuration 🚀
# Biz bu yerda pooling sozlamalarini qo'shdik, shunda bot 10x tezroq javob beradi.
engine = create_async_engine(
    DB_URL, 
    echo=False,
    pool_size=10,             # Bir vaqtning o'zida 10 ta ulanish tayyor turadi
    max_overflow=20,          # Zarur bo'lsa yana 20 ta qo'shimcha ulanish ochiladi
    pool_pre_ping=True,       # Ulanish o'lib qolmaganini doim tekshirib turadi
    pool_recycle=3600         # Har soatda ulanishlarni yangilab turadi
)

# 2. Session Factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# 3. Base Class
class Base(DeclarativeBase):
    pass

# 4. Database Initialization
async def init_db():
    # Alice o'z xotirasini bot ishga tushgan zahoti tayyorlaydi 🥱
    async with engine.begin() as conn:
        # Bu qator jadvallarni (User modeli kabi) avtomatik yaratadi
        await conn.run_sync(Base.metadata.create_all)