from sqlalchemy import Column, Integer, BigInteger, String, Boolean, DateTime, Text, Date
from datetime import datetime, date
from .connection import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    tg_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String, nullable=True)
    
    # --- 10 RC Daily Limit Logic 💅 ---
    daily_requests = Column(Integer, default=0)
    last_request_date = Column(Date, default=date.today)
    
    is_pro = Column(Boolean, default=False)
    expiry_date = Column(DateTime, nullable=True)
    
    # Custom Jinja2 Template
    template_text = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<User {self.tg_id} - {self.daily_requests}/10 RCs used today>"