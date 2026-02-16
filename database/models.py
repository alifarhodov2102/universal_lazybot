from sqlalchemy import Column, Integer, BigInteger, String, Boolean, DateTime, Text
from datetime import datetime
from .connection import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    tg_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String, nullable=True)
    
    # 2 ta bepul urinish (Default)
    free_uses = Column(Integer, default=2)
    
    # Pro holati
    is_pro = Column(Boolean, default=False)
    expiry_date = Column(DateTime, nullable=True)
    
    # Userning shaxsiy formati (Jinja2 uchun)
    # Agar bu bo'sh bo'lsa, default format ishlatiladi
    template_text = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<User {self.tg_id} - Pro: {self.is_pro}>"