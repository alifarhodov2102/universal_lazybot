import os
from dotenv import load_dotenv

# .env faylidagi o'zgaruvchilarni yuklash
load_dotenv()

# Telegram Bot sozlamalari
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN .env faylida topilmadi!")

# DeepSeek API sozlamalari
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
# DeepSeek API uchun asosiy URL manzili
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"

if not DEEPSEEK_API_KEY:
    # Agarda DeepSeek bo'lmasa bot ishlayverishi mumkin, 
    # lekin extraction sifatiga ta'sir qiladi
    print("Warning: DEEPSEEK_API_KEY topilmadi. AI extraction ishlamasligi mumkin.")

# Ma'lumotlar bazasi sozlamalari
# Railway-da agar Postgres ulasangiz, DATABASE_URL avtomatik beriladi
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    # Lokal ishlab chiqish uchun SQLite
    DATABASE_URL = "sqlite+aiosqlite:///./bot_database.db"
elif DATABASE_URL.startswith("postgres://"):
    # SQLAlchemy uchun postgres:// ni postgresql+asyncpg:// ga aylantirish kerak
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)

# To'lov sozlamalari (Stars uchun)
# Stars uchun provider token bo'sh bo'ladi
PAYMENT_PROVIDER_TOKEN = os.getenv("PAYMENT_PROVIDER_TOKEN", "")

# Admin ID-lar (Xatoliklar yoki statistikani ko'rish uchun)
# .env faylida: ADMIN_IDS=1234567,8901234 ko'rinishida yoziladi
ADMIN_IDS = [int(admin_id) for admin_id in os.getenv("ADMIN_IDS", "").split(",") if admin_id]
